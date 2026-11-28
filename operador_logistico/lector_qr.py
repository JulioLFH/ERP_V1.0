"""Qué trae el QR que escanea el operador.

- La hoja de despacho de un consolidado (la imprime Ceiba): «CEIBA-OL|CONSOLIDADO|CL-000001|TOKEN».
- La guía electrónica del cliente: PENDIENTE DE VALIDAR con guías reales (validaciones del requerimiento, bloque 2).
  Se cree que el QR de la GRE lleva a un PDF de SUNAT. Mientras no se pruebe con 5 a 10 guías reales, el lector
  reconoce estos formatos y nunca inventa datos:
    · texto con barras «RUC|09|SERIE|NÚMERO|…» (como el QR de los comprobantes): identifica la guía;
    · un enlace (http…): se guarda tal cual. Si es el enlace de SUNAT de la GRE (v1.117.17), se descarga la guía y se
      leen sus datos (obtener_datos_de_enlace); si no se puede, la guía queda con «datos pendientes» hasta que llegue
      su XML por correo;
    · «SERIE-NÚMERO» suelto (ej. T001-123 o EG01-45): identifica la guía si ya se cargó.
- La guía transportista (31) del servicio de transporte: mismo formato con tipo 31."""
import re
from urllib.parse import parse_qs, urlparse

PATRON_SERIE = re.compile(r'\b([A-Z0-9]{4})-0*(\d{1,8})\b')


class Lectura:
    """Resultado de interpretar el QR."""

    def __init__(self, texto, clase, ruc='', tipo='', serie='', numero='', url='', token=''):
        self.texto, self.clase = texto, clase
        self.ruc, self.tipo, self.serie, self.numero, self.url, self.token = ruc, tipo, serie, numero, url, token

    @property
    def identifica_guia(self):
        return bool(self.serie and self.numero)

    def __repr__(self):
        return f'<Lectura {self.clase} {self.ruc} {self.tipo} {self.serie}-{self.numero}>'


def interpretar(texto):
    texto = (texto or '').strip()
    if not texto:
        return Lectura('', 'VACIO')
    partes = [p.strip() for p in texto.split('|')]
    if len(partes) >= 4 and partes[0] == 'CEIBA-OL' and partes[1] == 'CONSOLIDADO':
        return Lectura(texto, 'CONSOLIDADO', serie='CL', numero=partes[2], token=partes[3] if len(partes) > 3 else '')
    if len(partes) >= 4 and re.fullmatch(r'\d{11}', partes[0]) and re.fullmatch(r'\d{2}', partes[1]) \
            and re.fullmatch(r'[A-Z0-9]{4}', partes[2].upper()) and partes[3].lstrip('0').isdigit():
        return Lectura(texto, 'GUIA', ruc=partes[0], tipo=partes[1], serie=partes[2].upper(),
                       numero=str(int(partes[3])))
    if texto.lower().startswith(('http://', 'https://')):
        lectura = Lectura(texto, 'ENLACE', url=texto)
        _datos_del_enlace(lectura)
        return lectura
    m = PATRON_SERIE.search(texto.upper())
    if m:
        ruc = re.search(r'\b(\d{11})\b', texto)
        return Lectura(texto, 'GUIA', ruc=ruc.group(1) if ruc else '', serie=m.group(1), numero=str(int(m.group(2))))
    return Lectura(texto, 'DESCONOCIDO')


def _datos_del_enlace(lectura):
    """Algunos enlaces llevan la serie y el número en los parámetros: se aprovechan sin descargar nada."""
    try:
        q = parse_qs(urlparse(lectura.url).query)
    except ValueError:
        return
    serie = (q.get('serie') or [''])[0].upper()
    numero = (q.get('numero') or q.get('correlativo') or [''])[0].lstrip('0')
    if re.fullmatch(r'[A-Z0-9]{4}', serie) and numero.isdigit():
        lectura.serie, lectura.numero = serie, numero
        lectura.ruc = ((q.get('ruc') or q.get('numRuc') or [''])[0])[:11]
        lectura.tipo = ((q.get('tipo') or q.get('codCpe') or ['09'])[0])[:2]


# ---------------------------------------------------------------- v1.117.17: datos de la guía desde el QR de SUNAT
# El QR de la representación impresa de la GRE lleva a SUNAT («…/gre/comprobantes/descargaqr?hashqr=…»), que devuelve
# la guía (PDF; si fuera el XML o un .zip con el XML, también se lee). Solo se descargan enlaces https de sunat.gob.pe
# (nunca otra dirección que venga en un QR), con tiempo y tamaño limitados. Lo que el PDF no trae claro queda
# pendiente: nunca se inventa. VALIDAR con 5 a 10 guías reales: el formato del PDF de SUNAT puede cambiar.
TIEMPO_SUNAT = 15
MAX_BYTES = 5 * 1024 * 1024
_CACHE = {}  # url -> datos (el celular verifica y luego confirma: una sola descarga)

MOTIVOS = [('VENTA SUJETA A CONFIRMACION', '14'), ('VENTA CON ENTREGA A TERCEROS', '03'), ('VENTA', '01'),
           ('COMPRA', '02'), ('TRASLADO ENTRE ESTABLECIMIENTOS', '04'), ('CONSIGNACION', '05'),
           ('DEVOLUCION', '06'), ('RECOJO DE BIENES TRANSFORMADOS', '07'), ('IMPORTACION', '08'),
           ('EXPORTACION', '09'), ('EMISOR ITINERANTE', '18'), ('ZONA PRIMARIA', '19'),
           ('TRASLADO DE BIENES PARA TRANSFORMACION', '17'), ('OTROS', '13')]


def es_enlace_sunat(url):
    try:
        u = urlparse(url or '')
    except ValueError:
        return False
    host = (u.hostname or '').lower()
    return u.scheme == 'https' and (host == 'sunat.gob.pe' or host.endswith('.sunat.gob.pe'))


def _descargar(url):
    """Bytes de lo que devuelve SUNAT (sin seguir redirecciones fuera de sunat.gob.pe) o None."""
    import urllib.request

    class SoloSunat(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, nueva):
            if not es_enlace_sunat(nueva):
                return None
            return super().redirect_request(req, fp, code, msg, headers, nueva)

    abridor = urllib.request.build_opener(SoloSunat)
    pedido = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Ceiba ERP)',
                                                  'Accept': 'application/pdf,application/xml,*/*'})
    with abridor.open(pedido, timeout=TIEMPO_SUNAT) as r:
        datos = r.read(MAX_BYTES + 1)
    return datos if len(datos) <= MAX_BYTES else None


def obtener_datos_de_enlace(url):
    """dict con los datos de la guía (mismas claves que el XML, más 'completos') desde el enlace de SUNAT del QR, o
    None si no es de SUNAT, no se pudo descargar o no se reconoce."""
    if not es_enlace_sunat(url):
        return None
    if url in _CACHE:
        return _CACHE[url]
    import logging
    try:
        contenido = _descargar(url)
    except Exception as exc:  # sin señal, SUNAT caído, enlace vencido: la guía espera su XML
        logging.getLogger(__name__).warning('QR de SUNAT: no se pudo descargar la guía (%s)', exc)
        return None
    datos = leer_documento(contenido) if contenido else None
    if datos is not None:
        _CACHE[url] = datos
        if len(_CACHE) > 200:
            _CACHE.pop(next(iter(_CACHE)))
    return datos


def leer_documento(contenido):
    """La guía que devolvió SUNAT: PDF, XML o .zip con el XML."""
    import io
    import zipfile
    from . import xml_guias
    if contenido[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                xml = next((n for n in z.namelist() if n.lower().endswith('.xml')), None)
                contenido = z.read(xml) if xml else b''
        except zipfile.BadZipFile:
            return None
    if contenido.lstrip()[:1] == b'<':
        try:
            datos = xml_guias.leer(contenido)
        except xml_guias.ErrorXML:
            return None
        datos['completos'] = True
        return datos
    if contenido[:5] == b'%PDF-':
        return leer_pdf(contenido)
    return None


def _sin_tildes(texto):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')


def _texto_pdf(contenido):
    import io
    from pypdf import PdfReader
    try:
        lector = PdfReader(io.BytesIO(contenido))
        return '\n'.join((p.extract_text() or '') for p in lector.pages[:5])
    except Exception:
        return ''


def _fecha_pdf(texto):
    from datetime import date
    m = re.search(r'(\d{2})[/-](\d{2})[/-](\d{4})', texto or '')
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def ubigeo_de_texto(direccion):
    """Ubigeo INEI cuyo departamento, provincia y distrito aparecen en la dirección (el distrito más largo gana:
    «Veintiséis de Octubre» antes que «Piura»). '' si no se reconoce."""
    from core import ubigeo
    m = re.search(r'\b(\d{6})\b', direccion or '')
    if m and ubigeo.existe(m.group(1)):
        return m.group(1)
    texto = ' ' + re.sub(r'[^A-Z0-9]+', ' ', _sin_tildes(direccion or '').upper()) + ' '
    mejor, largo = '', 0
    for codigo, (dep, prov, dist) in ubigeo.tabla().items():
        nombres = [' ' + re.sub(r'[^A-Z0-9]+', ' ', _sin_tildes(x).upper()).strip() + ' ' for x in (dep, prov, dist)]
        if all(n in texto for n in nombres) and len(nombres[2]) > largo:
            mejor, largo = codigo, len(nombres[2])
    return mejor


def _tramo(texto, etiqueta, fin):
    """Lo que sigue a la etiqueta hasta la próxima etiqueta conocida."""
    m = re.search(etiqueta + r'\s*:?\s*(.+?)\s*(?=' + fin + r'|$)', texto, re.I | re.S)
    return re.sub(r'\s+', ' ', m.group(1)).strip(' :-') if m else ''


ETIQUETAS = (r'Punto\s+de\s+partida|Punto\s+de\s+llegada|Datos\s+del|Destinatario|Motivo|Modalidad|Peso\s+bruto|'
             r'N[uú]mero\s+de\s+bultos|Cantidad\s+de\s+bultos|Fecha\s+de|Bienes\s+por\s+transportar|Indicador|'
             r'Observaci[oó]n|Unidad\s+de\s+medida|Documentos?\s+relacionados?|Representaci[oó]n')


def leer_pdf(contenido):
    """Datos de la guía desde el texto del PDF de SUNAT. Lo que no se encuentra queda vacío."""
    texto = _texto_pdf(contenido)
    if not texto.strip():
        return None  # PDF de imagen: sin texto que leer
    plano = _sin_tildes(texto)
    ruc = re.search(r'R\.?U\.?C\.?\s*(?:N[°ºo.]*)?\s*:?\s*(\d{11})', plano, re.I)
    sn = re.search(r'\b([A-Z][A-Z0-9]{3})\s*[-–]\s*(?:N[°ºo.]*\s*)?0*(\d{1,8})\b', plano)
    if not (ruc and sn):
        return None
    motivo_txt = _sin_tildes(_tramo(plano, r'Motivo\s+(?:de(?:l)?\s+)?traslado', ETIQUETAS)).upper()
    motivo = next((c for nombre, c in MOTIVOS if nombre in motivo_txt), '13' if motivo_txt else '')
    dest = _tramo(plano, r'(?:Datos\s+del\s+)?Destinatario', ETIQUETAS)
    dest_doc = re.search(r'(RUC|DNI|CE|C\.E\.|PASAPORTE)?\s*(?:N[°ºo.]*)?\s*:?\s*(\d{8,11})', dest, re.I)
    dest_nombre = dest
    if dest_doc:
        dest_nombre = (dest[:dest_doc.start()] + ' ' + dest[dest_doc.end():])
    dest_nombre = re.sub(r'\b(RUC|DNI|Raz[oó]n\s+social|Apellidos\s+y\s+nombres|Denominaci[oó]n)\b\s*:?', '',
                         dest_nombre, flags=re.I)
    peso = re.search(r'Peso\s+bruto[^:\d]*(?:\((\w+)\))?\s*:?\s*([\d.,]+)\s*(KGM|KG|TNE)?', plano, re.I)
    bultos = re.search(r'(?:N[uú]mero|Cantidad)\s+de\s+bultos(?:\s+o\s+pallets)?\s*:?\s*(\d+)', plano, re.I)
    partida = _tramo(texto, r'Punto\s+de\s+partida', ETIQUETAS)
    llegada = _tramo(texto, r'Punto\s+de\s+llegada', ETIQUETAS)
    nombre_emisor = ''
    antes = plano[:ruc.start()].strip().splitlines()
    if antes:
        nombre_emisor = antes[-1].strip()[:200]
    from decimal import Decimal, InvalidOperation
    try:
        valor_peso = Decimal((peso.group(2) if peso else '0').replace(',', ''))
    except InvalidOperation:
        valor_peso = Decimal('0')
    tipo_doc = ''
    if dest_doc:
        tipo_doc = '6' if len(dest_doc.group(2)) == 11 else '1'
    datos = {
        'tipo': '09', 'serie': sn.group(1).upper(), 'numero': str(int(sn.group(2))),
        'emisor_ruc': ruc.group(1), 'emisor_nombre': nombre_emisor,
        'fecha_emision': _fecha_pdf(_tramo(plano, r'Fecha\s+(?:y\s+hora\s+)?de\s+emision', ETIQUETAS)),
        'fecha_traslado': _fecha_pdf(_tramo(plano, r'Fecha\s+(?:de\s+)?inicio\s+(?:de(?:l)?\s+)?traslado', ETIQUETAS)),
        'motivo_traslado': motivo,
        'destinatario_tipo_doc': tipo_doc, 'destinatario_doc': dest_doc.group(2) if dest_doc else '',
        'destinatario_nombre': re.sub(r'\s+', ' ', dest_nombre).strip(' :-,')[:200],
        'partida_direccion': partida[:250], 'partida_ubigeo': ubigeo_de_texto(partida),
        'llegada_direccion': llegada[:250], 'llegada_ubigeo': ubigeo_de_texto(llegada),
        'peso': valor_peso, 'unidad_peso': {'KG': 'KGM'}.get(u := ((peso.group(1) or peso.group(3) or 'KGM') if peso
                                                              else 'KGM').upper()[:3], u),
        'bultos': int(bultos.group(1)) if bultos else 0, 'items': [],
    }
    datos['completos'] = bool(datos['llegada_direccion'] and datos['llegada_ubigeo'] and datos['partida_direccion']
                              and datos['peso'] > 0)
    return datos
