from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from compras.models import Compra
from core.models import Empresa
from core.utils import excel_response, fmt_fecha, txt_response
from core.views import FormGenerico, ListaGenerica
from finanzas.models import Movimiento
from ventas.models import Venta

from . import pcge, reportes
from .centralizar import ErrorContable, centralizar_periodo
from .forms import (AsientoForm, CentroCostoForm, CuentaContableForm, CuentaDefectoFormSet, lineas_formset)
from .models import LIBROS, ORIGENES, Asiento, AsientoLinea, CentroCosto, CuentaContable, CuentaDefecto, PeriodoContable

D0 = Decimal('0')


def _ultimo_periodo():
    return (Asiento.objects.order_by('-periodo').values_list('periodo', flat=True).first()
            or date.today().strftime('%Y%m'))


def _periodo(request, clave='periodo', defecto=None):
    valor = (request.GET.get(clave) or '').replace('-', '')
    return valor if len(valor) == 6 and valor.isdigit() else (defecto or _ultimo_periodo())


def _mes_input(periodo):
    return f'{periodo[:4]}-{periodo[4:]}'


# ---------------------------------------------------------------- periodos y centralización
@login_required
def periodos(request):
    if request.method == 'POST':
        periodo = request.POST.get('periodo', '')
        accion = request.POST.get('accion')
        if accion == 'centralizar':
            try:
                r = centralizar_periodo(periodo)
                messages.success(request, f"Periodo {periodo} centralizado: {r['compras']} compras, {r['ventas']} "
                                          f"ventas, {r['tesoreria']} movimientos de caja/bancos"
                                          f"{', costo de ventas' if r['costo'] else ''}.")
                for e in r['errores']:
                    messages.warning(request, e)
            except ErrorContable as exc:
                messages.error(request, str(exc))
        elif accion in ('cerrar', 'abrir'):
            PeriodoContable.objects.update_or_create(periodo=periodo, defaults={'cerrado': accion == 'cerrar'})
            messages.success(request, f'Periodo {periodo} {"cerrado" if accion == "cerrar" else "reabierto"}.')
        return redirect('contabilidad:periodos')

    meses = set(Compra.objects.values_list('periodo', flat=True)) | set(Venta.objects.values_list('periodo', flat=True))
    meses |= {f.strftime('%Y%m') for f in Movimiento.objects.dates('fecha', 'month')}
    meses |= set(Asiento.objects.values_list('periodo', flat=True))
    meses.add(date.today().strftime('%Y%m'))
    estado = {p.periodo: p for p in PeriodoContable.objects.all()}
    asientos = dict(Asiento.objects.values('periodo').annotate(n=Count('id')).values_list('periodo', 'n'))
    filas = []
    for p in sorted((m for m in meses if m), reverse=True):
        filas.append({
            'periodo': p, 'estado': estado.get(p), 'asientos': asientos.get(p, 0),
            'compras': Compra.objects.filter(periodo=p, estado='REGISTRADO').count(),
            'ventas': Venta.objects.filter(periodo=p, estado='REGISTRADO').count(),
            'movs': Movimiento.objects.filter(fecha__year=int(p[:4]), fecha__month=int(p[4:])).count(),
        })
    return render(request, 'contabilidad/periodos.html', {'filas': filas})


# ---------------------------------------------------------------- asientos
@login_required
def asientos(request):
    qs = Asiento.objects.annotate(total=Sum('lineas__debe'))
    periodo = _periodo(request)
    qs = qs.filter(periodo=periodo)
    for campo in ('libro', 'origen'):
        if request.GET.get(campo):
            qs = qs.filter(**{campo: request.GET[campo]})
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(glosa__icontains=q) | Q(numero__icontains=q) | Q(lineas__cuenta__codigo__startswith=q)).distinct()
    return render(request, 'contabilidad/asientos.html', {
        'page_obj': Paginator(qs.order_by('fecha', 'libro', 'numero'), 50).get_page(request.GET.get('page')),
        'periodo': periodo, 'mes': _mes_input(periodo), 'libros': LIBROS, 'origenes': ORIGENES, 'q': q,
        'cerrado': PeriodoContable.esta_cerrado(periodo)})


@login_required
def asiento_detalle(request, pk):
    a = get_object_or_404(Asiento, pk=pk)
    lineas = a.lineas.select_related('cuenta', 'tercero', 'centro_costo')
    d, h = a.totales
    return render(request, 'contabilidad/asiento_detalle.html', {
        'a': a, 'lineas': lineas, 'debe': d, 'haber': h, 'cerrado': PeriodoContable.esta_cerrado(a.periodo)})


def _guardar_asiento(request, asiento, titulo):
    form = AsientoForm(request.POST or None, instance=asiento)
    formset = lineas_formset(extra=0 if asiento.pk else 4)(request.POST or None, instance=asiento)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            a = form.save(commit=False)
            a.origen = 'MANUAL'
            if asiento.pk and a.fecha.strftime('%Y%m') != asiento.periodo:
                a.numero = ''  # cambió de periodo: nuevo correlativo
            a.save()
            formset.instance = a
            formset.save()
        messages.success(request, f'Asiento {a.numero} guardado.')
        return redirect('contabilidad:asiento_detalle', a.pk)
    cuentas = list(CuentaContable.objects.filter(imputable=True, activo=True).values('id', 'codigo', 'nombre'))
    return render(request, 'contabilidad/asiento_form.html', {'form': form, 'formset': formset, 'titulo': titulo,
                                                              'cuentas': cuentas})


@login_required
def asiento_nuevo(request):
    return _guardar_asiento(request, Asiento(libro='05'), 'Nuevo asiento manual')


@login_required
def asiento_editar(request, pk):
    a = get_object_or_404(Asiento, pk=pk)
    if a.origen != 'MANUAL' or PeriodoContable.esta_cerrado(a.periodo):
        messages.error(request, 'Solo se editan asientos manuales de periodos abiertos. Los automáticos se '
                                'regeneran al centralizar.')
        return redirect('contabilidad:asiento_detalle', pk)
    return _guardar_asiento(request, a, f'Editar asiento {a.numero}')


@login_required
def asiento_eliminar(request, pk):
    a = get_object_or_404(Asiento, pk=pk)
    if request.method == 'POST':
        if a.origen != 'MANUAL' or PeriodoContable.esta_cerrado(a.periodo):
            messages.error(request, 'Solo se eliminan asientos manuales de periodos abiertos.')
            return redirect('contabilidad:asiento_detalle', pk)
        a.delete()
        messages.success(request, 'Asiento eliminado.')
    return redirect('contabilidad:asientos')


# ---------------------------------------------------------------- libros
@login_required
def libro_diario(request):
    periodo = _periodo(request)
    asientos_qs = (Asiento.objects.filter(periodo=periodo).order_by('fecha', 'libro', 'numero')
                   .prefetch_related('lineas__cuenta', 'lineas__tercero', 'lineas__centro_costo'))
    formato = request.GET.get('formato')
    if formato == 'ple':
        return _diario_ple(periodo, asientos_qs)
    if formato == 'excel':
        filas = []
        for a in asientos_qs:
            for l in a.lineas.all():
                filas.append([a.numero, fmt_fecha(a.fecha), a.get_libro_display(), a.glosa, l.cuenta.codigo,
                              l.cuenta.nombre, l.tercero.numero_doc if l.tercero else '', l.documento,
                              l.centro_costo.codigo if l.centro_costo else '', l.debe, l.haber])
        empresa = Empresa.actual()
        return excel_response(f'Libro_diario_{periodo}', f'LIBRO DIARIO {periodo} - {empresa.razon_social} RUC {empresa.ruc}',
                              ['Asiento', 'Fecha', 'Libro', 'Glosa', 'Cuenta', 'Denominación', 'RUC/DNI', 'Documento',
                               'C. costo', 'Debe', 'Haber'], filas)
    tot = AsientoLinea.objects.filter(asiento__periodo=periodo).aggregate(d=Sum('debe'), h=Sum('haber'))
    return render(request, 'contabilidad/diario.html', {
        'asientos': asientos_qs, 'periodo': periodo, 'mes': _mes_input(periodo),
        'debe': tot['d'] or D0, 'haber': tot['h'] or D0})


def _diario_ple(periodo, asientos_qs):
    """Libro Diario formato 5.1 (PLE)."""
    lineas = []
    for a in asientos_qs:
        doc = a.documento
        for n, l in enumerate(a.lineas.all(), 1):
            tercero = l.tercero
            tipo_doc = serie = numero = ''
            if doc is not None and hasattr(doc, 'tipo_comprobante'):
                tipo_doc, serie, numero = doc.tipo_comprobante, doc.serie, doc.numero
            campos = [f'{periodo}00', a.numero.replace('-', ''), f'M{n}', l.cuenta.codigo, '',
                      l.centro_costo.codigo if l.centro_costo else '', a.moneda,
                      tercero.tipo_doc if tercero else '', tercero.numero_doc if tercero else '',
                      tipo_doc or '00', serie, numero, fmt_fecha(a.fecha), '', fmt_fecha(a.fecha),
                      (l.glosa or a.glosa)[:200].replace('|', ' '), '', f'{l.debe:.2f}', f'{l.haber:.2f}', '', '1']
            lineas.append('|'.join(campos) + '|')
    empresa = Empresa.actual()
    indicador = '1' if lineas else '0'
    return txt_response(f'LE{empresa.ruc}{periodo}00050100001{indicador}11.txt', lineas)


@login_required
def libro_mayor(request):
    cuentas = CuentaContable.objects.filter(imputable=True)
    cuenta = cuentas.filter(pk=request.GET.get('cuenta')).first() if request.GET.get('cuenta') else None
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    ctx = {'cuentas': cuentas, 'cuenta': cuenta, 'desde': desde, 'hasta': hasta,
           'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta)}
    if cuenta:
        base = AsientoLinea.objects.filter(cuenta=cuenta).select_related('asiento', 'tercero')
        ant = base.filter(asiento__periodo__lt=desde).aggregate(d=Sum('debe'), h=Sum('haber'))
        saldo = (ant['d'] or D0) - (ant['h'] or D0)
        inicial = saldo
        filas = []
        for l in base.filter(asiento__periodo__gte=desde, asiento__periodo__lte=hasta).order_by('asiento__fecha', 'asiento__numero', 'id'):
            saldo += l.debe - l.haber
            filas.append({'l': l, 'saldo': saldo})
        if request.GET.get('formato') == 'excel':
            datos = [[fmt_fecha(f['l'].asiento.fecha), f['l'].asiento.numero, f['l'].glosa or f['l'].asiento.glosa,
                      f['l'].tercero.nombre if f['l'].tercero else '', f['l'].documento, f['l'].debe, f['l'].haber,
                      f['saldo']] for f in filas]
            datos.insert(0, ['', '', 'Saldo anterior', '', '', '', '', inicial])
            return excel_response(f'Mayor_{cuenta.codigo}', f'LIBRO MAYOR {cuenta} {desde}-{hasta}',
                                  ['Fecha', 'Asiento', 'Glosa', 'Tercero', 'Documento', 'Debe', 'Haber', 'Saldo'], datos)
        ctx.update(filas=filas, inicial=inicial, final=saldo,
                   debe=sum((f['l'].debe for f in filas), D0), haber=sum((f['l'].haber for f in filas), D0))
    return render(request, 'contabilidad/mayor.html', ctx)


@login_required
def balance(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    nivel = int(request.GET.get('nivel') or 0) or None
    filas, tot = reportes.balance_comprobacion(desde, hasta, nivel)
    if request.GET.get('formato') == 'excel':
        datos = [[f['codigo'], f['nombre'], f['d'], f['h'], f['sd'], f['sa'], f.get('activo', D0), f.get('pasivo', D0),
                  f.get('nat_p', D0), f.get('nat_g', D0), f.get('fun_p', D0), f.get('fun_g', D0)] for f in filas]
        datos.append(['', 'TOTALES', tot['d'], tot['h'], tot['sd'], tot['sa'], tot['activo'], tot['pasivo'],
                      tot['nat_p'], tot['nat_g'], tot['fun_p'], tot['fun_g']])
        return excel_response(f'Balance_comprobacion_{desde}_{hasta}', f'BALANCE DE COMPROBACIÓN {desde} - {hasta}',
                              ['Cuenta', 'Denominación', 'Debe', 'Haber', 'Deudor', 'Acreedor', 'Activo',
                               'Pasivo y patrimonio', 'Pérdidas (naturaleza)', 'Ganancias (naturaleza)',
                               'Pérdidas (función)', 'Ganancias (función)'], datos)
    return render(request, 'contabilidad/balance.html', {
        'filas': filas, 'tot': tot, 'desde': desde, 'hasta': hasta, 'nivel': nivel or 0,
        'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta)})


@login_required
def estado_situacion(request):
    hasta = _periodo(request, 'hasta')
    datos = reportes.situacion_financiera(hasta, f'{hasta[:4]}01')
    return render(request, 'contabilidad/situacion.html', {**datos, 'hasta': hasta, 'mes_hasta': _mes_input(hasta)})


@login_required
def estado_resultados(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    lineas, neta = reportes.estado_resultados(desde, hasta)
    return render(request, 'contabilidad/resultados.html', {
        'lineas': lineas, 'neta': neta, 'desde': desde, 'hasta': hasta,
        'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta)})


@login_required
def centros_costo_reporte(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    filas = (reportes.lineas_rango(desde, hasta).filter(es_destino=False, cuenta__codigo__regex=r'^(6|3)')
             .values('centro_costo__codigo', 'centro_costo__nombre', 'cuenta__codigo', 'cuenta__nombre')
             .annotate(d=Sum('debe'), h=Sum('haber')).order_by('centro_costo__codigo', 'cuenta__codigo'))
    grupos = OrderedDict()
    for f in filas:
        clave = f['centro_costo__codigo'] or '—'
        g = grupos.setdefault(clave, {'nombre': f['centro_costo__nombre'] or 'Sin centro de costo', 'filas': [],
                                      'total': D0})
        neto = (f['d'] or D0) - (f['h'] or D0)
        g['filas'].append({'codigo': f['cuenta__codigo'], 'nombre': f['cuenta__nombre'], 'importe': neto})
        g['total'] += neto
    return render(request, 'contabilidad/centros_reporte.html', {
        'grupos': grupos, 'desde': desde, 'hasta': hasta, 'mes_desde': _mes_input(desde),
        'mes_hasta': _mes_input(hasta), 'total': sum((g['total'] for g in grupos.values()), D0)})


# ---------------------------------------------------------------- plan de cuentas y configuración
@login_required
def plan_cuentas(request):
    if request.method == 'POST' and request.POST.get('accion') == 'cargar_pcge':
        antes = CuentaContable.objects.count()
        pcge.cargar(CuentaContable, CuentaDefecto)
        messages.success(request, f'Plan contable actualizado: {CuentaContable.objects.count() - antes} cuentas nuevas.')
        return redirect('contabilidad:plan')
    qs = CuentaContable.objects.select_related('destino_debe', 'destino_haber')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(codigo__startswith=q) | Q(nombre__icontains=q))
    if request.GET.get('clase'):
        qs = qs.filter(codigo__startswith=request.GET['clase'])
    if request.GET.get('formato') == 'ple':
        empresa = Empresa.actual()
        periodo = date.today().strftime('%Y%m')
        lineas = [f'{periodo}00|{c.codigo}|{c.nombre}|01|PLAN CONTABLE GENERAL EMPRESARIAL|||1|' for c in qs]
        return txt_response(f'LE{empresa.ruc}{periodo}00050300001111.txt', lineas)
    return render(request, 'contabilidad/plan.html', {'cuentas': qs, 'q': q, 'clase': request.GET.get('clase', '')})


class CuentaNueva(FormGenerico, CreateView):
    model, form_class, titulo = CuentaContable, CuentaContableForm, 'Nueva cuenta contable'
    success_url = reverse_lazy('contabilidad:plan')


class CuentaEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CuentaContable, CuentaContableForm, 'Editar cuenta contable'
    success_url = reverse_lazy('contabilidad:plan')


@login_required
def configuracion(request):
    if not CuentaDefecto.objects.exists():
        pcge.cargar(CuentaContable, CuentaDefecto)
    formset = CuentaDefectoFormSet(request.POST or None, queryset=CuentaDefecto.objects.select_related('cuenta'))
    if request.method == 'POST' and formset.is_valid():
        formset.save()
        messages.success(request, 'Configuración contable guardada. Vuelva a centralizar los periodos abiertos '
                                  'para aplicar los cambios.')
        return redirect('contabilidad:configuracion')
    return render(request, 'contabilidad/configuracion.html', {'formset': formset})


class CentroCostoLista(ListaGenerica):
    model = CentroCosto
    titulo = 'Centros de costo'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'contabilidad:cc_nuevo', 'contabilidad:cc_editar'
    buscar_en = ['codigo', 'nombre']


class CentroCostoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = CentroCosto, CentroCostoForm, 'Nuevo centro de costo'
    success_url = reverse_lazy('contabilidad:centros')


class CentroCostoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CentroCosto, CentroCostoForm, 'Editar centro de costo'
    success_url = reverse_lazy('contabilidad:centros')
