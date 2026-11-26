"""Bloque 1: mensajes de los canales del CRM que son pedidos de recojo.

El remitente es la llave: un correo o un WhatsApp crea una Solicitud de recojo (y no una oportunidad) solo si su
dirección o su número coincide con un correo o celular autorizado del cliente (los celulares por sus últimos 9
dígitos) y el cliente tiene crédito (días o límite) o la casilla «Cliente de servicio logístico». Todo lo demás sigue
al CRM como hoy. No se exige ningún texto; las líneas «Almacén:», «Bultos:», «Contacto:» y «Fecha:» se leen si vienen.

Correo: los adjuntos .xml de guías remitente se cargan como guías de la solicitud, los .zip se descomprimen y los PDF
se guardan como sustento. WhatsApp: un documento o una imagen solo se registra como aviso (descargar la media de Meta
es otra etapa); las guías llegan por el QR en el recojo."""
import io
import logging
import re
import zipfile

from django.db import transaction

from . import servicios, xml_guias

log = logging.getLogger(__name__)
MAX_ADJUNTO = 5 * 1024 * 1024
MAX_EN_ZIP = 200  # archivos por .zip (y 20 MB descomprimidos en total)
MAX_ZIP_TOTAL = 20 * 1024 * 1024


def activo():
    """El módulo está activo en la empresa (giro «Operador logístico» y dentro del plan)."""
    from core.modulos import modulo_activo
    try:
        return modulo_activo('operador_logistico')
    except Exception:  # noqa: BLE001  (un canal nunca debe caerse por esto: sigue al CRM)
        log.exception('No se pudo saber si el operador logístico está activo')
        return False


# ---------------------------------------------------------------- correo
def _cabecera(mensaje, nombre):
    from email.header import decode_header, make_header
    valor = mensaje.get(nombre, '') or ''
    try:
        return str(make_header(decode_header(valor)))
    except Exception:  # noqa: BLE001  (cabeceras mal codificadas)
        return valor


def _cuerpo(mensaje):
    html = ''
    for parte in mensaje.walk():
        if parte.get_filename():
            continue
        tipo = parte.get_content_type()
        if tipo in ('text/plain', 'text/html'):
            carga = parte.get_payload(decode=True) or b''
            texto = carga.decode(parte.get_content_charset() or 'utf-8', errors='replace')
            if tipo == 'text/plain':
                return texto
            html = html or texto
    if html:  # solo HTML: sin etiquetas, con saltos de línea en los bloques
        html = re.sub(r'(?is)<(script|style).*?</\1>', '', html)
        html = re.sub(r'(?i)<br\s*/?>|</(p|div|tr|li)>', '\n', html)
        return re.sub(r'<[^>]+>', '', html)
    return ''


def _adjuntos(mensaje):
    """[(nombre, bytes)] de los adjuntos, con los .zip descomprimidos."""
    archivos = []
    for parte in mensaje.walk():
        nombre = parte.get_filename()
        if not nombre:
            continue
        nombre = _cabecera({'n': nombre}, 'n') if '=?' in nombre else nombre
        datos = parte.get_payload(decode=True) or b''
        if len(datos) > MAX_ADJUNTO * 4:
            continue
        if nombre.lower().endswith('.zip'):
            archivos += _del_zip(nombre, datos)
        else:
            archivos.append((nombre, datos))
    return archivos


def _del_zip(nombre, datos):
    archivos, total = [], 0
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            for info in z.infolist()[:MAX_EN_ZIP]:
                if info.is_dir() or info.file_size > MAX_ADJUNTO * 4:
                    continue
                total += info.file_size
                if total > MAX_ZIP_TOTAL:
                    break
                base = info.filename.replace('\\', '/').rsplit('/', 1)[-1]
                if base.lower().endswith(('.xml', '.pdf')):
                    archivos.append((base, z.read(info)))
    except (zipfile.BadZipFile, RuntimeError, ValueError):
        log.warning('Adjunto %s: no es un .zip válido', nombre)
    return archivos


def correo_a_solicitud(mensaje):
    """Correo del buzón comercial -> Solicitud de recojo si el remitente es un correo autorizado de un cliente con
    crédito o de servicio logístico. Devuelve la solicitud o None (el correo sigue al CRM)."""
    from email.utils import parseaddr
    if not activo():
        return None
    _, direccion = parseaddr(mensaje.get('From', ''))
    cliente = servicios.cliente_autorizado('CORREO', direccion)
    if cliente is None:
        return None
    asunto = _cabecera(mensaje, 'Subject')
    cuerpo = _cuerpo(mensaje).strip()
    with transaction.atomic():
        sol = servicios.crear_solicitud(cliente.tercero, 'CORREO', direccion.lower(), asunto,
                                        f'{asunto}\n{cuerpo}'.strip())
        avisos = []
        for nombre, datos in _adjuntos(mensaje):
            minus = nombre.lower()
            if minus.endswith('.xml') and xml_guias.es_guia(datos):
                try:
                    guia, nueva = servicios.registrar_guia_xml(datos, cliente.tercero, sol)
                    avisos.append(f'Guía {guia.numero_completo} cargada de {nombre}' + ('' if nueva else ' (ya estaba)'))
                except xml_guias.ErrorXML as exc:
                    avisos.append(f'{nombre}: {exc}')
            elif minus.endswith(('.pdf', '.xml')) and 0 < len(datos) <= MAX_ADJUNTO:
                from core.sustentos import guardar_archivo, vincular
                tipo = 'application/pdf' if minus.endswith('.pdf') else 'application/xml'
                vincular(sol, guardar_archivo(None, datos=datos, nombre=nombre, tipo=tipo), None,
                         f'Adjunto del correo: {nombre}')
                avisos.append(f'{nombre} guardado como sustento')
        if avisos:
            sol.avisos = '\n'.join(filter(None, [sol.avisos, *avisos]))[:5000]
            sol.save(update_fields=['avisos'])
    return sol


# ---------------------------------------------------------------- WhatsApp
MEDIOS = {'image': 'una imagen', 'document': 'un documento', 'audio': 'un audio', 'video': 'un video',
          'sticker': 'un sticker'}


def whatsapp_a_solicitud(mensaje):
    """Mensaje de WhatsApp (webhook de Meta) -> Solicitud de recojo si el número es un celular autorizado. Devuelve la
    solicitud o None (sigue a la bandeja del CRM)."""
    telefono = mensaje.get('from', '')
    if not telefono or not activo():
        return None
    cliente = servicios.cliente_autorizado('CELULAR', telefono)
    if cliente is None:
        return None
    tipo = mensaje.get('type')
    aviso = ''
    if tipo == 'text':
        texto = mensaje.get('text', {}).get('body', '')
    elif tipo in MEDIOS:
        media = mensaje.get(tipo, {})
        texto = media.get('caption', '')
        archivo = media.get('filename') or ''
        aviso = f'Envió {MEDIOS[tipo]}{f" ({archivo})" if archivo else ""} por WhatsApp: véalo en el teléfono de ' \
                f'la empresa'
    elif tipo == 'interactive':
        i = mensaje.get('interactive', {})
        texto = (i.get('button_reply') or i.get('list_reply') or {}).get('title', '')
    elif tipo == 'button':
        texto = mensaje.get('button', {}).get('text', '')
    else:
        texto = ''
    with transaction.atomic():
        sol = servicios.crear_solicitud(cliente.tercero, 'WHATSAPP', f'+{telefono.lstrip("+")}', '',
                                        texto or (f'({aviso})' if aviso else '(mensaje sin texto)'))
        if aviso:
            sol.avisos = '\n'.join(filter(None, [sol.avisos, aviso]))[:5000]
            sol.save(update_fields=['avisos'])
    return sol
