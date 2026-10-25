"""Riesgo cambiario (FIN-07): posición en dólares (activos y pasivos en US$ por rubro y plazo), exposición neta con y
sin forwards, y sensibilidad del resultado a una variación de ±5 % y ±10 % del tipo de cambio."""
from decimal import Decimal

from django.utils import timezone

from core.models import r2

D0 = Decimal('0')
PLAZOS = [('Vencido', None, 0), ('0-30 días', 0, 30), ('31-90', 31, 90), ('91-180', 91, 180), ('181-365', 181, 365),
          ('Más de 1 año', 366, None)]
VARIACIONES = (Decimal('-10'), Decimal('-5'), Decimal('5'), Decimal('10'))


def _plazo(fecha, hoy):
    if fecha is None:
        return 1
    dias = (fecha - hoy).days
    if dias < 0:
        return 0
    for i, (_, desde, hasta) in enumerate(PLAZOS[1:], 1):
        if dias >= desde and (hasta is None or dias <= hasta):
            return i
    return len(PLAZOS) - 1


def posicion(hoy=None):
    from compras.models import Compra
    from core.tipo_cambio import venta_del_dia
    from ventas.models import Venta

    from .models import Cuenta, DepositoPlazo, Forward, Letra, Prestamo
    hoy = hoy or timezone.localdate()
    filas = {}

    def sumar(lado, rubro, monto, fecha=None):
        if not monto:
            return
        f = filas.setdefault((lado, rubro), {'lado': lado, 'rubro': rubro, 'plazos': [D0] * len(PLAZOS), 'total': D0})
        f['plazos'][_plazo(fecha, hoy)] += monto
        f['total'] += monto

    for c in Cuenta.objects.filter(activo=True, moneda='USD'):
        sumar('ACTIVO', 'Caja y bancos', c.saldo)
    for v in Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO', moneda='USD').exclude(
            tipo_comprobante='07'):
        if v.saldo > 0:
            sumar('ACTIVO', 'Cuentas por cobrar', v.saldo, v.fecha_vencimiento or v.fecha_emision)
    for d in DepositoPlazo.objects.filter(estado='VIGENTE', moneda='USD'):
        sumar('ACTIVO', 'Depósitos a plazo', d.monto + d.devengado, d.vencimiento)
    for c in Compra.objects.con_saldos().cobrables().filter(estado='REGISTRADO', moneda='USD').exclude(
            tipo_comprobante='07'):
        if c.saldo > 0:
            sumar('PASIVO', 'Cuentas por pagar', c.saldo, c.fecha_vencimiento or c.fecha_emision)
    for l in Letra.objects.filter(estado__in=Letra.ABIERTAS, moneda='USD'):
        sumar('ACTIVO' if l.tipo == 'COBRAR' else 'PASIVO', 'Letras', l.saldo, l.fecha_vencimiento)
    for p in Prestamo.objects.filter(estado='VIGENTE', moneda='USD'):
        for q in p.cuotas.exclude(pagos__estado='VIGENTE'):
            sumar('PASIVO', 'Préstamos y leasing (capital)', q.capital, q.fecha)
    for f in Forward.objects.filter(estado='VIGENTE'):
        sumar('FORWARD', 'Forwards de compra' if f.tipo == 'COMPRA' else 'Forwards de venta',
              f.monto_usd if f.tipo == 'COMPRA' else -f.monto_usd, f.vencimiento)
    lista = sorted(filas.values(), key=lambda f: ({'ACTIVO': 0, 'PASIVO': 1, 'FORWARD': 2}[f['lado']], f['rubro']))
    activos = sum((f['total'] for f in lista if f['lado'] == 'ACTIVO'), D0)
    pasivos = sum((f['total'] for f in lista if f['lado'] == 'PASIVO'), D0)
    forwards = sum((f['total'] for f in lista if f['lado'] == 'FORWARD'), D0)
    neta = activos - pasivos
    cubierta = neta + forwards
    tc = venta_del_dia(hoy)
    sensibilidad = [{'variacion': v, 'tc': r2(tc * (1 + v / 100)), 'sin_cobertura': r2(neta * tc * v / 100),
                     'con_cobertura': r2(cubierta * tc * v / 100)} for v in VARIACIONES]
    por_plazo = [sum(((f['plazos'][i] if f['lado'] != 'PASIVO' else -f['plazos'][i]) for f in lista), D0)
                 for i in range(len(PLAZOS))]
    return {'filas': lista, 'activos': activos, 'pasivos': pasivos, 'forwards': forwards, 'neta': neta,
            'cubierta': cubierta, 'tc': tc, 'sensibilidad': sensibilidad, 'plazos': [p[0] for p in PLAZOS],
            'neta_por_plazo': por_plazo, 'hoy': hoy}
