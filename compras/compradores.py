"""Compradores y grupos de compras (COM-09) e indicadores de su desempeño (COM-10): cada línea de solicitud liberada
se asigna sola al comprador del grupo que atiende su familia, tipo o centro de costo; se reasigna a mano con
historial; el tablero mide carga, rapidez, cumplimiento del plazo, atraso, llegada a tiempo, ahorro, competencia,
urgencias, calidad del documento y monto gestionado por comprador y mes."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import r2

from .models import AsignacionCompra, GrupoCompras, LineaSolicitud, OrdenCompra

D0 = Decimal('0')
TRAMOS = [('0-7', 0, 7), ('8-15', 8, 15), ('16-30', 16, 30), ('+30', 31, 10 ** 6)]


class ErrorComprador(Exception):
    pass


# ---------------------------------------------------------------- asignación
def _tipos_de(linea):
    sp = linea.solicitud
    tipos = set()
    if sp.tipo == 'ACTIVO':
        tipos.add('ACTIVO')
    if linea.es_servicio or sp.tipo == 'SERVICIO':
        tipos.add('SERVICIO')
    if linea.producto_id:
        tipos.add(linea.producto.clase)
    proveedor = linea.proveedor_sugerido or (linea.producto.proveedor if linea.producto_id else None)
    if proveedor is not None and proveedor.pais and proveedor.pais.strip().lower() not in ('perú', 'peru', 'pe'):
        tipos.add('IMPORTACION')
    return tipos


def grupo_para(linea, grupos=None):
    """El grupo que atiende la línea: el que coincide por familia, luego por tipo, luego solo por centro; a igualdad,
    el de menor prioridad. None si ninguno."""
    grupos = grupos if grupos is not None else list(GrupoCompras.objects.filter(activo=True)
                                                    .prefetch_related('centros'))
    familia = (linea.producto.familia or '').strip().lower() if linea.producto_id else ''
    tipos = _tipos_de(linea)
    centro = linea.solicitud.centro_costo_id
    mejor, puntos_mejor = None, 0
    for g in grupos:
        centros = {c.pk for c in g.centros.all()}
        if centros and centro not in centros:
            continue
        puntos = 1  # cumple el centro (o atiende todos)
        if familia and familia in g.lista_familias:
            puntos += 4
        elif tipos & set(g.tipos or []):
            puntos += 2
        elif g.familias or g.tipos:
            continue  # tiene filtros y la línea no los cumple
        if puntos > puntos_mejor:
            mejor, puntos_mejor = g, puntos
    return mejor


@transaction.atomic
def asignar_lineas(lineas, por=None):
    """Asigna las líneas sin comprador al de su grupo. Devuelve cuántas asignó."""
    grupos = list(GrupoCompras.objects.filter(activo=True).prefetch_related('centros'))
    n = 0
    ahora = timezone.now()
    for l in lineas:
        if l.comprador_id:
            continue
        g = grupo_para(l, grupos)
        if g is None:
            continue
        LineaSolicitud.objects.filter(pk=l.pk).update(grupo=g, comprador=g.comprador_id, asignada_en=ahora)
        AsignacionCompra.objects.create(linea=l, grupo=g, comprador_id=g.comprador_id, por=por,
                                        motivo=f'Automática por el grupo {g.codigo}')
        n += 1
    return n


def asignar_solicitud(sp, por=None):
    return asignar_lineas(list(sp.lineas.select_related('producto__proveedor', 'proveedor_sugerido',
                                                        'solicitud')), por)


@transaction.atomic
def reasignar(ids, comprador, por, motivo):
    if comprador is None:
        raise ErrorComprador('Elija el comprador.')
    if len((motivo or '').strip()) < 5:
        raise ErrorComprador('Indique el motivo de la reasignación.')
    lineas = list(LineaSolicitud.objects.filter(pk__in=ids))
    if not lineas:
        raise ErrorComprador('Elija las líneas a reasignar.')
    grupo = GrupoCompras.objects.filter(comprador=comprador, activo=True).first()
    ahora = timezone.now()
    for l in lineas:
        LineaSolicitud.objects.filter(pk=l.pk).update(comprador=comprador, grupo=grupo, asignada_en=ahora)
        AsignacionCompra.objects.create(linea=l, grupo=grupo, comprador=comprador, por=por, motivo=motivo.strip()[:200])
    return len(lineas)


def es_comprador(user):
    return user.is_authenticated and GrupoCompras.objects.filter(comprador=user, activo=True).exists()


# ---------------------------------------------------------------- plazos
def dias_habiles(desde, hasta):
    """Días hábiles (lunes a viernes) entre dos fechas (0 si es el mismo día)."""
    if desde is None or hasta is None or hasta <= desde:
        return 0
    dias, d = 0, desde
    while d < hasta:
        d += timedelta(days=1)
        if d.weekday() < 5:
            dias += 1
    return dias


def plazo(linea):
    g = linea.grupo
    if g is None:
        return 3 if not linea.solicitud.urgente else 1
    return g.plazo_urgente if linea.solicitud.urgente else g.plazo_dias


def _fecha_liberada(linea):
    sp = linea.solicitud
    base = linea.asignada_en or sp.liberada_en
    return timezone.localtime(base).date() if base else sp.fecha


def _oc_de(linea):
    """La primera orden de compra vigente de la línea y la fecha en que se envió (o se emitió)."""
    for item in linea.items_oc.all():
        oc = item.documento
        if oc.estado == 'ANULADO':
            continue
        fecha = timezone.localtime(oc.enviada_en).date() if oc.enviada_en else oc.fecha
        return oc, fecha
    return None, None


def vencidas(usuario=None, jefe=None):
    """Líneas por pedir con el plazo vencido (del comprador o de los grupos del jefe)."""
    from .solped import lineas_abiertas
    qs = LineaSolicitud.objects.filter(comprador__isnull=False)
    if usuario is not None:
        qs = qs.filter(comprador=usuario)
    if jefe is not None:
        qs = qs.filter(grupo__jefe=jefe)
    hoy = timezone.localdate()
    return [l for l in lineas_abiertas(qs.select_related('grupo'))
            if dias_habiles(_fecha_liberada(l), hoy) > plazo(l)]


# ---------------------------------------------------------------- tablero (COM-10)
def _percentil(valores, p):
    if not valores:
        return None
    v = sorted(valores)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def desempeno(desde, hasta):
    """{comprador: indicadores} de las líneas asignadas y las OC del periodo."""
    from compras.historial_precios import historial
    from proveedores.models import FacturaProveedor
    hoy = timezone.localdate()
    datos = defaultdict(lambda: {'asignadas': 0, 'atendidas': 0, 'pendientes': 0, 'tiempos': [], 'en_plazo': 0,
                                 'vencidas': {t: 0 for t, _, _ in TRAMOS}, 'recepciones': 0, 'a_tiempo': 0,
                                 'ahorro': D0, 'monto': D0, 'competencia': D0, 'urgentes': 0, 'ocs': 0,
                                 'anuladas': 0, 'observadas': 0})
    lineas = (LineaSolicitud.objects.filter(comprador__isnull=False, asignada_en__date__gte=desde,
                                            asignada_en__date__lte=hasta)
              .select_related('solicitud', 'grupo', 'comprador', 'producto')
              .prefetch_related('items_oc__documento'))
    for l in lineas:
        d = datos[l.comprador]
        d['asignadas'] += 1
        oc, fecha_oc = _oc_de(l)
        liberada = _fecha_liberada(l)
        if oc is not None:
            d['atendidas'] += 1
            dias = dias_habiles(liberada, fecha_oc)
            d['tiempos'].append(dias)
            if dias <= plazo(l):
                d['en_plazo'] += 1
        elif not l.cerrada:
            d['pendientes'] += 1
            atraso = dias_habiles(liberada, hoy) - plazo(l)
            if atraso > 0:
                antig = (hoy - liberada).days
                for tramo, a, b in TRAMOS:
                    if a <= antig <= b:
                        d['vencidas'][tramo] += 1
    ordenes = (OrdenCompra.objects.filter(fecha__gte=desde, fecha__lte=hasta)
               .select_related('tercero').prefetch_related('items__producto', 'items__linea_solped__solicitud',
                                                           'recepciones'))
    from .models import LineaLicitacion
    con_licitacion = set(LineaLicitacion.objects.filter(orden_compra__in=ordenes)
                         .values_list('orden_compra_id', flat=True))
    for oc in ordenes:
        comprador = oc.comprador or _creador(oc)
        if comprador is None:
            continue
        d = datos[comprador]
        tc = oc.tipo_cambio if oc.moneda == 'USD' else Decimal('1')
        if oc.estado == 'ANULADO':
            d['anuladas'] += 1
            continue
        monto = r2(oc.total * tc)
        d['ocs'] += 1
        d['monto'] += monto
        if oc.contrato_id or oc.pk in con_licitacion:
            d['competencia'] += monto
        if any(i.linea_solped_id and i.linea_solped.solicitud.urgente for i in oc.items.all()):
            d['urgentes'] += 1
        for i in oc.items.all():
            if not i.producto_id:
                continue
            previas = historial(i.producto, hasta=oc.fecha - timedelta(days=1))
            if previas:  # ahorro frente al último precio pagado antes de la orden
                d['ahorro'] += r2((previas[-1]['precio_pen'] - i.precio_neto * tc) * i.cantidad)
        requerida = min((i.linea_solped.solicitud.fecha_requerida for i in oc.items.all()
                         if i.linea_solped_id and i.linea_solped.solicitud.fecha_requerida), default=None)
        if requerida:
            for r in oc.recepciones.all():
                if r.estado != 'CONFIRMADO':
                    continue
                d['recepciones'] += 1
                if r.fecha <= requerida:
                    d['a_tiempo'] += 1
        d['observadas'] += FacturaProveedor.objects.filter(orden_compra=oc).exclude(fuera_tolerancia='').count()
    filas = []
    for comprador, d in datos.items():
        tiempos = d['tiempos']
        filas.append({
            'comprador': comprador, **d,
            'tiempo_promedio': r2(Decimal(sum(tiempos)) / len(tiempos)) if tiempos else None,
            'p90': _percentil(tiempos, 90),
            'sla': r2(Decimal(d['en_plazo']) * 100 / d['atendidas']) if d['atendidas'] else None,
            'a_tiempo_pct': r2(Decimal(d['a_tiempo']) * 100 / d['recepciones']) if d['recepciones'] else None,
            'competencia_pct': r2(d['competencia'] * 100 / d['monto']) if d['monto'] else None,
            'urgentes_pct': r2(Decimal(d['urgentes']) * 100 / d['ocs']) if d['ocs'] else None,
            'vencidas_total': sum(d['vencidas'].values())})
    filas.sort(key=lambda f: (f['sla'] is None, -(f['sla'] or 0), f['vencidas_total']))
    return filas


def _creador(oc):
    from django.contrib.auth import get_user_model

    from core.segregacion import creador
    pk = creador(oc)
    return get_user_model().objects.filter(pk=pk).first() if pk else None


def mes(texto=None):
    """(desde, hasta) del mes AAAA-MM (o el actual)."""
    hoy = timezone.localdate()
    try:
        anio, m = (int(x) for x in (texto or '').split('-'))
        inicio = date(anio, m, 1)
    except (ValueError, TypeError):
        inicio = hoy.replace(day=1)
    fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return inicio, fin
