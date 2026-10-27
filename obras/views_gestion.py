"""Pantallas de gestión del contrato y del proyecto (v1.86, HR-01 a HR-20). Usan la tabla genérica del CRM
(ventas.crm_tabla): cada pantalla se descarga en Excel."""
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from core.forms import BootstrapMixin
from core.utils import id_o_none
from ventas.crm_tabla import responder

from . import gestion as g
from .models import AdendaContrato, EscenarioCronograma, EventoContrato, Obra, Presupuesto, Subcontrato

D0 = Decimal('0')


def _f(v):
    return float(v) if v is not None else None


def _p(v, sufijo=' %'):
    return f'{v}{sufijo}' if v is not None else '—'


def _s(v):
    return f'S/ {v:,.2f}' if v is not None else '—'


def _mes(per):
    return f'{per[4:]}/{per[:4]}' if per else '—'


def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _obra_botones(o):
    """Navegación entre las pantallas de gestión de la obra."""
    return [('Ejecución', reverse('obras:ejecucion', args=[o.pk]), 'bi-arrow-left'),
            ('Valor ganado', reverse('obras:valor_ganado', args=[o.pk]), 'bi-graph-up'),
            ('Proyección al cierre', reverse('obras:proyeccion', args=[o.pk]), 'bi-bullseye'),
            ('Flujo de caja', reverse('obras:flujo_proyecto', args=[o.pk]), 'bi-water'),
            ('Adendas', reverse('obras:adendas', args=[o.pk]), 'bi-file-earmark-plus'),
            ('Cartera', reverse('obras:cartera'), 'bi-grid-3x3-gap')]


def _semaforo(nivel):
    return {'danger': 'text-danger fw-semibold', 'warning': 'text-warning fw-semibold'}.get(nivel, 'text-success')


def _selects_obra_tipo(request, con_cliente=True):
    from core.models import Tercero
    selects = [('tipo', 'Tipo', [('', 'Todos')] + list(Obra.TIPOS), request.GET.get('tipo', ''))]
    if con_cliente:
        ids = Obra.objects.filter(cliente__isnull=False).values_list('cliente_id', flat=True).distinct()
        selects.append(('cliente', 'Cliente', [('', 'Todos')] + [(str(t.pk), t.nombre[:40]) for t in
                                                                 Tercero.objects.filter(pk__in=ids).order_by('nombre')],
                        request.GET.get('cliente', '')))
    return selects


# ---------------------------------------------------------------- HR-18: cartera de proyectos
@login_required
def cartera(request):
    from core.utils import id_o_none
    estados = [e for e in request.GET.getlist('estado') if e in dict(Obra.ESTADOS)] or None
    c = g.cartera(estados, request.GET.get('tipo') or None, id_o_none(request.GET.get('cliente')))
    filas = []
    for f in c['filas']:
        o = f['obra']
        alertas = []
        if f['atrasadas']:
            alertas.append({'texto': f'{f["atrasadas"]} partida(s) atrasada(s)',
                            'url': reverse('obras:cronograma', args=[o.pk]), 'clase': 'text-warning'})
        if f['sobre_costo']:
            alertas.append({'texto': f'{f["sobre_costo"]} sobre costo', 'url': reverse('obras:proyeccion', args=[o.pk]),
                            'clase': 'text-warning'})
        if f['bajo_meta']:
            alertas.append({'texto': 'margen bajo la meta', 'url': reverse('obras:proyeccion', args=[o.pk]),
                            'clase': 'text-danger'})
        filas.append([{'texto': f'● {o.codigo}', 'url': reverse('obras:ejecucion', args=[o.pk]),
                       'clase': _semaforo(f['semaforo'])}, o.nombre[:45],
                      o.cliente.nombre[:30] if o.cliente_id else '—', o.get_tipo_display().split(' (')[0],
                      o.get_estado_display(), f['contrato'], f['valorizado'], f['costo'], _p(f['avance']),
                      _p(f['programado']), f['spi'] if f['spi'] is not None else '—',
                      f['cpi'] if f['cpi'] is not None else '—',
                      {'texto': _p(f['margen_pct']), 'clase': 'text-danger' if f['bajo_meta'] else ''},
                      _p(f['margen_meta']),
                      f['termino_estimado'].strftime('%d/%m/%Y') if f['termino_estimado'] else '—',
                      alertas or '—',
                      [{'texto': 'Valor ganado', 'url': reverse('obras:valor_ganado', args=[o.pk])},
                       {'texto': 'Partidas', 'url': reverse('obras:proyeccion', args=[o.pk])},
                       {'texto': 'Flujo', 'url': reverse('obras:flujo_proyecto', args=[o.pk])}]])
    t = c['total']
    return responder(request, 'Cartera de proyectos', [{
        'columnas': ['Proyecto', 'Nombre', 'Cliente', 'Tipo', 'Estado', 'Contrato S/', 'Valorizado S/',
                     'Costo real S/', 'Avance físico', 'Programado', 'SPI', 'CPI', 'Margen proyectado', 'Margen meta',
                     'Término estimado', 'Alertas', 'Ver'], 'filas': filas}],
        selects=_selects_obra_tipo(request) + [
            ('estado', 'Estado', [('', 'Activos')] + list(Obra.ESTADOS), request.GET.get('estado', ''))],
        kpis=[('Proyectos', len(c['filas']), ''), ('En rojo', c['rojo'], 'text-danger'),
              ('En ámbar', c['ambar'], 'text-warning'), ('Contratado (con IGV)', _s(t['contrato']), ''),
              ('Valorizado (sin IGV)', _s(t['valorizado']), ''), ('Costo real', _s(t['costo']), '')],
        ayuda='Un clic en el código abre la ejecución de la obra y «Ver» su valor ganado, el control por partida o el '
              'flujo de caja. Rojo: margen proyectado bajo la meta, SPI o CPI menor a 0.85; ámbar: partidas atrasadas '
              'o con costo sobre lo esperado. SPI = valor ganado ÷ planificado (plazo); CPI = valor ganado ÷ costo '
              'real (costo).')


# ---------------------------------------------------------------- HR-01: tasa de éxito
@login_required
def tasa_exito(request):
    desde, hasta = _fecha(request.GET.get('desde')), _fecha(request.GET.get('hasta'))
    r = g.tasa_exito(desde, hasta)
    columnas = ['', 'Presentadas', 'Ganadas', 'Perdidas', 'Tasa de éxito', 'Monto presentado S/', 'Monto ganado S/',
                'Tasa en monto', 'Motivo principal de pérdida']

    def filas(lista):
        return [[x['clave'], x['presentadas'], x['ganadas'], x['perdidas'], _p(x['tasa']), x['monto'],
                 x['monto_ganado'], _p(x['tasa_monto']), x['motivo'] or '—'] for x in lista]

    total = r['total'][0] if r['total'] else None
    crm_total = r['crm_total'][0] if r['crm_total'] else None
    secciones = [{'titulo': f'Licitaciones y propuestas por {t}', 'columnas': [t.capitalize()] + columnas[1:],
                  'filas': filas(r['licitaciones'][k])}
                 for k, t in (('procedimiento', 'tipo de proceso'), ('cliente', 'cliente'),
                              ('responsable', 'responsable'))]
    secciones += [{'titulo': f'Oportunidades del CRM por {t}', 'columnas': [t.capitalize()] + columnas[1:],
                   'filas': filas(r['crm'][k])}
                  for k, t in (('origen', 'origen'), ('cliente', 'cliente'), ('responsable', 'responsable'))]
    return responder(request, 'Tasa de éxito de propuestas', secciones, por_seccion=True,
                     rango=(desde, hasta),
                     kpis=[('Propuestas con resultado', total['presentadas'] if total else 0, ''),
                           ('Ganadas', total['ganadas'] if total else 0, 'text-success'),
                           ('Tasa de éxito (licitaciones)', _p(total['tasa']) if total else '—', ''),
                           ('Tasa en monto', _p(total['tasa_monto']) if total else '—', ''),
                           ('Tasa de éxito (CRM)', _p(crm_total['tasa']) if crm_total else '—', '')],
                     ayuda='Ganadas ÷ presentadas con resultado (las desiertas o canceladas no cuentan), en número y en '
                           'monto de la oferta. El responsable es el de la oportunidad del CRM o, si no hay, el '
                           'residente de la obra. El rango filtra por la fecha del resultado.')


# ---------------------------------------------------------------- HR-02: sensibilidad del margen
@login_required
def sensibilidad(request, pk):
    p = get_object_or_404(Presupuesto.objects.select_related('obra'), pk=pk)
    texto = request.GET.get('variaciones', '').strip()
    variaciones = []
    for parte in texto.replace(';', ',').split(','):
        try:
            v = Decimal(parte.strip().replace('%', ''))
            if -90 <= v <= 300:
                variaciones.append(v)
        except Exception:  # noqa: BLE001 - texto libre del usuario
            continue
    r = g.sensibilidad(p, variaciones or None)
    cab = ['Insumo', 'Tipo', 'Monto S/', '% del costo directo'] + [
        f'Margen con {v:+g} %' for v in r['variaciones']] + ['Variación que anula el margen']

    def celdas(esc):
        return [{'texto': f'{e["margen"]:,.2f} ({_p(e["pct"])})', 'clase': 'num text-danger' if e['margen'] < 0
                 else 'num'} for e in esc]

    filas = [[f['recurso'].nombre, f['recurso'].get_tipo_display(), f['monto'], _p(f['peso'])] + celdas(f['escenarios'])
             + [_p(f['quiebre'], ' % de alza') if f['quiebre'] is not None else '—'] for f in r['filas']]
    conjunto = [[x['nombre'], '', x['monto'], ''] + celdas(x['escenarios'])
                + [_p(x['quiebre'], ' % de alza') if x['quiebre'] is not None else '—'] for x in r['conjunto']]
    return responder(
        request, f'Sensibilidad del margen · {p}', [
            {'titulo': 'Insumos principales (cada uno por separado)', 'columnas': cab, 'filas': filas},
            {'titulo': 'Varios insumos a la vez', 'columnas': cab, 'filas': conjunto}], por_seccion=True,
        textos=[('variaciones', 'Variaciones % (separadas por coma)', texto or '-10, -5, 5, 10, 20')],
        botones=[('Presupuesto', reverse('obras:presupuesto', args=[p.pk]), 'bi-arrow-left'),
                 ('Insumos', reverse('obras:insumos', args=[r['costo_de'].pk]), 'bi-list-columns')],
        kpis=[('Venta sin IGV', _s(r['ingreso']), ''), ('Costo directo + gastos generales', _s(r['costo']), ''),
              ('Margen', _s(r['margen']), 'text-danger' if r['margen'] < 0 else ''),
              ('Margen %', _p(r['margen_pct']), '')],
        ayuda=f'Margen = venta sin IGV − costo directo ({r["costo_de"]}) − gastos generales. Cada columna muestra el '
              f'margen si el precio del insumo varía ese %; la última, cuánto tendría que subir para que el margen '
              f'llegue a cero.')


# ---------------------------------------------------------------- HR-03: adendas
class AdendaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AdendaContrato
        fields = ['tipo', 'fecha', 'monto', 'plazo_dias', 'motivo', 'resolucion', 'fecha_resolucion', 'presupuesto']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}),
                   'fecha_resolucion': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['presupuesto'].queryset = Presupuesto.objects.filter(obra=obra).order_by('tipo', 'version')
        self.fields['presupuesto'].required = False


@login_required
def adendas(request, pk):
    o = get_object_or_404(Obra.objects.select_related('cliente'), pk=pk)
    form = AdendaForm(obra=o)
    if request.method == 'POST':
        try:
            if 'accion' in request.POST:
                a = get_object_or_404(AdendaContrato, pk=id_o_none(request.POST.get('adenda')), obra=o)
                if request.POST['accion'] == 'aprobar':
                    g.aprobar_adenda(a, request.user)
                    messages.success(request, f'{a} aprobada: el contrato vigente es S/ {Obra.objects.get(pk=o.pk).monto_contratado:,.2f} '
                                              f'y el plazo {Obra.objects.get(pk=o.pk).plazo_dias} días.')
                else:
                    g.anular_adenda(a, request.user, request.POST.get('motivo', ''))
                    messages.info(request, f'{a} anulada.')
                return redirect('obras:adendas', pk)
            form = AdendaForm(request.POST, obra=o)
            if form.is_valid():
                d = form.cleaned_data
                a = g.registrar_adenda(o, d['tipo'], d['monto'], d['plazo_dias'], d['motivo'], request.user,
                                       d['fecha'], d['resolucion'], d['fecha_resolucion'], d['presupuesto'])
                messages.success(request, f'{a} registrada: falta aprobarla para que cambie el contrato.')
                return redirect('obras:adendas', pk)
        except g.ErrorGestion as exc:
            messages.error(request, str(exc))
    o.refresh_from_db()
    v = g.contrato_vigente(o)
    filas = []
    for a in o.adendas.select_related('presupuesto', 'aprobado_por', 'creado_por'):
        acciones = []
        if a.estado == 'BORRADOR':
            acciones.append({'texto': 'Aprobar', 'post': '', 'campo': 'adenda', 'valor': a.pk,
                             'extra': {'accion': 'aprobar'},
                             'confirmar': f'¿Aprobar la adenda N° {a.numero}? Cambia el monto y el plazo del contrato.'})
        if a.estado != 'ANULADA':
            acciones.append({'texto': 'Anular', 'post': '', 'campo': 'adenda', 'valor': a.pk,
                             'extra': {'accion': 'anular'}, 'pedir': ('motivo', 'Motivo de la anulación')})
        filas.append([a.numero, a.fecha.strftime('%d/%m/%Y'), a.get_tipo_display(), a.monto, a.plazo_dias,
                      a.motivo, a.resolucion or '—',
                      a.fecha_resolucion.strftime('%d/%m/%Y') if a.fecha_resolucion else '—',
                      {'texto': str(a.presupuesto), 'url': reverse('obras:presupuesto', args=[a.presupuesto_id])}
                      if a.presupuesto_id else '—', a.get_estado_display(),
                      a.monto_anterior if a.monto_anterior is not None else '—',
                      (a.motivo_anulacion or str(a.aprobado_por or '')) or '—', acciones or '—'])
    avisos = []
    if v['diferencia_presupuesto'] and abs(v['diferencia_presupuesto']) > 1:
        avisos.append(('warning', f'El contrato vigente sin IGV (S/ {v["monto_sin_igv"]:,.2f}) no coincide con el '
                                  f'presupuesto que se valoriza ({v["presupuesto"]}: S/ {v["presupuesto"].subtotal:,.2f}). '
                                  f'Cargue la versión del adicional o deductivo en «Versiones (S10)» y apruébela para '
                                  f'valorizar la adenda.'))
    return responder(
        request, f'Adendas del contrato · {o.codigo} {o.nombre}', [{
            'columnas': ['N°', 'Fecha', 'Tipo', 'Monto S/', 'Plazo (días)', 'Motivo', 'Resolución', 'Fecha resol.',
                         'Versión del presupuesto', 'Estado', 'Monto anterior S/', 'Aprobada por / motivo', ''],
            'filas': filas}],
        form=form, form_titulo='Registrar una adenda (luego se aprueba)', botones=_obra_botones(o), avisos=avisos,
        kpis=[('Contrato original (con IGV)', _s(v['original']), ''),
              ('Adendas aprobadas', _s(v['variacion_monto']), ''), ('Contrato vigente', _s(v['monto']), 'fw-bold'),
              ('Plazo vigente', f'{v["plazo"]} días ({v["variacion_plazo"]:+d})', ''),
              ('Término', v['termino'].strftime('%d/%m/%Y') if v['termino'] else '—', ''),
              ('Por valorizar (sin IGV)', _s(v['por_valorizar']), '')],
        ayuda='Al aprobar una adenda cambian el monto contratado y el plazo de la obra (queda el anterior en el '
              'historial). Lo que se valoriza es el presupuesto contractual: vincule la versión del adicional o '
              'deductivo. Solo se anula la última adenda aprobada; quien la registra no la aprueba.')


# ---------------------------------------------------------------- HR-04: eventos y riesgo contractual
class EventoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = EventoContrato
        fields = ['fecha', 'tipo', 'gravedad', 'monto', 'descripcion', 'resuelto']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.obra = obra

    def clean(self):
        """OBR-39: fecha razonable, montos no negativos y penalidades con tope acumulado (% del contrato)."""
        d = super().clean()
        fecha, monto, tipo = d.get('fecha'), d.get('monto'), d.get('tipo')
        if fecha and self.obra is not None:
            try:
                g._fecha_razonable(self.obra, fecha, 'La fecha')
            except g.ErrorGestion as exc:
                self.add_error('fecha', str(exc))
        if monto is not None and monto < 0:
            self.add_error('monto', 'El monto no puede ser negativo.')
        elif tipo == 'PENALIDAD' and self.obra is not None:
            if not monto:
                self.add_error('monto', 'Indique el monto de la penalidad.')
            else:
                from core import parametros
                tope_pct = parametros.valor('OBR_PENALIDAD_MAX_PCT') or Decimal('10')
                tope = (self.obra.monto_contratado * tope_pct / 100).quantize(Decimal('0.01'))
                previas = sum((e.monto for e in self.obra.eventos_contrato.filter(tipo='PENALIDAD')), Decimal('0'))
                if previas + monto > tope:
                    self.add_error('monto', f'Las penalidades sumarían S/ {previas + monto:,.2f}: el tope es el '
                                            f'{tope_pct:g} % del contrato (S/ {tope:,.2f}; ya aplicadas '
                                            f'S/ {previas:,.2f}).')
        return d


@login_required
def eventos(request, pk):
    o = get_object_or_404(Obra.objects.select_related('cliente'), pk=pk)
    form = EventoForm(initial={'fecha': timezone.localdate()}, obra=o)
    if request.method == 'POST':
        if 'evento' in request.POST:
            e = get_object_or_404(EventoContrato, pk=id_o_none(request.POST['evento']), obra=o)
            e.resuelto = not e.resuelto
            e.save(update_fields=['resuelto'])
            return redirect('obras:eventos', pk)
        form = EventoForm(request.POST, obra=o)
        if form.is_valid():
            e = form.save(commit=False)
            e.obra, e.registrado_por = o, request.user
            e.save()
            messages.success(request, f'{e.get_tipo_display()} registrada.')
            return redirect('obras:eventos', pk)
    filas = [[e.fecha.strftime('%d/%m/%Y'), e.get_tipo_display(), e.get_gravedad_display(), e.monto, e.descripcion,
              'Sí' if e.resuelto else 'No', str(e.registrado_por or '—'),
              {'texto': 'Reabrir' if e.resuelto else 'Marcar resuelto', 'post': '', 'campo': 'evento', 'valor': e.pk}]
             for e in o.eventos_contrato.select_related('registrado_por')]
    return responder(
        request, f'Penalidades y observaciones · {o.codigo} {o.nombre}', [{
            'columnas': ['Fecha', 'Tipo', 'Gravedad', 'Monto S/', 'Descripción', 'Resuelto', 'Registrado por', ''],
            'filas': filas}],
        form=form, form_titulo='Registrar una penalidad, observación, reclamo o controversia',
        botones=_obra_botones(o) + [('Riesgo por cliente', reverse('obras:riesgo_clientes'), 'bi-shield-exclamation')],
        ayuda='Con los atrasos de pago del cliente forman su riesgo contractual (Gestión de proyectos › Riesgo '
              'contractual por cliente).')


@login_required
def riesgo_clientes(request):
    filas = g.riesgo_clientes()
    clase = {'Alto': 'text-danger fw-semibold', 'Medio': 'text-warning fw-semibold', 'Bajo': 'text-success'}
    return responder(request, 'Riesgo contractual por cliente', [{
        'columnas': ['Cliente', 'Obras', 'Contratado S/', 'Penalidades S/', 'Observaciones y reclamos', 'Abiertos',
                     'Graves', 'Atraso promedio de pago', '% pagado tarde', 'Comprobantes cobrados', 'Vencido hoy S/',
                     'Mora máxima (días)', 'Puntaje', 'Riesgo'],
        'filas': [[{'texto': c['tercero'].nombre, 'url': reverse('finanzas:cobranza_cliente', args=[c['tercero'].pk])},
                   c['obras'], c['contrato'], c['penalidades'], c['eventos'], c['abiertos'], c['graves'],
                   f'{c["atraso"]} días' if c['atraso'] is not None else '—', _p(c['tarde_pct']), c['docs_cobrados'],
                   c['vencido'], c['mora'], c['puntaje'], {'texto': c['nivel'], 'clase': clase[c['nivel']]}]
                  for c in filas]}],
        ayuda='Puntaje de 0 a 100: penalidades sobre el contrato (hasta 30), atraso promedio de pago de sus '
              'comprobantes cobrados en 2 años (hasta 30), observaciones, reclamos y controversias abiertas o graves '
              '(hasta 20) y saldo vencido hoy sobre el contrato (hasta 20). Bajo < 25, medio < 50, alto desde 50. Los '
              'eventos se registran en cada obra (Ejecución › Penalidades y observaciones).')


# ---------------------------------------------------------------- HR-05: valor ganado
@login_required
def valor_ganado(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    r = g.valor_ganado(o)
    if r is None:
        messages.error(request, 'La obra no tiene presupuesto.')
        return redirect('obras:obra', pk)
    filas = [[x['etiqueta'], x['pv'], x['ev'] if x['ev'] is not None else '—', x['ac'] if x['ac'] is not None else '—',
              x['pv_acum'], x['ev_acum'] if x['ev_acum'] is not None else '—',
              x['ac_acum'] if x['ac_acum'] is not None else '—',
              {'texto': x['spi'] if x['spi'] is not None else '—',
               'clase': 'text-danger' if x['spi'] is not None and x['spi'] < 1 else ''},
              {'texto': x['cpi'] if x['cpi'] is not None else '—',
               'clase': 'text-danger' if x['cpi'] is not None and x['cpi'] < 1 else ''}] for x in r['filas']]
    grafico = {'etiquetas': [x['etiqueta'] for x in r['filas']],
               'series': [{'nombre': 'PV planificado', 'datos': [_f(x['pv_acum']) for x in r['filas']]},
                          {'nombre': 'EV ganado', 'datos': [_f(x['ev_acum']) for x in r['filas']]},
                          {'nombre': 'AC costo real', 'datos': [_f(x['ac_acum']) for x in r['filas']]}]}
    avisos = []
    if r['fuente_costo'] == 'contable':
        avisos.append(('light border', 'El costo real sale de la contabilidad (gastos con el centro de costo de la '
                                       'obra): no hay planillas, partes de equipo, subcontratos ni salidas de almacén.'))
    return responder(
        request, f'Valor ganado · {o.codigo} {o.nombre}', [{
            'columnas': ['Mes', 'PV del mes', 'EV del mes', 'AC del mes', 'PV acumulado', 'EV acumulado',
                         'AC acumulado', 'SPI', 'CPI'], 'filas': filas}],
        botones=_obra_botones(o), grafico=grafico if r['filas'] else None, avisos=avisos,
        kpis=[('Presupuesto del costo (BAC)', _s(r['bac']), ''), ('Avance (EV / BAC)', _p(r['avance_pct']), ''),
              ('SPI (plazo)', r['spi'] or '—', 'text-danger' if r['spi'] and r['spi'] < 1 else ''),
              ('CPI (costo)', r['cpi'] or '—', 'text-danger' if r['cpi'] and r['cpi'] < 1 else ''),
              ('Costo directo al cierre (EAC, por partida)', _s(r['eac']), ''),
              ('EAC por índice (BAC ÷ CPI)', _s(r['eac_indice']), 'text-muted'),
              ('Variación al cierre (VAC)', _s(r['vac']), 'text-danger' if r['vac'] is not None and r['vac'] < 0
               else ''),
              ('Término estimado', r['termino_estimado'].strftime('%d/%m/%Y') if r['termino_estimado'] else '—',
               'text-danger' if r['termino_estimado'] and r['termino'] and r['termino_estimado'] > r['termino']
               else '')],
        ayuda='PV = lo programado en el cronograma y EV = lo ejecutado (avance físico), a precios del costo '
              '(presupuesto meta), del costo directo (sin el subpresupuesto de gastos generales); AC = el costo real '
              'directo (el de la proyección al cierre). SPI = EV/PV y CPI = EV/AC: menos de 1 es atraso o '
              'sobrecosto. El costo al cierre es el de la proyección por partida (el mismo en todo el sistema); el EAC '
              'por índice (BAC ÷ CPI, sin redondear) es la referencia. El término estimado es el plazo ÷ SPI.')


# ---------------------------------------------------------------- HR-06: escenarios del cronograma
@login_required
def escenarios(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        try:
            if request.POST.get('accion') == 'restaurar':
                e = get_object_or_404(EscenarioCronograma, pk=id_o_none(request.POST.get('escenario')), obra=o)
                n = g.restaurar_escenario(e, request.user)
                messages.success(request, f'El cronograma volvió a «{e.nombre}» ({n} metrados por mes); el anterior '
                                          f'quedó guardado como escenario.')
            elif request.POST.get('accion') == 'eliminar':
                e = get_object_or_404(EscenarioCronograma, pk=id_o_none(request.POST.get('escenario')), obra=o)
                e.delete()
                messages.info(request, f'Escenario «{e.nombre}» eliminado.')
            else:
                e = g.guardar_escenario(o, request.POST.get('nombre', ''), request.user,
                                        request.POST.get('descripcion', ''))
                messages.success(request, f'Escenario «{e.nombre}» guardado con el cronograma vigente.')
        except g.ErrorGestion as exc:
            messages.error(request, str(exc))
        return redirect('obras:escenarios', pk)
    lista = list(o.escenarios.select_related('creado_por'))
    por_id = {str(e.pk): e for e in lista}
    a, b = por_id.get(request.GET.get('a', '')), por_id.get(request.GET.get('b', ''))
    secciones = [{'titulo': 'Escenarios guardados', 'columnas': ['Escenario', 'Descripción', 'Guardado', 'Por',
                                                                  'Partidas', 'Inicio', 'Término', 'Costo directo S/', ''],
                  'filas': []}]
    p = g.presupuesto_ejecucion(o)
    for e in lista:
        rs = g._resumen_programa(p, e.datos) if p else {'partidas': 0, 'inicio': None, 'fin': None, 'total': D0}
        secciones[0]['filas'].append([
            e.nombre, e.descripcion or '—', timezone.localtime(e.creado).strftime('%d/%m/%Y %H:%M'),
            str(e.creado_por or '—'), rs['partidas'], _mes(rs['inicio']), _mes(rs['fin']), rs['total'],
            [{'texto': 'Comparar con el vigente', 'url': f'?a={e.pk}'},
             {'texto': 'Volver a este', 'post': '', 'campo': 'escenario', 'valor': e.pk,
              'extra': {'accion': 'restaurar'},
              'confirmar': f'¿Reemplazar el cronograma vigente por «{e.nombre}»? El vigente se guarda antes.'},
             {'texto': 'Eliminar', 'post': '', 'campo': 'escenario', 'valor': e.pk, 'extra': {'accion': 'eliminar'},
              'confirmar': f'¿Eliminar el escenario «{e.nombre}»?'}]])
    kpis = []
    if 'a' in request.GET or 'b' in request.GET:
        c = g.comparar_escenarios(o, a, b)
        if c is not None:
            na, nb = (a.nombre if a else 'Vigente'), (b.nombre if b else 'Vigente')
            secciones.append({'titulo': f'{na} frente a {nb}: costo directo programado por mes',
                              'columnas': ['Mes', f'{na} S/', f'{nb} S/', f'{na} acumulado', f'{nb} acumulado',
                                           f'{na} %', f'{nb} %'],
                              'filas': [[_mes(f['periodo']), f['a'], f['b'], f['acum_a'], f['acum_b'], _p(f['pct_a']),
                                         _p(f['pct_b'])] for f in c['filas']]})
            secciones.append({'titulo': 'Partidas que cambian de mes de término',
                              'columnas': ['Ítem', 'Partida', f'Término en {na}', f'Término en {nb}'],
                              'filas': [[x['partida'].item, x['partida'].descripcion[:60], _mes(x['fin_a']),
                                         _mes(x['fin_b'])] for x in c['cambios']]})
            kpis = [(f'Término {na}', _mes(c['a']['fin']), ''), (f'Término {nb}', _mes(c['b']['fin']), ''),
                    ('Diferencia', f'{c["meses_diferencia"]:+d} mes(es)' if c['meses_diferencia'] is not None else '—',
                     ''), (f'Costo directo {na}', _s(c['a']['total']), ''), (f'Costo directo {nb}', _s(c['b']['total']),
                                                                            '')]
    opciones = [('', 'Vigente')] + [(str(e.pk), e.nombre[:40]) for e in lista]

    class EscenarioForm(BootstrapMixin, forms.Form):
        nombre = forms.CharField(max_length=120, label='Nombre del escenario', help_text='Ej. Plan base, Con 2 '
                                                                                        'cuadrillas más')
        descripcion = forms.CharField(max_length=250, required=False, label='Descripción')

    return responder(
        request, f'Escenarios del cronograma · {o.codigo} {o.nombre}', secciones, por_seccion=True,
        form=EscenarioForm(), form_titulo='Guardar el cronograma vigente como escenario',
        selects=[('a', 'Comparar', opciones, request.GET.get('a', '')),
                 ('b', 'con', opciones, request.GET.get('b', ''))],
        botones=[('Cronograma y curva S', reverse('obras:cronograma', args=[o.pk]), 'bi-graph-up')] + _obra_botones(o),
        kpis=kpis,
        ayuda='Guarde el cronograma vigente antes de cambiarlo (importar otro o modificar metrados) y compare las '
              'alternativas mes a mes. «Volver a este» reemplaza el cronograma vigente (que se guarda antes).')


# ---------------------------------------------------------------- HR-07, HR-12, HR-14: proyección al cierre
@login_required
def proyeccion(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    c = g.control_partidas(o)
    if c is None:
        messages.error(request, 'La obra no tiene presupuesto.')
        return redirect('obras:obra', pk)
    cab = ['Ítem', 'Partida', 'Und.', 'Contractual S/', 'Presupuesto del costo S/', 'Avance', 'Valor ganado S/',
           'Costo real S/', 'CPI', 'Costo al cierre (EAC) S/', 'Margen proyectado S/', 'Margen %']

    def fila(f):
        x = f['partida']
        return [x.item, x.descripcion[:60], x.unidad, f['contractual'], f['bac'], _p(f['avance_pct']), f['ev'],
                {'texto': f'{f["ac"]:,.2f}', 'clase': 'num text-danger fw-semibold' if f['alerta'] else 'num'},
                f['cpi'] if f['cpi'] is not None else '—', f['eac'],
                {'texto': f'{f["margen"]:,.2f}', 'clase': 'num text-danger' if f['margen'] < 0 else 'num'},
                _p(f['margen_pct'])]

    t = c['totales']
    avisos = []
    if c['bajo_meta']:
        avisos.append(('danger', f'Margen proyectado al cierre {c["margen_pct"]} %: por debajo de la meta de '
                                 f'{c["margen_meta"]} %.'))
    if c['alertas']:
        avisos.append(('warning', f'{len(c["alertas"])} partida(s) con costo real mayor a su valor ganado en más del '
                                  f'{c["tolerancia"]} % (costo esperado = presupuesto del costo × avance).'))
    return responder(
        request, f'Proyección al cierre y control de costo · {o.codigo} {o.nombre}', [
            {'titulo': 'Partidas con costo sobre lo esperado', 'columnas': cab + ['Exceso S/', 'Exceso %'],
             'filas': [fila(f) + [f['exceso'], _p(f['exceso_pct'])] for f in c['alertas']]},
            {'titulo': 'Todas las partidas', 'columnas': cab, 'filas': [fila(f) for f in c['filas']] + [
                ['', 'Costos sin partida (no asignables)', '', '', '', '', '', c['sin_partida'], '', c['sin_partida'],
                 '', ''],
                ['', 'TOTAL', '', t.get('contractual', D0), t.get('bac', D0), _p(c['avance_pct']), t.get('ev', D0),
                 t.get('ac', D0), c['cpi'] or '—', t.get('eac', D0), '', '']]}],
        por_seccion=True, botones=_obra_botones(o) + [
            ('Control por partida (con gastos generales)', reverse('obras:reporte_partidas', args=[o.pk]),
             'bi-table')], avisos=avisos,
        kpis=[('Venta sin IGV', _s(c['ingreso']), ''), ('Costo directo presupuestado', _s(t.get('bac')), ''),
              ('Gastos generales del presupuesto', _s(c['gg']), ''),
              ('Costo real a la fecha (directo + GG)', _s(c['costo_real']), ''),
              ('Costo directo al cierre (EAC)', _s(t.get('eac')), ''),
              ('Margen presupuestado', _p(c['margen_presupuestado_pct']), ''),
              ('Margen proyectado', _p(c['margen_pct']), 'text-danger fw-bold' if c['bajo_meta'] else 'fw-bold'),
              ('Margen meta', _p(c['margen_meta']), '')],
        ayuda=f'Costo directo = partidas sin el subpresupuesto de gastos generales. EAC por partida = costo real × '
              f'presupuesto ÷ valor ganado (sin avance: costo real + presupuesto; terminada: el costo real). Margen '
              f'proyectado = venta sin IGV − EAC − gastos generales (los del presupuesto o los reales cargados a sus '
              f'partidas, S/ {c["gg_real"]:,.2f}, el mayor). El costo real es el mismo de la cartera y del valor '
              f'ganado. La tolerancia (OBR_TOLERANCIA_COSTO_PCT) y el margen meta (en la obra o el parámetro '
              f'OBR_MARGEN_META_PCT) están en Ajustes › Parámetros legales.')


# ---------------------------------------------------------------- HR-08: productividad
@login_required
def productividad(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    r = g.productividad(o)
    avisos = [('warning', 'La meta no tiene horas-hombre en sus análisis (mano de obra en hh): se muestra el '
                          'rendimiento real sin compararlo con el APU.')] if r['sin_apu'] and r['detalle'] else []
    return responder(
        request, f'Productividad de cuadrillas · {o.codigo} {o.nombre}', [
            {'titulo': 'Por cuadrilla', 'columnas': ['Cuadrilla', 'Horas reales', 'Horas ganadas (APU)', 'Eficiencia',
                                                     'Tendencia', 'Eficiencia por mes'],
             'filas': [[x['cuadrilla'], x['horas'], x['ganadas'], _p(x['eficiencia']), x['tendencia_texto'],
                        ' · '.join(f'{_mes(per)}: {_p(e)}' for per, e in x['tendencia'])] for x in r['cuadrillas']]},
            {'titulo': 'Por cuadrilla y partida', 'columnas': ['Cuadrilla', 'Ítem', 'Partida', 'Und.', 'Horas',
                                                               'Metrado', 'Rendimiento real (und/hh)',
                                                               'Rendimiento APU (und/hh)', 'Eficiencia'],
             'filas': [[x['cuadrilla'], x['partida'].item, x['partida'].descripcion[:50], x['partida'].unidad,
                        x['horas'], x['metrado'], x['rendimiento'] or '—', x['rendimiento_apu'] or '—',
                        _p(x['eficiencia'])] for x in r['partidas']]},
            {'titulo': 'Detalle por mes', 'columnas': ['Cuadrilla', 'Mes', 'Ítem', 'Partida', 'Horas', 'Metrado',
                                                       'Rendimiento real', 'Rendimiento APU', 'Eficiencia'],
             'filas': [[x['cuadrilla'], _mes(x['periodo']), x['partida'].item, x['partida'].descripcion[:50],
                        x['horas'], x['metrado'], x['rendimiento'] or '—', x['rendimiento_apu'] or '—',
                        _p(x['eficiencia'])] for x in r['detalle']]},
            {'titulo': 'Avance sin horas de tareo (no cuenta en la productividad)',
             'nota': 'Avance físico de semanas en que la partida no tiene horas en el tareo: lo ejecutó un '
                     'subcontrato o equipo, o falta registrar el tareo.',
             'columnas': ['Semana del', 'Ítem', 'Partida', 'Und.', 'Metrado'],
             'filas': [[x['semana'].strftime('%d/%m/%Y'), x['partida'].item, x['partida'].descripcion[:50],
                        x['partida'].unidad, x['metrado']] for x in r['sin_horas']]}],
        por_seccion=True, botones=[('Tareo', reverse('obras:tareo', args=[o.pk]), 'bi-person-lines-fill')]
        + _obra_botones(o), avisos=avisos,
        ayuda='Horas del tareo (normales y extra) por cuadrilla y partida; el metrado del avance físico de cada semana '
              'se reparte entre las cuadrillas según sus horas en esa partida esa semana (el avance de semanas sin '
              'horas se lista aparte). Eficiencia = horas del APU × metrado ÷ '
              'horas reales: más de 100 % rinde mejor que el análisis.')


# ---------------------------------------------------------------- HR-09: pronóstico de compras
@login_required
def pronostico_compras(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    try:
        meses = min(max(int(request.GET.get('meses') or 4), 1), 12)
    except ValueError:
        meses = 4
    r = g.pronostico_compras(o, meses)
    if r is None:
        messages.error(request, 'La obra no tiene presupuesto.')
        return redirect('obras:obra', pk)
    avisos = [('warning', 'El presupuesto no tiene cronograma: impórtelo en «Cronograma y curva S».')] \
        if r['sin_cronograma'] else []
    botones = _obra_botones(o)
    if r['meta'] is not None and r['meta'].tipo == 'META':
        botones = [('Control y requerimientos', reverse('obras:control', args=[r['meta'].pk]), 'bi-clipboard-data')] \
            + botones
    return responder(
        request, f'Pronóstico de compras por cronograma · {o.codigo} {o.nombre}', [{
            'columnas': ['Material', 'Producto', 'Und.'] + [_mes(c) for c in r['columnas']] + [
                'Total', 'Stock en obra', 'Pedido por llegar', 'Falta pedir', 'Monto estimado S/', 'Pedir desde'],
            'filas': [[f['recurso'].nombre[:50], f['producto'].codigo if f['producto'] else 'sin vincular',
                       f['unidad']] + f['meses'] + [
                f['total'], f['stock'], f['por_llegar'],
                {'texto': f'{f["faltante"]:,.2f}', 'clase': 'num fw-semibold' if f['faltante'] else 'num'},
                f['monto'], f['pedir_desde'].strftime('%d/%m/%Y') if f['pedir_desde'] else '—'] for f in r['filas']]}],
        selects=[('meses', 'Meses', [(str(n), f'{n} meses') for n in (2, 3, 4, 6, 9, 12)], str(meses))],
        botones=botones, avisos=avisos,
        kpis=[('Materiales', len(r['filas']), ''),
              ('Por pedir', sum(1 for f in r['filas'] if f['faltante']), 'text-warning'),
              ('Monto estimado por pedir', _s(sum((f['monto'] for f in r['filas']), D0)), '')],
        ayuda=f'Metrado programado de cada mes (el del mes en curso menos lo ya ejecutado) × aporte de los materiales '
              f'en el análisis de la meta; se descuenta el stock del almacén de obra y lo pedido por llegar. «Pedir '
              f'desde» = {g.DIAS_ANTICIPACION} días antes del mes en que el stock deja de alcanzar. Los materiales sin '
              f'producto vinculado no se pueden requerir (Presupuestos › Vincular recursos).')


# ---------------------------------------------------------------- HR-10: comprar o alquilar
@login_required
def comprar_alquilar(request):
    try:
        meses = min(max(int(request.GET.get('meses') or 12), 1), 36)
    except ValueError:
        meses = 12
    r = g.comprar_o_alquilar(meses)
    return responder(request, 'Comprar o alquilar equipos', [
        {'titulo': 'Equipos propios: costo hora frente al alquiler',
         'columnas': ['Equipo', 'Horas (partes)', 'Horas al mes', 'Depreciación al mes S/', 'Base', 'Combustible S/',
                      'Gastos S/', 'Mantenimiento S/', 'Costo hora propio S/', 'Tarifa de alquiler S/', 'Fuente',
                      'Ahorro por hora S/', 'Ahorro en el periodo S/', 'Horas al mes para que convenga', 'Decisión'],
         'filas': [[{'texto': str(f['equipo']), 'url': reverse('flota:equipo', args=[f['equipo'].pk])},
                    f['horas'], f['horas_mes'], f['depreciacion_mes'], f['fuente_dep'], f['combustible'], f['gastos'],
                    f['mantenimiento'], f['costo_hora'] if f['costo_hora'] is not None else '—', f['tarifa'] or '—',
                    f['fuente_tarifa'] or '—', f['ahorro_hora'] if f['ahorro_hora'] is not None else '—',
                    f['ahorro_periodo'] if f['ahorro_periodo'] is not None else '—', f['equilibrio'] or '—',
                    {'texto': f['decision'], 'clase': 'fw-semibold text-success' if f['decision'].startswith(
                        'Conviene tenerlo') else 'fw-semibold text-warning' if f['decision'].startswith(
                        'Conviene alquilarlo') else 'text-muted'}] for f in r['filas']]},
        {'titulo': 'Equipos alquilados: horas y gasto', 'columnas': [
            'Equipo', 'Horas', 'Gasto S/', 'Meses con uso', 'Tarifa promedio S/', 'Gasto al mes S/', 'Proveedores'],
         'filas': [[a['equipo'], a['horas'], a['costo'], a['meses'], a['tarifa'] or '—', a['gasto_mes'],
                    a['proveedores'] or '—'] for a in r['alquilados']]}], por_seccion=True,
        selects=[('meses', 'Periodo', [(str(n), f'Últimos {n} meses') for n in (6, 12, 24, 36)], str(meses))],
        ayuda='Costo hora propio = (depreciación del activo fijo del equipo o valor de reposición en 5 años + '
              'combustible + gastos + mantenimiento del periodo) ÷ horas de los partes de equipo. La tarifa de '
              'alquiler sale de la ficha del equipo (Flota › Equipos) o del promedio pagado en partes de alquiler. '
              '«Horas al mes para que convenga» = depreciación ÷ (tarifa − costo variable por hora).')


# ---------------------------------------------------------------- HR-11: subcontratistas
@login_required
def subcontrato_evaluar(request, pk):
    sc = get_object_or_404(Subcontrato, pk=pk)
    if request.method == 'POST':
        try:
            e = g.evaluar_subcontrato(sc, request.POST.get('plazo'), request.POST.get('calidad'),
                                      request.POST.get('seguridad'), request.user, request.POST.get('comentario', ''))
            messages.success(request, f'Evaluación guardada: {e.puntaje} de 5.')
        except g.ErrorGestion as exc:
            messages.error(request, str(exc))
    return redirect('obras:subcontrato', pk)


@login_required
def subcontratistas(request):
    filas = g.historial_subcontratistas()
    evaluaciones = []
    for p in filas:
        for e in sorted(p['evaluaciones'], key=lambda e: e.fecha, reverse=True):
            evaluaciones.append([p['proveedor'].nombre, {'texto': e.subcontrato.numero,
                                                         'url': reverse('obras:subcontrato', args=[e.subcontrato_id])},
                                 e.subcontrato.obra.codigo, e.fecha.strftime('%d/%m/%Y'), e.plazo, e.calidad,
                                 e.seguridad, e.puntaje, e.comentario or '—'])

    def clase(v):
        return 'num text-danger' if v is not None and v < 3 else 'num text-success' if v is not None and v >= 4 \
            else 'num'

    return responder(request, 'Evaluación de subcontratistas', [
        {'titulo': 'Historial por subcontratista', 'columnas': [
            'Subcontratista', 'Subcontratos', 'Monto S/', 'Obras', 'Evaluaciones', 'Plazo', 'Calidad', 'Seguridad',
            'Puntaje', 'Última evaluación'],
         'filas': [[p['proveedor'].nombre, p['subcontratos'], p['monto'], p['obras'], p['n'], p['plazo'] or '—',
                    p['calidad'] or '—', p['seguridad'] or '—',
                    {'texto': p['puntaje'] if p['puntaje'] is not None else 'sin evaluar', 'clase': clase(p['puntaje'])},
                    p['ultima'].fecha.strftime('%d/%m/%Y') if p['ultima'] else '—'] for p in filas]},
        {'titulo': 'Evaluaciones', 'columnas': ['Subcontratista', 'Subcontrato', 'Obra', 'Fecha', 'Plazo', 'Calidad',
                                                'Seguridad', 'Puntaje', 'Comentario'], 'filas': evaluaciones}],
        por_seccion=True,
        ayuda='Cada subcontrato vigente o liquidado se evalúa en su ficha (Ejecución › Subcontratos) de 1 a 5 en '
              'plazo, calidad y seguridad. Úselo al elegir subcontratistas: menos de 3 es una alerta.')


# ---------------------------------------------------------------- HR-13: rentabilidad
@login_required
def rentabilidad(request):
    from core.utils import id_o_none
    r = g.rentabilidad(request.GET.get('tipo') or None, id_o_none(request.GET.get('cliente')))
    cab = ['Obras', 'Ingreso (valorizado) S/', 'Costo real S/', 'Margen S/', 'Margen %']

    def grupo(lista):
        return [[x['clave'], x['obras'], x['ingreso'], x['costo'],
                 {'texto': f'{x["margen"]:,.2f}', 'clase': 'num text-danger' if x['margen'] < 0 else 'num'},
                 _p(x['margen_pct'])] for x in lista]

    return responder(request, 'Rentabilidad por cliente y tipo de proyecto', [
        {'titulo': 'Por cliente', 'columnas': ['Cliente'] + cab, 'filas': grupo(r['clientes'])},
        {'titulo': 'Por tipo de proyecto', 'columnas': ['Tipo'] + cab, 'filas': grupo(r['tipos'])},
        {'titulo': 'Por obra', 'columnas': ['Obra', 'Cliente', 'Tipo', 'Estado', 'Ingreso S/', 'Costo real S/',
                                            'Margen S/', 'Margen %', 'Margen proyectado al cierre',
                                            'Gastos contables con su centro S/ (referencia)'],
         'filas': [[{'texto': f'{f["obra"].codigo} {f["obra"].nombre[:40]}',
                     'url': reverse('obras:ejecucion', args=[f['obra'].pk])},
                    f['obra'].cliente.nombre[:30] if f['obra'].cliente_id else '—',
                    f['obra'].get_tipo_display().split(' (')[0], f['obra'].get_estado_display(), f['ingreso'],
                    f['costo'], f['margen'], _p(f['margen_pct']), _p(f['proyectado_pct']), f['contable']]
                   for f in r['obras']]}],
        por_seccion=True, selects=_selects_obra_tipo(request),
        ayuda='Ingreso = lo valorizado sin IGV (o las ventas con el centro de costo de la obra); costo real = '
              'planillas, partes de equipo, subcontratos y materiales consumidos (el mismo de la cartera, el valor '
              'ganado y la proyección); solo si la obra no tiene ninguno, los gastos contables con su centro de costo. '
              'La última columna muestra esos gastos contables como referencia. El margen proyectado sale de la '
              'proyección al cierre.')


# ---------------------------------------------------------------- HR-15 y HR-17: flujo de caja del proyecto
@login_required
def flujo_proyecto(request, pk):
    o = get_object_or_404(Obra.objects.select_related('cliente'), pk=pk)
    try:
        meses = min(max(int(request.GET.get('meses') or 6), 1), 24)
    except ValueError:
        meses = 6
    r = g.flujo_proyecto(o, meses)
    avisos = []
    if r['minimo']:
        avisos.append(('warning', f'El flujo acumulado del proyecto llega a S/ {r["minimo"]["acumulado"]:,.2f} en '
                                  f'{_mes(r["minimo"]["periodo"])}: necesitará financiarlo.'))
    grafico = {'tipo': 'bar', 'etiquetas': [_mes(f['periodo']) for f in r['filas']],
               'series': [{'nombre': 'Ingresos', 'datos': [_f(f['ingresos']) for f in r['filas']]},
                          {'nombre': 'Egresos', 'datos': [_f(f['egresos']) for f in r['filas']]},
                          {'nombre': 'Acumulado', 'tipo': 'line', 'datos': [_f(f['acumulado']) for f in r['filas']]}]}
    return responder(
        request, f'Flujo de caja del proyecto · {o.codigo} {o.nombre}', [
            {'titulo': 'Flujo mensual (real y proyectado)', 'columnas': ['Mes', 'Tipo', 'Ingresos S/', 'Egresos S/',
                                                                          'Neto S/', 'Acumulado S/'],
             'filas': [[_mes(f['periodo']), f['tipo'], f['ingresos'], f['egresos'],
                        {'texto': f'{f["neto"]:,.2f}', 'clase': 'num text-danger' if f['neto'] < 0 else 'num'},
                        {'texto': f'{f["acumulado"]:,.2f}', 'clase': 'num text-danger' if f['acumulado'] < 0
                         else 'num'}] for f in r['filas']]},
            {'titulo': 'Valorizaciones proyectadas por el cronograma', 'columnas': [
                'Mes', 'Valorización sin IGV S/', 'Amortización de adelantos S/', 'Cobro neto (con IGV) S/',
                'Fecha probable de cobro'],
             'filas': [[_mes(x['periodo']), x['valorizacion'], x['amortizacion'], x['neto'],
                        x['fecha_cobro'].strftime('%d/%m/%Y')] for x in r['valorizaciones']]}],
        por_seccion=True, grafico=grafico if r['filas'] else None, avisos=avisos, botones=_obra_botones(o),
        selects=[('meses', 'Proyectar', [(str(n), f'{n} meses') for n in (3, 6, 9, 12, 18, 24)], str(meses))],
        ayuda='Real: cobros de las facturas de la obra (valorizaciones, adelantos y las emitidas con su centro de '
              'costo) y pagos con su centro de costo, de sus compras o de sus planillas. Proyectado: facturas por '
              'cobrar y por pagar en su vencimiento, valorizaciones del cronograma (fin de mes + aprobación + crédito '
              'del cliente, con IGV, menos amortización y fondo de garantía) y el costo programado a precio de la meta. '
              'Las valorizaciones proyectadas también entran al flujo de caja de la empresa (Finanzas).')


# ---------------------------------------------------------------- SRV-05: consorcio
class SocioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        from .models import SocioConsorcio
        model = SocioConsorcio
        fields = ['tercero', 'nombre', 'ruc', 'participacion_pct', 'es_la_empresa', 'operador']

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.forms import remoto
        self.obra = obra
        remoto(self.fields['tercero'], 'terceros')
        self.fields['tercero'].required = False

    def clean(self):
        d = super().clean()
        if not d.get('tercero') and not (d.get('nombre') or '').strip():
            raise forms.ValidationError('Elija el socio en contactos o escriba su razón social.')
        pct = d.get('participacion_pct')
        if pct is not None and not 0 < pct <= 100:
            self.add_error('participacion_pct', 'La participación va de 0 a 100 %.')
        ruc = (d['tercero'].numero_doc if d.get('tercero') else (d.get('ruc') or '')).strip()
        if not d.get('tercero') and ruc and not (ruc.isdigit() and len(ruc) == 11):
            self.add_error('ruc', 'El RUC tiene 11 dígitos.')
        if self.obra is None:
            return d
        # SRV-16: participaciones hasta 100 %, un RUC una sola vez, una sola «nuestra empresa» y un solo operador
        otros = self.obra.socios.select_related('tercero')
        suma = sum(s.participacion_pct for s in otros)
        if pct is not None and 0 < pct <= 100 and suma + pct > 100:
            self.add_error('participacion_pct', f'Los socios ya suman {suma:g} %: el máximo para este socio es '
                                                f'{100 - suma:g} %.')
        for s in otros:
            if ruc and ruc == (s.tercero.numero_doc if s.tercero_id else s.ruc):
                self.add_error('ruc' if not d.get('tercero') else 'tercero', f'El RUC {ruc} ya es socio ({s}).')
            elif d.get('tercero') and s.tercero_id == d['tercero'].pk:
                self.add_error('tercero', f'{s} ya es socio del consorcio.')
        if d.get('es_la_empresa') and any(s.es_la_empresa for s in otros):
            self.add_error('es_la_empresa', 'Ya hay un socio marcado como «nuestra empresa».')
        if d.get('operador') and any(s.operador for s in otros):
            self.add_error('operador', 'Ya hay un operador del consorcio.')
        return d


@login_required
def consorcio(request, pk):
    from .models import SocioConsorcio
    o = get_object_or_404(Obra.objects.select_related('cliente'), pk=pk)
    form = SocioForm(obra=o)
    if request.method == 'POST':
        if request.POST.get('accion') == 'quitar':
            SocioConsorcio.objects.filter(pk=id_o_none(request.POST.get('socio')), obra=o).delete()
            return redirect('obras:consorcio', pk)
        form = SocioForm(request.POST, obra=o)
        if form.is_valid():
            s = form.save(commit=False)
            s.obra = o
            s.save()
            messages.success(request, f'Socio {s} agregado ({s.participacion_pct} %).')
            return redirect('obras:consorcio', pk)
    desde, hasta = _fecha(request.GET.get('desde')), _fecha(request.GET.get('hasta'))
    r = g.consorcio(o, desde, hasta)
    avisos = []
    if desde and hasta and desde > hasta:  # OBR-41
        avisos.append(('warning', f'El rango está invertido: «desde» ({desde:%d/%m/%Y}) es posterior a «hasta» '
                                  f'({hasta:%d/%m/%Y}); no hay movimientos en ese rango.'))
    if r['socios'] and r['suma'] != 100:
        avisos.append(('warning', f'Las participaciones suman {r["suma"]} %: deben sumar 100 %.'))
    if r['socios'] and not any(s.es_la_empresa for s in r['socios']):
        avisos.append(('warning', 'Marque cuál de los socios es nuestra empresa.'))
    lic = getattr(o, 'licitacion', None)
    if not r['socios'] and lic is not None and lic.consorcio:
        avisos.append(('info', f'La licitación se postuló en consorcio con «{lic.consorciados}» y nuestra participación '
                               f'es {lic.participacion_pct} %: registre aquí a cada socio con su %.'))
    filas = [[str(f['socio']) + (' (nuestra empresa)' if f['socio'].es_la_empresa else '') +
              (' · operador' if f['socio'].operador else ''),
              f['socio'].tercero.numero_doc if f['socio'].tercero_id else f['socio'].ruc or '—', _p(f['pct']),
              f['ventas'], f['igv_ventas'], f['compras'], f['igv_compras'], f['igv_neto'], f['costo'], f['resultado'],
              {'texto': 'Quitar', 'post': '', 'campo': 'socio', 'valor': f['socio'].pk, 'extra': {'accion': 'quitar'},
               'confirmar': f'¿Quitar a {f["socio"]} del consorcio?'}] for f in r['filas']]
    v, c = r['ventas'], r['compras']
    return responder(
        request, f'Consorcio · {o.codigo} {o.nombre}', [{
            'titulo': 'Atribución por socio', 'columnas': [
                'Socio', 'RUC', 'Participación', 'Ventas sin IGV S/', 'IGV de ventas S/', 'Compras sin IGV S/',
                'IGV de compras S/', 'IGV neto S/', 'Costo real S/', 'Resultado S/', ''], 'filas': filas}],
        form=form, form_titulo='Agregar un socio del consorcio', rango=(desde, hasta), avisos=avisos,
        botones=_obra_botones(o),
        kpis=[('Ventas sin IGV', _s(v['base_pen']), ''), ('IGV de ventas', _s(v['igv_pen']), ''),
              ('Compras sin IGV', _s(c['base_pen']), ''), ('IGV de compras', _s(c['igv_pen']), ''),
              ('Costo real', _s(r['costo']), ''), ('Participaciones', _p(r['suma']), '')],
        ayuda=f'Ventas: facturas de valorizaciones y adelantos de la obra y las emitidas con su centro de costo '
              f'({r["docs_ventas"]}); compras: las registradas con su centro de costo ({r["docs_compras"]}); costo real: '
              f'el de la proyección. Cada socio recibe su % (atribución de ingresos, costos e IGV). Si el consorcio '
              f'lleva contabilidad independiente, este reporte es la base para registrarla; confirme el tratamiento '
              f'con su contador.')


# ---------------------------------------------------------------- HR-20: predicción
@login_required
def prediccion(request):
    r = g.prediccion()
    clase = {'Alto': 'text-danger fw-semibold', 'Medio': 'text-warning fw-semibold', 'Bajo': 'text-success'}
    tipos = dict(Obra.TIPOS)
    modelos = []
    for t, m in r['modelos'].items():
        sc, at = m['sobrecosto'], m['atraso']
        modelos.append([tipos.get(t, 'Todas las obras').split(' (')[0], sc[2], _p(sc[0]), _p(sc[1]), at[2], _p(at[0]),
                        _p(at[1]), _p(m['probabilidad'])])
    avisos = [] if r['suficiente'] else [(
        'warning', f'Datos insuficientes: hay {len(r["historia"])} obra(s) terminada(s) y el modelo necesita al menos '
                   f'{r["min_historia"]}. Mientras tanto se usan solo los índices de cada obra (desde 20 % de avance).')]
    return responder(request, 'Modelos predictivos: atraso y sobrecosto', [
        {'titulo': 'Obras en ejecución', 'columnas': ['Obra', 'Tipo', 'Avance', 'CPI', 'SPI', 'Sobrecosto esperado',
                                                      'Atraso esperado', 'Riesgo', 'Base'],
         'filas': [[{'texto': f'{f["obra"].codigo} {f["obra"].nombre[:40]}',
                     'url': reverse('obras:valor_ganado', args=[f['obra'].pk])},
                    f['obra'].get_tipo_display().split(' (')[0], _p(f['avance']), f['cpi'] or '—', f['spi'] or '—',
                    _p(f['sobrecosto']), _p(f['atraso']),
                    {'texto': f['riesgo'] or 'datos insuficientes', 'clase': clase.get(f['riesgo'], 'text-muted')},
                    f['base']] for f in r['filas']]},
        {'titulo': 'Modelo: obras terminadas por tipo', 'columnas': [
            'Tipo', 'Obras (costo)', 'Sobrecosto promedio', 'Desviación', 'Obras (plazo)', 'Atraso promedio',
            'Desviación', 'Obras con sobrecosto'], 'filas': modelos},
        {'titulo': 'Historia: obras terminadas', 'columnas': ['Obra', 'Tipo', 'Estado', 'Sobrecosto real',
                                                              'Atraso real'],
         'filas': [[f'{h["obra"].codigo} {h["obra"].nombre[:40]}', h['obra'].get_tipo_display().split(' (')[0],
                    h['obra'].get_estado_display(), _p(h['sobrecosto']), _p(h['atraso'])] for h in r['historia']]}],
        por_seccion=True, avisos=avisos,
        ayuda='Modelo simple y explicable: el sobrecosto y el atraso promedio de las obras terminadas del mismo tipo '
              '(o de todas, si del tipo hay menos de tres) se combinan con los índices de la obra (1/CPI − 1 y '
              '1/SPI − 1) en proporción a su avance. Riesgo alto: más de 10 % de sobrecosto o 20 % de atraso.')
