"""Provisión mensual de beneficios sociales (cierre del mes): gratificaciones con su bonificación extraordinaria, CTS
y vacaciones de cada trabajador, por centro de costo, según su régimen laboral.

- Régimen general: gratificación 1/6 de la remuneración por mes, CTS (remuneración + 1/6 de la gratificación)/12,
  vacaciones 1/12.
- Pequeña empresa: la mitad de gratificación y CTS; vacaciones de 15 días (1/24).
- Microempresa: sin gratificación ni CTS; vacaciones de 15 días (1/24).
Si el trabajador entró o cesó en el mes, se provisiona solo por los días con vínculo."""
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from .models import LineaPlanilla, Parametro, Trabajador

D0 = Decimal('0')
FACTORES = {'GENERAL': (Decimal('1'), Decimal('1'), 12), 'PEQUENA': (Decimal('0.5'), Decimal('0.5'), 24),
            'MICRO': (D0, D0, 24)}  # (gratificación, CTS, divisor de vacaciones)


def _r2(v):
    return v.quantize(Decimal('0.01'))


def provisiones_del_mes(periodo):
    """{'por_trabajador': [...], 'por_centro': {concepto: {centro: monto}}, 'total': {...}, 'pagado': {cuenta_id: S/}}"""
    from contabilidad.models import CuentaDefecto
    anio, mes = int(periodo[:4]), int(periodo[4:])
    desde, hasta = date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])
    dias_mes = Decimal(hasta.day)
    try:
        param = Parametro.del_anio(anio)
    except ValueError:
        param = None
    bonif = (param.bonificacion_extraordinaria_pct / 100) if param else Decimal('0.09')
    filas, por_centro = [], {k: defaultdict(lambda: D0) for k in ('gratificacion', 'cts', 'vacaciones')}
    for t in Trabajador.objects.filter(sueldo__gt=0).select_related('centro_costo'):
        if not t.activo_en(desde, hasta):
            continue
        inicio, fin = max(desde, t.fecha_ingreso), min(hasta, t.fecha_cese or hasta)
        fraccion = Decimal((fin - inicio).days + 1) / dias_mes
        base = t.sueldo + (param.asignacion_familiar if param and t.asignacion_familiar else D0)
        f_grati, f_cts, div_vac = FACTORES.get(t.regimen, FACTORES['GENERAL'])
        grati_mes = base / 6 * f_grati
        grati = _r2(grati_mes * (1 + bonif) * fraccion)
        cts = _r2((base + grati_mes) / 12 * f_cts * fraccion)
        vac = _r2(base / div_vac * fraccion)
        filas.append({'t': t, 'base': base, 'fraccion': fraccion, 'gratificacion': grati, 'cts': cts,
                      'vacaciones': vac})
        for clave, monto in (('gratificacion', grati), ('cts', cts), ('vacaciones', vac)):
            if monto:
                por_centro[clave][t.centro_costo] += monto
    cta = {d.clave: d.cuenta_id for d in CuentaDefecto.objects.filter(
        clave__in=['prov_gratificacion', 'prov_cts', 'prov_vacaciones'])}
    pagado = dict(LineaPlanilla.objects.filter(
        fila__planilla__periodo=periodo, fila__planilla__estado__in=('CERRADA', 'PAGADA'),
        concepto__cuenta_id__in=list(cta.values())).values_list('concepto__cuenta_id').annotate(s=Sum('monto')))
    total = {k: sum((f[k] for f in filas), D0) for k in ('gratificacion', 'cts', 'vacaciones')}
    return {'por_trabajador': filas, 'por_centro': por_centro, 'total': total, 'pagado': pagado}
