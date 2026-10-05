from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.utils import excel_response

from . import presupuesto as servicio
from .forms import LineasPresupuestoFormSet, PresupuestoForm
from .models import Presupuesto, PresupuestoLinea

MESES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Set', 'Oct', 'Nov', 'Dic']


@login_required
def presupuestos(request):
    return render(request, 'contabilidad/presupuestos.html', {
        'presupuestos': Presupuesto.objects.prefetch_related('lineas__cuenta')})


def _guardar(request, pres, titulo):
    form = PresupuestoForm(request.POST or None, instance=pres)
    lineas = LineasPresupuestoFormSet(request.POST or None, instance=pres, prefix='lineas')
    if request.method == 'POST' and form.is_valid() and lineas.is_valid():
        with transaction.atomic():
            pres = form.save()
            lineas.instance = pres
            lineas.save()
        messages.success(request, f'Presupuesto {pres} guardado.')
        return redirect('contabilidad:presupuesto_ejecucion', pres.pk)
    return render(request, 'contabilidad/presupuesto_form.html', {
        'form': form, 'lineas': lineas, 'titulo': titulo, 'pres': pres, 'meses': MESES,
        'campos_mes': PresupuestoLinea.MESES})


@login_required
def presupuesto_nuevo(request):
    return _guardar(request, Presupuesto(anio=date.today().year + (date.today().month >= 10)), 'Nuevo presupuesto')


@login_required
def presupuesto_editar(request, pk):
    pres = get_object_or_404(Presupuesto, pk=pk)
    return _guardar(request, pres, f'Editar presupuesto {pres}')


@login_required
def presupuesto_generar(request, pk):
    """Propuesta: el real del año anterior por cuenta, centro de costo y mes, con un % de variación."""
    pres = get_object_or_404(Presupuesto, pk=pk)
    if request.method == 'POST':
        if pres.estado == 'APROBADO':
            messages.error(request, 'El presupuesto está aprobado: páselo a borrador para regenerarlo.')
        else:
            try:
                pct = Decimal(request.POST.get('variacion') or '0')
            except InvalidOperation:
                pct = Decimal('0')
            n = servicio.generar_desde_real(pres, pct)
            if n:
                messages.success(request, f'Se generaron {n} líneas desde el real de {pres.anio - 1} '
                                          f'({pct:+}%). Revíselas y ajústelas.')
            else:
                messages.warning(request, f'No hay movimientos de ingresos y gastos en {pres.anio - 1}.')
    return redirect('contabilidad:presupuesto_editar', pk)


@login_required
def presupuesto_ejecucion(request, pk):
    pres = get_object_or_404(Presupuesto, pk=pk)
    hoy = date.today()
    defecto = hoy.month if pres.anio == hoy.year else 12
    try:
        hasta_mes = min(max(int(request.GET.get('mes') or defecto), 1), 12)
    except ValueError:
        hasta_mes = defecto
    filas, tot = servicio.ejecucion(pres, hasta_mes)
    if request.GET.get('formato') == 'excel':
        datos = [[f['l'].cuenta.codigo, f['l'].cuenta.nombre, f['l'].centro_costo.codigo if f['l'].centro_costo else '',
                  f['anual'], f['pres'], f['real'], f['var'], f['pct']] for f in filas]
        datos.append(['', 'RESULTADO (ingresos − gastos)', '', tot['anual'], tot['pres'], tot['real'], tot['var'], None])
        return excel_response(f'Ejecucion_presupuesto_{pres.anio}', f'EJECUCIÓN DEL PRESUPUESTO {pres} A {MESES[hasta_mes - 1].upper()}',
                              ['Cuenta', 'Denominación', 'Centro de costo', 'Presupuesto anual',
                               f'Presupuesto a {MESES[hasta_mes - 1]}', f'Real a {MESES[hasta_mes - 1]}', 'Variación',
                               '% ejecución'], datos)
    return render(request, 'contabilidad/presupuesto_ejecucion.html', {
        'pres': pres, 'filas': filas, 'tot': tot, 'hasta_mes': hasta_mes, 'meses': list(enumerate(MESES, 1))})
