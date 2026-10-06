"""Cálculo de planillas (régimen laboral general y REMYPE).

Mensual: básico por días (faltas, vacaciones y descanso médico subsidiado), asignación familiar, horas extra al
25% y 35% (valor hora = (básico + asignación) / 30 / horas de la jornada), ONP o AFP (aporte, prima con tope y
comisión), renta de quinta categoría por proyección anual (art. 40 del reglamento) y EsSalud del empleador (con
la RMV como base mínima).
Gratificación (julio y diciembre): meses completos del semestre × (básico + asignación) / 6, más la
bonificación extraordinaria del 9%. Pequeña empresa: la mitad; microempresa: no corresponde.
CTS (mayo y noviembre): (básico + asignación + 1/6 de la gratificación) / 12 por mes y / 360 por día del semestre.
Pequeña empresa: la mitad; microempresa: no corresponde.
"""
import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from .models import ConceptoPlanilla, FilaPlanilla, LineaPlanilla, Parametro, Trabajador

D0 = Decimal('0')
FACTOR_BENEFICIO = {'GENERAL': Decimal('1'), 'PEQUENA': Decimal('0.5'), 'MICRO': D0}
TRAMOS_QUINTA = [(5, 8), (20, 14), (35, 17), (45, 20), (None, 30)]  # hasta n UIT: tasa %
ESTADOS_VALIDOS = ('CALCULADA', 'CERRADA', 'PAGADA')


class ErrorPlanilla(Exception):
    pass


def r2(x):
    return Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def rango(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    return date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])


def dias_del_mes(trabajador, periodo):
    """Días del mes con vínculo laboral, en meses de 30 días (ingreso o cese dentro del mes)."""
    desde, hasta = rango(periodo)
    if not trabajador.activo_en(desde, hasta):
        return D0
    inicio = trabajador.fecha_ingreso.day if trabajador.fecha_ingreso > desde else 1
    fin = min(trabajador.fecha_cese.day, 30) if trabajador.fecha_cese and trabajador.fecha_cese < hasta else 30
    return Decimal(max(fin - inicio + 1, 0))


def dias360(inicio, fin):
    """Días entre dos fechas (inclusive) contando meses de 30 días."""
    d1, d2 = min(inicio.day, 30), min(fin.day, 30)
    if fin.month == 2 and fin.day == calendar.monthrange(fin.year, 2)[1]:
        d2 = 30  # febrero completo vale 30 días
    return (fin.year - inicio.year) * 360 + (fin.month - inicio.month) * 30 + (d2 - d1) + 1


def impuesto_quinta(renta_neta, uit):
    impuesto, desde = D0, D0
    for hasta_uit, tasa in TRAMOS_QUINTA:
        tope = uit * hasta_uit if hasta_uit else None
        if renta_neta <= desde:
            break
        tramo = (min(renta_neta, tope) if tope else renta_neta) - desde
        impuesto += tramo * tasa / 100
        if tope is None:
            break
        desde = tope
    return impuesto


# ---------------------------------------------------------------- filas
@transaction.atomic
def generar_filas(planilla):
    """Agrega a los trabajadores con vínculo en el periodo (y datos completos); conserva lo ya ingresado.
    Devuelve los trabajadores que faltan completar (sin fecha de ingreso, sueldo o AFP)."""
    if planilla.estado in ('CERRADA', 'PAGADA'):
        raise ErrorPlanilla('La planilla está cerrada.')
    desde, hasta = rango(planilla.periodo)
    incompletos = []
    existentes = set(planilla.filas.values_list('trabajador_id', flat=True))
    for t in Trabajador.objects.select_related('afp'):
        if t.fecha_cese and t.fecha_cese < desde or t.fecha_ingreso and t.fecha_ingreso > hasta:
            continue
        if not t.datos_completos:
            if t.fecha_cese is None:
                incompletos.append(t)
            continue
        if t.pk in existentes:
            continue
        dias = dias_del_mes(t, planilla.periodo) if planilla.tipo == 'MENSUAL' else Decimal('30')
        if planilla.tipo == 'MENSUAL' and not dias:
            continue
        FilaPlanilla.objects.create(planilla=planilla, trabajador=t, dias_laborados=dias)
    return incompletos


@transaction.atomic
def calcular(planilla):
    if planilla.estado in ('CERRADA', 'PAGADA'):
        raise ErrorPlanilla('La planilla está cerrada: reábrala para recalcular.')
    p = Parametro.del_anio(planilla.anio)
    conceptos = {c.clave: c for c in ConceptoPlanilla.objects.all()}
    faltan = {'BASICO', 'GRATIFICACION', 'CTS', 'ESSALUD', 'QUINTA'} - set(conceptos)
    if faltan:
        raise ErrorPlanilla(f'Faltan conceptos de planilla: {", ".join(sorted(faltan))}.')
    for fila in planilla.filas.select_related('trabajador__afp'):
        fila.lineas.all().delete()
        if planilla.tipo == 'MENSUAL':
            lineas, notas = _mensual(fila, p, planilla)
        elif planilla.tipo == 'GRATIFICACION':
            lineas, notas = _gratificacion(fila, p, planilla)
        else:
            lineas, notas = _cts(fila, p, planilla)
        objetos = [LineaPlanilla(fila=fila, concepto=conceptos[clave], monto=r2(monto), base=r2(base))
                   for clave, monto, base in lineas if r2(monto) and clave in conceptos]
        LineaPlanilla.objects.bulk_create(objetos)
        tipos = {o.concepto.tipo for o in objetos}
        suma = lambda tipo: sum((o.monto for o in objetos if o.concepto.tipo == tipo), D0)
        fila.total_ingresos = suma('INGRESO')
        fila.total_descuentos = suma('DESCUENTO')
        fila.total_aportes = suma('APORTE')
        fila.neto = fila.total_ingresos - fila.total_descuentos
        fila.remuneracion_afecta = r2(next((b for c, m, b in lineas if c == 'ESSALUD'), D0)) if 'APORTE' in tipos \
            else fila.total_ingresos
        fila.detalle_calculo = '\n'.join(notas)
        fila.save()
    planilla.estado = 'CALCULADA'
    planilla.save(update_fields=['estado'])


def _remuneracion_regular(t, p):
    return t.sueldo + (p.asignacion_familiar if t.asignacion_familiar else D0)


def _mensual(fila, p, planilla):
    t = fila.trabajador
    lineas, notas = [], []
    dias = fila.dias_laborados
    pagados = max(dias - fila.dias_falta - fila.dias_vacaciones - fila.dias_subsidio, D0)
    basico = t.sueldo * pagados / 30
    vacaciones = t.sueldo * fila.dias_vacaciones / 30
    asignacion = p.asignacion_familiar * dias / 30 if t.asignacion_familiar else D0
    valor_hora = (t.sueldo + (p.asignacion_familiar if t.asignacion_familiar else D0)) / 30 / p.horas_jornada
    he25 = valor_hora * Decimal('1.25') * fila.horas_extra_25
    he35 = valor_hora * Decimal('1.35') * fila.horas_extra_35
    lineas += [('BASICO', basico, t.sueldo), ('VACACIONES', vacaciones, t.sueldo), ('ASIG_FAMILIAR', asignacion, p.rmv),
               ('HORAS_EXTRA_25', he25, valor_hora), ('HORAS_EXTRA_35', he35, valor_hora),
               ('OTROS_AFECTOS', fila.otros_ingresos, D0), ('NO_AFECTOS', fila.ingresos_no_afectos, D0)]
    notas.append(f'Días pagados {pagados:g} de {dias:g} · valor hora S/ {r2(valor_hora)}')
    afecta = r2(basico) + r2(vacaciones) + r2(asignacion) + r2(he25) + r2(he35) + r2(fila.otros_ingresos)
    # pensiones
    if t.sistema_pensiones == 'ONP':
        lineas.append(('ONP', afecta * p.onp_pct / 100, afecta))
    elif t.sistema_pensiones == 'AFP' and t.afp:
        afp = t.afp
        base_prima = min(afecta, afp.tope_prima) if afp.tope_prima else afecta
        comision = afp.comision_flujo_pct if t.comision_afp == 'FLUJO' else afp.comision_mixta_pct
        lineas += [('AFP_APORTE', afecta * afp.aporte_pct / 100, afecta),
                   ('AFP_PRIMA', base_prima * afp.prima_pct / 100, base_prima),
                   ('AFP_COMISION', afecta * comision / 100, afecta)]
    # quinta categoría
    retencion, nota = quinta(fila, afecta, p, planilla)
    lineas.append(('QUINTA', retencion, afecta))
    notas.append(nota)
    lineas += [('ADELANTO', fila.adelantos, D0), ('OTROS_DESCUENTOS', fila.otros_descuentos, D0)]
    # EsSalud (empleador): base mínima la RMV
    if afecta > 0:
        base_essalud = max(afecta, p.rmv)
        lineas.append(('ESSALUD', base_essalud * p.essalud_pct / 100, afecta))
    return lineas, notas


def quinta(fila, afecta_mes, p, planilla):
    """Retención del mes por el método de proyección anual (art. 40 del reglamento de la LIR)."""
    t, anio, mes = fila.trabajador, planilla.anio, planilla.mes
    anteriores = FilaPlanilla.objects.filter(trabajador=t, planilla__periodo__startswith=str(anio),
                                             planilla__estado__in=ESTADOS_VALIDOS).exclude(pk=fila.pk)
    previas = sum((f.remuneracion_afecta for f in anteriores.filter(planilla__tipo='MENSUAL',
                                                                    planilla__periodo__lt=planilla.periodo)), D0)
    grat_recibidas = sum((f.total_ingresos for f in anteriores.filter(planilla__tipo='GRATIFICACION',
                                                                      planilla__periodo__lte=planilla.periodo)), D0)
    retenido = sum((l.monto for f in anteriores.filter(planilla__periodo__lt=planilla.periodo)
                    for l in f.lineas.all() if l.concepto.clave == 'QUINTA'), D0)
    if t.quinta_anio == anio:  # lo percibido y retenido en el año antes del sistema
        previas += t.quinta_remuneracion_previa
        retenido += t.quinta_retencion_previa
    regular = _remuneracion_regular(t, p)
    grat_mes_registrada = anteriores.filter(planilla__tipo='GRATIFICACION', planilla__periodo=planilla.periodo).exists()
    futuras = sum(1 for m in (7, 12) if m > mes or (m == mes and not grat_mes_registrada))
    factor_grat = FACTOR_BENEFICIO[t.regimen] * (1 + p.bonificacion_extraordinaria_pct / 100)
    proyeccion = previas + grat_recibidas + afecta_mes + regular * (12 - mes) + regular * factor_grat * futuras
    renta_neta = proyeccion - 7 * p.uit
    if renta_neta <= 0:
        return D0, f'Quinta: proyección S/ {r2(proyeccion):,} no supera 7 UIT'
    impuesto = impuesto_quinta(renta_neta, p.uit)
    divisor = 12 if mes <= 3 else 9 if mes == 4 else 8 if mes <= 7 else 5 if mes == 8 else 4 if mes <= 11 else 1
    retencion = max((impuesto - (retenido if mes > 3 else D0)) / divisor, D0)
    return retencion, (f'Quinta: proyección S/ {r2(proyeccion):,} − 7 UIT = S/ {r2(renta_neta):,}; impuesto anual '
                       f'S/ {r2(impuesto):,}; retenido S/ {r2(retenido):,}; ÷ {divisor}')


def _gratificacion(fila, p, planilla):
    t = fila.trabajador
    if planilla.mes not in (7, 12):
        raise ErrorPlanilla('La gratificación se calcula en julio o en diciembre.')
    meses_sem = range(1, 7) if planilla.mes == 7 else range(7, 13)
    meses = 0
    for m in meses_sem:
        inicio, fin = rango(f'{planilla.anio}{m:02d}')
        if t.fecha_ingreso <= inicio and (t.fecha_cese is None or t.fecha_cese >= fin):
            meses += 1
    regular = _remuneracion_regular(t, p)
    factor = FACTOR_BENEFICIO[t.regimen]
    grat = regular * meses / 6 * factor
    bonif = r2(grat) * p.bonificacion_extraordinaria_pct / 100
    nota = f'{meses} meses completos del semestre × S/ {regular} / 6' + (f' × {factor}' if factor != 1 else '')
    return [('GRATIFICACION', grat, regular), ('BONIF_EXTRA', bonif, r2(grat))], [nota]


def _cts(fila, p, planilla):
    from .models import Planilla
    t = fila.trabajador
    if planilla.mes not in (5, 11):
        raise ErrorPlanilla('La CTS se calcula en mayo o en noviembre.')
    if planilla.mes == 5:
        sem_ini, sem_fin, grat_periodo = date(planilla.anio - 1, 11, 1), date(planilla.anio, 4, 30), \
            f'{planilla.anio - 1}12'
    else:
        sem_ini, sem_fin, grat_periodo = date(planilla.anio, 5, 1), date(planilla.anio, 10, 31), f'{planilla.anio}07'
    inicio = max(sem_ini, t.fecha_ingreso)
    fin = min(sem_fin, t.fecha_cese) if t.fecha_cese else sem_fin
    if fin < inicio:
        return [], ['Sin tiempo de servicios en el semestre']
    total = dias360(inicio, fin)
    meses, dias = divmod(min(total, 180), 30)
    regular = _remuneracion_regular(t, p)
    factor = FACTOR_BENEFICIO[t.regimen]
    grat = Planilla.objects.filter(tipo='GRATIFICACION', periodo=grat_periodo, estado__in=ESTADOS_VALIDOS).first()
    fila_grat = grat.filas.filter(trabajador=t).first() if grat else None
    if fila_grat:
        sexto = fila_grat.monto('GRATIFICACION') / 6
        origen = f'1/6 de la gratificación {grat_periodo}'
    else:
        sexto = regular * factor / 6  # sin planilla de gratificación registrada en el sistema: la teórica
        origen = f'1/6 de la gratificación teórica (no hay planilla {grat_periodo})'
    computable = regular + sexto
    cts = (computable / 12 * meses + computable / 360 * dias) * factor
    nota = (f'{meses} meses y {dias} días ({inicio:%d/%m/%Y} al {fin:%d/%m/%Y}) · remuneración computable '
            f'S/ {r2(computable)} (incluye {origen})' + (f' × {factor}' if factor != 1 else ''))
    return [('CTS', cts, computable)], [nota]
