"""Control de la obra por insumo: requerir a Compras lo que falta según el presupuesto Meta (solicitud de pedido con el
centro de costo de la obra) y comparar la Meta con lo pedido, lo recibido en el almacén de obra y lo consumido con el
centro de costo de la obra. Los materiales y subcontratos se enlazan a productos con obras.vinculos."""
from collections import OrderedDict, defaultdict
from decimal import Decimal

from django.db.models import Sum

from . import calculos

D0 = Decimal('0')
ESTADOS_SIN_EFECTO = ('ANULADA', 'RECHAZADA')


def meta(presupuesto):
    """(filas por producto, recursos sin producto) de la relación de insumos: materiales y subcontratos."""
    e = calculos.explosion(presupuesto)
    filas, sin_producto = OrderedDict(), []
    for t in e['filas']:
        r = t['recurso']
        if r.tipo not in ('MAT', 'SUB') or r.es_porcentaje_mo:
            continue
        if not r.producto_id:
            sin_producto.append(t)
            continue
        f = filas.setdefault(r.producto_id, {'producto': r.producto, 'recursos': [], 'tipo': r.tipo, 'unidad': r.unidad,
                                             'cantidad': D0, 'monto': D0, 'precio': t['precio']})
        f['recursos'].append(r)
        f['cantidad'] += t['cantidad']
        f['monto'] += t['monto']
    return filas, sin_producto


def pedido(obra):
    """{producto: cantidad} pedida en solicitudes de pedido con el centro de costo de la obra (vigentes)."""
    from compras.models import LineaSolicitud
    return dict(LineaSolicitud.objects.filter(solicitud__centro_costo=obra.centro_costo, producto__isnull=False)
                .exclude(solicitud__estado__in=ESTADOS_SIN_EFECTO).values('producto')
                .annotate(c=Sum('cantidad')).values_list('producto', 'c'))


def almacen(obra):
    """({producto: recibido en el almacén de obra}, {producto: (consumido con el centro de la obra, valor)})."""
    from core.models import Kardex
    recibido = dict(Kardex.objects.filter(almacen=obra.almacen, tipo='ENTRADA').values('producto')
                    .annotate(c=Sum('cantidad')).values_list('producto', 'c'))
    consumido = defaultdict(lambda: [D0, D0])
    for pid, cantidad, costo in Kardex.objects.filter(centro_costo=obra.centro_costo, tipo='SALIDA').values_list(
            'producto', 'cantidad', 'costo_unitario'):
        consumido[pid][0] += cantidad
        consumido[pid][1] += cantidad * costo
    return recibido, {k: (c, v.quantize(Decimal('0.01'))) for k, (c, v) in consumido.items()}


def control(presupuesto):
    """Filas de meta vs. real por insumo y totales."""
    obra = presupuesto.obra
    filas, sin_producto = meta(presupuesto)
    pedidos = pedido(obra)
    recibido, consumido = almacen(obra)
    for pid, f in filas.items():
        f['pedido'] = pedidos.get(pid, D0)
        f['recibido'] = recibido.get(pid, D0)
        f['consumido'], f['costo_real'] = consumido.get(pid, (D0, D0))
        f['por_pedir'] = max(f['cantidad'] - f['pedido'], D0)
        f['avance'] = (f['consumido'] / f['cantidad'] * 100).quantize(Decimal('0.1')) if f['cantidad'] else D0
        f['excede'] = f['pedido'] > f['cantidad']
        # lo que costaría consumir la Meta al costo real promedio, frente a su monto en el presupuesto
        f['desviacion'] = (f['costo_real'] - (f['consumido'] * f['precio'])).quantize(Decimal('0.01'))
    totales = {c: sum((f[c] for f in filas.values()), D0) for c in ('monto', 'costo_real', 'desviacion')}
    return {'filas': list(filas.values()), 'sin_producto': sin_producto, 'totales': totales,
            'costo_contable': costo_contable(obra)}


def costo_contable(obra):
    """Costo real de la obra en contabilidad: gastos por naturaleza (clase 6) con su centro de costo."""
    from contabilidad.models import AsientoLinea
    agg = AsientoLinea.objects.filter(centro_costo=obra.centro_costo, es_destino=False,
                                      cuenta__codigo__startswith='6').aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or D0) - (agg['h'] or D0)


class ErrorRequerimiento(Exception):
    pass


def requerir(presupuesto, cantidades, usuario, fecha_requerida=None, justificacion=''):
    """Solicitudes de pedido (una de materiales y otra de servicios) con lo indicado {producto_id: cantidad}. Pedir más
    de lo que falta según la Meta exige una justificación."""
    from compras import solped
    obra = presupuesto.obra
    filas, _ = meta(presupuesto)
    pedidos = pedido(obra)
    lineas = {'MATERIAL': [], 'SERVICIO': []}
    excesos = []
    for pid, cantidad in cantidades.items():
        f = filas.get(pid)
        if f is None or not cantidad or cantidad <= 0:
            continue
        falta = max(f['cantidad'] - pedidos.get(pid, D0), D0)
        if cantidad > falta:
            excesos.append(f'{f["producto"].nombre}: pide {cantidad:,.2f} y la Meta deja {falta:,.2f}')
        lineas['SERVICIO' if f['tipo'] == 'SUB' else 'MATERIAL'].append({
            'producto': f['producto'], 'cantidad': cantidad.quantize(Decimal('0.01')), 'precio_estimado': f['precio']})
    if not any(lineas.values()):
        raise ErrorRequerimiento('Indique la cantidad a pedir en al menos un insumo.')
    if excesos and not justificacion.strip():
        raise ErrorRequerimiento('Lo pedido supera la Meta (' + '; '.join(excesos[:5]) + '). Escriba la justificación '
                                 'para pedir de más.')
    motivo = f'Requerimiento de obra {obra.codigo} {obra.nombre} ({presupuesto})'
    if excesos:
        motivo += f' · sobre la Meta: {justificacion.strip()}'
    creadas = []
    for tipo, items in lineas.items():
        if items:
            creadas.append(solped.crear({'centro_costo': obra.centro_costo, 'tipo': tipo, 'motivo': motivo[:250],
                                         'fecha_requerida': fecha_requerida}, items, usuario, origen='OBRA'))
    return creadas
