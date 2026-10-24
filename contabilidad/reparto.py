"""Reparto de gastos entre centros de costo (ciclos de distribución de SAP CO, KSV5 / KSU5): cada empresa arma sus
reglas —centros de origen y cuentas, base (una cifra propia como los m², el costo directo, las compras, las ventas, la
planilla, % fijo o partes iguales), destinos, cuenta del asiento, vigencia y orden en cascada—.

Simular no graba. Ejecutar marca el periodo y genera un asiento por regla (haber al centro de origen, debe a cada
destino, con sus destinos 9x); mientras el periodo esté abierto la centralización lo recalcula con los datos al día.
Revertir lo quita y queda en la bitácora. Un periodo cerrado no se ejecuta ni se revierte."""
from collections import OrderedDict, defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum

from core.models import r2

from .centralizar import Borrador, ErrorContable, _fin_mes, _rango, _repartir
from .models import (Asiento, AsientoLinea, CentroCosto, CuentaContable, EjecucionReparto, PeriodoContable,
                     ReglaReparto, ValorCifra)

D0 = Decimal('0')


class ErrorReparto(Exception):
    pass


def _prefijos(prefijos):
    q = Q()
    for p in prefijos:
        q |= Q(cuenta__codigo__startswith=p)
    return q


def _gastos(periodo, centros, prefijos=('6',)):
    """Líneas de gasto por naturaleza (sin destinos ni reparto) del periodo en los centros."""
    return (AsientoLinea.objects.filter(asiento__periodo=periodo, es_destino=False, centro_costo_id__in=centros)
            .filter(_prefijos(prefijos)).exclude(asiento__origen='REPARTO'))


def _origenes(regla):
    ids = set(regla.origenes.values_list('pk', flat=True))
    if regla.incluir_dependientes:
        for c in CentroCosto.objects.filter(pk__in=list(ids)):
            ids |= c.descendientes_ids()
    return ids


def _destinos(regla):
    """Centros de destino (activos, sin los de origen). «Dependen de»: los últimos de la rama (las obras)."""
    origen = _origenes(regla)
    if regla.tipo_destino == 'HIJOS' and regla.centro_padre_id:
        rama = regla.centro_padre.descendientes_ids() - {regla.centro_padre_id}
        padres = set(CentroCosto.objects.filter(pk__in=rama, activo=True).exclude(padre=None)
                     .values_list('padre_id', flat=True))
        ids = rama - padres
    elif regla.tipo_destino == 'LISTA':
        ids = set(regla.destinos.values_list('pk', flat=True))
    else:
        ids = set(CentroCosto.objects.filter(activo=True).values_list('pk', flat=True))
    return set(CentroCosto.objects.filter(pk__in=ids - origen, activo=True).values_list('pk', flat=True))


def _neto_por_centro(qs):
    return {c: (d or D0) - (h or D0) for c, d, h in qs.values('centro_costo_id').annotate(
        d=Sum('debe'), h=Sum('haber')).values_list('centro_costo_id', 'd', 'h')}


def _documentos(modelo, periodo, centros):
    """Base imponible en soles de los comprobantes del periodo con el centro (las notas de crédito restan)."""
    total = defaultdict(lambda: D0)
    for c, tipo, t, igv in modelo.objects.filter(periodo=periodo, estado='REGISTRADO', centro_costo_id__in=centros) \
            .values_list('centro_costo_id', 'tipo_comprobante', 'total_pen', 'igv_pen'):
        total[c] += (t - igv) * (-1 if tipo == '07' else 1)
    return total


def bases(regla, periodo, destinos):
    """{centro: base} de la regla en el periodo (solo los que tienen base mayor a cero)."""
    destinos = list(destinos)
    if regla.base == 'FIJO':  # el % de cada destino de la lista
        porcentajes = {str(c): p for c, p in (regla.porcentajes or {}).items()}
        valores = {c: Decimal(str(porcentajes[str(c)])) for c in destinos if str(porcentajes.get(str(c), '')).strip()}
    elif regla.base == 'IGUALES':
        valores = {c: Decimal('1') for c in destinos}
    elif regla.base == 'CIFRA':
        valores = {}
        for v in ValorCifra.objects.filter(cifra=regla.cifra, centro_id__in=destinos, periodo__lte=periodo) \
                .order_by('centro_id', 'periodo'):
            valores[v.centro_id] = v.valor  # el último registrado hasta el periodo
    elif regla.base == 'OBRA_M2':
        from obras.models import Obra
        valores = dict(Obra.objects.filter(centro_costo_id__in=destinos).values_list('centro_costo_id', 'area_m2'))
    elif regla.base == 'COSTO_DIRECTO':
        valores = _neto_por_centro(_gastos(periodo, destinos))
    elif regla.base == 'PLANILLA':
        valores = _neto_por_centro(_gastos(periodo, destinos, ('62',)))
    elif regla.base == 'HORAS_EQUIPO':  # equipos propios: su costo (depreciación, combustible…) a las obras
        from obras.equipos import horas_por_centro
        valores = horas_por_centro(periodo, destinos)
    elif regla.base == 'COMPRAS':
        from compras.models import Compra
        valores = _documentos(Compra, periodo, destinos)
    elif regla.base == 'VENTAS':
        from ventas.models import Venta
        valores = _documentos(Venta, periodo, destinos)
    else:
        valores = {}
    return OrderedDict(sorted(((c, v) for c, v in valores.items() if v and v > 0),
                              key=lambda x: x[0]))


def calcular(periodo):
    """[{regla, origenes [(centro, cuenta, monto)], destinos [{centro, base, pct, monto}], total, aviso}] de las
    reglas vigentes, en cascada (lo que recibe un centro en una regla lo puede repartir la siguiente)."""
    recibido = defaultdict(lambda: D0)  # (centro, cuenta) -> lo asignado por las reglas anteriores
    resultado = []
    centros = {c.pk: c for c in CentroCosto.objects.all()}
    cuentas = {}
    for regla in ReglaReparto.objects.filter(activo=True).select_related('cifra', 'centro_padre', 'cuenta_asignacion'):
        if not regla.vigente(periodo):
            continue
        origen = _origenes(regla)
        montos = defaultdict(lambda: D0)
        for c, cta, d, h in _gastos(periodo, origen, regla.prefijos).values('centro_costo_id', 'cuenta_id').annotate(
                d=Sum('debe'), h=Sum('haber')).values_list('centro_costo_id', 'cuenta_id', 'd', 'h'):
            montos[(c, cta)] += (d or D0) - (h or D0)
        prefijos = regla.prefijos
        for (c, cta), v in recibido.items():
            if c in origen:
                if cta not in cuentas:
                    cuentas[cta] = CuentaContable.objects.get(pk=cta)
                if any(cuentas[cta].codigo.startswith(p) for p in prefijos):
                    montos[(c, cta)] += v
        montos = {k: r2(v) for k, v in montos.items() if r2(v) > 0}
        fila = {'regla': regla, 'origenes': [], 'destinos': [], 'total': sum(montos.values(), D0), 'aviso': ''}
        resultado.append(fila)
        if not montos:
            fila['aviso'] = 'Los centros de origen no tienen gastos en el periodo.'
            continue
        pesos = bases(regla, periodo, _destinos(regla))
        if not pesos:
            fila['aviso'] = 'Ningún centro de destino tiene base en el periodo: no se reparte.'
            continue
        suma = sum(pesos.values(), D0)
        por_destino = defaultdict(lambda: D0)
        for (c, cta), monto in sorted(montos.items()):
            if cta not in cuentas:
                cuentas[cta] = CuentaContable.objects.get(pk=cta)
            cuenta = regla.cuenta_asignacion or cuentas[cta]
            partes = _repartir(monto, pesos)
            fila['origenes'].append({'centro': centros[c], 'cuenta': cuentas[cta], 'cuenta_asiento': cuenta,
                                     'monto': monto, 'partes': partes})
            recibido[(c, cuenta.pk)] -= monto
            cuentas[cuenta.pk] = cuenta
            for destino, parte in partes:
                por_destino[destino] += parte
                recibido[(destino, cuenta.pk)] += parte
        fila['destinos'] = [{'centro': centros[d], 'base': pesos[d], 'pct': (pesos[d] / suma * 100).quantize(Decimal('0.01')),
                             'monto': por_destino[d]} for d in pesos]
    return resultado


def _grabar(periodo, filas):
    asientos = 0
    centros = {c.pk: c for c in CentroCosto.objects.all()}
    for fila in filas:
        if not fila['origenes']:
            continue
        regla = fila['regla']
        b = Borrador(Asiento(fecha=_fin_mes(periodo), libro='05', origen='REPARTO',
                             glosa=f'Reparto de gastos {periodo}: {regla.nombre}'[:250]))
        for o in fila['origenes']:
            b.add(o['cuenta_asiento'], haber=o['monto'], centro_costo=o['centro'], glosa=f'Reparto: {regla.nombre}'[:200])
            for destino, parte in o['partes']:
                b.add(o['cuenta_asiento'], debe=parte, centro_costo=centros[destino],
                      glosa=f'Asignado de {o["centro"].codigo}'[:200])
        b.agregar_destinos()
        if b.grabar():
            asientos += 1
    return asientos


def detalle(filas):
    """Lo repartido (para la bitácora y el reporte): de qué centro y cuenta vino cada sol y a quién fue."""
    return [{'regla': f['regla'].nombre, 'base': f['regla'].get_base_display(), 'total': str(f['total']),
             'aviso': f['aviso'],
             'origenes': [{'centro': o['centro'].codigo, 'cuenta': o['cuenta'].codigo, 'monto': str(o['monto'])}
                          for o in f['origenes']],
             'destinos': [{'centro': d['centro'].codigo, 'nombre': d['centro'].nombre, 'base': str(d['base']),
                           'pct': str(d['pct']), 'monto': str(d['monto'])} for d in f['destinos']]} for f in filas]


def regenerar(periodo):
    """Vuelve a generar los asientos del reparto ejecutado (la centralización ya borró los anteriores)."""
    ej = EjecucionReparto.objects.filter(periodo=periodo).first()
    if ej is None:
        return 0
    filas = calcular(periodo)
    n = _grabar(periodo, filas)
    ej.detalle = detalle(filas)
    ej.save(update_fields=['detalle'])
    return n


def _abierto(periodo):
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorReparto(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado.')


@transaction.atomic
def ejecutar(periodo, usuario):
    from core.auditoria import registrar
    _abierto(periodo)
    if EjecucionReparto.objects.filter(periodo=periodo).exists():
        raise ErrorReparto('El reparto de este periodo ya se ejecutó: reviértalo para corregir las bases y volver a '
                           'ejecutarlo.')
    Asiento.objects.filter(periodo=periodo, origen='REPARTO').delete()
    filas = calcular(periodo)
    try:
        n = _grabar(periodo, filas)
    except ErrorContable as exc:
        raise ErrorReparto(str(exc)) from exc
    if not n:
        raise ErrorReparto('No hay gastos que repartir en el periodo con las reglas vigentes.')
    ej = EjecucionReparto.objects.create(periodo=periodo, usuario=usuario, detalle=detalle(filas))
    registrar('CREAR', ej, {'asientos': n, 'total': str(sum((f['total'] for f in filas if f['origenes']), D0))})
    return ej, n


@transaction.atomic
def revertir(periodo, usuario, motivo):
    from core.auditoria import registrar
    _abierto(periodo)
    if len((motivo or '').strip()) < 5:
        raise ErrorReparto('Indique el motivo (por ejemplo, qué base se corrige).')
    ej = EjecucionReparto.objects.filter(periodo=periodo).first()
    if ej is None:
        raise ErrorReparto('El reparto de este periodo no está ejecutado.')
    n, _ = Asiento.objects.filter(periodo=periodo, origen='REPARTO').delete()
    registrar('ANULAR', ej, {'detalle': ej.detalle}, motivo)
    ej.delete()
    return n


# ---------------------------------------------------------------- resultado por centro (obra)
def ventas_por_centro(desde, hasta):
    """{centro: ingreso sin IGV} de los comprobantes con centro de costo. Los anticipos (líneas que van a la 122:
    adelantos de obra, cuotas de inmuebles) no son ventas y sus deducciones en las valorizaciones tampoco restan; las
    unidades inmobiliarias entregadas suman su ingreso reconocido."""
    from ventas.models import VentaItem
    ventas = defaultdict(lambda: D0)
    for c, tipo, moneda, tc, subtotal, cuenta, deduce in VentaItem.objects.filter(
            documento__periodo__gte=desde, documento__periodo__lte=hasta, documento__estado='REGISTRADO',
            documento__centro_costo__isnull=False).values_list(
            'documento__centro_costo_id', 'documento__tipo_comprobante', 'documento__moneda', 'documento__tipo_cambio',
            'subtotal', 'producto__cuenta_venta__codigo', 'anticipo_de_id'):
        if deduce or (cuenta or '').startswith('122'):  # anticipos de clientes (PCGE 2019 y 2026)
            continue
        ventas[c] += subtotal * (tc if moneda == 'USD' else 1) * (-1 if tipo == '07' else 1)
    try:
        from obras.models import VentaInmueble
        for c, ingreso in VentaInmueble.objects.filter(
                fecha_entrega__gte=_rango(desde)[0], fecha_entrega__lte=_rango(hasta)[1]).exclude(
                estado='ANULADA').values_list('obra__centro_costo_id', 'ingreso'):
            if c:
                ventas[c] += ingreso
    except ImportError:  # pragma: no cover
        pass
    return ventas


def resultado(desde, hasta, solo_obras=False):
    """Por centro de costo: ventas (comprobantes con el centro), gastos propios (clase 6 sin el reparto), asignados
    (reparto recibido menos entregado) y resultado; en las obras, el gasto general del presupuesto frente al real."""
    lineas = AsientoLinea.objects.filter(asiento__periodo__gte=desde, asiento__periodo__lte=hasta, es_destino=False,
                                         centro_costo__isnull=False, cuenta__codigo__startswith='6')
    propios = _neto_por_centro(lineas.exclude(asiento__origen='REPARTO'))
    asignados = _neto_por_centro(lineas.filter(asiento__origen='REPARTO'))
    ventas = ventas_por_centro(desde, hasta)
    obras = {}
    try:
        from obras.models import Obra
        for o in Obra.objects.select_related('centro_costo').prefetch_related('presupuestos'):
            presupuestos = sorted(o.presupuestos.all(), key=lambda p: (p.estado == 'APROBADO', p.tipo == 'META',
                                                                       p.version))
            obras[o.centro_costo_id] = (o, presupuestos[-1] if presupuestos else None)
    except ImportError:  # pragma: no cover
        pass
    ids = set(propios) | set(asignados) | set(ventas)
    if solo_obras:
        ids &= set(obras)
    filas = []
    for c in CentroCosto.objects.filter(pk__in=ids).order_by('codigo'):
        obra, pres = obras.get(c.pk, (None, None))
        f = {'centro': c, 'obra': obra, 'ventas': r2(ventas.get(c.pk, D0)), 'propios': r2(propios.get(c.pk, D0)),
             'asignados': r2(asignados.get(c.pk, D0)), 'presupuesto': pres,
             'gg_presupuesto': pres.gastos_generales if pres else None}
        f['resultado'] = f['ventas'] - f['propios'] - f['asignados']
        f['gg_avance'] = ((f['asignados'] / f['gg_presupuesto'] * 100).quantize(Decimal('0.1'))
                          if f['gg_presupuesto'] else None)
        filas.append(f)
    totales = {k: sum((f[k] for f in filas), D0) for k in ('ventas', 'propios', 'asignados', 'resultado')}
    return filas, totales
