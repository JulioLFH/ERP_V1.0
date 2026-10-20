"""Reportes masivos de inventario al estilo SAP: stock a una fecha (MB5B), stock por almacén, lote y ubicación con
comprometido y bloqueado (MB52/MMBE), kardex masivo (MB51), valorización a una fecha con cuadre contable (MC.9) y
rotación / stock sin movimiento (MC46). Cada uno devuelve encabezados y filas para la pantalla y el Excel."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, DecimalField, F, Max, OuterRef, Q, Subquery, Sum, When
from django.utils import timezone

from core.models import Kardex, Producto, StockAlmacen, StockLote, r2

D0 = Decimal('0')
_DEC = DecimalField(max_digits=16, decimal_places=2)
_NETO = Sum(Case(When(tipo='ENTRADA', then=F('cantidad')), default=-F('cantidad'), output_field=_DEC))
CUENTAS_EXISTENCIAS = ('20', '21', '22', '23', '24', '25', '26')


def _productos(q='', familia=''):
    qs = Producto.objects.filter(tipo='BIEN')
    if q:
        qs = qs.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q))
    if familia:
        qs = qs.filter(familia__iexact=familia)
    return qs


def familias():
    return list(Producto.objects.filter(tipo='BIEN').exclude(familia='').values_list('familia', flat=True)
                .distinct().order_by('familia'))


def _costos_al(fecha, ids):
    """{producto_id: costo promedio vigente a la fecha} (el del último movimiento hasta esa fecha)."""
    ultimo = Kardex.objects.filter(producto=OuterRef('pk'), fecha__lte=fecha).order_by('-fecha', '-id')
    return dict(Producto.objects.filter(pk__in=ids).annotate(c=Subquery(ultimo.values('costo_promedio')[:1]))
                .values_list('pk', 'c'))


# ---------------------------------------------------------------- MB5B: stock a una fecha
def stock_a_fecha(fecha, almacenes=None, q='', familia='', por_lote=False, ubicacion=''):
    productos = {p.pk: p for p in _productos(q, familia)}
    campos = ['producto_id', 'almacen_id'] + (['lote_id'] if por_lote else [])
    qs = Kardex.objects.filter(fecha__lte=fecha, producto_id__in=productos)
    if almacenes:
        qs = qs.filter(almacen_id__in=almacenes)
    saldos = [f for f in qs.values(*campos).annotate(c=_NETO).order_by() if f['c']]
    costos = _costos_al(fecha, {f['producto_id'] for f in saldos})
    from core.models import Almacen, Lote
    nombres_alm = dict(Almacen.objects.values_list('pk', 'codigo'))
    lotes = {l.pk: l for l in Lote.objects.filter(pk__in={f.get('lote_id') for f in saldos} - {None})}
    lugares = {(s.producto_id, s.almacen_id): s.ubicacion.codigo for s in
               StockAlmacen.objects.exclude(ubicacion=None).select_related('ubicacion')}
    filas, total = [], D0
    for f in sorted(saldos, key=lambda x: (productos[x['producto_id']].codigo or '', x['almacen_id'] or 0)):
        p = productos[f['producto_id']]
        lugar = lugares.get((p.pk, f['almacen_id']), '')
        if ubicacion and not lugar.upper().startswith(ubicacion.upper()):
            continue
        costo = costos.get(p.pk) or p.costo_promedio
        valor = r2(f['c'] * costo)
        total += valor
        lote = lotes.get(f.get('lote_id'))
        filas.append([p.codigo, p.nombre, p.familia, nombres_alm.get(f['almacen_id'], ''), lugar]
                     + ([lote.codigo if lote else '', lote.vencimiento if lote else None] if por_lote else [])
                     + [p.unidad, f['c'], costo, valor])
    encabezados = (['Código', 'Producto', 'Familia', 'Almacén', 'Ubicación'] + (['Lote', 'Vence'] if por_lote else [])
                   + ['U.M.', 'Cantidad', 'Costo unit.', 'Valor S/'])
    return encabezados, filas, total


# ---------------------------------------------------------------- MB52 / MMBE: stock actual con comprometido
def comprometido():
    """{(producto_id, almacen_id): cantidad} reservada: requerimientos aprobados por entregar e insumos de las
    órdenes de producción confirmadas o en proceso (aún no consumidos)."""
    from inventario.models import RequerimientoItem
    from produccion.models import ConsumoOrden
    res = defaultdict(lambda: D0)
    for i in RequerimientoItem.objects.filter(requerimiento__estado__in=['APROBADO', 'PARCIAL']).select_related(
            'requerimiento'):
        if i.pendiente > 0:
            res[(i.producto_id, i.requerimiento.almacen_id)] += i.pendiente
    for c in ConsumoOrden.objects.filter(orden__estado__in=['CONFIRMADA', 'EN_PROCESO']).values(
            'producto_id', 'orden__almacen_insumos_id').annotate(s=Sum('cantidad_real')):
        res[(c['producto_id'], c['orden__almacen_insumos_id'])] += c['s'] or D0
    return res


def bloqueado():
    """{(producto_id, almacen_id): cantidad} no disponible: almacenes de cuarentena o por destruir, productos en un
    inventario físico con bloqueo y lotes vencidos."""
    from inventario.models import ConteoItem
    res = defaultdict(lambda: D0)
    for s in StockAlmacen.objects.filter(almacen__uso='DESTRUCCION').exclude(cantidad=0):
        res[(s.producto_id, s.almacen_id)] += s.cantidad
    en_conteo = set(ConteoItem.objects.filter(conteo__bloquear=True, conteo__estado__in=['ABIERTO', 'CONTADO',
                                                                                        'APROBADO'])
                    .values_list('producto_id', 'conteo__almacen_id'))
    for s in StockAlmacen.objects.exclude(cantidad=0).exclude(almacen__uso='DESTRUCCION'):
        if (s.producto_id, s.almacen_id) in en_conteo:
            res[(s.producto_id, s.almacen_id)] += s.cantidad
    for sl in StockLote.objects.filter(lote__vencimiento__lt=timezone.localdate(), cantidad__gt=0).exclude(
            almacen__uso='DESTRUCCION').select_related('lote'):
        clave = (sl.lote.producto_id, sl.almacen_id)
        if clave not in en_conteo:
            res[clave] += sl.cantidad
    return res


def stock_actual(almacenes=None, q='', familia='', por_lote=False, ubicacion=''):
    qs = StockAlmacen.objects.filter(producto__in=_productos(q, familia)).exclude(cantidad=0).select_related(
        'producto', 'almacen', 'ubicacion').order_by('producto__codigo', 'almacen__codigo')
    if almacenes:
        qs = qs.filter(almacen_id__in=almacenes)
    if ubicacion:
        qs = qs.filter(ubicacion__codigo__istartswith=ubicacion)
    comp, bloq = comprometido(), bloqueado()
    lotes = defaultdict(list)
    if por_lote:
        for sl in StockLote.objects.exclude(cantidad=0).select_related('lote'):
            lotes[(sl.lote.producto_id, sl.almacen_id)].append(sl)
    filas, total = [], D0
    for s in qs:
        p, clave = s.producto, (s.producto_id, s.almacen_id)
        c, b = comp.get(clave, D0), min(bloq.get(clave, D0), s.cantidad)
        valor = r2(s.cantidad * p.costo_promedio)
        total += valor
        base = [p.codigo, p.nombre, p.familia, s.almacen.codigo, s.ubicacion.codigo if s.ubicacion_id else '']
        if por_lote and lotes.get(clave):
            for sl in lotes[clave]:
                filas.append(base + [sl.lote.codigo, sl.lote.vencimiento, p.unidad, sl.cantidad, '', '', '',
                                     r2(sl.cantidad * p.costo_promedio)])
        else:
            filas.append(base + ([None, None] if por_lote else []) + [p.unidad, s.cantidad, c, b,
                                                                       s.cantidad - c - b, valor])
    encabezados = (['Código', 'Producto', 'Familia', 'Almacén', 'Ubicación'] + (['Lote', 'Vence'] if por_lote else [])
                   + ['U.M.', 'Stock', 'Comprometido', 'Bloqueado', 'Disponible', 'Valor S/'])
    return encabezados, filas, total


# ---------------------------------------------------------------- MB51: kardex masivo
ENCABEZADOS_KARDEX = ['Fecha', 'Código', 'Producto', 'Almacén', 'Lote', 'Tipo', 'Tabla 12', 'Origen', 'Referencia',
                      'Entrada', 'Salida', 'Costo unit.', 'Valor S/', 'Centro de costo']


def kardex_masivo(desde, hasta, almacenes=None, q='', familia=''):
    """Iterador de filas (un movimiento por fila) para la pantalla, el Excel o el CSV."""
    qs = (Kardex.objects.filter(fecha__range=(desde, hasta), producto__in=_productos(q, familia))
          .select_related('producto', 'almacen', 'lote', 'centro_costo').order_by('fecha', 'id'))
    if almacenes:
        qs = qs.filter(almacen_id__in=almacenes)
    for k in qs.iterator(chunk_size=2000):
        entrada = k.cantidad if k.tipo == 'ENTRADA' else None
        salida = k.cantidad if k.tipo != 'ENTRADA' else None
        yield [k.fecha, k.producto.codigo, k.producto.nombre, k.almacen.codigo if k.almacen_id else '',
               k.lote.codigo if k.lote_id else '', k.tipo.capitalize(), k.codigo_sunat, k.origen, k.referencia,
               entrada, salida, k.costo_unitario, r2(k.cantidad * (k.costo_unitario or D0)),
               k.centro_costo.codigo if k.centro_costo_id else '']


# ---------------------------------------------------------------- MB5B con valores / MC.9: valorización y cuadre
def valorizacion(fecha, almacenes=None, q='', familia=''):
    """Valorización a la fecha por producto y cuadre por cuenta de existencias contra el saldo contable."""
    encabezados, filas, total = stock_a_fecha(fecha, almacenes, q, familia)
    productos = {p.codigo: p for p in Producto.objects.filter(codigo__in={f[0] for f in filas}).select_related(
        'cuenta_existencias')}
    por_cuenta = defaultdict(lambda: D0)
    for f in filas:
        p = productos.get(f[0])
        cuenta = p.cuenta_existencias.codigo if p and p.cuenta_existencias_id else '(sin cuenta)'
        por_cuenta[cuenta] += f[-1]
    cuadre = []
    if not almacenes and not q and not familia:  # el cuadre solo tiene sentido con todo el inventario
        from contabilidad.models import AsientoLinea
        contable = dict(AsientoLinea.objects.filter(asiento__fecha__lte=fecha, cuenta__codigo__regex=r'^2[0-6]')
                        .values_list('cuenta__codigo').annotate(s=Sum('debe') - Sum('haber')))
        from contabilidad.models import CuentaContable
        nombres = dict(CuentaContable.objects.filter(codigo__in=set(por_cuenta) | set(contable)).values_list(
            'codigo', 'nombre'))
        for cuenta in sorted(set(por_cuenta) | {c for c, v in contable.items() if v}):
            kardex, conta = por_cuenta.get(cuenta, D0), contable.get(cuenta) or D0
            cuadre.append([cuenta, nombres.get(cuenta, ''), kardex, conta, kardex - conta])
    return encabezados, filas, total, cuadre


# ---------------------------------------------------------------- MC.9 / MC46: rotación y sin movimiento
def rotacion(almacenes=None, q='', familia='', dias=365, sin_movimiento=None, hoy=None):
    """Por producto: stock y valor actual, costo de lo que salió en el periodo (sin transferencias entre almacenes),
    rotación (veces), días de inventario y último movimiento. sin_movimiento=N: solo los que no se mueven hace N
    días."""
    hoy = hoy or timezone.localdate()
    desde = hoy - timedelta(days=dias)
    productos = _productos(q, familia)
    stocks = StockAlmacen.objects.filter(producto__in=productos)
    if almacenes:
        stocks = stocks.filter(almacen_id__in=almacenes)
    stock = dict(stocks.values_list('producto_id').annotate(s=Sum('cantidad')))
    k = Kardex.objects.filter(producto__in=productos)
    if almacenes:
        k = k.filter(almacen_id__in=almacenes)
    salidas = dict(k.filter(tipo='SALIDA', fecha__gt=desde).exclude(codigo_sunat='11').values_list('producto_id')
                   .annotate(v=Sum(F('cantidad') * F('costo_unitario'), output_field=_DEC)))
    ultimo = dict(k.values_list('producto_id').annotate(f=Max('fecha')))
    filas = []
    for p in productos.filter(pk__in=set(stock) | set(salidas)).order_by('codigo'):
        cant = stock.get(p.pk) or D0
        valor = r2(cant * p.costo_promedio)
        consumo = r2(salidas.get(p.pk) or D0)
        rot = (consumo / valor).quantize(Decimal('0.01')) if valor > 0 else None
        dias_inv = int(dias / rot) if rot else None
        ult = ultimo.get(p.pk)
        quietos = (hoy - ult).days if ult else None
        if sin_movimiento and (quietos is None or quietos < sin_movimiento or cant == 0):
            continue
        if not sin_movimiento and cant == 0 and not consumo:
            continue
        filas.append([p.codigo, p.nombre, p.familia, p.unidad, cant, valor, consumo, rot, dias_inv, ult, quietos])
    encabezados = ['Código', 'Producto', 'Familia', 'U.M.', 'Stock', 'Valor S/', f'Salidas {dias} días S/',
                   'Rotación (veces)', 'Días de inventario', 'Último movimiento', 'Días sin moverse']
    total = sum((f[5] for f in filas), D0)
    return encabezados, filas, total
