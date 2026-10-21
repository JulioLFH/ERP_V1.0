"""Historial de precios de compra por producto y proveedor (n.º 44) y alerta de precio en la orden de compra: si una
línea supera el último precio pagado en más del % de Ajustes › Empresa, la OC lo muestra antes de aprobarla."""
from collections import defaultdict
from decimal import Decimal

from core.models import r2

D0 = Decimal('0')


def historial(producto, proveedor=None, desde=None, hasta=None):
    """[{fecha, proveedor, documento, compra_id, cantidad, precio, moneda, precio_pen, variacion}] en orden de fecha.
    precio = valor unitario sin IGV de la factura; variacion = % frente a la compra anterior del mismo proveedor."""
    from .models import CompraItem
    qs = (CompraItem.objects.filter(producto=producto, documento__estado='REGISTRADO')
          .exclude(documento__tipo_comprobante__in=['07', '08'])
          .select_related('documento__tercero').order_by('documento__fecha_emision', 'documento_id', 'id'))
    if proveedor is not None:
        qs = qs.filter(documento__tercero=proveedor)
    if desde:
        qs = qs.filter(documento__fecha_emision__gte=desde)
    if hasta:
        qs = qs.filter(documento__fecha_emision__lte=hasta)
    filas, anterior = [], {}
    for i in qs:
        c = i.documento
        tc = c.tipo_cambio if c.moneda == 'USD' else Decimal('1')
        precio_pen = r2(i.precio_unitario * tc) if tc != 1 else i.precio_unitario
        previo = anterior.get(c.tercero_id)
        variacion = (r2((precio_pen - previo) / previo * 100) if previo else None)
        anterior[c.tercero_id] = precio_pen
        filas.append({'fecha': c.fecha_emision, 'proveedor': c.tercero, 'documento': c.numero_completo,
                      'compra_id': c.pk, 'cantidad': i.cantidad, 'precio': i.precio_unitario, 'moneda': c.moneda,
                      'precio_pen': precio_pen, 'variacion': variacion})
    return filas


def resumen(filas):
    """Por proveedor: compras, primer y último precio (S/), mínimo, máximo y promedio ponderado."""
    grupos = defaultdict(list)
    for f in filas:
        grupos[f['proveedor']].append(f)
    salida = []
    for prov, fs in grupos.items():
        cantidad = sum((f['cantidad'] for f in fs), D0)
        ponderado = r2(sum((f['precio_pen'] * f['cantidad'] for f in fs), D0) / cantidad) if cantidad else None
        salida.append({'proveedor': prov, 'compras': len(fs), 'primero': fs[0]['precio_pen'],
                       'ultimo': fs[-1]['precio_pen'], 'minimo': min(f['precio_pen'] for f in fs),
                       'maximo': max(f['precio_pen'] for f in fs), 'promedio': ponderado,
                       'ultima_fecha': fs[-1]['fecha'],
                       'cambio': r2((fs[-1]['precio_pen'] - fs[0]['precio_pen']) / fs[0]['precio_pen'] * 100)
                       if fs[0]['precio_pen'] else None})
    return sorted(salida, key=lambda r: r['ultima_fecha'], reverse=True)


def grafico(filas, ancho=760, alto=220):
    """Puntos SVG por proveedor (precio en S/ en el tiempo). {'series': [{nombre, puntos}], 'ejes': {...}}."""
    if not filas:
        return None
    fechas = [f['fecha'].toordinal() for f in filas]
    precios = [float(f['precio_pen']) for f in filas]
    f0, f1 = min(fechas), max(fechas)
    p0, p1 = min(precios), max(precios)
    margen = (p1 - p0) * 0.1 or (p1 * 0.1 or 1)
    p0, p1 = max(p0 - margen, 0), p1 + margen
    izq, der, arriba, abajo = 56, 12, 12, 28

    def x(o):
        return izq + (o - f0) / ((f1 - f0) or 1) * (ancho - izq - der)

    def y(p):
        return arriba + (1 - (p - p0) / ((p1 - p0) or 1)) * (alto - arriba - abajo)

    grupos = defaultdict(list)
    for f in filas:
        grupos[f['proveedor'].nombre].append(f)
    series = [{'nombre': nombre, 'puntos': [(round(x(f['fecha'].toordinal()), 1), round(y(float(f['precio_pen'])), 1),
                                             f['fecha'], f['precio_pen'], f['documento']) for f in fs]}
              for nombre, fs in grupos.items()]
    for s in series:
        s['linea'] = ' '.join(f'{px},{py}' for px, py, *_ in s['puntos'])
    marcas = [round(p0 + (p1 - p0) * k / 4, 2) for k in range(5)]
    return {'series': series, 'ancho': ancho, 'alto': alto, 'izq': izq, 'base': alto - abajo,
            'marcas': [{'valor': m, 'y': round(y(m), 1)} for m in marcas],
            'desde': filas[0]['fecha'], 'hasta': filas[-1]['fecha']}


def ultimo_precio(producto, proveedor=None):
    """(precio en S/, fecha, proveedor) de la última compra registrada del producto (del proveedor si se indica)."""
    filas = historial(producto, proveedor)
    if not filas:
        return None
    f = filas[-1]
    return f['precio_pen'], f['fecha'], f['proveedor']


def alertas_orden(oc):
    """Líneas de la OC cuyo precio supera el último pagado (al mismo proveedor o, si nunca se le compró, a cualquiera)
    en más del % de la empresa. [texto]"""
    from core.models import Empresa
    pct = Empresa.actual().alerta_precio_pct
    if not pct:
        return []
    tc = oc.tipo_cambio if oc.moneda == 'USD' else Decimal('1')
    alertas = []
    for i in oc.items.filter(producto__isnull=False).select_related('producto'):
        ref = ultimo_precio(i.producto, oc.tercero) or ultimo_precio(i.producto)
        if ref is None or not ref[0]:
            continue
        precio, fecha, prov = ref
        actual = r2(i.precio_unitario * (tc or 1))
        alza = r2((actual - precio) / precio * 100)
        if alza > pct:
            alertas.append(f'{i.producto.codigo} {i.producto.nombre}: S/ {actual:,.4f} es {alza:,.2f}% más que el '
                           f'último precio pagado (S/ {precio:,.4f} a {prov.nombre} el {fecha:%d/%m/%Y}).')
    return alertas
