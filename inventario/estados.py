"""Estado del stock y política de lotes (v1.84: INV-25 a INV-30).

Estados del stock (como los tipos de stock de SAP):
- Libre: lote libre o liberado por Calidad en un almacén normal. Es lo único que entra al disponible, al MRP, a las
  reservas y a las salidas automáticas.
- En calidad: lote en cuarentena (inspección pendiente) o stock en un almacén «En control de calidad».
- Bloqueado: lote bloqueado (manual, con motivo) o rechazado, o stock en un almacén «Stock bloqueado» o de
  cuarentena / por destruir.
Un producto sin lote se pone en calidad o se bloquea trasladándolo a esos almacenes (Inventario › Estado del stock).

Un lote que no está libre (o está vencido) no sale por ninguna operación —venta, consumo, traslado, manufactura,
requerimiento, ajuste— salvo un traslado a un almacén de calidad, bloqueo o cuarentena, la destrucción, la devolución
al proveedor o el ajuste de un inventario físico. Las anulaciones (que devuelven el movimiento) no se restringen.

Regla de salida (sin lote indicado): FEFO (vence primero), FIFO (ingresó primero) o manual (hay que indicarlo), por
producto o la de la empresa; solo toma lotes libres, no vencidos y, en ventas, con la vida remanente mínima."""
import calendar
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .cierre import KardexCerrado

D0 = Decimal('0')
# almacenes cuyo stock no está libre
USOS_CALIDAD = ('CALIDAD',)
USOS_BLOQUEO = ('BLOQUEO', 'DESTRUCCION')
USOS_NO_LIBRE = USOS_CALIDAD + USOS_BLOQUEO
REGLAS = [('FEFO', 'FEFO: sale primero lo que vence primero'), ('FIFO', 'FIFO: sale primero lo que ingresó primero'),
          ('MANUAL', 'Manual: se indica el lote en cada salida')]


class ErrorLote(KardexCerrado):
    """Movimiento de lote no permitido (se muestra como aviso, igual que un kardex cerrado)."""


def estado_de_lote(lote):
    """LIBRE, CALIDAD o BLOQUEADO."""
    if lote.estado_calidad == 'CUARENTENA':
        return 'CALIDAD'
    if lote.estado_calidad in ('RECHAZADO', 'BLOQUEADO'):
        return 'BLOQUEADO'
    return 'LIBRE'


def regla_de(producto):
    if producto.regla_salida:
        return producto.regla_salida
    from core.models import Empresa
    return getattr(Empresa.actual(), 'regla_salida', '') or 'FEFO'


def fin_de_mes(fecha):
    return fecha.replace(day=calendar.monthrange(fecha.year, fecha.month)[1])


def vencimiento_propuesto(producto, fabricacion):
    """Vencimiento = fabricación + vida útil (a fin de mes si el producto vence por mes)."""
    if not (fabricacion and producto.vida_util_dias):
        return None
    vence = fabricacion + timedelta(days=producto.vida_util_dias)
    return fin_de_mes(vence) if producto.vencimiento_mes else vence


def dias_de_vida(lote, hoy=None):
    if not lote.vencimiento:
        return None
    return (lote.vencimiento - (hoy or timezone.localdate())).days


def salida_permitida(lote_o_almacen_destino, categoria, concepto='', origen='', destino=None):
    """¿Puede salir stock no libre (o vencido) en este movimiento?"""
    if categoria == 'DESTRUCCION':
        return True
    if categoria == 'DEVOLUCIONES' and concepto == 'DEV_PROV':
        return True
    if origen == 'INVENTARIO':  # ajuste de un inventario físico
        return True
    if categoria == 'TRASLADOS' and destino is not None and destino.uso in USOS_NO_LIBRE:
        return True
    return False


def validar_ingreso(producto, lote, creado, vence, fabricacion, categoria, hoy=None, inicial=False):
    """INV-28: vencimiento obligatorio, sin lotes vencidos ni con menos vida que la mínima, y sin cambiar el
    vencimiento de un lote existente. Devuelve el vencimiento que queda. El saldo inicial o migrado del sistema
    anterior (inicial=True) entra como está: sin exigir vencimiento ni vida mínima (un lote vencido igual no sale)."""
    hoy = hoy or timezone.localdate()
    if inicial:
        if vence and producto.vencimiento_mes:
            vence = fin_de_mes(vence)
        return vence or lote.vencimiento
    if vence and producto.vencimiento_mes:
        vence = fin_de_mes(vence)
    if not vence and fabricacion:
        vence = vencimiento_propuesto(producto, fabricacion)
    if not creado and lote.vencimiento and vence and vence != lote.vencimiento:
        raise ErrorLote(f'El lote {lote.codigo} de {producto.nombre} ya existe con vencimiento '
                        f'{lote.vencimiento:%d/%m/%Y}: no se registra otro ({vence:%d/%m/%Y}). Use otro código de lote '
                        f'o corrija el vencimiento del lote en Inventario › Lotes.')
    vence = vence or lote.vencimiento
    if producto.control == 'LOTE' and producto.exige_vencimiento and not vence and lote.codigo != 'S/L':
        raise ErrorLote(f'{producto.nombre} se controla por lote con vencimiento: indique el vencimiento del lote '
                        f'{lote.codigo} (o la fecha de fabricación, si el producto tiene vida útil).')
    devolucion = categoria == 'DEVOLUCIONES'
    if vence and vence < hoy and not devolucion:
        raise ErrorLote(f'El lote {lote.codigo} de {producto.nombre} venció el {vence:%d/%m/%Y}: no se ingresa '
                        f'(solo una devolución, que se lleva a cuarentena o destrucción).')
    if vence and producto.vida_remanente_recepcion and not devolucion and \
            (vence - hoy).days < producto.vida_remanente_recepcion:
        raise ErrorLote(f'El lote {lote.codigo} de {producto.nombre} vence el {vence:%d/%m/%Y}: le quedan '
                        f'{(vence - hoy).days} días y el mínimo para recibirlo es {producto.vida_remanente_recepcion}.')
    return vence


def validar_salida_lote(producto, lote, categoria, concepto='', origen='', destino=None, hoy=None):
    """INV-25/INV-27: un lote no libre o vencido no sale (salvo las excepciones); en ventas, con la vida
    remanente mínima de despacho."""
    hoy = hoy or timezone.localdate()
    excepcion = salida_permitida(lote, categoria, concepto, origen, destino)
    if lote.bloqueado and not excepcion:
        raise ErrorLote(f'El lote {lote.codigo} de {producto.nombre} está {lote.get_estado_calidad_display().lower()}: '
                        f'no sale hasta que Calidad lo libere (solo se traslada a un almacén de calidad o '
                        f'cuarentena, se destruye o se devuelve al proveedor).')
    if lote.vencimiento and lote.vencimiento < hoy and not excepcion:
        raise ErrorLote(f'El lote {lote.codigo} de {producto.nombre} venció el {lote.vencimiento:%d/%m/%Y}: solo sale '
                        f'por destrucción o devolución al proveedor.')
    if categoria == 'VENTAS' and producto.vida_remanente_despacho and lote.vencimiento and \
            (lote.vencimiento - hoy).days < producto.vida_remanente_despacho:
        raise ErrorLote(f'Al lote {lote.codigo} de {producto.nombre} le quedan {(lote.vencimiento - hoy).days} días de '
                        f'vida; el mínimo para despachar es {producto.vida_remanente_despacho}.')


def orden_salida(qs, regla):
    from django.db.models import F
    if regla == 'FIFO':
        return qs.order_by('lote__creado', 'lote__codigo')
    return qs.order_by(F('lote__vencimiento').asc(nulls_last=True), 'lote__creado', 'lote__codigo')


def utilizable(producto, lote, categoria, hoy=None):
    """¿La salida automática puede tomar este lote? Solo libre, no vencido y, en ventas, con vida suficiente."""
    hoy = hoy or timezone.localdate()
    if lote.bloqueado or (lote.vencimiento and lote.vencimiento < hoy):
        return False
    if categoria == 'VENTAS' and producto.vida_remanente_despacho and lote.vencimiento and \
            (lote.vencimiento - hoy).days < producto.vida_remanente_despacho:
        return False
    return True


def validar_almacen_origen(producto, almacen, categoria, concepto='', origen='', destino=None):
    """Lo que está en un almacén de calidad o bloqueo solo sale trasladándolo (la liberación) o por destrucción o
    devolución."""
    if almacen.uso in USOS_NO_LIBRE and categoria not in ('TRASLADOS', 'DESTRUCCION', 'DEVOLUCIONES') and \
            origen != 'INVENTARIO':
        raise ErrorLote(f'{producto.nombre} en {almacen} no está libre ({almacen.get_uso_display().lower()}): '
                        f'trasládelo a un almacén normal (liberación) antes de usarlo.')


# ---------------------------------------------------------------- cambio de estado (INV-26)
ESTADOS_DESTINO = [('LIBRE', 'Libre utilización'), ('CUARENTENA', 'En control de calidad'), ('BLOQUEADO', 'Bloqueado')]


def puede_cambiar(user, lote, nuevo):
    """Bloquear o poner en calidad: «estado_stock» de Inventario. Liberar un lote en cuarentena o rechazado lo hace
    Calidad («liberar_lotes»); desbloquear uno bloqueado a mano, Inventario."""
    from core.permisos import puede
    if user is None:
        return True
    if nuevo == 'LIBRE' and lote.estado_calidad in ('CUARENTENA', 'RECHAZADO'):
        return puede(user, 'control_calidad.liberar_lotes')
    return puede(user, 'inventario.estado_stock')


def cambiar_estado_lote(lote, nuevo, motivo, usuario=None):
    """Movimiento de cambio de estado del lote (SAP 321/322, 343/344, 349/350) con motivo, usuario y fecha."""
    from .models import CambioEstadoStock
    if nuevo not in dict(ESTADOS_DESTINO):
        raise ErrorLote('Elija el estado.')
    if len((motivo or '').strip()) < 5:
        raise ErrorLote('Indique el motivo del cambio de estado.')
    if not puede_cambiar(usuario, lote, nuevo):
        raise ErrorLote('Su usuario no cambia este estado (bloquear: «estado del stock» de Inventario; liberar un lote '
                        'en cuarentena o rechazado: «liberar lotes» de Calidad).')
    anterior = lote.estado_calidad
    destino = 'LIBERADO' if nuevo == 'LIBRE' and anterior in ('CUARENTENA', 'RECHAZADO') else nuevo
    if destino == anterior or (destino == 'LIBRE' and anterior in ('LIBRE', 'LIBERADO')):
        raise ErrorLote(f'El lote {lote.codigo} ya está {lote.get_estado_calidad_display().lower()}.')
    lote.estado_calidad, lote.motivo_estado = destino, motivo.strip()[:250]
    lote.save(update_fields=['estado_calidad', 'motivo_estado'])
    return CambioEstadoStock.objects.create(
        lote=lote, producto=lote.producto, cantidad=lote.stock, estado_anterior=anterior, estado_nuevo=destino,
        motivo=motivo.strip()[:250], usuario=usuario if usuario and usuario.is_authenticated else None)


def trasladar_estado(producto, cantidad, origen, destino, motivo, usuario, lote_codigo=''):
    """Producto sin lote (o un lote completo): se pone en calidad, se bloquea o se libera trasladándolo entre un
    almacén normal y uno de calidad o bloqueo (genera y confirma el traslado)."""
    from . import servicios
    from .models import CambioEstadoStock, Operacion, OperacionItem, TipoOperacion
    if (origen.uso in USOS_NO_LIBRE) == (destino.uso in USOS_NO_LIBRE):
        raise ErrorLote('Elija un almacén normal y uno de calidad o bloqueo (el cambio de estado va de uno al otro).')
    if len((motivo or '').strip()) < 5:
        raise ErrorLote('Indique el motivo del cambio de estado.')
    tipo = TipoOperacion.objects.filter(codigo='TRAS_ALM').first()
    if tipo is None:
        raise ErrorLote('Falta el tipo de operación «Traslado entre almacenes» (TRAS_ALM).')
    op = Operacion.objects.create(tipo=tipo, almacen_origen=origen, almacen_destino=destino,
                                  referencia=f'Cambio de estado: {motivo.strip()}'[:60])
    OperacionItem.objects.create(operacion=op, producto=producto, cantidad=cantidad, lote=lote_codigo)
    try:
        servicios.confirmar(op, usuario)
    except servicios.ErrorOperacion as exc:
        op.delete()
        raise ErrorLote(str(exc)) from exc
    a_estado = 'CUARENTENA' if destino.uso in USOS_CALIDAD else 'BLOQUEADO' if destino.uso in USOS_BLOQUEO else 'LIBRE'
    de_estado = 'CUARENTENA' if origen.uso in USOS_CALIDAD else 'BLOQUEADO' if origen.uso in USOS_BLOQUEO else 'LIBRE'
    return CambioEstadoStock.objects.create(
        producto=producto, almacen=destino, cantidad=cantidad, estado_anterior=de_estado, estado_nuevo=a_estado,
        motivo=motivo.strip()[:250], operacion=op, usuario=usuario if usuario and usuario.is_authenticated else None)


# ---------------------------------------------------------------- stock por estado
def no_libre():
    """{(producto_id, almacen_id): (en_calidad, bloqueado)} sin contar lo vencido (ver vencido())."""
    from collections import defaultdict

    from core.models import StockAlmacen, StockLote
    res = defaultdict(lambda: [D0, D0])
    for s in StockAlmacen.objects.filter(almacen__uso__in=USOS_NO_LIBRE).exclude(cantidad=0):
        res[(s.producto_id, s.almacen_id)][0 if s.almacen.uso in USOS_CALIDAD else 1] += s.cantidad
    for sl in (StockLote.objects.filter(cantidad__gt=0, lote__estado_calidad__in=['CUARENTENA', 'RECHAZADO',
                                                                                  'BLOQUEADO'])
               .exclude(almacen__uso__in=USOS_NO_LIBRE).select_related('lote')):
        res[(sl.lote.producto_id, sl.almacen_id)][0 if sl.lote.estado_calidad == 'CUARENTENA' else 1] += sl.cantidad
    return res


def vencido(hoy=None):
    """{(producto_id, almacen_id): cantidad} de lotes vencidos libres en almacenes normales."""
    from collections import defaultdict

    from core.models import StockLote
    res = defaultdict(lambda: D0)
    for sl in (StockLote.objects.filter(cantidad__gt=0, lote__vencimiento__lt=hoy or timezone.localdate())
               .exclude(lote__estado_calidad__in=['CUARENTENA', 'RECHAZADO', 'BLOQUEADO'])
               .exclude(almacen__uso__in=USOS_NO_LIBRE).select_related('lote')):
        res[(sl.lote.producto_id, sl.almacen_id)] += sl.cantidad
    return res


def no_libre_por_producto():
    """{producto_id: cantidad no libre} (calidad, bloqueado y vencido) de todos los almacenes: el MRP y el
    requerimiento de materiales solo cuentan el stock libre."""
    from collections import defaultdict
    res = defaultdict(lambda: D0)
    for (pid, _), (cal, bloq) in no_libre().items():
        res[pid] += cal + bloq
    for (pid, _), cant in vencido().items():
        res[pid] += cant
    return res
