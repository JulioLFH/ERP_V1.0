"""Requerimientos internos: un área pide materiales al almacén contra su centro de costo (la reserva de SAP).

Borrador -> Por aprobar -> Aprobado -> Atendido parcialmente / Atendido (o Rechazado / Anulado, con motivo).
El almacén atiende lo que tiene (despacho parcial); lo que no tiene stock al aprobarse pasa solo a una solicitud de
pedido vinculada (Compras la convierte en orden de compra). El consumo sale con una operación "Atención de
requerimiento" que lleva la cuenta de gasto y el centro de costo del requerimiento."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import Serie

from . import servicios as inv
from .models import Operacion, RequerimientoInterno, TipoOperacion

D0 = Decimal('0')


class ErrorRequerimiento(Exception):
    pass


def monto(req):
    """Monto valorizado: cantidad × costo promedio de cada producto."""
    return sum((i.cantidad * i.producto.costo_promedio for i in req.items.select_related('producto')), D0)


def enviar(req, usuario):
    """Envía a aprobación. Con reglas de aprobación de requerimientos (jefe del solicitante, responsable del centro
    de costo…, por monto valorizado) se calculan los niveles; sin reglas lo aprueba quien tenga el permiso."""
    from core import aprobaciones
    if req.estado != 'BORRADOR':
        raise ErrorRequerimiento('Solo se envían requerimientos en borrador.')
    if not req.items.exists():
        raise ErrorRequerimiento('Agregue al menos un producto.')
    try:
        niveles = aprobaciones.calcular_niveles('REQUERIMIENTO', req.solicitante, monto(req), req.centro_costo)
    except aprobaciones.ErrorAprobacion as exc:
        raise ErrorRequerimiento(str(exc)) from exc
    with transaction.atomic():
        if not req.numero:
            serie, numero = Serie.siguiente('REQ', 'RQ01')
            req.numero = f'{serie}-{numero}'
        req.estado, req.niveles, req.nivel_aprobado = 'ENVIADO', niveles, 0
        req.save()
        aprobaciones.registrar('REQUERIMIENTO', req, usuario, 'ENVIADO')
    return req


def pendiente(req):
    from core.aprobaciones import pendiente as siguiente
    return siguiente(req.niveles, req.nivel_aprobado) if req.estado == 'ENVIADO' else None


def historial(req):
    from core.aprobaciones import historial as h
    return h('REQUERIMIENTO', req)


def puede_aprobar(user, req):
    from core import aprobaciones
    from core.permisos import puede
    if req.estado != 'ENVIADO':
        return False
    if req.niveles:
        return aprobaciones.puede_aprobar(user, 'REQUERIMIENTO', req.niveles, req.nivel_aprobado, req.solicitante_id)
    return puede(user, 'requerimientos.aprobar') and (req.solicitante_id != user.pk or user.is_superuser)


@transaction.atomic
def aprobar(req, usuario):
    """Aprueba su nivel; en el último, el requerimiento queda aprobado y lo que el almacén no tiene pasa a una
    solicitud de pedido (el área ve el avance)."""
    from core import aprobaciones
    if req.estado != 'ENVIADO':
        raise ErrorRequerimiento('Solo se aprueban requerimientos por aprobar.')
    if req.solicitante_id == usuario.pk and not usuario.is_superuser:
        raise ErrorRequerimiento('Un requerimiento no lo aprueba quien lo pidió.')
    from core.segregacion import error_aprobacion
    if error_aprobacion(usuario, req):  # estricta: tampoco el administrador aprueba lo suyo
        raise ErrorRequerimiento(error_aprobacion(usuario, req))
    if req.niveles:
        if not puede_aprobar(usuario, req):
            n = aprobaciones.pendiente(req.niveles, req.nivel_aprobado)
            raise ErrorRequerimiento(f'Lo aprueba ahora el nivel {n["nivel"]}: {n["nombre"]}.')
        req.nivel_aprobado = aprobaciones.pendiente(req.niveles, req.nivel_aprobado)['nivel']
        aprobaciones.registrar('REQUERIMIENTO', req, usuario, 'APROBADO', '', req.nivel_aprobado)
        if aprobaciones.pendiente(req.niveles, req.nivel_aprobado) is not None:
            aprobaciones.avanzar(req.niveles, req.nivel_aprobado)
            req.save(update_fields=['niveles', 'nivel_aprobado'])
            return req
    else:
        aprobaciones.registrar('REQUERIMIENTO', req, usuario, 'APROBADO')
    req.estado, req.aprobado_por, req.aprobado_en = 'APROBADO', usuario, timezone.now()
    req.save()
    try:
        with transaction.atomic():
            pasar_a_compras(req, usuario, exigir=False)
    except ErrorRequerimiento:
        pass  # p. ej. supera el presupuesto: queda el botón «Pedir faltantes a compras» con el motivo
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
        if not puede_aprobar(usuario, req):
            raise ErrorRequerimiento('No le corresponde revisar este requerimiento.')
        from core.aprobaciones import registrar
        registrar('REQUERIMIENTO', req, usuario, 'RECHAZADO', motivo)
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
    """[(item, cantidad que no alcanza en el almacén)] de lo pendiente del requerimiento que aún no se pidió a compras
    (orden de compra del flujo anterior o solicitud de pedido vigente)."""
    filas = []
    for i in req.items.select_related('producto').prefetch_related('lineas_solped__solicitud'):
        stock = i.producto.stock_en(req.almacen)
        pedido = i.orden_compra_id or any(l.solicitud.estado not in ('RECHAZADA', 'ANULADA')
                                          for l in i.lineas_solped.all())
        if i.pendiente > stock and not pedido:
            filas.append((i, i.pendiente - max(stock, D0)))
    return filas


def pasar_a_compras(req, usuario, exigir=True):
    """Lo que el almacén no puede atender pasa a una solicitud de pedido vinculada al requerimiento (Compras la
    convierte en orden de compra). None si no falta nada."""
    from compras.solped import ErrorSolped, desde_requerimiento
    if req.estado not in ('APROBADO', 'PARCIAL'):
        raise ErrorRequerimiento('Solo se compran faltantes de requerimientos aprobados.')
    try:
        sp = desde_requerimiento(req, req.solicitante)
    except ErrorSolped as exc:
        raise ErrorRequerimiento(f'No se pudo crear la solicitud de pedido: {exc}') from exc
    if sp is None and exigir:
        raise ErrorRequerimiento('No hay faltantes por pedir: el stock alcanza o ya están en una solicitud de pedido.')
    return sp
