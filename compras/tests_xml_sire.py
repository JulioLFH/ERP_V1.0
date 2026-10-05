"""Compras desde el XML del proveedor y cruce con la propuesta SIRE (v1.17, punto 20)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import sustentos
from core.models import Empresa, Producto, Tercero

from . import sire
from .models import Compra, CompraItem

D = Decimal
HOY = date.today()


def xml_factura(ruc, receptor, lineas, numero='F001-00000077', nombre='DISTRIBUIDORA ANDINA SAC', total=None,
                raiz='Invoice', referencia=''):
    valor = sum((D(c) * D(p) for _, _, c, p, _ in lineas), D('0')).quantize(D('0.01'))
    igv = sum((D(c) * D(p) * D('0.18') for _, _, c, p, af in lineas if af == '10'), D('0')).quantize(D('0.01'))
    total = total if total is not None else valor + igv
    linea_tag, cant_tag = {'Invoice': ('InvoiceLine', 'InvoicedQuantity'),
                           'CreditNote': ('CreditNoteLine', 'CreditedQuantity')}[raiz]
    det = ''.join(
        f'<cac:{linea_tag}><cbc:ID>{i}</cbc:ID><cbc:{cant_tag} unitCode="NIU">{c}</cbc:{cant_tag}>'
        f'<cbc:LineExtensionAmount currencyID="PEN">{(D(c) * D(p)).quantize(D("0.01"))}</cbc:LineExtensionAmount>'
        f'<cac:TaxTotal><cac:TaxSubtotal><cac:TaxCategory><cbc:TaxExemptionReasonCode>{af}'
        f'</cbc:TaxExemptionReasonCode></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>'
        f'<cac:Item><cbc:Description>{desc}</cbc:Description><cac:SellersItemIdentification><cbc:ID>{cod}</cbc:ID>'
        f'</cac:SellersItemIdentification></cac:Item><cac:Price><cbc:PriceAmount currencyID="PEN">{p}'
        f'</cbc:PriceAmount></cac:Price></cac:{linea_tag}>'
        for i, (desc, cod, c, p, af) in enumerate(lineas, 1))
    ref = (f'<cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>{referencia}</cbc:ID>'
           f'</cac:InvoiceDocumentReference></cac:BillingReference>') if referencia else ''
    total_tag = 'LegalMonetaryTotal'
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<{raiz} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{raiz}-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>{numero}</cbc:ID><cbc:IssueDate>{HOY.isoformat()}</cbc:IssueDate>
 <cbc:DocumentCurrencyCode>PEN</cbc:DocumentCurrencyCode>{ref}
 <cac:AccountingSupplierParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="6">{ruc}</cbc:ID>
 </cac:PartyIdentification><cac:PartyLegalEntity><cbc:RegistrationName>{nombre}</cbc:RegistrationName>
 </cac:PartyLegalEntity></cac:Party></cac:AccountingSupplierParty>
 <cac:AccountingCustomerParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="6">{receptor}</cbc:ID>
 </cac:PartyIdentification></cac:Party></cac:AccountingCustomerParty>
 <cac:TaxTotal><cbc:TaxAmount currencyID="PEN">{igv}</cbc:TaxAmount></cac:TaxTotal>
 <cac:{total_tag}><cbc:LineExtensionAmount currencyID="PEN">{valor}</cbc:LineExtensionAmount>
 <cbc:PayableAmount currencyID="PEN">{total}</cbc:PayableAmount></cac:{total_tag}>{det}</{raiz}>'''
    return SimpleUploadedFile(f'{ruc}-{numero}.xml', xml.encode(), content_type='text/xml')


class CompraXMLTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.ruc_empresa = Empresa.actual().ruc
        self.harina = Producto.objects.create(codigo='HAR-50', nombre='Harina 50 kg', clase='MERCADERIA')

    def test_registra_compra_nueva_con_proveedor_productos_y_sustento(self):
        archivo = xml_factura('20600000011', self.ruc_empresa, [
            ('HARINA PREPARADA X 50KG', 'HAR-50', '10', '100.00', '10'),
            ('FLETE LIMA', 'FL', '1', '50.00', '30')])
        r = self.client.post(reverse('compras:importar_xml'), {'archivo': archivo})
        self.assertRedirects(r, reverse('compras:importar_xml_revisar'))
        r = self.client.get(reverse('compras:importar_xml_revisar'))
        self.assertContains(r, 'se creará como proveedor')
        self.assertContains(r, 'Sugerido por código')
        r = self.client.post(reverse('compras:importar_xml_revisar'), {
            'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO', 'ingresar_almacen': 'on',
            'producto_0': self.harina.pk, 'producto_1': ''})
        c = Compra.objects.get(serie='F001', numero='77')
        self.assertRedirects(r, reverse('compras:detalle', args=[c.pk]))
        self.assertEqual((c.tercero.nombre, c.tercero.tipo), ('DISTRIBUIDORA ANDINA SAC', 'PROVEEDOR'))
        self.assertEqual((c.base_imponible, c.igv, c.total), (D('1000.00'), D('180.00'), D('1230.00')))
        self.assertEqual(c.items.get(producto__isnull=True).afectacion, 'INAFECTA')
        self.harina.refresh_from_db()
        self.assertEqual(self.harina.stock, D('10'))
        self.assertTrue(sustentos.tiene(c))
        # el mismo XML otra vez: duplicada
        r = self.client.post(reverse('compras:importar_xml'), {'archivo': xml_factura(
            '20600000011', self.ruc_empresa, [('HARINA PREPARADA X 50KG', 'HAR-50', '10', '100.00', '10'),
                                              ('FLETE LIMA', 'FL', '1', '50.00', '30')])}, follow=True)
        self.assertContains(r, 'ya está registrada')

    def test_rechaza_otro_receptor_y_total_que_no_cuadra(self):
        r = self.client.post(reverse('compras:importar_xml'), {'archivo': xml_factura(
            '20600000011', '20999999999', [('X', '', '1', '10', '10')])}, follow=True)
        self.assertContains(r, 'está emitida al RUC 20999999999')
        self.client.post(reverse('compras:importar_xml'), {'archivo': xml_factura(
            '20600000011', self.ruc_empresa, [('X', '', '1', '10', '10')], total=D('15.00'))})
        r = self.client.post(reverse('compras:importar_xml_revisar'), {'clasificacion': 'GASTO',
                                                                      'forma_pago': 'CONTADO'})
        self.assertContains(r, 'no cuadra con el del XML')
        self.assertFalse(Compra.objects.filter(numero='77').exists())

    def test_sugerencia_por_compra_anterior_y_nota_de_credito(self):
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000011', nombre='Andina')
        c = Compra.objects.create(tercero=prov, serie='F001', numero='50', fecha_emision=HOY)
        CompraItem.objects.create(documento=c, producto=self.harina, descripcion='HARINA ESPECIAL', cantidad=1,
                                  precio_unitario=D('90'))
        c.calcular_totales()
        c.save()
        archivo = xml_factura('20600000011', self.ruc_empresa, [('HARINA ESPECIAL', '', '1', '90.00', '10')],
                              numero='FC01-00000003', raiz='CreditNote', referencia='F001-00000050')
        self.client.post(reverse('compras:importar_xml'), {'archivo': archivo})
        r = self.client.get(reverse('compras:importar_xml_revisar'))
        self.assertContains(r, 'Sugerido por compra anterior')
        self.assertContains(r, 'Nota de crédito')
        r = self.client.post(reverse('compras:importar_xml_revisar'), {
            'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO', 'producto_0': self.harina.pk})
        nc = Compra.objects.get(serie='FC01')
        self.assertEqual((nc.tipo_comprobante, nc.doc_referencia, nc.total), ('07', c, D('106.20')))


class SireTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000022', nombre='Andina')
        crear = lambda numero, precio: self._compra(numero, precio)
        self.ok = crear('101', '100')
        self.dif = crear('102', '200')
        self.solo_erp = crear('103', '50')

    def _compra(self, numero, precio):
        c = Compra.objects.create(tercero=self.prov, serie='F001', numero=numero, fecha_emision=date(2026, 9, 10))
        CompraItem.objects.create(documento=c, descripcion='Servicio', cantidad=1, precio_unitario=D(precio))
        c.calcular_totales()
        c.save()
        return c

    def test_cruce_con_propuesta(self):
        periodo = self.ok.periodo
        cab = ('RUC|Apellidos y Nombres o Razón social|Periodo|CAR SUNAT|Fecha de emisión|Fecha Vcto/Pago|'
               'Tipo CP/Doc.|Serie del CDP|Año|Nro CP o Doc. Nro Inicial (Rango)|Nro Final (Rango)|'
               'Tipo Doc Identidad|Nro Doc Identidad|Apellidos Nombres/ Razón Social|BI Gravado DG|IGV / IPM DG|'
               'Total CP|Moneda')
        fila = lambda numero, base, igv, total: (
            f'20100000001|MI EMPRESA|{periodo}|X|10/09/2026||01|F001||{numero}||6|20600000022|ANDINA SAC|'
            f'{base}|{igv}|{total}|PEN')
        txt = '\n'.join([cab, fila('00000101', '100.00', '18.00', '118.00'), fila('102', '210.00', '37.80', '247.80'),
                         fila('104', '80.00', '14.40', '94.40')])
        registros = sire.leer(SimpleUploadedFile('propuesta.txt', txt.encode('latin-1')))
        self.assertEqual((len(registros), registros[0]['numero'], registros[0]['ruc']), (3, '101', '20600000022'))
        r = sire.comparar(registros, periodo)
        self.assertEqual([x['compra'] for x in r['coinciden']], [self.ok])
        self.assertEqual((r['diferencias'][0]['compra'], r['diferencias'][0]['dif']), (self.dif, D('-11.80')))
        self.assertEqual([x['numero'] for x in r['solo_sunat']], ['104'])
        self.assertEqual([c for c in r['solo_erp'] if c.tercero == self.prov], [self.solo_erp])  # y las del demo
        # por la pantalla y exportación del último cruce
        resp = self.client.post(reverse('compras:sire'), {
            'periodo': f'{periodo[:4]}-{periodo[4:]}',
            'archivo': SimpleUploadedFile('propuesta.txt', txt.encode('latin-1'))})
        self.assertContains(resp, 'Diferencias de importe')
        resp = self.client.get(reverse('compras:sire'), {'formato': 'excel'})
        self.assertIn('spreadsheetml', resp['Content-Type'])
        with self.assertRaises(sire.ErrorSIRE):
            sire.leer(SimpleUploadedFile('x.txt', b'a|b\n1|2'))
