"""v1.80: el CRM es un módulo propio (separado de Ventas), con su permiso y sus usuarios."""
import importlib
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import PerfilAcceso, PerfilUsuario, Tercero
from ventas.models import Oportunidad

from .giro import GIROS
from .modulos import POR_CLAVE, menu_de, modulos_del_usuario
from .tests_modulo_tesoreria import Base


class ModuloCRMTest(Base):
    def setUp(self):
        super().setUp()
        self.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970', nombre='UNICON')

    def test_menu_propio_y_ventas_sin_crm(self):
        etiquetas = [h['etiqueta'] for i in menu_de(POR_CLAVE['crm']) for h in (i.get('hijos') or [i])]
        self.assertEqual(etiquetas[:4], ['Embudo de oportunidades', 'Todas las oportunidades', 'Mis oportunidades',
                                         'Nueva oportunidad'])
        for nombre in ('Mis actividades', 'Clientes (ficha 360)', 'Reportes del embudo', 'Campañas'):
            self.assertIn(nombre, etiquetas)
        ventas = [h['url'] for i in menu_de(POR_CLAVE['ventas']) for h in (i.get('hijos') or [i])]
        self.assertFalse(any('/crm/' in u for u in ventas))
        self.assertTrue(all('crm' in modulos for _, modulos, *_ in GIROS.values()))

    def test_usuario_solo_del_crm(self):
        self.client.force_login(self.usuario('vendedor.campo', ['crm']))
        for nombre in ('crm:embudo', 'crm:lista', 'crm:nueva'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        r = self.client.get(reverse('crm:embudo'))
        self.assertEqual(r.context['modulo']['clave'], 'crm')
        self.assertNotContains(r, 'Punto de venta')
        self.assertEqual(self.client.get(reverse('terceros') + '?tipo=CLIENTE').status_code, 200)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 403)
        r = self.client.post(reverse('crm:nueva'), {'nombre': 'Ampliación de planta', 'tercero': self.cliente.pk,
                                                   'etapa': 'PROSPECTO', 'monto': '250000', 'probabilidad': '20',
                                                   'origen': 'REFERIDO'})
        o = Oportunidad.objects.get(nombre='Ampliación de planta')
        self.assertRedirects(r, reverse('crm:oportunidad', args=[o.pk]))
        self.assertIn('contactos', modulos_del_usuario(User.objects.get(username='vendedor.campo')))

    def test_sin_el_permiso_solo_consulta(self):
        o = Oportunidad.objects.create(nombre='Mantenimiento anual', tercero=self.cliente, monto=Decimal('9000'))
        self.client.force_login(self.usuario('consulta', ['crm', 'ventas'], acciones=['ventas.emitir']))
        self.assertEqual(self.client.get(reverse('crm:oportunidad', args=[o.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('crm:nueva'), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse('crm:etapa', args=[o.pk]), {'etapa': 'GANADA'}).status_code, 403)

    def test_vendedor_sin_el_modulo_no_entra(self):
        self.client.force_login(self.usuario('cajera', ['ventas']))
        self.assertEqual(self.client.get(reverse('crm:embudo')).status_code, 403)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)

    def test_enlaces_antiguos_llevan_al_crm(self):
        o = Oportunidad.objects.create(nombre='Cerco perimétrico', tercero=self.cliente, monto=Decimal('50000'))
        self.client.force_login(self.usuario('vendedor', ['crm']))
        r = self.client.get(f'/ventas/crm/{o.pk}/?x=1')
        self.assertRedirects(r, f'/crm/{o.pk}/?x=1')

    def test_quien_tenia_ventas_conserva_el_crm(self):
        """La migración 0063 da el CRM y «crm.registrar» a los usuarios y perfiles que tenían Ventas y emitían."""
        antiguo = self.usuario('vendedor.antiguo', ['ventas'], acciones=['ventas.emitir', 'compras.oc'])
        cajero = self.usuario('cajero.pos', ['ventas'], acciones=['ventas.anular'])
        rol = PerfilAcceso.objects.create(nombre='Vendedor', modulos=['ventas'], acciones=['ventas.emitir'])
        migracion = importlib.import_module('core.migrations.0063_modulo_crm')
        migracion.separar(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        antiguo = User.objects.get(pk=antiguo.pk)
        self.assertIn('crm', modulos_del_usuario(antiguo))
        self.assertEqual(PerfilUsuario.objects.get(usuario=antiguo).acciones,
                         ['compras.oc', 'crm.registrar', 'ventas.emitir'])
        self.assertEqual(PerfilUsuario.objects.get(usuario=cajero).acciones, ['ventas.anular'])
        rol.refresh_from_db()
        self.assertEqual((rol.modulos, rol.acciones), (['ventas', 'crm'], ['crm.registrar', 'ventas.emitir']))
        self.client.force_login(antiguo)
        self.assertEqual(self.client.post(reverse('crm:nueva'), {}).status_code, 200)  # formulario con errores
