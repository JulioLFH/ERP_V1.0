"""Manuales de usuario en PDF (carpeta Manuales del proyecto): solo los administradores los ven y descargan."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render

CARPETA = settings.BASE_DIR / 'Manuales'


def _manuales():
    return sorted(CARPETA.glob('*.pdf')) if CARPETA.is_dir() else []


def _solo_admin(request):
    if not request.user.is_superuser:
        raise Http404  # para el resto de usuarios la pantalla no existe


@login_required
def manuales(request):
    _solo_admin(request)
    return render(request, 'core/manuales.html', {
        'manuales': [{'nombre': p.stem, 'archivo': p.name, 'kb': round(p.stat().st_size / 1024)} for p in _manuales()]})


@login_required
def manual_pdf(request, archivo):
    _solo_admin(request)
    pdf = next((p for p in _manuales() if p.name == archivo), None)  # solo archivos de la carpeta, por nombre exacto
    if pdf is None:
        raise Http404
    descargar = request.GET.get('descargar') == '1'
    return FileResponse(open(pdf, 'rb'), content_type='application/pdf', as_attachment=descargar, filename=pdf.name)
