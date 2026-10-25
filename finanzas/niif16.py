"""Arrendamientos NIIF 16 (FIN-06): activo por derecho de uso y pasivo por arrendamiento al valor presente de las
rentas a la tasa incremental; cada mes, interés del pasivo y depreciación lineal del activo.

Libros paralelos: la factura del arrendador se sigue registrando como alquiler (gasto tributario, 635). Los asientos de
este módulo van solo al libro NIIF: reconocimiento inicial (Debe 3222 / Haber 4521), y cada mes la renta se
reclasifica del gasto al pasivo (Debe 4521 / Haber 635), el interés (Debe 6732 / Haber 4521) y la depreciación
(Debe 6812 / Haber 3922). Confirmar las cuentas con el contador."""
from datetime import date
from decimal import Decimal

from django.db import transaction

from core.models import r2

from .models import Arrendamiento, CuotaArrendamiento, _siguiente
from .operaciones import ErrorTesoreria

D0 = Decimal('0')


def _cuentas():
    from obras.servicios import _cuenta
    return {'activo': _cuenta(['3222', '322'], 'Activos por derecho de uso – edificaciones y equipos'),
            'pasivo': _cuenta(['4521', '452'], 'Pasivo por arrendamiento'),
            'interes': _cuenta(['6732', '673'], 'Intereses por arrendamientos'),
            'depreciacion': _cuenta(['6812', '681'], 'Depreciación de activos por derecho de uso'),
            'acumulada': _cuenta(['3922', '392'], 'Depreciación acumulada – derecho de uso'),
            'alquiler': _cuenta(['6352', '635'], 'Alquileres'),
            'por_pagar': _cuenta(['4699', '469'], 'Otras cuentas por pagar diversas')}


def tasa_mensual(tea):
    return (1 + Decimal(tea) / 100) ** (Decimal(1) / 12) - 1


def cronograma(cuota, plazo, tea, inicio, anticipado=True, derecho_uso=None):
    """[{numero, fecha, cuota, interes, amortizacion, saldo, depreciacion}] y el pasivo inicial."""
    from .prestamos import _sumar_meses
    i = tasa_mensual(tea)
    v = 1 / (1 + i)
    factor = sum((v ** k for k in range(plazo)), D0) if anticipado else sum((v ** k for k in range(1, plazo + 1)), D0)
    pasivo = r2(cuota * factor)
    activo = derecho_uso if derecho_uso is not None else pasivo
    dep = r2(activo / plazo)
    filas, saldo = [], pasivo
    for n in range(1, plazo + 1):
        if anticipado:
            interes = D0 if n == 1 else r2(saldo * i)
            fecha = _sumar_meses(inicio, n - 1)
        else:
            interes = r2(saldo * i)
            fecha = _sumar_meses(inicio, n)
        amort = saldo if n == plazo else cuota - interes
        saldo -= amort
        filas.append({'numero': n, 'fecha': fecha, 'cuota': amort + interes, 'interes': interes,
                      'amortizacion': amort, 'saldo': saldo,
                      'depreciacion': activo - dep * (plazo - 1) if n == plazo else dep})
    return filas, pasivo


@transaction.atomic
def crear(arr, usuario):
    from contabilidad.models import Asiento, PeriodoContable
    if arr.cuota <= 0 or arr.plazo < 2 or arr.tasa < 0:
        raise ErrorTesoreria('Indique la renta mensual, el plazo (más de un mes; los de 12 meses o menos pueden '
                             'quedar fuera de la NIIF 16) y la tasa incremental.')
    if PeriodoContable.esta_cerrado(f'{arr.inicio:%Y%m}'):
        raise ErrorTesoreria(f'El periodo {arr.inicio:%m/%Y} está cerrado.')
    filas, pasivo = cronograma(arr.cuota, arr.plazo, arr.tasa, arr.inicio, arr.pago_anticipado)
    arr.pasivo_inicial = pasivo
    arr.derecho_uso = pasivo + (arr.costos_directos or D0)
    arr.numero = _siguiente('NIIF16', 'AR')
    arr.save()
    filas, _ = cronograma(arr.cuota, arr.plazo, arr.tasa, arr.inicio, arr.pago_anticipado, arr.derecho_uso)
    for f in filas:
        CuotaArrendamiento.objects.create(arrendamiento=arr, **f)
    c = _cuentas()
    a = Asiento.objects.create(fecha=arr.inicio, libro='05', origen='MANUAL', norma='NIIF', creado_por=usuario,
                               glosa=f'NIIF 16: reconocimiento del arrendamiento {arr}'[:250])
    a.lineas.create(cuenta=c['activo'], debe=_pen(arr, arr.derecho_uso), haber=D0, centro_costo=arr.centro_costo)
    a.lineas.create(cuenta=c['pasivo'], debe=D0, haber=_pen(arr, pasivo))
    if arr.costos_directos:
        a.lineas.create(cuenta=c['por_pagar'], debe=D0, haber=_pen(arr, arr.costos_directos),
                        glosa='Costos directos iniciales')
    arr.asiento_inicial = a
    arr.save(update_fields=['asiento_inicial'])
    return arr


def _pen(arr, monto, fecha=None):
    if arr.moneda == 'PEN':
        return monto
    from core.tipo_cambio import venta_del_dia
    return r2(monto * venta_del_dia(fecha or arr.inicio))


@transaction.atomic
def registrar_mes(periodo, usuario):
    """Asiento NIIF del mes con las cuotas que vencen en él: renta al pasivo, interés y depreciación."""
    import calendar

    from contabilidad.models import Asiento, PeriodoContable
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorTesoreria(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado.')
    a_, m = int(periodo[:4]), int(periodo[4:])
    desde, fin = date(a_, m, 1), date(a_, m, calendar.monthrange(a_, m)[1])
    cuotas = list(CuotaArrendamiento.objects.filter(arrendamiento__estado='VIGENTE', fecha__range=[desde, fin],
                                                    asiento__isnull=True).select_related('arrendamiento'))
    if not cuotas:
        raise ErrorTesoreria('No hay cuotas de arrendamientos por registrar en el mes.')
    c = _cuentas()
    asiento = Asiento.objects.create(fecha=fin, libro='05', origen='MANUAL', norma='NIIF', creado_por=usuario,
                                     glosa=f'NIIF 16: arrendamientos {periodo[4:]}/{periodo[:4]}')
    for q in cuotas:
        arr, g = q.arrendamiento, str(q.arrendamiento)[:200]
        cc = arr.centro_costo
        for cuenta, debe, centro in ((c['pasivo'], _pen(arr, q.cuota, fin), None),
                                     (c['alquiler'], -_pen(arr, q.cuota, fin), cc),
                                     (c['interes'], _pen(arr, q.interes, fin), cc),
                                     (c['pasivo'], -_pen(arr, q.interes, fin), None),
                                     (c['depreciacion'], _pen(arr, q.depreciacion, fin), cc),
                                     (c['acumulada'], -_pen(arr, q.depreciacion, fin), None)):
            if debe:
                asiento.lineas.create(cuenta=cuenta, debe=max(debe, D0), haber=max(-debe, D0), centro_costo=centro,
                                      glosa=g)
        q.asiento = asiento
        q.save(update_fields=['asiento'])
        if not arr.cuotas.filter(asiento__isnull=True).exists():
            Arrendamiento.objects.filter(pk=arr.pk).update(estado='TERMINADO')
    return asiento


def resumen():
    """Pasivo y activo neto por arrendamiento vigente (en su moneda)."""
    filas = []
    for a in Arrendamiento.objects.filter(estado='VIGENTE').select_related('arrendador'):
        registradas = a.cuotas.filter(asiento__isnull=False)
        ultima = registradas.order_by('-numero').first()
        dep = sum((q.depreciacion for q in registradas), D0)
        filas.append({'arr': a, 'pasivo': ultima.saldo if ultima else a.pasivo_inicial,
                      'activo_neto': a.derecho_uso - dep, 'cuotas': registradas.count()})
    return filas
