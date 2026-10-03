"""Pruebas de módulos estilo Odoo: pantalla de aplicaciones, menú por módulo y permisos."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from ventas.models import Venta


class ModulosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@x.com', 'x')
        cls.vendedor = User.objects.create_user('vendedor', password='x')
        cls.vendedor.groups.add(Group.objects.get(name='Ventas'))

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def test_inicio_muestra_solo_apps_permitidas(self):
        self.client.force_login(self.vendedor)
        r = self.client.get(reverse('home'))
        claves = [a['clave'] for a in r.context['apps']]
        self.assertEqual(claves, ['ventas', 'contactos'])
        self.client.force_login(self.admin)
        r = self.client.get(reverse('home'))
        self.assertIn('contabilidad', [a['clave'] for a in r.context['apps']])

    def test_bloquea_modulos_no_asignados(self):
        self.client.force_login(self.vendedor)
        for nombre in ('compras:lista', 'finanzas:cuentas', 'contabilidad:balance', 'inv_stock', 'usuarios',
                       'dashboard', 'logistica:lista'):
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 403, nombre)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)
        self.assertEqual(self.client.post(reverse('finanzas:transferencia'), {}).status_code, 403)

    def test_pantallas_compartidas_y_menu(self):
        self.client.force_login(self.vendedor)
        self.client.get(reverse('ventas:lista'))
        r = self.client.get(reverse('productos'))  # compartida con Inventario y Compras
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['modulo']['clave'], 'ventas')
        etiquetas = [m['etiqueta'] for m in r.context['menu_modulo']]
        self.assertIn('Cuentas por cobrar', etiquetas)
        self.assertNotIn('Configuración', etiquetas)
        self.assertEqual(self.client.get(reverse('terceros') + '?tipo=CLIENTE').status_code, 200)

    def test_oculta_botones_de_otros_modulos(self):
        self.client.force_login(self.vendedor)
        venta = Venta.objects.filter(tipo_comprobante='01').first()
        r = self.client.get(reverse('ventas:detalle', args=[venta.pk]))
        self.assertNotContains(r, reverse('finanzas:cobranza'))
        self.assertNotContains(r, reverse('logistica:nueva'))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('ventas:detalle', args=[venta.pk]))
        self.assertContains(r, reverse('finanzas:cobranza'))

    def test_admin_gestiona_usuarios(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('usuario_nuevo'), {
            'username': 'contador', 'first_name': 'Ana', 'last_name': 'Ruiz', 'email': 'ana@x.com',
            'is_active': 'on', 'modulos': ['contabilidad', 'finanzas'], 'clave1': 'ClaveSegura2026', 'clave2': 'ClaveSegura2026'})
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username='contador')
        self.assertEqual(set(u.groups.values_list('name', flat=True)), {'Contabilidad', 'Finanzas'})
        self.assertTrue(u.check_password('ClaveSegura2026'))
        # sin módulos ni admin: error
        r = self.client.post(reverse('usuario_nuevo'), {'username': 'x', 'is_active': 'on',
                                                        'clave1': 'ClaveSegura2026', 'clave2': 'ClaveSegura2026'})
        self.assertIn('modulos', r.context['form'].errors)
        # no puede quitarse su propio rol de administrador
        r = self.client.post(reverse('usuario_editar', args=[self.admin.pk]), {
            'username': 'jefe', 'is_active': 'on', 'modulos': ['ventas']})
        self.assertIn('es_admin', r.context['form'].errors)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('contabilidad:balance')).status_code, 200)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 403)

    def test_cambiar_clave(self):
        self.client.force_login(self.vendedor)
        r = self.client.post(reverse('cambiar_clave'), {'old_password': 'x', 'new_password1': 'OtraClave2026',
                                                         'new_password2': 'OtraClave2026'})
        self.assertEqual(r.status_code, 302)
        self.vendedor.refresh_from_db()
        self.assertTrue(self.vendedor.check_password('OtraClave2026'))
