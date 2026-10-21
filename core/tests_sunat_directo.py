"""Facturación electrónica directa con SUNAT (v1.46): XML UBL 2.1 firmado (la firma se verifica), envío con CDR
aceptado o rechazado, hash y QR, XML y CDR como sustento, baja con ticket, claves cifradas y configuración. SUNAT se
simula; el envío real al beta se probó aparte. Un escenario por clase."""
import base64
import io
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from lxml import etree

from ventas.models import Venta, VentaItem

from . import sunat, sunat_directo
from .cifrado import descifrar
from .models import EnvioResumenSUNAT, FacturacionConfig, Tercero
from .numeros_letras import monto_en_letras

D = Decimal


def cdr_zip(codigo='0', descripcion='La Factura numero F001-1, ha sido aceptada', notas=()):
    notas_xml = ''.join(f'<cbc:Note>{n}</cbc:Note>' for n in notas)
    xml = (f'<ar:ApplicationResponse xmlns:ar="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2" '
           f'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
           f'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">{notas_xml}'
           f'<cac:DocumentResponse><cac:Response><cbc:ResponseCode>{codigo}</cbc:ResponseCode>'
           f'<cbc:Description>{descripcion}</cbc:Description></cac:Response></cac:DocumentResponse>'
           f'</ar:ApplicationResponse>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('R-20100000001-01-F001-1.xml', xml)
    return base64.b64encode(buf.getvalue()).decode()


def respuesta(cuerpo):
    return etree.fromstring(f'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>{cuerpo}'
                            f'</S:Body></S:Envelope>'.encode())


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_see', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.cfg = FacturacionConfig.actual()
        self.cfg.proveedor, self.cfg.entorno = 'SUNAT', 'BETA'
        self.cfg.save()
        sunat_directo.generar_certificado_prueba(self.cfg)
        self.cliente = Tercero.objects.create(numero_doc='20100070970', nombre='CLIENTE SEE SAC', tipo='CLIENTE',
                                              tipo_doc='6', direccion='AV. LIMA 123')

    def venta(self, tipo='01', serie='F777', numero='00000001', **extra):
        v = Venta.objects.create(tercero=self.cliente, tipo_comprobante=tipo, serie=serie, numero=numero,
                                 forma_pago='CONTADO', descontar_stock=False, **extra)
        VentaItem.objects.create(documento=v, descripcion='Servicio de prueba', cantidad=2, precio_unitario=D('50'))
        v.calcular_totales()
        v.save()
        return v


class XMLFirmadoTest(Base):
    def test_ubl_firmado_y_verificable(self):
        from signxml import XMLVerifier
        v = self.venta()
        xml, digest = sunat_directo.firmar(sunat_directo.xml_comprobante(v))
        arbol = etree.fromstring(xml)
        q = lambda p, n: sunat_directo._q(p, n)
        self.assertEqual(arbol.findtext(q('cbc', 'ID')), 'F777-1')
        self.assertEqual(arbol.find(q('cbc', 'InvoiceTypeCode')).get('listID'), '0101')
        self.assertEqual(arbol.findtext(f'{q("cac", "LegalMonetaryTotal")}/{q("cbc", "PayableAmount")}'), '118.00')
        self.assertEqual(arbol.findtext(f'{q("cac", "PaymentTerms")}/{q("cbc", "PaymentMeansID")}'), 'Contado')
        firma = arbol.find('.//' + q('ds', 'Signature'))
        self.assertEqual(etree.QName(firma.getparent()).localname, 'ExtensionContent')
        _, cert = sunat_directo._pem(self.cfg)
        XMLVerifier().verify(xml, x509_cert=cert)  # lanza excepción si la firma no es válida
        self.assertEqual(digest, firma.findtext('.//' + q('ds', 'DigestValue')))
        self.assertEqual(monto_en_letras(D('118')), 'SON CIENTO DIECIOCHO CON 00/100 SOLES')
        self.assertEqual(monto_en_letras(D('1521.5'), 'USD'),
                         'SON MIL QUINIENTOS VEINTIUNO CON 50/100 DÓLARES AMERICANOS')


class EnvioAceptadoTest(Base):
    def test_cdr_aceptado_con_hash_qr_y_sustentos(self):
        v = self.venta()
        with patch('core.sunat_directo._soap', return_value=respuesta(
                f'<ns2:sendBillResponse xmlns:ns2="http://service.sunat.gob.pe"><applicationResponse>{cdr_zip()}'
                f'</applicationResponse></ns2:sendBillResponse>')) as soap:
            sunat.enviar_comprobante(v)
        operacion, cuerpo = soap.call_args[0][:2]
        self.assertEqual(operacion, 'sendBill')
        self.assertIn(f'<fileName>{sunat_directo.nombre_archivo("01", "F777", "1")}.zip</fileName>', cuerpo)
        v.refresh_from_db()
        self.assertEqual(v.estado_sunat, 'ACEPTADO')
        self.assertIn('BETA', v.sunat_descripcion)
        self.assertTrue(v.codigo_hash and v.cadena_qr.endswith(v.codigo_hash))
        self.assertTrue(v.enlace_xml and v.enlace_cdr)
        self.assertEqual(self.client.get(v.enlace_xml).status_code, 302)  # sin sesión: pide ingresar
        # los botones descargan el XML firmado y la CDR de este comprobante (no otro archivo)
        self.client.force_login(self.admin)
        r = self.client.get(v.enlace_xml)
        self.assertTrue(r['Content-Disposition'].startswith('attachment'))
        self.assertIn(f'{sunat_directo.nombre_archivo("01", "F777", "1")}.xml', r['Content-Disposition'])
        self.assertIn(b'<cbc:ID>F777-1</cbc:ID>', r.content)
        r = self.client.get(v.enlace_cdr)
        self.assertIn(f'R-{sunat_directo.nombre_archivo("01", "F777", "1")}.xml', r['Content-Disposition'])
        self.assertIn(b'<cbc:ResponseCode>0</cbc:ResponseCode>', r.content)


class RechazoTest(Base):
    def test_rechazo_y_falla(self):
        v = self.venta()
        with patch('core.sunat_directo._soap', side_effect=sunat_directo.ErrorSunatDirecto(
                'SUNAT rechazó el envío (2017): El numero de documento de identidad del receptor debe ser RUC',
                '2017')):
            sunat.enviar_comprobante(v)
        v.refresh_from_db()
        self.assertEqual(v.estado_sunat, 'RECHAZADO')
        otra = self.venta(numero='00000002')
        with patch('core.sunat_directo._soap', side_effect=sunat_directo.ErrorSunatDirecto(
                'SUNAT rechazó el envío (0111): No tiene el perfil para enviar comprobantes electronicos', '0111')):
            with self.assertRaises(sunat.ErrorFacturacion):
                sunat.enviar_comprobante(otra)
        otra.refresh_from_db()
        self.assertEqual(otra.estado_sunat, 'ERROR')
        con_bolsas = self.venta(numero='00000003', icbper=D('0.50'))  # ICBPER aún no va directo
        with self.assertRaises(sunat.ErrorValidacion):
            sunat.enviar_comprobante(con_bolsas)
        con_bolsas.refresh_from_db()
        self.assertEqual(con_bolsas.estado_sunat, 'NO_ENVIADO')


class BajaConTicketTest(Base):
    def test_comunicacion_de_baja(self):
        v = self.venta()
        respuestas = [
            respuesta('<ns2:sendSummaryResponse xmlns:ns2="http://service.sunat.gob.pe"><ticket>1792000000001'
                      '</ticket></ns2:sendSummaryResponse>'),
            sunat_directo.ErrorSunatDirecto('401 Authorization Required'),  # el beta a veces falla al inicio
            respuesta(f'<ns2:getStatusResponse xmlns:ns2="http://service.sunat.gob.pe"><status><content>'
                      f'{cdr_zip(descripcion="La Comunicacion de baja RA-1, ha sido aceptada")}</content>'
                      f'<statusCode>0</statusCode></status></ns2:getStatusResponse>')]
        with patch('core.sunat_directo._soap', side_effect=respuestas) as soap, patch('time.sleep'):
            sunat.anular_comprobante(v, 'ERROR EN EL PRECIO')
        self.assertEqual([c[0][0] for c in soap.call_args_list], ['sendSummary', 'getStatus', 'getStatus'])
        envio = EnvioResumenSUNAT.objects.get()
        self.assertEqual((envio.tipo, envio.estado, envio.ticket), ('RA', 'ACEPTADO', '1792000000001'))
        self.assertTrue(envio.identificador.startswith('RA-'))
        v.refresh_from_db()
        self.assertEqual(v.estado_sunat, 'BAJA')
        self.assertIn('aceptada', v.sunat_descripcion)


class ConfiguracionTest(Base):
    def test_claves_cifradas_y_produccion_exige_certificado_real(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('facturacion'), {'proveedor': 'SUNAT', 'entorno': 'PRODUCCION',
                                                       'sol_usuario': 'FACTURA1', 'sol_clave_nueva': 'ClaveSol123'})
        self.assertContains(r, 'el de prueba no sirve')
        self.cfg.refresh_from_db()
        self.assertEqual(self.cfg.entorno, 'BETA')  # no se guardó
        pfx = bytes(self.cfg.certificado)
        clave_pfx = descifrar(self.cfg.certificado_clave)
        r = self.client.post(reverse('facturacion'), {
            'proveedor': 'SUNAT', 'entorno': 'PRODUCCION', 'sol_usuario': 'FACTURA1', 'sol_clave_nueva': 'ClaveSol123',
            'certificado_archivo': SimpleUploadedFile('empresa.pfx', pfx), 'certificado_clave_nueva': clave_pfx})
        self.assertRedirects(r, reverse('facturacion'), fetch_redirect_response=False)
        self.cfg.refresh_from_db()
        self.assertEqual((self.cfg.entorno, self.cfg.certificado_prueba, self.cfg.certificado_nombre),
                         ('PRODUCCION', False, 'empresa.pfx'))
        self.assertNotIn('ClaveSol123', self.cfg.sol_clave)
        self.assertEqual(descifrar(self.cfg.sol_clave), 'ClaveSol123')
        self.assertTrue(self.cfg.activa)
        r = self.client.post(reverse('facturacion'), {
            'proveedor': 'SUNAT', 'entorno': 'BETA', 'certificado_archivo': SimpleUploadedFile('x.pfx', pfx),
            'certificado_clave_nueva': 'clave-incorrecta'})
        self.assertContains(r, 'No se pudo abrir el certificado')
        self.assertContains(self.client.get(reverse('facturacion')), 'SUNAT directo')
