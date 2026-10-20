"""Registro info (proveedores por producto): fuente de aprovisionamiento para el MRP, la sugerencia de compra y las
solicitudes de pedido, y aprendizaje automático del último precio con cada compra registrada."""
from django.db.models import F

from .models import RegistroInfo


def fuente(producto):
    """El registro info a usar: el preferido; si no hay, el más comprado. None si el producto no tiene."""
    if producto is None or not producto.pk:
        return None
    return (RegistroInfo.objects.filter(producto=producto, activo=True).select_related('proveedor')
            .order_by('-preferido', '-compras', 'pk').first())


def proveedor_y_plazo(producto):
    """(proveedor, días de entrega, lote mínimo): del registro info y, si no hay, de la ficha del producto."""
    r = fuente(producto)
    if r is not None:
        return r.proveedor, r.plazo_entrega or producto.tiempo_entrega or 0, r.cantidad_minima or producto.lote_compra
    return producto.proveedor, producto.tiempo_entrega or 0, producto.lote_compra


def precio_de(producto, proveedor):
    r = RegistroInfo.objects.filter(producto=producto, proveedor=proveedor, activo=True).first()
    return r.precio_vigente if r else None


def aprender(compra):
    """Cada producto comprado queda en el registro info del proveedor con su último precio y fecha."""
    if compra.tipo_comprobante in ('07', '08') or compra.estado == 'ANULADO':
        return
    for i in compra.items.filter(producto__isnull=False).select_related('producto'):
        r, creado = RegistroInfo.objects.get_or_create(
            producto=i.producto, proveedor=compra.tercero,
            defaults={'moneda': compra.moneda, 'plazo_entrega': i.producto.tiempo_entrega or 0,
                      'preferido': not RegistroInfo.objects.filter(producto=i.producto).exists()})
        if r.ultima_compra and r.ultima_compra > compra.fecha_emision:
            RegistroInfo.objects.filter(pk=r.pk).update(compras=F('compras') + 1)
            continue
        RegistroInfo.objects.filter(pk=r.pk).update(ultimo_precio=i.precio_unitario, ultima_compra=compra.fecha_emision,
                                                    moneda=compra.moneda, compras=F('compras') + 1)
