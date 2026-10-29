"""Lectura de los manuales (LUC-01): el texto del PDF o TXT adjunto, separado por secciones con su página, para que
Lucho responda citando «sección 4.1, pág. 3» y no solo el título.

Las secciones se reconocen por su numeración al inicio de la línea («4.1 Grumos: limpiar el filtro…», «5. Arranque»).
Un PDF escaneado (solo imagen) no trae texto: queda el aviso de que necesita OCR y Lucho sigue con los puntos clave."""
import re
from io import BytesIO

from django.utils import timezone

TITULO = re.compile(r'^\s*(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+(\S.{1,110})$')
MAXIMO_SECCION = 3000


class ErrorLectura(Exception):
    pass


def paginas_de(datos, nombre):
    """[(número de página, texto)] del archivo."""
    if nombre.lower().endswith('.txt'):
        for codificacion in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                return [(1, datos.decode(codificacion))]
            except UnicodeDecodeError:
                continue
    if not datos[:5] == b'%PDF-':
        raise ErrorLectura('No es un PDF ni un texto.')
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ErrorLectura('Falta la librería para leer PDF (pypdf) en el servidor.')
    try:
        lector = PdfReader(BytesIO(datos))
        return [(n, p.extract_text() or '') for n, p in enumerate(lector.pages, 1)]
    except Exception as exc:  # PDF dañado o protegido
        raise ErrorLectura(f'No se pudo leer el PDF: {exc}') from exc


def secciones_de(paginas):
    """[{titulo, pagina, texto}]: una por título numerado; sin títulos, una por página."""
    secciones, actual = [], None
    for pagina, texto in paginas:
        for linea in texto.splitlines():
            limpia = ' '.join(linea.split())
            if not limpia:
                continue
            m = TITULO.match(limpia)
            if m and not re.search(r'\d{3,}', m.group(2)[:6]):  # no confundir «1 500 rpm» con un título
                actual = {'titulo': limpia[:150], 'pagina': pagina, 'texto': ''}
                secciones.append(actual)
                continue
            if actual is None:
                actual = {'titulo': f'Página {pagina}', 'pagina': pagina, 'texto': ''}
                secciones.append(actual)
            if len(actual['texto']) < MAXIMO_SECCION:
                actual['texto'] = (actual['texto'] + ' ' + limpia).strip()
    return [s for s in secciones if s['texto'] or TITULO.match(s['titulo'])]


def leer_manual(manual):
    """Lee los PDF/TXT adjuntos al manual (el más reciente manda). Guarda las secciones y devuelve el aviso."""
    from core import sustentos
    adjuntos = [s for s in sustentos.de(manual).order_by('-creado')
                if s.archivo.nombre.lower().endswith(('.pdf', '.txt'))]
    if not adjuntos:
        return ''
    archivo = adjuntos[0].archivo
    try:
        paginas = paginas_de(bytes(archivo.contenido), archivo.nombre)
    except ErrorLectura as exc:
        manual.secciones, manual.lectura_aviso = [], str(exc)[:250]
    else:
        if not any(t.strip() for _, t in paginas):
            manual.secciones = []
            manual.lectura_aviso = ('El PDF es escaneado (solo imagen): no tiene texto que leer. Necesita OCR o '
                                    'escriba los puntos clave.')
        else:
            manual.secciones = secciones_de(paginas)
            manual.lectura_aviso = ''
    manual.leido_en = timezone.now()
    # contenido nuevo: vuelve a validarse antes de que Lucho lo cite (LUC-07)
    manual.validado, manual.validado_por, manual.validado_en = False, None, None
    manual.save(update_fields=['secciones', 'lectura_aviso', 'leido_en', 'validado', 'validado_por', 'validado_en'])
    if manual.lectura_aviso:
        return manual.lectura_aviso
    return (f'Lucho leyó «{archivo.nombre}»: {len(manual.secciones)} secciones en {len(paginas)} página(s). '
            f'Debe validarlo otra persona antes de que lo cite.')
