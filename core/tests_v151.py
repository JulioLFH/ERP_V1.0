"""v1.51: envío directo a SUNAT de facturas con detracción, retención del IGV y operaciones gratuitas; guías de
remisión remitente (09) y transportista (31) por la API REST de SUNAT (token, envío con ticket, constancia con QR);
lista de lo que falta para producción. SUNAT se simula; las facturas se probaron aparte en el beta (las guías no
tienen entorno de pruebas en SUNAT). Un escenario por clase."""
import base64
import hashlib
import io
import json
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from lxml import etree

from logistica.models import Conductor, GuiaItem, GuiaRemision, Vehiculo
from ventas.models import Venta, VentaItem

from . import sunat, sunat_directo, sunat_gre
from .cifrado import cifrar, descifrar
from .models import Empresa, FacturacionConfig, Tercero

D = Decimal
QR = 'https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr?hashqr=ABC123'


def q(prefijo, nombre):
    return sunat_directo._q(prefijo, nombre)


def cdr_guia(codigo='0', descripcion='La Guia numero T777-1, ha sido aceptada'):
    xml = (f'<ar:ApplicationResponse xmlns:ar="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2" '
           f'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
           f'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
           f'<cac:DocumentResponse><cac:Response><cbc:ResponseCode>{codigo}</cbc:ResponseCode>'
           f'<cbc:Description>{descripcion}</cbc:Description></cac:Response><cac:DocumentReference>'
           f'<cbc:DocumentDescription>{QR}</cbc:DocumentDescription></cac:DocumentReference>'
           f'</cac:DocumentResponse></ar:ApplicationResponse>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('R-guia.xml', xml)
    return base64.b64encode(buf.getvalue()).decode()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v151', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        cache.clear()
        self.cfg = FacturacionConfig.actual()
        self.cfg.proveedor, self.cfg.entorno = 'SUNAT', 'BETA'
        self.cfg.save()
        sunat_directo.generar_certificado_prueba(self.cfg)
        self.cliente = Tercero.objects.create(numero_doc='20100070970', nombre='CLIENTE V151 SAC', tipo='CLIENTE',
                                              tipo_doc='6', direccion='AV. LIMA 123', ubigeo='150101')

    def venta(self, tipo='01', serie='F777', precio=D('1000'), **extra):
        extra.setdefault('forma_pago', 'CONTADO')
        v = Venta.objects.create(tercero=self.cliente, tipo_comprobante=tipo, serie=serie, numero='00000001',
                                 descontar_stock=False, **extra)
        VentaItem.objects.create(documento=v, descripcion='Servicio de prueba', cantidad=2, precio_unitario=precio)
        v.calcular_totales()
        v.save()
        return v

    def xml(self, venta):
        from signxml import XMLVerifier
        firmado, _ = sunat_directo.firmar(sunat_directo.xml_comprobante(venta))
        XMLVerifier().verify(firmado, x509_cert=sunat_directo._pem(self.cfg)[1])
        return etree.fromstring(firmado)

    def guia(self, tipo='09', modalidad='02', **extra):
        if 'vehiculo' not in extra:
            extra['vehiculo'] = Vehiculo.objects.create(placa='V1A-517', certificado='15M21028296E')
        if 'conductor' not in extra:
            extra['conductor'] = Conductor.objects.create(numero_doc='44556677', nombres='JUAN',
                                                          apellidos='PEREZ RUIZ', licencia='Q44556677')
        datos = dict(tipo=tipo, serie='T777' if tipo == '09' else 'V777', numero='1', destinatario=self.cliente,
                     modalidad=modalidad, partida_ubigeo='150101',
                     partida_direccion='AV. PARTIDA 1', llegada_ubigeo='150131', llegada_direccion='AV. LLEGADA 2',
                     peso_bruto=D('12.5'), numero_bultos=2)
        datos.update(extra)
        g = GuiaRemision.objects.create(**datos)
        GuiaItem.objects.create(documento=g, descripcion='PRODUCTO X', unidad='NIU', cantidad=3)
        return g

    def produccion(self):
        self.cfg.entorno, self.cfg.sol_usuario, self.cfg.sol_clave = 'PRODUCCION', 'GUIAS1', cifrar('ClaveSol1')
        self.cfg.gre_client_id, self.cfg.gre_client_secret = 'id-gre-prueba', cifrar('secreto-gre')
        self.cfg.save()


class DetraccionTest(Base):
    def test_factura_con_detraccion_cuenta_bn_y_neto_al_credito(self):
        from datetime import timedelta

        from django.utils import timezone
        v = self.venta(detraccion_pct=D('12'), detraccion_codigo='25', forma_pago='CREDITO',
                       fecha_vencimiento=timezone.localdate() + timedelta(days=30))
        self.assertEqual((v.total, v.detraccion_monto), (D('2360.00'), D('283.20')))
        arbol = self.xml(v)
        self.assertEqual(arbol.find(q('cbc', 'InvoiceTypeCode')).get('listID'), '1001')
        self.assertIn('2006', [n.get('languageLocaleID') for n in arbol.findall(q('cbc', 'Note'))])
        medio = arbol.find(q('cac', 'PaymentMeans'))
        self.assertEqual(medio.findtext(q('cbc', 'ID')), 'Detraccion')
        self.assertEqual(medio.findtext(f"{q('cac', 'PayeeFinancialAccount')}/{q('cbc', 'ID')}"),
                         sunat_directo.cuenta_detracciones())
        terminos = {t.findtext(q('cbc', 'PaymentMeansID')): t for t in arbol.findall(q('cac', 'PaymentTerms'))}
        self.assertEqual(terminos['027'].findtext(q('cbc', 'ID')), 'Detraccion')  # 027 transporte de carga
        self.assertEqual(terminos['027'].find(q('cbc', 'Amount')).get('currencyID'), 'PEN')
        self.assertEqual(terminos['027'].findtext(q('cbc', 'Amount')), '283.20')
        self.assertEqual(terminos['Credito'].findtext(q('cbc', 'Amount')), '2076.80')  # total − detracción
        # en dólares la detracción va en soles
        dolares = self.venta(serie='F778', detraccion_pct=D('12'), moneda='USD', tipo_cambio=D('3.750'))
        monto = [t for t in self.xml(dolares).findall(q('cac', 'PaymentTerms'))
                 if t.findtext(q('cbc', 'ID')) == 'Detraccion'][0].findtext(q('cbc', 'Amount'))
        self.assertEqual(monto, f'{dolares.detr_pen:.2f}')
        # sin cuenta del Banco de la Nación o en boleta: no se envía
        from finanzas.models import Cuenta
        Cuenta.objects.filter(es_detracciones=True).update(es_detracciones=False)
        errores = ' '.join(sunat_directo.validar(v))
        self.assertIn('cuenta de detracciones', errores)
        boleta = self.venta(tipo='03', serie='B777', detraccion_pct=D('12'))
        self.assertIn('solo va en facturas', ' '.join(sunat_directo.validar(boleta)))


class GratuitaRetencionTest(Base):
    def test_gratuita_y_retencion(self):
        g = self.venta(tipo_operacion='GRATUITA')
        self.assertEqual(g.total, D('0'))
        self.assertEqual(sunat_directo.validar(g), [])
        arbol = self.xml(g)
        self.assertIn('1002', [n.get('languageLocaleID') for n in arbol.findall(q('cbc', 'Note'))])
        sub = arbol.find(f"{q('cac', 'TaxTotal')}/{q('cac', 'TaxSubtotal')}")
        self.assertEqual(sub.findtext(f".//{q('cbc', 'ID')}"), '9996')
        self.assertEqual(sub.findtext(q('cbc', 'TaxableAmount')), '2000.00')
        linea = arbol.find(q('cac', 'InvoiceLine'))
        self.assertEqual(linea.findtext(f".//{q('cbc', 'PriceTypeCode')}"), '02')
        self.assertEqual(linea.findtext(f"{q('cac', 'Price')}/{q('cbc', 'PriceAmount')}"), '0.0000')
        self.assertEqual(arbol.findtext(f"{q('cac', 'LegalMonetaryTotal')}/{q('cbc', 'PayableAmount')}"), '0.00')
        r = self.venta(serie='F778', retencion_pct=D('3'))
        cargo = self.xml(r).find(q('cac', 'AllowanceCharge'))
        self.assertEqual(cargo.findtext(q('cbc', 'AllowanceChargeReasonCode')), '62')
        self.assertEqual(cargo.findtext(q('cbc', 'Amount')), f'{r.retencion_monto:.2f}')
        self.assertEqual(cargo.findtext(q('cbc', 'BaseAmount')), '2360.00')


class XMLGuiaTest(Base):
    def test_remitente_transportista_y_publico(self):
        from signxml import XMLVerifier
        g = self.guia(doc_relacionado='01 F001-15')
        firmado, _ = sunat_directo.firmar(sunat_gre.xml_guia(g))
        XMLVerifier().verify(firmado, x509_cert=sunat_directo._pem(self.cfg)[1])
        a = etree.fromstring(firmado)
        self.assertEqual(etree.QName(a).localname, 'DespatchAdvice')
        self.assertEqual(a.findtext(q('cbc', 'DespatchAdviceTypeCode')), '09')
        self.assertEqual(a.findtext(f".//{q('cbc', 'HandlingCode')}"), '01')
        self.assertEqual(a.findtext(f".//{q('cbc', 'TransportModeCode')}"), '02')
        self.assertEqual(a.findtext(f".//{q('cac', 'TransportEquipment')}/{q('cbc', 'ID')}"), 'V1A517')
        self.assertEqual(a.findtext(f".//{q('cac', 'DriverPerson')}/{q('cbc', 'FirstName')}"), 'JUAN')
        self.assertEqual(a.findtext(f".//{q('cac', 'AdditionalDocumentReference')}/{q('cbc', 'ID')}"), 'F001-15')
        self.assertEqual(a.findtext(f".//{q('cac', 'DeliveryAddress')}/{q('cbc', 'ID')}"), '150131')
        # transportista: registro MTC del emisor y el remitente en Despatch/DespatchParty, sin motivo
        e = Empresa.actual()
        e.registro_mtc = '1578652-CNG'
        e.save()
        t = self.guia(tipo='31', remitente=self.cliente, vehiculo=Vehiculo.objects.create(placa='B2C-333'),
                      conductor=Conductor.objects.create(numero_doc='11223344', nombres='ANA', apellidos='LOPEZ',
                                                         licencia='Q11223344'))
        a = sunat_gre.xml_guia(t)
        self.assertEqual(a.findtext(f"{q('cac', 'DespatchSupplierParty')}//{q('cbc', 'CompanyID')}"), '1578652CNG')
        self.assertEqual(a.findtext(f".//{q('cac', 'DespatchParty')}//{q('cbc', 'RegistrationName')}"),
                         'CLIENTE V151 SAC')
        self.assertIsNone(a.find(f".//{q('cbc', 'HandlingCode')}"))
        # remitente con transporte público: empresa de transporte, sin vehículo ni conductor
        transporte = Tercero.objects.create(numero_doc='20512345678', nombre='TRANSPORTES SAC', tipo='PROVEEDOR',
                                            tipo_doc='6', registro_mtc='MTC-999')
        p = self.guia(numero='3', modalidad='01', transportista=transporte, vehiculo=Vehiculo.objects.create(placa='C3D-444'),
                      conductor=Conductor.objects.create(numero_doc='99887766', nombres='LUIS', apellidos='DIAZ',
                                                         licencia='Q99887766'))
        a = sunat_gre.xml_guia(p)
        self.assertEqual(a.findtext(f".//{q('cac', 'CarrierParty')}//{q('cbc', 'CompanyID')}"), 'MTC999')
        self.assertIsNone(a.find(f".//{q('cac', 'DriverPerson')}"))
        self.assertIsNone(a.find(f".//{q('cac', 'TransportEquipment')}"))


class GuiaBetaTest(Base):
    def test_beta_genera_y_firma_sin_enviar(self):
        g = self.guia()
        with patch('core.sunat_gre._http') as http:
            sunat.enviar_guia(g)
        http.assert_not_called()
        g.refresh_from_db()
        self.assertEqual(g.estado_sunat, 'NO_ENVIADO')
        self.assertIn('no tiene entorno de pruebas', g.sunat_descripcion)
        self.assertTrue(g.enlace_xml and g.codigo_hash)


class GuiaProduccionTest(Base):
    def test_token_envio_ticket_y_constancia(self):
        self.produccion()
        g = self.guia()
        respuestas = [(200, {'access_token': 'tok-1', 'expires_in': 3600}), (200, {'numTicket': 'tk-123'}),
                      (200, {'codRespuesta': '98'}), (200, {'codRespuesta': '0', 'indCdrGenerado': '1',
                                                            'arcCdr': cdr_guia()})]
        with patch('core.sunat_gre._http', side_effect=respuestas) as http, patch('time.sleep'):
            sunat.enviar_guia(g)
        token, envio, consulta = http.call_args_list[0], http.call_args_list[1], http.call_args_list[3]
        self.assertEqual(token[0][2]['username'], f'{Empresa.actual().ruc}GUIAS1')
        self.assertEqual(token[0][2]['scope'], 'https://api-cpe.sunat.gob.pe')
        nombre = sunat_directo.nombre_archivo('09', 'T777', '1')
        self.assertTrue(envio[0][1].endswith(f'/comprobantes/{nombre}'))
        archivo = envio[0][2]['archivo']
        self.assertEqual(archivo['nomArchivo'], f'{nombre}.zip')
        self.assertEqual(archivo['hashZip'], hashlib.sha256(base64.b64decode(archivo['arcGreZip'])).hexdigest())
        self.assertEqual(envio[1]['token'], 'tok-1')
        self.assertTrue(consulta[0][1].endswith('/envios/tk-123'))
        g.refresh_from_db()
        self.assertEqual((g.estado_sunat, g.sunat_ticket, g.cadena_qr), ('ACEPTADO', 'tk-123', QR))
        self.assertTrue(g.enlace_xml and g.enlace_cdr)
        # el token se reutiliza (dura una hora) y «Consultar» usa el ticket
        with patch('core.sunat_gre._http', return_value=(200, {'codRespuesta': '0', 'arcCdr': cdr_guia()})) as http:
            sunat.consultar_guia(g)
        self.assertEqual(http.call_count, 1)
        json.dumps(archivo)  # el cuerpo es JSON válido


class GuiaRechazoTest(Base):
    def test_rechazo_sin_cdr_y_credenciales_faltantes(self):
        self.produccion()
        g = self.guia()
        respuestas = [(200, {'access_token': 'tok', 'expires_in': 3600}), (200, {'numTicket': 'tk-9'}),
                      (200, {'codRespuesta': '99', 'indCdrGenerado': '0',
                             'error': {'numError': '2775', 'desError': 'El conductor no tiene licencia válida'}})]
        with patch('core.sunat_gre._http', side_effect=respuestas), patch('time.sleep'):
            sunat.enviar_guia(g)
        g.refresh_from_db()
        self.assertEqual(g.estado_sunat, 'RECHAZADO')
        self.assertIn('2775', g.sunat_descripcion)
        # SUNAT no entrega token: error de envío con el motivo
        otra = self.guia(numero='2', vehiculo=Vehiculo.objects.create(placa='Z9Z-999'),
                         conductor=Conductor.objects.create(numero_doc='12121212', nombres='A', apellidos='B',
                                                            licencia='Q12121212'))
        cache.clear()
        with patch('core.sunat_gre._http', return_value=(401, {'error': 'invalid_client'})):
            with self.assertRaises(sunat.ErrorFacturacion):
                sunat.enviar_guia(otra)
        otra.refresh_from_db()
        self.assertEqual(otra.estado_sunat, 'ERROR')
        self.assertIn('invalid_client', otra.sunat_descripcion)
        # sin credenciales de la API de guías
        self.cfg.gre_client_id = ''
        self.cfg.save()
        with self.assertRaises(sunat.ErrorFacturacion):
            sunat.enviar_guia(otra)
        otra.refresh_from_db()
        self.assertIn('client_id', otra.sunat_descripcion)


class ListaProduccionTest(Base):
    def test_lista_y_credenciales_cifradas(self):
        lista = dict((texto, estado) for estado, texto in sunat_directo.lista_produccion(self.cfg))
        certificado = next(t for t in lista if t.startswith('Certificado digital'))
        self.assertEqual(lista[certificado], 'falta')  # el de prueba no sirve en producción
        self.client.force_login(self.admin)
        r = self.client.get(reverse('facturacion'))
        self.assertContains(r, 'Para emitir en producción')
        self.assertContains(r, 'Guías: client_id')
        r = self.client.post(reverse('facturacion'), {'proveedor': 'SUNAT', 'entorno': 'BETA',
                                                       'gre_client_id': ' id-123 ',
                                                       'gre_client_secret_nuevo': 'secreto-xyz'})
        self.assertRedirects(r, reverse('facturacion'), fetch_redirect_response=False)
        self.cfg.refresh_from_db()
        self.assertEqual(self.cfg.gre_client_id, 'id-123')
        self.assertNotIn('secreto-xyz', self.cfg.gre_client_secret)
        self.assertEqual(descifrar(self.cfg.gre_client_secret), 'secreto-xyz')
