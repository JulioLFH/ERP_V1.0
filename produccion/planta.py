"""Calidad (inspecciones con plan de calidad y cuarentena) y mantenimiento de planta (equipos, planes preventivos y
órdenes de trabajo con consumo de repuestos)."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import Almacen, Serie

from .models import InspeccionCalidad, OrdenMantenimiento, ParametroCalidad, PlanMantenimiento, ResultadoCalidad


class ErrorPlanta(Exception):
    pass


def _numero(tipo, serie):
    s, n = Serie.siguiente(tipo, serie)
    return f'{s}-{n}'


# ---------------------------------------------------------------- calidad
@transaction.atomic
def crear_inspeccion(insp, usuario):
    insp.numero = _numero('CAL', 'QC')
    insp.inspector = usuario
    insp.save()
    for p in ParametroCalidad.objects.filter(producto=insp.producto):
        ResultadoCalidad.objects.create(inspeccion=insp, caracteristica=p.nombre, unidad=p.unidad, minimo=p.minimo,
                                        maximo=p.maximo, especificacion=p.especificacion)
    return insp


@transaction.atomic
def registrar_resultados(insp, valores, resultado, observaciones, usuario):
    """valores: {resultado_id: (valor Decimal o None, texto, conforme bool o None)}."""
    if insp.cuarentena_id:
        raise ErrorPlanta('La inspección ya envió la mercadería a cuarentena.')
    for r in insp.resultados.all():
        if r.pk in valores:
            r.valor, r.texto, conforme = valores[r.pk]
            r.conforme = conforme
            r.evaluar()
            r.save()
    if resultado not in dict(InspeccionCalidad.RESULTADOS):
        raise ErrorPlanta('Resultado no válido.')
    if resultado == 'APROBADO' and insp.resultados.filter(conforme=False).exists():
        raise ErrorPlanta('Hay características fuera de especificación: márquela como rechazada o aprobada con '
                          'observaciones (explíquelo en las observaciones).')
    if resultado == 'OBSERVADO' and len((observaciones or '').strip()) < 10:
        raise ErrorPlanta('Explique las observaciones (mínimo 10 caracteres).')
    insp.resultado, insp.observaciones, insp.inspector = resultado, observaciones, usuario
    insp.save()
    return insp


@transaction.atomic
def enviar_a_cuarentena(insp, cantidad, usuario):
    """Traslada lo rechazado al almacén de cuarentena (queda fuera del stock disponible)."""
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    if insp.resultado != 'RECHAZADO':
        raise ErrorPlanta('Solo se envía a cuarentena lo rechazado.')
    if insp.cuarentena_id:
        raise ErrorPlanta('Ya se envió a cuarentena.')
    if not insp.almacen_id:
        raise ErrorPlanta('Indique en qué almacén está la mercadería.')
    if not cantidad or cantidad <= 0:
        raise ErrorPlanta('Indique la cantidad.')
    op = Operacion.objects.create(
        tipo=TipoOperacion.objects.get(codigo='TRAS_ALM'), fecha=timezone.localdate(), almacen_origen=insp.almacen,
        almacen_destino=Almacen.especial('DESTRUCCION'), referencia=insp.numero, creado_por=usuario,
        glosa=f'Cuarentena por inspección {insp.numero}: {insp.observaciones}'[:500])
    op.items.create(producto=insp.producto, cantidad=cantidad, lote=insp.lote)
    try:
        inv.confirmar(op, usuario)
    except inv.ErrorOperacion as exc:
        raise ErrorPlanta(f'No se pudo trasladar a cuarentena: {exc}') from exc
    insp.cuarentena = op
    insp.save(update_fields=['cuarentena'])
    return op


# ---------------------------------------------------------------- mantenimiento
def crear_orden(orden, usuario):
    orden.numero = _numero('OTM', 'OT')
    orden.creado_por = usuario
    orden.save()
    return orden


def programar_preventivos(dias, usuario):
    """Crea las órdenes preventivas de los planes que vencen en los próximos días (sin otra orden abierta)."""
    hasta = timezone.localdate() + timedelta(days=dias)
    creadas = []
    for plan in PlanMantenimiento.objects.filter(activo=True, equipo__activo=True).select_related('equipo'):
        if plan.proxima > hasta:
            continue
        if plan.ordenes.filter(estado__in=('PROGRAMADA', 'EN_EJECUCION')).exists():
            continue
        creadas.append(crear_orden(OrdenMantenimiento(
            equipo=plan.equipo, tipo='PREVENTIVO', plan=plan, fecha_programada=max(plan.proxima, timezone.localdate()),
            descripcion=plan.tarea), usuario))
    return creadas


def iniciar(orden):
    if orden.estado != 'PROGRAMADA':
        raise ErrorPlanta('Solo se inician órdenes programadas.')
    orden.estado, orden.fecha_inicio = 'EN_EJECUCION', timezone.localdate()
    orden.save(update_fields=['estado', 'fecha_inicio'])


@transaction.atomic
def cerrar(orden, usuario, fecha=None):
    """Cierra la orden: descarga los repuestos del almacén (gasto de mantenimiento al centro de costo del puesto) y
    actualiza la última ejecución del plan preventivo."""
    from contabilidad.models import CuentaContable
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    if orden.estado not in ('PROGRAMADA', 'EN_EJECUCION'):
        raise ErrorPlanta('La orden ya está cerrada o anulada.')
    if len((orden.trabajo_realizado or '').strip()) < 5:
        raise ErrorPlanta('Describa el trabajo realizado antes de cerrar.')
    fecha = fecha or timezone.localdate()
    repuestos = list(orden.repuestos.select_related('producto', 'almacen'))
    if repuestos:
        centro = orden.equipo.centro.centro_costo if orden.equipo.centro_id else None
        por_almacen = {}
        for r in repuestos:
            por_almacen.setdefault(r.almacen_id, []).append(r)
        if len(por_almacen) > 1:
            raise ErrorPlanta('Use repuestos de un solo almacén por orden.')
        almacen_id, filas = next(iter(por_almacen.items()))
        op = Operacion.objects.create(
            tipo=TipoOperacion.objects.get(codigo='CONS_REQ'), fecha=fecha, almacen_origen_id=almacen_id,
            centro_costo=centro, cuenta_gasto=CuentaContable.objects.filter(codigo='6343').first(),
            referencia=orden.numero, creado_por=usuario,
            glosa=f'Repuestos de la {orden} ({orden.equipo.nombre})'[:500])
        for r in filas:
            op.items.create(producto=r.producto, cantidad=r.cantidad)
        try:
            inv.confirmar(op, usuario)
        except inv.ErrorOperacion as exc:
            raise ErrorPlanta(f'Repuestos: {exc}') from exc
        orden.consumo = op
    orden.estado, orden.fecha_fin = 'CERRADA', fecha
    orden.fecha_inicio = orden.fecha_inicio or fecha
    orden.save()
    if orden.plan_id:
        orden.plan.ultima_fecha = fecha
        orden.plan.save(update_fields=['ultima_fecha'])
    return orden
