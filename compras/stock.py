"""Revisar el inventario antes de pedir y de comprar (COM-13).

Disponible de un almacén = stock − reservado (requerimientos aprobados por atender y traslados por aprobar que salen de
él) − lo que la meta de la obra dueña del almacén aún va a consumir (parámetro STOCK_RESERVA_META). El material de un
almacén de obra pública que recibió adelanto para materiales está restringido (no se ofrece). Primero se ofrecen los
sobrantes de obras en recepción.

Si se compra habiendo disponible en otros almacenes, el parámetro STOCK_CONTROL_COMPRA decide: 0 no controla, 1 pide
la justificación (urgencia, distancia, especificación…) que queda en la línea, 2 bloquea la compra (se atiende por
traslado). El traslado lo aprueba el responsable del almacén de origen, sale a tránsito al costo promedio y quien
recibe lo acepta (ingresa a su almacén) o lo rechaza (vuelve al origen); no es gasto hasta que se consume."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

D0 = Decimal('0')
ESTADOS_SOBRANTE = ('RECEPCION',)
ESTADOS_RESERVA_META = ('ADJUDICADA', 'EJECUCION', 'PARALIZADA')


class ErrorStock(Exception):
    pass


def _parametro(clave):
    from core import parametros
    return int(parametros.valor(clave))


def control_compra():
    """0 = no controlar, 1 = pedir justificación, 2 = bloquear."""
    return _parametro('STOCK_CONTROL_COMPRA')


def _obras_por_almacen():
    from obras.models import Obra
    return {o.almacen_id: o for o in Obra.objects.exclude(almacen=None).select_related('centro_costo')}


def reservado(productos):
    """{(producto, almacén): cantidad} reservada: requerimientos aprobados por atender y traslados por aprobar."""
    from inventario.models import RequerimientoInterno, SolicitudTraslado
    salida = defaultdict(lambda: D0)
    for req in RequerimientoInterno.objects.filter(estado__in=('APROBADO', 'PARCIAL')).prefetch_related('items'):
        for i in req.items.all():
            if i.producto_id in productos and i.pendiente > 0:
                salida[(i.producto_id, req.almacen_id)] += i.pendiente
    for pid, alm, c in SolicitudTraslado.objects.filter(estado='POR_APROBAR', producto_id__in=productos).values_list(
            'producto_id', 'almacen_origen_id', 'cantidad'):
        salida[(pid, alm)] += c
    return salida


def por_consumir_meta(productos, obras):
    """{(producto, almacén de obra): cantidad} que la meta de cada obra en ejecución aún va a consumir."""
    if not _parametro('STOCK_RESERVA_META'):
        return {}
    from core.models import Kardex
    from obras import control
    from obras.valorizaciones import presupuesto_meta
    salida = {}
    for alm, obra in obras.items():
        if obra.estado not in ESTADOS_RESERVA_META:
            continue
        meta = presupuesto_meta(obra)
        if meta is None:
            continue
        filas, _ = control.meta(meta)
        consumido = dict(Kardex.objects.filter(centro_costo=obra.centro_costo, tipo='SALIDA',
                                               producto_id__in=productos).values('producto').annotate(
            c=Sum('cantidad')).values_list('producto', 'c'))
        for pid, f in filas.items():
            if pid in productos:
                salida[(pid, alm)] = max(f['cantidad'] - (consumido.get(pid) or D0), D0)
    return salida


def restringidos(obras):
    """Almacenes de obras públicas que recibieron adelanto para materiales (su material no se traslada)."""
    from obras.models import AdelantoObra
    con_adelanto = set(AdelantoObra.objects.filter(tipo='MATERIALES', factura__estado='REGISTRADO').values_list(
        'obra_id', flat=True))
    return {alm for alm, o in obras.items() if o.tipo == 'PUBLICA' and o.pk in con_adelanto}


def disponibilidad(productos, excluir_almacen=None):
    """{producto_id: [fila]} con el stock por almacén (con stock), lo reservado, lo que la meta de su obra aún
    consume, lo disponible y si es sobrante o restringido. Los sobrantes van primero."""
    from core.models import StockAlmacen
    productos = set(p for p in productos if p)
    if not productos:
        return {}
    obras = _obras_por_almacen()
    reserv, meta, restr = reservado(productos), por_consumir_meta(productos, obras), restringidos(obras)
    salida = defaultdict(list)
    for s in StockAlmacen.objects.filter(producto_id__in=productos, cantidad__gt=0, almacen__activo=True,
                                         almacen__cerrado=False).exclude(almacen__uso__in=('TRANSITO', 'DESTRUCCION',
                                                                                          'TERCEROS')).select_related(
            'almacen'):
        if excluir_almacen and s.almacen_id == excluir_almacen:
            continue
        obra = obras.get(s.almacen_id)
        clave = (s.producto_id, s.almacen_id)
        restringido = s.almacen_id in restr
        disponible = max(s.cantidad - reserv.get(clave, D0) - meta.get(clave, D0), D0)
        salida[s.producto_id].append({
            'almacen': s.almacen, 'obra': obra, 'stock': s.cantidad, 'reservado': reserv.get(clave, D0),
            'meta': meta.get(clave, D0), 'disponible': D0 if restringido else disponible,
            'restringido': restringido, 'sobrante': bool(obra and obra.estado in ESTADOS_SOBRANTE)})
    for filas in salida.values():
        filas.sort(key=lambda f: (not f['sobrante'], -f['disponible']))
    return salida


def total_disponible(filas):
    return sum((f['disponible'] for f in filas), D0)


def almacen_de_centro(centro_costo_id):
    """El almacén de la obra del centro de costo (o el principal)."""
    from core.models import Almacen
    from obras.models import Obra
    obra = Obra.de_centro(centro_costo_id)
    if obra is not None and obra.almacen_id:
        return obra.almacen
    return Almacen.principal()


def validar_compra(lineas, justificaciones):
    """Antes de crear órdenes de compra desde la bandeja: líneas con disponible en otros almacenes exigen la
    justificación {línea_id: (motivo, texto)} o se bloquean según el parámetro. Graba la justificación."""
    nivel = control_compra()
    if not nivel:
        return
    disp = disponibilidad({l.producto_id for l in lineas if l.producto_id})
    faltan, bloqueadas = [], []
    for l in lineas:
        if not l.producto_id:
            continue
        propio = almacen_de_centro(l.solicitud.centro_costo_id)
        otros = total_disponible([f for f in disp.get(l.producto_id, []) if f['almacen'].pk != propio.pk])
        if otros <= 0:
            continue
        if nivel >= 2:
            bloqueadas.append(f'{l.descripcion} ({otros:,.2f} disponibles)')
            continue
        motivo, texto = justificaciones.get(l.pk, ('', ''))
        if not motivo or (motivo == 'OTRO' and len((texto or '').strip()) < 5):
            faltan.append(f'{l.descripcion} ({otros:,.2f} disponibles en otros almacenes)')
            continue
        l.motivo_stock, l.justificacion_stock, l.disponible_al_pedir = motivo, (texto or '').strip()[:250], otros
        l.save(update_fields=['motivo_stock', 'justificacion_stock', 'disponible_al_pedir'])
    if bloqueadas:
        raise ErrorStock('Hay stock disponible en otros almacenes: atiéndalas por traslado (la empresa no permite '
                         'comprar en ese caso): ' + '; '.join(bloqueadas[:5]))
    if faltan:
        raise ErrorStock('Hay stock disponible en otros almacenes: elija el motivo para comprar de todos modos o '
                         'atiéndalas por traslado: ' + '; '.join(faltan[:5]))


# ---------------------------------------------------------------- traslados
@transaction.atomic
def solicitar_traslado(producto, cantidad, origen, destino, usuario, linea=None, centro_costo=None, motivo=''):
    from inventario.models import SolicitudTraslado
    cantidad = Decimal(cantidad or 0)
    if cantidad <= 0:
        raise ErrorStock('Indique la cantidad a trasladar.')
    if origen is None or destino is None or origen.pk == destino.pk:
        raise ErrorStock('Elija almacenes de origen y de destino distintos.')
    disp = {f['almacen'].pk: f for f in disponibilidad({producto.pk}).get(producto.pk, [])}
    fila = disp.get(origen.pk)
    if fila is None or fila['disponible'] < cantidad:
        raise ErrorStock(f'{origen} solo tiene {fila["disponible"] if fila else D0:,.2f} disponibles de '
                         f'{producto.nombre}.')
    if linea is not None and cantidad > linea.pendiente:
        raise ErrorStock(f'La línea solo tiene {linea.pendiente:,.2f} por atender.')
    return SolicitudTraslado.objects.create(
        producto=producto, cantidad=cantidad, almacen_origen=origen, almacen_destino=destino,
        centro_costo=centro_costo, linea_solped=linea, motivo=(motivo or '')[:250],
        solicitado_por=usuario if usuario and usuario.is_authenticated else None)


def puede_aprobar(user, traslado):
    """El responsable del almacén de origen (el residente de su obra) o un superusuario."""
    if user.is_superuser:
        return True
    alm = traslado.almacen_origen
    if alm.responsables.filter(pk=user.pk).exists():
        return True
    obra = _obras_por_almacen().get(alm.pk)
    return bool(obra and obra.residente_id == user.pk)


def puede_recibir(user, traslado):
    if user.is_superuser:
        return True
    alm = traslado.almacen_destino
    if alm.responsables.filter(pk=user.pk).exists():
        return True
    obra = _obras_por_almacen().get(alm.pk)
    return bool(obra and obra.residente_id == user.pk) or traslado.solicitado_por_id == user.pk


def _tipo(codigo):
    from inventario.models import TipoOperacion
    t = TipoOperacion.objects.filter(codigo=codigo, activo=True).first()
    if t is None:
        raise ErrorStock(f'Falta el tipo de operación {codigo} (Inventario › Configuración).')
    return t


@transaction.atomic
def aprobar_traslado(t, usuario):
    """El origen aprueba: sale a tránsito (traslado a tránsito confirmado, al costo promedio)."""
    from inventario import servicios
    from inventario.models import Operacion, OperacionItem
    if t.estado != 'POR_APROBAR':
        raise ErrorStock('El traslado ya fue atendido.')
    if not puede_aprobar(usuario, t):
        raise ErrorStock('Lo aprueba el responsable del almacén de origen o el residente de su obra.')
    op = Operacion.objects.create(tipo=_tipo('TRAS_TRANS'), almacen_origen=t.almacen_origen,
                                  almacen_destino=t.almacen_destino, centro_costo=t.centro_costo,
                                  referencia=t.numero, glosa=f'{t}: {t.motivo}'[:500], creado_por=usuario)
    OperacionItem.objects.create(operacion=op, producto=t.producto, cantidad=t.cantidad)
    try:
        servicios.confirmar(op, usuario)
    except servicios.ErrorOperacion as exc:
        raise ErrorStock(str(exc))
    t.estado, t.envio, t.aprobado_por, t.aprobado_en = 'EN_TRANSITO', op, usuario, timezone.now()
    t.save(update_fields=['estado', 'envio', 'aprobado_por', 'aprobado_en'])
    return t


def rechazar_traslado(t, usuario, motivo):
    if t.estado != 'POR_APROBAR':
        raise ErrorStock('El traslado ya fue atendido.')
    if not puede_aprobar(usuario, t):
        raise ErrorStock('Lo rechaza el responsable del almacén de origen o el residente de su obra.')
    if len((motivo or '').strip()) < 5:
        raise ErrorStock('Indique el motivo del rechazo.')
    t.estado, t.comentario, t.aprobado_por, t.aprobado_en = 'RECHAZADA', motivo.strip()[:250], usuario, timezone.now()
    t.save(update_fields=['estado', 'comentario', 'aprobado_por', 'aprobado_en'])
    return t


@transaction.atomic
def recibir_traslado(t, usuario, aceptar=True, motivo=''):
    """Quien recibe acepta (ingresa a su almacén) o rechaza (vuelve al almacén de origen)."""
    from inventario import servicios
    from inventario.models import Operacion, OperacionItem
    if t.estado != 'EN_TRANSITO':
        raise ErrorStock('El traslado no está en tránsito.')
    if not puede_recibir(usuario, t):
        raise ErrorStock('Lo recibe el responsable del almacén de destino o el residente de su obra.')
    if not aceptar and len((motivo or '').strip()) < 5:
        raise ErrorStock('Indique por qué se rechaza lo recibido.')
    destino = t.almacen_destino if aceptar else t.almacen_origen
    op = Operacion.objects.create(tipo=_tipo('REC_TRANS'), envio=t.envio, almacen_destino=destino,
                                  centro_costo=t.centro_costo, referencia=t.numero,
                                  glosa=(f'{t}: recibido' if aceptar else f'{t}: rechazado, vuelve al origen. {motivo}'
                                         )[:500], creado_por=usuario)
    OperacionItem.objects.create(operacion=op, producto=t.producto, cantidad=t.cantidad)
    try:
        servicios.confirmar(op, usuario)
    except servicios.ErrorOperacion as exc:
        raise ErrorStock(str(exc))
    t.recepcion, t.recibido_por = op, usuario
    t.estado = 'RECIBIDA' if aceptar else 'DEVUELTA'
    if not aceptar:
        t.comentario = motivo.strip()[:250]
    t.save(update_fields=['recepcion', 'recibido_por', 'estado', 'comentario'])
    if aceptar and t.linea_solped_id:
        _atender_linea(t)
    return t


def _atender_linea(t):
    """Lo recibido por traslado ya no se compra: la línea de la solicitud baja su cantidad (y se cierra si llega a 0)."""
    from compras.solped import actualizar_estados
    l = t.linea_solped
    l.cantidad = max(l.cantidad - t.cantidad, D0)
    if l.cantidad <= l.pedido:
        l.cerrada = True
    l.save(update_fields=['cantidad', 'cerrada'])
    actualizar_estados({l.solicitud})


def traslados_pendientes(user):
    """Por aprobar (para el responsable del origen) y por recibir (para el destino)."""
    from inventario.models import SolicitudTraslado
    qs = SolicitudTraslado.objects.filter(estado__in=('POR_APROBAR', 'EN_TRANSITO')).select_related(
        'producto', 'almacen_origen', 'almacen_destino')
    return [t for t in qs if (t.estado == 'POR_APROBAR' and puede_aprobar(user, t)) or
            (t.estado == 'EN_TRANSITO' and puede_recibir(user, t))]
