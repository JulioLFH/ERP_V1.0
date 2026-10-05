"""Presupuesto anual: propuesta a partir del real del año anterior y ejecución (real vs presupuesto)."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from core.models import r2

from .models import AsientoLinea, PresupuestoLinea

D0 = Decimal('0')
PRESUPUESTABLES = r'^(6[2-8]|69|7[0-8]|88)'


def _signo(codigo):
    """Ingresos (7) con saldo acreedor positivo; gastos con saldo deudor positivo."""
    return -1 if codigo.startswith('7') else 1


def real_por_mes(anio, cuentas=None):
    """{(cuenta_id, centro_id): [12 importes]} del año, con el signo del presupuesto."""
    qs = AsientoLinea.objects.filter(asiento__periodo__startswith=str(anio), es_destino=False,
                                     cuenta__codigo__regex=PRESUPUESTABLES)
    if cuentas is not None:
        qs = qs.filter(cuenta_id__in=cuentas)
    reales = defaultdict(lambda: [D0] * 12)
    for f in qs.values('cuenta_id', 'cuenta__codigo', 'centro_costo_id', 'asiento__periodo').annotate(
            d=Sum('debe'), h=Sum('haber')):
        mes = int(f['asiento__periodo'][4:]) - 1
        reales[(f['cuenta_id'], f['centro_costo_id'])][mes] += _signo(f['cuenta__codigo']) * (f['d'] - f['h'])
    return reales


@transaction.atomic
def generar_desde_real(presupuesto, variacion_pct=D0):
    """Reemplaza las líneas por el real del año anterior (por cuenta, centro de costo y mes) × (1 + variación %)."""
    factor = 1 + Decimal(variacion_pct) / 100
    presupuesto.lineas.all().delete()
    nuevas = []
    for (cuenta_id, centro_id), meses in sorted(real_por_mes(presupuesto.anio - 1).items(),
                                                key=lambda x: (x[0][0], x[0][1] or 0)):
        if not any(meses):
            continue
        valores = {f'm{i + 1:02d}': r2(v * factor) for i, v in enumerate(meses)}
        nuevas.append(PresupuestoLinea(presupuesto=presupuesto, cuenta_id=cuenta_id, centro_costo_id=centro_id,
                                       **valores))
    PresupuestoLinea.objects.bulk_create(nuevas)
    return len(nuevas)


def ejecucion(presupuesto, hasta_mes=12):
    """Por línea: presupuesto y real acumulados hasta el mes, variación y % de ejecución. Una línea sin centro de
    costo compara con el real de la cuenta en los centros que no tienen su propia línea."""
    lineas = list(presupuesto.lineas.select_related('cuenta', 'centro_costo'))
    reales = real_por_mes(presupuesto.anio, {l.cuenta_id for l in lineas})
    con_centro = defaultdict(set)
    for l in lineas:
        if l.centro_costo_id:
            con_centro[l.cuenta_id].add(l.centro_costo_id)
    filas, tot = [], {'pres': D0, 'real': D0, 'anual': D0}
    for l in lineas:
        if l.centro_costo_id:
            real = reales.get((l.cuenta_id, l.centro_costo_id), [D0] * 12)
        else:
            real = [sum(v[m] for (c, cc), v in reales.items() if c == l.cuenta_id and cc not in con_centro[l.cuenta_id])
                    for m in range(12)]
        pres = sum(l.meses[:hasta_mes], D0)
        acumulado = sum(real[:hasta_mes], D0)
        var = acumulado - pres
        filas.append({'l': l, 'pres': pres, 'real': acumulado, 'var': var, 'anual': l.total,
                      'ingreso': l.cuenta.codigo.startswith('7'),
                      'pct': r2(acumulado / pres * 100) if pres else None,
                      'meses': list(zip(l.meses[:hasta_mes], real[:hasta_mes]))})
        signo = -1 if l.cuenta.codigo.startswith('7') else 1  # totales: resultado (ingresos − gastos)
        tot['pres'] -= signo * pres
        tot['real'] -= signo * acumulado
        tot['anual'] -= signo * l.total
    tot['var'] = tot['real'] - tot['pres']
    return filas, tot
