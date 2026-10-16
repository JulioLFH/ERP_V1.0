"""Reportes de producción.

Cada reporte recibe los filtros comunes (`Filtros`) y devuelve un dict con: titulo, descripcion, columnas
[(clave, título, tipo)] con tipo texto / num / pct / dinero / fecha, filas [dict], total (dict o None), avisos y,
si corresponde, un gráfico [(etiqueta, valor)]. Las cantidades se informan en la unidad elegida: la base del
producto, kg, toneladas (TM) o la unidad de reporte de cada producto; la conversión usa el peso neto por unidad.
La fecha de producción es la real (término de la orden); la contable es la de la operación de almacén."""
from collections import OrderedDict, defaultdict
from copy import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Almacen, Kardex, Lote, Producto
from core.utils import a_fecha

from .models import (AvanceOrden, CentroTrabajo, ConsumoOrden, Equipo, HoraOrden, InspeccionCalidad,
                     OrdenMantenimiento, OrdenProduccion, Parada, ResultadoCalidad)

D0 = Decimal('0')
UNIDADES = [('KG', 'kg'), ('TM', 'TM'), ('BASE', 'unidad base'), ('PROD', 'unidad de reporte del producto')]
AGRUPAR_TIEMPO = [('dia', 'Día'), ('semana', 'Semana'), ('mes', 'Mes')]


def r2(x):
    return Decimal(x).quantize(Decimal('0.01'))


def r3(x):
    return Decimal(x).quantize(Decimal('0.001'))


def pct(a, b):
    return r2(Decimal(a) * 100 / b) if b else None


# ---------------------------------------------------------------- filtros comunes
class Filtros:
    CAMPOS = ('desde', 'hasta', 'planta', 'linea', 'familia', 'producto', 'turno', 'operario', 'estado', 'insumo',
              'equipo', 'agrupar', 'por', 'unidad', 'lote', 'cliente', 'fecha')

    def __init__(self, datos=None):
        datos = datos or {}
        hoy = timezone.localdate()
        self.hoy = hoy
        self.valores = {c: (datos.get(c) or '').strip() for c in self.CAMPOS}
        self.desde = self._fecha('desde', hoy.replace(day=1))
        self.hasta = self._fecha('hasta', hoy)
        self.fecha = self._fecha('fecha', hoy)
        self.unidad = self.valores['unidad'] if self.valores['unidad'] in dict(UNIDADES) else 'KG'
        self.agrupar = self.valores['agrupar']
        self.por = self.valores['por']
        for campo in ('planta', 'linea', 'producto', 'turno', 'insumo', 'equipo', 'cliente'):
            v = self.valores[campo]
            setattr(self, campo, int(v) if v.isdigit() else None)
        self.familia, self.operario = self.valores['familia'], self.valores['operario']
        self.estado, self.lote = self.valores['estado'], self.valores['lote']

    def _fecha(self, campo, defecto):
        try:
            return a_fecha(self.valores[campo]) if self.valores[campo] else defecto
        except ValueError:
            return defecto

    def productos(self, qs, prefijo='producto__'):
        if self.producto:
            qs = qs.filter(**{f'{prefijo}pk': self.producto})
        if self.familia:
            qs = qs.filter(**{f'{prefijo}familia__iexact': self.familia})
        return qs

    @property
    def nombre_unidad(self):
        return dict(UNIDADES)[self.unidad]


def factor(producto, unidad):
    """Multiplicador de la cantidad en unidad base a la unidad pedida (None si no se puede convertir)."""
    if unidad == 'PROD':
        unidad = producto.unidad_reporte or 'KG'
    if unidad == 'BASE':
        return Decimal('1')
    kg = producto.kg_por_unidad
    if kg is None:
        return None
    return kg if unidad == 'KG' else kg / 1000


def en(producto, cantidad, unidad):
    f = factor(producto, unidad)
    return None if f is None else cantidad * f


def etiqueta_unidad(producto, unidad):
    if unidad == 'PROD':
        unidad = producto.unidad_reporte or 'KG'
    return {'KG': 'kg', 'TM': 'TM', 'BASE': producto.unidad}[unidad]


def kg(producto, cantidad):
    return en(producto, cantidad, 'KG')


def _periodo(fecha, agrupar):
    if agrupar == 'dia':
        return fecha.strftime('%d/%m/%Y')
    if agrupar == 'semana':
        lunes = fecha - timedelta(days=fecha.weekday())
        return f'Sem. {lunes.isocalendar()[1]:02d} ({lunes:%d/%m})'
    return fecha.strftime('%m/%Y')


def fecha_real(orden):
    if orden.terminado_en:
        return timezone.localtime(orden.terminado_en).date()
    return orden.fecha_fin or orden.fecha


def puesto_principal(orden):
    if isinstance(orden, SimpleNamespace):  # lectura liviana de `_terminadas`
        return orden.puesto
    horas = sorted(orden.horas.all(), key=lambda h: (h.secuencia or 0, h.id))
    return horas[0].centro if horas else None


def turno_de(momento, turnos=None):
    """Turno (planillas) al que corresponde la hora del registro."""
    from planillas.models import Turno
    turnos = turnos if turnos is not None else list(Turno.objects.all())
    hora = timezone.localtime(momento).time()
    for t in turnos:
        if t.hora_entrada <= t.hora_salida:
            if t.hora_entrada <= hora < t.hora_salida:
                return t
        elif hora >= t.hora_entrada or hora < t.hora_salida:  # turno de noche
            return t
    return None


def _terminadas_qs(f):
    """Órdenes terminadas cuya fecha real (ver `fecha_real`) cae en el periodo, filtradas en la base de datos."""
    rango = [f.desde, f.hasta]
    qs = OrdenProduccion.objects.filter(estado='TERMINADA').filter(
        Q(terminado_en__date__range=rango) | Q(terminado_en__isnull=True, fecha_fin__range=rango) |
        Q(terminado_en__isnull=True, fecha_fin__isnull=True, fecha__range=rango))
    qs = f.productos(qs)
    if f.planta:
        qs = qs.filter(almacen_destino_id=f.planta)
    if f.linea:
        qs = qs.filter(pk__in=HoraOrden.objects.filter(centro_id=f.linea).values('orden_id'))
    return qs


_CAMPOS_ORDEN = ('pk', 'numero', 'producto_id', 'cantidad', 'cantidad_producida', 'terminado_en', 'fecha_fin', 'fecha',
                 'almacen_destino_id', 'costo_materiales', 'costo_mano_obra', 'costo_maquina', 'costo_cif',
                 'merma_anormal', 'ajuste_liquidacion', 'costo_subproductos')


def _terminadas(f):
    """Lectura liviana de las órdenes terminadas (decenas de miles en un año): solo los campos que usan los
    reportes, con el producto, la planta, la fecha contable y el puesto principal ya resueltos."""
    qs = _terminadas_qs(f)
    productos = {p.pk: p for p in Producto.objects.filter(pk__in=qs.values('producto_id'))}
    plantas = dict(Almacen.objects.values_list('pk', 'nombre'))
    centros = CentroTrabajo.objects.in_bulk()
    puestos = {}
    for orden_id, centro_id in (HoraOrden.objects.filter(orden__in=qs.values('pk'))
                                .order_by('orden_id', 'secuencia', 'id').values_list('orden_id', 'centro_id')):
        puestos.setdefault(orden_id, centros.get(centro_id))
    salida = []
    for x in qs.values(*_CAMPOS_ORDEN, 'operacion__fecha').order_by('id').iterator(chunk_size=2000):
        o = SimpleNamespace(**x)
        o.producto, o.fecha_contable = productos[o.producto_id], x['operacion__fecha']
        o.planta, o.puesto = plantas.get(o.almacen_destino_id, '—'), puestos.get(o.pk)
        o.costo_real_final = (o.costo_materiales + o.costo_mano_obra + o.costo_maquina + o.costo_cif -  # como el modelo
                              o.merma_anormal - o.costo_subproductos + o.ajuste_liquidacion)
        salida.append(o)
    return salida


def _convertir_fila(fila, producto, cantidad, f, avisos):
    fila['unidad'] = etiqueta_unidad(producto, f.unidad)
    valor = en(producto, cantidad, f.unidad)
    if valor is None:
        avisos.add(producto)
    return valor


def _avisos_peso(sin_peso):
    if not sin_peso:
        return []
    nombres = ', '.join(sorted(p.codigo for p in sin_peso)[:10])
    return [f'{len(sin_peso)} producto(s) sin peso neto por unidad no se convierten a kg/TM ({nombres}…): '
            f'regístrelo en la ficha del producto.']


# ---------------------------------------------------------------- 1. producción en unidad base, kg y TM
def produccion(f):
    """Cantidad producida por día, semana o mes y por planta, línea, familia o producto, en la unidad base y
    convertida a kg y TM."""
    agrupar = f.agrupar if f.agrupar in ('dia', 'semana', 'mes', 'orden') else 'mes'
    por = f.por if f.por in ('planta', 'linea', 'familia', 'producto') else 'producto'
    sin_peso = set()
    if agrupar == 'orden':
        filas = []
        for o in sorted(_terminadas(f), key=fecha_real):
            k = kg(o.producto, o.cantidad_producida)
            if k is None:
                sin_peso.add(o.producto)
            filas.append({'orden': o.numero, 'producto': f'{o.producto.codigo} {o.producto.nombre}',
                          'fecha_real': fecha_real(o), 'fecha_contable': o.fecha_contable,
                          'periodo_contable': o.fecha_contable.strftime('%m/%Y') if o.fecha_contable else '',
                          'cantidad': o.cantidad_producida, 'unidad': o.producto.unidad,
                          'kg': r2(k) if k is not None else None, 'tm': r3(k / 1000) if k is not None else None})
        cols = [('orden', 'Orden', 'texto'), ('producto', 'Producto', 'texto'), ('fecha_real', 'Fecha real', 'fecha'),
                ('fecha_contable', 'Fecha contable', 'fecha'), ('periodo_contable', 'Periodo contable', 'texto'),
                ('cantidad', 'Cantidad', 'num'), ('unidad', 'Unidad', 'texto'), ('kg', 'kg', 'num'), ('tm', 'TM', 'num')]
    else:
        grupos = OrderedDict()
        if f.turno or f.operario:  # por turno u operario: lo reportado desde la planta
            fuentes = [(a.orden.producto, a.cantidad_buena, timezone.localtime(a.registrado).date(), a.orden, a.hora)
                       for a in _avances(f)]
        else:
            fuentes = [(o.producto, o.cantidad_producida, fecha_real(o), o, None) for o in _terminadas(f)]
        for p, cantidad, fecha, o, hora in sorted(fuentes, key=lambda x: x[2]):
            dim = {'planta': lambda: (o.planta if isinstance(o, SimpleNamespace) else
                                      o.almacen_destino.nombre if o.almacen_destino_id else '—'),
                   'linea': lambda: str(hora.centro if hora else (puesto_principal(o) or '—')),
                   'familia': lambda: p.familia or 'Sin familia',
                   'producto': lambda: f'{p.codigo} {p.nombre}'}[por]()
            clave = (_periodo(fecha, agrupar), dim, p.unidad if por == 'producto' else '')
            g = grupos.setdefault(clave, {'base': D0, 'kg': D0, 'ordenes': set()})
            g['base'] += cantidad
            k = kg(p, cantidad)
            if k is None:
                sin_peso.add(p)
            else:
                g['kg'] += k
            g['ordenes'].add(o.pk)
        filas = [{'periodo': per, 'dimension': dim, 'cantidad': g['base'] if por == 'producto' else None,
                  'unidad': und, 'kg': r2(g['kg']), 'tm': r3(g['kg'] / 1000), 'ordenes': len(g['ordenes'])}
                 for (per, dim, und), g in grupos.items()]
        cols = [('periodo', dict(AGRUPAR_TIEMPO)[agrupar], 'texto'),
                ('dimension', {'planta': 'Planta', 'linea': 'Línea / puesto', 'familia': 'Familia',
                               'producto': 'Producto'}[por], 'texto'),
                ('cantidad', 'Cantidad (unidad base)', 'num'), ('unidad', 'Unidad', 'texto'), ('kg', 'kg', 'num'),
                ('tm', 'TM', 'num'), ('ordenes', 'Órdenes', 'num')]
    total = {'kg': r2(sum((x['kg'] or D0 for x in filas), D0)), 'tm': r3(sum((x['tm'] or D0 for x in filas), D0))}
    grafico = defaultdict(lambda: D0)
    for x in filas:
        grafico[x.get('periodo') or x.get('fecha_real').strftime('%d/%m')] += x['tm'] or D0
    return {'titulo': 'Producción en unidad base, kg y toneladas', 'columnas': cols, 'filas': filas, 'total': total,
            'avisos': _avisos_peso(sin_peso), 'grafico': list(grafico.items()),
            'descripcion': 'Órdenes terminadas por su fecha real de término (por turno u operario: lo reportado en '
                           'la planta). En el detalle por orden se ve también la fecha contable.'}


# ---------------------------------------------------------------- 2. plan contra real
def _ordenes(qs, campos=()):
    """Lectura liviana de órdenes (pueden ser decenas de miles): producto, puesto principal y lo producido (lo
    terminado o, en proceso, lo bueno reportado en su última operación, como avanzado.resumen_avance)."""
    productos = {p.pk: p for p in Producto.objects.filter(pk__in=qs.values('producto_id'))}
    centros = CentroTrabajo.objects.in_bulk()
    puestos, ultima = {}, {}
    for orden_id, hora_id, centro_id in (HoraOrden.objects.filter(orden__in=qs.values('pk'))
                                         .order_by('orden_id', 'secuencia', 'id')
                                         .values_list('orden_id', 'id', 'centro_id')):
        puestos.setdefault(orden_id, centros.get(centro_id))
        ultima[orden_id] = hora_id
    avances = defaultdict(list)
    for orden_id, hora_id, buena in (AvanceOrden.objects.filter(orden__in=qs.exclude(estado='TERMINADA').values('pk'))
                                     .values_list('orden_id', 'hora_id', 'cantidad_buena')):
        avances[orden_id].append((hora_id, buena))
    estados = dict(OrdenProduccion.ESTADOS)
    salida = []
    for x in qs.values('pk', 'numero', 'producto_id', 'cantidad', 'cantidad_producida', 'estado', 'fecha',
                       'version__dias_fabricacion', *campos).order_by('fecha', 'id').iterator(chunk_size=2000):
        o = SimpleNamespace(**x)
        o.producto, o.puesto, o.estado_texto = productos[o.producto_id], puestos.get(o.pk), estados.get(o.estado)
        if o.estado == 'TERMINADA':
            o.producido = o.cantidad_producida
        else:
            lista = avances.get(o.pk, [])
            base = [b for h, b in lista if ultima.get(o.pk) and h == ultima[o.pk]] or [b for h, b in lista if h is None]
            o.producido = sum(base, D0)
        salida.append(o)
    return salida


def plan_real(f):
    qs = OrdenProduccion.objects.filter(fecha__range=[f.desde, f.hasta]).exclude(estado__in=['ANULADA', 'BORRADOR'])
    qs = f.productos(qs)
    if f.estado:
        qs = qs.filter(estado=f.estado)
    if f.linea:
        qs = qs.filter(pk__in=HoraOrden.objects.filter(centro_id=f.linea).values('orden_id'))
    agrupar = f.agrupar if f.agrupar in ('orden', 'linea', 'semana') else 'orden'
    sin_peso, grupos = set(), OrderedDict()
    for o in _ordenes(qs):
        real = o.producido
        clave = {'orden': lambda: (o.numero, f'{o.producto.codigo} {o.producto.nombre}'),
                 'linea': lambda: (str(puesto_principal(o) or '—'), ''),
                 'semana': lambda: (_periodo(o.fecha, 'semana'), '')}[agrupar]()
        g = grupos.setdefault(clave, {'plan': D0, 'real': D0, 'ordenes': 0, 'estado': o.estado_texto,
                                      'fecha': o.fecha})
        p_plan, p_real = en(o.producto, o.cantidad, f.unidad), en(o.producto, real, f.unidad)
        if p_plan is None:
            sin_peso.add(o.producto)
            continue
        g['plan'] += p_plan
        g['real'] += p_real
        g['ordenes'] += 1
    filas = []
    for (c1, c2), g in grupos.items():
        cumplimiento = pct(g['real'], g['plan'])
        desvio = (cumplimiento - 100) if cumplimiento is not None else None
        filas.append({'grupo': c1, 'detalle': c2, 'fecha': g['fecha'] if agrupar == 'orden' else None,
                      'estado': g['estado'] if agrupar == 'orden' else '', 'ordenes': g['ordenes'],
                      'plan': r2(g['plan']), 'real': r2(g['real']), 'cumplimiento': cumplimiento,
                      'alerta': 'Desvío' if desvio is not None and abs(desvio) > 5 else ''})
    tp, trr = sum((x['plan'] for x in filas), D0), sum((x['real'] for x in filas), D0)
    titulo = {'orden': 'Orden', 'linea': 'Línea / puesto', 'semana': 'Semana'}[agrupar]
    return {'titulo': 'Plan contra real (cumplimiento)', 'filas': filas,
            'columnas': [('grupo', titulo, 'texto'), ('detalle', 'Producto', 'texto'), ('fecha', 'Fecha plan', 'fecha'),
                         ('estado', 'Estado', 'texto'), ('ordenes', 'Órdenes', 'num'),
                         ('plan', f'Planificado ({f.nombre_unidad})', 'num'), ('real', f'Producido ({f.nombre_unidad})', 'num'),
                         ('cumplimiento', 'Cumplimiento %', 'pct'), ('alerta', 'Desvío > ±5 %', 'texto')],
            'total': {'plan': r2(tp), 'real': r2(trr), 'cumplimiento': pct(trr, tp)}, 'avisos': _avisos_peso(sin_peso),
            'descripcion': 'Órdenes por su fecha planificada; en proceso, lo producido es lo bueno reportado en la '
                           'planta. Se marca el desvío mayor a ±5 % del plan.'}


# ---------------------------------------------------------------- 3 y 4. merma y consumo de materias primas
def _consumos(f):
    qs = ConsumoOrden.objects.filter(orden__in=_terminadas_qs(f).values('pk'))
    if f.insumo:
        qs = qs.filter(producto_id=f.insumo)
    ordenes = {o.pk: o for o in _terminadas(f)}
    insumos = {p.pk: p for p in Producto.objects.filter(pk__in=qs.values('producto_id'))}
    salida = []
    for x in qs.values('orden_id', 'producto_id', 'cantidad_plan', 'cantidad_real', 'costo_unitario').order_by(
            'orden_id', 'id').iterator(chunk_size=5000):
        c = SimpleNamespace(**x)
        c.orden, c.producto = ordenes[c.orden_id], insumos[c.producto_id]
        salida.append(c)
    return salida


def merma(f, ver_costos=True):
    """Consumo real contra receta por orden (y por insumo si se filtra): merma en kg y %; las 10 mayores."""
    sin_peso, por_orden = set(), OrderedDict()
    for c in _consumos(f):
        o = c.orden
        g = por_orden.setdefault(o.pk, {'orden': o.numero, 'producto': f'{o.producto.codigo} {o.producto.nombre}',
                                         'fecha': fecha_real(o), 'plan': D0, 'real': D0, 'soles': D0,
                                         'rechazo': D0})
        kp, kr = kg(c.producto, c.cantidad_plan), kg(c.producto, c.cantidad_real)
        if kp is None:
            sin_peso.add(c.producto)
            continue
        g['plan'] += kp
        g['real'] += kr
        g['soles'] += (c.cantidad_real - c.cantidad_plan) * c.costo_unitario
    rechazos = dict(AvanceOrden.objects.filter(orden__in=_terminadas_qs(f).values('pk')).values_list('orden')
                    .annotate(s=Sum('cantidad_merma')))
    filas = []
    for pk, g in por_orden.items():
        merma_kg = g['real'] - g['plan']
        fila = {**g, 'plan': r2(g['plan']), 'real': r2(g['real']), 'merma': r2(merma_kg),
                'merma_pct': pct(merma_kg, g['plan']), 'soles': r2(g['soles']) if ver_costos else None,
                'rechazo': rechazos.get(pk) or D0}
        filas.append(fila)
    filas.sort(key=lambda x: -x['merma'])
    for i, x in enumerate(filas, 1):
        x['ranking'] = i if i <= 10 and x['merma'] > 0 else ''
    tp, trr = sum((x['plan'] for x in filas), D0), sum((x['real'] for x in filas), D0)
    cols = [('ranking', 'Top 10', 'texto'), ('orden', 'Orden', 'texto'), ('producto', 'Producto', 'texto'),
            ('fecha', 'Fecha real', 'fecha'), ('plan', 'Insumos receta (kg)', 'num'), ('real', 'Insumos reales (kg)', 'num'),
            ('merma', 'Merma (kg)', 'num'), ('merma_pct', 'Merma %', 'pct'),
            ('rechazo', 'Rechazo de producto (unid.)', 'num')]
    if ver_costos:
        cols.append(('soles', 'Merma S/', 'dinero'))
    return {'titulo': 'Rendimiento y merma por orden', 'columnas': cols, 'filas': filas,
            'total': {'plan': r2(tp), 'real': r2(trr), 'merma': r2(trr - tp), 'merma_pct': pct(trr - tp, tp)},
            'avisos': _avisos_peso(sin_peso),
            'descripcion': 'Merma = consumo real − consumo según la receta (con su merma normal). Negativo = ahorro. '
                           'Ordenado de mayor a menor merma.'}


def consumo_mp(f, ver_costos=True):
    """TM consumidas por insumo y mes contra el estándar de la receta; variación en kg y S/."""
    sin_peso, grupos = set(), OrderedDict()
    familia, f = f.familia, copy(f)
    f.familia = ''  # aquí la familia es la del insumo, no la del producto fabricado
    consumos = [c for c in _consumos(f) if not familia or (c.producto.familia or '').lower() == familia.lower()]
    for c in sorted(consumos, key=lambda c: c.producto.codigo):
        mes = fecha_real(c.orden).strftime('%m/%Y')
        g = grupos.setdefault((mes, c.producto), {'plan': D0, 'real': D0, 'soles': D0})
        kp, kr = kg(c.producto, c.cantidad_plan), kg(c.producto, c.cantidad_real)
        if kp is None:
            sin_peso.add(c.producto)
            continue
        g['plan'] += kp
        g['real'] += kr
        g['soles'] += (c.cantidad_real - c.cantidad_plan) * c.costo_unitario
    filas = [{'mes': mes, 'insumo': f'{p.codigo} {p.nombre}', 'familia': p.familia, 'real_tm': r3(g['real'] / 1000),
              'plan_tm': r3(g['plan'] / 1000), 'variacion_kg': r2(g['real'] - g['plan']),
              'variacion_pct': pct(g['real'] - g['plan'], g['plan']), 'variacion_soles': r2(g['soles']) if ver_costos
              else None} for (mes, p), g in grupos.items()]
    cols = [('mes', 'Mes', 'texto'), ('insumo', 'Insumo', 'texto'), ('familia', 'Familia', 'texto'),
            ('real_tm', 'Consumido (TM)', 'num'), ('plan_tm', 'Estándar receta (TM)', 'num'),
            ('variacion_kg', 'Variación (kg)', 'num'), ('variacion_pct', 'Variación %', 'pct')]
    if ver_costos:
        cols.append(('variacion_soles', 'Variación S/', 'dinero'))
    return {'titulo': 'Consumo de materias primas', 'columnas': cols, 'filas': filas,
            'total': {'real_tm': r3(sum((x['real_tm'] for x in filas), D0)),
                      'plan_tm': r3(sum((x['plan_tm'] for x in filas), D0)),
                      'variacion_kg': r2(sum((x['variacion_kg'] for x in filas), D0)),
                      'variacion_soles': r2(sum((x['variacion_soles'] or D0 for x in filas), D0)) if ver_costos
                      else None},
            'avisos': _avisos_peso(sin_peso),
            'descripcion': 'Insumos de las órdenes terminadas en el periodo. Estándar = cantidad de la receta para '
                           'lo planificado (con su merma normal).'}


# ---------------------------------------------------------------- 5 y 9. productividad, turno y operario
def _avances(f):
    from planillas.models import Turno
    qs = (AvanceOrden.objects.filter(registrado__date__range=[f.desde, f.hasta])
          .select_related('orden__producto', 'orden__almacen_destino', 'hora__centro', 'turno'))
    qs = f.productos(qs, 'orden__producto__')
    if f.linea:
        qs = qs.filter(hora__centro_id=f.linea)
    if f.operario:
        qs = qs.filter(operario__icontains=f.operario)
    turnos = list(Turno.objects.all())
    salida = []
    for a in qs.order_by('registrado'):
        a.turno_efectivo = a.turno or turno_de(a.registrado, turnos)
        if f.turno and (not a.turno_efectivo or a.turno_efectivo.pk != f.turno):
            continue
        salida.append(a)
    return salida


def productividad(f):
    """kg por hora hombre y por hora máquina por línea, turno u operario (lo reportado en la planta)."""
    por = f.por if f.por in ('linea', 'turno', 'operario') else 'linea'
    grupos, sin_peso = OrderedDict(), set()
    for a in _avances(f):
        dim = {'linea': lambda: str(a.hora.centro) if a.hora_id else 'Sin operación',
               'turno': lambda: str(a.turno_efectivo or 'Sin turno'),
               'operario': lambda: a.operario or 'Sin operario'}[por]()
        g = grupos.setdefault(dim, {'kg': D0, 'hm': D0, 'hh': D0, 'avances': 0})
        k = kg(a.orden.producto, a.cantidad_buena)
        if k is None:
            sin_peso.add(a.orden.producto)
            continue
        personas = (a.hora.maquinistas + a.hora.ayudantes) if a.hora_id else 1
        g['kg'] += k
        g['hm'] += a.horas
        g['hh'] += a.horas * max(personas, 1)
        g['avances'] += 1
    filas = [{'dimension': d, 'kg': r2(g['kg']), 'horas_maquina': r2(g['hm']), 'horas_hombre': r2(g['hh']),
              'kg_hm': r2(g['kg'] / g['hm']) if g['hm'] else None, 'kg_hh': r2(g['kg'] / g['hh']) if g['hh'] else None,
              'avances': g['avances']} for d, g in grupos.items()]
    tk, thm, thh = (sum((x[c] for x in filas), D0) for c in ('kg', 'horas_maquina', 'horas_hombre'))
    return {'titulo': 'Productividad', 'filas': filas,
            'columnas': [('dimension', {'linea': 'Línea / puesto', 'turno': 'Turno', 'operario': 'Operario'}[por], 'texto'),
                         ('kg', 'Producido (kg)', 'num'), ('horas_maquina', 'Horas máquina', 'num'),
                         ('horas_hombre', 'Horas hombre', 'num'), ('kg_hm', 'kg por hora máquina', 'num'),
                         ('kg_hh', 'kg por hora hombre', 'num'), ('avances', 'Registros', 'num')],
            'total': {'kg': r2(tk), 'horas_maquina': r2(thm), 'horas_hombre': r2(thh),
                      'kg_hm': r2(tk / thm) if thm else None, 'kg_hh': r2(tk / thh) if thh else None},
            'avisos': _avisos_peso(sin_peso),
            'descripcion': 'Datos del reporte de planta (tablets). Horas hombre = horas × personas de la operación '
                           '(maquinistas + ayudantes).'}


def turno_operario(f):
    filas, sin_peso = [], set()
    for a in _avances(f):
        k = kg(a.orden.producto, a.cantidad_buena)
        if k is None:
            sin_peso.add(a.orden.producto)
        filas.append({'fecha': timezone.localtime(a.registrado).date(),
                      'hora': timezone.localtime(a.registrado).strftime('%H:%M'),
                      'turno': str(a.turno_efectivo or '—'), 'operario': a.operario or '—',
                      'puesto': str(a.hora.centro) if a.hora_id else '—', 'orden': a.orden.numero,
                      'producto': a.orden.producto.nombre, 'buena': a.cantidad_buena, 'merma': a.cantidad_merma,
                      'unidad': a.orden.producto.unidad, 'kg': r2(k) if k is not None else None, 'horas': a.horas})
    return {'titulo': 'Producción por turno y operario', 'filas': filas,
            'columnas': [('fecha', 'Fecha', 'fecha'), ('hora', 'Hora', 'texto'), ('turno', 'Turno', 'texto'),
                         ('operario', 'Operario', 'texto'), ('puesto', 'Puesto', 'texto'), ('orden', 'Orden', 'texto'),
                         ('producto', 'Producto', 'texto'), ('buena', 'Cantidad buena', 'num'),
                         ('merma', 'Merma', 'num'), ('unidad', 'Unidad', 'texto'), ('kg', 'kg', 'num'),
                         ('horas', 'Horas', 'num')],
            'total': {'buena': sum((x['buena'] for x in filas), D0), 'merma': sum((x['merma'] for x in filas), D0),
                      'kg': r2(sum((x['kg'] or D0 for x in filas), D0)), 'horas': sum((x['horas'] for x in filas), D0)},
            'avisos': _avisos_peso(sin_peso),
            'descripcion': 'Lo reportado en las tablets de planta. El turno es el elegido al reportar o, si no, el '
                           'que corresponde a la hora del registro (Planillas › Turnos).'}


# ---------------------------------------------------------------- 6. OEE y 12. paradas y mantenimiento
def _paradas(f):
    qs = (Parada.objects.filter(inicio__date__range=[f.desde, f.hasta], fin__isnull=False)
          .select_related('motivo', 'centro', 'equipo'))
    if f.linea:
        qs = qs.filter(centro_id=f.linea)
    if f.equipo:
        qs = qs.filter(equipo_id=f.equipo)
    return list(qs)


def oee(f):
    """OEE = disponibilidad × rendimiento × calidad por línea (puesto)."""
    paradas = defaultdict(lambda: {'plan': D0, 'no_plan': D0})
    for p in _paradas(f):
        paradas[p.centro_id]['plan' if p.motivo.planificada else 'no_plan'] += p.horas
    real, estandar, buena, mala = (defaultdict(lambda: D0) for _ in range(4))
    for a in _avances(f):
        if not a.hora_id:
            continue
        c = a.hora.centro_id
        real[c] += a.horas
        buena[c] += a.cantidad_buena
        mala[c] += a.cantidad_merma
        # horas estándar ganadas: horas planificadas de la operación por unidad × lo producido
        if a.orden.cantidad:
            estandar[c] += a.hora.horas_plan * (a.cantidad_buena + a.cantidad_merma) / a.orden.cantidad
    centros = CentroTrabajo.objects.filter(pk__in=set(real) | set(paradas))
    filas = []
    for c in centros.order_by('codigo'):
        funcionando = real[c.pk]
        no_plan = paradas[c.pk]['no_plan']
        programado = funcionando + no_plan
        disp = funcionando / programado if programado else None
        rend = min(estandar[c.pk] / funcionando, Decimal('1')) if funcionando else None
        total = buena[c.pk] + mala[c.pk]
        cal = buena[c.pk] / total if total else None
        valor = disp * rend * cal if None not in (disp, rend, cal) else None
        filas.append({'linea': str(c), 'funcionando': r2(funcionando), 'paradas_no_plan': r2(no_plan),
                      'paradas_plan': r2(paradas[c.pk]['plan']),
                      'disponibilidad': r2(disp * 100) if disp is not None else None,
                      'rendimiento': r2(rend * 100) if rend is not None else None,
                      'calidad': r2(cal * 100) if cal is not None else None,
                      'oee': r2(valor * 100) if valor is not None else None})
    return {'titulo': 'Eficiencia de equipos (OEE)', 'filas': filas,
            'columnas': [('linea', 'Línea / puesto', 'texto'), ('funcionando', 'Horas produciendo', 'num'),
                         ('paradas_no_plan', 'Paradas no planificadas (h)', 'num'),
                         ('paradas_plan', 'Paradas planificadas (h)', 'num'), ('disponibilidad', 'Disponibilidad %', 'pct'),
                         ('rendimiento', 'Rendimiento %', 'pct'), ('calidad', 'Calidad %', 'pct'), ('oee', 'OEE %', 'pct')],
            'total': None, 'avisos': [] if paradas else ['Registre las paradas (Manufactura › Paradas o desde la '
                                                        'tablet) para medir la disponibilidad.'],
            'descripcion': 'Disponibilidad = horas produciendo ÷ (produciendo + paradas no planificadas). '
                           'Rendimiento = horas estándar de lo producido ÷ horas produciendo. Calidad = buena ÷ '
                           '(buena + merma). Datos de la planta y de las paradas registradas.'}


def paradas_mantenimiento(f):
    """Horas de parada por equipo, tiempo medio entre fallas (MTBF) y tiempo medio de reparación (MTTR)."""
    datos = defaultdict(lambda: {'parada': D0, 'reparacion': D0, 'fallas': 0, 'ordenes': 0})
    for p in _paradas(f):
        if not p.equipo_id:
            continue
        d = datos[p.equipo_id]
        d['parada'] += p.horas
        if p.motivo.es_falla:
            d['fallas'] += 1
            d['reparacion'] += p.horas
    ots = OrdenMantenimiento.objects.filter(fecha_fin__range=[f.desde, f.hasta], estado='CERRADA')
    if f.equipo:
        ots = ots.filter(equipo_id=f.equipo)
    for ot in ots:
        d = datos[ot.equipo_id]
        d['ordenes'] += 1
        if ot.tipo == 'CORRECTIVO':
            d['fallas'] += 1
            d['reparacion'] += ot.horas_parada
            d['parada'] += ot.horas_parada
    operacion = defaultdict(lambda: D0)  # horas trabajadas del puesto del equipo en el periodo
    for a in _avances(Filtros({'desde': f.desde.isoformat(), 'hasta': f.hasta.isoformat()})):
        if a.hora_id:
            operacion[a.hora.centro_id] += a.horas
    filas = []
    for e in Equipo.objects.filter(pk__in=datos).select_related('centro').order_by('codigo'):
        d = datos[e.pk]
        horas_op = operacion.get(e.centro_id) or (e.centro.capacidad_entre(f.desde, f.hasta) if e.centro_id else D0)
        filas.append({'equipo': str(e), 'puesto': str(e.centro or ''), 'parada': r2(d['parada']), 'fallas': d['fallas'],
                      'ordenes': d['ordenes'], 'operacion': r2(horas_op),
                      'mtbf': r2(horas_op / d['fallas']) if d['fallas'] else None,
                      'mttr': r2(d['reparacion'] / d['fallas']) if d['fallas'] else None})
    return {'titulo': 'Paradas y mantenimiento', 'filas': filas,
            'columnas': [('equipo', 'Equipo', 'texto'), ('puesto', 'Puesto', 'texto'), ('parada', 'Horas de parada', 'num'),
                         ('fallas', 'Fallas', 'num'), ('ordenes', 'Órdenes de mantenimiento cerradas', 'num'),
                         ('operacion', 'Horas de operación', 'num'), ('mtbf', 'MTBF (h entre fallas)', 'num'),
                         ('mttr', 'MTTR (h de reparación)', 'num')],
            'total': None, 'avisos': [],
            'descripcion': 'Fallas = órdenes correctivas cerradas y paradas con motivo de falla. MTBF = horas de '
                           'operación ÷ fallas; MTTR = horas de reparación ÷ fallas.'}


# ---------------------------------------------------------------- 7. órdenes abiertas y atrasadas
def abiertas(f):
    qs = OrdenProduccion.objects.filter(estado__in=['BORRADOR', 'CONFIRMADA', 'EN_PROCESO'])
    if f.estado:
        qs = qs.filter(estado=f.estado)
    if f.linea:
        qs = qs.filter(pk__in=HoraOrden.objects.filter(centro_id=f.linea).values('orden_id'))
    if f.valores['desde'] or f.valores['hasta']:
        qs = qs.filter(fecha__range=[f.desde, f.hasta])
    qs = f.productos(qs)
    filas = []
    for o in _ordenes(qs):
        compromiso = o.fecha + timedelta(days=max((o.version__dias_fabricacion or 1) - 1, 0))
        producido = o.producido
        atraso = (f.hoy - compromiso).days
        filas.append({'orden': o.numero or '(borrador)', 'estado': o.estado_texto,
                      'producto': f'{o.producto.codigo} {o.producto.nombre}', 'linea': str(puesto_principal(o) or '—'),
                      'fecha': o.fecha, 'compromiso': compromiso, 'cantidad': o.cantidad, 'producido': producido,
                      'pendiente': max(o.cantidad - producido, D0), 'unidad': o.producto.unidad,
                      'atraso': atraso if atraso > 0 else 0, 'situacion': 'Atrasada' if atraso > 0 else 'A tiempo'})
    filas.sort(key=lambda x: -x['atraso'])
    return {'titulo': 'Órdenes abiertas y atrasadas', 'filas': filas,
            'columnas': [('orden', 'Orden', 'texto'), ('estado', 'Estado', 'texto'), ('producto', 'Producto', 'texto'),
                         ('linea', 'Línea / puesto', 'texto'), ('fecha', 'Fecha planificada', 'fecha'),
                         ('compromiso', 'Debía terminar', 'fecha'), ('cantidad', 'Cantidad', 'num'),
                         ('producido', 'Producido', 'num'), ('pendiente', 'Pendiente', 'num'), ('unidad', 'Unidad', 'texto'),
                         ('atraso', 'Días de atraso', 'num'), ('situacion', 'Situación', 'texto')],
            'total': {'pendiente': None}, 'avisos': [],
            'descripcion': f'{sum(1 for x in filas if x["atraso"])} orden(es) atrasada(s). Debía terminar = fecha '
                           f'planificada + plazo de fabricación de su versión.'}


# ---------------------------------------------------------------- 8. costo por kg y TM
def costo_kg(f):
    por = f.por if f.por in ('orden', 'producto', 'mes') else 'producto'
    grupos, sin_peso = OrderedDict(), set()
    for o in sorted(_terminadas(f), key=fecha_real):
        k = kg(o.producto, o.cantidad_producida)
        if not k:
            sin_peso.add(o.producto)
            continue
        clave = {'orden': o.numero, 'producto': f'{o.producto.codigo} {o.producto.nombre}',
                 'mes': fecha_real(o).strftime('%m/%Y')}[por]
        g = grupos.setdefault(clave, {'kg': D0, 'mat': D0, 'mo': D0, 'maq': D0, 'cif': D0, 'total': D0})
        g['kg'] += k
        g['mat'] += o.costo_materiales - o.merma_anormal
        g['mo'] += o.costo_mano_obra
        g['maq'] += o.costo_maquina
        g['cif'] += o.costo_cif + o.ajuste_liquidacion - o.costo_subproductos
        g['total'] += o.costo_real_final
    filas = []
    for clave, g in grupos.items():
        x = {'grupo': clave, 'kg': r2(g['kg']), 'total': r2(g['total'])}
        for c in ('mat', 'mo', 'maq', 'cif', 'total'):
            x[f'{c}_kg'] = (g[c] / g['kg']).quantize(Decimal('0.0001'))
            x[f'{c}_tm'] = r2(g[c] / g['kg'] * 1000)
        filas.append(x)
    return {'titulo': 'Costo de producción por kg y por TM', 'filas': filas,
            'columnas': [('grupo', {'orden': 'Orden', 'producto': 'Producto', 'mes': 'Mes'}[por], 'texto'),
                         ('kg', 'Producido (kg)', 'num'), ('mat_kg', 'Material S/ kg', 'num'),
                         ('mo_kg', 'Mano de obra S/ kg', 'num'), ('maq_kg', 'Máquina S/ kg', 'num'),
                         ('cif_kg', 'CIF S/ kg', 'num'), ('total_kg', 'Total S/ kg', 'num'),
                         ('total_tm', 'Total S/ TM', 'dinero'), ('total', 'Costo total S/', 'dinero')],
            'total': {'kg': r2(sum((x['kg'] for x in filas), D0)), 'total': r2(sum((x['total'] for x in filas), D0))},
            'avisos': _avisos_peso(sin_peso),
            'descripcion': 'Costo real de las órdenes terminadas (con la liquidación de costo real del cierre, NIC 2; '
                           'sin la merma anormal y descontado lo asignado a subproductos, que va en el CIF).'}


# ---------------------------------------------------------------- 10. trazabilidad de lote
def _venta_de(referencia):
    from ventas.models import Venta
    texto = referencia.split('|')[-1].strip()
    numero = texto.split()[-1] if texto else ''
    if '-' not in numero:
        return None
    serie, num = numero.split('-', 1)
    return (Venta.objects.filter(serie=serie, numero__in=[num, num.lstrip('0')]).select_related('tercero')
            .exclude(estado='ANULADO').first())


def trazabilidad(f):
    """Desde un lote: orden que lo fabricó, lotes de insumos usados y clientes a los que se vendió; y al revés,
    si es un lote de insumo, las órdenes que lo consumieron y a quién se vendió lo fabricado."""
    from ventas.models import Venta
    resultado = {'titulo': 'Trazabilidad de lote', 'lotes': [], 'avisos': []}
    lotes = Lote.objects.select_related('producto')
    if f.lote:
        lotes = lotes.filter(codigo__iexact=f.lote)
    elif f.cliente:  # lotes vendidos a un cliente: salidas de lotes cuyo documento es una venta del cliente
        ventas = {str(v) for v in Venta.objects.filter(tercero_id=f.cliente)}
        salidas = Kardex.objects.filter(tipo='SALIDA', lote__isnull=False).values_list('lote_id', 'referencia')
        ids = sorted({i for i, ref in salidas.iterator() if ref.split('|')[-1].strip() in ventas}, reverse=True)
        lotes = lotes.filter(pk__in=ids[:500])
    else:
        return resultado
    if f.producto:
        lotes = lotes.filter(producto_id=f.producto)
    for lote in lotes[:20]:
        info = {'lote': lote, 'orden': None, 'insumos': [], 'ventas': [], 'usado_en': []}
        # hacia atrás: la orden que fabricó el lote (el lote del producto terminado es el número de la orden)
        orden = OrdenProduccion.objects.filter(numero=lote.codigo, producto=lote.producto).select_related(
            'operacion__tipo').first()
        if orden and orden.operacion_id:
            info['orden'] = orden
            ref = f'{orden.operacion.tipo.nombre} {orden.operacion.numero}'
            for k in Kardex.objects.filter(referencia=ref, tipo='SALIDA').select_related('producto', 'lote'):
                info['insumos'].append({'producto': k.producto, 'lote': k.lote, 'cantidad': k.cantidad})
        # hacia adelante: a quién se vendió y, si es un insumo, qué órdenes lo consumieron
        for k in Kardex.objects.filter(lote=lote, tipo='SALIDA').order_by('fecha', 'id'):
            if k.concepto == 'MANUF':
                usada = OrdenProduccion.objects.filter(operacion__numero=k.referencia.split()[-1]).select_related(
                    'producto').first()
                info['usado_en'].append({'fecha': k.fecha, 'orden': usada, 'cantidad': k.cantidad})
                continue
            venta = _venta_de(k.referencia) if ('|' in k.referencia or k.origen == 'VENTA') else None
            if venta is not None:
                info['ventas'].append({'fecha': k.fecha, 'venta': venta, 'cliente': venta.tercero,
                                       'cantidad': k.cantidad})
        resultado['lotes'].append(info)
    if not resultado['lotes']:
        resultado['avisos'].append('No se encontró el lote.')
    return resultado


# ---------------------------------------------------------------- 11. calidad por lote
def calidad(f):
    qs = (InspeccionCalidad.objects.filter(fecha__range=[f.desde, f.hasta]).exclude(resultado='PENDIENTE')
          .select_related('producto', 'orden').prefetch_related('orden__horas__centro', 'resultados'))
    qs = f.productos(qs)
    if f.linea:
        qs = qs.filter(orden__horas__centro_id=f.linea).distinct()
    grupos, causas = OrderedDict(), defaultdict(int)
    for i in qs:
        linea = str(puesto_principal(i.orden) or '—') if i.orden_id else '—'
        g = grupos.setdefault((f'{i.producto.codigo} {i.producto.nombre}', linea),
                              {'n': 0, 'ok': 0, 'rep': 0, 'rech': 0, 'lotes': set()})
        g['n'] += 1
        g['lotes'].add(i.lote or i.numero)
        if i.resultado in ('APROBADO', 'OBSERVADO'):
            g['ok'] += 1
        elif i.resultado == 'REPROCESO':
            g['rep'] += 1
        else:
            g['rech'] += 1
        if i.resultado in ('RECHAZADO', 'REPROCESO'):
            for r in i.resultados.all():
                if r.conforme is False:
                    causas[r.caracteristica] += 1
    filas = [{'producto': p, 'linea': linea, 'inspecciones': g['n'], 'lotes': len(g['lotes']),
              'aprobado': pct(g['ok'], g['n']), 'reprocesado': pct(g['rep'], g['n']),
              'rechazado': pct(g['rech'], g['n'])} for (p, linea), g in grupos.items()]
    return {'titulo': 'Calidad por lote', 'filas': filas,
            'columnas': [('producto', 'Producto', 'texto'), ('linea', 'Línea / puesto', 'texto'),
                         ('inspecciones', 'Inspecciones', 'num'), ('lotes', 'Lotes', 'num'),
                         ('aprobado', 'Aprobado %', 'pct'), ('reprocesado', 'Reprocesado %', 'pct'),
                         ('rechazado', 'Rechazado %', 'pct')],
            'total': None, 'avisos': [],
            'causas': sorted(causas.items(), key=lambda x: -x[1]),
            'descripcion': 'Inspecciones con resultado en el periodo (aprobado incluye «con observaciones»). Causas '
                           'de rechazo: características fuera de especificación en lotes rechazados o reprocesados.'}


# ---------------------------------------------------------------- 13. producción, ventas y stock
def _stock_al(productos, fecha):
    from django.db.models import Case, F as Campo, When
    from django.db.models.functions import Abs
    stock = defaultdict(lambda: D0)
    ids = {p.pk for p in productos}
    filas = (Kardex.objects.filter(fecha__lte=fecha, producto__tipo='BIEN').values('producto_id')
             .annotate(s=Sum(Case(When(tipo='ENTRADA', then=Abs(Campo('cantidad'))), default=-Abs(Campo('cantidad'))))))
    for k in filas.order_by():
        if k['producto_id'] in ids:
            stock[k['producto_id']] = k['s'] or D0
    return stock


def produccion_ventas_stock(f):
    from ventas.models import VentaItem
    producido = defaultdict(lambda: D0, _terminadas_qs(f).order_by().values_list('producto_id')
                            .annotate(s=Sum('cantidad_producida')))
    vendido = defaultdict(lambda: D0)
    items = (VentaItem.objects.filter(documento__fecha_emision__range=[f.desde, f.hasta],
                                      documento__estado='REGISTRADO', producto__isnull=False)
             .values('producto_id', 'documento__tipo_comprobante').annotate(s=Sum('cantidad')))
    for i in items:
        vendido[i['producto_id']] += -i['s'] if i['documento__tipo_comprobante'] == '07' else i['s']
    ids = set(producido) | {k for k, v in vendido.items() if v}
    productos = [p for p in f.productos(Producto.objects.filter(tipo='BIEN'), '').order_by('codigo').iterator()
                 if p.pk in ids]
    stock = _stock_al(productos, f.hasta)
    dias = (f.hasta - f.desde).days + 1
    filas, sin_peso = [], set()
    for p in productos:
        fk = factor(p, 'TM')
        if fk is None:
            sin_peso.add(p)
            continue
        venta_dia = vendido[p.pk] / dias if dias else D0
        filas.append({'producto': f'{p.codigo} {p.nombre}', 'familia': p.familia,
                      'producido': r3(producido[p.pk] * fk), 'vendido': r3(vendido[p.pk] * fk),
                      'stock': r3(stock[p.pk] * fk),
                      'cobertura': int(stock[p.pk] / venta_dia) if venta_dia > 0 else None})
    return {'titulo': 'Producción, ventas y stock', 'filas': filas,
            'columnas': [('producto', 'Producto', 'texto'), ('familia', 'Familia', 'texto'),
                         ('producido', 'Producido (TM)', 'num'), ('vendido', 'Vendido (TM)', 'num'),
                         ('stock', 'Stock final (TM)', 'num'), ('cobertura', 'Días de cobertura', 'num')],
            'total': {c: r3(sum((x[c] for x in filas), D0)) for c in ('producido', 'vendido', 'stock')},
            'avisos': _avisos_peso(sin_peso),
            'descripcion': 'Vendido = comprobantes de venta del periodo menos notas de crédito. Stock final al último '
                           'día del periodo (kardex). Cobertura = stock ÷ venta diaria promedio del periodo.'}


# ---------------------------------------------------------------- 14. tablero diario
def tablero(f):
    dia = f.fecha
    mes = Filtros({'desde': dia.replace(day=1).isoformat(), 'hasta': dia.isoformat(),
                   'planta': f.valores['planta'], 'unidad': 'TM'})
    ordenes_mes = _terminadas(mes)
    por_dia, sin_peso = defaultdict(lambda: D0), set()
    for o in ordenes_mes:
        k = kg(o.producto, o.cantidad_producida)
        if k is None:
            sin_peso.add(o.producto)
            continue
        por_dia[fecha_real(o)] += k / 1000
    tm_dia, tm_mes = por_dia.get(dia, D0), sum(por_dia.values(), D0)
    plan = plan_real(Filtros({'desde': mes.desde.isoformat(), 'hasta': dia.isoformat(), 'unidad': 'TM',
                              'agrupar': 'orden'}))
    mer = merma(mes, ver_costos=False)
    prod = productividad(Filtros({'desde': mes.desde.isoformat(), 'hasta': dia.isoformat()}))
    abiertas_ = abiertas(Filtros())
    grafico = [(d.strftime('%d'), r3(por_dia.get(d, D0))) for d in
               (mes.desde + timedelta(days=i) for i in range((dia - mes.desde).days + 1))]
    return {'titulo': 'Tablero diario de producción', 'dia': dia, 'tm_dia': r3(tm_dia), 'tm_mes': r3(tm_mes),
            'cumplimiento': plan['total']['cumplimiento'], 'merma_pct': mer['total']['merma_pct'],
            'kg_hh': prod['total']['kg_hh'], 'kg_hm': prod['total']['kg_hm'],
            'atrasadas': [x for x in abiertas_['filas'] if x['atraso']][:10],
            'n_atrasadas': sum(1 for x in abiertas_['filas'] if x['atraso']),
            'grafico': grafico, 'maximo': max((v for _, v in grafico), default=D0) or Decimal('1'),
            'avisos': _avisos_peso(sin_peso)}


# ---------------------------------------------------------------- reporte diario (parte de producción)
def diario(f):
    """Parte diario de producción: lo terminado, lo reportado por turno, los insumos, las paradas, la calidad y
    las órdenes programadas del día."""
    dia = f.fecha
    base = {'desde': dia.isoformat(), 'hasta': dia.isoformat()}
    del_dia = Filtros({**base, **{c: f.valores[c] for c in ('planta', 'linea', 'turno', 'familia', 'producto')}})
    secciones, sin_peso = [], set()

    # 1. producción terminada
    filas = []
    for o in sorted(_terminadas(del_dia), key=lambda o: o.numero or ''):
        k = kg(o.producto, o.cantidad_producida)
        if k is None:
            sin_peso.add(o.producto)
        filas.append({'orden': o.numero, 'producto': f'{o.producto.codigo} {o.producto.nombre}',
                      'linea': str(o.puesto or '—'), 'plan': o.cantidad, 'producido': o.cantidad_producida,
                      'unidad': o.producto.unidad, 'cumplimiento': pct(o.cantidad_producida, o.cantidad),
                      'kg': r2(k) if k is not None else None, 'tm': r3(k / 1000) if k is not None else None})
    secciones.append({'titulo': 'Producción terminada', 'filas': filas, 'columnas': [
        ('orden', 'Orden', 'texto'), ('producto', 'Producto', 'texto'), ('linea', 'Línea / puesto', 'texto'),
        ('plan', 'Planificado', 'num'), ('producido', 'Producido', 'num'), ('unidad', 'Unidad', 'texto'),
        ('cumplimiento', 'Cumplimiento %', 'pct'), ('kg', 'kg', 'num'), ('tm', 'TM', 'num')],
        'total': {'kg': r2(sum((x['kg'] or D0 for x in filas), D0)),
                  'tm': r3(sum((x['tm'] or D0 for x in filas), D0))}})
    tm_dia = secciones[0]['total']['tm']

    # 2. avance de planta por turno y operario
    avances = turno_operario(del_dia)
    secciones.append({'titulo': 'Avance de planta por turno y operario', 'filas': avances['filas'],
                      'columnas': avances['columnas'], 'total': avances['total']})

    # 3. insumos consumidos por las órdenes terminadas
    insumos = OrderedDict()
    for c in sorted(_consumos(del_dia), key=lambda c: c.producto.codigo):
        g = insumos.setdefault(c.producto, {'plan': D0, 'real': D0})
        g['plan'] += c.cantidad_plan
        g['real'] += c.cantidad_real
    filas = [{'insumo': f'{p.codigo} {p.nombre}', 'unidad': p.unidad, 'plan': g['plan'], 'real': g['real'],
              'variacion': g['real'] - g['plan'], 'variacion_pct': pct(g['real'] - g['plan'], g['plan'])}
             for p, g in insumos.items()]
    secciones.append({'titulo': 'Insumos consumidos', 'filas': filas, 'total': None, 'columnas': [
        ('insumo', 'Insumo', 'texto'), ('unidad', 'Unidad', 'texto'), ('plan', 'Según receta', 'num'),
        ('real', 'Real', 'num'), ('variacion', 'Variación', 'num'), ('variacion_pct', 'Variación %', 'pct')]})

    # 4. paradas
    paradas = sorted(_paradas(del_dia), key=lambda p: p.inicio)
    filas = [{'puesto': str(p.centro), 'equipo': str(p.equipo or ''), 'motivo': p.motivo.nombre,
              'tipo': 'Planificada' if p.motivo.planificada else ('Falla' if p.motivo.es_falla else 'No planificada'),
              'inicio': timezone.localtime(p.inicio).strftime('%H:%M'), 'fin': timezone.localtime(p.fin).strftime('%H:%M'),
              'horas': p.horas, 'orden': p.orden.numero if p.orden_id else '', 'operario': p.operario}
             for p in paradas]
    horas_parada = sum((x['horas'] for x in filas), D0)
    secciones.append({'titulo': 'Paradas de línea', 'filas': filas, 'total': {'horas': horas_parada}, 'columnas': [
        ('puesto', 'Puesto', 'texto'), ('equipo', 'Equipo', 'texto'), ('motivo', 'Motivo', 'texto'),
        ('tipo', 'Tipo', 'texto'), ('inicio', 'Inicio', 'texto'), ('fin', 'Fin', 'texto'), ('horas', 'Horas', 'num'),
        ('orden', 'Orden', 'texto'), ('operario', 'Operario', 'texto')]})

    # 5. calidad
    qs = (InspeccionCalidad.objects.filter(fecha=dia).select_related('producto', 'orden')
          .prefetch_related('resultados').order_by('id'))
    qs = f.productos(qs)
    filas = [{'inspeccion': i.numero, 'tipo': i.get_tipo_display(), 'producto': f'{i.producto.codigo} {i.producto.nombre}',
              'lote': i.lote or (i.orden.numero if i.orden_id else ''), 'cantidad': i.cantidad,
              'resultado': i.get_resultado_display(),
              'causas': ', '.join(r.caracteristica for r in i.resultados.all() if r.conforme is False)} for i in qs]
    secciones.append({'titulo': 'Calidad', 'filas': filas, 'total': None, 'columnas': [
        ('inspeccion', 'Inspección', 'texto'), ('tipo', 'Tipo', 'texto'), ('producto', 'Producto', 'texto'),
        ('lote', 'Lote', 'texto'), ('cantidad', 'Cantidad', 'num'), ('resultado', 'Resultado', 'texto'),
        ('causas', 'Fuera de especificación', 'texto')]})

    # 6. órdenes programadas para el día que siguen abiertas
    qs = OrdenProduccion.objects.filter(fecha=dia, estado__in=['BORRADOR', 'CONFIRMADA', 'EN_PROCESO'])
    qs = f.productos(qs)
    if f.linea:
        qs = qs.filter(pk__in=HoraOrden.objects.filter(centro_id=f.linea).values('orden_id'))
    filas = [{'orden': o.numero or '(borrador)', 'estado': o.estado_texto,
              'producto': f'{o.producto.codigo} {o.producto.nombre}', 'linea': str(o.puesto or '—'),
              'cantidad': o.cantidad, 'producido': o.producido, 'pendiente': max(o.cantidad - o.producido, D0),
              'unidad': o.producto.unidad} for o in _ordenes(qs)]
    secciones.append({'titulo': 'Órdenes del día aún abiertas', 'filas': filas, 'total': None, 'columnas': [
        ('orden', 'Orden', 'texto'), ('estado', 'Estado', 'texto'), ('producto', 'Producto', 'texto'),
        ('linea', 'Línea / puesto', 'texto'), ('cantidad', 'Planificado', 'num'), ('producido', 'Producido', 'num'),
        ('pendiente', 'Pendiente', 'num'), ('unidad', 'Unidad', 'texto')]})

    calidad_ok = sum(1 for x in secciones[4]['filas'] if x['resultado'].startswith('Aprobado'))
    resumen = [('Órdenes terminadas', len(secciones[0]['filas'])), ('TM producidas', tm_dia),
               ('Merma reportada (unid.)', avances['total']['merma']), ('Horas trabajadas', avances['total']['horas']),
               ('Horas de parada', horas_parada),
               ('Inspecciones aprobadas', f'{calidad_ok} de {len(secciones[4]["filas"])}'),
               ('Órdenes del día abiertas', len(secciones[5]['filas']))]
    return {'titulo': 'Reporte diario de producción', 'dia': dia, 'secciones': secciones, 'resumen': resumen,
            'avisos': _avisos_peso(sin_peso)}


# ---------------------------------------------------------------- catálogo
REPORTES = OrderedDict([
    ('diario', ('Reporte diario de producción', diario, ['fecha', 'planta', 'linea', 'turno', 'familia', 'producto'])),
    ('produccion', ('Producción en unidad base, kg y TM', produccion, ['fechas', 'planta', 'linea', 'familia',
                                                                       'producto', 'turno', 'agrupar', 'por'])),
    ('plan_real', ('Plan contra real (cumplimiento)', plan_real, ['fechas', 'linea', 'producto', 'familia', 'estado',
                                                                  'unidad', 'agrupar'])),
    ('merma', ('Rendimiento y merma por orden', merma, ['fechas', 'linea', 'producto', 'familia', 'insumo'])),
    ('consumo', ('Consumo de materias primas', consumo_mp, ['fechas', 'insumo', 'familia', 'planta'])),
    ('productividad', ('Productividad', productividad, ['fechas', 'linea', 'turno', 'operario', 'por'])),
    ('oee', ('Eficiencia de equipos (OEE)', oee, ['fechas', 'linea', 'equipo'])),
    ('abiertas', ('Órdenes abiertas y atrasadas', abiertas, ['estado', 'linea', 'producto'])),
    ('costo_kg', ('Costo de producción por kg y TM', costo_kg, ['fechas', 'producto', 'familia', 'por'])),
    ('turno_operario', ('Producción por turno y operario', turno_operario, ['fechas', 'turno', 'operario', 'linea'])),
    ('trazabilidad', ('Trazabilidad de lote', trazabilidad, ['lote', 'producto', 'cliente'])),
    ('calidad', ('Calidad por lote', calidad, ['fechas', 'producto', 'linea'])),
    ('paradas', ('Paradas y mantenimiento', paradas_mantenimiento, ['fechas', 'equipo'])),
    ('prod_ventas', ('Producción, ventas y stock', produccion_ventas_stock, ['fechas', 'familia', 'producto'])),
    ('tablero', ('Tablero diario de producción', tablero, ['fecha', 'planta'])),
])
CON_COSTOS = {'merma', 'consumo', 'costo_kg'}
