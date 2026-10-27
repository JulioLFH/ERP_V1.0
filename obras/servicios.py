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
    if trabajador is not None and (persona or '').strip():  # HR-21: no se ignora uno de los dos en silencio
        raise ErrorServicio('Indique el trabajador en planilla o el especialista independiente, no los dos.')
    semana = lunes(semana or timezone.localdate())
    if semana > lunes(timezone.localdate()):
        raise ErrorServicio('No se registran horas de semanas futuras.')
    inicio = obra.fecha_inicio or obra.fecha_contrato
    if semana.year < 2000 or (inicio and semana < lunes(inicio)):  # HR-21: la semana de 1900
        raise ErrorServicio(f'La semana del {semana:%d/%m/%Y} es anterior al inicio del proyecto'
                            + (f' ({inicio:%d/%m/%Y}).' if inicio else '.'))
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
    for ent in vigentes(obra):
        por_entregable.setdefault(ent.pk, {'nombre': str(ent), 'presupuestadas': ent.horas_presupuestadas,
                                           'horas': D0, 'costo': D0})
    horas = sum((p.horas for p in partes), D0)
    return {'entregables': list(por_entregable.values()), 'personas': por_persona, 'horas': horas,
            'costo': sum((p.costo for p in partes), D0), 'presupuestadas': obra.horas_presupuestadas,
            'avance_horas': (horas * 100 / obra.horas_presupuestadas).quantize(Decimal('0.1'))
            if obra.horas_presupuestadas else None}


# ---------------------------------------------------------------- entregables (SRV-01)
def vigentes(obra):
    """Entregables del contrato (sin los anulados)."""
    return obra.entregables.exclude(estado='ANULADO')


def contrato_sin_igv(obra):
    """Monto del contrato sin IGV (el monto contratado; si no se registró, la suma de los entregables)."""
    from core.models import Empresa
    if obra.monto_contratado:
        return (obra.monto_contratado / (1 + Empresa.actual().igv_tasa / 100)).quantize(Decimal('0.01'))
    return sum((e.valor for e in vigentes(obra)), D0)


def fin_de_plazo(obra):
    inicio = obra.fecha_inicio or obra.fecha_contrato
    return inicio + timedelta(days=obra.plazo_dias) if inicio and obra.plazo_dias else None


def validar_entregable(e, autorizar_exceso=False, usuario=None, motivo=''):
    """SRV-08: los entregables no pasan el contrato (% y monto); avisa si las horas superan las del proyecto o la
    fecha comprometida el plazo. Exceder el contrato solo con autorización (permiso «aprobar» de Obras y motivo).
    Devuelve los avisos."""
    from core.permisos import puede
    obra = e.obra
    otros = vigentes(obra).exclude(pk=e.pk)
    pct = sum((x.porcentaje for x in otros), D0) + (e.porcentaje or D0)
    total = sum((x.valor for x in otros), D0) + e.valor
    contrato = contrato_sin_igv(obra) if obra.monto_contratado else None
    excesos = []
    if pct > 100:
        excesos.append(f'los entregables sumarían {pct.normalize():f} % del contrato')
    if contrato is not None and total > contrato:
        excesos.append(f'sumarían S/ {total:,.2f} contra S/ {contrato:,.2f} del contrato (sin IGV)')
    if excesos:
        if not autorizar_exceso:
            raise ErrorServicio('No se registra: ' + ' y '.join(excesos) + '. Ajuste los entregables o registre un '
                                'adicional al contrato (o autorice el exceso con su motivo).')
        if not (usuario is not None and (usuario.is_superuser or puede(usuario, 'obras.aprobar'))):
            raise ErrorServicio('Exceder el contrato lo autoriza quien aprueba en Obras (permiso «aprobar»).')
        if len((motivo or '').strip()) < 10:
            raise ErrorServicio('Indique el motivo del exceso (adenda, adicional aprobado por el cliente…).')
    avisos = []
    horas = sum((x.horas_presupuestadas for x in otros), D0) + (e.horas_presupuestadas or D0)
    if obra.horas_presupuestadas and horas > obra.horas_presupuestadas:
        avisos.append(f'Las horas de los entregables ({horas:g}) superan las del proyecto '
                      f'({obra.horas_presupuestadas:g}).')
    fin = fin_de_plazo(obra)
    if fin and e.fecha_comprometida and e.fecha_comprometida > fin:
        avisos.append(f'La fecha comprometida ({e.fecha_comprometida:%d/%m/%Y}) pasa el plazo del contrato '
                      f'({fin:%d/%m/%Y}).')
    return avisos


def nuevo_entregable(obra, autorizar_exceso=False, usuario=None, motivo='', **datos):
    ultimo = obra.entregables.order_by('-numero').values_list('numero', flat=True).first() or 0
    if datos.get('monto') is None:
        datos['monto'] = D0
    e = EntregableProyecto(obra=obra, numero=ultimo + 1, **datos)
    e.full_clean(exclude=['factura'])
    e.avisos = validar_entregable(e, autorizar_exceso, usuario, motivo)
    e.save()
    return e


def editar_entregable(e, datos, autorizar_exceso=False, usuario=None, motivo=''):
    """Un entregable pendiente u observado se corrige (nombre, %, monto, fecha, horas, penalidades)."""
    if e.estado not in ('PENDIENTE', 'OBSERVADO'):
        raise ErrorServicio('Solo se edita un entregable pendiente u observado.')
    for campo, valor in datos.items():
        setattr(e, campo, D0 if campo == 'monto' and valor is None else valor)
    e.full_clean(exclude=['factura'])
    e.avisos = validar_entregable(e, autorizar_exceso, usuario, motivo)
    e.save()
    return e


def anular_entregable(e, motivo):
    if e.estado not in ('PENDIENTE', 'OBSERVADO'):
        raise ErrorServicio('Solo se anula un entregable pendiente u observado (uno entregado o facturado no).')
    if len((motivo or '').strip()) < 5:
        raise ErrorServicio('Indique por qué se anula el entregable.')
    e.estado, e.observacion = 'ANULADO', f'Anulado: {motivo.strip()}'[:250]
    e.save(update_fields=['estado', 'observacion'])
    e.partes.update(entregable=None)  # las horas quedan en el proyecto
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
    # SRV-08: el adelanto se amortiza en proporción al contrato (no a la suma de los entregables, que puede pasarlo)
    # y el último entregable por facturar amortiza todo el saldo
    contrato = contrato_sin_igv(obra)
    ultimo = not vigentes(obra).exclude(pk=e.pk).exclude(estado='FACTURADO').exists()
    saldo = sum((a.saldo for a in val.adelantos(obra, 'DIRECTO')), D0)
    amortizar = saldo if ultimo else (
        min((total_adelantos * e.valor / contrato).quantize(Decimal('0.01')), saldo) if contrato else D0)
    for a in val.adelantos(obra, 'DIRECTO').order_by('fecha', 'id'):
        if amortizar <= 0:
            break
        parte = min(amortizar, a.saldo)
        if parte > 0:
            lineas.append((val.producto_adelanto(obra), f'Deducción del anticipo {a.factura.numero_completo}', -parte,
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
    contrato = contrato_sin_igv(obra)
    facturados = {e.pk for e in vigentes(obra).filter(estado='FACTURADO', factura__fecha_emision__lte=fin)}
    facturado = sum((e.valor for e in vigentes(obra) if e.pk in facturados), D0)
    costo_hora_prom = (horas / sum((p.horas for p in obra.partes_horas.filter(estado='APROBADO')), D0)
                       if obra.partes_horas.filter(estado='APROBADO').exists() else D0)
    presupuesto = obra.horas_presupuestadas * costo_hora_prom + obra.gastos_presupuestados
    avance = min(costo / presupuesto, Decimal('1')) if presupuesto else None
    # SRV-13: el costo de las horas cargadas a un entregable queda en proceso hasta que ese entregable se factura; lo
    # que no tiene entregable (horas generales y compras del centro) se reparte por el % facturado del contrato
    por_entregable, sin_entregable = {}, compras
    for p in obra.partes_horas.filter(estado='APROBADO', semana__lte=fin).select_related('entregable'):
        if p.entregable_id and p.entregable.estado != 'ANULADO':
            por_entregable[p.entregable_id] = por_entregable.get(p.entregable_id, D0) + p.costo
        else:
            sin_entregable += p.costo
    detalle = []
    for e in vigentes(obra):
        c = por_entregable.get(e.pk, D0)
        detalle.append({'entregable': e, 'costo': c, 'facturado': e.pk in facturados,
                        'en_proceso': D0 if e.pk in facturados else c})
    general_facturado = (sin_entregable * facturado / contrato).quantize(Decimal('0.01')) if contrato else D0
    proceso = (sum((d['en_proceso'] for d in detalle), D0) + max(sin_entregable - general_facturado, D0)
               ).quantize(Decimal('0.01'))
    devengado = max((contrato * avance).quantize(Decimal('0.01')) - facturado, D0) if avance is not None else D0
    return {'obra': obra, 'costo': costo, 'horas': horas, 'compras': compras, 'contrato': contrato,
            'facturado': facturado, 'avance': (avance * 100).quantize(Decimal('0.1')) if avance is not None else None,
            'costo_proceso': proceso, 'ingreso_devengado': devengado, 'presupuesto': presupuesto,
            'detalle': detalle, 'costo_general': sin_entregable.quantize(Decimal('0.01')),
            'general_en_proceso': max(sin_entregable - general_facturado, D0).quantize(Decimal('0.01'))}


def proyectos_de_servicio():
    return Obra.objects.filter(tipo='SERVICIO').exclude(estado__in=['CERRADA', 'NO_ADJUDICADA'])


def _cuenta(candidatos, nombre):
    """Cuenta imputable del código (en el plan de la empresa) para el asiento (SRV-09): la misma si acepta
    movimientos; si es de agrupación, su primera subcuenta imputable (o se crea la «…1»); si no existe, se crea. Antes
    buscaba solo imputables y, al no hallarla, intentaba crear un código que ya existía (error UNIQUE)."""
    from contabilidad import pcge
    from contabilidad.models import CuentaContable
    base = pcge.codigo(candidatos[0])
    c = CuentaContable.objects.filter(codigo=base).first()
    if c is None:
        return CuentaContable.objects.create(codigo=base, nombre=nombre)
    if c.imputable:
        return c
    hija = (CuentaContable.objects.filter(codigo__startswith=base, imputable=True).exclude(pk=c.pk)
            .order_by('codigo').first())
    if hija is not None:
        return hija
    hija, _ = CuentaContable.objects.get_or_create(codigo=f'{base}1', defaults={'nombre': nombre})
    return hija


@transaction.atomic
def distribuir_horas(periodo, usuario=None):
    """SRV-10: asiento del mes que lleva el costo de las horas aprobadas del personal en planilla (horas × costo hora)
    del centro de costo del trabajador al del proyecto (Debe y Haber en la cuenta de remuneraciones 6211), para que el
    resultado por proyecto muestre su costo. Los especialistas independientes llegan con su recibo por honorarios
    registrado con el centro de costo del proyecto."""
    from contabilidad.models import Asiento, PeriodoContable
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorServicio(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado.')
    desde, fin = _fin_de(periodo).replace(day=1), _fin_de(periodo)
    partes = list(ParteHoras.objects.filter(estado='APROBADO', semana__range=(desde, fin), trabajador__isnull=False,
                                            asiento_distribucion__isnull=True, obra__centro_costo__isnull=False)
                  .select_related('obra__centro_costo', 'trabajador__centro_costo'))
    sin_centro = sorted({str(p.trabajador) for p in partes if not p.trabajador.centro_costo_id})
    if sin_centro:
        raise ErrorServicio('Asigne el centro de costo de la planilla a: ' + ', '.join(sin_centro) + '.')
    partes = [p for p in partes if p.trabajador.centro_costo_id != p.obra.centro_costo_id and p.costo]
    if not partes:
        raise ErrorServicio('No hay horas aprobadas del mes por distribuir a los proyectos.')
    cuenta = _cuenta(['6211', '621'], 'Sueldos y salarios')
    asiento = Asiento.objects.create(fecha=fin, libro='05', origen='MANUAL',
                                     glosa=f'Distribución del costo de horas a proyectos {periodo[4:]}/{periodo[:4]}')
    grupos = {}
    for p in partes:
        clave = (p.obra.centro_costo, p.trabajador.centro_costo, p.obra)
        grupos[clave] = grupos.get(clave, D0) + p.costo
    for (destino, origen, obra), monto in grupos.items():
        asiento.lineas.create(cuenta=cuenta, debe=monto, haber=D0, centro_costo=destino,
                              glosa=f'Horas del personal en {obra.codigo} {obra.nombre}'[:200])
        asiento.lineas.create(cuenta=cuenta, debe=D0, haber=monto, centro_costo=origen,
                              glosa=f'Horas del personal en {obra.codigo} {obra.nombre}'[:200])
    ParteHoras.objects.filter(pk__in=[p.pk for p in partes]).update(asiento_distribucion=asiento)
    return asiento


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
