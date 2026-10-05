"""Diferencia de precio entre la recepción (costo de la orden de compra) y la factura del proveedor.

La recepción ingresa al almacén con el costo de la orden: existencias contra la cuenta 28 (por recibir). La factura
carga la 28 con su precio real. Si difieren, la 28 queda con saldo. Al conciliar (cantidades recibidas y
facturadas), la diferencia se liquida como en SAP: lo que sigue en stock revaloriza el costo promedio y lo ya
consumido o vendido va al costo de ventas. Así la 28 queda en cero."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from core.models import Kardex, Producto, r2

from .models import AjustePrecioCompra, Compra

D0 = Decimal('0')


def _recibido(compra):
    """{producto_id: (cantidad, valor S/)} recibido por Inventario para esta factura. Si la orden de compra tiene
    varias facturas, las recepciones de la orden se asignan en orden de registro (FIFO) a cada factura."""
    from inventario.models import Operacion
    ops = Operacion.objects.filter(estado='CONFIRMADO', tipo__clase='INGRESO')
    directas = defaultdict(lambda: [D0, D0])
    for op in ops.filter(compra=compra).prefetch_related('items'):
        for i in op.items.all():
            directas[i.producto_id][0] += i.cantidad
            directas[i.producto_id][1] += i.cantidad * (i.costo_unitario or D0)
    if not compra.orden_compra_id:
        return {k: tuple(v) for k, v in directas.items()}
    # recepciones de la orden (sin factura indicada) repartidas entre sus facturas por orden de registro
    lotes = defaultdict(list)  # producto -> [(cantidad, costo unitario)]
    for op in ops.filter(orden_compra_id=compra.orden_compra_id, compra__isnull=True).order_by(
            'fecha', 'id').prefetch_related('items'):
        for i in op.items.all():
            lotes[i.producto_id].append([i.cantidad, i.costo_unitario or D0])
    facturas = (Compra.objects.filter(orden_compra_id=compra.orden_compra_id, estado='REGISTRADO')
                .exclude(tipo_comprobante__in=['07', '08']).order_by('fecha_emision', 'id').prefetch_related('items'))
    resultado = {}
    for f in facturas:
        pedido = defaultdict(lambda: D0)
        for i in f.items.select_related('producto'):
            if i.producto_id:
                pedido[i.producto_id] += i.producto.a_stock(i.cantidad)  # en unidad de almacén
        for pid, falta in pedido.items():
            ya = directas[pid][0] if f.pk == compra.pk else D0
            falta -= ya
            cant = valor = D0
            for lote in lotes[pid]:
                if falta <= 0:
                    break
                toma = min(lote[0], falta)
                cant += toma
                valor += toma * lote[1]
                lote[0] -= toma
                falta -= toma
            if f.pk == compra.pk:
                resultado[pid] = (cant + directas[pid][0], valor + directas[pid][1])
        if f.pk == compra.pk:
            break
    return resultado


def liquidar(compra):
    """Registra (o corrige) la diferencia de precio de la factura. Idempotente: compara con lo ya liquidado."""
    if compra.tipo_comprobante in ('07', '08') or compra.es_saldo_inicial or compra.stock_aplicado:
        return []  # notas, saldos iniciales y facturas que ingresan solas al almacén (ya con su precio)
    esperado = {}
    if compra.estado == 'REGISTRADO':
        facturado = defaultdict(lambda: [D0, D0])
        for i in compra.items.filter(producto__isnull=False).select_related('producto'):
            facturado[i.producto_id][0] += i.producto.a_stock(i.cantidad)  # en unidad de almacén, como lo recibido
            facturado[i.producto_id][1] += r2(i.subtotal * compra.tc_efectivo)
        for pid, (cant_rec, valor_rec) in _recibido(compra).items():
            cant_fac, valor_fac = facturado.get(pid, (D0, D0))
            conciliada = min(cant_rec, cant_fac)
            if conciliada <= 0:
                continue
            diferencia = r2(valor_fac / cant_fac * conciliada - valor_rec / cant_rec * conciliada)
            esperado[pid] = (conciliada, diferencia)
    hechos = defaultdict(lambda: D0)
    for a in compra.ajustes_precio.all():
        hechos[a.producto_id] += a.diferencia
    nuevos = []
    with transaction.atomic():
        for pid in set(esperado) | set(hechos):
            conciliada, diferencia = esperado.get(pid, (D0, D0))
            pendiente = diferencia - hechos[pid]
            if abs(pendiente) < Decimal('0.01'):
                continue
            nuevos.append(_aplicar(compra, Producto.objects.get(pk=pid), conciliada, pendiente))
    return nuevos


def _aplicar(compra, producto, cantidad, diferencia):
    """Reparte la diferencia: la parte de lo que sigue en stock revaloriza el costo promedio (queda en el kardex);
    el resto va al costo de ventas."""
    from django.utils import timezone

    from inventario.cierre import error_cierre
    # el ajuste queda como último movimiento del producto (la valorización usa el costo del último registro)
    ultimo = Kardex.objects.filter(producto=producto).order_by('-fecha').values_list('fecha', flat=True).first()
    fecha = min(max(compra.fecha_emision, ultimo or compra.fecha_emision), timezone.localdate())
    if compra.estado == 'ANULADO' or error_cierre(fecha):
        fecha = timezone.localdate()
    actual = Producto.objects.select_for_update().get(pk=producto.pk)
    en_stock = min(actual.stock, cantidad) if cantidad else (actual.stock if actual.stock > 0 else D0)
    base = cantidad or en_stock or Decimal('1')
    a_inventario = r2(diferencia * en_stock / base) if actual.stock > 0 else D0
    a_costo = diferencia - a_inventario
    if a_inventario and actual.stock > 0:
        nuevo = ((actual.stock * actual.costo_promedio + a_inventario) / actual.stock).quantize(Decimal('0.0001'))
        actual.costo_promedio = max(nuevo, D0)
        actual.save(update_fields=['costo_promedio'])
        Kardex.objects.create(  # ajuste de valor sin cantidad: deja el nuevo costo promedio en el kardex
            producto=actual, almacen=None, fecha=fecha, tipo='ENTRADA', cantidad=D0, costo_unitario=D0,
            costo_promedio=actual.costo_promedio, saldo=actual.stock, origen='AJUSTE', concepto='PRECIO',
            referencia=f'Diferencia de precio {compra}', codigo_sunat='99')
    return AjustePrecioCompra.objects.create(compra=compra, producto=producto, fecha=fecha, cantidad=cantidad,
                                             diferencia=diferencia, a_inventario=a_inventario, a_costo=a_costo)
