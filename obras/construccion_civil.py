"""Mano de obra de la obra (OBR-18): tareo diario por cuadrilla y partida, planilla semanal del régimen de
construcción civil y control de horas-hombre de la meta (APU) contra las reales.

Planilla semanal por trabajador (jornales, porcentajes y montos vigentes en Ajustes › Parámetros, grupo «Construcción
civil»; confírmelos con el especialista laboral):
- Básico = jornal de la categoría × días trabajados; dominical = un jornal por semana completa (proporcional a 6 días);
  BUC = % del básico según la categoría; horas extra con sus sobretasas sobre el valor hora (jornal / 8); movilidad
  por día trabajado (no remunerativa).
- Descuentos: ONP o AFP sobre la remuneración (básico, dominical, BUC y horas extra) y CONAFOVICER sobre el básico.
- Aportes y provisiones (costo de la obra): EsSalud y SCTR sobre la remuneración; CTS, vacaciones y gratificaciones
  sobre básico y dominical.
Contabilidad (al centralizar el mes del fin de la semana): gasto de personal con el centro de costo de la obra (y sus
destinos), retenciones y aportes por pagar, provisiones (CTS, vacaciones, gratificaciones) y el neto a 4111. El costo se
reparte a las partidas por las horas del tareo."""
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import LineaPlanillaCivil, LineaTareo, PlanillaCivil, RecursoAPU, Tareo

D0 = Decimal('0')
JORNADA = Decimal('8')
CATEGORIAS = ('OPERARIO', 'OFICIAL', 'PEON')


class ErrorCivil(Exception):
    pass


def r2(v):
    return Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _p(clave, fecha):
    from core import parametros
    return parametros.valor(clave, fecha)


def tasas(fecha):
    """Jornal y BUC por categoría y los porcentajes vigentes a la fecha."""
    return {
        'jornal': {c: _p(f'CC_JORNAL_{c}', fecha) for c in CATEGORIAS},
        'buc': {c: _p(f'CC_BUC_{c}_PCT', fecha) for c in CATEGORIAS},
        'movilidad': _p('CC_MOVILIDAD_DIA', fecha), 'he1': _p('CC_HE_PRIMERAS_PCT', fecha),
        'he2': _p('CC_HE_SIGUIENTES_PCT', fecha), 'conafovicer': _p('CC_CONAFOVICER_PCT', fecha),
        'essalud': _p('CC_ESSALUD_PCT', fecha), 'sctr': _p('CC_SCTR_PCT', fecha), 'cts': _p('CC_CTS_PCT', fecha),
        'vacaciones': _p('CC_VACACIONES_PCT', fecha), 'gratificacion': _p('CC_GRATIFICACION_PCT', fecha),
    }


# ---------------------------------------------------------------- tareo
def trabajadores_obra():
    from planillas.models import Trabajador
    return Trabajador.objects.filter(regimen='CONSTRUCCION', fecha_cese__isnull=True).order_by(
        'categoria_civil', 'apellido_paterno', 'nombres')


@transaction.atomic
def registrar_tareo(obra, fecha, cuadrilla, filas, usuario, capataz=None, observaciones=''):
    """filas: [(trabajador, partida o None, horas, horas_60, horas_100)]. Un trabajador no pasa de 24 horas en el
    día sumando sus tareos de la obra, ni de 8 horas normales (HR-21: la jornada de construcción civil; el exceso va
    como horas extra). Las partidas son las de la meta (OBR-36). El tareo devuelto lleva `avisos` (el trabajador ya
    tenía otro tareo ese día)."""
    from planillas.models import Trabajador

    from .ejecucion import ErrorEjecucion, validar_fecha_obra
    from .valorizaciones import presupuesto_meta
    fecha = fecha or timezone.localdate()
    try:
        validar_fecha_obra(obra, fecha, 'El tareo')
    except ErrorEjecucion as exc:
        raise ErrorCivil(str(exc)) from exc
    if len(observaciones or '') > 250:
        raise ErrorCivil(f'Las observaciones tienen {len(observaciones)} caracteres: el máximo es 250.')
    filas = [f for f in filas if f[0] is not None and (f[2] or f[3] or f[4])]
    if not filas:
        raise ErrorCivil('Indique las horas de al menos un trabajador.')
    meta = presupuesto_meta(obra)
    errores, avisos = [], []
    del_dia, normales = defaultdict(lambda: D0), defaultdict(lambda: D0)
    for t, partida, horas, h60, h100 in filas:
        if not isinstance(t, Trabajador) or t.regimen != 'CONSTRUCCION' or not t.categoria_civil:
            errores.append(f'{t}: no es trabajador de construcción civil con categoría.')
        if partida is not None and partida.presupuesto.obra_id != obra.pk:
            errores.append(f'La partida {partida} no es de la obra.')
        elif partida is not None and meta is not None and partida.presupuesto_id != meta.pk:
            errores.append(f'La partida {partida.item} es de {partida.presupuesto}: el tareo va a las partidas de '
                           f'{meta}.')
        if min(horas or 0, h60 or 0, h100 or 0) < 0:
            errores.append(f'{t}: las horas no pueden ser negativas.')
        del_dia[t] += (horas or 0) + (h60 or 0) + (h100 or 0)
        normales[t] += horas or 0
    for t, horas in del_dia.items():
        previas = list(LineaTareo.objects.filter(trabajador=t, tareo__fecha=fecha).select_related('tareo__obra'))
        total = sum((l.horas + l.horas_60 + l.horas_100 for l in previas), D0) + horas
        normal = sum((l.horas for l in previas), D0) + normales[t]
        if total > 24:
            errores.append(f'{t.nombre_completo}: más de 24 horas el {fecha:%d/%m/%Y}.')
        elif normal > JORNADA:
            errores.append(f'{t.nombre_completo}: {normal:g} horas normales el {fecha:%d/%m/%Y}; la jornada es de '
                           f'{JORNADA:g} horas: registre el exceso como horas extra (60 % o 100 %).')
        if previas:
            avisos.append(f'{t.nombre_completo} ya tenía {len({l.tareo_id for l in previas})} tareo(s) el '
                          f'{fecha:%d/%m/%Y} ({total:g} h en el día).')
    if errores:
        raise ErrorCivil(' '.join(errores[:6]))
    tareo = Tareo.objects.create(obra=obra, fecha=fecha, cuadrilla=(cuadrilla or '')[:60], capataz=capataz,
                                 observaciones=(observaciones or '')[:250],
                                 registrado_por=usuario if usuario and usuario.is_authenticated else None)
    for t, partida, horas, h60, h100 in filas:
        LineaTareo.objects.create(tareo=tareo, trabajador=t, partida=partida, horas=horas or 0, horas_60=h60 or 0,
                                  horas_100=h100 or 0)
    tareo.avisos = avisos
    return tareo


# ---------------------------------------------------------------- planilla semanal
def semana_de(fecha):
    """(lunes, domingo) de la semana de la fecha."""
    lunes = fecha - timedelta(days=fecha.weekday())
    return lunes, lunes + timedelta(days=6)


@transaction.atomic
def nueva_planilla(obra, fecha):
    hoy = timezone.localdate()
    desde, hasta = semana_de(fecha or hoy)
    if desde > hoy:  # HR-21: ni semanas futuras ni planillas vacías
        raise ErrorCivil(f'La semana del {desde:%d/%m/%Y} todavía no empieza.')
    if PlanillaCivil.objects.filter(obra=obra, desde=desde).exclude(estado='ANULADA').exists():
        raise ErrorCivil(f'La semana del {desde:%d/%m/%Y} ya tiene planilla.')
    if not LineaTareo.objects.filter(tareo__obra=obra, tareo__fecha__range=(desde, hasta)).exists():
        raise ErrorCivil(f'La semana del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y} no tiene tareos: registre primero el '
                         f'tareo diario.')
    p = PlanillaCivil.objects.create(obra=obra, desde=desde, hasta=hasta)
    calcular(p)
    return p


def _pension(trabajador, remuneracion, fecha):
    """Aporte del trabajador a la ONP o a su AFP (aporte, prima con tope y comisión sobre el flujo)."""
    if trabajador.sistema_pensiones == 'ONP':
        from planillas.models import Parametro
        try:
            pct = Parametro.del_anio(fecha.year).onp_pct
        except ValueError:
            pct = Decimal('13')
        return r2(remuneracion * pct / 100)
    if trabajador.sistema_pensiones == 'AFP' and trabajador.afp_id:
        afp = trabajador.afp
        comision = afp.comision_flujo_pct if trabajador.comision_afp == 'FLUJO' else afp.comision_mixta_pct
        return r2(remuneracion * (afp.aporte_pct + comision) / 100 + remuneracion * afp.prima_pct / 100)
    return D0


@transaction.atomic
def calcular(planilla):
    """Recalcula la planilla desde los tareos de la semana."""
    if planilla.estado != 'BORRADOR':
        raise ErrorCivil('Solo se calcula una planilla en borrador.')
    t = tasas(planilla.hasta)
    if not all(t['jornal'].values()):
        raise ErrorCivil('Registre los jornales vigentes de construcción civil (Ajustes › Parámetros).')
    datos = defaultdict(lambda: {'dias': set(), 'horas': D0, 'h60': D0, 'h100': D0, 'partidas': defaultdict(
        lambda: D0), 'por_dia': defaultdict(lambda: D0)})
    for l in LineaTareo.objects.filter(tareo__obra=planilla.obra, tareo__fecha__range=(planilla.desde, planilla.hasta)
                                       ).select_related('trabajador__afp', 'tareo'):
        d = datos[l.trabajador]
        d['horas'] += l.horas
        d['h60'] += l.horas_60
        d['h100'] += l.horas_100
        d['por_dia'][l.tareo.fecha] += l.horas
        d['partidas'][str(l.partida_id) if l.partida_id else ''] += l.horas + l.horas_60 + l.horas_100
    planilla.lineas.all().delete()
    for trab, d in datos.items():
        cat = trab.categoria_civil or 'PEON'
        jornal = t['jornal'][cat]
        dias = sum((min(h, JORNADA) / JORNADA for h in d['por_dia'].values()), D0).quantize(Decimal('0.01'))
        basico = r2(jornal * dias)
        dominical = r2(jornal * min(dias, Decimal('6')) / 6)
        buc = r2(basico * t['buc'][cat] / 100)
        hora = jornal / JORNADA
        extras = r2(hora * d['h60'] * (1 + t['he1'] / 100) + hora * d['h100'] * (1 + t['he2'] / 100))
        movilidad = r2(t['movilidad'] * dias)
        remuneracion = basico + dominical + buc + extras
        pension = _pension(trab, remuneracion, planilla.hasta)
        conafovicer = r2(basico * t['conafovicer'] / 100)
        jornales = basico + dominical
        l = LineaPlanillaCivil(
            planilla=planilla, trabajador=trab, categoria=cat, dias=dias, horas=d['horas'], horas_60=d['h60'],
            horas_100=d['h100'], jornal=jornal, basico=basico, dominical=dominical, buc=buc, extras=extras,
            movilidad=movilidad, pension=pension, conafovicer=conafovicer,
            neto=remuneracion + movilidad - pension - conafovicer, essalud=r2(remuneracion * t['essalud'] / 100),
            sctr=r2(remuneracion * t['sctr'] / 100), cts=r2(jornales * t['cts'] / 100),
            vacaciones=r2(jornales * t['vacaciones'] / 100), gratificacion=r2(jornales * t['gratificacion'] / 100),
            partidas={k: str(v) for k, v in d['partidas'].items()})
        l.costo = remuneracion + movilidad + l.essalud + l.sctr + l.cts + l.vacaciones + l.gratificacion
        l.save()
    return planilla


def totales(planilla):
    campos = ('basico', 'dominical', 'buc', 'extras', 'movilidad', 'pension', 'conafovicer', 'neto', 'essalud', 'sctr',
              'cts', 'vacaciones', 'gratificacion', 'costo')
    lineas = list(planilla.lineas.all())
    return {c: sum((getattr(l, c) for l in lineas), D0) for c in campos}


def _marcar(planilla):
    from contabilidad.automatico import marcar_pendiente
    marcar_pendiente(planilla.periodo, posteriores=False)


@transaction.atomic
def cerrar(planilla):
    if planilla.estado != 'BORRADOR':
        raise ErrorCivil('La planilla está anulada.' if planilla.estado == 'ANULADA' else
                         f'La planilla ya está {planilla.get_estado_display().lower()}.')
    calcular(planilla)
    if not planilla.lineas.exists():
        raise ErrorCivil('La semana no tiene tareos de trabajadores de construcción civil.')
    if planilla.obra.centro_costo_id is None:
        raise ErrorCivil('La obra no tiene centro de costo.')
    planilla.estado = 'CERRADA'
    planilla.save(update_fields=['estado'])
    _marcar(planilla)
    return planilla


@transaction.atomic
def reabrir(planilla):
    from contabilidad.models import PeriodoContable
    if planilla.estado != 'CERRADA':
        raise ErrorCivil('Solo se reabre una planilla cerrada sin pagar.')
    if PeriodoContable.esta_cerrado(planilla.periodo):
        raise ErrorCivil(f'El periodo contable {planilla.periodo} está cerrado.')
    planilla.estado = 'BORRADOR'
    planilla.save(update_fields=['estado'])
    _marcar(planilla)
    return planilla


@transaction.atomic
def pagar(planilla, cuenta, fecha, usuario=None):
    """Egreso de tesorería por el neto de la semana (concepto Planilla → 4111)."""
    from finanzas.models import Movimiento
    if planilla.estado != 'CERRADA':
        raise ErrorCivil('Cierre la planilla antes de pagarla.')
    neto = totales(planilla)['neto']
    if neto <= 0:
        raise ErrorCivil('La planilla no tiene neto a pagar.')
    if cuenta is None or cuenta.moneda != 'PEN':
        raise ErrorCivil('Pague la planilla desde una cuenta en soles.')
    if not cuenta.admite_negativo and cuenta.saldo_al(fecha) < neto:
        raise ErrorCivil(f'Saldo insuficiente en {cuenta}: S/ {cuenta.saldo_al(fecha):,.2f} para pagar '
                         f'S/ {neto:,.2f}.')
    mov = Movimiento.objects.create(cuenta=cuenta, fecha=fecha, tipo='EGRESO', concepto='PLANILLA',
                                    medio_pago='TRANSFERENCIA', monto=neto, glosa=f'Pago {planilla}'[:250],
                                    centro_costo=planilla.obra.centro_costo,
                                    creado_por=usuario if usuario and usuario.is_authenticated else None)
    planilla.estado, planilla.movimiento_pago = 'PAGADA', mov
    planilla.save(update_fields=['estado', 'movimiento_pago'])
    return mov


def asiento_planillas_civil(periodo, cta):
    """Planillas de construcción civil cerradas o pagadas cuya semana termina en el periodo: gasto con el centro de la
    obra, retenciones, aportes, provisiones y neto a pagar."""
    from contabilidad.centralizar import Borrador, _rango
    from contabilidad.models import Asiento
    desde, hasta = _rango(periodo)
    planillas = list(PlanillaCivil.objects.filter(hasta__range=(desde, hasta), estado__in=('CERRADA', 'PAGADA'))
                     .select_related('obra__centro_costo'))
    if not planillas:
        return None
    b = Borrador(Asiento(fecha=hasta, libro='05', origen='PLANILLA',
                         glosa=f'Planillas de construcción civil {periodo}'))
    debe, haber = defaultdict(lambda: D0), defaultdict(lambda: D0)
    for p in planillas:
        cc = p.obra.centro_costo
        for l in p.lineas.select_related('trabajador'):
            debe[('civil_remuneracion', cc)] += l.basico + l.dominical + l.buc + l.extras
            debe[('civil_movilidad', cc)] += l.movilidad
            debe[('civil_essalud', cc)] += l.essalud
            debe[('civil_sctr', cc)] += l.sctr
            debe[('prov_cts', cc)] += l.cts
            debe[('prov_vacaciones', cc)] += l.vacaciones
            debe[('prov_gratificacion', cc)] += l.gratificacion
            haber['civil_afp' if l.trabajador.sistema_pensiones == 'AFP' else 'civil_onp'] += l.pension
            haber['civil_conafovicer'] += l.conafovicer
            haber['civil_essalud_pasivo'] += l.essalud
            haber['civil_sctr_pasivo'] += l.sctr
            haber['prov_cts_pasivo'] += l.cts
            haber['prov_vacaciones_pasivo'] += l.vacaciones
            haber['prov_gratificacion_pasivo'] += l.gratificacion
            haber['mov_PLANILLA'] += l.neto
    for (clave, cc), monto in sorted(debe.items(), key=lambda x: (x[0][0], x[0][1].codigo if x[0][1] else '')):
        if monto:
            b.add(cta[clave], debe=monto, centro_costo=cc, glosa='Planilla de construcción civil')
    for clave, monto in sorted(haber.items()):
        if monto:
            b.add(cta[clave], haber=monto, glosa='Planilla de construcción civil')
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- horas-hombre: meta vs real
def horas_meta_real(obra):
    """Por categoría y por partida: horas-hombre de la meta (aporte de mano de obra en hh × metrado) frente a las
    horas del tareo."""
    from .valorizaciones import presupuesto_meta
    meta = presupuesto_meta(obra)
    por_cat_meta, por_partida_meta = defaultdict(lambda: D0), defaultdict(lambda: D0)
    if meta is not None:
        from .calculos import aporte
        for l in RecursoAPU.objects.filter(partida__presupuesto=meta, recurso__tipo='MO').select_related(
                'recurso', 'partida__subpresupuesto'):
            if l.recurso.es_porcentaje_mo or (l.recurso.unidad or '').lower() not in ('hh', 'h-h', 'h.h.'):
                continue
            hh = aporte(l, l.partida) * l.partida.metrado
            por_cat_meta[l.recurso.categoria_mo or 'OTRO'] += hh
            por_partida_meta[l.partida.clave] += hh
    por_cat_real, por_partida_real = defaultdict(lambda: D0), defaultdict(lambda: D0)
    for l in LineaTareo.objects.filter(tareo__obra=obra).select_related('trabajador', 'partida__subpresupuesto'):
        horas = l.horas + l.horas_60 + l.horas_100
        por_cat_real[l.trabajador.categoria_civil or 'OTRO'] += horas
        por_partida_real[l.partida.clave if l.partida_id else ''] += horas
    categorias = [{'categoria': c, 'meta': por_cat_meta.get(c, D0).quantize(Decimal('0.01')),
                   'real': por_cat_real.get(c, D0)} for c in ('CAPATAZ', 'OPERARIO', 'OFICIAL', 'PEON', 'OTRO')
                  if por_cat_meta.get(c) or por_cat_real.get(c)]
    partidas = []
    if meta is not None:
        for x in meta.partidas.filter(es_titulo=False).select_related('subpresupuesto'):
            m, r = por_partida_meta.get(x.clave, D0), por_partida_real.get(x.clave, D0)
            if m or r:
                partidas.append({'partida': x, 'meta': m.quantize(Decimal('0.01')), 'real': r,
                                 'desvio': (r - m).quantize(Decimal('0.01'))})
    return {'categorias': categorias, 'partidas': partidas, 'sin_partida': por_partida_real.get('', D0)}
