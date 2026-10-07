"""Requerimientos internos: un área pide materiales al almacén contra su centro de costo.

Borrador -> Por aprobar -> Aprobado -> Atendido parcialmente / Atendido (o Rechazado / Anulado, con motivo).
El almacén atiende lo que tiene (despacho parcial); lo que falta se puede pasar a una orden de compra. El consumo sale
con una operación "Atención de requerimiento" que lleva la cuenta de gasto y el centro de costo del requerimiento."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import Serie

from . import servicios as inv
from .models import Operacion, RequerimientoInterno, TipoOperacion

D0 = Decimal('0')


class ErrorRequerimiento(Exception):
    pass


def enviar(req, usuario):
    if req.estado != 'BORRADOR':
        raise ErrorRequerimiento('Solo se envían requerimientos en borrador.')
    if not req.items.exists():
        raise ErrorRequerimiento('Agregue al menos un producto.')
    with transaction.atomic():
        if not req.numero:
            serie, numero = Serie.siguiente('REQ', 'RQ01')
            req.numero = f'{serie}-{numero}'
        req.estado = 'ENVIADO'
        req.save()
    return req


def aprobar(req, usuario):
    if req.estado != 'ENVIADO':
        raise ErrorRequerimiento('Solo se aprueban requerimientos por aprobar.')
    if req.solicitante_id == usuario.pk and not usuario.is_superuser:
        raise ErrorRequerimiento('Un requerimiento no lo aprueba quien lo pidió.')
    from core.segregacion import error_aprobacion
    if error_aprobacion(usuario, req):  # estricta: tampoco el administrador aprueba lo suyo
        raise ErrorRequerimiento(error_aprobacion(usuario, req))
    req.estado, req.aprobado_por, req.aprobado_en = 'APROBADO', usuario, timezone.now()
    req.save()
    return req


def rechazar(req, usuario, motivo, anular=False):
    if len((motivo or '').strip()) < 10:
        raise ErrorRequerimiento('Indique el motivo (mínimo 10 caracteres).')
    if anular:
        if req.estado in ('ATENDIDO', 'ANULADO') or req.atenciones.filter(estado='CONFIRMADO').exists():
            raise ErrorRequerimiento('Ya tiene despachos: no se anula (anule primero sus atenciones en Inventario).')
        req.estado = 'ANULADO'
    else:
        if req.estado != 'ENVIADO':
            raise ErrorRequerimiento('Solo se rechazan requerimientos por aprobar.')
        if req.solicitante_id == usuario.pk and not usuario.is_superuser:
            raise ErrorRequerimiento('Un requerimiento no lo rechaza quien lo pidió.')
        req.estado, req.aprobado_por, req.aprobado_en = 'RECHAZADO', usuario, timezone.now()
    req.motivo_rechazo = motivo.strip()[:250]
    req.save()
    return req


def atender(req, usuario, cantidades, fecha=None):
    """Despacha del almacén: cantidades = {item_id: cantidad a entregar}. Crea y confirma la salida."""
    if req.estado not in ('APROBADO', 'PARCIAL'):
        raise ErrorRequerimiento('Solo se atienden requerimientos aprobados.')
    items = {i.pk: i for i in req.items.select_related('producto')}
    lineas = [(items[pk], cant) for pk, cant in cantidades.items() if pk in items and cant and cant > 0]
    if not lineas:
        raise ErrorRequerimiento('Indique las cantidades a entregar.')
    for item, cant in lineas:
        if cant > item.pendiente:
            raise ErrorRequerimiento(f'{item.producto.nombre}: se entregarían {cant:,.2f} y solo faltan '
                                     f'{item.pendiente:,.2f}.')
    with transaction.atomic():
        op = Operacion.objects.create(
            tipo=TipoOperacion.objects.get(codigo='CONS_REQ'), fecha=fecha or timezone.localdate(),
            almacen_origen=req.almacen, centro_costo=req.centro_costo, cuenta_gasto=req.cuenta_gasto,
            referencia=req.numero, requerimiento=req, creado_por=usuario,
            glosa=f'Atención del requerimiento {req.numero}: {req.motivo}'[:500])
        for item, cant in lineas:
            op.items.create(producto=item.producto, cantidad=cant)
        try:
            inv.confirmar(op, usuario)
        except inv.ErrorOperacion as exc:
            raise ErrorRequerimiento(str(exc)) from exc
        for item, cant in lineas:
            item.cantidad_atendida += cant
            item.save(update_fields=['cantidad_atendida'])
        req.estado = 'ATENDIDO' if all(i.pendiente <= 0 for i in req.items.all()) else 'PARCIAL'
        req.save(update_fields=['estado'])
    return op


def faltantes(req):
    """[(item, cantidad que no alcanza en el almacén)] de lo pendiente del requerimiento."""
    filas = []
    for i in req.items.select_related('producto'):
        stock = i.producto.stock_en(req.almacen)
        if i.pendiente > stock and not i.orden_compra_id:
            filas.append((i, i.pendiente - max(stock, D0)))
    return filas


def pasar_a_compras(req, usuario):
    """Lo que el almacén no puede atender se pide al proveedor habitual: una orden de compra por proveedor."""
    from compras.models import OrdenCompra, OrdenCompraItem
    if req.estado not in ('APROBADO', 'PARCIAL'):
        raise ErrorRequerimiento('Solo se compran faltantes de requerimientos aprobados.')
    por_proveedor = defaultdict(list)
    sin_proveedor = []
    for item, cantidad in faltantes(req):
        (por_proveedor[item.producto.proveedor_id] if item.producto.proveedor_id else sin_proveedor).append(
            (item, cantidad))
    if sin_proveedor:
        raise ErrorRequerimiento('Sin proveedor habitual: ' + ', '.join(i.producto.nombre for i, _ in sin_proveedor)
                                 + '. Asígnelo en el producto.')
    ordenes = []
    with transaction.atomic():
        for proveedor_id, filas in por_proveedor.items():
            serie, numero = Serie.siguiente('OC', 'OC01')
            oc = OrdenCompra.objects.create(numero=f'{serie}-{numero}', tercero_id=proveedor_id,
                                            fecha=timezone.localdate(), fecha_entrega=req.fecha_requerida,
                                            centro_costo=req.centro_costo,
                                            glosa=f'Faltantes del requerimiento {req.numero}')
            for item, cantidad in filas:  # en unidad de compra
                OrdenCompraItem.objects.create(documento=oc, producto=item.producto, descripcion=item.producto.nombre,
                                               cantidad=item.producto.a_compra(cantidad),
                                               precio_unitario=item.producto.precio_compra or D0)
                item.orden_compra = oc
                item.save(update_fields=['orden_compra'])
            oc.calcular_totales()
            oc.save()
            ordenes.append(oc)
    return ordenes
