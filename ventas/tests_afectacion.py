"""Afectación del IGV por línea (v1.14): una factura con ítems gravados, exonerados e inafectos."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import sunat
from core.models import Producto, Tercero

from .models import Venta, VentaItem
from .views import ventas_views

D = Decimal


class AfectacionPorLineaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.cliente = Tercero.objects.filter(tipo_doc='6', tipo__in=['CLIENTE', 'AMBOS']).first()

    def venta_mixta(self):
        v = Venta.objects.create(tercero=self.cliente, serie='F001', numero='99999', tipo_operacion='GRAVADA')
        VentaItem.objects.create(documento=v, descripcion='Laptop', cantidad=1, precio_unitario=D('1000'))
        VentaItem.objects.create(documento=v, descripcion='Libro', cantidad=2, precio_unitario=D('50'),
                                 afectacion='EXONERADA')
        VentaItem.objects.create(documento=v, descripcion='Papa nativa', cantidad=10, precio_unitario=D('3'),
                                 afectacion='INAFECTA')
        v.calcular_totales()
        v.save()
        return v

    def test_totales_por_afectacion(self):
        v = self.venta_mixta()
        self.assertEqual((v.base_imponible, v.igv, v.exonerado, v.inafecto, v.no_gravado),
                         (D('1000'), D('180'), D('100'), D('30'), D('130')))
        self.assertEqual(v.total, D('1310'))
        self.assertEqual(v.exportacion, 0)
        self.assertTrue(v.afectaciones_mixtas)

    def test_sunat_recibe_el_tipo_de_igv_de_cada_linea(self):
        v = self.venta_mixta()
        with patch('core.sunat.validar_comprobante', return_value=[]):
            datos = sunat.payload_comprobante(v)
        self.assertEqual([i['tipo_de_igv'] for i in datos['items']], [1, 8, 9])
        self.assertEqual([i['igv'] for i in datos['items']], [180, 0, 0])
        self.assertEqual((datos['total_gravada'], datos['total_exonerada'], datos['total_inafecta'], datos['total']),
                         (1000, 100, 30, 1310))

    def test_registro_de_ventas_separa_exonerado_e_inafecto(self):
        v = self.venta_mixta()
        campos = ventas_views.linea_ple(v.periodo, 1, v).split('|')
        # 13 exportación, 14 base, 16 IGV, 18 exonerada, 19 inafecta (estructura 14.1)
        self.assertEqual((campos[12], campos[13], campos[15], campos[17], campos[18]),
                         ('0.00', '1000.00', '180.00', '100.00', '30.00'))

    def test_exportacion_y_cabecera_no_gravada(self):
        v = self.venta_mixta()
        v.tipo_operacion = 'EXPORTACION'
        v.calcular_totales()
        self.assertEqual((v.base_imponible, v.igv, v.exportacion), (D('0'), D('0'), D('1130')))
        v.tipo_operacion = 'EXONERADA'  # las líneas sin afectación siguen a la cabecera
        v.calcular_totales()
        self.assertEqual((v.exonerado, v.inafecto, v.igv), (D('1100'), D('30'), D('0')))

    def test_formulario_y_producto_exonerado(self):
        libro = Producto.objects.create(nombre='Libro de texto', clase='MERCADERIA', precio_venta=D('40'),
                                        afectacion_igv='EXONERADA')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('ventas:nuevo'))
        self.assertContains(r, 'js-afectacion')
        self.assertContains(r, '"afectacion_igv": "EXONERADA"')
        self.assertEqual(libro.afectacion_igv, 'EXONERADA')
