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
    # FIN-12: se paga hoy o después, en un periodo abierto, y los vencimientos llegan al menos a la fecha de pago
    if fecha < timezone.localdate():
        raise ErrorPropuesta(f'La fecha de pago ({fecha:%d/%m/%Y}) no puede ser anterior a hoy.')
    from contabilidad.models import PeriodoContable
    if PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
        raise ErrorPropuesta(f'El periodo {fecha:%m/%Y} está cerrado.')
    if hasta < fecha:
        raise ErrorPropuesta(f'«Vencimientos hasta» ({hasta:%d/%m/%Y}) no puede ser anterior a la fecha de pago '
                             f'({fecha:%d/%m/%Y}).')
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
    # v1.117.0: ruta de aprobación por niveles según las reglas «Pagos de tesorería» (monto en soles); v1.117.3: las
    # reglas de un centro de costo aplican a los pagos de las facturas de ese centro, con lo que esos pagos suman
    from core import aprobaciones as motor
    p.niveles = motor.calcular_niveles_por_centros('PAGO', p.creado_por or usuario, total_en_soles(p),
                                                   montos_por_centro(p)) if motor.hay_reglas('PAGO') else []
    p.nivel_aprobado = 0
    p.save(update_fields=['estado', 'motivo', 'niveles', 'nivel_aprobado'])
    motor.registrar('PAGO', p, usuario, 'ENVIADA')
    return p


def umbral_aprobacion():
    """v1.117.0: monto (S/) desde el que un pago necesita aprobación según las reglas activas «Pagos de tesorería»;
    None si no hay reglas."""
    from django.db.models import Min

    from core.models import ReglaAprobacion
    return ReglaAprobacion.objects.filter(documento='PAGO', activo=True).aggregate(m=Min('monto_desde'))['m']


def en_soles(movimientos):
    from core.tipo_cambio import venta_del_dia
    return sum((m.monto if m.cuenta.moneda == 'PEN' else r2(m.monto * venta_del_dia(m.fecha))
                for m in movimientos), D0)


def error_pago_directo(movimientos):
    """Con reglas de aprobación de pagos, los pagos a proveedores desde el umbral no se registran directo: van por la
    propuesta de pagos (que pasa por sus niveles). Las detracciones (tributo) no cuentan."""
    umbral = umbral_aprobacion()
    pagos = [m for m in movimientos if m.concepto != 'DETRACCION']
    if umbral is None or not pagos:
        return ''
    total = en_soles(pagos)
    if total < umbral or (umbral == 0 and total == 0):
        return ''
    desde = f'desde S/ {umbral:,.2f}' if umbral else 'de cualquier monto'
    return (f'Los pagos {desde} necesitan aprobación (Ajustes › Reglas de aprobación › Pagos de tesorería): este '
            f'suma S/ {total:,.2f}. Inclúyalo en una propuesta de pagos (Tesorería › Propuestas de pago) y, ya '
            f'aprobada, genere el pago masivo.')


def centro_de(compra):
    """Centro de costo del pago: el de la factura o, si no lo tiene, el de su orden de compra."""
    return compra.centro_costo or (compra.orden_compra.centro_costo if compra.orden_compra_id else None)


def montos_por_centro(p):
    """v1.117.3: {centro de costo o None: importe en soles} de los comprobantes incluidos en la propuesta."""
    tc = Decimal('1')
    if p.cuenta.moneda != 'PEN':
        from core.tipo_cambio import venta_del_dia
        tc = venta_del_dia(p.fecha)
    montos = {}
    for l in p.lineas.filter(incluir=True).select_related('compra__centro_costo', 'compra__orden_compra__centro_costo'):
        c = centro_de(l.compra)
        montos[c] = montos.get(c, D0) + l.monto_pen
    return {c: r2(m * tc) for c, m in montos.items()}


def total_en_soles(p):
    if p.cuenta.moneda == 'PEN':
        return p.total
    from core.tipo_cambio import venta_del_dia
    return r2(p.total * venta_del_dia(p.fecha))


def nivel_pendiente(p):
    from core import aprobaciones as motor
    return motor.pendiente(p.niveles, p.nivel_aprobado) if p.niveles else None


def ruta(p):
    from core import aprobaciones as motor
    return motor.ruta('PAGO', p, p.niveles, p.nivel_aprobado, rechazado=p.estado == 'RECHAZADA') if p.niveles else []


@transaction.atomic
def anular(p, usuario, motivo):
    """FIN-12: quien la armó (o tesorería) retira o anula la propuesta mientras no esté aprobada ni con pago
    masivo; los comprobantes vuelven a estar disponibles para otra propuesta."""
    if p.estado in ('APROBADA', 'GENERADA', 'ANULADA'):
        raise ErrorPropuesta('La propuesta ya está anulada.' if p.estado == 'ANULADA' else
                             f'La propuesta está {p.get_estado_display().lower()}: ya no se retira.')
    if len((motivo or '').strip()) < 5:
        raise ErrorPropuesta('Indique el motivo (mínimo 5 caracteres).')
    from core.permisos import puede
    if not (usuario.is_superuser or usuario.pk == p.creado_por_id or puede(usuario, 'tesoreria.registrar')):
        raise ErrorPropuesta('La retira quien la armó o tesorería.')
    p.estado, p.motivo = 'ANULADA', motivo.strip()[:250]
    p.save(update_fields=['estado', 'motivo'])
    return p


def puede_aprobar(user, p):
    """FIN-10: quien la armó no la aprueba, salvo el administrador o que sea el único usuario activo.
    v1.117.0: con reglas «Pagos de tesorería», aprueba el usuario del nivel pendiente (su suplente o su jefe si
    escaló); sin reglas, la gerencia con el permiso «Aprobar la propuesta de pagos»."""
    from core.permisos import puede
    from core.segregacion import propio
    if propio(user, p):
        return False
    if p.niveles:
        from core import aprobaciones as motor
        return motor.puede_aprobar(user, 'PAGO', p.niveles, p.nivel_aprobado, p.creado_por_id)
    return user.is_superuser or puede(user, 'tesoreria.aprobar_pagos')


def aprobar(p, usuario, aprobar_=True, motivo=''):
    from core import aprobaciones as motor
    if p.estado != 'ENVIADA':
        raise ErrorPropuesta('Solo se aprueba una propuesta enviada.')
    if not puede_aprobar(usuario, p):
        n = nivel_pendiente(p)
        raise ErrorPropuesta(f'Le toca aprobarla a {n["nombre"]} (nivel {n["nivel"]}); quien la armó no la aprueba.'
                             if n else 'La aprueba la gerencia (permiso «Aprobar la propuesta de pagos») y no quien '
                                       'la armó.')
    if not aprobar_ and len((motivo or '').strip()) < 10:
        raise ErrorPropuesta('Indique el motivo del rechazo (mínimo 10 caracteres).')
    n = nivel_pendiente(p)
    if aprobar_ and n is not None:
        p.nivel_aprobado = n['nivel']
        motor.avanzar(p.niveles, p.nivel_aprobado)
        siguiente = nivel_pendiente(p)
        p.aprobado_por, p.aprobado_en = usuario, timezone.now()
        p.estado = 'ENVIADA' if siguiente else 'APROBADA'
        p.save(update_fields=['estado', 'niveles', 'nivel_aprobado', 'aprobado_por', 'aprobado_en'])
        motor.registrar('PAGO', p, usuario, 'APROBADA', motivo, n['nivel'])
        return p
    p.estado = 'APROBADA' if aprobar_ else 'RECHAZADA'
    p.motivo = '' if aprobar_ else motivo.strip()[:250]
    p.aprobado_por, p.aprobado_en = usuario, timezone.now()
    p.save(update_fields=['estado', 'motivo', 'aprobado_por', 'aprobado_en'])
    motor.registrar('PAGO', p, usuario, 'APROBADA' if aprobar_ else 'RECHAZADA', p.motivo,
                    n['nivel'] if n else None)
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
