"""Manuales de usuario en PDF (carpeta Manuales del proyecto): solo los administradores los ven y descargan. Las
novedades por versión (del CHANGELOG) las ven todos los usuarios."""
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render

CARPETA = settings.BASE_DIR / 'Manuales'
PROCESOS = CARPETA / 'Procesos por módulo'  # procesos, pantallas y acciones de cada módulo (scripts/manual_procesos)


def _manuales(carpeta=CARPETA):
    """PDF de la carpeta en el orden de su número (1, 2… 10), no alfabético."""
    def orden(p):
        n = re.match(r'^(\d+)', p.name)
        return (int(n.group(1)) if n else 999, p.name)
    return sorted(carpeta.glob('*.pdf'), key=orden) if carpeta.is_dir() else []


def _fichas(carpeta):
    return [{'nombre': p.stem, 'archivo': p.name, 'kb': round(p.stat().st_size / 1024)} for p in _manuales(carpeta)]


def _solo_admin(request):
    if not request.user.is_superuser:
        raise Http404  # para el resto de usuarios la pantalla no existe


@login_required
def manuales(request):
    _solo_admin(request)
    return render(request, 'core/manuales.html', {'manuales': _fichas(CARPETA), 'procesos': _fichas(PROCESOS)})


def versiones(desde='1.0'):
    """[{version, fecha, titulo, items}] del CHANGELOG.md (lo más reciente primero), con los textos en HTML seguro
    (solo **negritas**). desde: versión mínima incluida."""
    import re

    from django.utils.html import escape
    ruta = settings.BASE_DIR / 'CHANGELOG.md'
    if not ruta.exists():
        return []
    minimo = tuple(int(x) for x in desde.split('.'))
    salida, actual = [], None
    for linea in ruta.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^## v(\d+)\.(\d+)\.(\d+) — ([\d/]+) · (.+)$', linea.strip())
        if m:
            numero = tuple(int(x) for x in m.groups()[:3])
            actual = None
            if numero[:len(minimo)] >= minimo:
                actual = {'version': '.'.join(m.groups()[:3]), 'fecha': m.group(4), 'titulo': m.group(5), 'items': []}
                salida.append(actual)
        elif actual is not None and linea.startswith('- '):
            texto = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escape(linea[2:].strip()))
            actual['items'].append(texto)
    return salida


@login_required
def novedades(request):
    """Novedades por versión: para todos los usuarios (qué cambió en el sistema y desde cuándo)."""
    desde = request.GET.get('desde') or '1.24'
    if not re.fullmatch(r'\d+\.\d+', desde):
        desde = '1.24'
    return render(request, 'core/novedades.html', {'versiones': versiones(desde), 'desde': desde,
                                                   'opciones': ['1.0', '1.24', '1.41', '1.50'],
                                                   'es_admin': request.user.is_superuser})


@login_required
def manual_pdf(request, archivo, carpeta=CARPETA):
    _solo_admin(request)
    pdf = next((p for p in _manuales(carpeta) if p.name == archivo), None)  # solo archivos de la carpeta, por nombre exacto
    if pdf is None:
        raise Http404
    descargar = request.GET.get('descargar') == '1'
    return FileResponse(open(pdf, 'rb'), content_type='application/pdf', as_attachment=descargar, filename=pdf.name)
