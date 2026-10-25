"""Financiamiento e inversiones (FIN-05): uso de las líneas de crédito por banco (préstamos, cartas fianza y factoring),
depósitos a plazo con devengo mensual de intereses, factura negociable y su cesión (factoring), y la central de cartas
fianza de la empresa (las de Finanzas y las de las obras) con alerta de vencimiento."""
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import r2

from .models import CartaFianza, CesionFactura, DepositoPlazo, LineaCredito, Movimiento
from .operaciones import ErrorTesoreria, _periodo_abierto, _validar_documento

D0 = Decimal('0')


# ---------------------------------------------------------------- líneas de crédito
def uso(linea):
    """{'prestamos', 'fianzas', 'factoring', 'utilizado', 'disponible', 'pct'} de la línea (en su moneda)."""
    prestamos = sum((p.saldo_capital for p in linea.prestamos.filter(estado='VIGENTE', moneda=linea.moneda)), D0)
    fianzas = sum((f.monto for f in linea.fianzas.filter(estado__in=['VIGENTE', 'RENOVADA'], moneda=linea.moneda)), D0)
    factoring = sum((c.monto for c in linea.cesiones.filter(estado='CEDIDA')), D0)
    utilizado = prestamos + fianzas + factoring
    return {'prestamos': prestamos, 'fianzas': fianzas, 'factoring': factoring, 'utilizado': utilizado,
            'disponible': linea.limite - utilizado,
            'pct': r2(utilizado * 100 / linea.limite) if linea.limite else None}


def validar_uso(linea, monto, moneda):
    """Error si la operación excede el disponible de la línea (o es de otra moneda)."""
    if linea is None:
        return
    if not linea.activa or (linea.vencimiento and linea.vencimiento < timezone.localdate()):
        raise ErrorTesoreria(f'La línea {linea} no está vigente.')
    if moneda != linea.moneda:
        raise ErrorTesoreria(f'La línea {linea} es en {linea.moneda}.')
    disponible = uso(linea)['disponible']
    if monto > disponible:
        raise ErrorTesoreria(f'La línea {linea} tiene disponible {disponible:,.2f}: no alcanza para {monto:,.2f}.')


def lineas_con_uso():
    salida = []
    for l in LineaCredito.objects.select_related('banco'):
        l.uso = uso(l)
        salida.append(l)
    return salida


# ---------------------------------------------------------------- depósitos a plazo
def interes_entre(monto, tea, desde, hasta):
    dias = max((hasta - desde).days, 0)
    return r2(monto * ((1 + Decimal(tea) / 100) ** (Decimal(dias) / 360) - 1)) if dias else D0


@transaction.atomic
def crear_deposito(dpf, usuario):
    """Saca el capital del banco de origen (cuenta 1062 depósitos a plazo)."""
    from obras.servicios import _cuenta
    if dpf.monto <= 0 or dpf.tasa < 0:
        raise ErrorTesoreria('Indique el capital y la tasa del depósito.')
    if dpf.vencimiento <= dpf.inicio:
        raise ErrorTesoreria('El depósito vence después de su inicio.')
    if dpf.cuenta.moneda != dpf.moneda:
        raise ErrorTesoreria(f'La cuenta {dpf.cuenta} no es de la moneda del depósito.')
    _periodo_abierto(dpf.inicio)
    error = dpf.cuenta.error_sobregiro(dpf.monto, fecha=dpf.inicio)
    if error:
        raise ErrorTesoreria(error)
    dpf.devengado_hasta = dpf.inicio
    dpf.save()
    Movimiento.objects.create(cuenta=dpf.cuenta, fecha=dpf.inicio, tipo='EGRESO', concepto='DEPOSITO',
                              tercero=dpf.banco, monto=dpf.monto, creado_por=usuario,
                              cuenta_contable=_cuenta(['1062', '106'], 'Depósitos a plazo'),
                              glosa=f'Apertura del {dpf}'[:250], numero_operacion=dpf.numero[:40])
    return dpf


@transaction.atomic
def devengar_depositos(periodo, usuario):
    """Asiento del mes con los intereses devengados de los depósitos vigentes: Debe 1683 / Haber 7721."""
    from contabilidad.models import Asiento, PeriodoContable
    from obras.servicios import _cuenta

    from .proyeccion import _fin_de_mes
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorTesoreria(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado.')
    fin = _fin_de_mes(date(int(periodo[:4]), int(periodo[4:]), 1))
    filas = []
    for d in DepositoPlazo.objects.filter(estado='VIGENTE').select_related('banco'):
        hasta = min(fin, d.vencimiento)
        desde = d.devengado_hasta or d.inicio
        if hasta <= desde:
            continue
        acumulado = interes_entre(d.monto, d.tasa, d.inicio, hasta)
        interes = acumulado - d.devengado
        if interes > 0:
            filas.append((d, interes, hasta, acumulado))
    if not filas:
        raise ErrorTesoreria('No hay intereses por devengar en el mes.')
    por_cobrar = _cuenta(['1683', '168'], 'Intereses por cobrar')
    ingreso = _cuenta(['7721', '772'], 'Intereses de depósitos en instituciones financieras')
    asiento = Asiento.objects.create(fecha=fin, libro='05', origen='MANUAL', creado_por=usuario,
                                     glosa=f'Intereses devengados de depósitos a plazo {periodo[4:]}/{periodo[:4]}')
    for d, interes, hasta, acumulado in filas:
        pen = interes if d.moneda == 'PEN' else r2(interes * _tc(hasta))
        asiento.lineas.create(cuenta=por_cobrar, debe=pen, haber=D0, glosa=str(d)[:200])
        asiento.lineas.create(cuenta=ingreso, debe=D0, haber=pen, glosa=str(d)[:200])
        d.devengado, d.devengado_hasta = acumulado, hasta
        d.save(update_fields=['devengado', 'devengado_hasta'])
    return asiento


def _tc(fecha):
    from core.tipo_cambio import venta_del_dia
    return venta_del_dia(fecha)


@transaction.atomic
def cancelar_deposito(dpf, fecha, usuario):
    """Al vencer: devuelve el capital (1062) y los intereses (1683 lo devengado; el resto, ingreso 7721)."""
    from obras.servicios import _cuenta
    if dpf.estado != 'VIGENTE':
        raise ErrorTesoreria(f'{dpf} ya está {dpf.get_estado_display().lower()}.')
    _periodo_abierto(fecha)
    total = interes_entre(dpf.monto, dpf.tasa, dpf.inicio, min(fecha, dpf.vencimiento))
    Movimiento.objects.create(cuenta=dpf.cuenta, fecha=fecha, tipo='INGRESO', concepto='DEPOSITO', tercero=dpf.banco,
                              monto=dpf.monto, creado_por=usuario,
                              cuenta_contable=_cuenta(['1062', '106'], 'Depósitos a plazo'),
                              glosa=f'Cancelación del {dpf}: capital'[:250])
    if dpf.devengado:
        Movimiento.objects.create(cuenta=dpf.cuenta, fecha=fecha, tipo='INGRESO', concepto='DEPOSITO',
                                  tercero=dpf.banco, monto=min(dpf.devengado, total), creado_por=usuario,
                                  cuenta_contable=_cuenta(['1683', '168'], 'Intereses por cobrar'),
                                  glosa=f'Cancelación del {dpf}: intereses devengados'[:250])
    if total > dpf.devengado:
        Movimiento.objects.create(cuenta=dpf.cuenta, fecha=fecha, tipo='INGRESO', concepto='OTRO', tercero=dpf.banco,
                                  monto=total - dpf.devengado, creado_por=usuario,
                                  cuenta_contable=_cuenta(['7721', '772'], 'Intereses de depósitos'),
                                  glosa=f'Cancelación del {dpf}: intereses del último tramo'[:250])
    dpf.estado = 'CANCELADO'
    dpf.save(update_fields=['estado'])
    return dpf


# ---------------------------------------------------------------- factura negociable y factoring
@transaction.atomic
def ceder(cesion, cuenta, usuario):
    """Factoring o confirming: la entidad paga el monto cedido de la factura (cobranza) y descuenta interés y
    comisión (gasto financiero 6739 desde el mismo banco). La factura negociable solo se anota."""
    from obras.servicios import _cuenta
    venta = type(cesion.venta).objects.con_saldos().get(pk=cesion.venta_id)
    if cesion.tipo == 'NEGOCIABLE':
        if not cesion.codigo_cavali:
            raise ErrorTesoreria('Indique el código de anotación en CAVALI.')
        cesion.estado = 'ANOTADA'
        cesion.save()
        return cesion
    if cesion.entidad is None or cuenta is None:
        raise ErrorTesoreria('Indique la entidad de factoring y el banco donde abona.')
    if cesion.monto <= 0 or cesion.interes < 0 or cesion.comision < 0 or cesion.neto <= 0:
        raise ErrorTesoreria('Revise el monto cedido, el interés y la comisión.')
    _validar_documento(venta, cesion.monto, cuenta.moneda)
    validar_uso(cesion.linea, cesion.monto, cuenta.moneda)
    _periodo_abierto(cesion.fecha)
    cesion.estado = 'CEDIDA'
    cesion.save()
    cesion.movimiento = Movimiento.objects.create(
        cuenta=cuenta, fecha=cesion.fecha, tipo='INGRESO', concepto='COBRANZA', tercero=venta.tercero, venta=venta,
        monto=cesion.monto, monto_doc=cesion.monto, creado_por=usuario,
        glosa=f'{cesion.get_tipo_display()} con {cesion.entidad.nombre}: {venta}'[:250])
    cesion.save(update_fields=['movimiento'])
    costo = cesion.interes + cesion.comision
    if costo:
        Movimiento.objects.create(cuenta=cuenta, fecha=cesion.fecha, tipo='EGRESO', concepto='GASTO_BANCARIO',
                                  tercero=cesion.entidad, monto=costo, creado_por=usuario,
                                  cuenta_contable=_cuenta(['6739', '673'], 'Intereses y gastos de factoring'),
                                  glosa=f'Interés y comisión del {cesion.get_tipo_display().lower()} de {venta}'[:250])
    return cesion


# ---------------------------------------------------------------- cartas fianza
def central_fianzas():
    """Cartas fianza de la empresa (Finanzas) y de las obras, con días por vencer."""
    from obras.models import GarantiaContrato
    hoy = timezone.localdate()
    filas = []
    for c in CartaFianza.objects.select_related('banco', 'beneficiario', 'linea'):
        filas.append({'origen': 'Finanzas', 'numero': c.numero, 'banco': c.banco.nombre,
                      'beneficiario': c.beneficiario.nombre, 'tipo': c.get_tipo_display(), 'moneda': c.moneda,
                      'monto': c.monto, 'vencimiento': c.vencimiento, 'estado': c.get_estado_display(),
                      'dias': (c.vencimiento - hoy).days, 'alerta': c.por_vencer, 'linea': str(c.linea or ''),
                      'obj': c})
    for g in GarantiaContrato.objects.select_related('obra__cliente'):
        filas.append({'origen': f'Obra {g.obra.codigo}', 'numero': g.numero, 'banco': g.entidad,
                      'beneficiario': g.obra.cliente.nombre if g.obra.cliente_id else '', 'tipo': g.get_tipo_display(),
                      'moneda': 'PEN', 'monto': g.monto, 'vencimiento': g.vencimiento, 'estado': g.get_estado_display(),
                      'dias': (g.vencimiento - hoy).days, 'alerta': g.por_vencer, 'linea': '', 'obj': g})
    return sorted(filas, key=lambda f: f['vencimiento'])


def fianzas_por_vencer(dias=15):
    hoy = timezone.localdate()
    return [f for f in central_fianzas() if f['estado'] == 'Vigente' and f['vencimiento'] <= hoy + timedelta(days=dias)]
