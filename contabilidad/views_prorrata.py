"""Prorrata del crédito fiscal del IGV por mes (Reglamento de la Ley del IGV, art. 6 num. 6.2)."""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from core.utils import excel_response

from . import prorrata as pr


@login_required
def prorrata(request):
    hoy = timezone.localdate()
    anio = int(request.GET['anio']) if (request.GET.get('anio') or '').isdigit() else hoy.year
    filas = pr.reporte(anio)
    totales = {k: sum((f[k] for f in filas), Decimal('0')) for k in (
        'gravadas', 'no_gravadas', 'igv_gravada', 'igv_comun', 'igv_no_gravada', 'credito', 'al_costo')}
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Prorrata_IGV_{anio}', f'Prorrata del crédito fiscal {anio}', [
            'Periodo', 'Operaciones gravadas y exportación', 'Operaciones no gravadas', 'Coeficiente (12 meses)',
            'IGV destinado a gravadas', 'IGV común', 'IGV destinado a no gravadas', 'Crédito fiscal',
            'IGV al costo o gasto'],
            [[f'{f["periodo"][4:]}/{anio}', f['gravadas'], f['no_gravadas'], f['coeficiente'], f['igv_gravada'],
              f['igv_comun'], f['igv_no_gravada'], f['credito'], f['al_costo']] for f in filas])
    return render(request, 'contabilidad/prorrata.html', {'filas': filas, 'totales': totales, 'anio': anio,
                                                           'anios': range(hoy.year - 4, hoy.year + 1)})
