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


def _propio(user, req):
    """REQ-03: quien pidió el requerimiento no lo aprueba, rechaza ni atiende (tampoco el administrador), salvo que
    sea el único usuario activo."""
    from core.segregacion import _otro_usuario
    return req.solicitante_id == user.pk and _otro_usuario(user)


def puede_aprobar(user, req):
    from core import aprobaciones
    from core.permisos import puede
    if req.estado != 'ENVIADO' or _propio(user, req):
        return False
    if req.niveles:
        return aprobaciones.puede_aprobar(user, 'REQUERIMIENTO', req.niveles, req.nivel_aprobado, req.solicitante_id)
    return puede(user, 'requerimientos.aprobar')


def en_lugar_de(user, req):
    """El administrador que aprueba un nivel que no es suyo (ni su suplente): el titular de ese nivel (o None)."""
    from core import aprobaciones
    n = aprobaciones.pendiente(req.niveles, req.nivel_aprobado) if req.niveles else None
    if not (n and n.get('usuario')) or not user.is_superuser:
        return None
    if user.pk in {u.pk for u in aprobaciones.aprobadores(n)}:
        return None
    return n


def aprobadores_de(req):
    """Quienes aprobaron el requerimiento (cualquier nivel)."""
    ids = set(historial(req).filter(accion='APROBADO').values_list('usuario_id', flat=True))
    if req.aprobado_por_id:
        ids.add(req.aprobado_por_id)
    return ids


def puede_atender(user, req):
    """REQ-02: la salida la registra el almacén (permiso de operar el inventario), nunca quien pidió el
    requerimiento ni quien lo aprobó (salvo que sea el único usuario activo)."""
    from core.permisos import puede
    from core.segregacion import _otro_usuario
    if not puede(user, 'inventario.operar'):
        return False
    return not ((req.solicitante_id == user.pk or user.pk in aprobadores_de(req)) and _otro_usuario(user))


@transaction.atomic
def aprobar(req, usuario, motivo=''):
    """Aprueba su nivel; en el último, el requerimiento queda aprobado y lo que el almacén no tiene pasa a una
    solicitud de pedido (el área ve el avance). El administrador que aprueba en lugar del aprobador del nivel lo
    hace como suplencia, con su motivo (queda en el historial)."""
    from core import aprobaciones
    if req.estado != 'ENVIADO':
        raise ErrorRequerimiento('Solo se aprueban requerimientos por aprobar.')
    if _propio(usuario, req):
        raise ErrorRequerimiento('Quien pidió el requerimiento no lo aprueba (tampoco el administrador): lo aprueba '
                                 'otro usuario.')
    from core.segregacion import error_aprobacion
    if error_aprobacion(usuario, req):  # estricta: tampoco el administrador aprueba lo suyo
        raise ErrorRequerimiento(error_aprobacion(usuario, req))
    if req.niveles:
        if not puede_aprobar(usuario, req):
            n = aprobaciones.pendiente(req.niveles, req.nivel_aprobado)
            raise ErrorRequerimiento(f'Lo aprueba ahora el nivel {n["nivel"]}: {n["nombre"]}.')
        comentario = ''
        reemplazado = en_lugar_de(usuario, req)
        if reemplazado is not None:
            if len((motivo or '').strip()) < 10:
                raise ErrorRequerimiento(f'El nivel {reemplazado["nivel"]} lo aprueba {reemplazado["nombre"]}: para '
                                         f'aprobar en su lugar indique el motivo (mínimo 10 caracteres) o registre una '
                                         f'suplencia en Ajustes › Suplencias.')
            comentario = f'Suplencia del administrador en lugar de {reemplazado["nombre"]}: {motivo.strip()}'
        req.nivel_aprobado = aprobaciones.pendiente(req.niveles, req.nivel_aprobado)['nivel']
        aprobaciones.registrar('REQUERIMIENTO', req, usuario, 'APROBADO', comentario, req.nivel_aprobado)
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
        if _propio(usuario, req):
            raise ErrorRequerimiento('Un requerimiento no lo rechaza quien lo pidió.')
        if not puede_aprobar(usuario, req):
            raise ErrorRequerimiento('No le corresponde revisar este requerimiento.')
        from core.aprobaciones import registrar
        registrar('REQUERIMIENTO', req, usuario, 'RECHAZADO', motivo)
        req.estado, req.aprobado_por, req.aprobado_en = 'RECHAZADO', usuario, timezone.now()
    req.motivo_rechazo = motivo.strip()[:250]
    req.save()
    return req


def atender(req, usuario, cantidades, fecha=None, recibe=''):
    """Despacha del almacén: cantidades = {item_id: cantidad a entregar}. Crea y confirma la salida (CONS_REQ) con
    quien recibe."""
    if req.estado not in ('APROBADO', 'PARCIAL'):
        raise ErrorRequerimiento('Solo se atienden requerimientos aprobados.')
    if not puede_atender(usuario, req):
        raise ErrorRequerimiento('El solicitante no puede atender su propio requerimiento ni quien lo aprobó: la '
                                 'entrega la registra el almacén.')
    recibe = (recibe or '').strip() or (req.solicitante.get_full_name() or req.solicitante.username
                                        if req.solicitante_id else '')
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
            glosa=f'Atención del requerimiento {req.numero}: {req.motivo}. Recibió: {recibe}'[:500])
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
