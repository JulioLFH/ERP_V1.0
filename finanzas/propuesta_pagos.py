"""Propuesta de pagos (FIN-04): tesorería la arma con los vencimientos hasta una fecha, por prioridad (proveedores
críticos, descuento por pronto pago, vencidos) y hasta la caja disponible del banco menos el saldo mínimo; el gerente
la aprueba (no quien la armó) y recién entonces se genera el pago masivo con el archivo del banco. También el reporte
de descuentos por pronto pago ganados y perdidos."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import r2

from .models import LineaPropuestaPago, PropuestaPago, _siguiente

D0 = Decimal('0')


class ErrorPropuesta(Exception):
    pass


def descuento_disponible(compra, fecha):
    t = compra.tercero
    if not t.descuento_pronto_pago_pct or not t.dias_pronto_pago:
        return D0
    if fecha > compra.fecha_emision + timedelta(days=t.dias_pronto_pago):
        return D0
    return r2(compra.total * t.descuento_pronto_pago_pct / 100)


def prioridad(compra, fecha, descuento):
    if compra.tercero.proveedor_critico:
        return 1
    if descuento:
        return 2
    if (compra.fecha_vencimiento or compra.fecha_emision) < fecha:
        return 3
    return 4


MOTIVOS = {1: 'Proveedor crítico', 2: 'Descuento por pronto pago', 3: 'Vencida', 4: 'Por vencer'}


@transaction.atomic
def armar(cuenta, fecha, hasta, reserva, usuario):
    """Crea la propuesta con los comprobantes por pagar hasta «hasta» en la moneda del banco."""
    from compras.models import Compra
    if cuenta.moneda not in ('PEN', 'USD'):
        raise ErrorPropuesta('Elija un banco.')
    if hasta < fecha - timedelta(days=366):
        raise ErrorPropuesta('Revise la fecha de los vencimientos.')
    disponible = cuenta.saldo
    p = PropuestaPago.objects.create(numero=_siguiente('PPG', 'PP'), cuenta=cuenta, fecha=fecha, hasta=hasta,
                                     reserva=reserva or D0, disponible=disponible, creado_por=usuario)
    docs = [c for c in (Compra.objects.con_saldos().cobrables().filter(
        estado='REGISTRADO', moneda=cuenta.moneda, pago_bloqueado=False, fecha_vencimiento__lte=hasta)
        .exclude(tipo_comprobante='07').select_related('tercero')) if c.saldo > 0]
    filas = []
    for c in docs:
        a_pagar = r2(c.saldo - c.detraccion_por_depositar)  # la detracción va a la cuenta del BN del proveedor
        if a_pagar <= 0:
            continue
        descuento = descuento_disponible(c, fecha)
        filas.append((prioridad(c, fecha, descuento), c.fecha_vencimiento or c.fecha_emision, c, a_pagar, descuento))
    filas.sort(key=lambda f: (f[0], f[1], f[2].pk))
    caja = disponible - (reserva or D0)
    usado = D0
    for prio, _, c, a_pagar, descuento in filas:
        entra = usado + a_pagar <= caja
        if entra:
            usado += a_pagar
        LineaPropuestaPago.objects.create(
            propuesta=p, compra=c, prioridad=prio, monto=a_pagar, monto_pen=a_pagar, descuento=descuento,
            incluir=entra, motivo=MOTIVOS[prio] if entra else f'{MOTIVOS[prio]} · sin caja disponible')
    return p


def _editable(p):
    if p.estado not in ('BORRADOR', 'RECHAZADA'):
        raise ErrorPropuesta(f'La propuesta está {p.get_estado_display().lower()}: ya no se modifica.')


def cambiar(p, incluir_ids, montos=None):
    """Marca qué comprobantes entran (y su monto) antes de enviarla."""
    _editable(p)
    montos = montos or {}
    for l in p.lineas.select_related('compra'):
        l.incluir = l.pk in incluir_ids
        if l.pk in montos:
            m = montos[l.pk]
            if m <= 0 or m > l.compra.saldo:
                raise ErrorPropuesta(f'{l.compra}: el monto debe ser mayor que cero y no mayor que su saldo.')
            l.monto = l.monto_pen = m
        l.save(update_fields=['incluir', 'monto', 'monto_pen'])
    return p


def enviar(p, usuario):
    _editable(p)
    if not p.lineas.filter(incluir=True).exists():
        raise ErrorPropuesta('Incluya al menos un comprobante.')
    if p.total > p.cuenta.saldo - p.reserva:
        raise ErrorPropuesta(f'La propuesta suma {p.cuenta.simbolo} {p.total:,.2f} y la caja disponible es '
                             f'{p.cuenta.simbolo} {p.cuenta.saldo - p.reserva:,.2f}.')
    p.estado, p.motivo = 'ENVIADA', ''
    p.save(update_fields=['estado', 'motivo'])
    return p


def puede_aprobar(user, p):
    """FIN-10: quien la armó no la aprueba (tampoco el administrador), salvo que sea el único usuario activo."""
    from core.permisos import puede
    from core.segregacion import propio
    return (user.is_superuser or puede(user, 'tesoreria.aprobar_pagos')) and not propio(user, p)


def aprobar(p, usuario, aprobar_=True, motivo=''):
    if p.estado != 'ENVIADA':
        raise ErrorPropuesta('Solo se aprueba una propuesta enviada.')
    if not puede_aprobar(usuario, p):
        raise ErrorPropuesta('La aprueba la gerencia (permiso «Aprobar la propuesta de pagos») y no quien la armó.')
    if not aprobar_ and len((motivo or '').strip()) < 10:
        raise ErrorPropuesta('Indique el motivo del rechazo (mínimo 10 caracteres).')
    p.estado = 'APROBADA' if aprobar_ else 'RECHAZADA'
    p.motivo = '' if aprobar_ else motivo.strip()[:250]
    p.aprobado_por, p.aprobado_en = usuario, timezone.now()
    p.save(update_fields=['estado', 'motivo', 'aprobado_por', 'aprobado_en'])
    return p


@transaction.atomic
def generar(p, usuario):
    """Pago masivo (archivo del banco) con los comprobantes aprobados."""
    from .operaciones import crear_pago_masivo
    if p.estado != 'APROBADA':
        raise ErrorPropuesta('Primero la gerencia debe aprobar la propuesta.')
    lineas = [(l.compra, l.monto) for l in p.lineas.filter(incluir=True).select_related('compra__tercero')]
    pago = crear_pago_masivo(p.cuenta, p.fecha, lineas, usuario, f'Propuesta de pagos {p.numero}')
    p.estado, p.pago_masivo = 'GENERADA', pago
    p.save(update_fields=['estado', 'pago_masivo'])
    return pago


def descuentos(desde, hasta):
    """Descuentos por pronto pago ganados (pagado a tiempo) y perdidos de las compras del rango."""
    from compras.models import Compra

    from .models import Movimiento
    filas = []
    for c in (Compra.objects.filter(estado='REGISTRADO', fecha_emision__range=[desde, hasta],
                                    tercero__descuento_pronto_pago_pct__gt=0, tercero__dias_pronto_pago__gt=0)
              .exclude(tipo_comprobante='07').select_related('tercero')):
        limite = c.fecha_emision + timedelta(days=c.tercero.dias_pronto_pago)
        pagos = list(Movimiento.objects.filter(compra=c, tipo='EGRESO').values_list('fecha', flat=True))
        importe = r2(c.total * c.tercero.descuento_pronto_pago_pct / 100)
        if pagos and max(pagos) <= limite:
            estado = 'GANADO'
        elif pagos or limite < timezone.localdate():
            estado = 'PERDIDO'
        else:
            estado = 'DISPONIBLE'
        filas.append({'compra': c, 'limite': limite, 'pagado': max(pagos) if pagos else None, 'importe': importe,
                      'estado': estado})
    return filas
