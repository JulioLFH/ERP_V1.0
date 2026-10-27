"""Seguimiento de las órdenes de compra (COM-22 a COM-25): por línea lo pedido, recibido, facturado y pendiente; la
situación de la orden (recibida o facturada parcial), sus entregas y facturas, el cierre del saldo que el proveedor no
entregará (individual o masivo, con motivo) y las reglas para anular o cambiar su estado.

- La orden pasa a «Atendido / Facturado» solo cuando todo lo pedido está facturado o su saldo se cerró; con una
  factura parcial sigue «Aprobado» y lo pendiente se ve en la orden, la lista y el reporte de seguimiento.
- Una orden con recepciones o facturas vigentes no se anula: se cierra su saldo (o se revierten primero).
- Todo cambio de estado, también los automáticos, queda en el historial de la orden."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

D0 = Decimal('0')
MOTIVOS_CIERRE = [('PROVEEDOR', 'El proveedor no entregará el saldo'), ('CAMBIO', 'Cambio de proveedor'),
                  ('ERROR', 'Cantidad pedida por error'), ('NO_NECESARIO', 'Ya no se necesita'), ('OTRO', 'Otro')]


class ErrorSeguimiento(Exception):
    pass


def _recibido(oc):
    """{producto_id: cantidad en unidad de almacén} recibida contra la orden (recepciones de la orden, recepciones de
    sus facturas y facturas que ingresaron directamente al almacén)."""
    from inventario.servicios import _cantidades_compra, _de_operaciones
    recibido = defaultdict(lambda: D0)
    for filtro in ({'operacion__orden_compra': oc}, {'operacion__compra__orden_compra': oc}):
        for k, v in _de_operaciones(filtro, ['INGRESO']).items():
            recibido[k] += v
    for c in oc.compras.filter(estado='REGISTRADO', stock_aplicado=True).exclude(tipo_comprobante__in=['07', '08']):
        for k, v in _cantidades_compra(c.items.select_related('producto')).items():
            recibido[k] += v
    return recibido


def _facturado(oc):
    """{producto_id o descripción: cantidad (unidad de almacén para los bienes)} en las facturas vigentes de la orden,
    menos sus notas de crédito con cantidad."""
    facturado = defaultdict(lambda: D0)
    for c in oc.compras.filter(estado='REGISTRADO').exclude(tipo_comprobante='08').prefetch_related(
            'items__producto'):
        signo = -1 if c.tipo_comprobante == '07' else 1
        for i in c.items.all():
            clave = i.producto_id or i.descripcion.strip().lower()
            cantidad = i.producto.a_stock(i.cantidad) if i.producto_id and i.producto.es_inventariable else i.cantidad
            facturado[clave] += signo * cantidad
    return facturado


def avance(oc):
    """Filas por línea: pedido, recibido, pendiente de recibir, facturado y pendiente de facturar (en la unidad de la
    orden). Los servicios no se reciben en el almacén: su avance es lo facturado (o la HES)."""
    recibido, facturado = _recibido(oc), _facturado(oc)
    filas, usado_r, usado_f = [], defaultdict(lambda: D0), defaultdict(lambda: D0)
    for i in oc.items.select_related('producto').order_by('id'):
        bien = bool(i.producto_id and i.producto.es_inventariable)
        factor = i.producto.factor if bien and i.producto.factor else Decimal('1')
        pedido = i.cantidad
        clave = i.producto_id or i.descripcion.strip().lower()
        # varias líneas del mismo producto: se reparten en orden
        disponible_r = (recibido.get(i.producto_id, D0) / factor if bien else None)
        disponible_f = facturado.get(clave, D0) / (factor if bien else 1)
        if disponible_r is not None:
            r = max(min(disponible_r - usado_r[clave], pedido), D0)
            usado_r[clave] += r
        else:
            r = None
        f = max(min(disponible_f - usado_f[clave], pedido), D0)
        usado_f[clave] += f
        filas.append({'item': i, 'bien': bien, 'pedido': pedido, 'recibido': r,
                      'por_recibir': (pedido - r) if r is not None else None, 'facturado': f,
                      'por_facturar': pedido - f})
    return filas


def situacion(oc, filas=None):
    """Texto de la situación de entrega y facturación (además del estado de la orden)."""
    if oc.estado in ('PENDIENTE', 'ANULADO'):
        return oc.get_estado_display()
    if oc.saldo_cerrado_en:
        return 'Cerrada (saldo cancelado)'
    filas = avance(oc) if filas is None else filas
    bienes = [f for f in filas if f['bien']]
    recibido = sum((f['recibido'] for f in bienes), D0)
    pedido_bienes = sum((f['pedido'] for f in bienes), D0)
    facturado = sum((f['facturado'] for f in filas), D0)
    pedido = sum((f['pedido'] for f in filas), D0)
    if pedido and facturado >= pedido:
        return 'Facturada'
    if facturado > 0:
        return 'Facturada parcial' + (' · recibida' if bienes and recibido >= pedido_bienes else
                                      ' · recibida parcial' if recibido else '')
    if bienes and recibido >= pedido_bienes and pedido_bienes:
        return 'Recibida'
    if recibido > 0:
        return 'Recibida parcial'
    return 'Aprobada · por recibir'


def tiene_saldo(oc, filas=None):
    filas = avance(oc) if filas is None else filas
    return oc.estado in ('APROBADO', 'ATENDIDO') and not oc.saldo_cerrado_en and any(
        f['por_facturar'] > 0 or (f['por_recibir'] or 0) > 0 for f in filas)


def entregas(oc):
    """Recepciones (NI) de la orden con fecha, guía, cantidades, lotes, quién la registró, acumulado y saldo."""
    from inventario.models import Operacion
    ops = list(Operacion.objects.filter(orden_compra=oc).exclude(estado='BORRADOR').select_related(
        'tipo', 'creado_por', 'confirmado_por').prefetch_related('items__producto').order_by('fecha', 'id'))
    pedido = defaultdict(lambda: D0)
    for i in oc.items.select_related('producto'):
        if i.producto_id and i.producto.es_inventariable:
            pedido[i.producto_id] += i.producto.a_stock(i.cantidad)
    acumulado, salida = defaultdict(lambda: D0), []
    for op in ops:
        vigente = op.estado == 'CONFIRMADO'
        for it in op.items.all():
            if vigente:
                acumulado[it.producto_id] += it.cantidad
            salida.append({'operacion': op, 'producto': it.producto, 'cantidad': it.cantidad, 'lote': it.lote,
                           'vence': it.vencimiento, 'referencia': op.referencia,
                           'acumulado': acumulado[it.producto_id] if vigente else None,
                           'saldo': pedido.get(it.producto_id, D0) - acumulado[it.producto_id] if vigente else None,
                           'por': op.confirmado_por or op.creado_por})
    return salida


def _historial(oc, accion, descripcion, cambios=None):
    from core.auditoria import registrar
    registrar(accion, oc, cambios or {}, descripcion[:250])


def actualizar_estado(oc, motivo='Factura registrada'):
    """COM-23: «Atendido / Facturado» solo con todo facturado (o el saldo cerrado); si no, vuelve o sigue
    «Aprobado». El cambio automático queda en el historial."""
    from .models import OrdenCompra
    oc.refresh_from_db()
    if oc.estado not in ('APROBADO', 'ATENDIDO'):
        return oc.estado
    filas = avance(oc)
    completa = bool(filas) and all(f['por_facturar'] <= 0 for f in filas)
    nuevo = 'ATENDIDO' if completa or oc.saldo_cerrado_en else 'APROBADO'
    if nuevo != oc.estado:
        anterior = oc.get_estado_display()
        OrdenCompra.objects.filter(pk=oc.pk).update(estado=nuevo)
        oc.estado = nuevo
        _historial(oc, 'MODIFICAR', f'Estado automático: {anterior} → {oc.get_estado_display()} ({motivo}; '
                                 f'{situacion(oc, filas)}).', {'estado': [anterior, oc.get_estado_display()]})
    return nuevo


def errores_anulacion(oc):
    """COM-22: no se anula una orden con recepciones confirmadas o facturas vigentes."""
    from inventario.models import Operacion
    errores = []
    recepciones = list(Operacion.objects.filter(orden_compra=oc, estado='CONFIRMADO').values_list('numero',
                                                                                                    flat=True)[:6])
    if recepciones:
        errores.append(f'tiene recepciones confirmadas ({", ".join(recepciones)})')
    facturas = [str(c) for c in oc.compras.filter(estado='REGISTRADO')[:6]]
    if facturas:
        errores.append(f'tiene facturas vigentes ({", ".join(facturas)})')
    return errores


@transaction.atomic
def anular(oc, usuario, motivo):
    if oc.estado == 'ANULADO':
        raise ErrorSeguimiento('La orden ya está anulada.')
    if len((motivo or '').strip()) < 5:
        raise ErrorSeguimiento('Indique el motivo de la anulación (mínimo 5 caracteres).')
    errores = errores_anulacion(oc)
    if errores:
        raise ErrorSeguimiento(f'La orden {oc.numero} no se anula: {"; ".join(errores)}. Si el proveedor no '
                               f'entregará el resto, use «Cerrar saldo»; si hay que deshacerla, anule antes sus '
                               f'recepciones y facturas.')
    anterior = oc.get_estado_display()
    oc.estado = 'ANULADO'
    oc.save(update_fields=['estado'])
    _historial(oc, 'ANULAR', f'Orden anulada: {motivo.strip()}', {'estado': [anterior, 'Anulado'],
                                                                   'motivo': motivo.strip()[:250]})
    from .models import SolicitudPedido
    from .solped import actualizar_estados
    actualizar_estados(SolicitudPedido.objects.filter(lineas__items_oc__documento=oc).distinct())
    return oc


@transaction.atomic
def cerrar_saldo(oc, usuario, motivo, detalle=''):
    """COM-24: el saldo que no se recibirá deja de estar pendiente (y libera el compromiso del presupuesto)."""
    if oc.estado not in ('APROBADO', 'ATENDIDO'):
        raise ErrorSeguimiento(f'{oc.numero}: solo se cierra el saldo de una orden aprobada.')
    if oc.saldo_cerrado_en:
        raise ErrorSeguimiento(f'{oc.numero}: su saldo ya se cerró.')
    if motivo not in dict(MOTIVOS_CIERRE):
        raise ErrorSeguimiento('Elija el motivo del cierre del saldo.')
    if motivo == 'OTRO' and len((detalle or '').strip()) < 5:
        raise ErrorSeguimiento('Describa el motivo del cierre.')
    filas = avance(oc)
    if not any(f['por_facturar'] > 0 or (f['por_recibir'] or 0) > 0 for f in filas):
        raise ErrorSeguimiento(f'{oc.numero} no tiene saldo pendiente.')
    texto = dict(MOTIVOS_CIERRE)[motivo] + (f': {detalle.strip()}' if detalle and detalle.strip() else '')
    pendiente = '; '.join(f'{f["item"].descripcion[:40]} {f["por_recibir"] if f["por_recibir"] is not None else f["por_facturar"]:,.2f}'
                          for f in filas if f['por_facturar'] > 0)
    oc.saldo_cerrado_en, oc.saldo_cerrado_por, oc.motivo_cierre = timezone.now(), usuario, texto[:250]
    anterior = oc.get_estado_display()
    oc.estado = 'ATENDIDO'
    oc.save(update_fields=['saldo_cerrado_en', 'saldo_cerrado_por', 'motivo_cierre', 'estado'])
    _historial(oc, 'MODIFICAR', f'Saldo cerrado ({texto}). Pendiente que ya no se recibirá: {pendiente}.',
               {'estado': [anterior, 'Atendido / Facturado (saldo cerrado)'], 'motivo': texto[:250]})
    return oc


def ordenes_con_saldo(qs=None, vencidas=False, proveedor=None, comprador=None):
    """Reporte de seguimiento: órdenes aprobadas con saldo por recibir o facturar."""
    from .models import OrdenCompra
    qs = (qs if qs is not None else OrdenCompra.objects.all()).filter(
        estado__in=('APROBADO', 'ATENDIDO'), saldo_cerrado_en__isnull=True).select_related('tercero', 'comprador')
    if proveedor:
        qs = qs.filter(tercero_id=proveedor)
    if comprador:
        qs = qs.filter(comprador_id=comprador)
    hoy = timezone.localdate()
    salida = []
    for oc in qs.order_by('fecha_entrega', 'fecha', 'id')[:500]:
        filas = avance(oc)
        if not tiene_saldo(oc, filas):
            continue
        vencida = bool(oc.fecha_entrega and oc.fecha_entrega < hoy)
        if vencidas and not vencida:
            continue
        salida.append({'oc': oc, 'filas': filas, 'situacion': situacion(oc, filas), 'vencida': vencida,
                       'por_recibir': sum((f['por_recibir'] or D0 for f in filas), D0),
                       'por_facturar': sum((f['por_facturar'] for f in filas), D0),
                       'pedido': sum((f['pedido'] for f in filas), D0)})
    return salida
