"""Lectura del XML de la factura electrónica (UBL 2.1, formato SUNAT).

Acepta el .xml o el .zip que entrega SUNAT/el OSE (toma el primer .xml que no sea la constancia CDR).
"""
import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

NS = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
      'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
MAX_BYTES = 3 * 1024 * 1024
# catálogo 07 SUNAT: 1x gravado, 2x exonerado, 3x inafecto, 40 exportación
AFECTACION = {'1': 'GRAVADA', '2': 'EXONERADA', '3': 'INAFECTA', '4': 'EXPORTACION'}


class ErrorXML(Exception):
    pass


def _contenido(datos, nombre):
    if len(datos) > MAX_BYTES:
        raise ErrorXML('El archivo XML es demasiado grande (máximo 3 MB).')
    if nombre.lower().endswith('.zip') or datos[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                xmls = [n for n in z.namelist() if n.lower().endswith('.xml') and not n.upper().startswith('R-')]
                if not xmls:
                    raise ErrorXML('El .zip no contiene el XML de la factura.')
                datos = z.read(xmls[0])
        except zipfile.BadZipFile as exc:
            raise ErrorXML('El archivo .zip está dañado.') from exc
    return datos


def _dec(nodo, ruta):
    el = nodo.find(ruta, NS)
    if el is None or not (el.text or '').strip():
        return None
    try:
        return Decimal(el.text.strip())
    except InvalidOperation as exc:
        raise ErrorXML(f'Importe inválido en el XML ({ruta}).') from exc


def _txt(nodo, ruta):
    el = nodo.find(ruta, NS)
    return (el.text or '').strip() if el is not None else ''


def _ruc(parte):
    """RUC de AccountingSupplierParty / AccountingCustomerParty (UBL 2.1 o el formato antiguo 2.0)."""
    if parte is None:
        return ''
    for ruta in ('cac:Party/cac:PartyIdentification/cbc:ID', 'cac:Party/cac:PartyLegalEntity/cac:CompanyLegalForm',
                 'cbc:CustomerAssignedAccountID'):
        valor = _txt(parte, ruta)
        if valor:
            return valor
    return ''


def leer(datos, nombre='factura.xml'):
    """dict con serie, numero, fecha, tipo, moneda, ruc_emisor, ruc_receptor, valor_venta, igv, total, lineas."""
    datos = _contenido(datos, nombre)
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError as exc:
        raise ErrorXML('El archivo no es un XML válido.') from exc
    etiqueta = raiz.tag.rsplit('}', 1)[-1]
    if etiqueta not in ('Invoice', 'CreditNote', 'DebitNote'):
        raise ErrorXML('El XML no es una factura ni una nota electrónica (UBL Invoice / CreditNote / DebitNote).')
    linea_tag, cantidad_tag = {'Invoice': ('cac:InvoiceLine', 'cbc:InvoicedQuantity'),
                               'CreditNote': ('cac:CreditNoteLine', 'cbc:CreditedQuantity'),
                               'DebitNote': ('cac:DebitNoteLine', 'cbc:DebitedQuantity')}[etiqueta]
    ident = _txt(raiz, 'cbc:ID')
    if '-' not in ident:
        raise ErrorXML('El XML no tiene la serie y número de la factura.')
    serie, numero = ident.split('-', 1)
    try:
        fecha = date.fromisoformat(_txt(raiz, 'cbc:IssueDate'))
    except ValueError as exc:
        raise ErrorXML('El XML no tiene una fecha de emisión válida.') from exc
    lineas = []
    for ln in raiz.findall(linea_tag, NS):
        cantidad = _dec(ln, cantidad_tag) or Decimal('0')
        valor = _dec(ln, 'cbc:LineExtensionAmount') or Decimal('0')
        precio = _dec(ln, 'cac:Price/cbc:PriceAmount')
        if precio is None and cantidad:
            precio = valor / cantidad
        lineas.append({'descripcion': _txt(ln, 'cac:Item/cbc:Description'),
                       'codigo': _txt(ln, 'cac:Item/cac:SellersItemIdentification/cbc:ID'),
                       'cantidad': cantidad, 'precio': (precio or Decimal('0')).quantize(Decimal('0.0001')),
                       'valor': valor, 'unidad': (ln.find(cantidad_tag, NS).get('unitCode', '')
                                                  if ln.find(cantidad_tag, NS) is not None else ''),
                       'afectacion': AFECTACION.get(_txt(
                           ln, 'cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReasonCode')[:1], '')})
    total = _dec(raiz, 'cac:LegalMonetaryTotal/cbc:PayableAmount') or _dec(
        raiz, 'cac:RequestedMonetaryTotal/cbc:PayableAmount')
    if total is None:
        raise ErrorXML('El XML no tiene el importe total (PayableAmount).')
    return {
        'serie': serie.strip().upper(), 'numero': str(int(numero)) if numero.strip().isdigit() else numero.strip(),
        'fecha': fecha, 'tipo': _txt(raiz, 'cbc:InvoiceTypeCode') or
        {'CreditNote': '07', 'DebitNote': '08'}.get(etiqueta, '01'),
        'nombre_emisor': _txt(raiz, 'cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName'),
        'vencimiento': _txt(raiz, 'cbc:DueDate'),
        'referencia': _txt(raiz, 'cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID'),
        'moneda': _txt(raiz, 'cbc:DocumentCurrencyCode') or 'PEN',
        'ruc_emisor': _ruc(raiz.find('cac:AccountingSupplierParty', NS)),
        'ruc_receptor': _ruc(raiz.find('cac:AccountingCustomerParty', NS)),
        'valor_venta': _dec(raiz, 'cac:LegalMonetaryTotal/cbc:LineExtensionAmount')
        or _dec(raiz, 'cac:RequestedMonetaryTotal/cbc:LineExtensionAmount')
        or sum((l['valor'] for l in lineas), Decimal('0')),
        'igv': _dec(raiz, 'cac:TaxTotal/cbc:TaxAmount') or Decimal('0'),
        'total': total, 'lineas': lineas, 'xml': datos.decode('utf-8', errors='replace'),
    }
