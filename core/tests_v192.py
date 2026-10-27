"""v1.92: buscador general (como el de Odoo) en todos los módulos, con los permisos del usuario."""
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import PerfilUsuario, Producto, Tercero
from core.modulos import GRUPOS

HOY = timezone.localdate()


def _usuario(nombre, modulos, acciones=None, **perfil):
    u = User.objects.create_user(nombre, password='x')
    for m in modulos:
        u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
    if acciones is not None:
        PerfilUsuario.objects.create(usuario=u, acciones=acciones, **perfil)
    return u


class BuscadorGeneralTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from compras.models import OrdenCompra
        from ventas.models import Venta
        cls.admin = User.objects.create_superuser('admin', 'a@a.com', 'x')
        cls.cliente = Tercero.objects.create(tipo='AMBOS', tipo_doc='6', numero_doc='20131370645',
                                             nombre='DISTRIBUIDORA ANDINA SAC')
        Producto.objects.create(codigo='AND-01', nombre='Harina andina', clase='MERCADERIA', unidad='KGM')
        Venta.objects.create(tipo_comprobante='01', serie='F001', numero='777', tercero=cls.cliente, fecha_emision=HOY,
                             vendedor='ana')
        OrdenCompra.objects.create(numero='OC01-00000777', tercero=cls.cliente, estado='APROBADO')

    def test_trae_el_contacto_y_sus_documentos(self):
        from core.busqueda import buscar
        grupos = {g['clave']: g for g in buscar(self.admin, 'andina')}
        self.assertTrue({'contactos', 'productos', 'ventas', 'ordenes'} <= set(grupos))
        self.assertEqual(buscar(self.admin, 'F001-777')[0]['clave'], 'ventas')  # por número de documento
        self.assertEqual(buscar(self.admin, 'a'), [])  # mínimo 2 caracteres

    def test_solo_los_modulos_y_datos_del_usuario(self):
        from core.busqueda import buscar
        comprador = _usuario('comprador', ['compras'], ['compras.oc'])
        claves = {g['clave'] for g in buscar(comprador, 'andina')}
        self.assertIn('ordenes', claves)
        self.assertNotIn('ventas', claves)  # no tiene Ventas
        self.assertNotIn('asientos', claves)
        otro = _usuario('luis', ['ventas'], ['ventas.emitir'], vendedor='luis')
        self.assertNotIn('ventas', {g['clave'] for g in buscar(otro, 'andina')})  # solo sus ventas
        ana = _usuario('ana', ['ventas'], ['ventas.emitir'], vendedor='ana')
        self.assertIn('ventas', {g['clave'] for g in buscar(ana, 'andina')})

    def test_pantalla_y_lista_al_escribir(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('buscar') + '?q=andina')
        self.assertContains(r, 'DISTRIBUIDORA ANDINA SAC')
        self.assertContains(r, 'Órdenes de compra')
        datos = self.client.get(reverse('buscar') + '?q=andina&formato=json').json()
        self.assertEqual(datos['q'], 'andina')
        self.assertTrue(any(g['titulo'] == 'Contactos' for g in datos['grupos']))
        inicio = self.client.get(reverse('home'))
        self.assertContains(inicio, 'id="paleta"')
        self.assertContains(inicio, 'data-inicio')  # en Inicio se abre al escribir o con espacio
        self.assertNotContains(self.client.get(reverse('terceros')), 'data-inicio')  # en las demás, con Ctrl + K

    def test_menus_como_la_paleta_de_odoo(self):
        """Sin texto, los módulos del usuario; con texto, también las opciones del menú (sin tildes)."""
        from core.busqueda import menus
        self.assertIn('Compras', [m['texto'] for m in menus(self.admin)])
        self.assertTrue(any('Órdenes de compra' in m['texto'] for m in menus(self.admin, 'ordenes de compra')))
        comprador = _usuario('comprador2', ['compras'], ['compras.oc'])
        nombres = [m['texto'] for m in menus(comprador)]
        self.assertIn('Compras', nombres)
        self.assertNotIn('Contabilidad', nombres)
        self.client.force_login(comprador)
        datos = self.client.get(reverse('buscar') + '?formato=json&q=').json()
        self.assertTrue(datos['menus'] and not datos['grupos'])
