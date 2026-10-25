"""Préstamos bancarios y leasing: cronograma de cuota fija (método francés), desembolso y pago de cuotas.

Contabilidad (al centralizar): el desembolso va a la caja o banco contra 4511 (préstamo); el leasing reconoce el bien
(32) contra 4521 en el mes de la firma; cada cuota pagada separa la amortización (45), el interés (673), las
comisiones (639) y, en el leasing, el IGV (40111)."""
from datetime import date, timedelta
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


def cronograma(monto, tea, plazo, meses, primera, opcion_compra=D0, leasing=False, sistema='FRANCES', gracia=0,
               tipo_gracia='', dobles=False, numero_inicial=1):
    """[{numero, fecha, capital, interes, igv, saldo}]. Francés: cuota fija (con cuotas dobles en julio y diciembre si
    se pide); alemán: amortización constante. Gracia parcial: las primeras cuotas pagan solo intereses; gracia total:
    los intereses de esos periodos se capitalizan y no hay cuota (el cronograma empiezan después). La última cuota
    ajusta el redondeo. En el leasing la opción de compra queda como valor residual y las cuotas llevan IGV."""
    i = tasa_periodo(tea, meses)
    tasa_igv = Empresa.actual().igv_tasa / 100 if leasing else D0
    gracia = min(gracia or 0, max(plazo - 1, 0)) if tipo_gracia else 0
    saldo = Decimal(monto)
    fechas = [_sumar_meses(primera, meses * k) for k in range(plazo)]
    filas, numero = [], numero_inicial
    if tipo_gracia == 'TOTAL':  # los intereses se capitalizan: se paga desde la cuota gracia + 1
        for _ in range(gracia):
            saldo = r2(saldo * (1 + i))
        fechas = fechas[gracia:]
    elif tipo_gracia == 'PARCIAL':
        for k in range(gracia):
            filas.append({'numero': numero, 'fecha': fechas[k], 'capital': D0, 'interes': r2(saldo * i),
                          'igv': r2(saldo * i * tasa_igv), 'saldo': saldo})
            numero += 1
        fechas = fechas[gracia:]
    n = len(fechas)
    residual = opcion_compra if leasing else D0
    if sistema == 'ALEMAN':
        amortizacion = r2((saldo - residual) / n)
        for k, fecha in enumerate(fechas):
            interes = r2(saldo * i)
            capital = saldo if k == n - 1 else amortizacion
            saldo -= capital
            filas.append({'numero': numero, 'fecha': fecha, 'capital': capital, 'interes': interes,
                          'igv': r2((capital + interes) * tasa_igv), 'saldo': saldo})
            numero += 1
        return filas
    pesos = [2 if dobles and f.month in (7, 12) else 1 for f in fechas]
    descuento = sum((Decimal(p) / (1 + i) ** (k + 1) for k, p in enumerate(pesos)), D0)
    financiado = saldo - residual / (1 + i) ** n if residual else saldo
    base = financiado / descuento if descuento else financiado / n
    for k, fecha in enumerate(fechas):
        cuota = (base * pesos[k]).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        interes = r2(saldo * i)
        capital = saldo if k == n - 1 else min(cuota - interes, saldo)
        saldo -= capital
        filas.append({'numero': numero, 'fecha': fecha, 'capital': capital, 'interes': interes,
                      'igv': r2((capital + interes) * tasa_igv), 'saldo': saldo})
        numero += 1
    return filas


def _cronograma_de(prestamo, **extra):
    datos = {'sistema': prestamo.sistema, 'gracia': prestamo.meses_gracia, 'tipo_gracia': prestamo.tipo_gracia,
             'dobles': prestamo.cuotas_dobles}
    datos.update(extra)
    return cronograma(prestamo.monto, prestamo.tasa_anual, prestamo.plazo, prestamo.meses_entre_cuotas,
                      prestamo.primera_cuota, prestamo.opcion_compra, prestamo.tipo == 'LEASING', **datos)


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
    if prestamo.tipo_gracia and not prestamo.meses_gracia:
        raise ErrorTesoreria('Indique cuántas cuotas de gracia tiene.')
    if prestamo.meses_gracia and not prestamo.tipo_gracia:
        raise ErrorTesoreria('Indique el tipo de gracia (total o parcial).')
    if prestamo.meses_gracia >= prestamo.plazo:
        raise ErrorTesoreria('La gracia debe ser menor que el número de cuotas.')
    from .financiamiento import validar_uso
    validar_uso(prestamo.linea, prestamo.monto, prestamo.moneda)
    prestamo.numero = _siguiente('PRE', 'PRE')
    prestamo.creado_por = usuario
    prestamo.save()
    for f in _cronograma_de(prestamo):
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


def _rehacer(prestamo, saldo, tea, cuotas_restantes, primera, numero_inicial, comision):
    """Reemplaza las cuotas impagas por un cronograma nuevo desde el saldo (prepago o cambio de tasa)."""
    prestamo.cuotas.exclude(pagos__estado='VIGENTE').delete()
    filas = cronograma(saldo, tea, cuotas_restantes, prestamo.meses_entre_cuotas, primera, prestamo.opcion_compra,
                       prestamo.tipo == 'LEASING', sistema=prestamo.sistema, dobles=prestamo.cuotas_dobles,
                       numero_inicial=numero_inicial)
    for f in filas:
        CuotaPrestamo.objects.create(prestamo=prestamo, comision=comision, **f)
    return filas


def _pendientes(prestamo):
    return list(prestamo.cuotas.exclude(pagos__estado='VIGENTE').order_by('numero'))


@transaction.atomic
def prepagar(prestamo, monto, fecha, usuario, modo='PLAZO', numero_operacion=''):
    """FIN-06: prepago de capital con recálculo. modo PLAZO: se mantiene la cuota y se reduce el número de cuotas;
    CUOTA: se mantiene el plazo y baja la cuota. El pago va contra el pasivo del préstamo."""
    from contabilidad.models import CuentaDefecto
    if prestamo.estado != 'VIGENTE':
        raise ErrorTesoreria(f'{prestamo} está {prestamo.get_estado_display().lower()}.')
    pendientes = _pendientes(prestamo)
    if not pendientes:
        raise ErrorTesoreria('El préstamo no tiene cuotas pendientes.')
    saldo = prestamo.saldo_capital
    monto = Decimal(monto or 0)
    if monto <= 0 or monto > saldo:
        raise ErrorTesoreria(f'El prepago debe ser mayor que cero y no mayor que el saldo de capital ({saldo:,.2f}).')
    _periodo_abierto(fecha)
    error = prestamo.cuenta.error_sobregiro(monto, fecha=fecha)
    if error:
        raise ErrorTesoreria(error)
    pasivo = CuentaDefecto.objects.filter(clave='prestamo_pasivo').select_related('cuenta').first()
    mov = Movimiento.objects.create(
        cuenta=prestamo.cuenta, fecha=fecha, tipo='EGRESO', concepto='PRESTAMO', tercero=prestamo.entidad,
        monto=monto, prestamo=prestamo, cuenta_contable=pasivo.cuenta if pasivo else None,
        numero_operacion=numero_operacion[:40], creado_por=usuario, glosa=f'Prepago de capital {prestamo}'[:250])
    nuevo = saldo - monto
    if nuevo <= 0:
        prestamo.cuotas.exclude(pagos__estado='VIGENTE').delete()
        prestamo.estado = 'CANCELADO'
        prestamo.save(update_fields=['estado'])
        return mov
    primera, numero, comision = pendientes[0].fecha, pendientes[0].numero, pendientes[0].comision
    restantes = len(pendientes)
    if modo == 'PLAZO':  # mismo importe de cuota: menos cuotas
        i = tasa_periodo(prestamo.tasa_anual, prestamo.meses_entre_cuotas)
        cuota = pendientes[0].capital + pendientes[0].interes
        import math
        if i and cuota > nuevo * i:
            restantes = max(1, math.ceil(-math.log(1 - float(nuevo * i / cuota)) / math.log(1 + float(i))))
        restantes = min(restantes, len(pendientes))
    _rehacer(prestamo, nuevo, prestamo.tasa_anual, restantes, primera, numero, comision)
    return mov


@transaction.atomic
def reprogramar(prestamo, tea, motivo, usuario, cuotas=None):
    """FIN-06: tasa variable o reprogramación: rehace las cuotas impagas con la nueva TEA (y, si se indica, otro
    número de cuotas) desde el saldo de capital."""
    from core.auditoria import registrar
    pendientes = _pendientes(prestamo)
    if prestamo.estado != 'VIGENTE' or not pendientes:
        raise ErrorTesoreria('Solo se reprograma un préstamo vigente con cuotas pendientes.')
    if len((motivo or '').strip()) < 10:
        raise ErrorTesoreria('Indique el motivo (mínimo 10 caracteres).')
    tea = Decimal(tea)
    if tea < 0:
        raise ErrorTesoreria('La tasa no puede ser negativa.')
    antes = prestamo.tasa_anual
    _rehacer(prestamo, prestamo.saldo_capital, tea, cuotas or len(pendientes), pendientes[0].fecha,
             pendientes[0].numero, pendientes[0].comision)
    prestamo.tasa_anual = tea
    prestamo.plazo = pendientes[0].numero - 1 + (cuotas or len(pendientes))
    prestamo.save(update_fields=['tasa_anual', 'plazo'])
    registrar('MODIFICAR', prestamo, {'TEA %': [str(antes), str(tea)]}, motivo.strip())
    return prestamo


def interes_devengado(prestamo, fin):
    """Interés corrido al «fin» de la cuota que vence después (proporcional a los días del periodo)."""
    cuota = prestamo.cuotas.filter(fecha__gt=fin).exclude(pagos__estado='VIGENTE').order_by('numero').first()
    if cuota is None or not cuota.interes:
        return D0
    anterior = prestamo.cuotas.filter(numero__lt=cuota.numero).order_by('-numero').first()
    inicio = anterior.fecha if anterior else prestamo.fecha_desembolso
    dias = (cuota.fecha - inicio).days
    corridos = (fin - inicio).days
    if dias <= 0 or corridos <= 0:
        return D0
    return r2(cuota.interes * min(corridos, dias) / dias)


@transaction.atomic
def devengar(periodo, usuario):
    """FIN-06: asiento de cierre con los intereses devengados no vencidos (Debe 6731 / Haber 4551) y su extorno el
    primer día del mes siguiente: la cuota sigue registrando su interés al pagarse."""
    import calendar

    from contabilidad.models import Asiento, PeriodoContable
    from obras.servicios import _cuenta

    from .models import DevengoPrestamo
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorTesoreria(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado.')
    if DevengoPrestamo.objects.filter(periodo=periodo, asiento__isnull=False).exists():
        raise ErrorTesoreria('El devengo de intereses de este mes ya se registró.')
    a, m = int(periodo[:4]), int(periodo[4:])
    fin = date(a, m, calendar.monthrange(a, m)[1])
    from core.tipo_cambio import venta_del_dia
    tc = venta_del_dia(fin)
    filas, capitalizados = [], []
    for p in Prestamo.objects.filter(estado='VIGENTE', fecha_desembolso__lte=fin):
        primera = p.cuotas.order_by('numero').first()
        if p.tipo_gracia == 'TOTAL' and primera and fin < primera.fecha:
            # gracia total: el interés del mes se capitaliza (aumenta la deuda) y no se extorna
            previo = sum((d.interes for d in p.devengos.filter(periodo__lt=periodo)), D0)
            desde = max(p.fecha_desembolso, date(a, m, 1) - timedelta(days=1))
            interes = r2((p.monto + previo) * ((1 + p.tasa_anual / 100) ** (Decimal((fin - desde).days) / 360) - 1))
            if interes > 0:
                capitalizados.append((p, interes, interes if p.moneda == 'PEN' else r2(interes * tc)))
            continue
        interes = interes_devengado(p, fin)
        if interes:
            filas.append((p, interes, interes if p.moneda == 'PEN' else r2(interes * tc)))
    if not filas and not capitalizados:
        raise ErrorTesoreria('No hay intereses devengados por registrar en el mes.')
    gasto = _cuenta(['6731', '673'], 'Intereses por préstamos de instituciones financieras')
    por_pagar = _cuenta(['4551', '455'], 'Costos de financiación por pagar')
    if capitalizados:
        from contabilidad.models import CuentaDefecto
        defecto = CuentaDefecto.objects.filter(clave='prestamo_pasivo').select_related('cuenta').first()
        pasivo = defecto.cuenta if defecto else _cuenta(['4511', '451'], 'Préstamos de instituciones financieras')
        cap = Asiento.objects.create(fecha=fin, libro='05', origen='MANUAL', creado_por=usuario,
                                     glosa=f'Intereses capitalizados (gracia total) {periodo[4:]}/{periodo[:4]}')
        for p, interes, pen in capitalizados:
            cap.lineas.create(cuenta=gasto, debe=pen, haber=D0, centro_costo=p.centro_costo, glosa=str(p)[:200])
            cap.lineas.create(cuenta=pasivo, debe=D0, haber=pen, glosa=str(p)[:200])
            DevengoPrestamo.objects.update_or_create(prestamo=p, periodo=periodo,
                                                     defaults={'interes': interes, 'asiento': cap})
        if not filas:
            return cap
    asiento = Asiento.objects.create(fecha=fin, libro='05', origen='MANUAL', creado_por=usuario,
                                     glosa=f'Intereses devengados de préstamos {periodo[4:]}/{periodo[:4]}')
    siguiente = fin + timedelta(days=1)
    extorno = None
    if not PeriodoContable.esta_cerrado(f'{siguiente:%Y%m}'):
        extorno = Asiento.objects.create(fecha=siguiente, libro='05', origen='MANUAL', creado_por=usuario,
                                         extorna=asiento, glosa=f'Extorno del devengo de intereses {periodo[4:]}/'
                                                                f'{periodo[:4]}')
    for p, interes, pen in filas:
        asiento.lineas.create(cuenta=gasto, debe=pen, haber=D0, centro_costo=p.centro_costo, glosa=str(p)[:200])
        asiento.lineas.create(cuenta=por_pagar, debe=D0, haber=pen, glosa=str(p)[:200])
        if extorno:
            extorno.lineas.create(cuenta=por_pagar, debe=pen, haber=D0, glosa=str(p)[:200])
            extorno.lineas.create(cuenta=gasto, debe=D0, haber=pen, centro_costo=p.centro_costo, glosa=str(p)[:200])
        DevengoPrestamo.objects.update_or_create(prestamo=p, periodo=periodo,
                                                 defaults={'interes': interes, 'asiento': asiento})
    return asiento


def deuda_corriente(hoy=None, consultar=True):
    """Reclasificación al cierre: por préstamo, capital que vence en los próximos 12 meses (corriente) y después (no
    corriente), en soles. consultar=False usa el tipo de cambio ya registrado (sin ir a SUNAT): el tablero financiero
    lo pide para cada uno de 12 cierres."""
    from core.tipo_cambio import venta_del_dia
    hoy = hoy or timezone.localdate()
    limite = _sumar_meses(hoy, 12)
    prestamos = Prestamo.objects.filter(estado='VIGENTE')
    tc = venta_del_dia(hoy, consultar) if prestamos.filter(moneda='USD').exists() else Decimal('1')
    filas = []
    for p in prestamos.select_related('entidad'):
        pendientes = p.cuotas.exclude(pagos__estado='VIGENTE')
        corriente = sum((c.capital for c in pendientes if c.fecha <= limite), D0)
        no_corriente = sum((c.capital for c in pendientes if c.fecha > limite), D0)
        f = 1 if p.moneda == 'PEN' else tc
        filas.append({'prestamo': p, 'corriente': r2(corriente * f), 'no_corriente': r2(no_corriente * f)})
    return filas


def resumen():
    """Deuda vigente por moneda y próximas cuotas (para la pantalla de préstamos)."""
    vigentes = Prestamo.objects.filter(estado='VIGENTE')
    saldos = {}
    for p in vigentes:
        saldos[p.moneda] = saldos.get(p.moneda, D0) + p.saldo_capital
    proximas = (CuotaPrestamo.objects.filter(prestamo__estado='VIGENTE').exclude(pagos__estado='VIGENTE')
                .select_related('prestamo__entidad').order_by('fecha')[:10])
    return saldos, proximas
