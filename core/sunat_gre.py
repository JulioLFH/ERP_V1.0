"""Guías de remisión electrónicas (GRE) directo con SUNAT, sin OSE.

El sistema arma el XML UBL 2.1 (DespatchAdvice, versión 2022) de la guía remitente (09, serie T) o transportista
(31, serie V), lo firma con el certificado digital de la empresa, lo comprime en ZIP y lo envía por la API REST de
SUNAT. SUNAT responde con un ticket; la constancia (CDR) se consulta con ese ticket y trae el enlace del QR.

    token:    POST https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/
              grant_type=password, scope=https://api-cpe.sunat.gob.pe, username = RUC + usuario SOL (dura 1 hora)
    envío:    POST https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/{RUC}-{tipo}-{serie}-{número}
              {"archivo": {"nomArchivo", "arcGreZip" (ZIP en base64), "hashZip" (SHA-256 del ZIP)}} -> numTicket
    consulta: GET  https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes/envios/{numTicket}
              codRespuesta 0 = aceptada (arcCdr), 98 = en proceso, 99 = rechazada (error o arcCdr)

SUNAT no tiene entorno de pruebas para guías: en beta el sistema genera y firma el XML (para revisarlo) sin enviarlo.
"""
import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.cache import cache
from django.utils import timezone
from lxml import etree

from .cifrado import descifrar
from .models import Empresa, FacturacionConfig
from .sunat_directo import (NS, ErrorSunatDirecto, _e, _firma_cac, _guardar_archivos, _guardar_cdr, _q, _zip,
                            firmar, leer_cdr, nombre_archivo)

TOKEN_URL = 'https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/'
SCOPE = 'https://api-cpe.sunat.gob.pe'
BASE = 'https://api-cpe.sunat.gob.pe/v1/contribuyente/gem/comprobantes'
DESPATCH = 'urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2'
# catálogo 61: documentos relacionados al transporte
DOCUMENTOS = {'01': 'Factura', '03': 'Boleta de Venta', '09': 'Guía de Remisión Remitente',
              '31': 'Guía de Remisión Transportista', '04': 'Liquidación de Compra', '12': 'Ticket'}
CATALOGO06 = {'schemeName': 'Documento de Identidad', 'schemeAgencyName': 'PE:SUNAT',
              'schemeURI': 'urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo06'}


# ---------------------------------------------------------------- XML DespatchAdvice
def _parte(padre, etiqueta, tipo_doc, numero, nombre, mtc=''):
    p = _e(_e(padre, etiqueta), 'cac:Party') if etiqueta else padre
    _e(_e(p, 'cac:PartyIdentification'), 'cbc:ID', numero, schemeID=tipo_doc or '0', **CATALOGO06)
    legal = _e(p, 'cac:PartyLegalEntity')
    _e(legal, 'cbc:RegistrationName', (nombre or '-')[:250])
    if mtc:
        _e(legal, 'cbc:CompanyID', mtc)
    return p


def _mtc(texto):
    import re
    return re.sub(r'[^A-Z0-9]', '', (texto or '').upper())


def _direccion(padre, etiqueta, ubigeo, direccion, ruc='', establecimiento=''):
    d = _e(padre, etiqueta)
    _e(d, 'cbc:ID', ubigeo, schemeAgencyName='PE:INEI', schemeName='Ubigeos')
    if establecimiento:  # traslado entre establecimientos: código del anexo registrado en SUNAT
        _e(d, 'cbc:AddressTypeCode', establecimiento, listID=ruc, listAgencyName='PE:SUNAT',
           listName='Establecimientos anexos')
    _e(_e(d, 'cac:AddressLine'), 'cbc:Line', (direccion or '-')[:200])
    return d


def documentos_relacionados(guia):
    """[(tipo, serie-número, RUC del emisor)] del comprobante o documento relacionado a la guía."""
    import re
    empresa = Empresa.actual()
    if guia.venta_id:
        v = guia.venta
        return [(v.tipo_comprobante, f'{v.serie}-{int(v.numero)}', empresa.ruc)]
    m = re.match(r'^\s*(\d{2})\s+([A-Z0-9]{4})-(\d+)\s*$', (guia.doc_relacionado or '').upper())
    if m and m.group(1) in DOCUMENTOS:
        emisor = guia.remitente.numero_doc if guia.tipo == '31' and guia.remitente else empresa.ruc
        return [(m.group(1), f'{m.group(2)}-{int(m.group(3))}', emisor)]
    return []


def xml_guia(guia):
    """Árbol lxml (sin firmar) de la guía en UBL 2.1."""
    empresa = Empresa.actual()
    remitente = guia.tipo == '09'
    privado = not remitente or guia.modalidad == '02'
    nsmap = {None: DESPATCH, **{p: NS[p] for p in ('cac', 'cbc', 'ext', 'ds')}}
    raiz = etree.Element(f'{{{DESPATCH}}}DespatchAdvice', nsmap=nsmap)
    contenido = _e(_e(_e(raiz, 'ext:UBLExtensions'), 'ext:UBLExtension'), 'ext:ExtensionContent')
    etree.SubElement(contenido, _q('ds', 'Signature'), Id='placeholder')
    _e(raiz, 'cbc:UBLVersionID', '2.1')
    _e(raiz, 'cbc:CustomizationID', '2.0')
    _e(raiz, 'cbc:ID', f'{guia.serie}-{int(guia.numero)}')
    _e(raiz, 'cbc:IssueDate', guia.fecha_emision.isoformat())
    creado = timezone.localtime(guia.creado) if guia.creado else timezone.localtime()
    _e(raiz, 'cbc:IssueTime', creado.strftime('%H:%M:%S'))
    _e(raiz, 'cbc:DespatchAdviceTypeCode', guia.tipo, listAgencyName='PE:SUNAT', listName='Tipo de Documento',
       listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo01')
    if (guia.observaciones or '').strip():
        _e(raiz, 'cbc:Note', guia.observaciones.strip()[:250])
    for tipo, numero, emisor in documentos_relacionados(guia):
        ref = _e(raiz, 'cac:AdditionalDocumentReference')
        _e(ref, 'cbc:ID', numero)
        _e(ref, 'cbc:DocumentTypeCode', tipo, listAgencyName='PE:SUNAT',
           listName='Documento relacionado al transporte',
           listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo61')
        _e(ref, 'cbc:DocumentType', DOCUMENTOS[tipo])
        _e(_e(_e(ref, 'cac:IssuerParty'), 'cac:PartyIdentification'), 'cbc:ID', emisor, schemeID='6',
           **CATALOGO06)
    _firma_cac(raiz, empresa)
    # emisor: el remitente en la guía 09; la empresa de transporte (con su registro MTC) en la 31
    _parte(raiz, 'cac:DespatchSupplierParty', '6', empresa.ruc, empresa.razon_social,
           '' if remitente else _mtc(empresa.registro_mtc))
    d = guia.destinatario
    _parte(raiz, 'cac:DeliveryCustomerParty', d.tipo_doc, d.numero_doc, d.nombre)
    envio = _e(raiz, 'cac:Shipment')
    _e(envio, 'cbc:ID', 'SUNAT_Envio')
    if remitente:
        _e(envio, 'cbc:HandlingCode', guia.motivo_traslado, listAgencyName='PE:SUNAT', listName='Motivo de traslado',
           listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo20')
        if guia.motivo_traslado == '13' and guia.descripcion_motivo:
            _e(envio, 'cbc:HandlingInstructions', guia.descripcion_motivo[:100])
    _e(envio, 'cbc:GrossWeightMeasure', f'{guia.peso_bruto:.3f}', unitCode=guia.unidad_peso)
    if remitente and guia.numero_bultos:
        _e(envio, 'cbc:TotalTransportHandlingUnitQuantity', guia.numero_bultos)
    etapa = _e(envio, 'cac:ShipmentStage')
    if remitente:
        _e(etapa, 'cbc:TransportModeCode', guia.modalidad, listName='Modalidad de traslado',
           listAgencyName='PE:SUNAT', listURI='urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo18')
    _e(_e(etapa, 'cac:TransitPeriod'), 'cbc:StartDate', guia.fecha_traslado.isoformat())
    if remitente and guia.modalidad == '01' and guia.transportista:
        t = guia.transportista
        _parte(_e(etapa, 'cac:CarrierParty'), None, '6', t.numero_doc, t.nombre, _mtc(t.registro_mtc))
    if privado and guia.conductor:
        c = guia.conductor
        chofer = _e(etapa, 'cac:DriverPerson')
        _e(chofer, 'cbc:ID', c.numero_doc, schemeID=c.tipo_doc, **CATALOGO06)
        _e(chofer, 'cbc:FirstName', c.nombres[:250])
        _e(chofer, 'cbc:FamilyName', c.apellidos[:250])
        _e(chofer, 'cbc:JobTitle', 'Principal')
        _e(_e(chofer, 'cac:IdentityDocumentReference'), 'cbc:ID', c.licencia.strip().upper())
    entre_locales = remitente and guia.motivo_traslado in ('04', '18')
    entrega = _e(envio, 'cac:Delivery')
    _direccion(entrega, 'cac:DeliveryAddress', guia.llegada_ubigeo, guia.llegada_direccion, empresa.ruc,
               (guia.almacen_destino.codigo_sunat if guia.almacen_destino else '0000') if entre_locales else '')
    despacho = _e(entrega, 'cac:Despatch')
    _direccion(despacho, 'cac:DespatchAddress', guia.partida_ubigeo, guia.partida_direccion, empresa.ruc,
               (guia.almacen_origen.codigo_sunat if guia.almacen_origen else '0000') if entre_locales else '')
    if not remitente and guia.remitente:  # guía transportista: quien entrega la mercadería
        r = guia.remitente
        _parte(_e(despacho, 'cac:DespatchParty'), None, r.tipo_doc, r.numero_doc, r.nombre)
    if privado and guia.vehiculo:
        from .sunat import _placa
        equipo = _e(_e(envio, 'cac:TransportHandlingUnit'), 'cac:TransportEquipment')
        _e(equipo, 'cbc:ID', _placa(guia.vehiculo.placa))
        if not remitente and guia.vehiculo.certificado:  # TUCE o certificado de habilitación vehicular
            _e(_e(equipo, 'cac:ApplicableTransportMeans'), 'cbc:RegistrationNationalityID',
               _placa(guia.vehiculo.certificado))
    for n, i in enumerate(guia.items.select_related('producto'), 1):
        linea = _e(raiz, 'cac:DespatchLine')
        _e(linea, 'cbc:ID', n)
        _e(linea, 'cbc:DeliveredQuantity', f'{i.cantidad:.2f}', unitCode=i.unidad or 'NIU')
        _e(_e(linea, 'cac:OrderLineReference'), 'cbc:LineID', n)
        item = _e(linea, 'cac:Item')
        _e(item, 'cbc:Description', (i.descripcion or '-')[:500])
        if i.producto and i.producto.codigo:
            _e(_e(item, 'cac:SellersItemIdentification'), 'cbc:ID', i.producto.codigo[:30])
    return raiz


def validar(guia):
    from .sunat import validar_guia
    errores = validar_guia(guia)
    if guia.tipo == '31' and not _mtc(Empresa.actual().registro_mtc):
        errores.append('La guía transportista requiere el registro MTC de la empresa (Ajustes › Empresa).')
    d = guia.destinatario
    if not (d.numero_doc or '').strip() or d.numero_doc in ('00000000', '-'):
        errores.append('El destinatario debe tener número de documento.')
    if guia.tipo == '09' and guia.modalidad == '01' and guia.transportista and \
            guia.transportista.tipo_doc != '6':
        errores.append('La empresa de transporte debe tener RUC.')
    return errores


# ---------------------------------------------------------------- API REST
def _http(metodo, url, cuerpo=None, token=None, formulario=False):
    """(código HTTP, JSON de respuesta)."""
    cabeceras = {'Accept': 'application/json'}
    datos = None
    if cuerpo is not None:
        if formulario:
            datos = urllib.parse.urlencode(cuerpo).encode()
            cabeceras['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            datos = json.dumps(cuerpo).encode()
            cabeceras['Content-Type'] = 'application/json'
    if token:
        cabeceras['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=datos, method=metodo, headers=cabeceras)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            estado, texto = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        estado, texto = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErrorSunatDirecto(f'No se pudo conectar con SUNAT: {exc}') from exc
    try:
        return estado, json.loads(texto.decode('utf-8') or '{}')
    except ValueError:
        return estado, {'msg': texto[:300].decode('utf-8', 'replace')}


def _mensaje(datos):
    """Texto legible de un error de la API de SUNAT."""
    errores = datos.get('errors') or []
    partes = [f"{e.get('cod', '')} {e.get('msg', '')}".strip() for e in errores if isinstance(e, dict)]
    if datos.get('error') and isinstance(datos['error'], dict):
        partes.append(f"{datos['error'].get('numError', '')} {datos['error'].get('desError', '')}".strip())
    for clave in ('msg', 'error_description', 'message'):
        if datos.get(clave) and isinstance(datos[clave], str):
            partes.append(datos[clave])
    if isinstance(datos.get('error'), str):
        partes.append(datos['error'])
    return '; '.join(p for p in partes if p) or 'sin detalle'


def _token(cfg):
    ruc = Empresa.actual().ruc
    if not (cfg.gre_client_id and cfg.gre_client_secret):
        raise ErrorSunatDirecto('Registre el client_id y el client_secret de la API de guías en Ajustes › '
                                'Facturación electrónica.')
    if not (cfg.sol_usuario and cfg.sol_clave):
        raise ErrorSunatDirecto('Registre el usuario SOL secundario y su clave en Ajustes › Facturación '
                                'electrónica.')
    clave_cache = f'gre-token:{ruc}:{cfg.gre_client_id}'
    token = cache.get(clave_cache)
    if token:
        return token
    estado, datos = _http('POST', TOKEN_URL.format(client_id=cfg.gre_client_id.strip()), {
        'grant_type': 'password', 'scope': SCOPE, 'client_id': cfg.gre_client_id.strip(),
        'client_secret': descifrar(cfg.gre_client_secret),
        'username': f'{ruc}{cfg.sol_usuario.strip().upper()}', 'password': descifrar(cfg.sol_clave)},
        formulario=True)
    if estado != 200 or not datos.get('access_token'):
        raise ErrorSunatDirecto(f'SUNAT no entregó el token de la API de guías (HTTP {estado}): {_mensaje(datos)}. '
                                'Revise el client_id/secret, el usuario SOL secundario y su clave.')
    cache.set(clave_cache, datos['access_token'], max(int(datos.get('expires_in') or 3600) - 120, 60))
    return datos['access_token']


def _qr(cdr_xml):
    """Enlace del QR que SUNAT incluye en la constancia de la guía."""
    arbol = etree.fromstring(cdr_xml)
    for nodo in arbol.xpath('//*[local-name()="DocumentDescription" or local-name()="URI"]'):
        if (nodo.text or '').strip().startswith('http'):
            return nodo.text.strip()
    return ''


def enviar(guia):
    """Arma, firma y envía la guía; consulta enseguida el ticket (SUNAT suele responder en segundos)."""
    from .sunat import ErrorFacturacion, ErrorValidacion
    errores = validar(guia)
    if errores:
        raise ErrorValidacion(' '.join(errores))
    cfg = FacturacionConfig.actual()
    nombre = nombre_archivo(guia.tipo, guia.serie, guia.numero)
    try:
        xml, hash_ = firmar(xml_guia(guia), cfg)
    except ErrorSunatDirecto as exc:
        raise ErrorValidacion(str(exc)) from exc
    guia.codigo_hash = hash_
    if cfg.entorno == 'BETA':
        _guardar_archivos(guia, nombre, xml)
        guia.estado_sunat = 'NO_ENVIADO'
        guia.sunat_descripcion = ('[BETA] SUNAT no tiene entorno de pruebas para guías de remisión: se generó y firmó '
                                  'el XML (adjunto, para revisarlo) pero no se envió. En producción se envía a SUNAT.')
        guia.save()
        return guia
    archivo = _zip(nombre, xml)
    try:
        token = _token(cfg)
        estado, datos = _http('POST', f'{BASE}/{nombre}', {'archivo': {
            'nomArchivo': f'{nombre}.zip', 'arcGreZip': base64.b64encode(archivo).decode(),
            'hashZip': hashlib.sha256(archivo).hexdigest()}}, token=token)
    except ErrorSunatDirecto as exc:
        raise ErrorFacturacion(str(exc)) from exc
    if estado == 401:
        cache.delete(f'gre-token:{Empresa.actual().ruc}:{cfg.gre_client_id}')
    if estado not in (200, 201) or not datos.get('numTicket'):
        raise ErrorFacturacion(f'SUNAT no recibió la guía (HTTP {estado}): {_mensaje(datos)}')
    guia.sunat_ticket = datos['numTicket']
    guia.estado_sunat = 'PENDIENTE'
    guia.sunat_descripcion = f"Recibida por SUNAT (ticket {guia.sunat_ticket}). Esperando la constancia."
    guia.fecha_envio = timezone.now()
    _guardar_archivos(guia, nombre, xml)
    guia.save()
    for espera in (1, 2, 3):
        time.sleep(espera)
        consultar(guia)
        if guia.estado_sunat != 'PENDIENTE':
            break
    return guia


def consultar(guia):
    from .sunat import ErrorFacturacion
    if not guia.sunat_ticket:
        raise ErrorFacturacion('La guía aún no tiene ticket de SUNAT: envíela primero.')
    cfg = FacturacionConfig.actual()
    try:
        estado, datos = _http('GET', f'{BASE}/envios/{guia.sunat_ticket}', token=_token(cfg))
    except ErrorSunatDirecto as exc:
        raise ErrorFacturacion(str(exc)) from exc
    if estado != 200:
        raise ErrorFacturacion(f'SUNAT no respondió el ticket {guia.sunat_ticket} (HTTP {estado}): '
                               f'{_mensaje(datos)}')
    codigo = str(datos.get('codRespuesta', ''))
    if codigo == '98':
        guia.estado_sunat = 'PENDIENTE'
        guia.sunat_descripcion = f'SUNAT aún procesa la guía (ticket {guia.sunat_ticket}). Consulte en unos minutos.'
    elif codigo in ('0', '99'):
        cdr = None
        if datos.get('arcCdr'):
            cdr = leer_cdr(datos['arcCdr'])
            _guardar_cdr(guia, nombre_archivo(guia.tipo, guia.serie, guia.numero), cdr['xml'])
            guia.cadena_qr = _qr(cdr['xml']) or guia.cadena_qr
        rechazo = codigo == '99' or (cdr and cdr['codigo'].isdigit() and 0 < int(cdr['codigo']) < 4000)
        guia.estado_sunat = 'RECHAZADO' if rechazo else 'ACEPTADO'
        detalle = [cdr['descripcion']] + [f'Observación: {n}' for n in cdr['notas']] if cdr else []
        if datos.get('error'):
            detalle.append(_mensaje({'error': datos['error']}))
        guia.sunat_descripcion = ' '.join(d for d in detalle if d)[:2000] or (
            'Aceptada por SUNAT.' if not rechazo else 'Rechazada por SUNAT.')
    else:
        guia.sunat_descripcion = f'SUNAT respondió el ticket con código {codigo or "vacío"}: {_mensaje(datos)}'
    guia.save(update_fields=['estado_sunat', 'sunat_descripcion', 'cadena_qr', 'enlace_cdr'])
    return guia


def probar_credenciales():
    """(ok, mensaje): pide un token de la API de guías."""
    cfg = FacturacionConfig.actual()
    cache.delete(f'gre-token:{Empresa.actual().ruc}:{cfg.gre_client_id}')
    try:
        _token(cfg)
    except ErrorSunatDirecto as exc:
        return False, str(exc)
    return True, 'API de guías de SUNAT: credenciales correctas (token recibido).'
