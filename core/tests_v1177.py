"""v1.117.7: al asignar un módulo a un usuario (o a un perfil de acceso) se eligen también sus secciones."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.tests_grupo import CLAVE, Base as GrupoBase
from core.usuarios import modulos_con_secciones


class SeccionesDeModuloTest(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('t1177a', 't@t.com', 'x'))

    def test_secciones_de_ventas_salen_del_menu(self):
        ventas = next(m for m in modulos_con_secciones(['ventas']) if m['clave'] == 'ventas')
        titulos = [s['titulo'] for s in ventas['secciones']]
        self.assertIn('Ventas', titulos)
        self.assertIn('Reportes', titulos)
        grupo = next(s for s in ventas['secciones'] if s['titulo'] == 'Ventas')
        self.assertTrue(grupo['grupo'])
        self.assertIn('ventas:lista', [p['clave'] for p in grupo['pantallas']])
        clientes = next(s for s in ventas['secciones'] if s['titulo'] == 'Clientes')
        self.assertIn('Compras', clientes['pantallas'][0]['otros'])  # Clientes y Proveedores: la misma pantalla

    def test_formulario_de_usuario_muestra_las_secciones(self):
        r = self.client.get(reverse('usuario_nuevo'))
        self.assertContains(r, 'class="secciones-modulo small mt-1" data-modulo="ventas"')
        self.assertContains(r, 'data-clave="ventas:registro"')
        self.assertContains(r, 'seccionesSync(marcados, admin.checked)')

    def test_seccion_desmarcada_se_guarda_oculta(self):
        # la casilla solo cambia el selector «pantalla|…» a OCULTA: lo que llega al servidor es ese selector
        r = self.client.post(reverse('usuario_nuevo'), {
            'username': 'vend1177', 'first_name': 'Vend', 'clave1': 'Clave-Segura-1177!', 'clave2': 'Clave-Segura-1177!',
            'is_active': 'on', 'modulos': ['ventas'], 'pantalla|ventas:registro': 'OCULTA',
            'pantalla|ventas:detracciones': 'OCULTA'})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        from core.politicas import ocultas
        u = User.objects.get(username='vend1177')
        self.assertEqual(ocultas(u) & {'ventas:registro', 'ventas:detracciones', 'ventas:lista'},
                         {'ventas:registro', 'ventas:detracciones'})
        r = self.client.get(reverse('usuario_editar', args=[u.pk]))
        self.assertContains(r, '<option value="OCULTA" selected>', count=2)

    def test_perfil_de_acceso_muestra_las_secciones(self):
        r = self.client.get(reverse('perfil_acceso_nuevo'))
        self.assertContains(r, 'data-modulo="ventas"')
        self.assertContains(r, 'seccionesSync(marcados, false)')


class SeccionesDelGrupoTest(GrupoBase):
    """Panel del grupo › Módulos y secciones: una sección apagada no se ve ni se abre en ninguna empresa, ni para los
    administradores, y no se ofrece al asignar usuarios. La pantalla de inicio del módulo no se apaga sola."""

    def setUp(self):
        super().setUp()
        from core import plataforma
        plataforma.invalidar()
        self.addCleanup(plataforma.invalidar)
        User.objects.create_superuser('jefe1177', 'jefe@holding.pe', CLAVE)
        self.ingresar('jefe1177')

    def guardar(self, apagadas, vistas=('ventas:notas', 'ventas:registro', 'ventas:pos')):
        from core.plataforma import FIJOS, modulos_del_plan
        return self.client.post(reverse('grupo_panel'), {
            'accion': 'licencia', 'modulos': sorted(modulos_del_plan() - FIJOS),
            'secciones_vistas': list(vistas), 'secciones': [c for c in vistas if c not in apagadas]})

    def test_apagar_y_prender_una_seccion(self):
        from core.models import PlanGrupo
        r = self.client.get(reverse('grupo_panel'))
        self.assertContains(r, 'Módulos y secciones del grupo')
        self.assertContains(r, 'name="secciones" value="ventas:notas" checked')
        self.assertNotContains(r, 'name="secciones" value="ventas:lista"')  # inicio de Ventas: se apaga con el módulo
        self.guardar({'ventas:notas'})
        self.assertEqual(PlanGrupo.objects.get().secciones_apagadas, ['ventas:notas'])
        # el administrador tampoco la ve ni la abre
        r = self.client.get(reverse('ventas:lista'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, reverse('ventas:notas') + '"')
        r = self.client.get(reverse('ventas:notas'))
        self.assertContains(r, 'desactivada en todas las empresas del grupo', status_code=403)
        # no se ofrece al asignar usuarios
        self.assertNotContains(self.client.get(reverse('usuario_nuevo')), 'data-clave="ventas:notas"')
        # una sección que no vino en el formulario conserva su estado; al prenderla vuelve
        self.guardar(set(), vistas=('ventas:registro',))
        self.assertEqual(PlanGrupo.objects.get().secciones_apagadas, ['ventas:notas'])
        self.guardar(set())
        self.assertEqual(PlanGrupo.objects.get().secciones_apagadas, [])
        self.assertEqual(self.client.get(reverse('ventas:notas')).status_code, 200)

    def test_no_se_apaga_lo_que_no_es_seccion(self):
        from core.models import PlanGrupo
        self.guardar({'ventas:lista', 'ajustes_x'}, vistas=('ventas:lista', 'ajustes_x'))
        self.assertEqual(PlanGrupo.objects.get().secciones_apagadas, [])


class FirmaDelCorreoTest(TestCase):
    """v1.117.8: la firma de los correos muestra el correo con que se envía, no uno fijo."""

    def test_firma_con_el_remitente(self):
        from django.core import mail
        from core import correo
        from core.models import CorreoConfig
        cfg = CorreoConfig.actual()
        cfg.servidor, cfg.puerto, cfg.usuario, cfg.remitente = 'smtp.ejemplo.pe', 587, 'envios@ejemplo.pe', 'avisos@ejemplo.pe'
        cfg.save()
        with self.settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'), \
                __import__('unittest.mock').mock.patch.object(correo, 'get_connection',
                                                              lambda *a, **k: mail.get_connection()):
            correo.enviar(['cliente@ejemplo.pe'], 'Prueba', 'core/correo_simple.html', {'texto': 'Hola', 'asunto': 'Prueba'})
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('mailto:avisos@ejemplo.pe', html)
        self.assertNotIn('gmail.com', html)

    def test_sin_remitente_no_hay_linea_de_correo(self):
        from django.template.loader import render_to_string
        html = render_to_string('core/correo_simple.html', {'texto': 'Hola', 'asunto': 'Prueba'})
        self.assertNotIn('mailto:', html)
        self.assertIn('Administrador de Ceiba ERP', html)
