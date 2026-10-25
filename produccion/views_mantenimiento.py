"""Pantallas del mantenimiento ampliado (v1.84, MNT-02 a MNT-08): avisos del operador, lecturas de condición, hoja de
tareas y materiales del plan, ubicaciones técnicas y códigos de falla, repuestos críticos, tablero de indicadores,
calendario semanal con ventanas de parada, órdenes del técnico (celular) e historial por equipo."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.forms import BootstrapMixin, remoto
from ventas.crm_tabla import responder

from . import mantenimiento as mnt
from .models import (AvisoMantenimiento, CodigoFalla, Equipo, LecturaCondicion, MaterialPlan, OrdenMantenimiento,
                     PlanMantenimiento, RepuestoCritico, TareaPlan, UbicacionTecnica, VentanaParada)
from .planta import ErrorPlanta

D0 = Decimal('0')


def _equipos(campo):
    campo.queryset = Equipo.objects.filter(activo=True)


class AvisoForm(BootstrapMixin, forms.ModelForm):
    foto = forms.ImageField(label='Foto (opcional)', required=False)

    class Meta:
        model = AvisoMantenimiento
        fields = ['equipo', 'sintoma', 'prioridad', 'equipo_parado']
        widgets = {'sintoma': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _equipos(self.fields['equipo'])
        self.fields['foto'].widget.attrs.update({'accept': 'image/*', 'capture': 'environment'})


class LecturaForm(BootstrapMixin, forms.Form):
    equipo = forms.ModelChoiceField(queryset=Equipo.objects.none())
    variable = forms.CharField(max_length=40, help_text='La misma del plan por condición (ej. Temperatura)')
    valor = forms.DecimalField(max_digits=12, decimal_places=3)
    unidad = forms.CharField(max_length=15, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _equipos(self.fields['equipo'])


class UbicacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = UbicacionTecnica
        fields = ['codigo', 'nombre', 'padre']


class CodigoFallaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CodigoFalla
        fields = ['tipo', 'codigo', 'nombre', 'activo']


class RepuestoCriticoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = RepuestoCritico
        fields = ['equipo', 'producto', 'stock_minimo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Producto
        _equipos(self.fields['equipo'])
        self.fields['producto'].queryset = Producto.objects.filter(tipo='BIEN', es_plantilla=False)
        remoto(self.fields['producto'], 'productos_bienes')


class VentanaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = VentanaParada
        fields = ['centro', 'equipo', 'inicio', 'fin', 'motivo']
        widgets = {'inicio': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
                   'fin': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')}

    def clean(self):
        d = super().clean()
        if d.get('inicio') and d.get('fin') and d['fin'] <= d['inicio']:
            self.add_error('fin', 'Debe ser posterior al inicio.')
        if not d.get('centro') and not d.get('equipo'):
            raise forms.ValidationError('Indique la línea o el equipo.')
        return d


class TareaPlanForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TareaPlan
        fields = ['secuencia', 'descripcion', 'minutos', 'herramientas']


class MaterialPlanForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MaterialPlan
        fields = ['producto', 'cantidad']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Producto
        self.fields['producto'].queryset = Producto.objects.filter(tipo='BIEN', es_plantilla=False)
        remoto(self.fields['producto'], 'productos_bienes')

    def clean_cantidad(self):
        c = self.cleaned_data['cantidad']
        if c <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return c


def _guardar(request, form):
    if request.method == 'POST':
        if form.is_valid():
            return form.save()
        messages.error(request, 'Revise el formulario.')
    return None


def _mostrar(request, form):
    return form if request.method == 'POST' or request.GET.get('nuevo') else None


# ---------------------------------------------------------------- avisos (MNT-03)
@login_required
def avisos(request):
    if request.method == 'POST' and request.POST.get('aviso'):
        a = get_object_or_404(AvisoMantenimiento, pk=request.POST['aviso'])
        try:
            if request.POST.get('accion') == 'convertir':
                ot = mnt.convertir_aviso(a, request.user)
                messages.success(request, f'{a} convertido en la {ot}.')
                return redirect('manufactura:orden_mant', ot.pk)
            mnt.rechazar_aviso(a, request.POST.get('motivo') or 'Rechazado por mantenimiento')
            messages.success(request, f'{a} rechazado.')
        except ErrorPlanta as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:avisos_mant')
    from core.permisos import puede
    estado = request.GET.get('estado', 'PENDIENTE')
    qs = AvisoMantenimiento.objects.select_related('equipo', 'reportado_por', 'orden')
    if estado:
        qs = qs.filter(estado=estado)
    aprueba = puede(request.user, 'mantenimiento.avisos')
    filas = []
    for a in qs[:300]:
        pendiente = ([{'texto': 'Aprobar → OT', 'post': '', 'campo': 'aviso', 'valor': a.pk,
                       'extra': {'accion': 'convertir'}},
                      {'texto': 'Rechazar', 'post': '', 'campo': 'aviso', 'valor': a.pk, 'extra': {'accion': 'rechazar'},
                       'confirmar': f'¿Rechazar el {a}?'}] if aprueba else
                     {'texto': 'Lo aprueba quien tiene «Aprobar o rechazar avisos de mantenimiento»',
                      'clase': 'text-muted small'})
        acciones = (pendiente if a.estado == 'PENDIENTE' else
                    ({'texto': str(a.orden), 'url': reverse('manufactura:orden_mant', args=[a.orden_id])}
                     if a.orden_id else a.motivo_rechazo))
        filas.append([a.numero, timezone.localtime(a.creado).strftime('%d/%m %H:%M'), str(a.equipo),
                      a.sintoma[:80], {'texto': a.get_prioridad_display(),
                                       'clase': 'text-danger fw-semibold' if a.prioridad == 'EMERGENCIA' else ''},
                      'Sí' if a.equipo_parado else '', str(a.reportado_por or ''), a.get_estado_display(), acciones])
    return responder(request, 'Avisos de mantenimiento', [{'columnas': [
        'N°', 'Fecha', 'Equipo', 'Síntoma', 'Prioridad', 'Parado', 'Reportó', 'Estado', ''], 'filas': filas}],
        selects=[('estado', 'Estado', [('', 'Todos')] + AvisoMantenimiento.ESTADOS, estado)],
        botones=[('Nuevo aviso', reverse('manufactura:aviso_nuevo'), 'bi-megaphone')],
        ayuda='El operador avisa la falla desde su celular (con foto); mantenimiento lo aprueba y se convierte en '
              'orden con su prioridad.')


@login_required
def aviso_nuevo(request):
    form = AvisoForm(request.POST or None, request.FILES or None, initial={'equipo': request.GET.get('equipo')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            a = mnt.crear_aviso(d['equipo'], d['sintoma'], d['prioridad'], d['equipo_parado'], request.user,
                                d.get('foto'))
            messages.success(request, f'{a} registrado: mantenimiento lo revisará.')
            return redirect('manufactura:avisos_mant')
        except ErrorPlanta as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Avisar una falla',
                                              'ayuda': 'Describa qué pasa, marque si el equipo está parado y '
                                                       'tome una foto.'})


# ---------------------------------------------------------------- lecturas y planes (MNT-02)
@login_required
def lecturas(request):
    form = LecturaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            l = mnt.registrar_lectura(d['equipo'], d['variable'], d['valor'], request.user, d.get('unidad', ''))
            if l.orden_id:
                messages.warning(request, f'Lectura fuera del umbral: se generó la {l.orden}.')
            else:
                messages.success(request, 'Lectura registrada' + (' (fuera de rango).' if l.fuera_de_rango else '.'))
            return redirect('manufactura:lecturas_mant')
        except (ErrorPlanta, InvalidOperation) as exc:
            messages.error(request, str(exc))
    filas = [[timezone.localtime(l.fecha).strftime('%d/%m/%Y %H:%M'), str(l.equipo), l.variable,
              f'{l.valor:g} {l.unidad}', {'texto': 'Fuera de rango', 'clase': 'text-danger'} if l.fuera_de_rango else '',
              {'texto': str(l.orden), 'url': reverse('manufactura:orden_mant', args=[l.orden_id])} if l.orden_id else '']
             for l in LecturaCondicion.objects.select_related('equipo', 'orden')[:300]]
    return responder(request, 'Lecturas de condición', [{'columnas': [
        'Fecha', 'Equipo', 'Variable', 'Valor', '', 'Orden generada'], 'filas': filas}], form=form,
        form_titulo='Registrar lectura (temperatura, vibración, presión…)',
        ayuda='Si el equipo tiene un plan por condición con esa variable y la lectura sale del umbral, se genera la '
              'orden predictiva con su hoja de tareas.')


@login_required
def plan_mant(request, pk):
    plan = get_object_or_404(PlanMantenimiento.objects.select_related('equipo'), pk=pk)
    tipo = 'material' if request.GET.get('tipo') == 'material' or request.POST.get('tipo') == 'material' else 'tarea'
    form = (MaterialPlanForm if tipo == 'material' else TareaPlanForm)(request.POST or None)
    if request.method == 'POST' and request.POST.get('quitar'):
        (plan.materiales if tipo == 'material' else plan.tareas).filter(pk=request.POST['quitar']).delete()
        return redirect(f'{reverse("manufactura:plan_mant", args=[pk])}?tipo={tipo}')
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.plan = plan
        obj.save()
        messages.success(request, 'Agregado al plan.')
        return redirect(f'{reverse("manufactura:plan_mant", args=[pk])}?tipo={tipo}')
    tareas = [[t.secuencia, t.descripcion, t.minutos, t.herramientas,
               {'texto': 'Quitar', 'post': '', 'campo': 'quitar', 'valor': t.pk, 'extra': {'tipo': 'tarea'}}]
              for t in plan.tareas.all()]
    materiales = [[m.producto.codigo, m.producto.nombre, m.cantidad,
                   {'texto': 'Quitar', 'post': '', 'campo': 'quitar', 'valor': m.pk, 'extra': {'tipo': 'material'}}]
                  for m in plan.materiales.select_related('producto')]
    return responder(request, f'Plan: {plan}', [
        {'titulo': 'Hoja de tareas (se copia como checklist a la orden)', 'columnas': [
            'Sec.', 'Tarea', 'Minutos', 'Herramientas', ''], 'filas': tareas},
        {'titulo': 'Materiales y repuestos (se reservan en la orden)', 'columnas': ['Código', 'Producto', 'Cantidad', ''],
         'filas': materiales}], por_seccion=True, form=form,
        form_titulo='Agregar material' if tipo == 'material' else 'Agregar tarea',
        botones=[('Agregar tarea', '?tipo=tarea', 'bi-list-check'), ('Agregar material', '?tipo=material', 'bi-box'),
                 ('Equipo', reverse('manufactura:equipo', args=[plan.equipo_id]), 'bi-arrow-left')],
        ayuda=f'Frecuencia: {plan.frecuencia_dias or "—"} días · {plan.frecuencia_uso or "—"} '
              f'{plan.equipo.unidad_medidor} · condición: {plan.variable or "—"} '
              f'({plan.umbral_min if plan.umbral_min is not None else "—"} a '
              f'{plan.umbral_max if plan.umbral_max is not None else "—"}) · anticipación {plan.anticipacion_dias} días.')


# ---------------------------------------------------------------- catálogos (MNT-05)
@login_required
def ubicaciones(request):
    form = UbicacionForm(request.POST or None)
    if _guardar(request, form):
        messages.success(request, 'Ubicación técnica registrada.')
        return redirect('manufactura:ubicaciones_mant')
    filas = [[u.codigo, u.nombre, str(u.padre or ''), u.equipos.count()]
             for u in UbicacionTecnica.objects.select_related('padre')]
    return responder(request, 'Ubicaciones técnicas', [{'columnas': ['Código', 'Nombre', 'Dentro de', 'Equipos'],
                                                        'filas': filas}], form=_mostrar(request, form),
                     form_titulo='Nueva ubicación técnica', botones=[('Nueva ubicación', '?nuevo=1', 'bi-plus-lg')],
                     ayuda='Árbol planta › área › línea. Asigne la ubicación en la ficha del equipo.')


@login_required
def codigos_falla(request):
    form = CodigoFallaForm(request.POST or None)
    if _guardar(request, form):
        messages.success(request, 'Código de falla registrado.')
        return redirect('manufactura:codigos_falla')
    filas = [[c.get_tipo_display(), c.codigo, c.nombre, 'Sí' if c.activo else 'No'] for c in CodigoFalla.objects.all()]
    return responder(request, 'Códigos de falla', [{'columnas': ['Tipo', 'Código', 'Nombre', 'Activo'],
                                                    'filas': filas}], form=_mostrar(request, form),
                     form_titulo='Nuevo código', botones=[('Nuevo código', '?nuevo=1', 'bi-plus-lg')],
                     ayuda='Con el catálogo cargado, cerrar un correctivo exige síntoma, modo, causa y acción (base del '
                           'Pareto de fallas).')


@login_required
def repuestos_criticos(request):
    form = RepuestoCriticoForm(request.POST or None)
    if _guardar(request, form):
        messages.success(request, 'Repuesto crítico registrado.')
        return redirect('manufactura:repuestos_criticos')
    bajos = {b['critico'].pk: b for b in mnt.repuestos_criticos_bajos()}
    filas = []
    for r in RepuestoCritico.objects.select_related('equipo', 'producto'):
        b = bajos.get(r.pk)
        reservado = mnt.reservado(r.producto)
        filas.append([str(r.equipo), f'{r.producto.codigo} {r.producto.nombre}', r.producto.stock, reservado,
                      r.producto.stock - reservado, r.stock_minimo,
                      {'texto': 'Bajo el mínimo', 'clase': 'text-danger fw-semibold'} if b else 'OK'])
    return responder(request, 'Repuestos críticos', [{'columnas': [
        'Equipo', 'Repuesto', 'Stock', 'Reservado en OT', 'Disponible', 'Mínimo', 'Estado'], 'filas': filas}],
        form=_mostrar(request, form), form_titulo='Nuevo repuesto crítico',
        botones=[('Nuevo repuesto crítico', '?nuevo=1', 'bi-plus-lg')],
        ayuda='Lo reservado son los repuestos de las órdenes abiertas. Si el disponible baja del mínimo, compre '
              '(Requerimientos › Solicitud de pedido).')


# ---------------------------------------------------------------- tablero (MNT-06)
def _fecha(texto, defecto):
    try:
        return date.fromisoformat(texto or '')
    except ValueError:
        return defecto


@login_required
def tablero_mant(request):
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde'), hoy.replace(day=1) - timedelta(days=150))
    hasta = _fecha(request.GET.get('hasta'), hoy)
    ind = mnt.indicadores(desde, hasta)
    filas = [[{'texto': str(f['equipo']), 'url': reverse('manufactura:historial_equipo', args=[f['equipo'].pk])},
              f['equipo'].get_criticidad_display() if f['equipo'].criticidad else '', f['ordenes'], f['fallas'],
              f['mtbf'] if f['mtbf'] is not None else '', f['mttr'] if f['mttr'] is not None else '',
              f'{f["disponibilidad"]} %' if f['disponibilidad'] is not None else '', f['horas_parada'], f['costo'],
              f'{f["pct_preventivo"]} %' if f['pct_preventivo'] is not None else '', f['costo_anio'],
              {'texto': f'{f["pct_reposicion"]} %', 'clase': 'text-danger fw-semibold' if f['pct_reposicion'] >= 50
               else ''} if f['pct_reposicion'] is not None else '',
              {'texto': f'{f["pct_presupuesto"]} %', 'clase': 'text-danger' if f['pct_presupuesto'] > 100 else ''}
              if f['pct_presupuesto'] is not None else ''] for f in ind['filas'] if f['ordenes'] or f['costo_anio']]
    pareto = [[p['modo'], p['fallas'], f'{p["pct"]} %', f'{p["acumulado"]} %'] for p in ind['pareto']]
    alertas = mnt.alertas()
    avisos = ([[f'Garantía de {e} vence el {e.garantia_hasta:%d/%m/%Y}'] for e in alertas['garantias']] +
              [[f'Repuesto crítico {b["critico"].producto.nombre} ({b["critico"].equipo.codigo}): disponible '
                f'{b["disponible"]:g}, mínimo {b["critico"].stock_minimo:g}'] for b in alertas['repuestos']] +
              [[{'texto': f'{o} ({o.equipo.codigo}): valores imposibles (horas o costos negativos o fin antes del '
                          f'inicio); no entran en los indicadores: corríjala',
                 'url': reverse('manufactura:orden_mant', args=[o.pk]), 'clase': 'text-danger'}]
               for o in alertas['imposibles']])
    resumen = (f'Cumplimiento del preventivo: {ind["cumplimiento"] if ind["cumplimiento"] is not None else "—"} % '
               f'({ind["a_tiempo"]} de {ind["programadas"]} a tiempo) · backlog {ind["backlog"]:g} h en '
               f'{ind["abiertas"]} órdenes abiertas · fallas {ind["fallas"]} · costo S/ {ind["costo"]:,.2f} · '
               f'preventivo {ind["pct_preventivo"] if ind["pct_preventivo"] is not None else "—"} % del costo.')
    return responder(request, 'Tablero de mantenimiento', [
        {'titulo': 'Indicadores por equipo', 'columnas': [
            'Equipo', 'Criticidad', 'Órdenes', 'Fallas', 'MTBF (h)', 'MTTR (h)', 'Disponibilidad', 'Horas de parada',
            'Costo del periodo', '% preventivo', 'Costo del año', '% del valor de reposición', '% del presupuesto'],
         'filas': filas},
        {'titulo': 'Pareto de modos de falla', 'columnas': ['Modo de falla', 'Fallas', '%', '% acumulado'],
         'filas': pareto},
        {'titulo': 'Alertas', 'columnas': ['Alerta'], 'filas': avisos}], por_seccion=True, rango=(desde, hasta),
        botones=[('Calendario semanal', reverse('manufactura:calendario_mant'), 'bi-calendar-week'),
                 ('Repuestos críticos', reverse('manufactura:repuestos_criticos'), 'bi-exclamation-diamond')],
        ayuda=resumen + ' MTBF = horas operativas / fallas; MTTR = horas de reparación promedio; disponibilidad = '
                        'horas sin parada / horas del periodo. En rojo: costo del año ≥ 50 % del valor de reposición '
                        '(evaluar reemplazo) o sobre el presupuesto.')


@login_required
def historial_equipo(request, pk):
    e = get_object_or_404(Equipo, pk=pk)
    filas = [[{'texto': o.numero, 'url': reverse('manufactura:orden_mant', args=[o.pk])}, o.get_tipo_display(),
              o.fecha_programada, o.fecha_fin or '', o.get_estado_display(), o.descripcion[:60],
              str(o.falla_modo or ''), str(o.falla_causa or ''), o.horas_parada, o.costo_total]
             for o in e.ordenes.select_related('falla_modo', 'falla_causa', 'consumo').prefetch_related(
                 'consumo__items')]
    return responder(request, f'Historial técnico · {e}', [{'columnas': [
        'OT', 'Tipo', 'Programada', 'Cerrada', 'Estado', 'Descripción', 'Modo de falla', 'Causa', 'Parada (h)',
        'Costo S/'], 'filas': filas}],
        botones=[('Equipo', reverse('manufactura:equipo', args=[pk]), 'bi-arrow-left')],
        ayuda=f'Criticidad {e.get_criticidad_display() or "—"} · ubicación {e.ubicacion_tecnica or "—"} · garantía '
              f'{e.garantia_hasta.strftime("%d/%m/%Y") if e.garantia_hasta else "—"} · valor de reposición S/ '
              f'{e.valor_reposicion:,.2f}.')


# ---------------------------------------------------------------- programación y campo (MNT-07)
@login_required
def calendario_mant(request):
    hoy = timezone.localdate()
    lunes = _fecha(request.GET.get('semana'), hoy)
    lunes -= timedelta(days=lunes.weekday())
    form = VentanaForm(request.POST or None)
    if _guardar(request, form):
        messages.success(request, 'Ventana de parada registrada.')
        return redirect(f'{reverse("manufactura:calendario_mant")}?semana={lunes:%Y-%m-%d}')
    return render(request, 'produccion/calendario_mant.html', {
        'c': mnt.calendario(lunes), 'lunes': lunes, 'anterior': lunes - timedelta(days=7),
        'siguiente': lunes + timedelta(days=7), 'form': form, 'capacidad': mnt.HORAS_DIA})


@login_required
def mis_ordenes_mant(request):
    ordenes = mnt.ordenes_del_tecnico(request.user)
    return render(request, 'produccion/mis_ordenes_mant.html', {
        'ordenes': ordenes, 'trabajador': getattr(request.user, 'trabajador_planilla', None)})
