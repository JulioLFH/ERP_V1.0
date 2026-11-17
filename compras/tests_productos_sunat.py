"""v1.116.1: «Agregar productos a compras de SUNAT» — completa de a una las compras de la propuesta SIRE (sin productos):
desde los XML solo si cuadran, o con una línea por compra; los totales registrados no cambian."""
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, Producto, Tercero

from .models import Compra
from .tests_xml_masivo import zip_de
from .tests_xml_sire import HOY, xml_factura


class ProductosSunatTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('prodsunat', 'p@p.com', 'Clave-segura-9')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.ruc = Empresa.actual().ruc
        self.periodo = HOY.strftime('%Y%m')
        Producto.objects.create(codigo='HAR-50', nombre='Harina 50 kg', clase='MERCADERIA')
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000777',
                                           nombre='DISTRIBUIDORA ANDINA SAC')

    def compra(self, numero, base, igv, total, **extra):
        return Compra.objects.create(tercero=self.prov, tipo_comprobante='01', serie='F001', numero=numero,
                                     fecha_emision=HOY, periodo=self.periodo, base_imponible=D(base), igv=D(igv),
                                     total=D(total), glosa='Creado desde la propuesta SIRE', **extra)

    def subir_y_procesar(self, *xmls):
        r = self.client.post(reverse('compras:productos_sunat_subir'), {'archivos': [zip_de(*xmls)]}).json()
        return [self.client.post(reverse('compras:productos_sunat_procesar'), {'token': r['token'], 'i': a['i']}).json()
                for a in r['archivos']]

    def test_pantalla_lista_las_pendientes(self):
        self.compra('77', '200', '36', '236')
        r = self.client.get(reverse('compras:productos_sunat') + f'?periodo={self.periodo}')
        self.assertContains(r, 'F001-77')
        self.assertContains(r, 'Agregar productos a compras de SUNAT')
        lista = self.client.get(reverse('compras:lista'))
        self.assertContains(lista, reverse('compras:productos_sunat'))

    def test_desde_xml_guarda_solo_si_cuadra(self):
        bien = self.compra('77', '300', '54', '354')
        mal = self.compra('78', '100', '18', '118')
        x77 = xml_factura('20600000777', self.ruc, [('HARINA 50KG', 'HAR-50', '2', '100', '10'),
                                                    ('Flete', 'FLT', '1', '100', '10')], numero='F001-00000077')
        x78 = xml_factura('20600000777', self.ruc, [('Algo', 'X', '2', '100', '10')], numero='F001-00000078')
        x99 = xml_factura('20600000999', self.ruc, [('Otro', 'Y', '1', '10', '10')], numero='F009-00000001')
        res = {r['comprobante']: r for r in self.subir_y_procesar(x77, x78, x99)}
        self.assertEqual(res['01 F001-77']['estado'], 'guardada')
        self.assertEqual([l['descripcion'] for l in res['01 F001-77']['lineas']], ['HARINA 50KG', 'Flete'])
        self.assertEqual(res['01 F001-77']['lineas'][0]['producto'], 'HAR-50')
        self.assertEqual(res['01 F001-78']['estado'], 'no_cuadra')  # XML de 236 frente a una compra de 118
        self.assertEqual(res['01 F009-1']['estado'], 'sin_compra')
        bien.refresh_from_db()
        mal.refresh_from_db()
        self.assertEqual((bien.items.count(), bien.total, bien.base_imponible), (2, D('354'), D('300')))
        self.assertEqual(mal.items.count(), 0)  # no cuadra: no se tocó
        # volver a procesar la misma: ya tiene productos
        x77b = xml_factura('20600000777', self.ruc, [('HARINA 50KG', 'HAR-50', '2', '100', '10'),
                                                     ('Flete', 'FLT', '1', '100', '10')], numero='F001-00000077')
        self.assertEqual(self.subir_y_procesar(x77b)[0]['estado'], 'ya_tenia')

    def test_sin_xml_una_linea_que_cuadra(self):
        c = self.compra('80', '100', '18', '148.00', no_gravado=D('30'), exonerado=D('30'))
        r = self.client.post(reverse('compras:productos_sunat_linea'), {'compra': c.pk}).json()
        self.assertEqual(r['estado'], 'guardada')
        c.refresh_from_db()
        importes = sorted(i.cantidad * i.precio_unitario for i in c.items.all())
        self.assertEqual(importes, [D('30'), D('100')])  # base y no gravado: suman lo registrado
        self.assertEqual(c.total, D('148.00'))
        self.assertIn('Según comprobante F001-80', c.items.first().descripcion)
