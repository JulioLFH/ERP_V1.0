"""Mantenimiento ampliado (v1.84, MNT-01 a MNT-08): avisos del operador que se aprueban y pasan a orden, planes por
condición (lecturas con umbral) con hoja de tareas y materiales que se copian a la orden, horas por técnico a su costo,
reservas de repuestos y repuestos críticos, permiso de trabajo y LOTO antes de iniciar, orden de calibración, y los
indicadores (MTBF, MTTR, disponibilidad, cumplimiento, backlog, Pareto de fallas, costo vs reposición y presupuesto)."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import r2

from .models import AvisoMantenimiento, LecturaCondicion, OrdenMantenimiento, PlanMantenimiento, TareaOrden
from .planta import ErrorPlanta, _numero, crear_orden

D0 = Decimal('0')
HORAS_DIA = Decimal('8')  # capacidad del técnico por día en el calendario


# ---------------------------------------------------------------- avisos (MNT-03)
def crear_aviso(equipo, sintoma, prioridad, parado, usuario, foto=None):
    from core.sustentos import adjuntar
    if len((sintoma or '').strip()) < 5:
        raise ErrorPlanta('Describa qué pasa con el equipo (mínimo 5 caracteres).')
    a = AvisoMantenimiento.objects.create(numero=_numero('AVM', 'AV'), equipo=equipo, sintoma=sintoma.strip(),
                                          prioridad=prioridad or 'NORMAL', equipo_parado=bool(parado),
                                          reportado_por=usuario if usuario and usuario.is_authenticated else None)
    if foto:
        adjuntar(a, foto, usuario, 'Foto del aviso')
    if parado and equipo.estado == 'OPERATIVO':
        equipo.estado = 'INOPERATIVO'
        equipo.save(update_fields=['estado'])
    return a


@transaction.atomic
def convertir_aviso(aviso, usuario, tipo='CORRECTIVO', fecha=None, tecnico=None):
    if aviso.estado != 'PENDIENTE':
        raise ErrorPlanta('El aviso ya se revisó.')
    ot = crear_orden(OrdenMantenimiento(
        equipo=aviso.equipo, tipo=tipo, prioridad=aviso.prioridad, tecnico=tecnico,
        fecha_programada=fecha or timezone.localdate(),
        descripcion=f'{aviso}: {aviso.sintoma}'[:2000] + (' (equipo parado)' if aviso.equipo_parado else '')), usuario)
    aviso.estado, aviso.orden = 'CONVERTIDO', ot
    aviso.save(update_fields=['estado', 'orden'])
    return ot


def rechazar_aviso(aviso, motivo):
    if aviso.estado != 'PENDIENTE':
        raise ErrorPlanta('El aviso ya se revisó.')
    if len((motivo or '').strip()) < 5:
        raise ErrorPlanta('Indique por qué se rechaza.')
    aviso.estado, aviso.motivo_rechazo = 'RECHAZADO', motivo.strip()[:200]
    aviso.save(update_fields=['estado', 'motivo_rechazo'])
    return aviso


# ---------------------------------------------------------------- planes: hoja de tareas, materiales y condición (MNT-02)
def preparar_orden(ot):
    """Copia la hoja de tareas y reserva los materiales del plan en la orden."""
    from core.models import Almacen
    if not ot.plan_id:
        return ot
    for t in ot.plan.tareas.all():
        TareaOrden.objects.create(orden=ot, secuencia=t.secuencia, descripcion=t.descripcion[:200])
    horas = sum((Decimal(t.minutos) for t in ot.plan.tareas.all()), D0) / 60
    if horas and not ot.horas_estimadas:
        ot.horas_estimadas = r2(horas)
        ot.save(update_fields=['horas_estimadas'])
    almacen = Almacen.principal()
    for m in ot.plan.materiales.select_related('producto'):
        if not ot.repuestos.filter(producto=m.producto).exists():
            ot.repuestos.create(producto=m.producto, cantidad=m.cantidad, almacen=almacen)
    return ot


@transaction.atomic
def registrar_lectura(equipo, variable, valor, usuario, unidad=''):
    """Lectura de condición: si está fuera del umbral de un plan del equipo con esa variable, genera la orden
    predictiva (si no hay otra abierta del plan)."""
    valor = Decimal(valor)
    lectura = LecturaCondicion.objects.create(equipo=equipo, variable=variable.strip()[:40], valor=valor,
                                              unidad=unidad[:15],
                                              usuario=usuario if usuario and usuario.is_authenticated else None)
    for plan in equipo.planes.filter(activo=True, variable__iexact=variable.strip()):
        fuera = ((plan.umbral_min is not None and valor < plan.umbral_min) or
                 (plan.umbral_max is not None and valor > plan.umbral_max))
        if not fuera:
            continue
        lectura.fuera_de_rango = True
        if plan.ordenes.filter(estado__in=('PROGRAMADA', 'EN_EJECUCION')).exists():
            continue
        ot = crear_orden(OrdenMantenimiento(
            equipo=equipo, tipo='PREDICTIVO', plan=plan, prioridad='ALTA', fecha_programada=timezone.localdate(),
            descripcion=f'{plan.tarea}: {variable} {valor:g} {unidad} fuera del umbral '
                        f'({plan.umbral_min if plan.umbral_min is not None else "—"} a '
                        f'{plan.umbral_max if plan.umbral_max is not None else "—"})'), usuario)
        preparar_orden(ot)
        lectura.orden = ot
    lectura.save(update_fields=['fuera_de_rango', 'orden'])
    return lectura


# ---------------------------------------------------------------- recursos (MNT-04)
def registrar_horas(orden, trabajador, horas, fecha=None):
    from obras.servicios import costo_hora
    horas = Decimal(horas or 0)
    if horas <= 0 or horas > 24:
        raise ErrorPlanta('Indique las horas trabajadas (entre 0 y 24).')
    if orden.estado not in ('PROGRAMADA', 'EN_EJECUCION'):
        raise ErrorPlanta('La orden ya está cerrada.')
    return orden.horas_tecnicos.create(trabajador=trabajador, horas=horas, fecha=fecha or timezone.localdate(),
                                       costo_hora=costo_hora(trabajador, fecha))


def costo_mano_obra(orden):
    return sum((h.costo for h in orden.horas_tecnicos.all()), D0)


def reservado(producto, excluir=None):
    """Cantidad reservada del repuesto en las órdenes abiertas."""
    qs = orden_repuestos().filter(producto=producto, orden__estado__in=('PROGRAMADA', 'EN_EJECUCION'))
    if excluir is not None:
        qs = qs.exclude(orden=excluir)
    return qs.aggregate(s=Sum('cantidad'))['s'] or D0


def orden_repuestos():
    from .models import RepuestoOrden
    return RepuestoOrden.objects.all()


def repuestos_criticos_bajos():
    """Repuestos críticos con stock libre (menos lo reservado) bajo el mínimo."""
    from .models import RepuestoCritico
    salida = []
    for r in RepuestoCritico.objects.select_related('equipo', 'producto'):
        disponible = r.producto.stock - reservado(r.producto)
        if disponible < r.stock_minimo:
            salida.append({'critico': r, 'stock': r.producto.stock, 'reservado': reservado(r.producto),
                           'disponible': disponible})
    return salida


# ---------------------------------------------------------------- seguridad (MNT-08)
def validar_inicio(orden):
    """Permiso de trabajo validado por SST y LOTO aplicado antes de iniciar, si la orden los requiere."""
    if orden.requiere_permiso:
        p = orden.permiso_trabajo
        if p is None:
            raise ErrorPlanta('Esta orden requiere un permiso de trabajo de alto riesgo (Seguridad y salud › '
                              'Permisos de trabajo): vincúlelo antes de iniciar.')
        if p.cerrado or p.estado_validacion != 'VALIDADO':
            raise ErrorPlanta(f'El {p} debe estar validado por SST y no cerrado.')
        if p.fecha > timezone.localdate():
            raise ErrorPlanta(f'El {p} es para el {p.fecha:%d/%m/%Y}.')
    if orden.requiere_loto and not (orden.loto_aplicado_por or '').strip():
        raise ErrorPlanta('Registre quién aplicó el bloqueo y etiquetado (LOTO) antes de iniciar.')


def iniciar(orden):
    from . import planta
    validar_inicio(orden)
    planta.iniciar(orden)
    if not orden.inicio_real:
        orden.inicio_real = timezone.now()
        orden.save(update_fields=['inicio_real'])
    return orden


@transaction.atomic
def cerrar(orden, usuario, fecha=None, calibracion=None):
    """Cierre ampliado: mano de obra desde las horas de los técnicos, horas de parada desde las paradas de producción
    vinculadas (MNT-01), costo del servicio desde la orden de servicio (MNT-04), checklist completo (MNT-07) y, si es
    de calibración, el registro de la calibración del instrumento (MNT-08)."""
    from . import calidad_farma, planta
    pendientes = orden.tareas.filter(hecha=False)
    if pendientes.exists():
        raise ErrorPlanta(f'Faltan {pendientes.count()} tarea(s) del checklist.')
    if orden.horas_tecnicos.exists() and not orden.costo_mano_obra:
        orden.costo_mano_obra = costo_mano_obra(orden)
    paradas = sum((p.horas for p in orden.paradas.all()), D0)
    if paradas and not orden.horas_parada:
        orden.horas_parada = paradas
    if orden.orden_servicio_id and not orden.costo_servicios:
        oc = orden.orden_servicio
        orden.costo_servicios = (oc.total or D0) - (oc.igv or D0)
    if orden.tipo == 'CALIBRACION':
        if calibracion is None:
            raise ErrorPlanta('Registre el certificado y la próxima calibración del instrumento.')
        calibracion.equipo = orden.equipo
        calidad_farma.registrar_calibracion(calibracion, usuario)
    if orden.lectura_cierre is not None and orden.equipo.medidor and orden.lectura_cierre < orden.equipo.lectura_actual:
        raise ErrorPlanta(f'La lectura de cierre no puede ser menor que la actual del equipo '
                          f'({orden.equipo.lectura_actual:,.1f}).')
    if not orden.fin_real:
        orden.fin_real = timezone.now()
    planta.cerrar(orden, usuario, fecha)
    if orden.equipo.estado in ('INOPERATIVO', 'MANTENIMIENTO'):
        orden.equipo.estado = 'OPERATIVO'
        orden.equipo.save(update_fields=['estado'])
    return orden


# ---------------------------------------------------------------- programación (MNT-07)
def calendario(lunes):
    """Semana: órdenes abiertas por día y técnico con su carga (horas estimadas) frente a 8 h por día, y las
    ventanas de parada acordadas con producción."""
    from .models import VentanaParada
    dias = [lunes + timedelta(days=i) for i in range(7)]
    ordenes = (OrdenMantenimiento.objects.filter(estado__in=('PROGRAMADA', 'EN_EJECUCION'),
                                                 fecha_programada__range=(dias[0], dias[-1]))
               .select_related('equipo', 'tecnico'))
    carga = defaultdict(lambda: [D0] * 7)
    por_dia = defaultdict(list)
    for o in ordenes:
        i = (o.fecha_programada - lunes).days
        por_dia[i].append(o)
        carga[str(o.tecnico or 'Sin técnico')][i] += o.horas_estimadas or D0
    inicio = timezone.make_aware(datetime.combine(dias[0], time.min))
    fin = timezone.make_aware(datetime.combine(dias[-1], time.max))
    ventanas = VentanaParada.objects.filter(inicio__lte=fin, fin__gte=inicio).select_related('centro', 'equipo')
    return {'dias': dias, 'por_dia': [por_dia[i] for i in range(7)], 'ventanas': ventanas,
            'carga': [{'tecnico': k, 'horas': v, 'exceso': [h > HORAS_DIA for h in v]} for k, v in sorted(carga.items())]}


def ordenes_del_tecnico(usuario):
    t = getattr(usuario, 'trabajador_planilla', None)
    qs = OrdenMantenimiento.objects.filter(estado__in=('PROGRAMADA', 'EN_EJECUCION')).select_related('equipo')
    return qs.filter(tecnico=t) if t is not None else qs.none()


# ---------------------------------------------------------------- indicadores (MNT-06)
def indicadores(desde, hasta, centro=None):
    """Por equipo: fallas, MTBF, MTTR, disponibilidad, costo, preventivo vs correctivo, costo vs reposición y
    presupuesto. Más el cumplimiento del preventivo, el backlog y el Pareto de modos de falla."""
    from .models import Equipo
    horas_periodo = Decimal((hasta - desde).days + 1) * 24
    equipos = Equipo.objects.filter(activo=True)
    if centro:
        equipos = equipos.filter(centro=centro)
    cerradas = (OrdenMantenimiento.objects.filter(estado='CERRADA', fecha_fin__range=(desde, hasta),
                                                  equipo__in=equipos)
                .select_related('equipo', 'falla_modo', 'consumo').prefetch_related('consumo__items'))
    por_equipo = defaultdict(list)
    for o in cerradas:
        por_equipo[o.equipo_id].append(o)
    anio = OrdenMantenimiento.objects.filter(estado='CERRADA', fecha_fin__year=hasta.year).select_related('consumo')
    costo_anio = defaultdict(lambda: D0)
    for o in anio.prefetch_related('consumo__items'):
        costo_anio[o.equipo_id] += o.costo_total
    filas = []
    for e in equipos.select_related('centro'):
        ots = por_equipo.get(e.pk, [])
        fallas = [o for o in ots if o.tipo == 'CORRECTIVO']
        parada = sum((o.horas_parada for o in ots), D0)
        reparacion = [o.horas_reparacion or o.horas_parada for o in fallas]
        operativas = horas_periodo - parada
        costo = sum((o.costo_total for o in ots), D0)
        prev = sum((o.costo_total for o in ots if o.tipo != 'CORRECTIVO'), D0)
        filas.append({
            'equipo': e, 'ordenes': len(ots), 'fallas': len(fallas), 'horas_parada': parada,
            'mtbf': r2(operativas / len(fallas)) if fallas else None,
            'mttr': r2(sum(reparacion, D0) / len(reparacion)) if reparacion else None,
            'disponibilidad': r2(operativas * 100 / horas_periodo) if horas_periodo else None,
            'costo': costo, 'pct_preventivo': r2(prev * 100 / costo) if costo else None,
            'costo_anio': costo_anio[e.pk],
            'pct_reposicion': r2(costo_anio[e.pk] * 100 / e.valor_reposicion) if e.valor_reposicion else None,
            'presupuesto': e.presupuesto_anual,
            'pct_presupuesto': r2(costo_anio[e.pk] * 100 / e.presupuesto_anual) if e.presupuesto_anual else None})
    programadas = OrdenMantenimiento.objects.filter(tipo='PREVENTIVO', fecha_programada__range=(desde, hasta),
                                                    equipo__in=equipos).exclude(estado='ANULADA')
    a_tiempo = programadas.filter(estado='CERRADA', fecha_fin__lte=models_f('fecha_programada', 3)).count()
    cumplimiento = r2(Decimal(a_tiempo) * 100 / programadas.count()) if programadas.count() else None
    abiertas = OrdenMantenimiento.objects.filter(estado__in=('PROGRAMADA', 'EN_EJECUCION'), equipo__in=equipos)
    backlog = abiertas.aggregate(s=Sum('horas_estimadas'))['s'] or D0
    pareto = defaultdict(int)
    for o in cerradas:
        if o.tipo == 'CORRECTIVO':
            pareto[str(o.falla_modo or 'Sin código de falla')] += 1
    total_fallas = sum(pareto.values())
    acumulado, lista = 0, []
    for modo, n in sorted(pareto.items(), key=lambda kv: -kv[1]):
        acumulado += n
        lista.append({'modo': modo, 'fallas': n, 'pct': r2(Decimal(n) * 100 / total_fallas),
                      'acumulado': r2(Decimal(acumulado) * 100 / total_fallas)})
    total = sum((f['costo'] for f in filas), D0)
    preventivo = sum((o.costo_total for o in cerradas if o.tipo != 'CORRECTIVO'), D0)
    return {'filas': filas, 'cumplimiento': cumplimiento, 'programadas': programadas.count(), 'a_tiempo': a_tiempo,
            'backlog': backlog, 'abiertas': abiertas.count(), 'pareto': lista, 'costo': total,
            'pct_preventivo': r2(preventivo * 100 / total) if total else None,
            'fallas': sum((f['fallas'] for f in filas), 0)}


def models_f(campo, dias):
    """F(campo) + días (tolerancia del cumplimiento del preventivo)."""
    from django.db.models import DateField, ExpressionWrapper, F
    return ExpressionWrapper(F(campo) + timedelta(days=dias), output_field=DateField())


def alertas():
    """Garantías por vencer, repuestos críticos bajos y equipos que superan el % del valor de reposición."""
    from .models import Equipo
    hoy = timezone.localdate()
    garantias = Equipo.objects.filter(activo=True, garantia_hasta__range=(hoy, hoy + timedelta(days=30)))
    return {'garantias': garantias, 'repuestos': repuestos_criticos_bajos()}
