"""Pantallas de calidad farmacéutica (v1.84, módulo Calidad): investigaciones OOS, desviaciones, control de cambios,
quejas, retiros de mercado, contramuestras, estabilidad, registro de lote, revisión anual de producto, calibraciones
y el paquete de validación del sistema."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.firmas import ip_de
from core.forms import BootstrapMixin, remoto
from core.models import Lote, Producto, Tercero
from core.utils import fmt_fecha
from ventas.crm_tabla import responder

from . import calidad_farma as cf
from .models import (CalibracionEquipo, CalificacionEquipo, Contramuestra, ControlCambio, Desviacion, Equipo,
                     EstudioEstabilidad, InvestigacionOOS, OrdenProduccion, PasoRegistroLote, QuejaLote, RetiroMercado)
from .planta import ErrorPlanta


def _lotes(campo):
    campo.queryset = Lote.objects.exclude(codigo='S/L').select_related('producto').order_by('-creado')
    campo.label_from_instance = lambda l: f'{l.producto.codigo} · {l}'


def _detalle(request, obj, titulo, info, form=None, acciones=(), tablas=(), volver=None, ayuda=''):
    return render(request, 'produccion/gmp_detalle.html', {
        'obj': obj, 'titulo': titulo, 'info': info, 'form': form, 'acciones': acciones, 'tablas': tablas,
        'volver': volver, 'ayuda': ayuda, 'estado': getattr(obj, 'get_estado_display', lambda: '')(),
        'pide_firma': any(a[2] for a in acciones)})


def _guardar(request, form, titulo, destino, antes=None):
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if antes:
            antes(obj)
        obj.save()
        form.save_m2m()
        messages.success(request, f'{obj} registrado.')
        return redirect(destino, obj.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': titulo})


# ---------------------------------------------------------------- OOS (CAL-02)
class OOSForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = InvestigacionOOS
        fields = ['fase', 'hipotesis', 'investigacion_proceso', 'reanalisis', 'causa_raiz', 'conclusion', 'capa']
        widgets = {c: forms.Textarea(attrs={'rows': 2}) for c in ('hipotesis', 'investigacion_proceso', 'causa_raiz',
                                                                   'capa')}


@login_required
def oos_lista(request):
    qs = InvestigacionOOS.objects.select_related('inspeccion__producto', 'resultado')
    if request.GET.get('estado', 'ABIERTA'):
        qs = qs.filter(estado=request.GET.get('estado', 'ABIERTA'))
    filas = [[{'texto': o.numero, 'url': reverse('manufactura:oos', args=[o.pk])}, fmt_fecha(o.creado.date()),
              f'{o.inspeccion.producto.codigo} {o.inspeccion.producto.nombre}', o.inspeccion.lote,
              f'{o.resultado.caracteristica} = {o.valor_original}', o.get_fase_display(), o.get_estado_display(),
              o.get_conclusion_display()] for o in qs]
    return responder(request, 'Investigaciones fuera de especificación (OOS)', [{
        'columnas': ['N°', 'Fecha', 'Producto', 'Lote', 'Resultado', 'Fase', 'Estado', 'Conclusión'], 'filas': filas}],
        selects=[('estado', 'Estado', [('ABIERTA', 'Abiertas'), ('CERRADA', 'Cerradas'), ('', 'Todas')],
                  request.GET.get('estado', 'ABIERTA'))],
        ayuda='Un valor fuera de especificación abre la investigación y la inspección queda «en investigación»: no se '
              'aprueba ni se libera el lote hasta que QA la cierre (fase I laboratorio, fase II proceso).')


@login_required
def oos(request, pk):
    o = get_object_or_404(InvestigacionOOS.objects.select_related('inspeccion__producto', 'resultado'), pk=pk)
    form = OOSForm(request.POST or None, instance=o) if o.estado == 'ABIERTA' else None
    if request.method == 'POST' and form is not None:
        if form.is_valid():
            try:
                if request.POST.get('accion') == 'cerrar':
                    cf.cerrar_oos(InvestigacionOOS.objects.get(pk=o.pk), form.cleaned_data, request.user,
                                  request.POST.get('clave', ''), ip_de(request))
                    messages.success(request, f'{o} cerrada.')
                else:
                    form.save()
                    messages.success(request, 'Investigación guardada.')
            except ErrorPlanta as exc:
                messages.error(request, str(exc))
            return redirect('manufactura:oos', pk)
    r = o.resultado
    rango = ' a '.join(cf._texto(x) for x in (r.minimo, r.maximo) if x is not None) or r.especificacion
    return _detalle(request, o, str(o), [
        ('Inspección', o.inspeccion.numero, reverse('manufactura:inspeccion', args=[o.inspeccion_id])),
        ('Producto · lote', f'{o.inspeccion.producto} · {o.inspeccion.lote or "—"}', None),
        ('Característica', f'{r.caracteristica} ({r.unidad})', None), ('Especificación', rango, None),
        ('Valor original', o.valor_original, None), ('Abierta', fmt_fecha(o.creado.date()), None),
        ('Cerrada por', o.cerrada_por.get_full_name() if o.cerrada_por else '—', None)], form,
        [('guardar', 'Guardar avance', False, 'outline-primary'),
         ('cerrar', 'Cerrar con firma (QA)', True, 'success')] if form else (),
        volver=reverse('manufactura:oos_lista'),
        ayuda='Error de laboratorio demostrado: el reanálisis reemplaza el valor (el original queda en el rastro) y la '
              'inspección se vuelve a decidir. OOS confirmado: el lote queda rechazado.')


# ---------------------------------------------------------------- desviaciones, cambios, quejas, retiros (CAL-07)
class DesviacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Desviacion
        fields = ['fecha', 'criticidad', 'descripcion', 'lote', 'orden', 'inspeccion', 'impacto', 'causa_raiz', 'capa',
                  'estado']
        widgets = {c: forms.Textarea(attrs={'rows': 2}) for c in ('descripcion', 'impacto', 'causa_raiz', 'capa')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lotes(self.fields['lote'])
        self.fields['orden'].queryset = OrdenProduccion.objects.select_related('producto').order_by('-id')
        remoto(self.fields['orden'], 'ordenes_produccion')  # con miles de órdenes la página tardaba más de 20 s
        self.fields['estado'].choices = [c for c in Desviacion.ESTADOS if c[0] != 'CERRADA']


class CambioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ControlCambio
        fields = ['fecha', 'titulo', 'tipo', 'descripcion', 'productos', 'impacto']
        widgets = {c: forms.Textarea(attrs={'rows': 3}) for c in ('descripcion', 'impacto')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['productos'].queryset = Producto.objects.filter(tipo='BIEN', es_plantilla=False)
        self.fields['productos'].widget.attrs['size'] = 6


class QuejaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = QuejaLote
        fields = ['fecha', 'cliente', 'lote', 'clasificacion', 'descripcion', 'investigacion', 'estado']
        widgets = {c: forms.Textarea(attrs={'rows': 3}) for c in ('descripcion', 'investigacion')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lotes(self.fields['lote'])
        self.fields['cliente'].queryset = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')


class RetiroForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = RetiroMercado
        fields = ['fecha', 'tipo', 'lote', 'motivo', 'recuperado']
        widgets = {'motivo': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lotes(self.fields['lote'])


def _lista(request, titulo, qs, columnas, fila, nuevo, ayuda=''):
    filas = [fila(o) for o in qs[:300]]
    return responder(request, titulo, [{'columnas': columnas, 'filas': filas}],
                     botones=[('Nuevo', reverse(nuevo), 'bi-plus-lg')], ayuda=ayuda)


@login_required
def desviaciones(request):
    return _lista(request, 'Desviaciones', Desviacion.objects.select_related('lote', 'orden'),
                  ['N°', 'Fecha', 'Criticidad', 'Descripción', 'Lote / orden', 'Estado'],
                  lambda d: [{'texto': d.numero, 'url': reverse('manufactura:desviacion', args=[d.pk])},
                             fmt_fecha(d.fecha), d.get_criticidad_display(), d.descripcion[:80],
                             d.lote.codigo if d.lote_id else (d.orden.numero if d.orden_id else '—'),
                             d.get_estado_display()],
                  'manufactura:desviacion_nueva',
                  'Una desviación abierta del lote, su orden o su inspección impide liberar el lote.')


@login_required
def desviacion_nueva(request):
    form = DesviacionForm(request.POST or None, initial={'lote': request.GET.get('lote'),
                                                        'orden': request.GET.get('orden')})
    return _guardar(request, form, 'Nueva desviación', 'manufactura:desviacion',
                    lambda o: setattr(o, 'registrado_por', request.user))


@login_required
def desviacion(request, pk):
    d = get_object_or_404(Desviacion, pk=pk)
    form = DesviacionForm(request.POST or None, instance=d) if d.estado != 'CERRADA' else None
    if request.method == 'POST' and form is not None and form.is_valid():
        try:
            form.save()
            if request.POST.get('accion') == 'cerrar':
                cf.cerrar_desviacion(Desviacion.objects.get(pk=pk), request.user, request.POST.get('clave', ''),
                                     ip_de(request))
            messages.success(request, f'{d} guardada.')
        except ErrorPlanta as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:desviacion', pk)
    return _detalle(request, d, str(d), [('Criticidad', d.get_criticidad_display(), None),
                                         ('Cerrada por', d.cerrado_por.get_full_name() if d.cerrado_por else '—', None)],
                    form, [('guardar', 'Guardar', False, 'outline-primary'),
                           ('cerrar', 'Cerrar con firma (QA)', True, 'success')] if form else (),
                    volver=reverse('manufactura:desviaciones'))


@login_required
def cambios(request):
    return _lista(request, 'Control de cambios', ControlCambio.objects.prefetch_related('productos'),
                  ['N°', 'Fecha', 'Cambio', 'Tipo', 'Productos', 'Estado'],
                  lambda c: [{'texto': c.numero, 'url': reverse('manufactura:cambio_cc', args=[c.pk])},
                             fmt_fecha(c.fecha), c.titulo, c.get_tipo_display(),
                             ', '.join(p.codigo for p in c.productos.all()), c.get_estado_display()],
                  'manufactura:cambio_cc_nuevo',
                  'Un cambio propuesto o aprobado (sin implementar) sobre un producto impide liberar sus lotes.')


@login_required
def cambio_nuevo(request):
    form = CambioForm(request.POST or None)
    return _guardar(request, form, 'Nuevo control de cambio', 'manufactura:cambio_cc',
                    lambda o: setattr(o, 'registrado_por', request.user))


@login_required
def cambio(request, pk):
    c = get_object_or_404(ControlCambio, pk=pk)
    form = CambioForm(request.POST or None, instance=c) if c.estado == 'PROPUESTO' else None
    if request.method == 'POST':
        try:
            accion = request.POST.get('accion')
            if form is not None and form.is_valid():
                form.save()
            if accion in ('aprobar', 'rechazar', 'implementar', 'cerrar'):
                cf.resolver_cambio(ControlCambio.objects.get(pk=pk), request.user, accion,
                                   request.POST.get('clave', ''), ip_de(request))
            messages.success(request, f'{c} actualizado.')
        except ErrorPlanta as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:cambio_cc', pk)
    acciones = {'PROPUESTO': [('guardar', 'Guardar', False, 'outline-primary'), ('aprobar', 'Aprobar (QA)', True,
                                                                                 'success'),
                              ('rechazar', 'Rechazar (QA)', True, 'outline-danger')],
                'APROBADO': [('implementar', 'Marcar implementado', False, 'primary')],
                'IMPLEMENTADO': [('cerrar', 'Cerrar: eficacia verificada (QA)', True, 'success')]}.get(c.estado, [])
    return _detalle(request, c, str(c), [
        ('Tipo', c.get_tipo_display(), None), ('Productos', ', '.join(str(p) for p in c.productos.all()) or '—', None),
        ('Descripción', c.descripcion, None), ('Impacto', c.impacto or '—', None),
        ('Aprobado por', c.aprobado_por.get_full_name() if c.aprobado_por else '—', None)], form, acciones,
        volver=reverse('manufactura:cambios_cc'))


@login_required
def quejas(request):
    return _lista(request, 'Quejas de calidad por lote', QuejaLote.objects.select_related('cliente', 'lote__producto'),
                  ['N°', 'Fecha', 'Cliente', 'Producto · lote', 'Clasificación', 'Estado'],
                  lambda q: [{'texto': q.numero, 'url': reverse('manufactura:queja', args=[q.pk])}, fmt_fecha(q.fecha),
                             q.cliente.nombre, f'{q.lote.producto.codigo} · {q.lote.codigo}', q.get_clasificacion_display(),
                             q.get_estado_display()], 'manufactura:queja_nueva')


@login_required
def queja_nueva(request):
    return _guardar(request, QuejaForm(request.POST or None, initial={'lote': request.GET.get('lote')}),
                    'Nueva queja de calidad', 'manufactura:queja')


@login_required
def queja(request, pk):
    q = get_object_or_404(QuejaLote.objects.select_related('cliente', 'lote__producto'), pk=pk)
    form = QuejaForm(request.POST or None, instance=q)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'{q} guardada.')
        return redirect('manufactura:queja', pk)
    return _detalle(request, q, str(q), [
        ('Lote', f'{q.lote.producto} · {q.lote}', reverse('inventario:trazabilidad') + f'?lote={q.lote.codigo}')],
        form, [('guardar', 'Guardar', False, 'primary')], volver=reverse('manufactura:quejas'),
        ayuda='Desde el lote: abra una desviación o un retiro de mercado si la queja lo requiere.')


@login_required
def retiros(request):
    return _lista(request, 'Retiros de mercado y simulacros', RetiroMercado.objects.select_related('lote__producto'),
                  ['N°', 'Fecha', 'Tipo', 'Producto · lote', 'Clientes', 'Efectividad', 'Estado'],
                  lambda r: [{'texto': r.numero, 'url': reverse('manufactura:retiro', args=[r.pk])}, fmt_fecha(r.fecha),
                             r.get_tipo_display(), f'{r.lote.producto.codigo} · {r.lote.codigo}', len(r.clientes),
                             f'{r.efectividad} %' if r.efectividad is not None else '—', r.get_estado_display()],
                  'manufactura:retiro_nuevo',
                  'El retiro arma la lista de clientes que recibieron el lote desde la trazabilidad; uno real bloquea '
                  'el lote.')


@login_required
def retiro_nuevo(request):
    form = RetiroForm(request.POST or None, initial={'lote': request.GET.get('lote')})
    if request.method == 'POST' and form.is_valid():
        r = cf.iniciar_retiro(form.save(commit=False))
        messages.success(request, f'{r}: {len(r.clientes)} cliente(s) recibieron el lote.')
        return redirect('manufactura:retiro', r.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nuevo retiro de mercado (o simulacro)'})


@login_required
def retiro(request, pk):
    r = get_object_or_404(RetiroMercado.objects.select_related('lote__producto'), pk=pk)
    form = RetiroForm(request.POST or None, instance=r) if r.estado == 'ABIERTO' else None
    if request.method == 'POST' and form is not None and form.is_valid():
        form.save()
        if request.POST.get('accion') == 'cerrar':
            from core.permisos import puede
            if not (request.user.is_superuser or puede(request.user, 'control_calidad.aprobar_qa')):
                messages.error(request, 'Cierra el retiro QA (permiso «aprobación de QA»).')
                return redirect('manufactura:retiro', pk)
            RetiroMercado.objects.filter(pk=pk).update(estado='CERRADO')
        messages.success(request, f'{r} guardado.')
        return redirect('manufactura:retiro', pk)
    return _detalle(request, r, str(r), [
        ('Lote', f'{r.lote.producto} · {r.lote}', reverse('inventario:trazabilidad') + f'?lote={r.lote.codigo}'),
        ('Distribuido', str(r.distribuido), None),
        ('Efectividad', f'{r.efectividad} %' if r.efectividad is not None else '—', None)], form,
        [('guardar', 'Guardar', False, 'outline-primary'), ('cerrar', 'Cerrar el retiro', False, 'success')]
        if form else (),
        [{'titulo': 'Clientes que recibieron el lote', 'columnas': ['Cliente', 'RUC / DNI', 'Teléfono', 'Comprobantes',
                                                                   'Cantidad'],
          'filas': [[c['cliente'], c['documento'], c['telefono'], ', '.join(c['comprobantes']), c['cantidad']]
                    for c in r.clientes]}], volver=reverse('manufactura:retiros'))


# ---------------------------------------------------------------- contramuestras y estabilidad (CAL-05)
class ContramuestraForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Contramuestra
        fields = ['lote', 'cantidad', 'unidad', 'ubicacion', 'fecha', 'conservar_hasta', 'estado', 'observacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lotes(self.fields['lote'])


@login_required
def contramuestras(request):
    hoy = timezone.localdate()
    return _lista(request, 'Contramuestras (muestras de retención)', Contramuestra.objects.select_related(
        'lote__producto'), ['Lote', 'Producto', 'Cantidad', 'Ubicación', 'Desde', 'Conservar hasta', 'Estado', ''],
        lambda c: [c.lote.codigo, c.lote.producto.nombre, f'{c.cantidad} {c.unidad}', c.ubicacion, fmt_fecha(c.fecha),
                   {'texto': fmt_fecha(c.conservar_hasta),
                    'clase': 'text-danger' if c.estado == 'RETENIDA' and c.conservar_hasta < hoy else ''},
                   c.get_estado_display(), {'texto': 'Editar', 'url': reverse('manufactura:contramuestra', args=[c.pk])}],
        'manufactura:contramuestra_nueva', 'En rojo: ya pasó la fecha de conservación (destrúyala con su acta).')


@login_required
def contramuestra(request, pk=None):
    c = get_object_or_404(Contramuestra, pk=pk) if pk else None
    inicial = {}
    if c is None and (request.GET.get('lote') or '').isdigit():
        lote = Lote.objects.filter(pk=request.GET['lote']).first()
        if lote is not None:
            inicial = {'lote': lote.pk, 'conservar_hasta': (lote.vencimiento or timezone.localdate()).replace(
                year=(lote.vencimiento or timezone.localdate()).year + 1)}
    form = ContramuestraForm(request.POST or None, instance=c, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Contramuestra guardada.')
        return redirect('manufactura:contramuestras')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Contramuestra'})


class EstudioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = EstudioEstabilidad
        fields = ['lote', 'condicion', 'inicio', 'meses']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lotes(self.fields['lote'])


@login_required
def estabilidad(request):
    return _lista(request, 'Estudios de estabilidad', EstudioEstabilidad.objects.select_related('lote__producto'),
                  ['N°', 'Producto · lote', 'Condición', 'Inicio', 'Tiempos', 'Estado'],
                  lambda e: [{'texto': e.numero, 'url': reverse('manufactura:estudio', args=[e.pk])},
                             f'{e.lote.producto.codigo} · {e.lote.codigo}', e.get_condicion_display(),
                             fmt_fecha(e.inicio), e.meses, e.get_estado_display()],
                  'manufactura:estudio_nuevo')


@login_required
def estudio_nuevo(request):
    form = EstudioForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        e = cf.crear_estudio(form.save(commit=False))
        messages.success(request, f'{e} creado con {e.puntos.count()} tiempos de análisis.')
        return redirect('manufactura:estudio', e.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nuevo estudio de estabilidad'})


@login_required
def estudio(request, pk):
    e = get_object_or_404(EstudioEstabilidad.objects.select_related('lote__producto'), pk=pk)
    if request.method == 'POST':
        p = get_object_or_404(e.puntos, pk=request.POST.get('punto') or 0)
        p.realizado = timezone.localdate()
        p.resultado = request.POST.get('resultado', '')[:250]
        p.conforme = request.POST.get('conforme') == '1'
        p.save()
        if not p.conforme:
            messages.warning(request, f'Tiempo {p.mes} meses no conforme: abra una investigación (OOS o desviación).')
        return redirect('manufactura:estudio', pk)
    return render(request, 'produccion/estabilidad.html', {'e': e, 'puntos': e.puntos.all(),
                                                           'hoy': timezone.localdate()})


# ---------------------------------------------------------------- registro de lote (CAL-06)
@login_required
def registro_lote(request, pk):
    orden = get_object_or_404(OrdenProduccion.objects.select_related('producto'), pk=pk)
    reg = cf.crear_registro_lote(orden)
    if request.method == 'POST':
        try:
            accion = request.POST.get('accion')
            if accion == 'despeje':
                reg.despeje_linea, reg.despeje_por, reg.despeje_en = True, request.user, timezone.now()
                reg.save(update_fields=['despeje_linea', 'despeje_por', 'despeje_en'])
            elif accion == 'paso_nuevo':
                texto = (request.POST.get('descripcion') or '').strip()
                if texto:
                    PasoRegistroLote.objects.create(registro=reg, descripcion=texto[:200],
                                                    parametro=request.POST.get('parametro', '')[:120],
                                                    secuencia=(reg.pasos.count() + 1) * 10)
            else:
                paso = get_object_or_404(reg.pasos, pk=request.POST.get('paso') or 0)
                cf.firmar_paso(paso, request.user, accion, request.POST.get('valor', ''))
        except ErrorPlanta as exc:
            messages.error(request, str(exc))
        return redirect('manufactura:registro_lote', pk)
    filas, rendimiento = cf.conciliacion(orden)
    return render(request, 'produccion/registro_lote.html', {
        'o': orden, 'reg': reg, 'pasos': reg.pasos.select_related('hecho_por', 'verificado_por'),
        'conciliacion': filas, 'rendimiento': rendimiento,
        'controles': orden.inspecciones.filter(tipo='PROCESO'),
        'lote': Lote.objects.filter(producto=orden.producto, codigo=orden.lote_producido or orden.numero).first()})


# ---------------------------------------------------------------- revisión anual de producto (CAL-07)
@login_required
def pqr(request):
    producto = Producto.objects.filter(pk=request.GET.get('producto') or 0).first()
    anio = int(request.GET.get('anio') or timezone.localdate().year) if (request.GET.get('anio') or '0').isdigit() \
        else timezone.localdate().year
    datos = cf.revision_anual(producto, anio) if producto else None
    return render(request, 'produccion/pqr.html', {'d': datos, 'producto': producto, 'anio': anio})


# ---------------------------------------------------------------- calibraciones y calificaciones (CAL-08, MNT-08)
class CalibracionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CalibracionEquipo
        fields = ['equipo', 'fecha', 'entidad', 'certificado', 'resultado', 'error_encontrado', 'proxima',
                  'observaciones']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['equipo'].queryset = Equipo.objects.filter(activo=True)


class CalificacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CalificacionEquipo
        fields = ['equipo', 'tipo', 'fecha', 'protocolo', 'resultado', 'vigente_hasta']


@login_required
def calibraciones(request):
    hoy = timezone.localdate()
    filas = []
    for eq in Equipo.objects.filter(activo=True, es_instrumento=True).order_by('codigo'):
        estado = cf.estado_calibracion(eq, hoy)
        filas.append([eq.codigo, eq.nombre, eq.rango_tolerancia, fmt_fecha(eq.proxima_calibracion)
                      if eq.proxima_calibracion else '—',
                      {'texto': {'VIGENTE': 'Vigente', 'VENCIDA': 'Vencida', 'NO_CONFORME': 'No conforme',
                                 'SIN_CALIBRAR': 'Sin calibrar'}[estado],
                       'clase': '' if estado == 'VIGENTE' else 'text-danger fw-semibold'},
                      {'texto': 'Registrar calibración', 'url': reverse('manufactura:calibracion_nueva') +
                       f'?equipo={eq.pk}'}])
    ultimas = [[c.equipo.codigo, fmt_fecha(c.fecha), c.entidad, c.certificado, c.get_resultado_display(),
                fmt_fecha(c.proxima)] for c in CalibracionEquipo.objects.select_related('equipo')[:100]]
    calif = [[c.equipo.codigo, c.get_tipo_display(), fmt_fecha(c.fecha), c.protocolo, c.get_resultado_display(),
              fmt_fecha(c.vigente_hasta) if c.vigente_hasta else '—']
             for c in CalificacionEquipo.objects.select_related('equipo')[:100]]
    return responder(request, 'Calibración y calificación de equipos', [
        {'titulo': 'Instrumentos de medición', 'columnas': ['Código', 'Instrumento', 'Rango y tolerancia',
                                                            'Próxima calibración', 'Estado', ''], 'filas': filas},
        {'titulo': 'Calibraciones registradas', 'columnas': ['Equipo', 'Fecha', 'Laboratorio', 'Certificado',
                                                             'Resultado', 'Próxima'], 'filas': ultimas},
        {'titulo': 'Calificaciones (IQ / OQ / PQ) y validación de limpieza', 'columnas': [
            'Equipo', 'Tipo', 'Fecha', 'Protocolo', 'Resultado', 'Vigente hasta'], 'filas': calif}], por_seccion=True,
        botones=[('Registrar calibración', reverse('manufactura:calibracion_nueva'), 'bi-plus-lg'),
                 ('Registrar calificación', reverse('manufactura:calificacion_nueva'), 'bi-plus-lg')],
        ayuda='Un instrumento sin calibración vigente (o con la última no conforme) no se acepta en las inspecciones. '
              'Marque el equipo como «Instrumento de medición» con su frecuencia en la ficha del equipo.')


@login_required
def calibracion_nueva(request):
    form = CalibracionForm(request.POST or None, initial={'equipo': request.GET.get('equipo')})
    if request.method == 'POST' and form.is_valid():
        try:
            cf.registrar_calibracion(form.save(commit=False), request.user)
            messages.success(request, 'Calibración registrada. Adjunte el certificado en la ficha del equipo.')
            return redirect('manufactura:calibraciones')
        except ErrorPlanta as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Calibración de instrumento'})


@login_required
def calificacion_nueva(request):
    form = CalificacionForm(request.POST or None, initial={'equipo': request.GET.get('equipo')})
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Calificación registrada.')
        return redirect('manufactura:calibraciones')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Calificación de equipo (IQ / OQ / PQ)'})


# ---------------------------------------------------------------- validación del sistema (CAL-08)
@login_required
def validacion_sistema(request):
    from django.conf import settings
    if not request.user.is_superuser and not request.user.has_perm('auth.view_user'):
        from core.permisos import puede
        if not puede(request.user, 'control_calidad.aprobar_qa'):
            raise Http404
    return render(request, 'produccion/validacion_sistema.html', {'version': settings.ERP_VERSION,
                                                                  'hoy': timezone.localdate()})
