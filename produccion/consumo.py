"""Consumo de materiales de la orden de producción (MAN-14 y MAN-15).

- Política (empresa o receta): libre (se registra lo consumido), con tolerancia (fuera del ± % lo aprueba otro usuario
  con el permiso «Aprobar consumos», con motivo) o estricta (exactamente la receta para lo producido).
- «Agregar material» en una orden confirmada o en proceso (permiso «consumo_adicional», motivo obligatorio y, si
  sustituye un insumo, a cuál): con la política libre queda aprobado; con las otras lo aprueba otro usuario.
- Salida de almacén «Salida a orden de producción» (y su devolución): el material sale del almacén imputado a la orden
  mientras su mes esté abierto (kardex y contabilidad). En una orden abierta queda como consumo ya despachado; en una
  terminada dentro del mes abierto se recalcula su costo y se revaloriza el ingreso del producto terminado y las
  salidas posteriores del mismo producto en el kardex."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import ConsumoOrden

D0 = Decimal('0')
D4 = Decimal('0.0001')


class ErrorConsumo(Exception):
    pass


def politica(orden):
    """(modo, tolerancia %) de la receta de la orden o, si no tiene, de la empresa."""
    from core.models import Empresa
    e = Empresa.actual()
    lista = orden.lista
    modo = (lista.politica_consumo if lista and lista.politica_consumo else e.politica_consumo) or 'LIBRE'
    tol = lista.tolerancia_consumo_pct if lista and lista.tolerancia_consumo_pct is not None \
        else e.tolerancia_consumo_pct
    return modo, Decimal(tol or 0)


def teorico(consumo, cantidad_producida):
    """Lo que la receta pide del insumo para lo producido."""
    orden = consumo.orden
    if not orden.cantidad:
        return consumo.cantidad_plan
    return (consumo.cantidad_plan * Decimal(cantidad_producida) / orden.cantidad).quantize(D4)


def _otro_usuario(usuario):
    from core.segregacion import _otro_usuario as otro
    return otro(usuario)


@transaction.atomic
def agregar_material(orden, producto, cantidad, motivo, usuario, sustituye=None, almacen=None):
    if orden.estado not in orden.ABIERTAS:
        raise ErrorConsumo('Solo se agrega material a una orden confirmada o en proceso.')
    if not producto or not cantidad or Decimal(cantidad) <= 0:
        raise ErrorConsumo('Elija el material y una cantidad mayor a cero.')
    if len((motivo or '').strip()) < 5:
        raise ErrorConsumo('Indique el motivo (sustituto, reproceso, merma…).')
    if producto.pk == orden.producto_id:
        raise ErrorConsumo('El producto de la orden no puede ser su propio insumo.')
    if sustituye is not None and sustituye.orden_id != orden.pk:
        raise ErrorConsumo('El insumo sustituido es de otra orden.')
    modo, _ = politica(orden)
    libre = modo == 'LIBRE'
    return ConsumoOrden.objects.create(
        orden=orden, producto=producto, cantidad_plan=D0, cantidad_real=Decimal(cantidad) if libre else D0,
        almacen=almacen, adicional=True, sustituye=sustituye, motivo=motivo.strip()[:250],
        aprobacion='APROBADO' if libre else 'POR_APROBAR', solicitado=Decimal(cantidad),
        solicitado_por=usuario, aprobado_por=usuario if libre else None,
        aprobado_en=timezone.now() if libre else None, costo_unitario=producto.costo_promedio)


@transaction.atomic
def resolver(consumo, usuario, aprobar, comentario=''):
    """Aprueba o rechaza material agregado o un consumo fuera de la tolerancia (otro usuario que quien lo pidió)."""
    c = ConsumoOrden.objects.select_for_update().get(pk=consumo.pk)
    if c.aprobacion != 'POR_APROBAR':
        raise ErrorConsumo('No hay nada por aprobar en este insumo.')
    if c.solicitado_por_id == usuario.pk and _otro_usuario(usuario):
        raise ErrorConsumo('Quien pidió el consumo no lo aprueba: lo hace otro usuario (supervisor o jefe de '
                           'producción).')
    if c.orden.estado not in c.orden.ABIERTAS:
        raise ErrorConsumo('La orden ya no está abierta.')
    c.aprobacion = 'APROBADO' if aprobar else 'RECHAZADO'
    c.aprobado_por, c.aprobado_en = usuario, timezone.now()
    if aprobar:
        c.cantidad_real = c.solicitado
    elif comentario:
        c.motivo = f'{c.motivo} · Rechazado: {comentario}'[:250]
    c.save()
    return c


def revisar_terminar(orden, consumos, cantidad_producida, usuario, motivos=None):
    """Aplica la política antes de terminar: {consumo_id: cantidad} ajustado. Con la estricta, la receta; con la de
    tolerancia, lo que se aleja más del ± % queda por aprobar (con su motivo) y no deja terminar."""
    modo, tol = politica(orden)
    motivos = motivos or {}
    salida = dict(consumos)
    pendientes = []
    for c in orden.consumos.select_related('producto'):
        if c.adicional:
            if c.aprobacion == 'POR_APROBAR':
                pendientes.append(f'{c.producto.nombre} (material agregado, por aprobar)')
            salida[c.pk] = c.cantidad_real  # lo agregado solo cambia con su aprobación
            continue
        real = Decimal(consumos.get(c.pk, c.cantidad_real))
        esperado = teorico(c, cantidad_producida)
        if modo == 'ESTRICTO':
            salida[c.pk] = esperado
            continue
        if modo != 'TOLERANCIA' or real == esperado:
            continue
        limite = esperado * tol / 100
        if abs(real - esperado) <= limite:
            continue
        if c.aprobacion == 'APROBADO' and c.solicitado == real:
            continue  # ya aprobado por otro usuario
        motivo = (motivos.get(c.pk) or '').strip()
        if len(motivo) < 5:
            raise ErrorConsumo(f'{c.producto.nombre}: el consumo ({real:,.4f}) se aleja más del {tol:g} % de la receta '
                               f'({esperado:,.4f}): indique el motivo para pedir su aprobación.')
        ConsumoOrden.objects.filter(pk=c.pk).update(aprobacion='POR_APROBAR', solicitado=real, motivo=motivo[:250],
                                                    solicitado_por=usuario, aprobado_por=None, aprobado_en=None)
        pendientes.append(f'{c.producto.nombre} ({real:,.4f} frente a {esperado:,.4f})')
    if pendientes:
        raise ErrorConsumo('Requiere la aprobación de otro usuario antes de terminar: ' + '; '.join(pendientes) +
                           '. Lo aprueba quien tiene «Aprobar consumos» en la orden.')
    return salida


# ---------------------------------------------------------------- MAN-15: salidas de almacén a la orden
def error_imputacion(orden, fecha):
    """Mensaje si no se puede imputar material a la orden en esa fecha (orden y mes abiertos)."""
    from contabilidad.models import PeriodoContable
    from inventario.cierre import error_cierre
    if orden is None:
        return 'Elija la orden de producción.'
    if orden.estado not in ('CONFIRMADA', 'EN_PROCESO', 'PARCIAL', 'CERRADA_TEC', 'TERMINADA'):
        return f'La orden {orden.numero} no está confirmada, en proceso ni terminada.'
    if orden.estado == 'TERMINADA' and orden.liquidada_en:
        return (f'La orden {orden.numero} ya se liquidó: registre el material como costo tardío (a otra orden del '
                f'producto o a gasto).')
    if orden.estado == 'TERMINADA':
        if orden.es_historica or not orden.operacion_id or not orden.fecha_fin:
            return f'La orden {orden.numero} no se puede recostear.'
        if (fecha.year, fecha.month) != (orden.fecha_fin.year, orden.fecha_fin.month):
            return (f'La orden {orden.numero} se liquidó en {orden.fecha_fin:%m/%Y}: la salida debe ser de ese mes '
                    f'(la operación es del {fecha:%d/%m/%Y}).')
        if fecha < orden.fecha_fin:
            return f'La salida no puede ser anterior al término de la orden ({orden.fecha_fin:%d/%m/%Y}).'
    if error_cierre(fecha):
        return error_cierre(fecha)
    if PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
        return f'El periodo contable {fecha:%m/%Y} está cerrado.'
    return ''


@transaction.atomic
def imputar(op, usuario):
    """Después de confirmar la salida (o la devolución): suma (o resta) lo despachado al consumo de la orden y, si ya
    terminó, recalcula su costo y revaloriza el producto terminado."""
    orden = op.orden_imputada
    signo = -1 if op.tipo.clase == 'INGRESO' else 1
    agregado = D0
    for i in op.items.select_related('producto'):
        valor = (i.cantidad * (i.costo_unitario or i.producto.costo_promedio)).quantize(Decimal('0.01'))
        c = (ConsumoOrden.objects.filter(orden=orden, producto=i.producto).order_by('adicional', 'id').first())
        if c is None:
            if signo < 0:
                raise ErrorConsumo(f'{i.producto.nombre} no es insumo de la orden {orden.numero}: no se puede devolver.')
            c = ConsumoOrden.objects.create(
                orden=orden, producto=i.producto, cantidad_plan=D0, cantidad_real=D0, adicional=True,
                motivo=f'Salida de almacén {op.numero}'[:250], aprobacion='APROBADO', solicitado_por=usuario,
                aprobado_por=usuario, aprobado_en=timezone.now())
        c.despachado += signo * i.cantidad
        c.valor_despachado += signo * valor
        if c.despachado < 0:
            raise ErrorConsumo(f'{i.producto.nombre}: se devolvería más de lo entregado a la orden.')
        if orden.estado == 'TERMINADA':  # después de terminar, lo entregado es consumo adicional
            c.cantidad_real += signo * i.cantidad
        elif not c.adicional:
            # MAN-19: lo entregado cuenta para la receta (no se suma encima): se consume la receta o lo entregado si
            # fue más
            c.cantidad_real = max(c.cantidad_plan, c.despachado)
        else:
            c.cantidad_real = max(c.solicitado or D0, c.despachado) if c.aprobacion == 'APROBADO' else c.despachado
        if c.cantidad_real < 0:
            raise ErrorConsumo(f'{i.producto.nombre}: se devolvería más de lo consumido en la orden.')
        c.save()
        agregado += signo * valor
    if orden.estado == 'TERMINADA' and agregado:
        recostear(orden, agregado)
    return agregado


def recostear(orden, diferencia):
    """La orden terminada suma (o resta) material: su costo unitario cambia y el ingreso del producto terminado se
    revaloriza en el kardex, con las salidas posteriores del mismo producto (costo promedio)."""
    from core.models import Kardex
    from inventario.models import Operacion
    orden.costo_materiales += diferencia
    total = (orden.costo_materiales + orden.costo_mano_obra + orden.costo_maquina + orden.costo_cif
             + (orden.costo_servicio or D0) - (orden.merma_anormal or D0) - (orden.costo_subproductos or D0))
    nuevo = (max(total, D0) / orden.cantidad_producida).quantize(D4) if orden.cantidad_producida else D0
    orden.costo_unitario = nuevo
    orden.save(update_fields=['costo_materiales', 'costo_unitario'])
    op = Operacion.objects.get(pk=orden.operacion_id)
    item = op.items.filter(rol='PRODUCTO').first()
    if item is None:
        return
    item.costo_unitario = nuevo
    item.save(update_fields=['costo_unitario'])
    entrada = Kardex.objects.filter(producto=orden.producto, tipo='ENTRADA', referencia__contains=op.numero) \
        .order_by('id').first()
    if entrada is not None:
        revalorizar(entrada, nuevo)


def revalorizar(entrada, costo):
    """Recalcula el costo promedio del producto desde un ingreso con su nuevo costo: las salidas posteriores salen al
    nuevo promedio y el producto queda con el promedio final."""
    from core.models import Kardex, Producto
    p = Producto.objects.select_for_update().get(pk=entrada.producto_id)
    previo = Kardex.objects.filter(producto=p, id__lt=entrada.id).order_by('-id').first()
    saldo = (previo.saldo if previo else D0)
    promedio = (previo.costo_promedio if previo else D0)
    for k in Kardex.objects.filter(producto=p, id__gte=entrada.id).order_by('id'):
        if k.pk == entrada.pk:
            k.costo_unitario = costo
        if k.tipo == 'ENTRADA':
            nuevo_saldo = saldo + k.cantidad
            if nuevo_saldo > 0:
                promedio = ((saldo * promedio + k.cantidad * k.costo_unitario) / nuevo_saldo).quantize(D4)
            saldo = nuevo_saldo
        else:
            k.costo_unitario = promedio
            saldo -= k.cantidad
        k.costo_promedio = promedio
        k.save(update_fields=['costo_unitario', 'costo_promedio'])
    p.costo_promedio = promedio
    p.save(update_fields=['costo_promedio'])
