"""Servicios profesionales por proyecto (v1.81): entregables con conformidad y factura (SRV-01), partes de horas con
aprobación y costo hora (SRV-02), garantías del contrato y el cierre del mes: servicio en proceso e ingreso por avance
(NIIF 15) del proyecto (SRV-06)."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import CierreServicioProyecto, EntregableProyecto, Obra, ParteHoras

D0 = Decimal('0')
MAX_HORAS_SEMANA = Decimal('80')


class ErrorServicio(Exception):
    pass


def _param(clave, fecha=None):
    from core import parametros
    return parametros.valor(clave, fecha)


def _usuario(u):
    return u if u is not None and getattr(u, 'is_authenticated', False) else None


def lunes(fecha):
    return fecha - timedelta(days=fecha.weekday())


# ---------------------------------------------------------------- costo hora y partes de horas (SRV-02)
def costo_hora(trabajador, fecha=None):
    """Costo hora del trabajador en planilla: sueldo × factor de cargas / horas del mes (parámetros SRV_*)."""
    if trabajador is None or not trabajador.sueldo:
        return D0
    return (trabajador.sueldo * _param('SRV_FACTOR_CARGAS_PCT', fecha) / 100 / (_param('SRV_HORAS_MES', fecha) or 1)
            ).quantize(Decimal('0.01'))


def jefe_de_proyecto(obra):
    return obra.residente


def puede_aprobar_horas(user, obra):
    from core.permisos import puede
    return user.is_superuser or obra.residente_id == user.pk or puede(user, 'obras.aprobar')


@transaction.atomic
def registrar_parte(obra, semana, horas, usuario, trabajador=None, persona='', entregable=None, descripcion='',
                    costo=None):
    horas = Decimal(horas or 0)
    if horas <= 0:
        raise ErrorServicio('Indique las horas trabajadas.')
    if trabajador is None and not (persona or '').strip():
        raise ErrorServicio('Indique el trabajador o el nombre del especialista independiente.')
    semana = lunes(semana or timezone.localdate())
    if semana > lunes(timezone.localdate()):
        raise ErrorServicio('No se registran horas de semanas futuras.')
    if entregable is not None and entregable.obra_id != obra.pk:
        raise ErrorServicio('El entregable es de otro proyecto.')
    otras = ParteHoras.objects.filter(semana=semana).exclude(estado='RECHAZADO')
    otras = otras.filter(trabajador=trabajador) if trabajador else otras.filter(persona__iexact=persona.strip())
    total = (otras.aggregate(s=Sum('horas'))['s'] or D0) + horas
    if total > MAX_HORAS_SEMANA:
        raise ErrorServicio(f'{trabajador or persona} sumaría {total:g} horas en la semana del {semana:%d/%m} '
                            f'(máximo {MAX_HORAS_SEMANA:g}).')
    if costo is None or costo == '':
        costo = costo_hora(trabajador, semana)
    return ParteHoras.objects.create(obra=obra, semana=semana, horas=horas, trabajador=trabajador,
                                     persona=(persona or '').strip()[:120], entregable=entregable,
                                     descripcion=(descripcion or '')[:250], costo_hora=Decimal(costo or 0),
                                     estado='ENVIADO', registrado_por=_usuario(usuario))


def aprobar_parte(parte, usuario, aprobar=True, motivo=''):
    if parte.estado != 'ENVIADO':
        raise ErrorServicio('Solo se aprueban partes enviados.')
    if not puede_aprobar_horas(usuario, parte.obra):
        raise ErrorServicio(f'Lo aprueba el jefe de proyecto ({parte.obra.residente or "sin asignar"}).')
    if not aprobar and len((motivo or '').strip()) < 5:
        raise ErrorServicio('Indique por qué se rechaza.')
    parte.estado = 'APROBADO' if aprobar else 'RECHAZADO'
    parte.motivo_rechazo = '' if aprobar else motivo.strip()[:200]
    parte.aprobado_por, parte.aprobado_en = _usuario(usuario), timezone.now()
    parte.save(update_fields=['estado', 'motivo_rechazo', 'aprobado_por', 'aprobado_en'])
    return parte


def horas_proyecto(obra):
    """Horas y costo presupuestados frente a los reales (partes aprobados), por entregable y por persona."""
    partes = list(obra.partes_horas.filter(estado='APROBADO').select_related('trabajador', 'entregable'))
    por_entregable, por_persona = {}, {}
    for p in partes:
        e = por_entregable.setdefault(p.entregable_id, {'nombre': str(p.entregable) if p.entregable else 'Sin entregable',
                                                        'presupuestadas': p.entregable.horas_presupuestadas
                                                        if p.entregable else D0, 'horas': D0, 'costo': D0})
        e['horas'] += p.horas
        e['costo'] += p.costo
        x = por_persona.setdefault(p.nombre_persona, {'horas': D0, 'costo': D0})
        x['horas'] += p.horas
        x['costo'] += p.costo
    for ent in obra.entregables.all():
        por_entregable.setdefault(ent.pk, {'nombre': str(ent), 'presupuestadas': ent.horas_presupuestadas,
                                           'horas': D0, 'costo': D0})
    horas = sum((p.horas for p in partes), D0)
    return {'entregables': list(por_entregable.values()), 'personas': por_persona, 'horas': horas,
            'costo': sum((p.costo for p in partes), D0), 'presupuestadas': obra.horas_presupuestadas,
            'avance_horas': (horas * 100 / obra.horas_presupuestadas).quantize(Decimal('0.1'))
            if obra.horas_presupuestadas else None}


# ---------------------------------------------------------------- entregables (SRV-01)
def nuevo_entregable(obra, **datos):
    ultimo = obra.entregables.order_by('-numero').values_list('numero', flat=True).first() or 0
    e = EntregableProyecto(obra=obra, numero=ultimo + 1, **datos)
    e.full_clean(exclude=['factura'])
    e.save()
    return e


def entregar(e, fecha=None):
    if e.estado not in ('PENDIENTE', 'OBSERVADO'):
        raise ErrorServicio('El entregable ya se entregó.')
    e.fecha_entrega = fecha or timezone.localdate()
    e.estado = 'ENTREGADO'
    e.penalidad = penalidad(e)
    e.save(update_fields=['fecha_entrega', 'estado', 'penalidad'])
    return e


def observar(e, observacion):
    if e.estado != 'ENTREGADO':
        raise ErrorServicio('Solo se observa un entregable entregado.')
    if len((observacion or '').strip()) < 5:
        raise ErrorServicio('Indique la observación del cliente.')
    e.estado, e.observacion = 'OBSERVADO', observacion.strip()[:250]
    e.save(update_fields=['estado', 'observacion'])
    return e


def conformidad(e, por, fecha=None):
    if e.estado != 'ENTREGADO':
        raise ErrorServicio('La conformidad se da a un entregable entregado (y sin observaciones pendientes).')
    if len((por or '').strip()) < 3:
        raise ErrorServicio('Indique quién dio la conformidad (nombre y cargo del cliente).')
    e.estado, e.conformidad_por, e.conformidad_fecha = 'CONFORME', por.strip()[:150], fecha or timezone.localdate()
    e.save(update_fields=['estado', 'conformidad_por', 'conformidad_fecha'])
    return e


def penalidad(e):
    """Penalidad por atraso: % diario del monto por cada día de atraso, con tope (contrato)."""
    if not e.dias_atraso:
        return D0
    bruta = e.valor * e.penalidad_diaria_pct / 100 * e.dias_atraso
    return min(bruta, e.valor * e.penalidad_maxima_pct / 100).quantize(Decimal('0.01'))


def producto_servicio():
    """Servicio «Servicios profesionales del proyecto» (ventas de servicios, detracción 022 al SRV_DETRACCION_PCT)."""
    from contabilidad.models import CuentaDefecto
    from core.models import Producto
    p = Producto.objects.filter(codigo='SRVPROY').first()
    if p is None:
        p = Producto(codigo='SRVPROY', nombre='Servicios profesionales del proyecto', clase='SERVICIO', unidad='ZZ',
                     puede_comprarse=False)
        p.cuenta_venta = CuentaDefecto.mapa().get('ventas_servicios')
        p.detraccion_pct = _param('SRV_DETRACCION_PCT')
        p.detraccion_codigo = '022'
        p.save()
    return p


@transaction.atomic
def facturar(e, usuario, fecha=None):
    """Factura del entregable conforme: su valor y la deducción proporcional del adelanto (regularización del
    anticipo), como las valorizaciones."""
    from . import valorizaciones as val
    if e.estado != 'CONFORME':
        raise ErrorServicio('Se factura el entregable con la conformidad del cliente.')
    fecha = fecha or timezone.localdate()
    obra = e.obra
    try:
        val._validar_periodo(fecha)
    except val.ErrorValorizacion as exc:
        raise ErrorServicio(str(exc)) from exc
    lineas = [(producto_servicio(), f'Entregable {e.numero}: {e.nombre} – {obra.nombre}', e.valor, None)]
    total_adelantos = sum((a.monto for a in val.adelantos(obra, 'DIRECTO')), D0)
    contrato = sum((x.valor for x in obra.entregables.all()), D0)
    amortizar = (total_adelantos * e.valor / contrato).quantize(Decimal('0.01')) if contrato else D0
    for a in val.adelantos(obra, 'DIRECTO').order_by('fecha', 'id'):
        if amortizar <= 0:
            break
        parte = min(amortizar, a.saldo)
        if parte > 0:
            lineas.append((val.producto_adelanto(), f'Deducción del anticipo {a.factura.numero_completo}', -parte,
                           a.factura))
            amortizar -= parte
    try:
        comp = val._factura(obra, fecha, f'Entregable {e.numero}: {e.nombre} – {obra.nombre}'
                            + (f' – contrato {obra.contrato}' if obra.contrato else ''), lineas)
    except val.ErrorValorizacion as exc:
        raise ErrorServicio(str(exc)) from exc
    e.factura, e.estado = comp, 'FACTURADO'
    e.save(update_fields=['factura', 'estado'])
    return comp


# ---------------------------------------------------------------- cierre del mes: servicios en proceso (SRV-06)
def _fin_de(periodo):
    from datetime import date
    inicio = date(int(periodo[:4]), int(periodo[4:]), 1)
    return (inicio + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def situacion(obra, periodo):
    """Costo acumulado (horas aprobadas y compras del centro de costo), lo facturado, el avance por costo y el
    ingreso por avance aún no facturado al cierre del mes."""
    from compras.models import Compra
    fin = _fin_de(periodo)
    horas = sum((p.costo for p in obra.partes_horas.filter(estado='APROBADO', semana__lte=fin)), D0)
    compras = D0
    if obra.centro_costo_id:
        for c in Compra.objects.filter(centro_costo_id=obra.centro_costo_id, estado='REGISTRADO',
                                       fecha_emision__lte=fin).exclude(tipo_comprobante__in=['07', '08']):
            base = (c.base_imponible or D0) + (c.no_gravado or D0)
            compras += base * (c.tipo_cambio or 1) if c.moneda == 'USD' else base
    costo = (horas + compras).quantize(Decimal('0.01'))
    from core.models import Empresa
    igv = 1 + Empresa.actual().igv_tasa / 100
    contrato = ((obra.monto_contratado / igv).quantize(Decimal('0.01')) if obra.monto_contratado else
                sum((e.valor for e in obra.entregables.all()), D0))
    facturado = sum((e.valor for e in obra.entregables.filter(estado='FACTURADO', factura__fecha_emision__lte=fin)),
                    D0)
    costo_hora_prom = (horas / sum((p.horas for p in obra.partes_horas.filter(estado='APROBADO')), D0)
                       if obra.partes_horas.filter(estado='APROBADO').exists() else D0)
    presupuesto = obra.horas_presupuestadas * costo_hora_prom + obra.gastos_presupuestados
    avance = min(costo / presupuesto, Decimal('1')) if presupuesto else None
    costo_facturado = (costo * facturado / contrato).quantize(Decimal('0.01')) if contrato else D0
    proceso = max(costo - costo_facturado, D0)
    devengado = max((contrato * avance).quantize(Decimal('0.01')) - facturado, D0) if avance is not None else D0
    return {'obra': obra, 'costo': costo, 'horas': horas, 'compras': compras, 'contrato': contrato,
            'facturado': facturado, 'avance': (avance * 100).quantize(Decimal('0.1')) if avance is not None else None,
            'costo_proceso': proceso, 'ingreso_devengado': devengado, 'presupuesto': presupuesto}


def proyectos_de_servicio():
    return Obra.objects.filter(tipo='SERVICIO').exclude(estado__in=['CERRADA', 'NO_ADJUDICADA'])


def _cuenta(candidatos, nombre):
    from contabilidad.models import CuentaContable
    for codigo in candidatos:
        c = CuentaContable.objects.filter(codigo=codigo, imputable=True).first()
        if c is not None:
            return c
    return CuentaContable.objects.create(codigo=candidatos[0], nombre=nombre)


@transaction.atomic
def registrar_cierre(periodo, usuario):
    """Asiento del mes con la variación del servicio en proceso (Debe 2351 / Haber 7152) y del ingreso por avance
    (Debe 1211 facturas por emitir / Haber ventas de servicios), por la diferencia con el cierre anterior."""
    from contabilidad.models import Asiento, CuentaDefecto, PeriodoContable
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorServicio(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado.')
    if CierreServicioProyecto.objects.filter(periodo=periodo, asiento__isnull=False).exists():
        raise ErrorServicio('El cierre de servicios de este mes ya se registró.')
    proceso_cta = _cuenta(['2351', '235', '2311'], 'Existencias de servicios en proceso')
    variacion_cta = _cuenta(['7152', '715', '7131'], 'Variación de existencias de servicios en proceso')
    por_emitir = _cuenta(['1211', '121'], 'Facturas por cobrar no emitidas')
    ingreso = CuentaDefecto.mapa().get('ventas_servicios') or _cuenta(['7041'], 'Prestación de servicios')
    fin = _fin_de(periodo)
    filas = []
    for obra in proyectos_de_servicio():
        s = situacion(obra, periodo)
        anterior = CierreServicioProyecto.objects.filter(obra=obra, periodo__lt=periodo).order_by('-periodo').first()
        d_proceso = s['costo_proceso'] - (anterior.costo_proceso if anterior else D0)
        d_ingreso = s['ingreso_devengado'] - (anterior.ingreso_devengado if anterior else D0)
        filas.append((obra, s, d_proceso, d_ingreso))
    if not any(d1 or d2 for _, _, d1, d2 in filas):
        raise ErrorServicio('No hay variación del servicio en proceso ni del ingreso por avance que registrar.')
    asiento = Asiento.objects.create(fecha=fin, libro='05', origen='MANUAL',
                                     glosa=f'Servicios por proyecto en proceso e ingreso por avance (NIIF 15) '
                                           f'{periodo[4:]}/{periodo[:4]}')
    for obra, s, d_proceso, d_ingreso in filas:
        cc = obra.centro_costo
        for cuenta, debe in ((proceso_cta, d_proceso), (variacion_cta, -d_proceso), (por_emitir, d_ingreso),
                             (ingreso, -d_ingreso)):
            if debe:
                asiento.lineas.create(cuenta=cuenta, debe=max(debe, D0), haber=max(-debe, D0), centro_costo=cc,
                                      glosa=f'{obra.codigo} {obra.nombre}'[:200])
        CierreServicioProyecto.objects.update_or_create(obra=obra, periodo=periodo, defaults={
            'costo_proceso': s['costo_proceso'], 'ingreso_devengado': s['ingreso_devengado'], 'asiento': asiento})
    return asiento
