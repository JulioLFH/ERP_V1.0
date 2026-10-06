"""Pantallas de calidad y mantenimiento de planta (módulo Manufactura)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Producto

from . import planta
from .forms_planta import (CierreMantenimientoForm, EquipoForm, InspeccionForm, OrdenMantenimientoForm,
                           ParametrosFormSet, PlanForm, RepuestoForm)
from .models import Equipo, InspeccionCalidad, OrdenMantenimiento, PlanMantenimiento


def _dec(valor):
    try:
        return Decimal(str(valor).replace(',', '').strip()) if str(valor or '').strip() else None
    except InvalidOperation:
        return None


# ---------------------------------------------------------------- calidad
@login_required
def inspecciones(request):
    qs = InspeccionCalidad.objects.select_related('producto', 'almacen')
    for campo in ('tipo', 'resultado'):
        if request.GET.get(campo):
            qs = qs.filter(**{campo: request.GET[campo]})
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(numero__icontains=q) | Q(producto__nombre__icontains=q) | Q(lote__icontains=q))
    resumen = dict(InspeccionCalidad.objects.values_list('resultado').annotate(n=Count('id')))
    return render(request, 'produccion/inspecciones.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'tipos': InspeccionCalidad.TIPOS,
        'resultados': InspeccionCalidad.RESULTADOS,
        'resumen': [(n, resumen.get(v, 0)) for v, n in InspeccionCalidad.RESULTADOS]})


@login_required
def inspeccion_nueva(request):
    inicial = {k: request.GET[k] for k in ('producto', 'lote', 'cantidad', 'almacen', 'tipo') if request.GET.get(k)}
    form = InspeccionForm(request.POST or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        insp = form.save(commit=False)
        if request.GET.get('operacion'):
            insp.operacion_id = request.GET['operacion']
        if request.GET.get('orden'):
            insp.orden_id = request.GET['orden']
        planta.crear_inspeccion(insp, request.user)
        messages.success(request, f'{insp} creada con {insp.resultados.count()} característica(s) del plan de calidad.')
        return redirect('manufactura:inspeccion', insp.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva inspección de calidad'})


@login_required
def inspeccion(request, pk):
    insp = get_object_or_404(InspeccionCalidad.objects.select_related('producto', 'almacen', 'operacion', 'orden'),
                             pk=pk)
    if request.method == 'POST':
        try:
            if request.POST.get('accion') == 'cuarentena':
                op = planta.enviar_a_cuarentena(insp, _dec(request.POST.get('cantidad')), request.user)
                messages.success(request, f'Mercadería trasladada a cuarentena ({op}).')
            else:
                valores = {}
                for r in insp.resultados.all():
                    conforme = request.POST.get(f'conforme_{r.pk}')
                    valores[r.pk] = (_dec(request.POST.get(f'valor_{r.pk}')), request.POST.get(f'texto_{r.pk}', '')[:120],
                                     None if conforme in (None, '') else conforme == '1')
                planta.registrar_resultados(insp, valores, request.POST.get('resultado'),
                                            request.POST.get('observaciones', ''), request.user)
                messages.success(request, f'Resultado registrado: {insp.get_resultado_display()}.')
        except planta.ErrorPlanta as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:inspeccion', pk)
    return render(request, 'produccion/inspeccion.html', {
        'i': insp, 'resultados': insp.resultados.all(), 'opciones': InspeccionCalidad.RESULTADOS})


@login_required
def plan_calidad(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    formset = ParametrosFormSet(request.POST or None, instance=producto, prefix='par')
    if request.method == 'POST' and formset.is_valid():
        formset.save()
        messages.success(request, f'Plan de calidad de {producto.nombre} guardado.')
        return redirect('manufactura:plan_calidad', pk)
    return render(request, 'produccion/plan_calidad.html', {'producto': producto, 'formset': formset})


@login_required
def planes_calidad(request):
    if request.GET.get('producto'):
        return redirect('manufactura:plan_calidad', request.GET['producto'])
    con_plan = (Producto.objects.annotate(n=Count('parametros_calidad')).filter(n__gt=0).order_by('nombre'))
    return render(request, 'produccion/planes_calidad.html', {'productos': con_plan})


# ---------------------------------------------------------------- mantenimiento
@login_required
def equipos(request):
    qs = Equipo.objects.select_related('centro').annotate(
        n_ordenes=Count('ordenes'), parada=Sum('ordenes__horas_parada', filter=Q(ordenes__estado='CERRADA')))
    if request.GET.get('q'):
        qs = qs.filter(Q(codigo__icontains=request.GET['q']) | Q(nombre__icontains=request.GET['q']))
    hoy = timezone.localdate()
    vencidos = [p for p in PlanMantenimiento.objects.filter(activo=True, equipo__activo=True).select_related('equipo')
                if p.proxima <= hoy]
    return render(request, 'produccion/equipos.html', {'equipos': qs, 'vencidos': vencidos})


@login_required
def equipo(request, pk=None):
    eq = get_object_or_404(Equipo, pk=pk) if pk else Equipo()
    form = EquipoForm(request.POST if request.POST.get('accion') == 'equipo' else None, instance=eq)
    plan_form = PlanForm(request.POST if request.POST.get('accion') == 'plan' else None)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'equipo' and form.is_valid():
            eq = form.save()
            messages.success(request, f'Equipo {eq} guardado.')
            return redirect('manufactura:equipo', eq.pk)
        if accion == 'plan' and eq.pk and plan_form.is_valid():
            plan = plan_form.save(commit=False)
            plan.equipo = eq
            plan.save()
            messages.success(request, 'Plan preventivo agregado.')
            return redirect('manufactura:equipo', eq.pk)
        if accion == 'quitar_plan' and eq.pk:
            eq.planes.filter(pk=request.POST.get('plan')).update(activo=False)
            return redirect('manufactura:equipo', eq.pk)
    return render(request, 'produccion/equipo.html', {
        'eq': eq, 'form': form, 'plan_form': plan_form,
        'planes': eq.planes.filter(activo=True) if eq.pk else [],
        'ordenes': eq.ordenes.all()[:30] if eq.pk else []})


@login_required
def ordenes_mantenimiento(request):
    if request.method == 'POST' and request.POST.get('accion') == 'programar':
        creadas = planta.programar_preventivos(int(request.POST.get('dias') or 7), request.user)
        messages.success(request, f'{len(creadas)} orden(es) preventiva(s) programada(s).')
        return redirect('manufactura:ordenes_mant')
    qs = OrdenMantenimiento.objects.select_related('equipo')
    estado = request.GET.get('estado', 'ABIERTAS')
    if estado == 'ABIERTAS':
        qs = qs.filter(estado__in=('PROGRAMADA', 'EN_EJECUCION'))
    elif estado:
        qs = qs.filter(estado=estado)
    if request.GET.get('tipo'):
        qs = qs.filter(tipo=request.GET['tipo'])
    return render(request, 'produccion/ordenes_mant.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'estado': estado,
        'estados': OrdenMantenimiento.ESTADOS})


@login_required
def orden_mant_nueva(request):
    form = OrdenMantenimientoForm(request.POST or None, initial={'equipo': request.GET.get('equipo')})
    if request.method == 'POST' and form.is_valid():
        o = planta.crear_orden(form.save(commit=False), request.user)
        messages.success(request, f'{o} creada.')
        return redirect('manufactura:orden_mant', o.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva orden de mantenimiento'})


@login_required
def orden_mant(request, pk):
    o = get_object_or_404(OrdenMantenimiento.objects.select_related('equipo__centro', 'plan', 'proveedor', 'consumo'),
                          pk=pk)
    abierta = o.estado in ('PROGRAMADA', 'EN_EJECUCION')
    cierre = CierreMantenimientoForm(request.POST if request.POST.get('accion') == 'cerrar' else None, instance=o)
    rep_form = RepuestoForm(request.POST if request.POST.get('accion') == 'repuesto' else None)
    if request.method == 'POST' and abierta:
        accion = request.POST.get('accion')
        try:
            if accion == 'iniciar':
                planta.iniciar(o)
            elif accion == 'repuesto':
                if not rep_form.is_valid():
                    raise planta.ErrorPlanta('Revise el repuesto: ' + '; '.join(
                        f'{k}: {" ".join(v)}' for k, v in rep_form.errors.items()))
                r = rep_form.save(commit=False)
                r.orden = o
                r.save()
            elif accion == 'quitar':
                o.repuestos.filter(pk=request.POST.get('repuesto')).delete()
            elif accion == 'cerrar':
                if not cierre.is_valid():
                    raise planta.ErrorPlanta('Revise los datos del cierre.')
                planta.cerrar(cierre.save(commit=False), request.user)
                messages.success(request, f'{o} cerrada.')
            elif accion == 'anular':
                o.estado = 'ANULADA'
                o.save(update_fields=['estado'])
        except planta.ErrorPlanta as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:orden_mant', pk)
    return render(request, 'produccion/orden_mant.html', {
        'o': o, 'abierta': abierta, 'cierre': cierre, 'rep_form': rep_form,
        'repuestos': o.repuestos.select_related('producto', 'almacen')})
