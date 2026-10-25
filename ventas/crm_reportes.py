"""Reportes del CRM (v1.81): conversión por etapa, ganadas y perdidas por motivo, por origen y por vendedor, ciclo de
venta, pronóstico por mes de cierre y oportunidades estancadas (CRM-05); metas y avance (CRM-08); comisiones sobre
ventas nacidas en una oportunidad (CRM-14) y clientes sin compras (CRM-12). Todo exportable a Excel."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.utils import fmt_fecha

from . import crm_servicios as srv
from .models import MetaVenta, Oportunidad, Venta

D0 = Decimal('0')
FLUJO = ['PROSPECTO', 'CALIFICADO', 'PROPUESTA', 'NEGOCIACION', 'GANADA']


def _rango(request, meses=12):
    hoy = timezone.localdate()
    try:
        desde = date.fromisoformat(request.GET.get('desde') or '')
    except ValueError:
        desde = (hoy.replace(day=1) - timedelta(days=31 * (meses - 1))).replace(day=1)
    try:
        hasta = date.fromisoformat(request.GET.get('hasta') or '')
    except ValueError:
        hasta = hoy
    return desde, hasta


def _pct(a, b):
    return f'{a * 100 / b:.1f}%' if b else '—'


def _nombre(u):
    return (u.get_full_name() or u.username) if u else 'Sin responsable'


def _responder(request, titulo, secciones, **extra):
    """Varias tablas en una página; cada una con su Excel (?formato=excel&seccion=N)."""
    from .crm_tabla import responder
    return responder(request, titulo, secciones, por_seccion=True, **extra)


@login_required
def reportes(request):
    desde, hasta = _rango(request)
    nombres = dict(srv.etapas())
    ops = list(srv.visibles(request.user, Oportunidad.objects.filter(creado__date__range=[desde, hasta]))
               .select_related('responsable').prefetch_related('cambios'))
    # 1. conversión por etapa: etapas alcanzadas (historial, etapa actual y las anteriores del flujo)
    alcanzadas = defaultdict(int)
    for o in ops:
        hitos = {c.a for c in o.cambios.all()} | {o.etapa}
        maximo = max((FLUJO.index(e) for e in hitos if e in FLUJO), default=0)
        for i in range(maximo + 1):
            alcanzadas[FLUJO[i]] += 1
    base = alcanzadas['PROSPECTO'] or len(ops)
    conversion = [[nombres[e], alcanzadas[e], _pct(alcanzadas[e], base),
                   _pct(alcanzadas[e], alcanzadas[FLUJO[i - 1]]) if i else '100.0%'] for i, e in enumerate(FLUJO)]
    perdidas = [o for o in ops if o.etapa == 'PERDIDA']
    conversion.append([nombres['PERDIDA'], len(perdidas), _pct(len(perdidas), base), '—'])
    # 2. ganadas y perdidas por motivo
    por_motivo = defaultdict(lambda: [0, D0, defaultdict(int)])
    for o in perdidas:
        m = por_motivo[o.get_motivo_perdida_tipo_display() or 'Sin motivo']
        m[0] += 1
        m[1] += o.monto
        if o.competidor:
            m[2][o.competidor] += 1
    motivos = [[k, v[0], v[1], ', '.join(f'{c} ({n})' for c, n in sorted(v[2].items(), key=lambda x: -x[1])[:3])]
               for k, v in sorted(por_motivo.items(), key=lambda x: -x[1][0])]

    # 3 y 4. por origen y por vendedor
    def agrupar(clave):
        g = defaultdict(lambda: {'n': 0, 'ganadas': 0, 'perdidas': 0, 'monto': D0})
        for o in ops:
            x = g[clave(o)]
            x['n'] += 1
            if o.etapa == 'GANADA':
                x['ganadas'] += 1
                x['monto'] += o.monto
            elif o.etapa == 'PERDIDA':
                x['perdidas'] += 1
        return [[k, x['n'], x['ganadas'], x['perdidas'], _pct(x['ganadas'], x['ganadas'] + x['perdidas']), x['monto']]
                for k, x in sorted(g.items(), key=lambda i: -i[1]['monto'])]
    por_origen = agrupar(lambda o: o.get_origen_display() if o.origen else 'Sin origen')
    por_vendedor = agrupar(lambda o: _nombre(o.responsable))
    # 5. ciclo de venta (días de la creación al cierre de las ganadas)
    ciclos = [(o.cerrada_en - o.creado.date()).days for o in ops if o.etapa == 'GANADA' and o.cerrada_en]
    ciclo = [['Oportunidades ganadas con fecha de cierre', len(ciclos)],
             ['Ciclo promedio (días)', round(sum(ciclos) / len(ciclos)) if ciclos else '—'],
             ['Ciclo más corto (días)', min(ciclos) if ciclos else '—'],
             ['Ciclo más largo (días)', max(ciclos) if ciclos else '—']]
    # 6. pronóstico por mes de cierre (todas las abiertas visibles)
    abiertas = list(srv.visibles(request.user, Oportunidad.objects.filter(etapa__in=Oportunidad.ABIERTAS)))
    meses = defaultdict(lambda: [0, D0, D0])
    for o in abiertas:
        clave = o.fecha_cierre.strftime('%Y-%m') if o.fecha_cierre else 'Sin fecha'
        meses[clave][0] += 1
        meses[clave][1] += o.monto
        meses[clave][2] += o.ponderado
    pronostico = [[k, v[0], v[1], v[2].quantize(Decimal('0.01'))] for k, v in sorted(meses.items())]
    # 7. estancadas
    limite = timezone.now() - timedelta(days=int(srv.parametro('CRM_DIAS_ESTANCADA')))
    estancadas = [[{'texto': o.nombre, 'url': reverse('crm:oportunidad', args=[o.pk])}, o.cliente,
                   nombres.get(o.etapa), o.monto, _nombre(o.responsable), fmt_fecha(o.actualizado.date()),
                   (timezone.now() - o.actualizado).days]
                  for o in sorted(abiertas, key=lambda x: x.actualizado) if o.actualizado < limite]
    secciones = [
        {'titulo': 'Conversión por etapa', 'columnas': ['Etapa', 'Oportunidades que la alcanzaron',
                                                        '% del total', '% de la etapa anterior'],
         'filas': conversion},
        {'titulo': 'Perdidas por motivo', 'columnas': ['Motivo', 'Oportunidades', 'Monto S/', 'Competidores'],
         'filas': motivos},
        {'titulo': 'Por origen', 'columnas': ['Origen', 'Oportunidades', 'Ganadas', 'Perdidas', 'Tasa de éxito',
                                              'Ganado S/'], 'filas': por_origen},
        {'titulo': 'Por vendedor', 'columnas': ['Vendedor', 'Oportunidades', 'Ganadas', 'Perdidas',
                                                'Tasa de éxito', 'Ganado S/'], 'filas': por_vendedor},
        {'titulo': 'Ciclo de venta', 'columnas': ['Indicador', 'Valor'], 'filas': ciclo},
        {'titulo': 'Pronóstico por mes de cierre (abiertas)', 'columnas': ['Mes', 'Oportunidades', 'Monto S/',
                                                                         'Ponderado S/'], 'filas': pronostico},
        {'titulo': f'Estancadas (sin movimiento más de {int(srv.parametro("CRM_DIAS_ESTANCADA"))} días)',
         'columnas': ['Oportunidad', 'Cliente', 'Etapa', 'Monto S/', 'Vendedor', 'Último movimiento', 'Días'],
         'filas': estancadas}]
    return _responder(request, 'Reportes del embudo', secciones, rango=(desde, hasta),
                      ayuda=f'Oportunidades creadas del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y} '
                            f'({len(ops)}); el pronóstico y las estancadas son de todas las abiertas.')


def _usuarios_de_meta(m):
    if m.vendedor_id:
        return [m.vendedor]
    return list(m.equipo.miembros.all()) + [m.equipo.jefe]


def _facturado(usuarios, desde, hasta):
    """Ventas (sin IGV, en soles) del periodo de los vendedores: por su nombre en el comprobante o porque nacieron
    de una oportunidad de la que son responsables."""
    from core.models import PerfilUsuario
    nombres, ids = set(), set()
    for u in usuarios:
        ids.add(u.pk)
        nombres |= {n for n in (u.username, u.get_full_name()) if n}
    nombres |= {v for v in PerfilUsuario.objects.filter(usuario_id__in=ids).values_list('vendedor', flat=True) if v}
    qs = Venta.objects.filter(estado='REGISTRADO', fecha_emision__range=[desde, hasta]).exclude(
        tipo_comprobante__in=['07', '08']).filter(Q(vendedor__in=nombres) | Q(cotizacion__oportunidad__responsable_id__in=ids))
    total = D0
    for v in qs.distinct():
        base = (v.base_imponible or D0) + (v.no_gravado or D0)
        total += base * (v.tipo_cambio or 1) if v.moneda == 'USD' else base
    return total.quantize(Decimal('0.01'))


@login_required
def metas(request):
    hoy = timezone.localdate()
    periodo = request.GET.get('periodo') or hoy.strftime('%Y%m')
    if not (periodo.isdigit() and len(periodo) == 6):
        periodo = hoy.strftime('%Y%m')
    desde = date(int(periodo[:4]), int(periodo[4:]), 1)
    hasta = (desde + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    filas = []
    for m in MetaVenta.objects.filter(periodo=periodo).select_related('vendedor', 'equipo__jefe').prefetch_related(
            'equipo__miembros'):
        usuarios = _usuarios_de_meta(m)
        ganado = sum((o.monto for o in Oportunidad.objects.filter(
            etapa='GANADA', cerrada_en__range=[desde, hasta], responsable__in=usuarios)), D0)
        facturado = _facturado(usuarios, desde, hasta)
        filas.append([{'texto': _nombre(m.vendedor) if m.vendedor_id else f'Equipo {m.equipo}',
                       'url': reverse('crm:meta', args=[m.pk])}, m.monto, ganado, _pct(ganado, m.monto), facturado,
                      {'texto': _pct(facturado, m.monto),
                       'clase': 'text-success fw-semibold' if facturado >= m.monto else ''}])
    return _responder(request, f'Metas y avance · {periodo[4:]}/{periodo[:4]}', [{
        'titulo': 'Avance', 'columnas': ['Vendedor / equipo', 'Meta S/', 'Ganado S/', '% ganado', 'Facturado S/',
                                         '% facturado'], 'filas': filas}],
        botones=[('Nueva meta', reverse('crm:meta_nueva'), 'bi-plus-lg'),
                 ('Equipos de ventas', reverse('crm:equipos'), 'bi-people')],
        textos=[('periodo', 'Mes (AAAAMM)', periodo)],
        ayuda='Ganado: oportunidades ganadas en el mes. Facturado: comprobantes del mes (sin IGV) del vendedor o de '
              'sus oportunidades.')


@login_required
def comisiones(request):
    desde, hasta = _rango(request, meses=1)
    pct = srv.parametro('CRM_COMISION_PCT')
    por = defaultdict(lambda: [0, D0])
    detalle = []
    qs = (Venta.objects.filter(estado='REGISTRADO', fecha_emision__range=[desde, hasta],
                               cotizacion__oportunidad__isnull=False).exclude(tipo_comprobante__in=['07', '08'])
          .select_related('cotizacion__oportunidad__responsable', 'tercero'))
    for v in qs:
        base = (v.base_imponible or D0) + (v.no_gravado or D0)
        base = base * (v.tipo_cambio or 1) if v.moneda == 'USD' else base
        u = v.cotizacion.oportunidad.responsable
        por[_nombre(u)][0] += 1
        por[_nombre(u)][1] += base
        detalle.append([fmt_fecha(v.fecha_emision), v.numero_completo, v.tercero.nombre,
                        v.cotizacion.oportunidad.nombre, _nombre(u), base.quantize(Decimal('0.01')),
                        (base * pct / 100).quantize(Decimal('0.01'))])
    resumen = [[k, n, b.quantize(Decimal('0.01')), (b * pct / 100).quantize(Decimal('0.01'))]
               for k, (n, b) in sorted(por.items(), key=lambda x: -x[1][1])]
    return _responder(request, 'Comisiones (ventas nacidas en una oportunidad)', [
        {'titulo': 'Por vendedor', 'columnas': ['Vendedor', 'Comprobantes', 'Base S/', f'Comisión {pct:g} %'],
         'filas': resumen},
        {'titulo': 'Detalle', 'columnas': ['Fecha', 'Comprobante', 'Cliente', 'Oportunidad', 'Vendedor', 'Base S/',
                                           'Comisión S/'], 'filas': detalle}], rango=(desde, hasta),
        ayuda=f'Solo comprobantes que vienen de una cotización unida a una oportunidad. Porcentaje: parámetro '
              f'CRM_COMISION_PCT ({pct:g} %).')


@login_required
def inactivos(request):
    dias = request.GET.get('dias') or ''
    ids = None if srv.ve_todo(request.user) else srv.equipo_de(request.user)
    filas = [[{'texto': t.nombre, 'url': reverse('crm:cliente', args=[t.pk])}, t.numero_doc, fmt_fecha(ultima), d,
              t.telefono or t.email,
              {'texto': 'Agendar visita', 'url': f"{reverse('crm:visitas')}?tercero={t.pk}"}]
             for t, ultima, d in srv.clientes_inactivos(int(dias) if dias.isdigit() else None, ids)]
    return _responder(request, 'Clientes sin compras', [{
        'titulo': '', 'columnas': ['Cliente', 'RUC / DNI', 'Última compra', 'Días sin comprar', 'Contacto', ''],
        'filas': filas}], textos=[('dias', 'Días sin comprar', dias or str(int(srv.parametro(
            'CRM_DIAS_CLIENTE_INACTIVO'))))],
        ayuda='Clientes con compras anteriores que no compran hace más de los días indicados (parámetro '
              'CRM_DIAS_CLIENTE_INACTIVO). El jefe ve su equipo; el vendedor, su cartera.')


def pronostico_semanal(semanas):
    """{(desde, hasta): ponderado} de las oportunidades abiertas por semana de cierre (flujo proyectado: CRM-16)."""
    salida = []
    abiertas = list(Oportunidad.objects.filter(etapa__in=Oportunidad.ABIERTAS, fecha_cierre__isnull=False))
    for d, h in semanas:
        salida.append(sum((o.ponderado for o in abiertas if d <= o.fecha_cierre <= h), D0).quantize(Decimal('0.01')))
    return salida
