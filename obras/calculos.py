"""Cálculos del presupuesto de obra: aporte de cada recurso, precio unitario desde el APU, parciales, títulos,
subpresupuestos, pie (costo directo, gastos generales en % o detallados, utilidad, IGV), sub-análisis y explosión de
insumos. Redondeo como S10: aportes a 4 decimales, parciales y precios unitarios a 2."""
from collections import OrderedDict, defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from .models import D0, ComponenteRecurso, Partida, PrecioPresupuesto, Presupuesto, RecursoAPU, Subpresupuesto

C2, C4 = Decimal('0.01'), Decimal('0.0001')
ORDEN_TIPOS = ['MO', 'MAT', 'EQ', 'SUB']


def r2(v):
    return Decimal(v).quantize(C2, rounding=ROUND_HALF_UP)


def r4(v):
    return Decimal(v).quantize(C4, rounding=ROUND_HALF_UP)


def aporte(linea, partida):
    """Cantidad del recurso por unidad de partida: con cuadrilla (mano de obra, equipos) = cuadrilla × jornada /
    rendimiento; si no, el aporte escrito. `partida` puede ser un sub-análisis (tiene rendimiento y jornada)."""
    if linea.cuadrilla is not None and linea.recurso.tipo in ('MO', 'EQ') and partida.rendimiento:
        return r4(linea.cuadrilla * partida.jornada / partida.rendimiento)
    return linea.cantidad


def analisis(partida, precios, lineas=None):
    """{'lineas': [{linea, aporte, precio, parcial}], 'subtotales': {tipo: S/}, 'precio': S/} del APU de la partida
    (o de un sub-análisis, con sus componentes). Herramientas manuales (%mo): parcial = % de la mano de obra."""
    if lineas is None:
        lineas = list(partida.recursos.select_related('recurso') if isinstance(partida, Partida)
                      else partida.componentes.select_related('recurso'))
    filas, subtotales = [], OrderedDict((t, D0) for t in ORDEN_TIPOS)
    porcentuales = []
    for l in lineas:
        cantidad = aporte(l, partida)
        if l.recurso.es_porcentaje_mo:
            porcentuales.append((l, cantidad))
            continue
        precio = precios.get(l.recurso_id, l.recurso.precio)
        parcial = r2(cantidad * precio)
        subtotales[l.recurso.tipo] += parcial
        filas.append({'linea': l, 'aporte': cantidad, 'precio': precio, 'parcial': parcial})
    for l, cantidad in porcentuales:  # sobre la mano de obra ya sumada
        base = subtotales['MO']
        parcial = r2(base * cantidad / 100)
        subtotales[l.recurso.tipo] += parcial
        filas.append({'linea': l, 'aporte': cantidad, 'precio': base, 'parcial': parcial})
    filas.sort(key=lambda f: (ORDEN_TIPOS.index(f['linea'].recurso.tipo), f['linea'].orden, f['linea'].pk or 0))
    return {'lineas': filas, 'subtotales': subtotales, 'precio': sum(subtotales.values(), D0)}


# ---------------------------------------------------------------- sub-análisis
def componentes_de():
    """{id del sub-análisis: [ComponenteRecurso…]} del catálogo."""
    comps = defaultdict(list)
    for c in ComponenteRecurso.objects.select_related('recurso', 'padre').order_by('orden', 'id'):
        comps[c.padre_id].append(c)
    return comps


def _precios_subanalisis(precios, comps):
    """Precio de cada sub-análisis con los precios del presupuesto (un sub-análisis puede usar otro)."""
    resueltos = {}

    def precio(rid, camino):
        if rid in resueltos:
            return resueltos[rid]
        if rid in camino:  # ciclo: se toma el precio guardado
            return precios.get(rid, D0)
        lineas = comps[rid]
        locales = dict(precios)
        for l in lineas:
            if comps.get(l.recurso_id):
                locales[l.recurso_id] = precio(l.recurso_id, camino | {rid})
        resueltos[rid] = r2(analisis(lineas[0].padre, locales, lineas)['precio'])
        return resueltos[rid]

    for rid in list(comps):
        precio(rid, frozenset())
    return resueltos


def precios_de(presupuesto, comps=None):
    """{recurso: precio} del presupuesto; el de los sub-análisis sale de sus componentes."""
    precios = dict(PrecioPresupuesto.objects.filter(presupuesto=presupuesto).values_list('recurso_id', 'precio'))
    comps = componentes_de() if comps is None else comps
    if comps:
        precios.update(_precios_subanalisis(precios, comps))
    return precios


# ---------------------------------------------------------------- recalcular
def _hojas_por_titulo(partidas):
    """{título: [partidas de su rama]} (dentro del mismo subpresupuesto)."""
    hojas = [p for p in partidas if not p.es_titulo]
    return {t: [h for h in hojas if h.subpresupuesto_id == t.subpresupuesto_id and h.orden.startswith(t.orden + '.')]
            for t in partidas if t.es_titulo}


@transaction.atomic
def recalcular(presupuesto):
    """Precios unitarios (APU), parciales, títulos, subpresupuestos y pie del presupuesto."""
    comps = componentes_de()
    precios = precios_de(presupuesto, comps)
    partidas = list(presupuesto.partidas.all())
    lineas = defaultdict(list)
    for l in RecursoAPU.objects.filter(partida__presupuesto=presupuesto).select_related('recurso'):
        lineas[l.partida_id].append(l)
    cambios_lineas = []
    for p in partidas:
        if p.es_titulo:
            continue
        for l in lineas[p.pk]:  # el aporte con cuadrilla se guarda calculado (como lo muestra S10)
            if l.cuadrilla is not None and l.recurso.tipo in ('MO', 'EQ') and p.rendimiento:
                nuevo = aporte(l, p)
                if nuevo != l.cantidad:
                    l.cantidad = nuevo
                    cambios_lineas.append(l)
        if not p.precio_fijo and lineas[p.pk]:
            p.precio = r2(analisis(p, precios, lineas[p.pk])['precio'])
        p.parcial = r2(p.metrado * p.precio)
    for t, ramas in _hojas_por_titulo(partidas).items():
        t.parcial = sum((h.parcial for h in ramas), D0)
    Partida.objects.bulk_update(partidas, ['precio', 'parcial'])
    if cambios_lineas:
        RecursoAPU.objects.bulk_update(cambios_lineas, ['cantidad'])
    # el precio guardado de los sub-análisis se mantiene al día (lista de precios del presupuesto)
    derivados = [x for x in presupuesto.precios.filter(recurso_id__in=list(comps))
                 if x.precio != precios.get(x.recurso_id, x.precio)]
    for x in derivados:
        x.precio = precios[x.recurso_id]
    PrecioPresupuesto.objects.bulk_update(derivados, ['precio'])
    hojas = [p for p in partidas if not p.es_titulo]
    subs = list(presupuesto.subpresupuestos.all())
    for s in subs:
        s.parcial = sum((h.parcial for h in hojas if h.subpresupuesto_id == s.pk), D0)
    Subpresupuesto.objects.bulk_update(subs, ['parcial'])
    gg_ids = {s.pk for s in subs if s.es_gastos_generales}
    cd = sum((h.parcial for h in hojas if h.subpresupuesto_id not in gg_ids), D0)
    presupuesto.costo_directo = cd
    presupuesto.gastos_generales = (sum((s.parcial for s in subs if s.pk in gg_ids), D0) if gg_ids
                                    else r2(cd * presupuesto.gastos_generales_pct / 100))
    presupuesto.utilidad = r2(cd * presupuesto.utilidad_pct / 100)
    presupuesto.subtotal = cd + presupuesto.gastos_generales + presupuesto.utilidad
    presupuesto.igv = r2(presupuesto.subtotal * presupuesto.igv_pct / 100)
    presupuesto.total = presupuesto.subtotal + presupuesto.igv
    presupuesto.save(update_fields=['costo_directo', 'gastos_generales', 'utilidad', 'subtotal', 'igv', 'total'])
    return presupuesto


def gastos_generales_detallados(presupuesto):
    return presupuesto.subpresupuestos.filter(es_gastos_generales=True).exists()


# ---------------------------------------------------------------- relación de insumos
def explosion(presupuesto, subpresupuesto=None, explotar=True):
    """Relación de insumos: por recurso, cantidad total (metrado × aporte) y parcial = cantidad × precio (como S10).
    Los sub-análisis se explotan hasta sus recursos básicos (con explotar=False se listan como un recurso más). La
    diferencia frente a los parciales del presupuesto es el redondeo de cada análisis y se muestra aparte."""
    comps = componentes_de() if explotar else {}
    precios = precios_de(presupuesto, comps or None)
    totales = OrderedDict()
    sin_apu = referencia = D0
    lineas = defaultdict(list)
    for l in RecursoAPU.objects.filter(partida__presupuesto=presupuesto).select_related('recurso'):
        lineas[l.partida_id].append(l)

    def sumar(r, cantidad=D0, soles=D0):
        t = totales.setdefault(r.pk, {'recurso': r, 'cantidad': D0, 'soles': D0,
                                      'precio': precios.get(r.pk, r.precio)})
        t['cantidad'] += cantidad
        t['soles'] += soles

    def agregar(fila, multiplo, camino):
        r = fila['linea'].recurso
        if r.es_porcentaje_mo:  # herramientas: un monto en soles (porcentaje de la mano de obra)
            sumar(r, soles=multiplo * fila['parcial'])
        elif explotar and comps.get(r.pk) and r.pk not in camino:
            cantidad = multiplo * fila['aporte']
            for g in analisis(r, precios, comps[r.pk])['lineas']:
                agregar(g, cantidad, camino | {r.pk})
        else:
            sumar(r, cantidad=multiplo * fila['aporte'])

    partidas = presupuesto.partidas.filter(es_titulo=False)
    if subpresupuesto is not None:
        partidas = partidas.filter(subpresupuesto=subpresupuesto)
    for p in partidas:
        if p.precio_fijo or not lineas[p.pk]:
            sin_apu += p.parcial
            continue
        referencia += p.parcial
        for f in analisis(p, precios, lineas[p.pk])['lineas']:
            agregar(f, p.metrado, frozenset())
    filas = sorted(totales.values(), key=lambda t: (ORDEN_TIPOS.index(t['recurso'].tipo), t['recurso'].nombre))
    for t in filas:
        if t['recurso'].es_porcentaje_mo:
            t['cantidad'] = t['monto'] = r2(t['soles'])
        else:
            t['cantidad'] = r4(t['cantidad'])
            t['monto'] = r2(t['cantidad'] * t['precio'])  # el parcial es la cantidad mostrada por su precio
    por_tipo = OrderedDict((tipo, sum((t['monto'] for t in filas if t['recurso'].tipo == tipo), D0))
                           for tipo in ORDEN_TIPOS)
    insumos = sum(por_tipo.values(), D0)
    return {'filas': filas, 'por_tipo': por_tipo, 'sin_apu': sin_apu, 'insumos': insumos,
            'diferencia': referencia - insumos, 'total': referencia + sin_apu}


# ---------------------------------------------------------------- copiar y precios
@transaction.atomic
def copiar(presupuesto, tipo, nombre=''):
    """Nueva versión (u otro tipo: oferta → contractual → meta) con sus subpresupuestos, partidas, análisis y precios."""
    nuevo = Presupuesto.objects.create(
        obra=presupuesto.obra, tipo=tipo, nombre=nombre or presupuesto.nombre, fecha_precios=presupuesto.fecha_precios,
        gastos_generales_pct=presupuesto.gastos_generales_pct, utilidad_pct=presupuesto.utilidad_pct,
        igv_pct=presupuesto.igv_pct, copia_de=presupuesto, origen=f'Copia de {presupuesto}'[:40])
    PrecioPresupuesto.objects.bulk_create([PrecioPresupuesto(presupuesto=nuevo, recurso_id=p.recurso_id, precio=p.precio)
                                           for p in presupuesto.precios.all()])
    subs = {}
    for s in presupuesto.subpresupuestos.all():
        subs[s.pk] = Subpresupuesto.objects.create(presupuesto=nuevo, codigo=s.codigo, nombre=s.nombre,
                                                   es_gastos_generales=s.es_gastos_generales)
    for p in presupuesto.partidas.prefetch_related('recursos'):
        lineas = list(p.recursos.all())
        p.pk, p.presupuesto = None, nuevo
        p.subpresupuesto = subs.get(p.subpresupuesto_id)
        p.save()
        RecursoAPU.objects.bulk_create([RecursoAPU(partida=p, recurso_id=l.recurso_id, cuadrilla=l.cuadrilla,
                                                   cantidad=l.cantidad, orden=l.orden) for l in lineas])
    return recalcular(nuevo)


def asegurar_precio(presupuesto, recurso, precio=None, _camino=frozenset()):
    """El recurso en la lista de precios del presupuesto (con su precio referencial si no se indica otro). Un
    sub-análisis trae también a sus componentes (sus precios se editan en la lista; el suyo se calcula)."""
    obj, creado = PrecioPresupuesto.objects.get_or_create(
        presupuesto=presupuesto, recurso=recurso, defaults={'precio': recurso.precio if precio is None else precio})
    if not creado and precio is not None and obj.precio != precio and not recurso.es_subanalisis:
        obj.precio = precio
        obj.save(update_fields=['precio'])
    if recurso.es_subanalisis and recurso.pk not in _camino:
        for c in recurso.componentes.select_related('recurso'):
            asegurar_precio(presupuesto, c.recurso, _camino=_camino | {recurso.pk})
    return obj
