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


def cargar_desde_compras():
    """Arma el registro info con todas las compras ya registradas (las de antes del registro info o migradas): por
    producto y proveedor, número de compras, último precio y fecha. Se puede repetir: recalcula, no suma dos veces.
    Si el producto no tiene preferido, queda el proveedor al que más se le compró. (creados, actualizados)"""
    from collections import defaultdict

    from .models import CompraItem
    datos = defaultdict(lambda: {'n': 0, 'fecha': None, 'precio': None, 'moneda': 'PEN'})
    for producto_id, proveedor_id, fecha, precio, moneda in (
            CompraItem.objects.filter(producto__isnull=False, documento__estado='REGISTRADO')
            .exclude(documento__tipo_comprobante__in=['07', '08'])
            .order_by('documento__fecha_emision', 'documento_id', 'id')
            .values_list('producto_id', 'documento__tercero_id', 'documento__fecha_emision', 'precio_unitario',
                         'documento__moneda')):
        d = datos[(producto_id, proveedor_id)]
        d['n'] += 1
        d['fecha'], d['precio'], d['moneda'] = fecha, precio, moneda  # en orden de fecha: queda el último
    existentes = {(r.producto_id, r.proveedor_id): r for r in RegistroInfo.objects.all()}
    creados = actualizados = 0
    for (producto_id, proveedor_id), d in datos.items():
        r = existentes.get((producto_id, proveedor_id))
        if r is None:
            RegistroInfo.objects.create(producto_id=producto_id, proveedor_id=proveedor_id, moneda=d['moneda'],
                                        ultimo_precio=d['precio'], ultima_compra=d['fecha'], compras=d['n'])
            creados += 1
        else:
            RegistroInfo.objects.filter(pk=r.pk).update(ultimo_precio=d['precio'], ultima_compra=d['fecha'],
                                                        compras=d['n'], moneda=d['moneda'])
            actualizados += 1
    sin_preferido = set(RegistroInfo.objects.values_list('producto_id', flat=True)) - set(
        RegistroInfo.objects.filter(preferido=True).values_list('producto_id', flat=True))
    for producto_id in sin_preferido:
        mejor = RegistroInfo.objects.filter(producto_id=producto_id, activo=True).order_by('-compras', 'pk').first()
        if mejor:
            RegistroInfo.objects.filter(pk=mejor.pk).update(preferido=True)
    return creados, actualizados


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
