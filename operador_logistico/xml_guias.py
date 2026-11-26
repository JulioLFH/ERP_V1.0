"""Lectura del XML UBL 2.1 de una guía remitente (DespatchAdvice, tipo 09) que el cliente envía por correo: de ahí
salen remitente, destinatario, puntos de partida y llegada, bultos, peso e ítems sin digitar nada.

Se aceptan el formato GRE 2022 (Shipment/Delivery/DeliveryAddress y Delivery/Despatch/DespatchAddress) y el anterior
(Shipment/OriginAddress). El XML firmado de SUNAT trae la firma en UBLExtensions: no se valida aquí."""
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree


class ErrorXML(Exception):
    pass


def _parser():
    # sin entidades externas ni red (XXE)
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)


def _txt(nodo, ruta):
    """Texto del primer nodo que cumple la ruta (por nombre local, sin importar el espacio de nombres)."""
    if nodo is None:
        return ''
    pasos = '/'.join(f"*[local-name()='{p}']" for p in ruta.split('/'))
    r = nodo.xpath(f'./{pasos}')
    if not r:
        return ''
    valor = r[0] if isinstance(r[0], str) else r[0].text
    return (valor or '').strip()


def _nodo(nodo, ruta):
    if nodo is None:
        return None
    pasos = '/'.join(f"*[local-name()='{p}']" for p in ruta.split('/'))
    r = nodo.xpath(f'./{pasos}')
    return r[0] if r else None


def _decimal(texto, defecto=Decimal('0')):
    try:
        return Decimal(str(texto).strip())
    except (InvalidOperation, ValueError):
        return defecto


def _fecha(texto):
    try:
        return date.fromisoformat((texto or '')[:10])
    except ValueError:
        return None


def es_guia(contenido):
    """¿Los bytes son un XML de guía (DespatchAdvice)? Sin lanzar errores."""
    try:
        raiz = etree.fromstring(contenido, _parser())
    except (etree.XMLSyntaxError, ValueError):
        return False
    return etree.QName(raiz).localname == 'DespatchAdvice'


def leer(contenido):
    """dict con los datos de la guía a partir de los bytes del XML. ErrorXML si no es una guía remitente."""
    try:
        raiz = etree.fromstring(contenido, _parser())
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ErrorXML(f'El XML no se puede leer: {exc}') from exc
    if etree.QName(raiz).localname != 'DespatchAdvice':
        raise ErrorXML('El XML no es una guía de remisión (DespatchAdvice).')
    tipo = _txt(raiz, 'DespatchAdviceTypeCode') or '09'
    if tipo != '09':
        raise ErrorXML(f'El XML es una guía tipo {tipo}: se esperaba una guía remitente (09).')
    serie, _, numero = _txt(raiz, 'ID').upper().partition('-')
    if not serie or not numero.isdigit():
        raise ErrorXML('La guía no trae su serie y número.')
    emisor = _nodo(raiz, 'DespatchSupplierParty/Party')
    if emisor is None:
        emisor = _nodo(raiz, 'DespatchSupplierParty')
    ruc = _txt(emisor, 'PartyIdentification/ID') or _txt(raiz, 'DespatchSupplierParty/CustomerAssignedAccountID')
    nombre_emisor = _txt(emisor, 'PartyLegalEntity/RegistrationName')
    destino = _nodo(raiz, 'DeliveryCustomerParty/Party')
    dest_id = _nodo(destino, 'PartyIdentification/ID')
    envio = _nodo(raiz, 'Shipment')
    entrega = _nodo(envio, 'Delivery')
    llegada = _nodo(entrega, 'DeliveryAddress')
    partida = _nodo(entrega, 'Despatch/DespatchAddress')
    if partida is None:  # formato anterior a la GRE 2022
        partida = _nodo(envio, 'OriginAddress')
    peso = _nodo(envio, 'GrossWeightMeasure')
    items = []
    for linea in raiz.xpath("./*[local-name()='DespatchLine']"):
        cantidad = _nodo(linea, 'DeliveredQuantity')
        items.append({'descripcion': _txt(linea, 'Item/Description')[:250],
                      'codigo': _txt(linea, 'Item/SellersItemIdentification/ID')[:30],
                      'cantidad': str(_decimal(cantidad.text if cantidad is not None else '0')),
                      'unidad': (cantidad.get('unitCode') if cantidad is not None else '') or 'NIU'})
    bultos = _txt(envio, 'TotalTransportHandlingUnitQuantity')
    return {
        'tipo': '09', 'serie': serie[:4], 'numero': str(int(numero)),
        'fecha_emision': _fecha(_txt(raiz, 'IssueDate')),
        'fecha_traslado': _fecha(_txt(envio, 'ShipmentStage/TransitPeriod/StartDate')),
        'motivo_traslado': _txt(envio, 'HandlingCode')[:2],
        'emisor_ruc': ruc[:11], 'emisor_nombre': nombre_emisor[:200],
        'destinatario_tipo_doc': ((dest_id.get('schemeID') if dest_id is not None else '') or '')[:1],
        'destinatario_doc': (dest_id.text or '').strip()[:15] if dest_id is not None else '',
        'destinatario_nombre': _txt(destino, 'PartyLegalEntity/RegistrationName')[:200],
        'llegada_ubigeo': _txt(llegada, 'ID')[:6], 'llegada_direccion': _txt(llegada, 'AddressLine/Line')[:250],
        'partida_ubigeo': _txt(partida, 'ID')[:6], 'partida_direccion': _txt(partida, 'AddressLine/Line')[:250],
        'peso': _decimal(peso.text if peso is not None else '0'),
        'unidad_peso': ((peso.get('unitCode') if peso is not None else '') or 'KGM')[:3],
        'bultos': int(_decimal(bultos or '0')),
        'items': items,
    }
