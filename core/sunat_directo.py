"""Facturación electrónica directa con SUNAT (sistema de emisión del contribuyente, SEE-Del contribuyente).

El sistema arma el XML UBL 2.1 de la factura, boleta o nota, lo firma con el certificado digital de la empresa
(XMLDSig, firma envolvente en ext:UBLExtensions), lo comprime en ZIP y lo envía por el servicio SOAP de SUNAT
(sendBill). SUNAT devuelve el CDR (constancia de recepción): código 0 = aceptado, 2000-3999 = rechazado, 4000 o más =
aceptado con observaciones. Las bajas de facturas (RA) y los resúmenes de boletas (RC) van con sendSummary y se
consultan con getStatus por ticket.

Entorno de pruebas (beta): https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService, usuario RUC + MODDATOS,
sin certificado registrado (se puede generar uno de prueba). Solo acepta facturas, boletas y notas.
"""
import base64
import io
import secrets
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone
from lxml import etree

from .cifrado import cifrar, descifrar
from .models import Empresa, FacturacionConfig, r2
from .numeros_letras import monto_en_letras

SERVICIO = {'BETA': 'https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService',
            'PRODUCCION': 'https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService'}
CONSULTA_CDR = 'https://e-factura.sunat.gob.pe/ol-it-wsconscpegem/billConsultService'
CLAVE_BETA = 'moddatos'

NS = {
    'Invoice': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'CreditNote': 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2',
    'DebitNote': 'urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2',
    'VoidedDocuments': 'urn:sunat:names:specification:ubl:peru:schema:xsd:VoidedDocuments-1',
    'SummaryDocuments': 'urn:sunat:names:specification:ubl:peru:schema:xsd:SummaryDocuments-1',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2',
    'sac': 'urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
}
RAIZ = {'01': 'Invoice', '03': 'Invoice', '07': 'CreditNote', '08': 'DebitNote'}
# afectación -> (código catálogo 07, id del tributo, nombre, código internacional)
TRIBUTO = {'GRAVADA': ('10', '1000', 'IGV', 'VAT'), 'EXONERADA': ('20', '9997', 'EXO', 'VAT'),
           'INAFECTA': ('30', '9998', 'INA', 'FRE'), 'EXPORTACION': ('40', '9995', 'EXP', 'FRE'),
           'GRATUITA': ('31', '9996', 'GRA', 'FRE')}  # gratuita: inafecto, retiro por bonificación


class ErrorSunatDirecto(Exception):
    def __init__(self, mensaje, codigo=None):
        super().__init__(mensaje)
        self.codigo = codigo


# ---------------------------------------------------------------- certificado
def generar_certificado_prueba(cfg=None):
    """Certificado autofirmado para el entorno beta (SUNAT no exige certificado registrado en pruebas)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    cfg = cfg or FacturacionConfig.actual()
    e = Empresa.actual()
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, 'PE'),
                        x509.NameAttribute(NameOID.ORGANIZATION_NAME, (e.razon_social or 'EMPRESA')[:64]),
                        x509.NameAttribute(NameOID.COMMON_NAME, f'{e.ruc} PRUEBA BETA'[:64])])
    ahora = datetime.utcnow()
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(llave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - timedelta(days=1))
            .not_valid_after(ahora + timedelta(days=730)).sign(llave, hashes.SHA256()))
    clave = secrets.token_urlsafe(16)
    pfx = pkcs12.serialize_key_and_certificates(b'ceiba', llave, cert, None,
                                                serialization.BestAvailableEncryption(clave.encode()))
    guardar_certificado(cfg, pfx, clave, 'certificado-prueba-beta.pfx', prueba=True)
    return cfg


def _leer_pfx(datos, clave):
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        llave, cert, _ = pkcs12.load_key_and_certificates(datos, clave.encode() if clave else None)
    except ValueError as exc:
        raise ErrorSunatDirecto('No se pudo abrir el certificado: revise el archivo .pfx / .p12 y su clave.') from exc
    if llave is None or cert is None:
        raise ErrorSunatDirecto('El archivo no trae la llave privada y el certificado.')
    return llave, cert


def guardar_certificado(cfg, datos, clave, nombre, prueba=False):
    _, cert = _leer_pfx(datos, clave)
    vence = getattr(cert, 'not_valid_after_utc', None) or cert.not_valid_after
    cfg.certificado, cfg.certificado_clave = datos, cifrar(clave)
    cfg.certificado_nombre, cfg.certificado_vence, cfg.certificado_prueba = nombre[:200], vence.date(), prueba
    cfg.save()
    return cfg


def _pem(cfg):
    from cryptography.hazmat.primitives import serialization
    if not cfg.certificado:
        raise ErrorSunatDirecto('Cargue el certificado digital (o genere uno de prueba) en Ajustes › Facturación '
                                'electrónica.')
    llave, cert = _leer_pfx(bytes(cfg.certificado), descifrar(cfg.certificado_clave))
    return (llave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM))


# ---------------------------------------------------------------- XML UBL 2.1
def _q(prefijo, nombre):
    return f'{{{NS[prefijo]}}}{nombre}'


def _e(padre, etiqueta, texto=None, **attrs):
    prefijo, nombre = etiqueta.split(':')
    el = etree.SubElement(padre, _q(prefijo, nombre), {k: str(v) for k, v in attrs.items()})
    if texto is not None:
        el.text = str(texto)
    return el


def _m(valor):
    return f'{Decimal(valor):.2f}'


def _raiz(nombre, prefijos=('cac', 'cbc', 'ext', 'ds')):
    nsmap = {None: NS[nombre], **{p: NS[p] for p in prefijos}}
    raiz = etree.Element(f'{{{NS[nombre]}}}{nombre}', nsmap=nsmap)
    contenido = _e(_e(_e(raiz, 'ext:UBLExtensions'), 'ext:UBLExtension'), 'ext:ExtensionContent')
    etree.SubElement(contenido, _q('ds', 'Signature'), Id='placeholder')
    return raiz


def _firma_cac(raiz, empresa):
    s = _e(raiz, 'cac:Signature')
    _e(s, 'cbc:ID', f'SIGN{empresa.ruc}')
    parte = _e(s, 'cac:SignatoryParty')
    _e(_e(parte, 'cac:PartyIdentification'), 'cbc:ID', empresa.ruc)
    _e(_e(parte, 'cac:PartyName'), 'cbc:Name', empresa.razon_social[:250])
    _e(_e(_e(s, 'cac:DigitalSignatureAttachment'), 'cac:ExternalReference'), 'cbc:URI', f'#SIGN{empresa.ruc}')


def _emisor(raiz, empresa):
    p = _e(_e(raiz, 'cac:AccountingSupplierParty'), 'cac:Party')
    _e(_e(p, 'cac:PartyIdentification'), 'cbc:ID', empresa.ruc, schemeID='6')
    _e(_e(p, 'cac:PartyName'), 'cbc:Name', (empresa.nombre_comercial or empresa.razon_social)[:250])
    legal = _e(p, 'cac:PartyLegalEntity')
    _e(legal, 'cbc:RegistrationName', empresa.razon_social[:250])
    direccion = _e(legal, 'cac:RegistrationAddress')
    if getattr(empresa, 'ubigeo', ''):
        _e(direccion, 'cbc:ID', empresa.ubigeo)
    _e(direccion, 'cbc:AddressTypeCode', '0000')
    if empresa.direccion:
        _e(_e(direccion, 'cac:AddressLine'), 'cbc:Line', empresa.direccion[:200])


def _doc_cliente(cli):
    varios = cli.tipo_doc in ('', None, '0') and cli.numero_doc in ('', '00000000', '-')
    return ('0', '-') if varios else (cli.tipo_doc or '0', cli.numero_doc)


def _cliente(raiz, cli):
    tipo, numero = _doc_cliente(cli)
    p = _e(_e(raiz, 'cac:AccountingCustomerParty'), 'cac:Party')
    _e(_e(p, 'cac:PartyIdentification'), 'cbc:ID', numero, schemeID=tipo)
    legal = _e(p, 'cac:PartyLegalEntity')
    _e(legal, 'cbc:RegistrationName', (cli.nombre or 'CLIENTES VARIOS')[:250])
    if (cli.direccion or '').strip():
        _e(_e(_e(legal, 'cac:RegistrationAddress'), 'cac:AddressLine'), 'cbc:Line', cli.direccion[:200])


def _subtotal_tributo(padre, moneda, base, impuesto, afectacion, tasa=None, linea=False):
    codigo, tid, nombre, intl = TRIBUTO[afectacion]
    sub = _e(padre, 'cac:TaxSubtotal')
    _e(sub, 'cbc:TaxableAmount', _m(base), currencyID=moneda)
    _e(sub, 'cbc:TaxAmount', _m(impuesto), currencyID=moneda)
    cat = _e(sub, 'cac:TaxCategory')
    if linea:
        _e(cat, 'cbc:Percent', _m(tasa or 0))
        _e(cat, 'cbc:TaxExemptionReasonCode', codigo)
    esquema = _e(cat, 'cac:TaxScheme')
    _e(esquema, 'cbc:ID', tid)
    _e(esquema, 'cbc:Name', nombre)
    _e(esquema, 'cbc:TaxTypeCode', intl)


def cuenta_detracciones():
    """N° de la cuenta de detracciones de la empresa en el Banco de la Nación (Tesorería › Caja y bancos)."""
    from finanzas.models import Cuenta
    cuenta = Cuenta.objects.filter(es_detracciones=True, activo=True).exclude(numero='').first()
    return cuenta.numero.strip() if cuenta else ''


def codigo_detraccion(venta):
    """Código del catálogo 54 de SUNAT (el campo guarda el código interno de Nubefact)."""
    from .sunat import DETRACCION_TIPOS
    return dict(DETRACCION_TIPOS).get(venta.detraccion_codigo or '35', '037')[:3]


def validar(venta):
    """Lo que el envío directo aún no cubre (se envían por Nubefact o se registran sin esos conceptos)."""
    from .sunat import validar_comprobante
    errores = validar_comprobante(venta)
    if venta.tipo_operacion == 'GRATUITA' and venta.tipo_comprobante not in ('01', '03'):
        errores.append('Las operaciones gratuitas van en factura o boleta, no en notas.')
    if venta.detraccion_monto:
        if venta.tipo_comprobante != '01':
            errores.append('La detracción solo va en facturas (no en boletas ni notas).')
        if not cuenta_detracciones():
            errores.append('Registre la cuenta de detracciones del Banco de la Nación en Tesorería › Caja y bancos (marque '
                           '«Cuenta de detracciones» y su número): va en el XML de la factura.')
    if venta.retencion_monto and venta.tipo_comprobante != '01':
        errores.append('La retención del IGV solo va en facturas.')
    if venta.icbper:
        errores.append('El impuesto a las bolsas plásticas (ICBPER) aún no se envía directo a SUNAT.')
    if venta.percepcion_monto:
        errores.append('Las ventas con percepción aún no se envían directo a SUNAT.')
    return errores


def xml_comprobante(venta):
    """Árbol lxml (sin firmar) del comprobante en UBL 2.1."""
    empresa = Empresa.actual()
    tipo = venta.tipo_comprobante
    nombre = RAIZ[tipo]
    moneda = venta.moneda
    raiz = _raiz(nombre)
    _e(raiz, 'cbc:UBLVersionID', '2.1')
    _e(raiz, 'cbc:CustomizationID', '2.0')
    numero = f'{venta.serie}-{int(venta.numero)}'
    _e(raiz, 'cbc:ID', numero)
    _e(raiz, 'cbc:IssueDate', venta.fecha_emision.isoformat())
    creado = timezone.localtime(venta.creado) if venta.creado else timezone.localtime()
    _e(raiz, 'cbc:IssueTime', creado.strftime('%H:%M:%S'))
    detraccion = tipo == '01' and venta.detraccion_monto > 0
    gratuita = venta.tipo_operacion == 'GRATUITA'
    if nombre == 'Invoice':
        if venta.fecha_vencimiento and venta.fecha_vencimiento > venta.fecha_emision:
            _e(raiz, 'cbc:DueDate', venta.fecha_vencimiento.isoformat())
        # catálogo 51: 0101 venta interna, 0200 exportación, 1001 operación sujeta a detracción
        operacion = '0200' if venta.tipo_operacion == 'EXPORTACION' else ('1001' if detraccion else '0101')
        _e(raiz, 'cbc:InvoiceTypeCode', tipo, listID=operacion,
           listAgencyName='PE:SUNAT', listName='Tipo de Documento',
           listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo01', name='Tipo de Operacion',
           listSchemeURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo51')
    _e(raiz, 'cbc:Note', monto_en_letras(venta.total, moneda), languageLocaleID='1000')
    if gratuita:  # leyendas del catálogo 52
        _e(raiz, 'cbc:Note', 'TRANSFERENCIA GRATUITA DE UN BIEN Y/O SERVICIO PRESTADO GRATUITAMENTE',
           languageLocaleID='1002')
    if detraccion:
        _e(raiz, 'cbc:Note', 'Operación sujeta al Sistema de Pago de Obligaciones Tributarias',
           languageLocaleID='2006')
    _e(raiz, 'cbc:DocumentCurrencyCode', moneda, listID='ISO 4217 Alpha', listName='Currency',
       listAgencyName='United Nations Economic Commission for Europe')
    lineas = list(venta.items.select_related('producto'))
    if nombre == 'Invoice':
        _e(raiz, 'cbc:LineCountNumeric', len(lineas))
    else:
        ref = venta.doc_referencia
        discrepancia = _e(raiz, 'cac:DiscrepancyResponse')
        _e(discrepancia, 'cbc:ReferenceID', f'{ref.serie}-{int(ref.numero)}')
        _e(discrepancia, 'cbc:ResponseCode', venta.motivo_nota)
        _e(discrepancia, 'cbc:Description', (venta.get_motivo_nota_display().split(' ', 1)[-1] or 'AJUSTE')[:250])
        doc_ref = _e(_e(raiz, 'cac:BillingReference'), 'cac:InvoiceDocumentReference')
        _e(doc_ref, 'cbc:ID', f'{ref.serie}-{int(ref.numero)}')
        _e(doc_ref, 'cbc:DocumentTypeCode', ref.tipo_comprobante)
    _firma_cac(raiz, empresa)
    _emisor(raiz, empresa)
    _cliente(raiz, venta.tercero)
    pen = moneda == 'PEN'
    if detraccion:  # cuenta del Banco de la Nación, código del bien o servicio, porcentaje y monto en soles
        medio = _e(raiz, 'cac:PaymentMeans')
        _e(medio, 'cbc:ID', 'Detraccion')
        _e(medio, 'cbc:PaymentMeansCode', '001', listName='Medio de pago', listAgencyName='PE:SUNAT',
           listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo59')
        _e(_e(medio, 'cac:PayeeFinancialAccount'), 'cbc:ID', cuenta_detracciones())
        t = _e(raiz, 'cac:PaymentTerms')
        _e(t, 'cbc:ID', 'Detraccion')
        _e(t, 'cbc:PaymentMeansID', codigo_detraccion(venta), schemeName='Codigo de detraccion',
           schemeAgencyName='PE:SUNAT', schemeURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo54')
        _e(t, 'cbc:PaymentPercent', _m(venta.detraccion_pct))
        _e(t, 'cbc:Amount', _m(venta.detraccion_monto if pen else venta.detr_pen), currencyID='PEN')
    if tipo == '01':  # forma de pago (obligatoria en facturas)
        if venta.forma_pago == 'CREDITO':
            neto = venta.total - venta.detraccion_monto - venta.retencion_monto
            cuotas = list(venta.cronograma_cuotas) or [(1, venta.fecha_vencimiento or venta.fecha_emision, neto)]
            pendiente = sum((Decimal(c[2]) for c in cuotas), Decimal('0'))
            t = _e(raiz, 'cac:PaymentTerms')
            _e(t, 'cbc:ID', 'FormaPago')
            _e(t, 'cbc:PaymentMeansID', 'Credito')
            _e(t, 'cbc:Amount', _m(pendiente), currencyID=moneda)
            for n, fecha, importe in cuotas:
                t = _e(raiz, 'cac:PaymentTerms')
                _e(t, 'cbc:ID', 'FormaPago')
                _e(t, 'cbc:PaymentMeansID', f'Cuota{int(n):03d}')
                _e(t, 'cbc:Amount', _m(importe), currencyID=moneda)
                _e(t, 'cbc:PaymentDueDate', fecha.isoformat())
        else:
            t = _e(raiz, 'cac:PaymentTerms')
            _e(t, 'cbc:ID', 'FormaPago')
            _e(t, 'cbc:PaymentMeansID', 'Contado')
    if tipo == '01' and venta.retencion_monto > 0:  # retención del IGV (catálogo 53, código 62), en soles
        ac = _e(raiz, 'cac:AllowanceCharge')
        _e(ac, 'cbc:ChargeIndicator', 'false')
        _e(ac, 'cbc:AllowanceChargeReasonCode', '62', listAgencyName='PE:SUNAT', listName='Cargo/descuento',
           listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo53')
        _e(ac, 'cbc:MultiplierFactorNumeric', f'{venta.retencion_pct / 100:.5f}')
        _e(ac, 'cbc:Amount', _m(venta.retencion_monto if pen else venta.ret_pen), currencyID='PEN')
        _e(ac, 'cbc:BaseAmount', _m(venta.total if pen else venta.total_pen), currencyID='PEN')
    # tributos del documento por afectación
    tasa = empresa.igv_tasa
    lineas_gratuitas = sum((i.subtotal for i in lineas), Decimal('0')) if gratuita else Decimal('0')
    bases = {'GRAVADA': venta.base_imponible, 'EXONERADA': venta.exonerado, 'INAFECTA': venta.inafecto,
             'EXPORTACION': venta.exportacion, 'GRATUITA': lineas_gratuitas}
    tt = _e(raiz, 'cac:TaxTotal')
    _e(tt, 'cbc:TaxAmount', _m(venta.igv), currencyID=moneda)
    usados = [a for a, b in bases.items() if b] or ['GRAVADA']
    for a in usados:
        _subtotal_tributo(tt, moneda, bases[a], venta.igv if a == 'GRAVADA' else 0, a)
    valor_venta = sum((b for a, b in bases.items() if a != 'GRATUITA'), Decimal('0'))
    total_tag = 'cac:RequestedMonetaryTotal' if nombre == 'DebitNote' else 'cac:LegalMonetaryTotal'
    tot = _e(raiz, total_tag)
    _e(tot, 'cbc:LineExtensionAmount', _m(valor_venta), currencyID=moneda)
    _e(tot, 'cbc:TaxInclusiveAmount', _m(venta.total), currencyID=moneda)
    _e(tot, 'cbc:PayableAmount', _m(venta.total), currencyID=moneda)
    etiqueta_linea, etiqueta_cant = {'Invoice': ('cac:InvoiceLine', 'cbc:InvoicedQuantity'),
                                     'CreditNote': ('cac:CreditNoteLine', 'cbc:CreditedQuantity'),
                                     'DebitNote': ('cac:DebitNoteLine', 'cbc:DebitedQuantity')}[nombre]
    for n, i in enumerate(lineas, 1):
        afectacion = i.afectacion_en(venta)
        porcentaje = tasa if afectacion == 'GRAVADA' else Decimal('0')
        igv = r2(i.subtotal * porcentaje / 100)
        linea = _e(raiz, etiqueta_linea)
        _e(linea, 'cbc:ID', n)
        unidad = (i.producto.unidad if i.producto else '') or 'ZZ'
        _e(linea, etiqueta_cant, f'{i.cantidad:.2f}', unitCode=unidad, unitCodeListID='UN/ECE rec 20',
           unitCodeListAgencyName='United Nations Economic Commission for Europe')
        _e(linea, 'cbc:LineExtensionAmount', _m(i.subtotal), currencyID=moneda)
        precio = _e(_e(linea, 'cac:PricingReference'), 'cac:AlternativeConditionPrice')
        con_igv = (i.subtotal + igv) / i.cantidad if i.cantidad else Decimal('0')
        _e(precio, 'cbc:PriceAmount', f'{con_igv:.4f}', currencyID=moneda)
        # catálogo 16: 01 precio unitario con IGV, 02 valor referencial de una operación gratuita
        _e(precio, 'cbc:PriceTypeCode', '02' if gratuita else '01', listName='Tipo de Precio',
           listAgencyName='PE:SUNAT', listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo16')
        if i.descuento > 0 and not gratuita:
            ac = _e(linea, 'cac:AllowanceCharge')
            _e(ac, 'cbc:ChargeIndicator', 'false')
            _e(ac, 'cbc:AllowanceChargeReasonCode', '00')
            _e(ac, 'cbc:MultiplierFactorNumeric', f'{(i.descuento_pct or 0) / 100:.5f}')
            _e(ac, 'cbc:Amount', _m(i.descuento), currencyID=moneda)
            _e(ac, 'cbc:BaseAmount', _m(i.bruto), currencyID=moneda)
        tl = _e(linea, 'cac:TaxTotal')
        _e(tl, 'cbc:TaxAmount', _m(igv), currencyID=moneda)
        _subtotal_tributo(tl, moneda, i.subtotal, igv, afectacion, porcentaje, linea=True)
        item = _e(linea, 'cac:Item')
        _e(item, 'cbc:Description', (i.descripcion or '-')[:500])
        if i.producto and i.producto.codigo:
            _e(_e(item, 'cac:SellersItemIdentification'), 'cbc:ID', i.producto.codigo[:30])
        _e(_e(linea, 'cac:Price'), 'cbc:PriceAmount', '0.0000' if gratuita else f'{i.precio_unitario:.4f}',
           currencyID=moneda)
    return raiz


# ---------------------------------------------------------------- firma, ZIP y SOAP
def firmar(raiz, cfg=None):
    """(xml firmado en bytes, digest base64 = código hash del comprobante)."""
    from signxml import CanonicalizationMethod, DigestAlgorithm, SignatureMethod, XMLSigner, methods
    cfg = cfg or FacturacionConfig.actual()
    llave, cert = _pem(cfg)
    firmador = XMLSigner(method=methods.enveloped, signature_algorithm=SignatureMethod.RSA_SHA256,
                         digest_algorithm=DigestAlgorithm.SHA256,
                         c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0)
    firmado = firmador.sign(raiz, key=llave, cert=cert)
    ruc = Empresa.actual().ruc
    for s in firmado.iter(_q('ds', 'Signature')):
        s.set('Id', f'SIGN{ruc}')
    digest = firmado.find('.//' + _q('ds', 'DigestValue'))
    return (etree.tostring(firmado, xml_declaration=True, encoding='UTF-8', standalone=False),
            digest.text if digest is not None else '')


def _zip(nombre, contenido):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'{nombre}.xml', contenido)
    return buf.getvalue()


def _credenciales(cfg):
    ruc = Empresa.actual().ruc
    if cfg.entorno == 'BETA':
        return f'{ruc}MODDATOS', CLAVE_BETA
    if not (cfg.sol_usuario and cfg.sol_clave):
        raise ErrorSunatDirecto('Registre el usuario SOL secundario y su clave en Ajustes › Facturación '
                                'electrónica.')
    return f'{ruc}{cfg.sol_usuario.strip().upper()}', descifrar(cfg.sol_clave)


def _soap(operacion, cuerpo, url=None, cfg=None):
    cfg = cfg or FacturacionConfig.actual()
    usuario, clave = _credenciales(cfg)
    sobre = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ser="http://service.sunat.gob.pe" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        '<soapenv:Header><wsse:Security><wsse:UsernameToken>'
        f'<wsse:Username>{usuario}</wsse:Username><wsse:Password>{_escapar(clave)}</wsse:Password>'
        '</wsse:UsernameToken></wsse:Security></soapenv:Header>'
        f'<soapenv:Body><ser:{operacion}>{cuerpo}</ser:{operacion}></soapenv:Body></soapenv:Envelope>')
    req = urllib.request.Request(url or SERVICIO[cfg.entorno], data=sobre.encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'text/xml; charset=utf-8',
                                          'SOAPAction': f'"urn:{operacion}"'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            texto = resp.read()
    except urllib.error.HTTPError as exc:
        texto = exc.read()  # los rechazos llegan como SOAP Fault con HTTP 500
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErrorSunatDirecto(f'No se pudo conectar con SUNAT: {exc}') from exc
    return _respuesta(texto)


def _escapar(texto):
    return (texto or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _local(arbol, nombre):
    encontrados = arbol.xpath(f'//*[local-name()="{nombre}"]')
    return encontrados[0].text if encontrados else None


def _respuesta(texto):
    try:
        arbol = etree.fromstring(texto)
    except etree.XMLSyntaxError as exc:
        raise ErrorSunatDirecto(f'SUNAT respondió algo no reconocible: {texto[:200]!r}') from exc
    falla = _local(arbol, 'faultstring')
    if falla is not None:
        codigo = (_local(arbol, 'faultcode') or '').split('.')[-1]
        mensaje = _local(arbol, 'message') or falla
        raise ErrorSunatDirecto(f'SUNAT rechazó el envío ({codigo}): {mensaje}', codigo)
    return arbol


def leer_cdr(zip_b64):
    """{'codigo', 'descripcion', 'notas', 'xml'} del CDR (ApplicationResponse)."""
    datos = base64.b64decode(zip_b64)
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        nombre = next(n for n in z.namelist() if n.lower().endswith('.xml'))
        xml = z.read(nombre)
    arbol = etree.fromstring(xml)
    notas = [n.text for n in arbol.xpath('//*[local-name()="Note"]') if n.text]
    return {'codigo': _local(arbol, 'ResponseCode') or '', 'descripcion': _local(arbol, 'Description') or '',
            'notas': notas, 'xml': xml, 'zip': datos}


def nombre_archivo(tipo, serie, numero):
    return f'{Empresa.actual().ruc}-{tipo}-{serie}-{int(numero)}'


# ---------------------------------------------------------------- operaciones
def _enlace(sustento):
    """Descarga del sustento (el enlace usa el id del Sustento, no el del archivo)."""
    from django.urls import reverse
    return f"{reverse('sustento_ver', args=[sustento.pk])}?descargar=1"


def _guardar_cdr(venta, nombre, cdr_xml, descripcion='CDR de SUNAT (constancia de recepción)'):
    from .sustentos import guardar_archivo, vincular
    a = guardar_archivo(None, None, datos=cdr_xml, nombre=f'R-{nombre}.xml', tipo='text/xml')
    venta.enlace_cdr = _enlace(vincular(venta, a, None, descripcion))


def _guardar_archivos(venta, nombre, xml, cdr_xml=None):
    from .sustentos import guardar_archivo, vincular
    archivo = guardar_archivo(None, None, datos=xml, nombre=f'{nombre}.xml', tipo='text/xml')
    venta.enlace_xml = _enlace(vincular(venta, archivo, None, 'XML firmado enviado a SUNAT'))
    if cdr_xml:
        _guardar_cdr(venta, nombre, cdr_xml)


def cadena_qr(venta, hash_):
    tipo, numero = _doc_cliente(venta.tercero)
    return '|'.join([Empresa.actual().ruc, venta.tipo_comprobante, venta.serie, str(int(venta.numero)),
                     _m(venta.igv), _m(venta.total), venta.fecha_emision.isoformat(), tipo, numero, hash_])


def enviar(venta):
    """Arma, firma y envía el comprobante; guarda XML, CDR, hash, QR y el estado."""
    from .sunat import ErrorFacturacion, ErrorValidacion
    errores = validar(venta)
    if errores:
        raise ErrorValidacion(' '.join(errores))
    cfg = FacturacionConfig.actual()
    try:
        xml, hash_ = firmar(xml_comprobante(venta), cfg)
        nombre = nombre_archivo(venta.tipo_comprobante, venta.serie, venta.numero)
        arbol = _soap('sendBill', f'<fileName>{nombre}.zip</fileName>'
                                  f'<contentFile>{base64.b64encode(_zip(nombre, xml)).decode()}</contentFile>', cfg=cfg)
    except ErrorSunatDirecto as exc:
        codigo = exc.codigo or ''
        # 1033: ya fue informado anteriormente; los demás (0100-1999, 2000-3999) son rechazos o fallas
        if codigo.isdigit() and 2000 <= int(codigo) < 4000:
            venta.estado_sunat, venta.sunat_descripcion = 'RECHAZADO', str(exc)
            venta.fecha_envio = timezone.now()
            venta.save(update_fields=['estado_sunat', 'sunat_descripcion', 'fecha_envio'])
            return venta
        raise ErrorFacturacion(str(exc), codigo) from exc
    cdr = leer_cdr(_local(arbol, 'applicationResponse'))
    codigo = int(cdr['codigo'] or 0) if (cdr['codigo'] or '0').isdigit() else -1
    if codigo == 0 or codigo >= 4000:
        venta.estado_sunat = 'ACEPTADO'
    else:
        venta.estado_sunat = 'RECHAZADO'
    venta.sunat_descripcion = ' '.join([cdr['descripcion']] + [f'Observación: {n}' for n in cdr['notas']])[:2000]
    if cfg.entorno == 'BETA':
        venta.sunat_descripcion = f'[BETA, sin validez tributaria] {venta.sunat_descripcion}'
    venta.codigo_hash, venta.cadena_qr = hash_, cadena_qr(venta, hash_)
    venta.fecha_envio = timezone.now()
    _guardar_archivos(venta, nombre, xml, cdr['xml'])
    venta.save()
    return venta


def consultar(venta):
    """Producción: consulta el CDR en SUNAT (getStatusCdr). En beta SUNAT no tiene este servicio."""
    from .sunat import ErrorFacturacion
    cfg = FacturacionConfig.actual()
    pendiente = venta.envios_resumen.exclude(estado__in=['ACEPTADO', 'RECHAZADO']).first()
    if pendiente is not None:  # baja o resumen con ticket por confirmar
        consultar_ticket(pendiente)
        venta.sunat_descripcion = (f'{pendiente.get_tipo_display()} {pendiente.identificador} (ticket '
                                   f'{pendiente.ticket}): {pendiente.respuesta or pendiente.estado}')[:2000]
        venta.save(update_fields=['sunat_descripcion'])
        return venta
    if cfg.entorno == 'BETA':
        raise ErrorFacturacion('El entorno beta de SUNAT no permite consultar comprobantes: el estado es el que '
                               'devolvió SUNAT al enviarlo.')
    cuerpo = (f'<rucComprobante>{Empresa.actual().ruc}</rucComprobante>'
              f'<tipoComprobante>{venta.tipo_comprobante}</tipoComprobante><serieComprobante>{venta.serie}'
              f'</serieComprobante><numeroComprobante>{int(venta.numero)}</numeroComprobante>')
    try:
        arbol = _soap('getStatusCdr', cuerpo, url=CONSULTA_CDR, cfg=cfg)
    except ErrorSunatDirecto as exc:
        raise ErrorFacturacion(str(exc)) from exc
    contenido = _local(arbol, 'content')
    mensaje = _local(arbol, 'statusMessage') or ''
    if contenido:
        cdr = leer_cdr(contenido)
        venta.estado_sunat = 'ACEPTADO' if (cdr['codigo'] in ('0', '') or int(cdr['codigo']) >= 4000) \
            else 'RECHAZADO'
        venta.sunat_descripcion = cdr['descripcion']
        _guardar_cdr(venta, nombre_archivo(venta.tipo_comprobante, venta.serie, venta.numero), cdr['xml'],
                     'CDR de SUNAT (consulta)')
    else:
        venta.sunat_descripcion = mensaje or venta.sunat_descripcion
    venta.save(update_fields=['estado_sunat', 'sunat_descripcion', 'enlace_cdr'])
    return venta


def _correlativo_resumen(tipo, fecha):
    from .models import EnvioResumenSUNAT
    return EnvioResumenSUNAT.objects.filter(tipo=tipo, fecha=fecha).count() + 1


def xml_baja(venta, motivo, identificador, hoy):
    empresa = Empresa.actual()
    raiz = _raiz('VoidedDocuments', ('cac', 'cbc', 'ext', 'ds', 'sac'))
    _e(raiz, 'cbc:UBLVersionID', '2.0')
    _e(raiz, 'cbc:CustomizationID', '1.0')
    _e(raiz, 'cbc:ID', identificador)
    _e(raiz, 'cbc:ReferenceDate', venta.fecha_emision.isoformat())
    _e(raiz, 'cbc:IssueDate', hoy.isoformat())
    _firma_cac(raiz, empresa)
    p = _e(raiz, 'cac:AccountingSupplierParty')
    _e(p, 'cbc:CustomerAssignedAccountID', empresa.ruc)
    _e(p, 'cbc:AdditionalAccountID', '6')
    _e(_e(_e(p, 'cac:Party'), 'cac:PartyLegalEntity'), 'cbc:RegistrationName', empresa.razon_social[:250])
    linea = _e(raiz, 'sac:VoidedDocumentsLine')
    _e(linea, 'cbc:LineID', '1')
    _e(linea, 'cbc:DocumentTypeCode', venta.tipo_comprobante)
    _e(linea, 'sac:DocumentSerialID', venta.serie)
    _e(linea, 'sac:DocumentNumberID', int(venta.numero))
    _e(linea, 'sac:VoidReasonDescription', motivo[:100])
    return raiz


def xml_resumen_anulacion(venta, identificador, hoy):
    """Resumen diario con la boleta (o su nota) en estado 3 = anulado."""
    empresa = Empresa.actual()
    moneda = venta.moneda
    raiz = _raiz('SummaryDocuments', ('cac', 'cbc', 'ext', 'ds', 'sac'))
    _e(raiz, 'cbc:UBLVersionID', '2.0')
    _e(raiz, 'cbc:CustomizationID', '1.1')
    _e(raiz, 'cbc:ID', identificador)
    _e(raiz, 'cbc:ReferenceDate', venta.fecha_emision.isoformat())
    _e(raiz, 'cbc:IssueDate', hoy.isoformat())
    _firma_cac(raiz, empresa)
    p = _e(raiz, 'cac:AccountingSupplierParty')
    _e(p, 'cbc:CustomerAssignedAccountID', empresa.ruc)
    _e(p, 'cbc:AdditionalAccountID', '6')
    _e(_e(_e(p, 'cac:Party'), 'cac:PartyLegalEntity'), 'cbc:RegistrationName', empresa.razon_social[:250])
    linea = _e(raiz, 'sac:SummaryDocumentsLine')
    _e(linea, 'cbc:LineID', '1')
    _e(linea, 'cbc:DocumentTypeCode', venta.tipo_comprobante)
    _e(linea, 'cbc:ID', f'{venta.serie}-{int(venta.numero)}')
    tipo, numero = _doc_cliente(venta.tercero)
    cli = _e(linea, 'cac:AccountingCustomerParty')
    _e(cli, 'cbc:CustomerAssignedAccountID', numero)
    _e(cli, 'cbc:AdditionalAccountID', tipo)
    if venta.doc_referencia_id:
        ref = _e(_e(linea, 'cac:BillingReference'), 'cac:InvoiceDocumentReference')
        _e(ref, 'cbc:ID', f'{venta.doc_referencia.serie}-{int(venta.doc_referencia.numero)}')
        _e(ref, 'cbc:DocumentTypeCode', venta.doc_referencia.tipo_comprobante)
    _e(_e(linea, 'cac:Status'), 'cbc:ConditionCode', '3')
    _e(linea, 'sac:TotalAmount', _m(venta.total), currencyID=moneda)
    for monto, codigo in ((venta.base_imponible, '01'), (venta.exonerado, '02'), (venta.inafecto, '03')):
        if monto:
            pago = _e(linea, 'sac:BillingPayment')
            _e(pago, 'cbc:PaidAmount', _m(monto), currencyID=moneda)
            _e(pago, 'cbc:InstructionID', codigo)
    tt = _e(linea, 'cac:TaxTotal')
    _e(tt, 'cbc:TaxAmount', _m(venta.igv), currencyID=moneda)
    sub = _e(tt, 'cac:TaxSubtotal')
    _e(sub, 'cbc:TaxableAmount', _m(venta.base_imponible), currencyID=moneda)
    _e(sub, 'cbc:TaxAmount', _m(venta.igv), currencyID=moneda)
    categoria = _e(sub, 'cac:TaxCategory')
    _e(categoria, 'cbc:Percent', _m(empresa.igv_tasa))
    esquema = _e(categoria, 'cac:TaxScheme')
    _e(esquema, 'cbc:ID', '1000')
    _e(esquema, 'cbc:Name', 'IGV')
    _e(esquema, 'cbc:TaxTypeCode', 'VAT')
    return raiz


def anular(venta, motivo):
    """Factura (y sus notas): comunicación de baja. Boleta (y sus notas): resumen diario con estado anulado."""
    from .models import EnvioResumenSUNAT
    from .sunat import ErrorFacturacion
    cfg = FacturacionConfig.actual()
    hoy = timezone.localdate()
    boleta = venta.serie.startswith('B')
    tipo = 'RC' if boleta else 'RA'
    identificador = f'{tipo}-{hoy:%Y%m%d}-{_correlativo_resumen(tipo, hoy)}'
    raiz = xml_resumen_anulacion(venta, identificador, hoy) if boleta else xml_baja(venta, motivo, identificador, hoy)
    try:
        xml, _ = firmar(raiz, cfg)
        nombre = f'{Empresa.actual().ruc}-{identificador}'
        arbol = _soap('sendSummary', f'<fileName>{nombre}.zip</fileName>'
                                     f'<contentFile>{base64.b64encode(_zip(nombre, xml)).decode()}</contentFile>',
                      cfg=cfg)
    except ErrorSunatDirecto as exc:
        raise ErrorFacturacion(str(exc), exc.codigo) from exc
    envio = EnvioResumenSUNAT.objects.create(tipo=tipo, identificador=identificador, fecha=hoy,
                                             ticket=_local(arbol, 'ticket') or '', venta=venta)
    consultar_ticket(envio)
    venta.estado_sunat = 'BAJA'
    venta.sunat_descripcion = (f'{envio.get_tipo_display()} {identificador} (ticket {envio.ticket}): '
                               f'{envio.respuesta or envio.estado}')[:2000]
    venta.save(update_fields=['estado_sunat', 'sunat_descripcion'])
    return venta


def consultar_ticket(envio, esperar=True):
    """getStatus: 0 = procesado (con CDR), 98 = en proceso, 99 = procesado con errores."""
    import time
    if not envio.ticket:
        return envio
    arbol = None
    for intento in range(2):  # el beta a veces responde 401 justo después del envío: un reintento corto
        try:
            arbol = _soap('getStatus', f'<ticket>{envio.ticket}</ticket>')
            break
        except ErrorSunatDirecto as exc:
            ultimo = exc
            if intento == 0 and esperar:
                time.sleep(2)
    if arbol is None:
        envio.estado = 'ENVIADO'
        envio.respuesta = f'SUNAT aún no responde el ticket ({ultimo}). Use «Consultar estado» más tarde.'[:2000]
        envio.save(update_fields=['estado', 'respuesta'])
        return envio
    codigo = _local(arbol, 'statusCode') or ''
    contenido = _local(arbol, 'content') or ''
    if codigo == '98':
        envio.estado, envio.respuesta = 'ENVIADO', 'SUNAT aún procesa el ticket.'
    elif codigo in ('0', '99'):
        try:
            cdr = leer_cdr(contenido)
            envio.respuesta = cdr['descripcion']
            rechazado = cdr['codigo'].isdigit() and 0 < int(cdr['codigo']) < 4000
        except (ValueError, zipfile.BadZipFile, StopIteration):
            envio.respuesta, rechazado = contenido[:500], codigo == '99'
        envio.estado = 'RECHAZADO' if codigo == '99' or rechazado else 'ACEPTADO'
    else:  # 0127 ticket inexistente, etc.
        envio.estado, envio.respuesta = 'ENVIADO', f'SUNAT: {contenido or "sin respuesta"} (código {codigo}).'
    envio.save(update_fields=['estado', 'respuesta'])
    return envio


def lista_produccion(cfg=None):
    """Lo que hace falta para emitir directo en producción: [(estado, texto)], estado = 'ok', 'falta' o 'opcional'."""
    from .forms import error_ruc
    from .models import Serie
    cfg = cfg or FacturacionConfig.actual()
    e = Empresa.actual()
    hoy = timezone.localdate()
    salida = []

    def item(ok, texto, opcional=False):
        salida.append(('ok' if ok else ('opcional' if opcional else 'falta'), texto))

    item(e.ruc and not error_ruc(e.ruc), f'RUC de la empresa válido ({e.ruc or "vacío"}) — Ajustes › Empresa.')
    item(bool((e.direccion or '').strip()) and len(e.ubigeo or '') == 6,
         'Dirección y ubigeo del domicilio fiscal — Ajustes › Empresa.')
    vigente = bool(cfg.certificado) and not cfg.certificado_prueba and bool(cfg.certificado_vence) \
        and cfg.certificado_vence > hoy
    texto = 'Certificado digital de la empresa (no el de prueba) y vigente'
    if vigente and (cfg.certificado_vence - hoy).days <= 30:
        texto += f' — vence el {cfg.certificado_vence:%d/%m/%Y}: renuévelo'
    item(vigente, texto + '.')
    item(bool(cfg.sol_usuario and cfg.sol_clave), 'Usuario SOL secundario y su clave (perfil de emisión '
                                                  'electrónica).')
    series = set(Serie.objects.filter(activo=True).values_list('tipo', 'serie'))
    for tipo, letra, nombre in (('01', 'F', 'facturas'), ('03', 'B', 'boletas')):
        item(any(t == tipo and s.startswith(letra) for t, s in series),
             f'Serie de {nombre} que empiece con {letra} — Ajustes › Correlativos.')
    item(any(t == '09' and s.startswith('T') for t, s in series),
         'Serie de guías remitente que empiece con T (solo si emite guías).', opcional=True)
    item(bool(cfg.gre_client_id and cfg.gre_client_secret),
         'Credenciales de la API de guías (client_id y client_secret, solo si emite guías).', opcional=True)
    item(bool(cuenta_detracciones()), 'Cuenta de detracciones del Banco de la Nación en Tesorería › Caja y bancos (solo si '
                                      'factura servicios con detracción).', opcional=True)
    item(bool(e.registro_mtc), 'Registro MTC de la empresa (solo si emite guías transportista).', opcional=True)
    return salida


def probar_conexion():
    """Firma un XML de prueba y consulta un ticket inexistente: valida certificado y credenciales."""
    cfg = FacturacionConfig.actual()
    try:
        _pem(cfg)
        _soap('getStatus', '<ticket>0</ticket>', cfg=cfg)
    except ErrorSunatDirecto as exc:
        texto = str(exc)
        if 'certificado' in texto.lower() or 'conectar' in texto.lower() or '0102' in texto or '0103' in texto \
                or '0104' in texto or 'usuario' in texto.lower():
            return False, texto
        return True, f'Conexión con SUNAT ({cfg.get_entorno_display()}) correcta. Respuesta de prueba: {texto}'
    return True, f'Conexión con SUNAT ({cfg.get_entorno_display()}) correcta.'
