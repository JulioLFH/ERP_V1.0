"""Descuentos por línea y listas de precios por cliente / volumen (v1.17, punto 15)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from core import sunat
from core.models import Empresa, Producto, Tercero

from .models import ListaPrecios, PrecioLista, Venta

D = Decimal
ITEMS = {'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000'}


class PreciosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        Empresa.objects.update(bloquear_deuda_vencida=False)
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        self.prod = Producto.objects.get(codigo='P002')
        self.lista = ListaPrecios.objects.create(codigo='MAY', nombre='Mayoristas')
        PrecioLista.objects.create(lista=self.lista, producto=self.prod, cantidad_minima=1, precio=D('450'))
        PrecioLista.objects.create(lista=self.lista, producto=self.prod, cantidad_minima=10, precio=D('450'),
                                   descuento_pct=D('5'))

    def _precio(self, **q):
        return self.client.get(reverse('ventas:precio'), {'producto': self.prod.pk, **q}).json()

    def test_precio_segun_cliente_lista_y_tramo(self):
        self.assertEqual(self._precio(tercero=self.cli.pk)['lista'], None)  # sin lista: precio del producto
        self.assertEqual(D(self._precio(tercero=self.cli.pk)['precio']), self.prod.precio_venta)
        self.cli.lista_precios = self.lista
        self.cli.save()
        r = self._precio(tercero=self.cli.pk, cantidad='3')
        self.assertEqual((D(r['precio']), D(r['descuento']), r['lista']), (D('450'), D('0'), 'MAY'))
        r = self._precio(tercero=self.cli.pk, cantidad='12')  # tramo por volumen
        self.assertEqual((D(r['precio']), D(r['descuento'])), (D('450'), D('5')))
        # lista vencida: vuelve al precio del producto
        self.lista.vigente_hasta = date.today() - timedelta(days=1)
        self.lista.save()
        self.assertEqual(D(self._precio(tercero=self.cli.pk)['precio']), self.prod.precio_venta)

    def test_venta_con_descuento_totales_y_sunat(self):
        data = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': self.cli.pk,
                'fecha_emision': date.today().isoformat(), 'fecha_vencimiento': '', 'forma_pago': 'CONTADO',
                'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0',
                'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '',
                'descontar_stock': '', 'glosa': '', 'lista_precios': self.lista.pk, 'items-TOTAL_FORMS': '1',
                'items-0-producto': self.prod.pk, 'items-0-descripcion': self.prod.nombre, 'items-0-cantidad': '2',
                'items-0-precio_unitario': '500', 'items-0-descuento_pct': '10', **ITEMS}
        r = self.client.post(reverse('ventas:nuevo'), data)
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context.get('form').errors)
        v = Venta.objects.latest('id')
        item = v.items.get()
        self.assertEqual((item.bruto, item.descuento, item.subtotal), (D('1000.00'), D('100.00'), D('900.00')))
        self.assertEqual((v.base_imponible, v.igv, v.total), (D('900.00'), D('162.00'), D('1062.00')))
        self.assertEqual(v.lista_precios, self.lista)
        p = sunat.payload_comprobante(v)
        self.assertEqual((p['items'][0]['descuento'], p['items'][0]['subtotal'], p['items'][0]['valor_unitario']),
                         (100.0, 900.0, 500.0))
        self.assertEqual(p['total_gravada'], 900.0)

    def test_descuento_invalido(self):
        data = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': self.cli.pk,
                'fecha_emision': date.today().isoformat(), 'forma_pago': 'CONTADO', 'moneda': 'PEN',
                'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0',
                'percepcion_pct': '0', 'icbper': '0', 'items-TOTAL_FORMS': '1', 'items-0-producto': self.prod.pk,
                'items-0-descripcion': 'x', 'items-0-cantidad': '1', 'items-0-precio_unitario': '10',
                'items-0-descuento_pct': '120', **ITEMS}
        n = Venta.objects.count()
        self.assertEqual(self.client.post(reverse('ventas:nuevo'), data).status_code, 200)
        self.assertEqual(Venta.objects.count(), n)

    def test_compra_con_descuento_costea_neto(self):
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        mp = Producto.objects.create(nombre='Insumo X', clase='MERCADERIA', unidad='NIU')
        c = Compra.objects.create(tercero=prov, serie='F001', numero='7777', fecha_emision=date.today())
        CompraItem.objects.create(documento=c, producto=mp, descripcion='Insumo', cantidad=10,
                                  precio_unitario=D('20'), descuento_pct=D('10'))
        c.calcular_totales()
        c.save()
        c.aplicar_stock()
        mp.refresh_from_db()
        self.assertEqual((c.base_imponible, mp.stock, mp.costo_promedio), (D('180.00'), D('10'), D('18.0000')))

    def test_mantenimiento_de_listas(self):
        r = self.client.post(reverse('ventas:lista_precios_nueva'), {
            'codigo': 'WEB', 'nombre': 'Tienda online', 'activa': 'on',
            'precios-TOTAL_FORMS': '2', 'precios-INITIAL_FORMS': '0',
            'precios-0-producto': self.prod.pk, 'precios-0-cantidad_minima': '1', 'precios-0-descuento_pct': '3',
            'precios-1-producto': self.prod.pk, 'precios-1-cantidad_minima': '1', 'precios-1-precio': '400'})
        self.assertEqual(r.status_code, 200)  # tramo repetido: no se guarda
        self.assertFalse(ListaPrecios.objects.filter(codigo='WEB').exists())
        r = self.client.post(reverse('ventas:lista_precios_nueva'), {
            'codigo': 'WEB', 'nombre': 'Tienda online', 'activa': 'on',
            'precios-TOTAL_FORMS': '1', 'precios-INITIAL_FORMS': '0',
            'precios-0-producto': self.prod.pk, 'precios-0-cantidad_minima': '1', 'precios-0-descuento_pct': '3'})
        self.assertRedirects(r, reverse('ventas:listas_precios'))
        lista = ListaPrecios.objects.get(codigo='WEB')
        self.assertEqual(self.prod.precio_para(lista), (self.prod.precio_venta, D('3.00')))
        self.assertContains(self.client.get(reverse('ventas:listas_precios')), 'Tienda online')
        self.assertEqual(self.client.get(reverse('ventas:lista_precios_editar', args=[lista.pk])).status_code, 200)
