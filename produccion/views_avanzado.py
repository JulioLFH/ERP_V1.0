"""Programación de planta, reporte de planta desde tablets, cambios de ingeniería y costeo por actividades."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.utils import excel_response

from . import abc, avanzado
from .forms_planta import ActividadForm, CambioForm, RecursosFormSet
from .models import ActividadABC, CambioIngenieria, CentroTrabajo, OrdenProduccion
from .views_costos import con_costos


def _dec(valor):
    try:
        return Decimal(str(valor).replace(',', '').strip()) if str(valor or '').strip() else None
    except InvalidOperation:
        return None


# ---------------------------------------------------------------- programación detallada
@login_required
def programacion(request):
    if request.method == 'POST' and request.POST.get('accion') == 'prioridad':
        try:
            prioridad = max(1, min(9, int(request.POST.get('prioridad') or 5)))
        except ValueError:
            prioridad = 5
        OrdenProduccion.objects.filter(pk=request.POST.get('orden'), estado__in=['CONFIRMADA', 'EN_PROCESO']) \
            .update(prioridad=prioridad)
        return redirect('manufactura:programacion')
    hoy = timezone.localdate()
    ordenes, por_centro = avanzado.programar(hoy)
    filas, fechas = avanzado.diagrama(por_centro, hoy, 21)
    return render(request, 'produccion/programacion.html', {
        'ordenes': ordenes, 'gantt': filas, 'fechas': fechas, 'hoy': hoy,
        'atrasadas': sum(1 for o in ordenes if o['atrasada']),
        'sin_capacidad': sum(1 for o in ordenes if o['sin_capacidad'])})


# ---------------------------------------------------------------- reporte de planta (tablets)
@login_required
def planta(request):
    qs = (OrdenProduccion.objects.filter(estado__in=['CONFIRMADA', 'EN_PROCESO'])
          .select_related('producto').annotate(n_avances=Count('avances')).order_by('prioridad', 'fecha', 'id'))
    centro = request.GET.get('centro')
    if centro:
        qs = qs.filter(horas__centro_id=centro).distinct()
    return render(request, 'produccion/planta.html', {
        'ordenes': qs, 'centros': CentroTrabajo.objects.filter(activo=True), 'centro': centro})


@login_required
def planta_orden(request, pk):
    orden = get_object_or_404(OrdenProduccion.objects.select_related('producto'), pk=pk)
    operaciones = list(orden.horas.select_related('centro').order_by('secuencia', 'id'))
    if request.method == 'POST':
        hora = next((h for h in operaciones if str(h.pk) == request.POST.get('hora')), None)
        try:
            avanzado.registrar_avance(orden, hora, _dec(request.POST.get('buena')), _dec(request.POST.get('merma')),
                                      _dec(request.POST.get('horas')), request.POST.get('operario', ''),
                                      request.POST.get('nota', ''), request.user)
            messages.success(request, 'Avance registrado.')
        except avanzado.ErrorAvanzado as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:planta_orden', pk)
    resumen = avanzado.resumen_avance(orden)
    reportado = {}
    for a in (resumen['avances'] if resumen else []):
        if a.hora_id:
            r = reportado.setdefault(a.hora_id, {'buena': Decimal('0'), 'horas': Decimal('0')})
            r['buena'] += a.cantidad_buena
            r['horas'] += a.horas
    return render(request, 'produccion/planta_orden.html', {
        'o': orden, 'operaciones': [(h, reportado.get(h.pk)) for h in operaciones], 'resumen': resumen,
        'activa': orden.estado in ('CONFIRMADA', 'EN_PROCESO'),
        'hora_sel': request.GET.get('hora') or (str(operaciones[0].pk) if operaciones else '')})


# ---------------------------------------------------------------- cambios de ingeniería
@login_required
def cambios(request):
    qs = CambioIngenieria.objects.select_related('lista_actual__producto', 'lista_nueva', 'solicitado_por',
                                                 'aprobado_por')
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado=estado)
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(numero__icontains=q) | Q(lista_actual__producto__nombre__icontains=q))
    return render(request, 'produccion/cambios.html', {
        'cambios': qs[:300], 'estados': CambioIngenieria.ESTADOS, 'estado': estado,
        'por_aprobar': CambioIngenieria.objects.filter(estado='POR_APROBAR').count()})


@login_required
def cambio_nuevo(request):
    form = CambioForm(request.POST or None, initial={'lista_actual': request.GET.get('lista')})
    if request.method == 'POST' and form.is_valid():
        try:
            cambio = avanzado.crear_cambio(form.save(commit=False), request.user)
            messages.success(request, f'{cambio} creado: edite la receta propuesta y envíela a aprobación.')
            return redirect('manufactura:cambio', cambio.pk)
        except avanzado.ErrorAvanzado as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nuevo cambio de ingeniería'})


@login_required
def cambio(request, pk):
    c = get_object_or_404(CambioIngenieria.objects.select_related(
        'lista_actual__producto', 'lista_nueva', 'solicitado_por', 'aprobado_por'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'enviar':
                avanzado.enviar(c)
                messages.success(request, f'{c} enviado a aprobación.')
            elif accion == 'aprobar':
                avanzado.aprobar(c, request.user, request.POST.get('comentario', ''))
                messages.success(request, f'{c} aprobado: la receta {c.lista_nueva.codigo} rige desde el '
                                          f'{c.fecha_efectiva:%d/%m/%Y}.')
            elif accion == 'rechazar':
                avanzado.rechazar(c, request.user, request.POST.get('comentario', ''))
                messages.success(request, f'{c} rechazado.')
        except avanzado.ErrorAvanzado as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:cambio', pk)
    filas, (base_antes, base_despues) = avanzado.diferencias(c)
    return render(request, 'produccion/cambio.html', {
        'c': c, 'filas': filas, 'base_antes': base_antes, 'base_despues': base_despues,
        'puede_aprobar': c.estado == 'POR_APROBAR' and (request.user.pk != c.solicitado_por_id
                                                         or not avanzado._hay_otro_usuario())})


# ---------------------------------------------------------------- costeo por actividades (ABC)
def _periodo(request):
    return (request.GET.get('periodo') or timezone.localdate().strftime('%Y-%m')).replace('-', '')[:6]


@con_costos
def costeo_abc(request):
    periodo = _periodo(request)
    datos = abc.costeo(periodo)
    if request.GET.get('formato') == 'excel':
        acts = datos['actividades']
        filas = [[p['p'].codigo, p['p'].nombre, p['unidades']] + p['por_actividad'] +
                 [p['total'], p['unitario'] or '', p['tradicional'], p['tradicional_unit'] or ''] for p in datos['productos']]
        return excel_response(f'Costeo_ABC_{periodo}', f'Costeo por actividades {periodo[4:]}/{periodo[:4]}',
                              ['Código', 'Producto', 'Unidades'] + [a['a'].nombre for a in acts] +
                              ['Total ABC S/', 'ABC por unidad', 'MO + CIF absorbidos S/', 'Absorbido por unidad'],
                              filas)
    return render(request, 'produccion/costeo_abc.html', {
        **datos, 'periodo': periodo, 'periodo_input': f'{periodo[:4]}-{periodo[4:]}'})


@con_costos
def actividades(request):
    return render(request, 'produccion/actividades.html', {
        'actividades': ActividadABC.objects.prefetch_related('recursos__centro_costo')})


@con_costos
def actividad(request, pk=None):
    act = get_object_or_404(ActividadABC, pk=pk) if pk else ActividadABC()
    form = ActividadForm(request.POST or None, instance=act)
    recursos = RecursosFormSet(request.POST or None, instance=act, prefix='rec')
    if request.method == 'POST' and form.is_valid() and recursos.is_valid():
        act = form.save()
        recursos.instance = act
        recursos.save()
        messages.success(request, f'Actividad {act} guardada.')
        return redirect('costos:actividades')
    return render(request, 'produccion/actividad.html', {'form': form, 'recursos': recursos, 'act': act})
