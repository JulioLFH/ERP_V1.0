"""Operación de la flota: combustible y rendimiento (FLO-05), costo por equipo y por km u hora (FLO-06),
conductores asignados, viajes desde las guías y préstamos en comodato (FLO-07)."""
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Min, Sum
from django.utils import timezone

from core.models import r2

from .models import AsignacionConductor, CargaCombustible, PrestamoEquipo, Viaje

D0 = Decimal('0')
DESVIO_PCT = Decimal('15')  # rendimiento fuera de ±15 % del esperado


class ErrorOperacion(Exception):
    pass


# ---------------------------------------------------------------- combustible (FLO-05)
@transaction.atomic
def registrar_carga(carga, usuario):
    from .lecturas import ErrorLectura, registrar, validar
    eq = carga.equipo
    if not carga.galones or carga.galones <= 0 or not carga.precio_galon or carga.precio_galon <= 0:
        raise ErrorOperacion('Indique los galones y el precio por galón.')
    if carga.lectura is not None and eq.medidor:
        try:
            validar(eq, carga.lectura, carga.fecha)
        except ErrorLectura as exc:
            raise ErrorOperacion(str(exc)) from exc
    carga.importe = r2(carga.galones * carga.precio_galon)
    carga.centro_costo = carga.centro_costo or eq.centro_costo_efectivo
    carga.registrado_por = usuario
    carga.save()
    if carga.lectura is not None and eq.medidor:
        registrar(eq, carga.lectura, carga.fecha, usuario, 'COMBUSTIBLE', observacion=f'Vale {carga.vale}'.strip())
    calcular_rendimiento(carga)
    return carga


def calcular_rendimiento(carga):
    """Entre dos cargas a tanque lleno: recorrido / galones cargados desde la anterior (incluida esta)."""
    if not carga.tanque_lleno or carga.lectura is None:
        return None
    previa = (CargaCombustible.objects.filter(equipo=carga.equipo, tanque_lleno=True, lectura__isnull=False,
                                              lectura__lt=carga.lectura).exclude(pk=carga.pk)
              .order_by('-lectura').first())
    if previa is None:
        return None
    galones = (CargaCombustible.objects.filter(equipo=carga.equipo, lectura__gt=previa.lectura,
                                               lectura__lte=carga.lectura).aggregate(s=Sum('galones'))['s'] or D0)
    if galones <= 0:
        return None
    carga.rendimiento = r2((carga.lectura - previa.lectura) / galones)
    esperado = carga.equipo.rendimiento_esperado
    carga.desvio = bool(esperado and abs(carga.rendimiento - esperado) * 100 / esperado > DESVIO_PCT)
    carga.save(update_fields=['rendimiento', 'desvio'])
    return carga.rendimiento


# ---------------------------------------------------------------- conductores (FLO-07)
@transaction.atomic
def asignar_conductor(equipo, conductor, desde, usuario, observacion=''):
    """Cierra la asignación vigente y abre la nueva; el conductor de planilla queda como responsable del equipo."""
    actual = equipo.asignaciones.filter(hasta__isnull=True).first()
    if actual is not None:
        if actual.conductor_id == conductor.pk:
            raise ErrorOperacion(f'{conductor} ya está asignado a {equipo}.')
        if desde < actual.desde:
            raise ErrorOperacion('La nueva asignación no puede empezar antes que la vigente.')
        actual.hasta = desde
        actual.save(update_fields=['hasta'])
    a = AsignacionConductor.objects.create(equipo=equipo, conductor=conductor, desde=desde, observacion=observacion,
                                           registrado_por=usuario)
    if conductor.trabajador_id:
        type(equipo).objects.filter(pk=equipo.pk).update(responsable=conductor.trabajador)
    return a


def conductor_actual(equipo):
    a = equipo.asignaciones.filter(hasta__isnull=True).select_related('conductor').first()
    return a.conductor if a else None


# ---------------------------------------------------------------- viajes (FLO-07)
def viaje_de_guia(guia, usuario=None):
    """La guía con vehículo propio (transporte privado) abre su viaje con el km de salida del vehículo."""
    vehiculo = guia.vehiculo
    if vehiculo is None or not vehiculo.equipo_id or guia.modalidad != '02' or guia.estado == 'ANULADA':
        return None
    if Viaje.objects.filter(guia=guia).exists():
        return guia.viaje
    eq = vehiculo.equipo
    return Viaje.objects.create(
        equipo=eq, conductor=guia.conductor, guia=guia, salida=guia.fecha_traslado,
        km_salida=eq.lectura_actual if eq.medidor else None, origen=guia.partida_direccion[:150],
        destino=guia.llegada_direccion[:150], registrado_por=usuario)


@transaction.atomic
def cerrar_viaje(viaje, km_llegada, llegada, usuario):
    from .lecturas import ErrorLectura, registrar
    if viaje.estado != 'EN_RUTA':
        raise ErrorOperacion('El viaje ya está cerrado.')
    if km_llegada is None:
        raise ErrorOperacion('Indique el km de llegada.')
    if viaje.km_salida is not None and km_llegada < viaje.km_salida:
        raise ErrorOperacion(f'El km de llegada ({km_llegada:,.1f}) es menor al de salida ({viaje.km_salida:,.1f}).')
    if llegada < viaje.salida:
        raise ErrorOperacion('La llegada no puede ser antes de la salida.')
    if viaje.equipo.medidor:
        try:
            registrar(viaje.equipo, km_llegada, llegada, usuario, 'VIAJE',
                      observacion=f'Viaje {viaje.guia or viaje.pk}'[:200])
        except ErrorLectura as exc:
            raise ErrorOperacion(str(exc)) from exc
    viaje.km_llegada, viaje.llegada, viaje.estado = km_llegada, llegada, 'CERRADO'
    viaje.save()
    return viaje


# ---------------------------------------------------------------- préstamos / comodato (FLO-07)
@transaction.atomic
def prestar(prestamo, usuario):
    from core.models import Serie
    eq = prestamo.equipo
    if not eq.aplica_prestamo:
        raise ErrorOperacion(f'{eq} no está marcado como «Se presta a clientes».')
    if eq.prestamos.filter(estado='PRESTADO').exists():
        raise ErrorOperacion(f'{eq} ya está prestado: registre primero su devolución.')
    if prestamo.devolucion_prevista and prestamo.devolucion_prevista < prestamo.entrega:
        raise ErrorOperacion('La devolución prevista es anterior a la entrega.')
    s, n = Serie.siguiente('PRE', 'AC')
    prestamo.numero, prestamo.registrado_por = f'{s}-{n}', usuario
    prestamo.save()
    return prestamo


def devolver(prestamo, fecha, estado_devolucion):
    if prestamo.estado != 'PRESTADO':
        raise ErrorOperacion('El equipo ya fue devuelto.')
    if fecha < prestamo.entrega:
        raise ErrorOperacion('La devolución no puede ser antes de la entrega.')
    prestamo.devolucion, prestamo.estado = fecha, 'DEVUELTO'
    prestamo.estado_devolucion = (estado_devolucion or '').strip()
    prestamo.save()
    return prestamo


def prestamos_vencidos():
    return [p for p in PrestamoEquipo.objects.filter(estado='PRESTADO', devolucion_prevista__lt=timezone.localdate())
            .select_related('equipo', 'cliente')]


# ---------------------------------------------------------------- costos (FLO-06)
def _periodos(desde, hasta):
    return f'{desde:%Y%m}', f'{hasta:%Y%m}'


def uso_en(equipo, desde, hasta):
    """km u horas recorridos en el rango según las lecturas aprobadas."""
    if not equipo.medidor:
        return None
    aprobadas = equipo.lecturas.filter(estado='APROBADA')
    fin = aprobadas.filter(fecha__lte=hasta).aggregate(m=Max('valor'))['m']
    inicio = aprobadas.filter(fecha__lt=desde).aggregate(m=Max('valor'))['m']
    if inicio is None:
        inicio = aprobadas.filter(fecha__gte=desde, fecha__lte=hasta).aggregate(m=Min('valor'))['m']
    if fin is None or inicio is None or fin <= inicio:
        return None
    return fin - inicio


def costos(equipos, desde, hasta):
    """[{equipo, mantenimiento, combustible, documentos, gastos, depreciacion, total, uso, por_uso}] del rango:
    OT cerradas (mano de obra, servicios y repuestos), vales de combustible, documentos (seguros, revisiones),
    otros gastos y la depreciación de su activo fijo."""
    from activos.models import Depreciacion
    p_desde, p_hasta = _periodos(desde, hasta)
    filas = []
    for eq in equipos:
        mant = sum((o.costo_total for o in eq.ordenes.filter(estado='CERRADA', fecha_fin__gte=desde,
                                                             fecha_fin__lte=hasta).select_related('consumo')), D0)
        comb = eq.cargas.filter(fecha__gte=desde, fecha__lte=hasta).aggregate(s=Sum('importe'))['s'] or D0
        docs = (eq.documentos.filter(emision__gte=desde, emision__lte=hasta).aggregate(s=Sum('costo'))['s'] or D0)
        gastos = eq.gastos.filter(fecha__gte=desde, fecha__lte=hasta).aggregate(s=Sum('importe'))['s'] or D0
        dep = (Depreciacion.objects.filter(activo_id=eq.activo_fijo_id, periodo__gte=p_desde, periodo__lte=p_hasta)
               .aggregate(s=Sum('cuota'))['s'] or D0) if eq.activo_fijo_id else D0
        total = mant + comb + docs + gastos + dep
        uso = uso_en(eq, desde, hasta)
        filas.append({'equipo': eq, 'mantenimiento': mant, 'combustible': comb, 'documentos': docs, 'gastos': gastos,
                      'depreciacion': dep, 'total': total, 'uso': uso,
                      'por_uso': r2(total / uso) if uso else None})
    return filas


def rango_por_defecto():
    hoy = timezone.localdate()
    return date(hoy.year, 1, 1), hoy


def ultimos_dias(dias=30):
    return timezone.localdate() - timedelta(days=dias)
