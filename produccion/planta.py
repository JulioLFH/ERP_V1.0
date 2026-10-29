"""Calidad (inspecciones con plan de calidad y cuarentena) y mantenimiento de planta (equipos, planes preventivos y
órdenes de trabajo con consumo de repuestos)."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import Almacen, Serie

from .models import (InspeccionCalidad, OrdenMantenimiento, ParametroCalidad, PlanMantenimiento,
                     ResultadoCalidad)


class ErrorPlanta(Exception):
    pass


def _numero(tipo, serie):
    s, n = Serie.siguiente(tipo, serie)
    return f'{s}-{n}'


# ---------------------------------------------------------------- calidad
@transaction.atomic
def crear_inspeccion(insp, usuario):
    """La inspección se hace con la especificación aprobada vigente (CAL-05): sus características y la versión quedan
    en la inspección. Si el producto tiene especificaciones y ninguna aprobada, no se inspecciona."""
    from decimal import Decimal, InvalidOperation

    from .calidad_farma import especificacion_vigente, validar_instrumento
    validar_instrumento(insp.instrumento, insp.fecha)
    esp = especificacion_vigente(insp.producto)
    if esp is None and insp.producto.especificaciones.exists():
        raise ErrorPlanta(f'{insp.producto.nombre} no tiene una especificación aprobada vigente: QA debe aprobarla.')
    insp.numero = _numero('CAL', 'QC')
    insp.inspector = usuario
    insp.especificacion = esp
    insp.save()

    def _dec(texto):
        try:
            return Decimal(texto) if texto not in (None, '') else None
        except InvalidOperation:
            return None
    if esp is not None:
        for c in esp.caracteristicas:
            ResultadoCalidad.objects.create(inspeccion=insp, caracteristica=c['nombre'], unidad=c.get('unidad', ''),
                                            minimo=_dec(c.get('minimo')), maximo=_dec(c.get('maximo')),
                                            especificacion=c.get('especificacion', ''))
    else:
        for p in ParametroCalidad.objects.filter(producto=insp.producto):
            ResultadoCalidad.objects.create(inspeccion=insp, caracteristica=p.nombre, unidad=p.unidad,
                                            minimo=p.minimo, maximo=p.maximo, especificacion=p.especificacion)
    return insp


def inspeccion_pendiente(producto, lote, cantidad, almacen, concepto=''):
    """AUD-09: el lote que entra en cuarentena por su plan de calidad deja su inspección pendiente (con las
    características de la especificación vigente) para que Calidad se entere. Una sola por lote. Si el producto no
    tiene especificación aprobada, la inspección queda sin características (QA la completa)."""
    if InspeccionCalidad.objects.filter(producto=producto, lote__iexact=lote.codigo,
                                        resultado__in=('PENDIENTE', 'OOS')).exists():
        return None
    tipo = 'TERMINADO' if concepto in ('ENT_OP', 'PRODUCCION') else 'RECEPCION'
    insp = InspeccionCalidad(tipo=tipo, producto=producto, lote=lote.codigo[:40], cantidad=cantidad,
                             almacen=almacen if isinstance(almacen, Almacen) else None,
                             observaciones='Creada al ingresar el lote en cuarentena (plan de calidad).')
    try:
        return crear_inspeccion(insp, None)
    except ErrorPlanta:
        insp.numero = _numero('CAL', 'QC')
        insp.save()
        return insp


def registrar_resultados(insp, valores, resultado, observaciones, usuario, motivo=''):
    """valores: {resultado_id: (valor Decimal o None, texto, conforme bool o None)}. Con rastro de auditoría, bloqueo
    al decidir y OOS (produccion/calidad_farma.py)."""
    from .calidad_farma import registrar_resultados as registrar
    return registrar(insp, valores, resultado, observaciones, usuario, motivo)


@transaction.atomic
def crear_certificado(cert, usuario):
    """Certificado de análisis del lote (por aprobar) con los resultados de su inspección: las características del
    plan de calidad marcadas «en el certificado»."""
    lote = cert.lote  # CAL-04: solo lotes con stock y vigentes
    if lote.stock <= 0:
        raise ErrorPlanta(f'El lote {lote.codigo} no tiene stock.')
    if lote.vencido:
        raise ErrorPlanta(f'El lote {lote.codigo} está vencido ({lote.vencimiento:%d/%m/%Y}).')
    if cert.inspeccion_id:
        insp = cert.inspeccion
        if insp.producto_id != cert.lote.producto_id:
            raise ErrorPlanta('La inspección es de otro producto.')
        if insp.lote and insp.lote.upper() != lote.codigo.upper():
            raise ErrorPlanta(f'La inspección {insp.numero} es del lote {insp.lote}.')
        if insp.resultado not in ('APROBADO', 'OBSERVADO', 'RECHAZADO'):
            raise ErrorPlanta(f'La inspección {insp.numero} aún no tiene resultado.')
        excluidas = set(ParametroCalidad.objects.filter(producto=insp.producto, en_certificado=False)
                        .values_list('nombre', flat=True))
        cert.resultados = [{'caracteristica': r.caracteristica, 'unidad': r.unidad,
                            'especificacion': r.especificacion or ' – '.join(
                                str(v.normalize()) for v in (r.minimo, r.maximo) if v is not None),
                            'resultado': r.texto or ('' if r.valor is None else str(r.valor.normalize())),
                            'conforme': r.conforme} for r in insp.resultados.all() if r.caracteristica not in excluidas]
    if cert.lote.certificados.filter(estado__in=('BORRADOR', 'APROBADO')).exists():
        raise ErrorPlanta(f'El lote {cert.lote.codigo} ya tiene un certificado vigente: anúlelo para emitir otro.')
    cert.numero = _numero('COA', 'CA')
    cert.emitido_por = usuario
    cert.save()
    return cert


@transaction.atomic
def resolver_certificado(cert, usuario, accion, comentario='', firmado=False):
    """Aprobar libera el lote (solo con firma electrónica y la revisión de QA: calidad_farma.liberar); rechazar lo deja
    rechazado; anular devuelve el lote a cuarentena si su producto exige liberación (si ya estaba liberado, con
    motivo y permiso de QA)."""
    from core.permisos import puede
    lote = cert.lote
    if accion in ('aprobar', 'rechazar'):
        if cert.estado != 'BORRADOR':
            raise ErrorPlanta('El certificado ya fue resuelto.')
        if accion == 'aprobar' and not firmado:
            raise ErrorPlanta('La liberación del lote va con firma electrónica (Liberar con firma).')
        if accion == 'aprobar' and any(r.get('conforme') is False for r in cert.resultados):
            raise ErrorPlanta('Hay resultados fuera de especificación: un OOS no se libera (CAL-02).')
        cert.estado = 'APROBADO' if accion == 'aprobar' else 'RECHAZADO'
        lote.estado_calidad = 'LIBERADO' if accion == 'aprobar' else 'RECHAZADO'
    elif accion == 'anular':
        if cert.estado == 'ANULADO':
            raise ErrorPlanta('El certificado ya está anulado.')
        if cert.estado == 'APROBADO':
            if not (usuario.is_superuser or puede(usuario, 'control_calidad.reabrir')):
                raise ErrorPlanta('Un certificado aprobado (lote liberado) lo anula QA (permiso «reabrir»).')
            if len((comentario or '').strip()) < 10:
                raise ErrorPlanta('Indique el motivo de la anulación (mínimo 10 caracteres).')
        cert.estado = 'ANULADO'
        lote.estado_calidad = 'CUARENTENA' if lote.producto._requiere_liberacion() else 'LIBRE'
    else:
        raise ErrorPlanta('Acción no válida.')
    if comentario:
        cert.observaciones = (f'{cert.observaciones}\n' if cert.observaciones else '') + comentario
    cert.aprobado_por, cert.aprobado_en = usuario, timezone.now()
    cert.save()
    lote.save(update_fields=['estado_calidad'])
    return cert


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


@transaction.atomic
def programar_preventivos(dias, usuario):
    """Crea las órdenes preventivas de los planes que vencen en los próximos días por tiempo o por uso (lectura
    actual o proyectada del odómetro / horómetro), sin otra orden abierta. Si el plan es de un componente con
    repuesto, la orden lo trae y, si falta stock, se genera la solicitud de pedido del repuesto."""
    hoy = timezone.localdate()
    hasta = hoy + timedelta(days=dias)
    creadas = []
    for plan in PlanMantenimiento.objects.filter(activo=True, equipo__activo=True).select_related(
            'equipo', 'componente__repuesto'):
        if not plan.vence(hasta + timedelta(days=plan.anticipacion_dias or 0)):  # MNT-02: anticipación del plan
            continue
        if plan.ordenes.filter(estado__in=('PROGRAMADA', 'EN_EJECUCION')).exists():
            continue
        descripcion = plan.tarea + (f' ({plan.componente.ruta})' if plan.componente_id else '')
        if plan.frecuencia_uso and plan.equipo.medidor:
            descripcion += (f' · cada {plan.frecuencia_uso:,.0f} {plan.equipo.unidad_medidor}, toca a los '
                            f'{plan.proxima_lectura:,.0f} (actual {plan.equipo.lectura_actual:,.0f})')
        ot = crear_orden(OrdenMantenimiento(
            equipo=plan.equipo, tipo='PREVENTIVO', plan=plan, fecha_programada=max(plan.proxima or hoy, hoy),
            descripcion=descripcion), usuario)
        from .mantenimiento import preparar_orden
        preparar_orden(ot)  # MNT-02: hoja de tareas (checklist) y materiales reservados
        if plan.componente_id and plan.componente.repuesto_id and not ot.repuestos.filter(
                producto=plan.componente.repuesto).exists():
            ot.repuestos.create(producto=plan.componente.repuesto, cantidad=plan.componente.cantidad_repuesto,
                                almacen=Almacen.principal())
        if ot.repuestos.exists():
            ot.solicitud = _solicitar_repuestos(ot, usuario)
        creadas.append(ot)
    return creadas


def _solicitar_repuestos(ot, usuario):
    """Solicitud de pedido de los repuestos de la orden que no tienen stock (None si alcanza o no hay centro)."""
    from compras.solped import crear, iniciales_mantenimiento
    cabecera, lineas = iniciales_mantenimiento(ot)
    lineas = [l for l in lineas if l['producto'] is not None]
    if not lineas or cabecera['centro_costo'] is None:
        return None
    return crear({**cabecera, 'tipo': 'MATERIAL', 'orden_mantenimiento': ot}, lineas, usuario,
                 origen='MANTENIMIENTO')


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
        centro = orden.equipo.centro_costo_efectivo
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
        lectura = orden.equipo.lectura_actual if orden.equipo.medidor else None
        orden.plan.ultima_fecha, orden.plan.ultima_lectura = fecha, lectura
        orden.plan.save(update_fields=['ultima_fecha', 'ultima_lectura'])
        if orden.plan.componente_id:  # el componente registra su último cambio
            c = orden.plan.componente
            c.fecha_ultimo_cambio, c.lectura_ultimo_cambio = fecha, lectura
            c.save(update_fields=['fecha_ultimo_cambio', 'lectura_ultimo_cambio'])
    return orden
