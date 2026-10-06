from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Empresa
from core.utils import excel_response

from . import calculo, servicios
from .forms import (AFPFormSet, ConceptosFormSet, FilasFormSet, LiquidacionFormSet, PagoForm, ParametrosFormSet,
                    PlanillaForm, TrabajadorForm, VacacionForm)
from .models import ConceptoPlanilla, FilaPlanilla, Planilla, Trabajador, Vacacion


# ---------------------------------------------------------------- trabajadores
@login_required
def trabajadores(request):
    from datetime import date
    qs = Trabajador.objects.select_related('afp', 'centro_costo')
    estado = request.GET.get('estado', 'activos')
    hoy = date.today()
    if estado == 'activos':
        qs = qs.filter(Q(fecha_cese__isnull=True) | Q(fecha_cese__gte=hoy))
    elif estado == 'cesados':
        qs = qs.filter(fecha_cese__lt=hoy)
    elif estado == 'incompletos':
        qs = qs.filter(Q(fecha_ingreso__isnull=True) | Q(sueldo__lte=0)).filter(fecha_cese__isnull=True)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero_doc__startswith=q) | Q(apellido_paterno__icontains=q) | Q(nombres__icontains=q) |
                       Q(cargo__icontains=q))
    if request.GET.get('formato') == 'excel':
        return excel_response('Trabajadores', 'TRABAJADORES', [
            'Documento', 'Apellidos y nombres', 'Cargo', 'Centro de costo', 'Ingreso', 'Cese', 'Régimen', 'Sueldo',
            'Asig. familiar', 'Pensiones', 'AFP', 'CUSPP'], [
            [t.numero_doc, t.nombre_completo, t.cargo, str(t.centro_costo or ''), t.fecha_ingreso, t.fecha_cese,
             t.get_regimen_display(), t.sueldo, 'Sí' if t.asignacion_familiar else 'No',
             t.get_sistema_pensiones_display(), str(t.afp or ''), t.cuspp] for t in qs])
    return render(request, 'planillas/trabajadores.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'estado': estado, 'q': q,
        'estados': [('activos', 'Activos'), ('cesados', 'Cesados'), ('incompletos', 'Por completar'),
                    ('todos', 'Todos')],
        'incompletos': Trabajador.objects.filter(Q(fecha_ingreso__isnull=True) | Q(sueldo__lte=0),
                                                 fecha_cese__isnull=True).count()})


def _guardar_trabajador(request, t, titulo):
    form = TrabajadorForm(request.POST or None, instance=t)
    if request.method == 'POST' and form.is_valid():
        t = form.save()
        messages.success(request, f'Trabajador {t.nombre_completo} guardado.')
        return redirect('planillas:trabajadores')
    return render(request, 'planillas/trabajador_form.html', {'form': form, 'titulo': titulo, 't': t})


@login_required
def trabajador_nuevo(request):
    return _guardar_trabajador(request, Trabajador(), 'Nuevo trabajador')


@login_required
def trabajador_editar(request, pk):
    t = get_object_or_404(Trabajador, pk=pk)
    return _guardar_trabajador(request, t, f'Editar {t.nombre_completo}')


# ---------------------------------------------------------------- planillas
@login_required
def planillas(request):
    return render(request, 'planillas/planillas.html', {
        'planillas': Planilla.objects.all()[:60]})


@login_required
def planilla_nueva(request):
    form = PlanillaForm(request.POST or None, initial={'tipo': request.GET.get('tipo', 'MENSUAL')})
    if request.method == 'POST' and form.is_valid():
        planilla = form.save()
        incompletos = calculo.generar_filas(planilla)
        messages.success(request, f'{planilla} creada con {planilla.filas.count()} trabajadores.')
        if incompletos:
            messages.warning(request, f'{len(incompletos)} trabajadores activos no se incluyeron por datos incompletos '
                                      f'(fecha de ingreso, sueldo o AFP).')
        return redirect('planillas:detalle', planilla.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva planilla'})


@login_required
def planilla_detalle(request, pk):
    planilla = get_object_or_404(Planilla, pk=pk)
    editable = planilla.estado in ('BORRADOR', 'CALCULADA')
    filas_qs = planilla.filas.select_related('trabajador__afp', 'trabajador__centro_costo') \
        .prefetch_related('lineas__concepto')
    clase = {'MENSUAL': FilasFormSet, 'LIQUIDACION': LiquidacionFormSet}.get(planilla.tipo)
    formset = clase(request.POST or None, queryset=filas_qs, prefix='filas') if editable and clase else None
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion in ('guardar', 'calcular') and formset is not None:
                if not formset.is_valid():
                    messages.error(request, 'Revise los datos marcados.')
                    return render(request, 'planillas/planilla.html', _ctx(planilla, formset, filas_qs))
                formset.save()
            if accion == 'generar':
                incompletos = calculo.generar_filas(planilla)
                messages.success(request, 'Trabajadores actualizados.')
                if incompletos:
                    messages.warning(request, f'{len(incompletos)} trabajadores con datos incompletos no se incluyeron.')
            elif accion == 'calcular':
                calculo.calcular(planilla)
                messages.success(request, 'Planilla calculada.')
            elif accion == 'guardar':
                planilla.estado = 'BORRADOR' if planilla.estado == 'CALCULADA' else planilla.estado
                planilla.save(update_fields=['estado'])
                messages.info(request, 'Datos guardados: vuelva a calcular.')
            elif accion == 'cerrar':
                servicios.cerrar(planilla)
                messages.success(request, 'Planilla cerrada: se contabiliza al centralizar el periodo.')
            elif accion == 'reabrir':
                servicios.reabrir(planilla)
                messages.info(request, 'Planilla reabierta.')
            elif accion == 'quitar':
                if not editable:
                    raise calculo.ErrorPlanilla('La planilla está cerrada.')
                planilla.filas.filter(pk=request.POST.get('fila')).delete()
            elif accion == 'pagar':
                form = PagoForm(request.POST)
                if form.is_valid():
                    mov = servicios.pagar(planilla, form.cleaned_data['cuenta'], form.cleaned_data['fecha'],
                                          request.user)
                    messages.success(request, f'Pago registrado en tesorería ({mov.voucher}).')
        except (calculo.ErrorPlanilla, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect('planillas:detalle', pk)
    if request.GET.get('formato') == 'excel':
        return _excel(planilla, filas_qs)
    return render(request, 'planillas/planilla.html', _ctx(planilla, formset, filas_qs))


def _ctx(planilla, formset, filas_qs):
    conceptos = [c for c in ConceptoPlanilla.objects.all()
                 if any(l.concepto_id == c.pk for f in filas_qs for l in f.lineas.all())]
    filas = list(filas_qs)
    for f in filas:
        montos = {l.concepto_id: l.monto for l in f.lineas.all()}
        f.columnas = [montos.get(c.pk) for c in conceptos]
    formularios = {f.instance.pk: f for f in formset.forms} if formset is not None else {}
    for f in filas:
        f.form = formularios.get(f.pk)
    columnas = formset.form.COLUMNAS if formset is not None else []
    return {'planilla': planilla, 'formset': formset, 'filas': filas, 'conceptos': conceptos,
            'columnas_form': columnas, 'n_columnas': len(columnas),
            'totales': planilla.totales(), 'pago_form': PagoForm(),
            'totales_concepto': [sum((x or 0) for x in col) for col in zip(*[f.columnas for f in filas])]
            if filas else []}


def _excel(planilla, filas_qs):
    conceptos = list(ConceptoPlanilla.objects.all())
    filas = []
    for f in filas_qs:
        montos = {l.concepto_id: l.monto for l in f.lineas.all()}
        t = f.trabajador
        filas.append([t.numero_doc, t.nombre_completo, str(t.centro_costo or ''), f.dias_laborados] +
                     [montos.get(c.pk, 0) for c in conceptos] + [f.total_ingresos, f.total_descuentos, f.neto,
                                                                  f.total_aportes])
    return excel_response(f'Planilla_{planilla.tipo}_{planilla.periodo}', str(planilla).upper(),
                          ['Documento', 'Trabajador', 'Centro de costo', 'Días'] + [c.nombre for c in conceptos] +
                          ['Total ingresos', 'Total descuentos', 'Neto a pagar', 'Aportes del empleador'], filas)


@login_required
def boletas(request, pk):
    planilla = get_object_or_404(Planilla, pk=pk)
    filas = planilla.filas.select_related('trabajador__afp', 'trabajador__centro_costo') \
        .prefetch_related('lineas__concepto')
    if request.GET.get('fila'):
        filas = filas.filter(pk=request.GET['fila'])
    return render(request, 'planillas/boletas.html', {
        'planilla': planilla, 'filas': filas, 'empresa': Empresa.actual(),
        'tipos': [('INGRESO', 'Ingresos'), ('DESCUENTO', 'Descuentos'), ('APORTE', 'Aportes del empleador')]})


@login_required
def plame(request, periodo):
    try:
        nombre, datos = servicios.archivos_plame(periodo, Empresa.actual().ruc)
    except (calculo.ErrorPlanilla, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect('planillas:lista')
    resp = HttpResponse(datos, content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return resp


@login_required
def aportes_afp(request, periodo):
    """Retenciones por AFP y trabajador del periodo (para declarar y pagar en AFPnet)."""
    filas = FilaPlanilla.objects.filter(planilla__periodo=periodo, planilla__tipo='MENSUAL',
                                        planilla__estado__in=('CALCULADA', 'CERRADA', 'PAGADA'),
                                        trabajador__sistema_pensiones='AFP') \
        .select_related('trabajador__afp').prefetch_related('lineas__concepto')
    datos = [[f.trabajador.afp.nombre if f.trabajador.afp else '', f.trabajador.cuspp, f.trabajador.numero_doc,
              f.trabajador.nombre_completo, f.remuneracion_afecta, f.monto('AFP_APORTE'), f.monto('AFP_PRIMA'),
              f.monto('AFP_COMISION'), f.monto('AFP_APORTE') + f.monto('AFP_PRIMA') + f.monto('AFP_COMISION')]
             for f in sorted(filas, key=lambda x: (str(x.trabajador.afp), x.trabajador.apellido_paterno))]
    return excel_response(f'Aportes_AFP_{periodo}', f'APORTES AFP {periodo}', [
        'AFP', 'CUSPP', 'DNI', 'Trabajador', 'Remuneración asegurable', 'Aporte obligatorio', 'Prima de seguro',
        'Comisión', 'Total'], datos)


# ---------------------------------------------------------------- vacaciones
@login_required
def vacaciones(request):
    """Récord vacacional de cada trabajador activo y registro de goces / ventas."""
    from datetime import date
    form = VacacionForm(request.POST or None, initial={'fecha_inicio': date.today(), 'fecha_fin': date.today()})
    if request.method == 'POST':
        if request.POST.get('eliminar'):
            v = get_object_or_404(Vacacion, pk=request.POST['eliminar'])
            if FilaPlanilla.objects.filter(trabajador=v.trabajador, planilla__estado__in=('CERRADA', 'PAGADA'),
                                           planilla__periodo=v.fecha_inicio.strftime('%Y%m')).exists():
                messages.error(request, 'Esas vacaciones ya están en una planilla cerrada.')
            else:
                v.delete()
                messages.success(request, 'Registro de vacaciones eliminado.')
            return redirect(request.get_full_path())
        if form.is_valid():
            v = form.save()
            messages.success(request, f'{v.get_tipo_display()} registrado ({v.dias} días). Se toma en la planilla '
                                      f'del mes al "Actualizar trabajadores" (o ingrese los días en la fila).')
            return redirect(request.get_full_path())
    q = request.GET.get('q', '').strip()
    hoy = date.today()
    qs = Trabajador.objects.filter(Q(fecha_cese__isnull=True) | Q(fecha_cese__gte=hoy)).exclude(
        fecha_ingreso__isnull=True).prefetch_related('vacaciones')
    if q:
        qs = qs.filter(Q(numero_doc__startswith=q) | Q(apellido_paterno__icontains=q) | Q(nombres__icontains=q))
    filas = []
    for t in qs:
        anios, trunco = calculo.record_vacacional(t, hoy)
        filas.append({'t': t, 'anios': anios, 'trunco': trunco,
                      'pendientes': sum(a['pendientes'] for a in anios),
                      'vencidos': sum(a['pendientes'] for a in anios if a['vencido']),
                      'gozados': sum(a['gozados'] for a in anios), 'vendidos': sum(a['vendidos'] for a in anios)})
    if request.GET.get('formato') == 'excel':
        return excel_response('Record_vacacional', 'RÉCORD VACACIONAL', [
            'Documento', 'Trabajador', 'Ingreso', 'Años cumplidos', 'Días gozados', 'Días vendidos', 'Pendientes',
            'Vencidos'], [[f['t'].numero_doc, f['t'].nombre_completo, f['t'].fecha_ingreso, len(f['anios']),
                           f['gozados'], f['vendidos'], f['pendientes'], f['vencidos']] for f in filas])
    detalle = None
    if request.GET.get('trabajador'):
        detalle = next((f for f in filas if str(f['t'].pk) == request.GET['trabajador']), None)
    return render(request, 'planillas/vacaciones.html', {
        'form': form, 'filas': filas, 'q': q, 'detalle': detalle,
        'registros': Vacacion.objects.select_related('trabajador')[:30]})


# ---------------------------------------------------------------- costo por centro de costo
@login_required
def costo_centros(request):
    """Costo de la planilla (ingresos + aportes del empleador) por centro de costo y mes."""
    from collections import defaultdict
    from datetime import date
    anio = int(request.GET.get('anio') or date.today().year)
    filas = (FilaPlanilla.objects.filter(planilla__periodo__startswith=str(anio),
                                         planilla__estado__in=('CALCULADA', 'CERRADA', 'PAGADA'))
             .select_related('trabajador__centro_costo', 'planilla'))
    tabla = defaultdict(lambda: [0] * 13)
    for f in filas:
        centro = str(f.trabajador.centro_costo or 'Sin centro de costo')
        costo = f.total_ingresos + f.total_aportes
        tabla[centro][int(f.planilla.periodo[4:]) - 1] += costo
        tabla[centro][12] += costo
    datos = sorted(tabla.items())
    totales = [sum(v[i] for _, v in datos) for i in range(13)]
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Costo_planilla_{anio}', f'COSTO DE PLANILLA POR CENTRO DE COSTO {anio}',
                              ['Centro de costo'] + [f'{m:02d}' for m in range(1, 13)] + ['Total'],
                              [[c] + v for c, v in datos] + [['TOTAL'] + totales])
    return render(request, 'planillas/costo_centros.html', {'anio': anio, 'datos': datos, 'totales': totales,
                                                             'meses': range(1, 13)})


# ---------------------------------------------------------------- configuración
@login_required
def configuracion(request):
    parametros = ParametrosFormSet(request.POST or None, prefix='par')
    afps = AFPFormSet(request.POST or None, prefix='afp')
    conceptos = ConceptosFormSet(request.POST or None, prefix='con')
    if request.method == 'POST':
        if parametros.is_valid() and afps.is_valid() and conceptos.is_valid():
            parametros.save()
            afps.save()
            conceptos.save()
            messages.success(request, 'Configuración de planillas guardada.')
            return redirect('planillas:configuracion')
        messages.error(request, 'Revise los datos marcados.')
    return render(request, 'planillas/configuracion.html', {'parametros': parametros, 'afps': afps,
                                                            'conceptos': conceptos})
