"""Reglas de las operaciones de inventario: pendientes por documento de origen, confirmación y anulación."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Almacen, Kardex, Producto, Serie, r2
from core.utils import faltantes_stock

from .models import PREFIJOS, Operacion, OperacionItem

D0 = Decimal('0')


class ErrorOperacion(Exception):
    pass


def _cantidades(items):
    """{producto_id: cantidad} de ítems con producto inventariable."""
    total = defaultdict(lambda: D0)
    for i in items:
        if i.producto_id and i.producto.es_inventariable:
            total[i.producto_id] += i.cantidad
    return total


def _cantidades_compra(items):
    """{producto_id: cantidad en unidad de almacén} de ítems de documentos de compra (vienen en unidad de compra)."""
    total = defaultdict(lambda: D0)
    for i in items:
        if i.producto_id and i.producto.es_inventariable:
            total[i.producto_id] += i.producto.a_stock(i.cantidad)
    return total


def _de_operaciones(filtro, clases, excluir=None):
    qs = OperacionItem.objects.filter(operacion__estado='CONFIRMADO', operacion__tipo__clase__in=clases, **filtro)
    if excluir is not None and excluir.pk:
        qs = qs.exclude(operacion=excluir)
    return {r['producto_id']: r['c'] for r in qs.values('producto_id').annotate(c=Sum('cantidad'))}


def _notas_con_stock(doc):
    notas = doc.notas.filter(estado='REGISTRADO', tipo_comprobante='07', stock_aplicado=True)
    contar = _cantidades_compra if doc._meta.model_name == 'compra' else _cantidades
    total = defaultdict(lambda: D0)
    for n in notas:
        for k, v in contar(n.items.select_related('producto')).items():
            total[k] += v
    return total


def pendientes(op):
    """{producto_id: (cantidad pendiente, costo sugerido)} según el documento de origen de la operación."""
    tipo = op.tipo
    resultado = {}
    if tipo.clase == 'INGRESO' and (op.orden_compra_id or op.compra_id):  # recepción de compras
        doc = op.orden_compra or op.compra
        tc = doc.tipo_cambio if doc.moneda == 'USD' else Decimal('1')
        pedido, costo = defaultdict(lambda: D0), {}
        for i in doc.items.select_related('producto'):
            if i.producto_id and i.producto.es_inventariable:  # unidad de compra -> unidad de almacén
                pedido[i.producto_id] += i.producto.a_stock(i.cantidad)
                costo[i.producto_id] = (i.precio_neto * tc / i.producto.factor).quantize(Decimal('0.0001'))
        filtro = {'operacion__orden_compra': doc} if op.orden_compra_id else {'operacion__compra': doc}
        recibido = _de_operaciones(filtro, ['INGRESO'], excluir=op)
        if op.orden_compra_id:  # facturas de la orden ya recibidas o que ingresaron al almacén directamente
            for k, v in _de_operaciones({'operacion__compra__orden_compra': doc}, ['INGRESO'], excluir=op).items():
                recibido[k] = recibido.get(k, D0) + v
            for c in doc.compras.filter(estado='REGISTRADO', stock_aplicado=True).exclude(tipo_comprobante='07'):
                for k, v in _cantidades_compra(c.items.select_related('producto')).items():
                    recibido[k] = recibido.get(k, D0) + v
        elif op.compra.stock_aplicado:
            recibido = dict(pedido)
        elif op.compra.orden_compra_id:  # recepciones hechas contra la orden de la factura
            for k, v in _de_operaciones({'operacion__orden_compra_id': op.compra.orden_compra_id},
                                        ['INGRESO'], excluir=op).items():
                recibido[k] = recibido.get(k, D0) + v
        for k, v in pedido.items():
            resultado[k] = (v - recibido.get(k, D0), costo.get(k))
    elif tipo.clase == 'SALIDA' and op.compra_id:  # devolución a proveedor
        c = op.compra
        if c.stock_aplicado:
            recibido = _cantidades_compra(c.items.select_related('producto'))
        else:
            recibido = _de_operaciones({'operacion__compra': c}, ['INGRESO'])
            if c.orden_compra_id:
                for k, v in _de_operaciones({'operacion__orden_compra_id': c.orden_compra_id}, ['INGRESO']).items():
                    recibido[k] = recibido.get(k, D0) + v
            facturado = _cantidades_compra(c.items.select_related('producto'))
            recibido = {k: min(v, facturado.get(k, D0)) for k, v in recibido.items()}
        devuelto = _de_operaciones({'operacion__compra': c}, ['SALIDA'], excluir=op)
        for k, v in _notas_con_stock(c).items():
            devuelto[k] = devuelto.get(k, D0) + v
        for k, v in recibido.items():
            resultado[k] = (v - devuelto.get(k, D0), None)
    elif op.venta_id:
        v = op.venta
        vendido = _cantidades(v.items.select_related('producto'))
        if tipo.clase == 'SALIDA':  # despacho de una venta que no movió el almacén
            despachado = dict(vendido) if v.stock_aplicado else \
                _de_operaciones({'operacion__venta': v}, ['SALIDA'], excluir=op)
            for k, cant in vendido.items():
                resultado[k] = (cant - despachado.get(k, D0), None)
        else:  # devolución de clientes
            entregado = vendido if v.stock_aplicado else _de_operaciones({'operacion__venta': v}, ['SALIDA'])
            devuelto = _de_operaciones({'operacion__venta': v}, ['INGRESO'], excluir=op)
            for k, cant in _notas_con_stock(v).items():
                devuelto[k] = devuelto.get(k, D0) + cant
            for k, cant in entregado.items():
                resultado[k] = (cant - devuelto.get(k, D0), costo_de_venta(v, k))
    elif tipo.clase == 'TRANSITO_RECEPCION' and op.envio_id:
        enviado = _cantidades(op.envio.items.select_related('producto'))
        recibido = _de_operaciones({'operacion__envio': op.envio}, ['TRANSITO_RECEPCION'], excluir=op)
        for k, cant in enviado.items():
            resultado[k] = (cant - recibido.get(k, D0), None)
    return {k: v for k, v in resultado.items() if v[0] > 0}


def costo_de_venta(venta, producto_id):
    """Costo con el que salió la mercadería en esa venta (para valorizar la devolución)."""
    filas = Kardex.objects.filter(producto_id=producto_id, tipo='SALIDA', referencia=str(venta))
    if not filas.exists():
        filas = Kardex.objects.filter(producto_id=producto_id, tipo='SALIDA', origen='OPERACION',
                                      referencia__endswith=f'| {venta}')
    agg = filas.aggregate(c=Sum('cantidad'))
    if agg['c']:
        valor = sum((k.cantidad * k.costo_unitario for k in filas), D0)
        return (valor / agg['c']).quantize(Decimal('0.0001'))
    return Producto.objects.get(pk=producto_id).costo_promedio


def items_desde_origen(op):
    """Ítems sugeridos (pendientes) para prellenar el formulario."""
    filas = []
    for pid, (cantidad, costo) in pendientes(op).items():
        # cantidad × factor deja 4–6 decimales (160.560000) y el formulario solo admite 2: se normaliza
        filas.append({'producto': pid, 'cantidad': Decimal(cantidad).quantize(Decimal('0.01')), 'costo_unitario': costo,
                      'observacion': ''})
    return filas


# ---------------------------------------------------------------- validación y confirmación
def errores_confirmacion(op):
    tipo = op.tipo
    items = list(op.items.select_related('producto'))
    errores = []
    if not items:
        return ['Agregue al menos un producto.']
    from .cierre import error_cierre
    if error_cierre(op.fecha):
        errores.append(error_cierre(op.fecha))
    if op.fecha and op.fecha > timezone.localdate():  # ALM-08: ni kardex ni contabilidad de un periodo futuro
        errores.append(f'La fecha contable ({op.fecha:%d/%m/%Y}) no puede ser futura.')
    from .fisico import ProductoEnConteo, validar_movimiento
    for almacen in {op.almacen_origen, op.almacen_destino} - {None}:  # inventario físico con bloqueo
        for i in items:
            try:
                validar_movimiento(i.producto, almacen)
            except ProductoEnConteo as exc:
                errores.append(str(exc))
    if tipo.requiere_sustento and op.pk:
        from core.sustentos import tiene
        if not tiene(op):
            errores.append(f'"{tipo.nombre}" exige sustento: adjunte el documento (acta, informe, guía, etc.).')
    if tipo.usa_origen_almacen and not op.almacen_origen_id and tipo.clase != 'TRANSITO_RECEPCION':
        errores.append('Indique el almacén de origen.')
    if tipo.usa_destino_almacen and not op.almacen_destino_id:
        errores.append('Indique el almacén de destino.')
    if tipo.clase in ('TRASLADO', 'TRANSITO_ENVIO') and op.almacen_origen_id == op.almacen_destino_id:
        errores.append('El almacén de destino debe ser distinto del origen.')
    if tipo.origen == 'ORDEN_COMPRA' and not (op.orden_compra_id or op.compra_id):
        errores.append('Indique la orden de compra o la factura que se recibe.')
    if op.orden_compra_id and op.orden_compra.estado not in ('APROBADO', 'ATENDIDO'):
        errores.append(f'La orden de compra {op.orden_compra.numero} está «{op.orden_compra.get_estado_display()}»: '
                       f'solo se recibe mercadería de órdenes aprobadas. Apruébela primero en Compras.')
    if tipo.origen == 'COMPRA' and not op.compra_id:
        errores.append('Indique la factura de compra.')
    if tipo.origen == 'VENTA' and not op.venta_id:
        errores.append('Indique la factura o boleta de venta.')
    if tipo.origen == 'TRANSITO' and not op.envio_id:
        errores.append('Indique el traslado a tránsito que se recibe.')
    if tipo.clase == 'MANUFACTURA':
        roles = {i.rol for i in items}
        if 'INSUMO' not in roles or 'PRODUCTO' not in roles:
            errores.append('La manufactura necesita al menos un insumo y un producto terminado.')
    if tipo.requiere_costo:
        for i in items:
            if not i.costo_unitario and not i.producto.costo_promedio:
                errores.append(f'Indique el costo unitario de "{i.producto.nombre}".')
    if tipo.origen:
        limite = pendientes(op)
        for pid, cantidad in _cantidades(items).items():
            disponible = limite.get(pid, (D0, None))[0]
            if cantidad > disponible:
                nombre = Producto.objects.get(pk=pid).nombre
                errores.append(f'"{nombre}": el documento de origen solo permite {disponible:,.2f} '
                               f'(se indican {cantidad:,.2f}).')
    salidas = _lineas_que_salen(op, items)
    if salidas:
        almacen = Almacen.especial('TRANSITO') if tipo.clase == 'TRANSITO_RECEPCION' else op.almacen_origen
        errores += faltantes_stock(salidas, almacen, fecha=op.fecha)  # sin negativos en su fecha ni después
    errores += errores_lotes(op, items)
    return errores


def errores_lotes(op, items):
    """Productos con lote o serie: el lote es obligatorio al ingresar; las series deben coincidir con la cantidad
    y, al sacar lotes indicados, deben tener stock en el almacén."""
    from core.models import StockLote
    clase = op.tipo.clase
    errores = []
    for i in items:
        control = i.producto.control
        if not control:
            continue
        entra = clase == 'INGRESO' or (clase == 'MANUFACTURA' and i.rol in ('PRODUCTO', 'SUBPROD'))
        sale = clase in ('SALIDA', 'TRASLADO', 'TRANSITO_ENVIO') or (clase == 'MANUFACTURA' and i.rol == 'INSUMO')
        codigos = i.codigos_lote
        nombre = i.producto.nombre
        if control == 'SERIE' and codigos:
            if i.cantidad != int(i.cantidad) or len(codigos) != int(i.cantidad):
                errores.append(f'"{nombre}": indique {i.cantidad.normalize():f} número(s) de serie '
                               f'(indicó {len(codigos)}).')
                continue
            if len(set(codigos)) != len(codigos):
                errores.append(f'"{nombre}": hay números de serie repetidos.')
        if entra:
            if not codigos:
                errores.append(f'"{nombre}" se controla por {"lote" if control == "LOTE" else "número de serie"}: '
                               f'indíquelo.')
            elif control == 'SERIE':
                existentes = StockLote.objects.filter(lote__producto=i.producto, lote__codigo__in=codigos,
                                                      cantidad__gt=0).values_list('lote__codigo', flat=True)
                if existentes:
                    errores.append(f'"{nombre}": las series {", ".join(existentes)} ya están en stock.')
            elif control == 'LOTE' and len(codigos) > 1:
                errores.append(f'"{nombre}": indique un solo lote por línea (use otra línea para otro lote).')
        elif sale and codigos and op.almacen_origen_id:
            necesarios = ({c: Decimal('1') for c in codigos} if control == 'SERIE' else {codigos[0]: i.cantidad})
            stock = dict(StockLote.objects.filter(lote__producto=i.producto, almacen_id=op.almacen_origen_id,
                                                  lote__codigo__in=necesarios).values_list('lote__codigo', 'cantidad'))
            for codigo, cant in necesarios.items():
                if stock.get(codigo, D0) < cant:
                    errores.append(f'"{nombre}": el lote/serie {codigo} solo tiene '
                                   f'{stock.get(codigo, D0).normalize():f} en {op.almacen_origen}.')
    return errores


def costos_documento(op):
    """{producto_id: costo unitario en soles por unidad de almacén} de la orden o la factura que se recibe."""
    doc = op.orden_compra or op.compra
    if doc is None:
        return {}
    tc = doc.tipo_cambio if doc.moneda == 'USD' else Decimal('1')
    costos = {}
    for i in doc.items.select_related('producto'):
        if i.producto_id and i.producto.es_inventariable:
            costos[i.producto_id] = (i.precio_neto * tc / i.producto.factor).quantize(Decimal('0.0001'))
    return costos


def _lineas_que_salen(op, items):
    clase = op.tipo.clase
    if clase in ('SALIDA', 'TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION'):
        return [(i.producto, i.cantidad) for i in items]
    if clase == 'MANUFACTURA':
        return [(i.producto, i.cantidad) for i in items if i.rol == 'INSUMO']
    return []


def _mover(item, cantidad, op, almacen, costo, codigo):
    base = f'{op.tipo.nombre} {op.numero}'
    lotes = item.lotes_para(cantidad)
    # en traslados la entrada al destino lleva los mismos lotes que salieron del origen
    lotes_de = base if not lotes and cantidad > 0 and op.tipo.clase in (
        'TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION') else None
    item.producto.mover_stock(cantidad, base + (f' | {op.venta}' if op.venta_id else ''),
                              costo=costo, fecha=op.fecha, almacen=almacen, origen='OPERACION',
                              concepto=op.tipo.codigo, codigo_sunat=codigo, lotes=lotes, lotes_de=lotes_de,
                              centro_costo=op.centro_costo, cuenta_contra=op.cuenta_gasto)


def confirmar(op, usuario=None):
    if op.estado != 'BORRADOR':
        raise ErrorOperacion('Solo se confirman operaciones en borrador.')
    errores = errores_confirmacion(op)
    if errores:
        raise ErrorOperacion(' '.join(errores))
    tipo = op.tipo
    entrada_cod = tipo.codigo_sunat_ingreso or '21'
    with transaction.atomic():
        prefijo = PREFIJOS[tipo.clase]
        serie, numero = Serie.siguiente(prefijo, f'{prefijo}01')
        op.numero = f'{serie}-{numero}'
        items = list(op.items.select_related('producto'))
        transito = Almacen.especial('TRANSITO') if tipo.clase in ('TRANSITO_ENVIO', 'TRANSITO_RECEPCION') else None
        if tipo.clase == 'INGRESO':
            # recepción de compras: el costo es el de la orden o la factura, no el que se escriba (ALM-07)
            del_documento = costos_documento(op) if (op.orden_compra_id or op.compra_id) else {}
            for i in items:
                if i.producto_id in del_documento and i.costo_unitario != del_documento[i.producto_id]:
                    i.costo_unitario = del_documento[i.producto_id]
                    i.save(update_fields=['costo_unitario'])
                costo = i.costo_unitario if i.costo_unitario else None
                _mover(i, i.cantidad, op, op.almacen_destino, costo, tipo.codigo_sunat)
        elif tipo.clase == 'SALIDA':
            for i in items:
                i.costo_unitario = Producto.objects.get(pk=i.producto_id).costo_promedio
                i.save(update_fields=['costo_unitario'])
                _mover(i, -i.cantidad, op, op.almacen_origen, None, tipo.codigo_sunat)
        elif tipo.clase in ('TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION'):
            origen = transito if tipo.clase == 'TRANSITO_RECEPCION' else op.almacen_origen
            destino = transito if tipo.clase == 'TRANSITO_ENVIO' else op.almacen_destino
            for i in items:
                i.costo_unitario = Producto.objects.get(pk=i.producto_id).costo_promedio
                i.save(update_fields=['costo_unitario'])
                _mover(i, -i.cantidad, op, origen, None, tipo.codigo_sunat)
                _mover(i, i.cantidad, op, destino, None, entrada_cod)
        elif tipo.clase == 'MANUFACTURA':
            insumos = [i for i in items if i.rol == 'INSUMO']
            productos = [i for i in items if i.rol == 'PRODUCTO']
            valor = D0
            for i in insumos:
                i.costo_unitario = Producto.objects.get(pk=i.producto_id).costo_promedio
                i.save(update_fields=['costo_unitario'])
                valor += i.cantidad * i.costo_unitario
                _mover(i, -i.cantidad, op, op.almacen_origen, None, tipo.codigo_sunat)
            valor += op.costo_adicional or D0  # mano de obra y costos indirectos (órdenes de producción)
            # subproductos y coproductos entran al costo que les asignó la orden; el producto principal, al resto
            for i in (x for x in items if x.rol == 'SUBPROD'):
                costo_sub = i.costo_unitario or D0
                valor -= i.cantidad * costo_sub
                _mover(i, i.cantidad, op, op.almacen_destino, costo_sub, tipo.codigo_sunat_ingreso or '19')
            unidades = sum((i.cantidad for i in productos), D0)
            costo = (max(valor, D0) / unidades).quantize(Decimal('0.0001')) if unidades else D0
            for i in productos:
                i.costo_unitario = costo
                i.save(update_fields=['costo_unitario'])
                _mover(i, i.cantidad, op, op.almacen_destino, costo, tipo.codigo_sunat_ingreso or '19')
        op.estado = 'CONFIRMADO'
        op.stock_aplicado = True
        op.confirmado_por = usuario if usuario and usuario.is_authenticated else None
        op.confirmado_en = timezone.now()
        op.save()
        _actualizar_vencimientos(op)
    return op


def orden_de(op):
    """Orden de compra de una recepción (directa o a través de su factura)."""
    if op.orden_compra_id:
        return op.orden_compra
    return op.compra.orden_compra if op.compra_id and op.compra.orden_compra_id else None


def _actualizar_vencimientos(op):
    """Las facturas de la orden vencen desde la fecha de ingreso de la mercadería; además se liquida la diferencia
    entre el precio facturado y el costo de la recepción (cuenta 28)."""
    if op.tipo.clase == 'INGRESO':
        oc = orden_de(op)
        if oc:
            oc.actualizar_vencimientos()
        from compras.models import Compra
        from compras.precios import liquidar
        facturas = Compra.objects.filter(pk=op.compra_id) if op.compra_id else Compra.objects.none()
        if oc:
            facturas = facturas | Compra.objects.filter(orden_compra=oc)
        for f in facturas.distinct():
            liquidar(f)


def errores_anulacion(op, desde_conteo=False):
    if op.estado != 'CONFIRMADO':
        return ['Solo se anulan operaciones confirmadas.']
    if not desde_conteo:  # el ajuste de un inventario físico se revierte desde el conteo (INV-23)
        from django.db.models import Q

        from .models import ConteoCiclico
        conteo = ConteoCiclico.objects.filter(Q(ajuste_ingreso=op) | Q(ajuste_salida=op), estado='CERRADO').first()
        if conteo is not None:
            return [f'Es el ajuste del inventario físico {conteo.numero}: revierta su contabilización desde el conteo '
                    f'(vuelve a «Aprobado»).']
    from .cierre import error_cierre
    if error_cierre(op.fecha):
        return [error_cierre(op.fecha)]
    if op.tipo.clase == 'TRANSITO_ENVIO' and op.recepciones.filter(estado='CONFIRMADO').exists():
        return ['El traslado ya tiene recepciones confirmadas: anúlelas primero.']
    # revertir saca stock de donde entró
    items = list(op.items.select_related('producto'))
    clase = op.tipo.clase
    if clase == 'INGRESO':
        return faltantes_stock([(i.producto, i.cantidad) for i in items], op.almacen_destino)
    if clase in ('TRASLADO', 'TRANSITO_RECEPCION'):
        return faltantes_stock([(i.producto, i.cantidad) for i in items], op.almacen_destino)
    if clase == 'TRANSITO_ENVIO':
        return faltantes_stock([(i.producto, i.cantidad) for i in items], Almacen.especial('TRANSITO'))
    if clase == 'MANUFACTURA':
        return faltantes_stock([(i.producto, i.cantidad) for i in items if i.rol in ('PRODUCTO', 'SUBPROD')],
                               op.almacen_destino)
    return []


def anular(op, usuario, motivo, desde_conteo=False):
    errores = errores_anulacion(op, desde_conteo)
    if errores:
        raise ErrorOperacion(' '.join(errores))
    if len((motivo or '').strip()) < 5:
        raise ErrorOperacion('Indique el motivo de la anulación (mínimo 5 caracteres).')
    tipo = op.tipo
    transito = Almacen.especial('TRANSITO') if tipo.clase in ('TRANSITO_ENVIO', 'TRANSITO_RECEPCION') else None
    with transaction.atomic():
        for i in op.items.select_related('producto'):
            ref = f'Anulación {tipo.nombre} {op.numero}'
            # cada movimiento vuelve a los lotes en que se hizo
            kw = dict(fecha=op.fecha, origen='OPERACION', concepto=tipo.codigo, lotes_de=f'{tipo.nombre} {op.numero}',
                      centro_costo=op.centro_costo, cuenta_contra=op.cuenta_gasto)
            if tipo.clase == 'INGRESO':  # sale al costo con que entró, no al promedio (INV-16)
                i.producto.mover_stock(-i.cantidad, ref, costo=i.costo_unitario or None, almacen=op.almacen_destino,
                                       codigo_sunat=tipo.codigo_sunat, **kw)
            elif tipo.clase == 'SALIDA':
                i.producto.mover_stock(i.cantidad, ref, costo=i.costo_unitario, almacen=op.almacen_origen,
                                       codigo_sunat=tipo.codigo_sunat, **kw)
            elif tipo.clase in ('TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION'):
                origen = transito if tipo.clase == 'TRANSITO_RECEPCION' else op.almacen_origen
                destino = transito if tipo.clase == 'TRANSITO_ENVIO' else op.almacen_destino
                i.producto.mover_stock(-i.cantidad, ref, almacen=destino, codigo_sunat='11', **kw)
                i.producto.mover_stock(i.cantidad, ref, almacen=origen, codigo_sunat='21', **kw)
            elif tipo.clase == 'MANUFACTURA':
                if i.rol in ('PRODUCTO', 'SUBPROD'):
                    i.producto.mover_stock(-i.cantidad, ref, almacen=op.almacen_destino, codigo_sunat='19', **kw)
                else:
                    i.producto.mover_stock(i.cantidad, ref, costo=i.costo_unitario, almacen=op.almacen_origen,
                                           codigo_sunat='10', **kw)
        op.estado = 'ANULADO'
        op.stock_aplicado = False
        op.motivo_anulacion = motivo.strip()[:250]
        op.anulado_por = usuario if usuario and usuario.is_authenticated else None
        op.anulado_en = timezone.now()
        op.save()
        _actualizar_vencimientos(op)
        if tipo.clase == 'INGRESO' and op.orden_compra_id:
            recepcion_anulada(op)
    return op


def facturas_de_recepcion(op):
    """Facturas vigentes de la orden que recibió la operación (para avisar antes de anularla)."""
    if not op.orden_compra_id:
        return []
    return list(op.orden_compra.compras.filter(estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08']))


def recepcion_anulada(op):
    """ALM-09: la orden vuelve a pendiente de recibir y sus facturas con más de lo recibido quedan retenidas para pago
    (con diferencia: nota de crédito o reingreso)."""
    from compras.cruce import recibido_oc, facturado_oc
    from compras.models import OrdenCompra
    from compras.tolerancias import bloquear_pago
    oc = op.orden_compra
    recibido, facturado = recibido_oc(oc), facturado_oc(oc)
    pedido = _cantidades_compra(oc.items.select_related('producto'))
    if any(recibido.get(k, D0) < v for k, v in pedido.items()):
        OrdenCompra.objects.filter(pk=oc.pk, estado='ATENDIDO').update(estado='APROBADO')
    exceso = {k: v - recibido.get(k, D0) for k, v in facturado.items() if v > recibido.get(k, D0)}
    if exceso:
        nombres = dict(Producto.objects.filter(pk__in=exceso).values_list('pk', 'nombre'))
        detalle = ', '.join(f'{nombres[k]}: facturado {facturado[k]:,.2f}, recibido {recibido.get(k, D0):,.2f}'
                            for k in exceso)
        for c in oc.compras.filter(estado='REGISTRADO', pago_bloqueado=False).exclude(tipo_comprobante__in=['07', '08']):
            bloquear_pago(c, f'Con diferencia: se anuló la recepción {op.numero} ({detalle}). Emita la nota de crédito '
                             f'o registre el reingreso.')


def contrapartidas(op):
    """Cuentas que la contabilidad usa de contrapartida para los productos de la operación (como al centralizar): la
    por recibir de cada producto (281 mercaderías, 284 materias primas, 285 suministros…) o su costo de ventas, en vez
    de la cuenta general del tipo."""
    from contabilidad.models import CuentaDefecto
    tipo = op.tipo
    if not tipo.cuenta_contable_id:
        return []
    cta = CuentaDefecto.mapa()
    cuentas = {}
    for i in op.items.select_related('producto__cuenta_compra__destino_debe', 'producto__cuenta_costo'):
        p, contra = i.producto, tipo.cuenta_contable
        if contra.pk == cta['mercaderia_por_recibir'].pk:
            c = p.cuenta_compra
            contra = c.destino_debe if c and c.destino_debe_id and c.destino_debe.codigo.startswith('28') else contra
        elif contra.pk == cta['costo_ventas'].pk and p.cuenta_costo_id:
            contra = p.cuenta_costo
        if op.cuenta_gasto_id and tipo.clase == 'SALIDA':
            contra = op.cuenta_gasto
        cuentas[contra.pk] = contra
    return sorted(cuentas.values(), key=lambda c: c.codigo) or [tipo.cuenta_contable]


def acciones_para(doc):
    """Botones de Inventario para el detalle de una orden de compra, compra o venta: [(texto, url, icono)]."""
    from django.urls import reverse

    from .models import TipoOperacion
    modelo = doc._meta.model_name
    if getattr(doc, 'estado', '') in ('ANULADO', 'ANULADA'):
        return []
    if modelo in ('compra', 'venta') and doc.tipo_comprobante in ('07', '08'):
        return []
    if modelo == 'ordencompra':
        opciones = [('REC_COMPRA', 'oc', 'Recibir mercadería')]
    elif modelo == 'compra':
        opciones = [('REC_COMPRA', 'compra', 'Recibir mercadería'), ('DEV_PROV', 'compra', 'Devolución a proveedor')]
    elif modelo == 'venta':
        opciones = [('SAL_VENTA', 'venta', 'Despachar (salida por venta)'),
                    ('DEV_CLI', 'venta', 'Devolución del cliente')]
    else:
        return []
    tipos = {t.codigo: t for t in TipoOperacion.objects.filter(codigo__in=[o[0] for o in opciones], activo=True)}
    acciones = []
    for codigo, param, texto in opciones:
        tipo = tipos.get(codigo)
        if tipo is None:
            continue
        op = Operacion(tipo=tipo, **{{'oc': 'orden_compra'}.get(param, param): doc})
        if pendientes(op):
            acciones.append((texto, f"{reverse('inventario:nueva')}?tipo={codigo}&{param}={doc.pk}", tipo.icono))
    return acciones


def operaciones_de(doc):
    modelo = doc._meta.model_name
    campo = {'ordencompra': 'orden_compra', 'compra': 'compra', 'venta': 'venta'}.get(modelo)
    if not campo:
        return Operacion.objects.none()
    filtro = Q(**{campo: doc})
    if modelo == 'compra' and doc.orden_compra_id:  # la mercadería pudo recibirse contra la orden de compra
        filtro |= Q(orden_compra_id=doc.orden_compra_id, tipo__clase='INGRESO', estado='CONFIRMADO')
    return Operacion.objects.filter(filtro).select_related('tipo')


def tiene_recepciones(compra=None, orden=None):
    """La compra u orden ya fue recibida con operaciones de inventario (evita doble ingreso)."""
    qs = Operacion.objects.filter(estado='CONFIRMADO', tipo__clase='INGRESO')
    if compra is not None:
        cond = qs.filter(compra=compra).exists()
        if compra.orden_compra_id:
            cond = cond or qs.filter(orden_compra_id=compra.orden_compra_id).exists()
        return cond
    return qs.filter(orden_compra=orden).exists()
