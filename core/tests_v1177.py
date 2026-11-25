"""v1.117.7: al asignar un módulo a un usuario (o a un perfil de acceso) se eligen también sus secciones."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

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
