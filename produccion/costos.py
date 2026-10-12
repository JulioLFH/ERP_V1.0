"""Rentabilidad: ventas, costo de ventas y margen por producto, cliente, vendedor o mes."""
from collections import OrderedDict, defaultdict
from decimal import Decimal

from core.models import Kardex, r2
from ventas.models import Venta

D0 = Decimal('0')
AGRUPACIONES = [('producto', 'Producto'), ('cliente', 'Cliente'), ('vendedor', 'Vendedor'), ('mes', 'Mes')]


def _costos_por_documento(ventas):
    """{(referencia, producto_id): costo unitario promedio} de las salidas (y entradas por NC) del kardex."""
    refs = {str(v): v for v in ventas}
    acumulado = defaultdict(lambda: [D0, D0])
    for k in Kardex.objects.filter(origen='VENTA', referencia__in=list(refs)).only(
            'referencia', 'producto_id', 'cantidad', 'costo_unitario'):
        a = acumulado[(k.referencia, k.producto_id)]
        a[0] += k.cantidad
        a[1] += k.cantidad * k.costo_unitario
    return {clave: (v / c) if c else D0 for clave, (c, v) in acumulado.items()}


def _clave_anterior(v):
    """Número del comprobante como lo guarda el kardex del sistema anterior: FF01-00011785."""
    numero = str(v.numero or '').strip()
    return f'{v.serie}-{int(numero):08d}' if numero.isdigit() else f'{v.serie}-{numero}'


def _costos_anteriores(ventas):
    """{(comprobante, producto_id): costo unitario} de las ventas del sistema anterior, según su kardex (el costo que
    exporta es el total de la línea)."""
    from historial.models import MovimientoAnterior
    claves = list({_clave_anterior(v) for v in ventas})
    acumulado = defaultdict(lambda: [D0, D0])
    for i in range(0, len(claves), 5000):
        for m in (MovimientoAnterior.objects.filter(comprobante__in=claves[i:i + 5000], producto__isnull=False,
                                                    transaccion__in=['Salida por Ventas', 'Devolucion de Clientes'])
                  .values('comprobante', 'producto_id', 'salida', 'ingreso', 'costo')):
            a = acumulado[(m['comprobante'], m['producto_id'])]
            a[0] += (m['salida'] or D0) + (m['ingreso'] or D0)
            a[1] += m['costo'] or D0
    return {clave: (v / c) if c else D0 for clave, (c, v) in acumulado.items()}


def rentabilidad(desde, hasta, agrupar='producto'):
    """Filas ordenadas por margen con ventas netas, costo de ventas, margen y % (importes en soles, sin IGV).
    Las ventas del sistema anterior toman su costo del kardex de ese sistema (por comprobante y producto)."""
    from inventario.servicios import costo_de_venta
    ventas = list(Venta.objects.filter(estado='REGISTRADO', es_saldo_inicial=False,
                                       fecha_emision__range=[desde, hasta])
                  .exclude(tipo_comprobante='08').select_related('tercero', 'doc_referencia')
                  .prefetch_related('items__producto'))
    costos = _costos_por_documento([v for v in ventas if not v.es_historico])
    anteriores = _costos_anteriores([v for v in ventas if v.es_historico] +
                                    [v.doc_referencia for v in ventas if v.es_historico and v.doc_referencia_id])
    sin_costo = 0
    grupos = OrderedDict()
    for v in ventas:
        signo = -1 if v.tipo_comprobante == '07' else 1
        tc = v.tc_efectivo
        for i in v.items.all():
            if agrupar == 'producto':
                clave, nombre = (i.producto_id or f'x{i.descripcion}'), (i.producto.nombre if i.producto else i.descripcion)
            elif agrupar == 'cliente':
                clave, nombre = v.tercero_id, v.tercero.nombre
            elif agrupar == 'vendedor':
                clave = nombre = v.vendedor or '(sin vendedor)'
            else:
                clave = nombre = v.fecha_emision.strftime('%Y-%m')
            g = grupos.setdefault(clave, {'nombre': nombre, 'cantidad': D0, 'ventas': D0, 'costo': D0})
            ingreso = r2(i.subtotal * tc) * signo
            costo = D0
            if i.producto and i.producto.es_inventariable and v.es_historico:
                unit = anteriores.get((_clave_anterior(v), i.producto_id))
                if unit is None and v.tipo_comprobante == '07' and v.doc_referencia_id:  # NC: costo de la factura
                    unit = anteriores.get((_clave_anterior(v.doc_referencia), i.producto_id))
                if unit is None:
                    sin_costo += 1
                    unit = D0
                costo = r2(i.cantidad * unit) * signo
            elif i.producto and i.producto.es_inventariable:
                unit = costos.get((str(v), i.producto_id))
                if unit is None:  # despachada por operación de inventario o NC sin movimiento: costo de la venta
                    origen = v.doc_referencia if v.tipo_comprobante == '07' and v.doc_referencia_id else v
                    unit = costo_de_venta(origen, i.producto_id)
                costo = r2(i.cantidad * unit) * signo
            g['cantidad'] += i.cantidad * signo
            g['ventas'] += ingreso
            g['costo'] += costo
    filas = []
    for g in grupos.values():
        g['margen'] = g['ventas'] - g['costo']
        g['pct'] = (g['margen'] / g['ventas'] * 100).quantize(Decimal('0.1')) if g['ventas'] else None
        filas.append(g)
    filas.sort(key=lambda f: f['margen'], reverse=True)
    total = {k: sum((f[k] for f in filas), D0) for k in ('ventas', 'costo', 'margen')}
    total['pct'] = (total['margen'] / total['ventas'] * 100).quantize(Decimal('0.1')) if total['ventas'] else None
    total['sin_costo'] = sin_costo  # líneas del sistema anterior sin su salida en el kardex de ese sistema
    return filas, total
