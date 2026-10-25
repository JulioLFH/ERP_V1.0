"""Consulta RUC (v1.83): padrón de SUNAT de uno o varios RUC (estado, condición, domicilio fiscal), con aviso si ya
es cliente o proveedor y su historial."""
import re

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import ConsultaRUC, Tercero

MAXIMO = 20


def rucs_de(texto):
    """RUC (11 dígitos) del texto pegado, sin repetir y en orden."""
    return list(dict.fromkeys(re.findall(r'\b\d{11}\b', texto or '')))[:MAXIMO]


@login_required
def consulta(request):
    from .padron_ruc import ErrorPadron, consultar
    texto = request.POST.get('rucs', '') if request.method == 'POST' else request.GET.get('ruc', '')
    rucs = rucs_de(texto)
    resultados = []
    if rucs:
        registrados = {t.numero_doc: t for t in Tercero.objects.filter(numero_doc__in=rucs)}
        for ruc in rucs:
            try:
                datos = consultar(ruc)
                c = ConsultaRUC.objects.create(
                    ruc=ruc, razon_social=datos['razon_social'][:250], estado=datos['estado'][:30],
                    condicion=datos['condicion'][:30], direccion=datos['direccion'][:300], ubigeo=datos['ubigeo'],
                    datos={k: v for k, v in datos.items() if k != 'locales_anexos'}, usuario=request.user)
                resultados.append({'c': c, 'datos': datos, 'tercero': registrados.get(ruc)})
            except ErrorPadron as exc:
                ConsultaRUC.objects.create(ruc=ruc, error=str(exc)[:250], usuario=request.user)
                resultados.append({'ruc': ruc, 'error': str(exc), 'tercero': registrados.get(ruc)})
    return render(request, 'core/ruc_consulta.html', {
        'texto': texto, 'resultados': resultados, 'maximo': MAXIMO,
        'con_alerta': sum(1 for r in resultados if r.get('c') and r['c'].alerta)})


@login_required
def historial(request):
    qs = ConsultaRUC.objects.select_related('usuario')
    if request.GET.get('todas') != '1':
        qs = qs.filter(usuario=request.user)
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(ruc__startswith=q) if q.isdigit() else qs.filter(razon_social__icontains=q)
    return render(request, 'core/ruc_historial.html', {'consultas': qs[:300], 'q': q,
                                                       'todas': request.GET.get('todas') == '1'})
