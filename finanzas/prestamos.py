"""Préstamos bancarios y leasing: cronograma de cuota fija (método francés), desembolso y pago de cuotas.

Contabilidad (al centralizar): el desembolso va a la caja o banco contra 4511 (préstamo); el leasing reconoce el bien
(32) contra 4521 en el mes de la firma; cada cuota pagada separa la amortización (45), el interés (673), las
comisiones (639) y, en el leasing, el IGV (40111)."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from core.models import D0, Empresa, r2

from .models import CuotaPrestamo, Movimiento, Prestamo, _siguiente
from .operaciones import ErrorTesoreria, _periodo_abierto


def _sumar_meses(fecha, meses):
    import calendar
    mes = fecha.month - 1 + meses
    anio, mes = fecha.year + mes // 12, mes % 12 + 1
    return date(anio, mes, min(fecha.day, calendar.monthrange(anio, mes)[1]))


def tasa_periodo(tea, meses):
    """Tasa del periodo equivalente a la TEA: (1 + TEA)^(meses/12) - 1."""
    return (1 + Decimal(tea) / 100) ** (Decimal(meses) / 12) - 1


def cronograma(monto, tea, plazo, meses, primera, opcion_compra=D0, leasing=False):
    """[{numero, fecha, capital, interes, igv, saldo}]: cuota fija; la última ajusta el redondeo. En el leasing la
    opción de compra queda como valor residual (se amortiza en la última cuota) y las cuotas llevan IGV."""
    i = tasa_periodo(tea, meses)
    residual = opcion_compra if leasing else D0
    financiado = monto - residual / (1 + i) ** plazo if residual else monto
    cuota = (financiado * i / (1 - (1 + i) ** -plazo)) if i else financiado / plazo
    cuota = cuota.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tasa_igv = Empresa.actual().igv_tasa / 100 if leasing else D0
    filas, saldo = [], monto
    for n in range(1, plazo + 1):
        interes = r2(saldo * i)
        capital = saldo if n == plazo else min(cuota - interes, saldo)
        saldo -= capital
        filas.append({'numero': n, 'fecha': _sumar_meses(primera, meses * (n - 1)), 'capital': capital,
                      'interes': interes, 'igv': r2((capital + interes) * tasa_igv), 'saldo': saldo})
    return filas


@transaction.atomic
def crear(prestamo, usuario, comision_cuota=D0):
    """Graba el préstamo con su cronograma. El préstamo bancario registra el desembolso en su caja o banco."""
    from contabilidad.models import CuentaDefecto
    if prestamo.monto <= 0 or prestamo.plazo <= 0:
        raise ErrorTesoreria('Indique el capital y el número de cuotas.')
    if prestamo.cuenta.moneda != prestamo.moneda:
        raise ErrorTesoreria(f'La cuenta {prestamo.cuenta} no es de la moneda del préstamo.')
    if prestamo.primera_cuota <= prestamo.fecha_desembolso:
        raise ErrorTesoreria('La primera cuota vence después del desembolso.')
    _periodo_abierto(prestamo.fecha_desembolso)
    prestamo.numero = _siguiente('PRE', 'PRE')
    prestamo.creado_por = usuario
    prestamo.save()
    for f in cronograma(prestamo.monto, prestamo.tasa_anual, prestamo.plazo, prestamo.meses_entre_cuotas,
                        prestamo.primera_cuota, prestamo.opcion_compra, prestamo.tipo == 'LEASING'):
        CuotaPrestamo.objects.create(prestamo=prestamo, comision=comision_cuota, **f)
    if prestamo.tipo == 'PRESTAMO':
        pasivo = CuentaDefecto.objects.filter(clave='prestamo_pasivo').select_related('cuenta').first()
        Movimiento.objects.create(
            cuenta=prestamo.cuenta, fecha=prestamo.fecha_desembolso, tipo='INGRESO', concepto='PRESTAMO',
            tercero=prestamo.entidad, monto=prestamo.monto, prestamo=prestamo,
            cuenta_contable=pasivo.cuenta if pasivo else None, numero_operacion=prestamo.referencia[:40],
            glosa=f'Desembolso {prestamo}: {prestamo.descripcion}'[:250], creado_por=usuario)
    return prestamo


@transaction.atomic
def pagar_cuota(cuota, fecha, usuario, numero_operacion=''):
    p = cuota.prestamo
    if p.estado != 'VIGENTE':
        raise ErrorTesoreria(f'{p} está {p.get_estado_display().lower()}.')
    if cuota.pagada:
        raise ErrorTesoreria(f'La cuota {cuota.numero} ya está pagada.')
    anterior = p.cuotas.filter(numero__lt=cuota.numero).exclude(pagos__estado='VIGENTE').first()
    if anterior:
        raise ErrorTesoreria(f'Pague primero la cuota {anterior.numero}.')
    _periodo_abierto(fecha)
    error = p.cuenta.error_sobregiro(cuota.cuota, fecha=fecha)
    if error:
        raise ErrorTesoreria(error)
    mov = Movimiento.objects.create(
        cuenta=p.cuenta, fecha=fecha, tipo='EGRESO', concepto='PRESTAMO', tercero=p.entidad, monto=cuota.cuota,
        prestamo=p, cuota=cuota, numero_operacion=numero_operacion[:40], creado_por=usuario,
        glosa=f'Cuota {cuota.numero}/{p.plazo} {p}'[:250])
    if not p.cuotas.exclude(pagos__estado='VIGENTE').exists():
        p.estado = 'CANCELADO'
        p.save(update_fields=['estado'])
    return mov


@transaction.atomic
def anular(prestamo, motivo, usuario):
    """Solo sin cuotas pagadas: anula el desembolso."""
    if prestamo.cuotas_pagadas().exists():
        raise ErrorTesoreria('Tiene cuotas pagadas: anule primero esos pagos en Movimientos.')
    if len((motivo or '').strip()) < 10:
        raise ErrorTesoreria('Explique el motivo (mínimo 10 caracteres).')
    for m in prestamo.movimientos.all():
        if m.conciliado:
            raise ErrorTesoreria(f'{m.voucher} está conciliado: quite primero la conciliación.')
        _periodo_abierto(m.fecha)
        m.estado, m.motivo_anulacion = 'ANULADO', f'Anulación de {prestamo}: {motivo.strip()}'[:250]
        m.anulado_por, m.anulado_en = usuario, timezone.now()
        m.save()
    prestamo.estado = 'ANULADO'
    prestamo.save(update_fields=['estado'])


def sincronizar(prestamo):
    """Si se anuló el pago de una cuota en Movimientos, el préstamo cancelado vuelve a estar vigente."""
    if prestamo.estado == 'CANCELADO' and prestamo.cuotas.exclude(pagos__estado='VIGENTE').exists():
        prestamo.estado = 'VIGENTE'
        prestamo.save(update_fields=['estado'])
    return prestamo


def resumen():
    """Deuda vigente por moneda y próximas cuotas (para la pantalla de préstamos)."""
    vigentes = Prestamo.objects.filter(estado='VIGENTE')
    saldos = {}
    for p in vigentes:
        saldos[p.moneda] = saldos.get(p.moneda, D0) + p.saldo_capital
    proximas = (CuotaPrestamo.objects.filter(prestamo__estado='VIGENTE').exclude(pagos__estado='VIGENTE')
                .select_related('prestamo__entidad').order_by('fecha')[:10])
    return saldos, proximas
