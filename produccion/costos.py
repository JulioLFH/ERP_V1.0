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


def rentabilidad(desde, hasta, agrupar='producto'):
    """Filas ordenadas por margen con ventas netas, costo de ventas, margen y % (importes en soles, sin IGV)."""
    from inventario.servicios import costo_de_venta
    ventas = list(Venta.objects.filter(estado='REGISTRADO', es_saldo_inicial=False,
                                       fecha_emision__range=[desde, hasta])
                  .exclude(tipo_comprobante='08').select_related('tercero', 'doc_referencia')
                  .prefetch_related('items__producto'))
    costos = _costos_por_documento(ventas)
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
            if i.producto and i.producto.es_inventariable:
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
    return filas, total
