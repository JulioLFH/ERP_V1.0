"""Cálculos del presupuesto de obra: aporte de cada recurso, precio unitario desde el APU, parciales, títulos, pie
(costo directo, gastos generales, utilidad, IGV) y explosión de insumos. Redondeo como S10: aportes a 4 decimales,
parciales y precios unitarios a 2."""
from collections import OrderedDict, defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from .models import D0, Partida, PrecioPresupuesto, Presupuesto, RecursoAPU

C2, C4 = Decimal('0.01'), Decimal('0.0001')
ORDEN_TIPOS = ['MO', 'MAT', 'EQ', 'SUB']


def r2(v):
    return Decimal(v).quantize(C2, rounding=ROUND_HALF_UP)


def r4(v):
    return Decimal(v).quantize(C4, rounding=ROUND_HALF_UP)


def aporte(linea, partida):
    """Cantidad del recurso por unidad de partida: con cuadrilla (mano de obra, equipos) = cuadrilla × jornada /
    rendimiento; si no, el aporte escrito."""
    if linea.cuadrilla is not None and linea.recurso.tipo in ('MO', 'EQ') and partida.rendimiento:
        return r4(linea.cuadrilla * partida.jornada / partida.rendimiento)
    return linea.cantidad


def analisis(partida, precios, lineas=None):
    """{'lineas': [{linea, aporte, precio, parcial}], 'subtotales': {tipo: S/}, 'precio': S/} del APU de la partida.
    Herramientas manuales (%mo): parcial = % de la mano de obra de la misma partida."""
    lineas = list(partida.recursos.select_related('recurso')) if lineas is None else lineas
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


def precios_de(presupuesto):
    return dict(PrecioPresupuesto.objects.filter(presupuesto=presupuesto).values_list('recurso_id', 'precio'))


@transaction.atomic
def recalcular(presupuesto):
    """Precios unitarios (APU), parciales, títulos y pie del presupuesto."""
    precios = precios_de(presupuesto)
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
    # títulos: suma de las partidas de su rama
    hojas = [p for p in partidas if not p.es_titulo]
    for t in (p for p in partidas if p.es_titulo):
        prefijo = t.orden + '.'
        t.parcial = sum((h.parcial for h in hojas if h.orden.startswith(prefijo)), D0)
    Partida.objects.bulk_update(partidas, ['precio', 'parcial'])
    if cambios_lineas:
        RecursoAPU.objects.bulk_update(cambios_lineas, ['cantidad'])
    cd = sum((h.parcial for h in hojas), D0)
    presupuesto.costo_directo = cd
    presupuesto.gastos_generales = r2(cd * presupuesto.gastos_generales_pct / 100)
    presupuesto.utilidad = r2(cd * presupuesto.utilidad_pct / 100)
    presupuesto.subtotal = cd + presupuesto.gastos_generales + presupuesto.utilidad
    presupuesto.igv = r2(presupuesto.subtotal * presupuesto.igv_pct / 100)
    presupuesto.total = presupuesto.subtotal + presupuesto.igv
    presupuesto.save(update_fields=['costo_directo', 'gastos_generales', 'utilidad', 'subtotal', 'igv', 'total'])
    return presupuesto


def explosion(presupuesto):
    """Relación de insumos: por recurso, cantidad total (metrado × aporte) y monto. La suma de los montos es el costo
    directo de las partidas con análisis (las de precio sin análisis van aparte)."""
    precios = precios_de(presupuesto)
    totales = OrderedDict()
    sin_apu = D0
    lineas = defaultdict(list)
    for l in RecursoAPU.objects.filter(partida__presupuesto=presupuesto).select_related('recurso'):
        lineas[l.partida_id].append(l)
    for p in presupuesto.partidas.filter(es_titulo=False):
        if p.precio_fijo or not lineas[p.pk]:
            sin_apu += p.parcial
            continue
        for f in analisis(p, precios, lineas[p.pk])['lineas']:
            r = f['linea'].recurso
            t = totales.setdefault(r.pk, {'recurso': r, 'cantidad': D0, 'monto': D0, 'precio': f['precio']})
            if r.es_porcentaje_mo:
                t['cantidad'] += r2(p.metrado * f['parcial'])  # en soles: se lista por su monto
                t['monto'] += r2(p.metrado * f['parcial'])
            else:
                t['cantidad'] += p.metrado * f['aporte']
                t['monto'] += p.metrado * f['parcial']
    filas = sorted(totales.values(), key=lambda t: (ORDEN_TIPOS.index(t['recurso'].tipo), t['recurso'].nombre))
    for t in filas:
        t['cantidad'], t['monto'] = r4(t['cantidad']), r2(t['monto'])
    por_tipo = OrderedDict((tipo, sum((t['monto'] for t in filas if t['recurso'].tipo == tipo), D0))
                           for tipo in ORDEN_TIPOS)
    return {'filas': filas, 'por_tipo': por_tipo, 'sin_apu': sin_apu, 'total': sum(por_tipo.values(), D0) + sin_apu}


@transaction.atomic
def copiar(presupuesto, tipo, nombre=''):
    """Nueva versión (u otro tipo: oferta → contractual → meta) con sus partidas, análisis y precios."""
    nuevo = Presupuesto.objects.create(
        obra=presupuesto.obra, tipo=tipo, nombre=nombre or presupuesto.nombre, fecha_precios=presupuesto.fecha_precios,
        gastos_generales_pct=presupuesto.gastos_generales_pct, utilidad_pct=presupuesto.utilidad_pct,
        igv_pct=presupuesto.igv_pct, copia_de=presupuesto, origen=f'Copia de {presupuesto}'[:40])
    PrecioPresupuesto.objects.bulk_create([PrecioPresupuesto(presupuesto=nuevo, recurso_id=p.recurso_id, precio=p.precio)
                                           for p in presupuesto.precios.all()])
    for p in presupuesto.partidas.prefetch_related('recursos'):
        lineas = list(p.recursos.all())
        p.pk, p.presupuesto = None, nuevo
        p.save()
        RecursoAPU.objects.bulk_create([RecursoAPU(partida=p, recurso_id=l.recurso_id, cuadrilla=l.cuadrilla,
                                                   cantidad=l.cantidad, orden=l.orden) for l in lineas])
    return recalcular(nuevo)


def asegurar_precio(presupuesto, recurso, precio=None):
    """El recurso en la lista de precios del presupuesto (con su precio referencial si no se indica otro)."""
    obj, creado = PrecioPresupuesto.objects.get_or_create(
        presupuesto=presupuesto, recurso=recurso, defaults={'precio': recurso.precio if precio is None else precio})
    if not creado and precio is not None and obj.precio != precio:
        obj.precio = precio
        obj.save(update_fields=['precio'])
    return obj
