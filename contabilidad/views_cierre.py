"""Cierre guiado del mes, conciliación NIIF - tributaria y estados financieros consolidados del grupo."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from core.utils import excel_response

from . import cierre
from .centralizar import ErrorContable, centralizar_periodo
from .models import PeriodoContable
from .views import _mes_input, _periodo


@login_required
def cierre_mes(request):
    periodo = _periodo(request) if request.method == 'GET' else (request.POST.get('periodo') or '')[:6]
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if PeriodoContable.esta_cerrado(periodo) and accion != 'ver':
            messages.error(request, 'El periodo está cerrado.')
        elif accion in ('provisionar', 'no_provisionar'):
            PeriodoContable.objects.update_or_create(periodo=periodo, defaults={
                'provisiones': accion == 'provisionar', 'pendiente': True})
            messages.success(request, 'Provisiones activadas: se registran al centralizar.' if accion == 'provisionar'
                             else 'Provisiones desactivadas para el mes.')
        elif accion == 'centralizar':
            try:
                r = centralizar_periodo(periodo)
                messages.success(request, f'Periodo centralizado ({r["compras"]} compras, {r["ventas"]} ventas, '
                                          f'{r["tesoreria"]} movimientos).')
                for e in r['errores']:
                    messages.warning(request, e)
            except ErrorContable as exc:
                messages.error(request, str(exc))
        elif accion == 'cerrar':
            pendientes = [p['titulo'] for p in cierre.pasos(periodo)[0]
                          if p['estado'] == 'pendiente' and p['titulo'] != 'Cierre del periodo contable']
            if pendientes and not request.POST.get('forzar'):
                messages.error(request, 'Faltan pasos: ' + ', '.join(pendientes) + '. Complételos o confirme el cierre '
                                        'con pasos pendientes.')
            else:
                PeriodoContable.objects.update_or_create(periodo=periodo, defaults={'cerrado': True})
                messages.success(request, f'Periodo {periodo[4:]}/{periodo[:4]} cerrado.')
        return redirect(f'{request.path}?periodo={_mes_input(periodo)}')
    lista, p = cierre.pasos(periodo)
    from planillas.provisiones import provisiones_del_mes
    prov = provisiones_del_mes(periodo)
    hechos = sum(1 for x in lista if x['estado'] in ('ok', 'na'))
    return render(request, 'contabilidad/cierre.html', {
        'pasos': lista, 'p': p, 'periodo': periodo, 'mes': _mes_input(periodo), 'prov': prov,
        'avance': round(hechos * 100 / len(lista)) if lista else 0, 'hechos': hechos})


@login_required
def conciliacion_normas(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    datos = cierre.conciliacion_normas(desde, hasta)
    if request.GET.get('formato') == 'excel':
        filas = [[f['codigo'], f['nombre'], f['niif'], f['trib'], f['diferencia']] for f in datos['filas']]
        filas.append(['', 'RESULTADO DEL EJERCICIO', datos['res_niif'], datos['res_trib'], datos['dif_resultado']])
        return excel_response(f'Conciliacion_NIIF_tributaria_{desde}_{hasta}',
                              f'CONCILIACIÓN NIIF - TRIBUTARIA {desde} - {hasta}',
                              ['Cuenta', 'Denominación', 'Saldo NIIF', 'Saldo tributario', 'Diferencia'], filas)
    return render(request, 'contabilidad/conciliacion_normas.html', {
        **datos, 'desde': desde, 'hasta': hasta, 'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta)})


@login_required
def conciliacion_migracion(request):
    """Auxiliares del ERP (documentos por cobrar y pagar, caja y bancos, kardex) frente a sus cuentas contables."""
    from datetime import date

    from historial.contabilidad_anterior import conciliacion
    try:
        hasta = date.fromisoformat(request.GET.get('hasta') or '')
    except ValueError:
        hasta = None
    filas, corte = conciliacion(hasta)
    return render(request, 'contabilidad/conciliacion_migracion.html', {
        'filas': filas, 'corte': corte, 'hasta': hasta or date.today()})


@login_required
def consolidacion(request):
    """Solo administradores: lee los libros de todas las empresas del grupo."""
    if not request.user.is_superuser:
        raise Http404
    from erp.empresas import es_multiempresa
    hasta = _periodo(request, 'hasta')
    if not es_multiempresa():
        return render(request, 'contabilidad/consolidacion.html', {'sin_grupo': True, 'hasta': hasta,
                                                                    'mes_hasta': _mes_input(hasta)})
    datos = cierre.consolidar(hasta)
    return render(request, 'contabilidad/consolidacion.html', {**datos, 'hasta': hasta,
                                                                'mes_hasta': _mes_input(hasta)})
