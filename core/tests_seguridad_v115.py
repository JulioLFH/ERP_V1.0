"""v1.115: defectos de la matriz de verificación — SEG-05 (chat y formulario web sin límite de conversaciones nuevas),
SEG-06 (freno por IP a quien prueba contraseñas), IP real detrás de IIS/Cloudflare y DAT-03 (revisar_datos en la
consola de Windows y órdenes anuladas sin número)."""
import io
from datetime import date

from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, override_settings
from django.urls import reverse

from ventas.models import ConfigCRM, ConversacionCanal, Oportunidad

from .acceso_ip import ip_cliente
from .models import Empresa
from .tests_crm_canales import Base as BaseCRM
from .tests_crm_canales import configurar
from .tests_seguridad_v143 import Base as BaseSeguridad


class IpRealTest(BaseSeguridad):
    def test_detras_de_iis_usa_la_ip_del_visitante(self):
        f = RequestFactory()
        r = f.get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='190.40.1.2, 172.70.1.1',
                  HTTP_CF_CONNECTING_IP='190.40.1.9')
        self.assertEqual(ip_cliente(r), '127.0.0.1')  # sin proxy configurado, la de la conexión
        with override_settings(USE_X_FORWARDED_HOST=True):
            self.assertEqual(ip_cliente(r), '190.40.1.9')  # Cloudflare
            r2 = f.get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='190.40.1.2, 172.70.1.1')
            self.assertEqual(ip_cliente(r2), '190.40.1.2')
            # una conexión directa (no del proxy local) no puede inventarse la IP con la cabecera
            r3 = f.get('/', REMOTE_ADDR='203.0.113.5', HTTP_X_FORWARDED_FOR='1.1.1.1')
            self.assertEqual(ip_cliente(r3), '203.0.113.5')


class FrenoPorIpTest(BaseSeguridad):
    def entrar(self, usuario, clave, ip):
        return self.client.post(reverse('login'), {'username': usuario, 'password': clave}, REMOTE_ADDR=ip)

    def test_once_intentos_desde_una_ip(self):
        """SEG-06: con la empresa sin bloqueo (0), la IP que falla 10 veces en 15 minutos no entra ni con la clave
        correcta; otra IP sí, y el usuario no queda bloqueado."""
        Empresa.objects.update(intentos_bloqueo=0)
        self.usuario('victima')
        for n in range(10):
            self.entrar(f'prueba{n}' if n % 2 else 'victima', 'mala', '203.0.113.7')
        r = self.entrar('victima', 'Clave-segura-9', '203.0.113.7')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Demasiados intentos fallidos desde su conexión')
        self.assertNotIn('_auth_user_id', self.client.session)
        r = self.entrar('victima', 'Clave-segura-9', '198.51.100.20')
        self.assertEqual(r.status_code, 302)

    def test_la_pc_del_servidor_no_se_frena(self):
        Empresa.objects.update(intentos_bloqueo=0)
        self.usuario('local1')
        for _ in range(12):
            self.entrar('local1', 'mala', '127.0.0.1')
        self.assertEqual(self.entrar('local1', 'Clave-segura-9', '127.0.0.1').status_code, 302)


class ConversacionesNuevasTest(BaseCRM):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_chat_web_cinco_por_ip_por_hora(self):
        """SEG-05: 40 envíos seguidos desde una IP crean como máximo 5 oportunidades; otra IP sigue pudiendo."""
        cfg = configurar(chat_activo=True)
        api = reverse('crm_chat', args=['default', cfg.token_web])
        antes = Oportunidad.objects.count()
        codigos = [self.client.post(api, {'texto': f'Hola {n}', 'nombre': f'Robot {n}', 'telefono': f'9{n:08d}',
                                          'consentimiento': '1'}, REMOTE_ADDR='203.0.113.7').status_code
                   for n in range(40)]
        self.assertEqual(codigos.count(200), 5)
        self.assertEqual(codigos.count(429), 35)
        self.assertEqual(Oportunidad.objects.count() - antes, 5)
        self.assertEqual(ConversacionCanal.objects.filter(canal='CHAT_WEB').count(), 5)
        r = self.client.post(api, {'texto': 'Consulta real', 'nombre': 'Ana', 'telefono': '955000111',
                                   'consentimiento': '1'}, REMOTE_ADDR='198.51.100.20')
        self.assertEqual(r.status_code, 200)

    def test_tope_por_empresa(self):
        from ventas import crm_canales
        cfg = configurar(chat_activo=True)
        api = reverse('crm_chat', args=['default', cfg.token_web])
        crm_canales.MAX_NUEVAS_POR_HORA, original = 3, crm_canales.MAX_NUEVAS_POR_HORA
        try:
            codigos = [self.client.post(api, {'texto': 'Hola', 'nombre': 'X', 'telefono': '9', 'consentimiento': '1'},
                                        REMOTE_ADDR=f'203.0.113.{n}').status_code for n in range(1, 6)]
        finally:
            crm_canales.MAX_NUEVAS_POR_HORA = original
        self.assertEqual(codigos, [200, 200, 200, 429, 429])

    def test_formulario_web_mismo_limite(self):
        cfg = ConfigCRM.actual()
        cfg.web_activo = True
        cfg.save()
        url = reverse('crm_web', args=['default', cfg.token_web])
        codigos = [self.client.post(url, {'nombre': 'Robot', 'telefono': '900000000', 'consentimiento': '1', 't': 0},
                                    HTTP_ACCEPT='application/json', REMOTE_ADDR='203.0.113.8').status_code
                   for _ in range(8)]
        self.assertEqual((codigos.count(200), codigos.count(429)), (5, 3))


class RevisarDatosConsolaTest(BaseCRM):
    def test_consola_de_windows_y_anuladas_sin_numero(self):
        """DAT-03: en una consola cp1252 el comando termina sin error, y la orden anulada sin número (pero con fecha y
        motivo) ya no aparece en la lista."""
        from django.utils import timezone

        from core.models import Almacen
        from produccion.models import OrdenProduccion
        Almacen.objects.create(codigo='OB8', nombre='Obra <b>x</b> → «nueva»')
        orden = OrdenProduccion.objects.filter(estado='ANULADA').first()
        consola = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        call_command('revisar_datos', corregir=True, stdout=consola)
        consola.flush()
        texto = consola.buffer.getvalue().decode('cp1252')
        self.assertIn('corrección(es)', texto)
        if orden is not None:
            orden.refresh_from_db()
            OrdenProduccion.objects.filter(pk=orden.pk).update(numero='', anulado_en=timezone.now(),
                                                              motivo_anulacion='Prueba')
            salida = io.StringIO()
            call_command('revisar_datos', stdout=salida)
            self.assertNotIn(f'id {orden.pk} (sin número)', salida.getvalue())
