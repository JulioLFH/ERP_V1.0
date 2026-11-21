"""v1.117.0: la factura importada desde el XML se conecta a su orden de compra (la que cita el XML en OrderReference
o la elegida), toma sus productos y respeta el cruce con lo pedido."""
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Almacen, Empresa, Producto, Tercero

from . import importar_xml
from .models import Compra, OrdenCompra, OrdenCompraItem
from .tests_xml_sire import xml_factura


def con_orden(archivo, numero_oc):
    xml = archivo.read().decode()
    xml = xml.replace('</cbc:DocumentCurrencyCode>',
                      f'</cbc:DocumentCurrencyCode><cac:OrderReference><cbc:ID>{numero_oc}</cbc:ID>'
                      f'</cac:OrderReference>', 1)
    return SimpleUploadedFile(archivo.name, xml.encode(), content_type='text/xml')


class XMLOrdenCompraTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('xmloc', 'x@x.com', 'Clave-segura-9')

    def setUp(self):
        Empresa.objects.update(exigir_orden_compra=False)
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.ruc = Empresa.actual().ruc
        self.harina = Producto.objects.create(codigo='HAR-77', nombre='Harina especial 50 kg', clase='MERCADERIA')
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000779',
                                           nombre='MOLINOS DEL SUR SAC')
        self.oc = OrdenCompra.objects.create(numero='OC01-00000901', tercero=self.prov, estado='APROBADO')
        OrdenCompraItem.objects.create(documento=self.oc, producto=self.harina, descripcion='Harina especial',
                                       cantidad=D('2'), precio_unitario=D('100'))
        self.oc.calcular_totales()
        self.oc.save()

    def xml(self, cantidad='2', oc='OC01-901'):
        archivo = xml_factura('20600000779', self.ruc, [('HARINA ESP. BOLSA', 'ZZ-1', cantidad, '100', '10')],
                              numero='F005-00000031', nombre='MOLINOS DEL SUR SAC')
        return con_orden(archivo, oc) if oc else archivo

    def test_lee_la_orden_citada_y_registra_la_compra_conectada(self):
        r = self.client.post(reverse('compras:importar_xml'), {'archivo': self.xml()})
        self.assertRedirects(r, reverse('compras:importar_xml_revisar'), fetch_redirect_response=False)
        r = self.client.get(reverse('compras:importar_xml_revisar'))
        self.assertContains(r, 'citada en el XML')
        self.assertContains(r, 'Sugerido por orden OC01-00000901')
        r = self.client.post(reverse('compras:importar_xml_revisar'), {
            'orden_compra': self.oc.pk, 'producto_0': self.harina.pk, 'clasificacion': 'MERCADERIA',
            'forma_pago': 'CONTADO', 'ingresar_almacen': 'on', 'almacen': Almacen.principal().pk})
        compra = Compra.objects.get(serie='F005', numero='31')
        self.assertRedirects(r, reverse('compras:detalle', args=[compra.pk]), fetch_redirect_response=False)
        self.assertEqual(compra.orden_compra, self.oc)
        self.assertEqual(compra.items.get().producto, self.harina)

    def test_no_factura_mas_de_lo_pedido(self):
        datos = importar_xml.analizar(self.xml(cantidad='5').read(), 'f.xml')
        oc, _ = importar_xml.orden_sugerida(datos, datos['proveedor'])
        self.assertEqual(oc, self.oc)
        with self.assertRaises(importar_xml.ErrorImportacion):
            importar_xml.registrar(datos, {0: self.harina}, {'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO',
                                                             'ingresar_almacen': True, 'orden_compra': oc})
        self.assertFalse(Compra.objects.filter(serie='F005', numero='31').exists())

    def test_sin_cita_se_sugiere_la_unica_orden_aprobada_y_se_puede_quitar(self):
        datos = importar_xml.analizar(self.xml(oc='').read(), 'f.xml')
        self.assertEqual(importar_xml.orden_sugerida(datos, datos['proveedor']),
                         (self.oc, 'única orden aprobada del proveedor'))
        self.client.post(reverse('compras:importar_xml'), {'archivo': self.xml(oc='')})
        r = self.client.get(reverse('compras:importar_xml_revisar') + '?orden_compra=')
        self.assertNotContains(r, 'Conectada a')

    def test_boton_en_la_orden_y_carga_masiva_conecta_la_citada(self):
        r = self.client.get(reverse('compras:oc_detalle', args=[self.oc.pk]))
        self.assertContains(r, f'{reverse("compras:importar_xml")}?oc={self.oc.pk}')
        # carga masiva (no ingresa al almacén): sin recepción, el cruce no deja conectarla y se registra con aviso
        r = self.client.post(reverse('compras:importar_xml_varios'), {
            'archivos': [self.xml()], 'clasificacion': 'GASTO', 'forma_pago': 'CONTADO'})
        self.assertContains(r, 'No se conectó a la orden OC01-00000901')
        self.assertIsNone(Compra.objects.get(serie='F005', numero='31').orden_compra)

    def test_carga_masiva_conecta_la_orden_citada_de_servicios(self):
        servicio = Producto.objects.get(codigo='S001')
        oc = OrdenCompra.objects.create(numero='OC01-00000902', tercero=self.prov, estado='APROBADO')
        OrdenCompraItem.objects.create(documento=oc, producto=servicio, descripcion='Soporte', cantidad=1,
                                       precio_unitario=D('200'))
        archivo = xml_factura('20600000779', self.ruc, [('SOPORTE MES', 'S-9', '1', '200', '10')],
                              numero='F005-00000032', nombre='MOLINOS DEL SUR SAC')
        self.client.post(reverse('compras:importar_xml_varios'), {
            'archivos': [con_orden(archivo, 'OC01-902')], 'clasificacion': 'SERVICIO', 'forma_pago': 'CONTADO'})
        compra = Compra.objects.get(serie='F005', numero='32')
        self.assertEqual((compra.orden_compra, compra.items.get().producto), (oc, servicio))
