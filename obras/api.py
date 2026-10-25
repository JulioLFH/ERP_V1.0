"""API v1 de Obras para BI (HR-19): proyectos con sus indicadores, partidas con su control, costos por mes,
valorizaciones y cobranza de las obras. Solo lectura, paginadas y en CSV con formato=csv (Power BI, Excel, Looker)."""
from core.api import _pagina, api, error, fecha_param, respuesta

from . import gestion as g
from .models import Obra, Valorizacion


def _obra_param(request):
    pk = (request.GET.get('obra') or '').strip()
    if not pk.isdigit():
        return None
    return Obra.objects.filter(pk=int(pk)).first()


def _s_obra(o, indicadores=False):
    datos = {'id': o.pk, 'codigo': o.codigo, 'nombre': o.nombre, 'tipo': o.tipo, 'estado': o.estado,
             'cliente': {'id': o.cliente_id, 'nombre': o.cliente.nombre if o.cliente_id else None},
             'moneda': o.moneda, 'monto_contratado': o.monto_contratado, 'plazo_dias': o.plazo_dias,
             'fecha_inicio': o.fecha_inicio, 'centro_costo': o.centro_costo.codigo if o.centro_costo_id else None}
    if indicadores:
        ev = g.valor_ganado(o) if o.tipo != 'SERVICIO' else None
        c = g.control_partidas(o) if o.tipo != 'SERVICIO' else None
        ingreso, costo = g.resultado_obra(o)
        datos.update({'valorizado': ingreso, 'costo_real': costo, 'avance_pct': ev['avance_pct'] if ev else None,
                      'spi': ev['spi'] if ev else None, 'cpi': ev['cpi'] if ev else None,
                      'eac': c['totales'].get('eac') if c else None,
                      'margen_proyectado_pct': c['margen_pct'] if c else None,
                      'margen_meta_pct': g.margen_meta(o),
                      'termino_estimado': ev['termino_estimado'] if ev else None})
    return datos


@api('obras')
def proyectos(request):
    qs = Obra.objects.select_related('cliente', 'centro_costo').order_by('codigo')
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    if request.GET.get('tipo'):
        qs = qs.filter(tipo=request.GET['tipo'])
    indicadores = request.GET.get('indicadores', 'true') != 'false'
    return respuesta(_pagina(request, qs, lambda o: _s_obra(o, indicadores)))


@api('obras')
def partidas(request):
    o = _obra_param(request)
    if o is None:
        return error('Indique la obra: obra=<id> (ver /api/v1/obras/proyectos/).')
    c = g.control_partidas(o)
    if c is None:
        return error('La obra no tiene presupuesto.', 404)
    filas = [{'obra_id': o.pk, 'item': f['partida'].item, 'descripcion': f['partida'].descripcion,
              'unidad': f['partida'].unidad, 'metrado': f['partida'].metrado, 'precio': f['partida'].precio,
              'contractual': f['contractual'], 'presupuesto_costo': f['bac'], 'avance': f['avance'],
              'avance_pct': f['avance_pct'], 'valor_ganado': f['ev'], 'costo_real': f['ac'], 'cpi': f['cpi'],
              'costo_al_cierre': f['eac'], 'margen_proyectado': f['margen'], 'alerta_costo': f['alerta']}
             for f in c['filas']]
    return respuesta(_pagina(request, filas, lambda x: x))


@api('obras')
def costos(request):
    o = _obra_param(request)
    obras = [o] if o is not None else list(Obra.objects.exclude(estado__in=('LICITACION', 'NO_ADJUDICADA')))
    desde, hasta = fecha_param(request, 'desde'), fecha_param(request, 'hasta')
    filas = []
    for obra in obras:
        meses, fuente = g.costos_por_mes(obra)
        for per, m in meses.items():
            if desde and per < f'{desde:%Y%m}' or hasta and per > f'{hasta:%Y%m}':
                continue
            filas.append({'obra_id': obra.pk, 'obra': obra.codigo, 'periodo': per, 'materiales': m['mat'],
                          'mano_obra': m['mo'], 'equipos': m['eq'], 'subcontratos': m['sub'], 'total': m['total'],
                          'fuente': fuente})
    return respuesta(_pagina(request, filas, lambda x: x))


@api('obras')
def valorizaciones(request):
    qs = Valorizacion.objects.select_related('obra', 'factura').order_by('obra__codigo', 'numero')
    o = _obra_param(request)
    if o is not None:
        qs = qs.filter(obra=o)
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    return respuesta(_pagina(request, qs, lambda v: {
        'id': v.pk, 'obra_id': v.obra_id, 'obra': v.obra.codigo, 'numero': v.numero, 'periodo': v.periodo,
        'estado': v.estado, 'costo_directo': v.costo_directo, 'gastos_generales': v.gastos_generales,
        'utilidad': v.utilidad, 'subtotal': v.subtotal, 'amortizacion': v.amort_directo + v.amort_materiales,
        'reintegro': v.reintegro - v.deduccion_reintegro, 'retencion_garantia': v.retencion_garantia,
        'total_factura': v.total_factura,
        'factura': v.factura.numero_completo if v.factura_id else None}))


@api('obras')
def cobranza(request):
    """Comprobantes de las obras con saldo por cobrar (valorizaciones, adelantos y ventas con su centro de costo)."""
    o = _obra_param(request)
    obras = [o] if o is not None else list(Obra.objects.exclude(estado__in=('LICITACION', 'NO_ADJUDICADA')))
    filas = []
    for obra in obras:
        ventas = g._documentos_de_obra(obra)
        for v in ventas.model.objects.con_saldos().cobrables().filter(pk__in=ventas.values('pk'),
                                                                      estado='REGISTRADO').select_related('tercero'):
            if v.saldo > 0:
                filas.append({'obra_id': obra.pk, 'obra': obra.codigo, 'venta_id': v.pk, 'numero': v.numero_completo,
                              'cliente': v.tercero.nombre, 'fecha_emision': v.fecha_emision,
                              'fecha_vencimiento': v.fecha_vencimiento, 'moneda': v.moneda, 'total': v.total,
                              'saldo': v.saldo, 'saldo_pen': v.saldo_pen})
    return respuesta(_pagina(request, filas, lambda x: x))


@api('obras')
def proyecto(request, pk):
    o = Obra.objects.select_related('cliente', 'centro_costo').filter(pk=pk).first()
    if o is None:
        return error('Proyecto no encontrado.', 404)
    datos = _s_obra(o, True)
    datos['flujo'] = g.flujo_proyecto(o)['filas'] if o.tipo != 'SERVICIO' else []
    return respuesta(datos)
