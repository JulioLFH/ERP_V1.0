"""Saldo del pedido de venta (VEN-21): por línea lo pedido, facturado, despachado y pendiente; la factura propone el
saldo y no supera lo pedido; el pedido queda «Atendido / Facturado» solo con todo facturado (o su saldo cerrado);
no se anula con facturas vigentes y sus cambios de estado quedan en el historial."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

D0 = Decimal('0')
VENTAS = ('01', '03', '12', '00')


class ErrorPedido(Exception):
    pass


def _clave(item):
    return item.producto_id or (item.descripcion or '').strip().lower()


def facturado(cot, excluir=None):
    """{producto o descripción: cantidad} facturada en los comprobantes vigentes del pedido menos sus notas de
    crédito."""
    from .models import Venta
    salida = defaultdict(lambda: D0)
    qs = cot.ventas.filter(estado='REGISTRADO')
    if excluir is not None and excluir.pk:
        qs = qs.exclude(pk=excluir.pk)
    for v in qs.prefetch_related('items'):
        if v.tipo_comprobante not in VENTAS:
            continue
        for i in v.items.all():
            salida[_clave(i)] += i.cantidad
        for n in Venta.objects.filter(doc_referencia=v, estado='REGISTRADO', tipo_comprobante='07').prefetch_related(
                'items'):
            for i in n.items.all():
                if i.cantidad and (n.motivo_nota or '') in ('06', '07', '01', '02'):
                    salida[_clave(i)] -= i.cantidad
    return salida


def despachado(cot):
    """{producto: cantidad} que salió del almacén por los comprobantes del pedido."""
    from inventario.servicios import _cantidades, _de_operaciones
    salida = defaultdict(lambda: D0)
    for v in cot.ventas.filter(estado='REGISTRADO', tipo_comprobante__in=VENTAS):
        if v.stock_aplicado:
            for k, c in _cantidades(v.items.select_related('producto')).items():
                salida[k] += c
        else:
            for k, c in _de_operaciones({'operacion__venta': v}, ['SALIDA']).items():
                salida[k] += c
    return salida


def avance(cot, excluir=None):
    fact, desp = facturado(cot, excluir), despachado(cot)
    usado_f, usado_d, filas = defaultdict(lambda: D0), defaultdict(lambda: D0), []
    for i in cot.items.select_related('producto').order_by('id'):
        clave = _clave(i)
        f = max(min(fact.get(clave, D0) - usado_f[clave], i.cantidad), D0)
        usado_f[clave] += f
        bien = bool(i.producto_id and i.producto.es_inventariable)
        d = None
        if bien:
            d = max(min(desp.get(i.producto_id, D0) - usado_d[clave], i.cantidad), D0)
            usado_d[clave] += d
        filas.append({'item': i, 'pedido': i.cantidad, 'facturado': f, 'despachado': d, 'pendiente': i.cantidad - f})
    return filas


def situacion(cot, filas=None):
    if cot.estado in ('PENDIENTE', 'ANULADO') or cot.tipo != 'PED':
        return cot.get_estado_display()
    if getattr(cot, 'saldo_cerrado', False):
        return 'Cerrado (saldo cancelado)'
    filas = avance(cot) if filas is None else filas
    total, hecho = sum((f['pedido'] for f in filas), D0), sum((f['facturado'] for f in filas), D0)
    if total and hecho >= total:
        return 'Facturado'
    return 'Facturado parcial' if hecho else 'Aprobado · por facturar'


def _historial(cot, accion, texto, cambios=None):
    from core.auditoria import registrar
    registrar(accion, cot, cambios or {}, texto[:250])


def actualizar_estado(cot, motivo='Comprobante emitido'):
    """«Atendido / Facturado» solo con todo facturado; con una factura parcial sigue «Aprobado» (VEN-21)."""
    from .models import Cotizacion
    cot.refresh_from_db()
    if cot.estado == 'ANULADO':
        return cot.estado
    filas = avance(cot)
    completo = bool(filas) and all(f['pendiente'] <= 0 for f in filas)
    if cot.tipo == 'COT':  # la cotización (no el pedido) se cierra con su primer comprobante, como antes
        completo = True
    nuevo = 'ATENDIDO' if completo else ('APROBADO' if cot.estado in ('APROBADO', 'ATENDIDO') else cot.estado)
    if nuevo != cot.estado:
        anterior = cot.get_estado_display()
        Cotizacion.objects.filter(pk=cot.pk).update(estado=nuevo)
        cot.estado = nuevo
        _historial(cot, 'MODIFICAR', f'Estado automático: {anterior} → {cot.get_estado_display()} ({motivo}; '
                                  f'{situacion(cot, filas)}).', {'estado': [anterior, cot.get_estado_display()]})
    return nuevo


def errores_factura(cot, lineas, venta=None):
    """La factura del pedido no supera lo pendiente de cada línea (VEN-21): lineas [(producto, descripción,
    cantidad)]."""
    if cot is None or cot.tipo != 'PED':
        return []
    pendiente = defaultdict(lambda: D0)
    for f in avance(cot, excluir=venta):
        pendiente[_clave(f['item'])] += f['pendiente']
    pedir = defaultdict(lambda: D0)
    nombres = {}
    for producto, descripcion, cantidad in lineas:
        clave = producto.pk if producto is not None else (descripcion or '').strip().lower()
        pedir[clave] += cantidad or D0
        nombres[clave] = producto.nombre if producto is not None else descripcion
    errores = []
    for clave, cantidad in pedir.items():
        if clave in pendiente and cantidad > pendiente[clave] + Decimal('0.0001'):
            errores.append(f'{nombres[clave]}: {cantidad:,.2f} supera el saldo del pedido {cot.numero} '
                           f'({pendiente[clave]:,.2f} por facturar)')
    return errores


def items_saldo(cot):
    """Ítems propuestos para la siguiente factura del pedido: solo lo pendiente de cada línea."""
    salida = []
    for f in avance(cot):
        if f['pendiente'] > 0:
            i = f['item']
            salida.append({'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': f['pendiente'],
                           'precio_unitario': i.precio_unitario, 'descuento_pct': i.descuento_pct})
    return salida


@transaction.atomic
def anular(cot, usuario, motivo):
    if cot.estado == 'ANULADO':
        raise ErrorPedido('El documento ya está anulado.')
    vivas = [str(v) for v in cot.ventas.filter(estado='REGISTRADO')[:6]]
    if vivas:
        raise ErrorPedido(f'{cot.numero} tiene comprobantes vigentes ({", ".join(vivas)}): no se anula. Si el cliente '
                          f'ya no comprará el resto, cierre su saldo.')
    if len((motivo or '').strip()) < 5:
        raise ErrorPedido('Indique el motivo de la anulación (mínimo 5 caracteres).')
    anterior = cot.get_estado_display()
    cot.estado = 'ANULADO'
    cot.save(update_fields=['estado'])
    _historial(cot, 'ANULAR', f'Anulado: {motivo.strip()}', {'estado': [anterior, 'Anulado']})
    return cot


@transaction.atomic
def cerrar_saldo(cot, usuario, motivo):
    """El cliente no comprará lo pendiente: el pedido pasa a «Atendido / Facturado» con el motivo en el historial."""
    if cot.tipo != 'PED' or cot.estado != 'APROBADO':
        raise ErrorPedido('Solo se cierra el saldo de un pedido aprobado.')
    if len((motivo or '').strip()) < 5:
        raise ErrorPedido('Indique el motivo del cierre del saldo.')
    filas = avance(cot)
    if not any(f['pendiente'] > 0 for f in filas):
        raise ErrorPedido(f'{cot.numero} no tiene saldo pendiente.')
    pendiente = '; '.join(f'{f["item"].descripcion[:40]} {f["pendiente"]:,.2f}' for f in filas if f['pendiente'] > 0)
    cot.estado = 'ATENDIDO'
    cot.save(update_fields=['estado'])
    _historial(cot, 'MODIFICAR', f'Saldo cerrado ({motivo.strip()}). Pendiente que ya no se facturará: {pendiente}.',
               {'estado': ['Aprobado', 'Atendido / Facturado (saldo cerrado)']})
    return cot
