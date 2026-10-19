"""Monitoreo (v1.33): errores del sistema como incidencias agrupadas con aviso por correo, «Reportar un problema»,
/salud/, log de modificaciones por módulo y formularios de venta sin las cotizaciones atendidas."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import monitoreo
from .models import Incidencia, Tercero

D = Decimal


def _error(ruta='/ventas/'):
    """Simula un error 500 en una petición y lo registra como lo hace la señal got_request_exception."""
    request = RequestFactory().get(ruta, HTTP_USER_AGENT='Prueba')
    request.user = AnonymousUser()
    try:
        raise ValueError('dato inválido de prueba')
    except ValueError:
        monitoreo.registrar_error(sender=None, request=request)


@override_settings(MONITOREO_SINCRONO=True)
class ErroresDelSistemaTest(TestCase):
    def test_agrupa_reabre_y_avisa_una_vez_por_hora(self):
        User.objects.create_superuser('jefe', 'jefe@empresa.pe', 'x')
        with patch('core.correo.enviar') as enviar:
            _error()
            _error()
        inc = Incidencia.objects.get()
        self.assertEqual((inc.tipo, inc.veces, inc.estado), ('ERROR', 2, 'ABIERTA'))
        self.assertIn('ValueError: dato inválido de prueba', inc.descripcion)
        self.assertIn('Traceback', inc.detalle)
        self.assertEqual(enviar.call_count, 1)  # el segundo, igual y dentro de la hora, no vuelve a avisar
        self.assertEqual(enviar.call_args[0][0], ['jefe@empresa.pe'])
        inc.estado = 'RESUELTA'
        inc.save()
        with patch('core.correo.enviar'):
            _error()
        inc.refresh_from_db()
        self.assertEqual((inc.estado, inc.veces), ('ABIERTA', 3))  # volvió a ocurrir: se reabre
        with patch('core.correo.enviar'):
            _error('/compras/')  # otra página: otro error
        self.assertEqual(Incidencia.objects.count(), 2)


class ReportarYRevisarTest(TestCase):
    def test_reporte_de_usuario_y_bandeja_del_administrador(self):
        vendedor = User.objects.create_user('vendedor', password='x')
        vendedor.groups.add(Group.objects.get_or_create(name='Ventas')[0])
        self.client.force_login(vendedor)
        with patch('core.correo.enviar'):
            r = self.client.post(reverse('reportar'), {'url': '/ventas/nuevo/',
                                                       'descripcion': 'Al guardar la factura sale un error raro.'})
        self.assertRedirects(r, '/ventas/nuevo/', fetch_redirect_response=False)
        inc = Incidencia.objects.get()
        self.assertEqual((inc.tipo, inc.usuario, inc.url), ('REPORTE', vendedor, '/ventas/nuevo/'))
        self.client.post(reverse('reportar'), {'url': 'https://otro-sitio.com/', 'descripcion': 'corto'})
        self.assertEqual(Incidencia.objects.count(), 1)  # muy corto: no se registra
        self.assertEqual(self.client.get(reverse('incidencias')).status_code, 403)  # no es de Ajustes
        admin = User.objects.create_superuser('admin2', 'a@a.pe', 'x')
        self.client.force_login(admin)
        r = self.client.get(reverse('incidencias'))
        self.assertContains(r, 'Al guardar la factura')
        self.client.post(reverse('incidencia', args=[inc.pk]), {'estado': 'RESUELTA', 'nota': 'Corregido en v1.33'})
        inc.refresh_from_db()
        self.assertEqual((inc.estado, inc.revisado_por, inc.nota), ('RESUELTA', admin, 'Corregido en v1.33'))
        self.assertContains(self.client.get('/'), 'Reportar un problema')


class SaludTest(TestCase):
    databases = '__all__'

    def test_salud_sin_iniciar_sesion(self):
        r = self.client.get(reverse('salud'))
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(datos['estado'], 'ok')
        self.assertEqual(datos['bases']['default'], 'ok')


class LogPorModuloTest(TestCase):
    def test_log_de_contactos_solo_para_administrador(self):
        admin = User.objects.create_superuser('admin3', 'b@b.pe', 'x')
        self.client.force_login(admin)
        t = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970', nombre='Cliente Uno SAC')
        t.nombre = 'Cliente Uno Renombrado SAC'
        t.save()
        r = self.client.get(reverse('modificaciones'), {'modulo': 'contactos'})
        self.assertContains(r, 'Log de modificaciones · Contactos')
        self.assertContains(r, 'Cliente Uno Renombrado SAC')
        self.assertContains(r, 'name="modulo" value="contactos"')  # los filtros no pierden el módulo
        r = self.client.get(reverse('modificaciones'), {'modulo': 'ventas'})
        self.assertNotContains(r, 'Cliente Uno Renombrado SAC')
        self.assertContains(self.client.get(reverse('ventas:lista')), 'modificaciones/?modulo=ventas')
        vendedor = User.objects.create_user('vend2', password='x')
        vendedor.groups.add(Group.objects.get_or_create(name='Ventas')[0])
        self.client.force_login(vendedor)
        self.assertEqual(self.client.get(reverse('modificaciones'), {'modulo': 'ventas'}).status_code, 403)
        self.assertNotContains(self.client.get(reverse('ventas:lista')), 'Log de modificaciones')


class CotizacionesAbiertasTest(TestCase):
    def test_formulario_de_venta_solo_ofrece_cotizaciones_abiertas(self):
        from ventas.forms import VentaForm
        from ventas.models import Cotizacion, Venta
        cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970', nombre='Cliente')
        base = dict(tercero=cliente)
        abierta = Cotizacion.objects.create(numero='1', estado='PENDIENTE', **base)
        atendida = Cotizacion.objects.create(numero='2', estado='ATENDIDO', **base)
        Cotizacion.objects.create(numero='3', estado='ANULADO', **base)
        self.assertEqual(list(VentaForm().fields['cotizacion'].queryset), [abierta])
        venta = Venta(tercero=cliente, cotizacion=atendida)
        self.assertEqual(set(VentaForm(instance=venta).fields['cotizacion'].queryset), {abierta, atendida})
