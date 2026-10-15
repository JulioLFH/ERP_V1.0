"""Lectura y validación de comprobantes para la rendición de gastos.

- `leer_qr`: texto del QR SUNAT de un comprobante electrónico.
- `consultar_ruc`: razón social, estado y condición (clientes/proveedores ya registrados o la API de Decolecta).
- `validar_en_sunat`: consulta de validez del comprobante (API SUNAT de la empresa).
- `desde_xml`: datos del XML UBL de una factura reenviada por correo.
- `leer_con_ia`: datos de la foto de un ticket o boleta sin QR (Claude, visión). El rendidor confirma antes de enviar.
"""
import base64
import json
import logging
import os
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)
D0 = Decimal('0')
TIPOS_QR = {'01': '01', '03': '03', '02': '02', '12': '12', '07': '00', '08': '00'}


class ErrorLectura(Exception):
    pass


def _dec(valor):
    try:
        return Decimal(str(valor).replace(',', '').strip() or '0').quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return D0


def _fecha(valor):
    texto = str(valor or '').strip()[:10]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------- QR SUNAT
def leer_qr(texto):
    """RUC|TIPO|SERIE|NUMERO|IGV|TOTAL|FECHA|TIPO DOC ADQUIRENTE|NUM DOC ADQUIRENTE|... -> dict.
    Avisa si el comprobante no está a nombre del RUC de la empresa."""
    partes = [p.strip() for p in str(texto or '').replace('\n', '').split('|')]
    if len(partes) < 7 or not (partes[0].isdigit() and len(partes[0]) == 11):
        raise ErrorLectura('El código QR no es de un comprobante electrónico SUNAT.')
    from core.models import Empresa
    tipo = TIPOS_QR.get(partes[1].zfill(2), '00')
    total, igv = _dec(partes[5]), _dec(partes[4])
    numero = partes[3].lstrip('0') or '0'
    doc_cliente = partes[8] if len(partes) > 8 else ''
    empresa = Empresa.actual()
    datos = {'ruc': partes[0], 'tipo_comprobante': tipo, 'serie': partes[2].upper(), 'numero': numero,
             'igv': igv, 'total': total, 'base': total - igv if tipo == '01' else total,
             'fecha': _fecha(partes[6]), 'doc_cliente': doc_cliente, 'aviso': ''}
    datos['a_nombre_empresa'] = bool(empresa.ruc and doc_cliente == empresa.ruc)
    if tipo == '01' and not datos['a_nombre_empresa']:
        datos['aviso'] = (f'La factura está emitida a {doc_cliente or "otro documento"}, no al RUC de la empresa '
                          f'({empresa.ruc}): no da crédito fiscal.')
    return datos


# ---------------------------------------------------------------- RUC
RUC_URL = 'https://api.decolecta.com/v1/sunat/ruc?numero={ruc}'


def consultar_ruc(ruc):
    """{'ruc', 'nombre', 'estado', 'condicion', 'alerta', 'fuente'} o ErrorLectura."""
    from core.forms import error_ruc
    from core.models import Empresa, Tercero
    ruc = (ruc or '').strip()
    if error_ruc(ruc):
        raise ErrorLectura(error_ruc(ruc))
    clave = f'ruc-{ruc}'
    datos = cache.get(clave)
    if datos is None:
        token = Empresa.objects.values_list('token_tipo_cambio', flat=True).first()
        if token:
            try:
                req = urllib.request.Request(RUC_URL.format(ruc=ruc), headers={
                    'Authorization': f'Bearer {token}', 'User-Agent': 'Ceiba-ERP'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    r = json.loads(resp.read().decode('utf-8'))
                datos = {'ruc': ruc, 'nombre': r.get('razon_social') or r.get('razonSocial') or r.get('nombre') or '',
                         'estado': (r.get('estado') or '').upper(), 'condicion': (r.get('condicion') or '').upper(),
                         'fuente': 'SUNAT (Decolecta)'}
                cache.set(clave, datos, 86400)
            except Exception as exc:  # sin servicio: se usa lo que hay en el sistema
                log.info('Consulta RUC %s no disponible: %s', ruc, exc)
        if datos is None:
            t = Tercero.objects.filter(numero_doc=ruc).first()
            if t is None:
                raise ErrorLectura('RUC no registrado en el sistema y la consulta a SUNAT no está disponible '
                                   '(configure el token de Decolecta en Ajustes › Empresa).')
            datos = {'ruc': ruc, 'nombre': t.nombre, 'estado': '', 'condicion': '', 'fuente': 'Clientes y proveedores'}
    alerta = ''
    if datos['estado'] and datos['estado'] != 'ACTIVO':
        alerta = f'RUC {datos["estado"]}'
    if datos['condicion'] and datos['condicion'] != 'HABIDO':
        alerta = (alerta + ' · ' if alerta else 'RUC ') + datos['condicion']
    return {**datos, 'alerta': alerta}


# ---------------------------------------------------------------- validez SUNAT
ELECTRONICOS = ('01', '03', '02')


def validar_en_sunat(linea, guardar=True):
    """Consulta el comprobante en SUNAT y guarda el resultado y la fecha en la línea. None si no aplica o no hay
    credenciales; ErrorLectura si SUNAT no responde."""
    from core import sunat_consulta
    if linea.tipo_comprobante not in ELECTRONICOS or linea.serie[:1] not in ('F', 'B', 'E') or not linea.numero:
        return None
    if not sunat_consulta.configurada():
        return None
    try:
        r = sunat_consulta.validar(linea.ruc, linea.tipo_comprobante if linea.tipo_comprobante != '02' else 'R1',
                                   linea.serie, linea.numero, linea.fecha, linea.total)
    except sunat_consulta.ErrorConsulta as exc:
        raise ErrorLectura(str(exc))
    linea.estado_sunat, linea.sunat_valido, linea.validado_en = r['detalle'][:200], r['valido'], timezone.now()
    if 'RUC' in r['detalle']:
        linea.ruc_estado = ' · '.join(p for p in r['detalle'].split(' · ')[1:3])[:80]
    if guardar and linea.pk:
        type(linea).objects.filter(pk=linea.pk).update(estado_sunat=linea.estado_sunat, sunat_valido=linea.sunat_valido,
                                                       validado_en=linea.validado_en, ruc_estado=linea.ruc_estado)
    return r


# ---------------------------------------------------------------- XML
def desde_xml(contenido, nombre='factura.xml'):
    from proveedores.xml_ubl import ErrorXML, leer
    from core.models import Empresa
    try:
        x = leer(contenido, nombre)
    except ErrorXML as exc:
        raise ErrorLectura(str(exc))
    empresa = Empresa.actual()
    tipo = x['tipo'] if x['tipo'] in ('01', '03') else '00'
    return {'ruc': x['ruc_emisor'], 'razon_social': x['nombre_emisor'], 'tipo_comprobante': tipo,
            'serie': x['serie'], 'numero': x['numero'], 'fecha': x['fecha'], 'moneda': x['moneda'],
            'base': x['valor_venta'] if tipo == '01' else x['total'], 'igv': x['igv'] if tipo == '01' else D0,
            'total': x['total'], 'a_nombre_empresa': not x['ruc_receptor'] or x['ruc_receptor'] == empresa.ruc,
            'glosa': (x['lineas'][0]['descripcion'] if x['lineas'] else 'Comprobante recibido por correo')[:200]}


# ---------------------------------------------------------------- IA (Claude, visión)
ESQUEMA = {
    'type': 'object',
    'properties': {
        'es_comprobante': {'type': 'boolean'},
        'ruc_emisor': {'type': 'string', 'description': 'RUC de 11 dígitos del emisor, o vacío'},
        'razon_social': {'type': 'string'},
        'tipo': {'type': 'string', 'enum': ['01', '03', '02', '12', '00'],
                 'description': '01 factura, 03 boleta, 02 recibo por honorarios, 12 ticket, 00 otro'},
        'serie': {'type': 'string'}, 'numero': {'type': 'string'},
        'fecha': {'type': 'string', 'description': 'AAAA-MM-DD'},
        'moneda': {'type': 'string', 'enum': ['PEN', 'USD']},
        'base': {'type': 'number'}, 'igv': {'type': 'number'}, 'total': {'type': 'number'},
        'ruc_cliente': {'type': 'string', 'description': 'RUC o DNI del cliente impreso, o vacío'},
        'qr': {'type': 'string', 'description': 'Texto del código QR si se puede leer, o vacío'},
    },
    'required': ['es_comprobante', 'ruc_emisor', 'razon_social', 'tipo', 'serie', 'numero', 'fecha', 'moneda',
                 'base', 'igv', 'total', 'ruc_cliente', 'qr'],
    'additionalProperties': False,
}
INSTRUCCION = ('Eres un asistente contable peruano. Lee el comprobante de pago de la imagen (ticket, boleta, factura '
               'o recibo) y devuelve sus datos. Si un dato no se ve con claridad, déjalo vacío o en 0: no lo inventes. '
               'La fecha en formato AAAA-MM-DD. Los importes en números, sin símbolo de moneda.')
TIPOS_IMAGEN = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}


def ia_configurada():
    from .models import ConfigRendicion
    cfg = ConfigRendicion.actual()
    return cfg.ia_activa and bool(cfg.ia_api_key or os.environ.get('ANTHROPIC_API_KEY'))


def _llamar_claude(datos, tipo_mime, cfg):
    """Una llamada a la API de Claude con la imagen (o PDF) y salida JSON con el esquema."""
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.ia_api_key or None, timeout=60.0)
    b64 = base64.standard_b64encode(datos).decode('ascii')
    bloque = ({'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': b64}}
              if tipo_mime == 'application/pdf' else
              {'type': 'image', 'source': {'type': 'base64', 'media_type': tipo_mime, 'data': b64}})
    respuesta = client.beta.messages.create(
        model=cfg.ia_modelo or 'claude-opus-5-5',
        max_tokens=4000,
        betas=['server-side-fallback-2026-07-01'],
        fallbacks='default',
        output_config={'effort': 'low', 'format': {'type': 'json_schema', 'schema': ESQUEMA}},
        messages=[{'role': 'user', 'content': [bloque, {'type': 'text', 'text': INSTRUCCION}]}],
    )
    if respuesta.stop_reason == 'refusal':
        raise ErrorLectura('La lectura automática no procesó esta imagen: ingrese los datos a mano.')
    texto = next((b.text for b in respuesta.content if b.type == 'text'), '')
    return json.loads(texto)


def leer_con_ia(datos, tipo_mime):
    """Datos sugeridos del comprobante (el rendidor los confirma). ErrorLectura si no se puede."""
    from .models import ConfigRendicion
    if not ia_configurada():
        raise ErrorLectura('La lectura con IA no está activada (Rendición de gastos › Configuración › Integraciones).')
    tipo_mime = (tipo_mime or '').split(';')[0].lower()
    if tipo_mime == 'image/jpg':
        tipo_mime = 'image/jpeg'
    if tipo_mime not in TIPOS_IMAGEN and tipo_mime != 'application/pdf':
        raise ErrorLectura('Envíe una foto (JPG, PNG o WEBP) o un PDF.')
    try:
        r = _llamar_claude(datos, tipo_mime, ConfigRendicion.actual())
    except ErrorLectura:
        raise
    except Exception as exc:  # red, clave inválida, límite de uso
        log.warning('Lectura con IA falló: %s', exc)
        raise ErrorLectura(f'No se pudo leer el comprobante con IA: {exc}')
    if not r.get('es_comprobante'):
        raise ErrorLectura('La imagen no parece un comprobante de pago.')
    if r.get('qr'):
        try:
            return {**leer_qr(r['qr']), 'razon_social': r.get('razon_social', ''), 'fuente': 'QR'}
        except ErrorLectura:
            pass
    from core.models import Empresa
    ruc_empresa = Empresa.actual().ruc
    tipo = r.get('tipo') or '00'
    total, igv = _dec(r.get('total')), _dec(r.get('igv'))
    return {'ruc': (r.get('ruc_emisor') or '').strip(), 'razon_social': (r.get('razon_social') or '')[:200],
            'tipo_comprobante': tipo, 'serie': (r.get('serie') or '').upper()[:4],
            'numero': (r.get('numero') or '').lstrip('0')[:10], 'fecha': _fecha(r.get('fecha')),
            'moneda': r.get('moneda') or 'PEN', 'total': total, 'igv': igv if tipo == '01' else D0,
            'base': (_dec(r.get('base')) or total - igv) if tipo == '01' else total,
            'a_nombre_empresa': (r.get('ruc_cliente') or '') == ruc_empresa, 'fuente': 'IA', 'aviso': ''}


def a_json(datos):
    """Para responder a la pantalla (fechas e importes como texto)."""
    return {k: (v.isoformat() if isinstance(v, date) else str(v) if isinstance(v, Decimal) else v)
            for k, v in datos.items()}
