"""v1.117.4: factura de transporte como la del cliente (Transportes CR). Guías de remisión en la referencia del impreso y
en el XML, detracción sin decimales, valor referencial sin nota, con 1 cuota la factura sale al contado con su condición
de pago en días, el impreso no muestra la versión del ERP y centra los encabezados."""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import sunat, sunat_directo
from core.models import Empresa, Producto, Tercero
from finanzas.models import Cuenta

from .models import Venta


class FacturaTransporteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t1174', 't@t.com', 'x')
        cls.servicio = Producto.objects.get(codigo='P003')
        cls.cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        Tercero.objects.filter(pk=cls.cli.pk).update(dias_credito=15, limite_credito=0, bloqueado_mora=False)
        Cuenta.objects.filter(es_detracciones=True).update(activo=False)
        Cuenta.objects.create(nombre='BN detracciones', banco='BN', es_detracciones=True, numero='00-091-153319')

    def setUp(self):
        Empresa.objects.update(bloquear_deuda_vencida=False)
        self.client.force_login(self.user)
        plazo = patch('core.utils.DIAS_ENVIO_SUNAT', 3650)
        plazo.start()
        self.addCleanup(plazo.stop)

    def emitir(self, **extra):
        datos = {
            'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': self.cli.pk, 'fecha_emision': '2026-10-30',
            'fecha_vencimiento': '', 'forma_pago': 'CREDITO', 'cuotas': '1', 'dias_entre_cuotas': '30', 'moneda': 'PEN',
            'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '4', 'retencion_pct': '0',
            'percepcion_pct': '0', 'icbper': '0', 'detraccion_codigo': '25', 'valor_referencial': '19039.13',
            'guias_referencia': 'GRE-EG03-00000035, GRE-EG03-00000040, GRE-T005-02721882, T005-2721882',
            'vendedor': '', 'glosa': '', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk,
            'items-0-descripcion': 'Servicio de transporte de clinker', 'items-0-cantidad': '180.86',
            'items-0-precio_unitario': '55'}
        datos.update(extra)
        r = self.client.post(reverse('ventas:nuevo'), datos)
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Venta.objects.latest('id')

    def test_detraccion_sin_decimales(self):
        v = self.emitir()
        self.assertEqual(v.detraccion_monto, D('762.00'))  # 4% de 19,039.13 = 761.57
        self.assertEqual(v.detr_pen, D('762.00'))
        v = self.emitir(valor_referencial='0')  # 4% de 11,737.81 = 469.51
        self.assertEqual(v.detraccion_monto, D('470.00'))

    def test_una_cuota_es_contado_con_condicion_de_pago(self):
        v = self.emitir()
        self.assertEqual((v.forma_pago, v.fecha_vencimiento, v.condicion_dias), ('CONTADO', date(2026, 11, 14), 15))
        self.assertEqual(v.cronograma_cuotas, [])
        xml = sunat_directo.xml_comprobante(v)
        ns = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
              'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
        self.assertEqual(xml.xpath("cac:PaymentTerms[cbc:ID='FormaPago']/cbc:PaymentMeansID/text()", namespaces=ns),
                         ['Contado'])
        self.assertEqual(xml.xpath('cbc:DueDate/text()', namespaces=ns), ['2026-11-14'])
        self.assertEqual(sunat.payload_comprobante(v)['condiciones_de_pago'], 'Contado - 15 días')
        # con 2 cuotas sigue siendo crédito
        v = self.emitir(cuotas='2')
        self.assertEqual((v.forma_pago, len(v.cronograma_cuotas)), ('CREDITO', 2))

    def test_una_cuota_sigue_con_el_control_de_credito(self):
        Tercero.objects.filter(pk=self.cli.pk).update(bloqueado_mora=True)
        r = self.client.post(reverse('ventas:nuevo'), {
            'tipo_comprobante': '01', 'tercero': self.cli.pk, 'fecha_emision': '2026-10-30', 'forma_pago': 'CREDITO',
            'cuotas': '1', 'dias_entre_cuotas': '30', 'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
            'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0', 'valor_referencial': '0',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk, 'items-0-descripcion': 'Flete',
            'items-0-cantidad': '1', 'items-0-precio_unitario': '100'})
        self.assertContains(r, 'bloqueado por mora')

    def test_guias_en_el_impreso_y_en_el_xml(self):
        v = self.emitir()
        self.assertEqual(v.guias_citadas, [('31', 'EG03-00000035'), ('31', 'EG03-00000040'), ('09', 'T005-02721882')])
        ns = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
              'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
        xml = sunat_directo.xml_comprobante(v)
        refs = [(r.findtext('cbc:ID', namespaces=ns), r.findtext('cbc:DocumentTypeCode', namespaces=ns))
                for r in xml.findall('cac:DespatchDocumentReference', namespaces=ns)]
        self.assertEqual(refs, [('EG03-35', '31'), ('EG03-40', '31'), ('T005-2721882', '09')])
        hijos = [h.tag.split('}')[1] for h in xml]
        self.assertLess(hijos.index('DespatchDocumentReference'), hijos.index('Signature'))
        self.assertEqual([g['guia_tipo'] for g in sunat.payload_comprobante(v)['guias']], [2, 2, 1])
        r = self.client.get(reverse('ventas:imprimir', args=[v.pk]))
        self.assertContains(r, 'GRE-EG03-00000035, GRE-EG03-00000040, GRE-T005-02721882')
        self.assertContains(r, 'Condición de pago: 15 días')
        self.assertContains(r, 'Valor referencial: S/ 19,039.13</div>')
        self.assertNotContains(r, 'mayor que el importe')
        self.assertNotContains(r, 'generada por')
        self.assertContains(r, 'thead th{text-align:center')
        self.assertContains(r, 'Vista previa: el comprobante aún no fue aceptado por SUNAT.')
        self.assertContains(self.client.get(reverse('ventas:detalle', args=[v.pk])), 'Guías (referencia)')
        self.assertContains(self.client.get(reverse('ventas:nuevo')), 'name="guias_referencia"')
