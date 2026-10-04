"""Pruebas de la segunda evaluación (v1.12): sesiones que sobreviven al despliegue, panel de Django restringido,
cabeceras de seguridad, comprobante emitido inmutable, exportación de listas, inventario permanente 12.1/13.1,
envío del comprobante, recuperación de contraseña, doble factor y reapertura controlada de periodos."""
import os
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from contabilidad.models import PeriodoContable
from core import doble_factor
from core.models import Bitacora, Producto, SegundoFactor
from core.modulos import GRUPOS
from ventas.models import Venta

D = Decimal


class EvaluacionDosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'jefe@empresa.pe', 'clave-segura-1')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    # ---------------------------------------------------------------- 1. sesiones y despliegue
    def test_despliegue_no_cambia_la_clave_del_administrador(self):
        with patch.dict(os.environ, {'DJANGO_SUPERUSER_USERNAME': 'jefe', 'DJANGO_SUPERUSER_PASSWORD': 'otra-clave'}):
            hash_antes = User.objects.get(username='jefe').password
            call_command('ensure_admin', verbosity=0)
            self.assertEqual(User.objects.get(username='jefe').password, hash_antes)  # la sesión sigue válida
            with patch.dict(os.environ, {'DJANGO_SUPERUSER_RESET': '1'}):
                call_command('ensure_admin', verbosity=0)
            self.assertTrue(User.objects.get(username='jefe').check_password('otra-clave'))

    # ---------------------------------------------------------------- 6 y 8. panel de Django y cabeceras
    def test_panel_django_solo_superusuario_y_cabecera_csp(self):
        vendedor = User.objects.create_user('vend', password='x')
        vendedor.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        self.client.force_login(vendedor)
        self.assertEqual(self.client.get('/admin/').status_code, 404)
        r = self.client.get(reverse('ventas:lista'))
        self.assertIn("default-src 'self'", r['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", r['Content-Security-Policy'])

    # ---------------------------------------------------------------- 13. comprobante inmutable
    def test_comprobante_emitido_no_se_edita(self):
        self.client.force_login(self.admin)
        v = Venta.objects.filter(estado='REGISTRADO', tipo_comprobante='01').exclude(estado_sunat='ERROR').first()
        r = self.client.get(reverse('ventas:editar', args=[v.pk]))
        self.assertRedirects(r, reverse('ventas:detalle', args=[v.pk]))
        Venta.objects.filter(pk=v.pk).update(estado_sunat='ERROR')
        if not v.movimientos.exists() and not v.notas.exists():
            self.assertEqual(self.client.get(reverse('ventas:editar', args=[v.pk])).status_code, 200)

    # ---------------------------------------------------------------- 11. exportar listas
    def test_listas_exportan_a_excel(self):
        self.client.force_login(self.admin)
        for url in [reverse('ventas:lista'), reverse('compras:lista'), reverse('terceros'), reverse('productos')]:
            r = self.client.get(url + '?formato=excel')
            self.assertEqual(r.status_code, 200, url)
            self.assertIn('spreadsheetml', r['Content-Type'])

    def test_exportar_productos_sin_permiso_de_costos_oculta_costos(self):
        from openpyxl import load_workbook
        import io
        u = User.objects.create_user('alm', password='x')
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['inventario'])[0])
        self.client.force_login(u)
        r = self.client.get(reverse('productos') + '?formato=excel')
        encabezados = [c.value for c in load_workbook(io.BytesIO(r.content)).active[3]]
        self.assertNotIn('Costo prom.', encabezados)

    # ---------------------------------------------------------------- 22. inventario permanente
    def test_inventario_permanente_12_1_y_13_1(self):
        from core.libros_inventario import documento
        self.assertEqual(documento('Factura F001-00000081'), ('01', 'F001', '00000081'))
        self.assertEqual(documento('Reversión Boleta de venta B001-00000003')[0], '03')
        self.client.force_login(self.admin)
        for formato in ('12.1', '13.1'):
            url = reverse('inv_libro_sunat') + f'?formato_sunat={formato}&mes=anual'
            self.assertEqual(self.client.get(url).status_code, 200)
            r = self.client.get(url + '&descargar=1')
            self.assertIn('spreadsheetml', r['Content-Type'])
        sin_costos = User.objects.create_user('alm2', password='x')
        sin_costos.groups.add(Group.objects.get_or_create(name=GRUPOS['inventario'])[0])
        self.client.force_login(sin_costos)
        self.assertEqual(self.client.get(reverse('inv_libro_sunat') + '?formato_sunat=13.1').status_code, 403)

    # ---------------------------------------------------------------- 16. envío del comprobante
    def test_enviar_comprobante_por_correo_y_whatsapp(self):
        self.client.force_login(self.admin)
        v = Venta.objects.filter(estado='REGISTRADO').first()
        with patch('core.correo.enviar') as enviar:
            r = self.client.post(reverse('ventas:enviar_correo', args=[v.pk]), {'para': 'cliente@correo.pe'})
        self.assertRedirects(r, reverse('ventas:detalle', args=[v.pk]))
        self.assertEqual(enviar.call_args[0][0], ['cliente@correo.pe'])
        self.assertTrue(Bitacora.objects.filter(accion='ENVIAR', objeto_id=str(v.pk)).exists())
        with patch('core.correo.enviar') as enviar:
            self.client.post(reverse('ventas:enviar_correo', args=[v.pk]), {'para': 'no-es-correo'})
        enviar.assert_not_called()
        r = self.client.get(reverse('ventas:whatsapp', args=[v.pk]) + '?telefono=987654321')
        self.assertTrue(r['Location'].startswith('https://wa.me/51987654321?text='))

    # ---------------------------------------------------------------- 5. recuperación y doble factor
    def test_recuperar_contrasena_por_correo(self):
        with patch('core.correo.enviar') as enviar:
            r = self.client.post(reverse('recuperar'), {'usuario': 'jefe'})
            self.assertContains(r, 'Revise su correo')
            self.assertEqual(enviar.call_args[0][0], ['jefe@empresa.pe'])
            r = self.client.post(reverse('recuperar'), {'usuario': 'no-existe'})
            self.assertContains(r, 'Revise su correo')  # no revela si el usuario existe
            self.assertEqual(enviar.call_count, 1)
        uid = urlsafe_base64_encode(force_bytes(self.admin.pk))
        token = default_token_generator.make_token(self.admin)
        r = self.client.get(reverse('recuperar_confirmar', args=[uid, token]), follow=True)
        r = self.client.post(r.redirect_chain[-1][0], {'new_password1': 'Nueva-clave-2026', 'new_password2':
                                                       'Nueva-clave-2026'})
        self.assertRedirects(r, reverse('recuperar_listo'))
        self.assertTrue(User.objects.get(pk=self.admin.pk).check_password('Nueva-clave-2026'))

    def test_totp_rfc6238(self):
        # vector de prueba del RFC 6238 (SHA1, T=59 -> 94287082, últimos 6 dígitos)
        secreto = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        self.assertEqual(doble_factor.codigo_actual(secreto, 59), '287082')
        self.assertIsNotNone(doble_factor.verificar_totp(secreto, '287082', instante=59))
        self.assertIsNone(doble_factor.verificar_totp(secreto, '287082', ultimo_paso=1, instante=59))  # ya usado

    def test_inicio_de_sesion_con_doble_factor(self):
        secreto = doble_factor.nuevo_secreto()
        codigos, hashes = doble_factor.nuevos_codigos_respaldo()
        SegundoFactor.objects.create(usuario=self.admin, secreto=secreto, activo=True, codigos_respaldo=hashes)
        r = self.client.post(reverse('login'), {'username': 'jefe', 'password': 'clave-segura-1'})
        self.assertRedirects(r, reverse('login_2fa'))
        self.assertNotIn('_auth_user_id', self.client.session)  # aún sin sesión
        r = self.client.post(reverse('login_2fa'), {'codigo': '000000'})
        self.assertContains(r, 'Código incorrecto')
        r = self.client.post(reverse('login_2fa'), {'codigo': doble_factor.codigo_actual(secreto)})
        self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.admin.pk)
        # código de respaldo: sirve una sola vez
        self.client.logout()
        self.client.post(reverse('login'), {'username': 'jefe', 'password': 'clave-segura-1'})
        self.client.post(reverse('login_2fa'), {'codigo': codigos[0]})
        self.assertIn('_auth_user_id', self.client.session)
        self.assertEqual(len(SegundoFactor.objects.get(usuario=self.admin).codigos_respaldo), 7)

    def test_activar_doble_factor_desde_la_cuenta(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('seguridad'), {'accion': 'iniciar'})
        secreto = self.client.session['doble_factor_secreto']
        self.assertContains(self.client.get(reverse('seguridad')), secreto)
        r = self.client.post(reverse('seguridad'), {'accion': 'confirmar', 'codigo': doble_factor.codigo_actual(secreto)})
        self.assertContains(r, 'Códigos de respaldo')
        self.assertTrue(SegundoFactor.objects.get(usuario=self.admin).activo)

    # ---------------------------------------------------------------- 29. reapertura de periodos
    def test_reabrir_periodo_solo_admin_con_motivo(self):
        PeriodoContable.objects.create(periodo='202401', cerrado=True, pendiente=False)
        contador = User.objects.create_user('conta', password='x')
        contador.groups.add(Group.objects.get_or_create(name=GRUPOS['contabilidad'])[0])
        self.client.force_login(contador)
        self.client.post(reverse('contabilidad:periodos'), {'periodo': '202401', 'accion': 'abrir',
                                                            'motivo': 'Corregir una factura mal registrada'})
        self.assertTrue(PeriodoContable.esta_cerrado('202401'))
        self.client.force_login(self.admin)
        self.client.post(reverse('contabilidad:periodos'), {'periodo': '202401', 'accion': 'abrir', 'motivo': 'x'})
        self.assertTrue(PeriodoContable.esta_cerrado('202401'))
        self.client.post(reverse('contabilidad:periodos'), {'periodo': '202401', 'accion': 'abrir',
                                                            'motivo': 'Corregir una factura mal registrada'})
        self.assertFalse(PeriodoContable.esta_cerrado('202401'))
        self.assertTrue(Bitacora.objects.filter(modelo='contabilidad.PeriodoContable',
                                                motivo__icontains='Corregir').exists())

    # ---------------------------------------------------------------- 26. peso del producto
    def test_guia_recibe_el_peso_de_los_productos(self):
        p = Producto.objects.get(codigo='P003')
        p.peso = D('2.5')
        p.save()
        self.client.force_login(self.admin)
        r = self.client.get(reverse('logistica:nueva') + '?tipo=09')
        self.assertContains(r, '"2.500"')
