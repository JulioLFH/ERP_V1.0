"""Cruce de tres vías (ALM-06): orden de compra, recepción y factura del proveedor.

- Cantidad: lo facturado de la orden (esta factura más las anteriores vigentes) no pasa de lo recibido en el almacén
  (recepciones de Inventario o facturas que ingresaron la mercadería), con la tolerancia de cantidad; si la propia
  factura ingresa la mercadería, no pasa de lo pedido. Fuera de eso no se registra.
- Precio: cada precio facturado frente al de la orden, con la tolerancia de precio del proveedor o de la empresa. Fuera
  de ella la factura se registra «retenida para pago» y Compras la libera con su motivo.
Las cantidades se comparan en la unidad de almacén."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from .tolerancias import permitida

D0 = Decimal('0')


def _ops(filtro, clases):
    from inventario.models import OperacionItem
    return {r['producto_id']: r['c'] for r in OperacionItem.objects.filter(
        operacion__estado='CONFIRMADO', operacion__tipo__clase__in=clases, **filtro).values('producto_id').annotate(
        c=Sum('cantidad'))}


def _sumar(destino, origen, signo=1):
    for k, v in origen.items():
        destino[k] += v * signo


def _por_producto(items):
    total = defaultdict(lambda: D0)
    for i in items:
        if i.producto_id and i.producto.es_inventariable:
            total[i.producto_id] += i.producto.a_stock(i.cantidad)
    return total


def recibido_oc(oc, excluir_compra=None):
    """{producto: cantidad recibida} de la orden: recepciones contra la orden o sus facturas y facturas que ingresaron
    al almacén, menos las devoluciones al proveedor."""
    recibido = defaultdict(lambda: D0)
    _sumar(recibido, _ops({'operacion__orden_compra': oc}, ['INGRESO']))
    _sumar(recibido, _ops({'operacion__compra__orden_compra': oc}, ['INGRESO']))
    _sumar(recibido, _ops({'operacion__compra__orden_compra': oc}, ['SALIDA']), -1)
    facturas = oc.compras.filter(estado='REGISTRADO', stock_aplicado=True)
    if excluir_compra is not None and excluir_compra.pk:
        facturas = facturas.exclude(pk=excluir_compra.pk)
    for c in facturas:
        _sumar(recibido, _por_producto(c.items.select_related('producto')), -1 if c.tipo_comprobante == '07' else 1)
    return recibido


def facturado_oc(oc, excluir_compra=None):
    """{producto: cantidad facturada} de la orden (las notas de crédito con productos restan)."""
    facturado = defaultdict(lambda: D0)
    qs = oc.compras.filter(estado='REGISTRADO').exclude(tipo_comprobante='08')
    if excluir_compra is not None and excluir_compra.pk:
        qs = qs.exclude(pk=excluir_compra.pk)
    for c in qs:
        _sumar(facturado, _por_producto(c.items.select_related('producto')), -1 if c.tipo_comprobante == '07' else 1)
    from .models import Compra
    notas = Compra.objects.filter(doc_referencia__orden_compra=oc, estado='REGISTRADO', tipo_comprobante='07').exclude(
        orden_compra=oc)
    for n in notas:
        _sumar(facturado, _por_producto(n.items.select_related('producto')), -1)
    return facturado


def errores_cantidad(oc, lineas, ingresa, compra=None):
    """lineas: [(producto, cantidad en unidad de compra)] de la factura. Errores si se factura más de lo recibido (o
    de lo pedido si la factura ingresa la mercadería)."""
    esta = defaultdict(lambda: D0)
    for p, cantidad in lineas:
        if p is not None and p.es_inventariable:
            esta[p.pk] += p.a_stock(Decimal(cantidad or 0))
    if not esta:
        return []
    from core.models import Producto
    pedido = _por_producto(oc.items.select_related('producto'))
    antes = facturado_oc(oc, excluir_compra=compra)
    recibido = recibido_oc(oc, excluir_compra=compra)
    nombres = dict(Producto.objects.filter(pk__in=esta).values_list('pk', 'nombre'))
    errores = []
    for pid, cantidad in esta.items():
        if pid not in pedido:
            errores.append(f'{nombres[pid]} no está en la orden {oc.numero}.')
            continue
        tope = pedido[pid] if ingresa else recibido.get(pid, D0)
        tolerancia = permitida(oc.tercero, 'cantidad', tope)
        if antes.get(pid, D0) + cantidad > tope + tolerancia:
            base = 'pedido' if ingresa else 'recibido en el almacén'
            errores.append(f'{nombres[pid]}: se facturaría {antes.get(pid, D0) + cantidad:,.2f} y lo {base} es '
                           f'{tope:,.2f} (ya facturado {antes.get(pid, D0):,.2f}). Registre la recepción primero o '
                           f'factúrelo después.')
    return errores


def fuera_de_precio(compra):
    """Líneas cuyo precio difiere del de la orden más que la tolerancia: [texto]."""
    oc = compra.orden_compra
    if oc is None or compra.tipo_comprobante in ('07', '08') or oc.moneda != compra.moneda:
        return []
    precios = {}
    for i in oc.items.all():
        if i.producto_id:
            precios.setdefault(i.producto_id, i.precio_neto)
    salida = []
    for i in compra.items.select_related('producto'):
        if not i.producto_id or i.producto_id not in precios:
            continue
        ref = precios[i.producto_id]
        if abs(i.precio_neto - ref) > permitida(oc.tercero, 'precio', ref):
            salida.append(f'{i.producto.nombre}: {i.precio_neto:,.4f} frente a {ref:,.4f} de la orden')
    return salida


def diferencias_cantidad(compra):
    """[texto] de los productos de la orden de la factura con más facturado que recibido (p. ej. tras anular una
    recepción), con la tolerancia de cantidad."""
    oc = compra.orden_compra
    if oc is None or compra.tipo_comprobante in ('07', '08'):
        return []
    from core.models import Producto
    recibido, facturado = recibido_oc(oc), facturado_oc(oc)
    exceso = {k: v for k, v in facturado.items()
              if v > recibido.get(k, D0) + permitida(oc.tercero, 'cantidad', recibido.get(k, D0))}
    nombres = dict(Producto.objects.filter(pk__in=exceso).values_list('pk', 'nombre'))
    return [f'{nombres[k]}: facturado {v:,.2f} y recibido {recibido.get(k, D0):,.2f}' for k, v in exceso.items()]


def motivos_retencion(compra):
    """ALM-14: todas las condiciones que impiden pagar la factura, cada una por separado: [(tipo, texto)]. La de
    precio la puede liberar Compras con su motivo; la de cantidad no (nota de crédito o reingreso)."""
    motivos = [('PRECIO', f'Precio fuera de la tolerancia: {t}') for t in fuera_de_precio(compra)]
    motivos += [('CANTIDAD', f'Recibido menor que lo facturado: {t}') for t in diferencias_cantidad(compra)]
    return motivos


def cantidad_sugerida(oc):
    """ALM-14: {producto: cantidad recibida y aún no facturada} de la orden (en unidad de almacén)."""
    recibido, facturado = recibido_oc(oc), facturado_oc(oc)
    return {k: v - facturado.get(k, D0) for k, v in recibido.items() if v > facturado.get(k, D0)}


def retener_si_corresponde(compra):
    """Fuera de la tolerancia de precio: la factura queda retenida para pago hasta que Compras la libere."""
    from .tolerancias import bloquear_pago, texto
    fuera = fuera_de_precio(compra)
    if fuera and not compra.pago_bloqueado:
        bloquear_pago(compra, f'Precio fuera de la tolerancia ({texto(compra.orden_compra.tercero, "precio")}): '
                              + '; '.join(fuera))
    return fuera
