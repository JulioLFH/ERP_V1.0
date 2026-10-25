"""Pantallas de servicios profesionales por proyecto (v1.81): entregables, partes de horas y su aprobación, horas
presupuestadas frente a reales, garantías del contrato y cierre del mes de los servicios en proceso."""
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.forms import BootstrapMixin
from core.utils import fmt_fecha
from ventas.crm_tabla import responder

from . import servicios as srv
from .models import EntregableProyecto, GarantiaContrato, Obra, ParteHoras

D0 = Decimal('0')


def _fecha(texto):
    try:
        return date.fromisoformat(texto or '')
    except ValueError:
        return None


class EntregableForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = EntregableProyecto
        fields = ['nombre', 'porcentaje', 'monto', 'fecha_comprometida', 'horas_presupuestadas',
                  'penalidad_diaria_pct', 'penalidad_maxima_pct']


@login_required
def entregables(request, pk):
    obra = get_object_or_404(Obra, pk=pk)
    form = EntregableForm(request.POST if request.POST.get('accion') == 'nuevo' else None)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'nuevo':
                if form.is_valid():
                    e = srv.nuevo_entregable(obra, **form.cleaned_data)
                    messages.success(request, f'Entregable {e} registrado.')
                    return redirect('obras:entregables', pk)
            else:
                e = get_object_or_404(EntregableProyecto, pk=request.POST.get('entregable') or 0, obra=obra)
                if accion == 'entregar':
                    srv.entregar(e, _fecha(request.POST.get('fecha')))
                    messages.success(request, f'{e} entregado.' + (f' Atraso de {e.dias_atraso} día(s): penalidad '
                                                                   f'S/ {e.penalidad:,.2f}.' if e.penalidad else ''))
                elif accion == 'observar':
                    srv.observar(e, request.POST.get('observacion'))
                    messages.warning(request, f'{e} observado por el cliente: corríjalo y vuelva a entregarlo.')
                elif accion == 'conformidad':
                    srv.conformidad(e, request.POST.get('por'), _fecha(request.POST.get('fecha')))
                    archivo = request.FILES.get('sustento')
                    if archivo:
                        from core.sustentos import adjuntar
                        adjuntar(e, archivo, request.user, f'Conformidad del entregable {e}')
                    messages.success(request, f'{e}: conformidad registrada; ya se puede facturar.')
                elif accion == 'facturar':
                    from core.permisos import puede
                    if not puede(request.user, 'obras.facturar'):
                        raise srv.ErrorServicio('Su usuario no factura en Obras (permiso «facturar»).')
                    comp = srv.facturar(e, request.user, _fecha(request.POST.get('fecha')))
                    messages.success(request, f'{e} facturado: {comp.numero_completo}.')
                return redirect('obras:entregables', pk)
        except srv.ErrorServicio as exc:
            messages.error(request, str(exc))
            return redirect('obras:entregables', pk)
    lista = list(obra.entregables.select_related('factura'))
    total = sum((e.valor for e in lista), D0)
    from core import sustentos
    for e in lista:
        e.sustentos = sustentos.de(e)
        e.penalidad_actual = e.penalidad or srv.penalidad(e)
    return render(request, 'obras/entregables.html', {
        'o': obra, 'entregables': lista, 'form': form, 'total': total, 'hoy': timezone.localdate(),
        'horas': srv.horas_proyecto(obra)})


class ParteForm(BootstrapMixin, forms.Form):
    obra = forms.ModelChoiceField(label='Proyecto', queryset=None)
    semana = forms.DateField(label='Semana (cualquier día)')
    trabajador = forms.ModelChoiceField(label='Trabajador (planilla)', queryset=None, required=False)
    persona = forms.CharField(label='o especialista independiente', max_length=120, required=False)
    entregable = forms.ModelChoiceField(queryset=None, required=False)
    horas = forms.DecimalField(min_value=Decimal('0.25'), max_digits=6, decimal_places=2)
    costo_hora = forms.DecimalField(label='Costo hora S/ (independiente)', required=False, min_value=0,
                                    help_text='Vacío = el del trabajador (sueldo con cargas / horas del mes)')
    descripcion = forms.CharField(label='Qué se hizo', max_length=250, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from planillas.models import Trabajador
        self.fields['obra'].queryset = srv.proyectos_de_servicio()
        self.fields['trabajador'].queryset = Trabajador.objects.filter(fecha_cese__isnull=True)
        self.fields['entregable'].queryset = EntregableProyecto.objects.exclude(estado='FACTURADO').select_related(
            'obra')
        self.fields['entregable'].label_from_instance = lambda e: f'{e.obra.codigo} · {e}'


@login_required
def partes_horas(request):
    """Registro semanal de horas por persona y proyecto (lo envía y lo aprueba el jefe de proyecto)."""
    from planillas.models import Trabajador
    propio = Trabajador.objects.filter(usuario=request.user).first()
    form = ParteForm(request.POST or None, initial={'semana': timezone.localdate(), 'trabajador': propio,
                                                     'obra': request.GET.get('obra')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            p = srv.registrar_parte(d['obra'], d['semana'], d['horas'], request.user, d.get('trabajador'),
                                    d.get('persona') or '', d.get('entregable'), d.get('descripcion') or '',
                                    d.get('costo_hora'))
            messages.success(request, f'{p.horas:g} horas en {p.obra.codigo} enviadas al jefe de proyecto.')
            return redirect('obras:partes_horas')
        except srv.ErrorServicio as exc:
            form.add_error(None, str(exc))
    qs = ParteHoras.objects.select_related('obra', 'trabajador', 'entregable')
    if not request.user.is_superuser:
        from django.db.models import Q
        qs = qs.filter(Q(registrado_por=request.user) | Q(trabajador=propio)) if propio else \
            qs.filter(registrado_por=request.user)
    filas = [[fmt_fecha(p.semana), p.obra.codigo, p.nombre_persona, str(p.entregable or ''), p.horas, p.costo_hora,
              p.costo, p.get_estado_display() + (f': {p.motivo_rechazo}' if p.motivo_rechazo else ''), p.descripcion]
             for p in qs.distinct()[:300]]
    return responder(request, 'Partes de horas por proyecto', [{
        'titulo': 'Registrados', 'columnas': ['Semana', 'Proyecto', 'Persona', 'Entregable', 'Horas', 'Costo hora',
                                              'Costo S/', 'Estado', 'Qué se hizo'], 'filas': filas}],
        form=form, form_titulo='Registrar horas de la semana',
        botones=[('Por aprobar', reverse('obras:partes_aprobar'), 'bi-check2-square')])


@login_required
def partes_aprobar(request):
    if request.method == 'POST':
        p = get_object_or_404(ParteHoras, pk=request.POST.get('parte') or 0)
        try:
            srv.aprobar_parte(p, request.user, request.POST.get('accion') == 'aprobar', request.POST.get('motivo', ''))
            messages.success(request, f'Parte de {p.nombre_persona} {p.get_estado_display().lower()}.')
        except srv.ErrorServicio as exc:
            messages.error(request, str(exc))
        return redirect('obras:partes_aprobar')
    qs = ParteHoras.objects.filter(estado='ENVIADO').select_related('obra', 'trabajador', 'entregable')
    if not request.user.is_superuser:
        from core.permisos import puede
        if not puede(request.user, 'obras.aprobar'):
            qs = qs.filter(obra__residente=request.user)
    url = reverse('obras:partes_aprobar')
    filas = [[fmt_fecha(p.semana), f'{p.obra.codigo} {p.obra.nombre}', p.nombre_persona, str(p.entregable or ''),
              p.horas, p.costo, p.descripcion,
              [{'texto': 'Aprobar', 'post': url, 'campo': 'parte', 'valor': p.pk, 'extra': {'accion': 'aprobar'}},
               {'texto': 'Rechazar', 'post': url, 'campo': 'parte', 'valor': p.pk,
                'extra': {'accion': 'rechazar', 'motivo': 'Rechazado por el jefe de proyecto: revise las horas'}}]]
             for p in qs]
    return responder(request, 'Partes de horas por aprobar', [{
        'columnas': ['Semana', 'Proyecto', 'Persona', 'Entregable', 'Horas', 'Costo S/', 'Qué se hizo', ''],
        'filas': filas}], ayuda='Los aprueba el jefe de proyecto (el residente del proyecto) o quien aprueba en Obras.')


@login_required
def horas(request, pk):
    obra = get_object_or_404(Obra, pk=pk)
    h = srv.horas_proyecto(obra)
    filas = [[e['nombre'], e['presupuestadas'], e['horas'], e['horas'] - e['presupuestadas'], e['costo']]
             for e in h['entregables']]
    filas.append([{'texto': 'Total del proyecto', 'clase': 'fw-semibold'}, h['presupuestadas'], h['horas'],
                  h['horas'] - h['presupuestadas'], h['costo']])
    personas = [[k, v['horas'], v['costo']] for k, v in sorted(h['personas'].items())]
    return responder(request, f'Horas del proyecto {obra.codigo}', [
        {'titulo': 'Por entregable (presupuestadas frente a reales)', 'columnas': [
            'Entregable', 'Horas presupuestadas', 'Horas reales', 'Diferencia', 'Costo S/'], 'filas': filas},
        {'titulo': 'Por persona', 'columnas': ['Persona', 'Horas', 'Costo S/'], 'filas': personas}],
        por_seccion=True, botones=[('Entregables', reverse('obras:entregables', args=[pk]), 'bi-list-check')],
        ayuda=f'Solo partes aprobados. Avance de horas del proyecto: '
              f'{h["avance_horas"] if h["avance_horas"] is not None else "—"} %.')


class GarantiaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = GarantiaContrato
        fields = ['obra', 'tipo', 'entidad', 'numero', 'monto', 'emision', 'vencimiento', 'estado', 'notas']


@login_required
def garantias(request):
    qs = GarantiaContrato.objects.select_related('obra')
    if request.GET.get('ver') != 'todas':
        qs = qs.filter(estado='VIGENTE')
    filas = [[{'texto': g.obra.codigo, 'url': reverse('obras:obra', args=[g.obra_id])}, g.get_tipo_display(),
              g.entidad, g.numero, g.monto, fmt_fecha(g.emision),
              {'texto': fmt_fecha(g.vencimiento), 'clase': 'text-danger fw-semibold' if g.por_vencer else ''},
              g.get_estado_display(), {'texto': 'Editar', 'url': reverse('obras:garantia', args=[g.pk])}] for g in qs]
    return responder(request, 'Garantías y cartas fianza', [{
        'columnas': ['Obra / proyecto', 'Tipo', 'Banco / aseguradora', 'N°', 'Monto S/', 'Emisión', 'Vence', 'Estado',
                     ''], 'filas': filas}],
        botones=[('Nueva garantía', reverse('obras:garantia_nueva'), 'bi-plus-lg')],
        selects=[('ver', 'Ver', [('', 'Vigentes'), ('todas', 'Todas')], request.GET.get('ver', ''))],
        ayuda='En rojo: vencen en 15 días (también sale en Inicio). Renueve la carta fianza antes del vencimiento.')


@login_required
def garantia(request, pk=None):
    g = get_object_or_404(GarantiaContrato, pk=pk) if pk else GarantiaContrato(obra_id=request.GET.get('obra') or None)
    form = GarantiaForm(request.POST or None, instance=g)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Garantía guardada.')
        return redirect('obras:garantias')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Garantía del contrato'})


@login_required
def servicios_proceso(request):
    hoy = timezone.localdate()
    periodo = request.GET.get('periodo') or (hoy.replace(day=1) - timedelta(days=1)).strftime('%Y%m')
    if not (periodo.isdigit() and len(periodo) == 6):
        periodo = hoy.strftime('%Y%m')
    if request.method == 'POST':
        try:
            a = srv.registrar_cierre(request.POST.get('periodo') or periodo, request.user)
            messages.success(request, f'Asiento {a.numero or a.pk} registrado con el servicio en proceso y el ingreso '
                                      f'por avance.')
        except srv.ErrorServicio as exc:
            messages.error(request, str(exc))
        return redirect(f"{reverse('obras:servicios_proceso')}?periodo={periodo}")
    filas = []
    for obra in srv.proyectos_de_servicio():
        s = srv.situacion(obra, periodo)
        filas.append([{'texto': f'{obra.codigo} {obra.nombre}', 'url': reverse('obras:entregables', args=[obra.pk])},
                      s['contrato'], s['costo'], s['presupuesto'], f'{s["avance"]} %' if s['avance'] is not None else '—',
                      s['facturado'], s['costo_proceso'], s['ingreso_devengado']])
    return responder(request, f'Servicios en proceso al cierre de {periodo[4:]}/{periodo[:4]}', [{
        'columnas': ['Proyecto', 'Contrato S/ (sin IGV)', 'Costo acumulado', 'Costo presupuestado', 'Avance por costo',
                     'Facturado', 'Costo sin facturar (en proceso)', 'Ingreso por avance no facturado (NIIF 15)'],
        'filas': filas}], textos=[('periodo', 'Mes (AAAAMM)', periodo)],
        form=_CierreForm(initial={'periodo': periodo}), form_titulo='Registrar el asiento del mes',
        ayuda='Costo acumulado = horas aprobadas × costo hora + compras con el centro de costo del proyecto. El asiento '
              'registra la diferencia con el mes anterior (servicio en proceso 2351 / variación 7152 e ingreso por '
              'avance: facturas por emitir 1211 / ventas de servicios). Confirme el criterio NIIF 15 con su contador.')


class _CierreForm(BootstrapMixin, forms.Form):
    periodo = forms.CharField(label='Mes (AAAAMM)', max_length=6)
