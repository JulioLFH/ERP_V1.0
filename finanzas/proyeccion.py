"""Flujo de caja proyectado por semana: saldo de hoy de caja y bancos más lo que se espera cobrar y pagar, con la
fecha de vencimiento de cada documento (lo vencido se espera en la primera semana).

Entradas: facturas de venta por cobrar, letras por cobrar y pedidos de venta aprobados aún no facturados.
Salidas: facturas de compra por pagar, letras por pagar, órdenes de compra aprobadas aún no facturadas, cuotas de
préstamos y leasing, y la planilla (neto a fin de mes y retenciones y aportes el 15 del mes siguiente, según la última
planilla mensual calculada). Todo en soles al tipo de cambio de hoy."""
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

D0 = Decimal('0')


def _fin_de_mes(d):
    siguiente = date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)
    return siguiente - timedelta(days=1)


def proyectar(semanas=12, hoy=None, cuentas=None):
    from compras.models import Compra, OrdenCompra
    from core.tipo_cambio import venta_del_dia
    from planillas.models import Planilla
    from ventas.models import Cotizacion, Venta

    from .models import Cuenta, CuotaPrestamo, Letra
    hoy = hoy or timezone.localdate()
    tc = venta_del_dia(hoy)
    a_soles = lambda monto, moneda: (monto or D0) * (tc if moneda == 'USD' else 1)
    fin = hoy + timedelta(days=7 * semanas - 1)
    rangos = [(hoy + timedelta(days=7 * i), hoy + timedelta(days=7 * i + 6)) for i in range(semanas)]

    def semana(fecha):
        if fecha is None or fecha <= hoy:
            return 0
        return (fecha - hoy).days // 7 if fecha <= fin else None

    filas = OrderedDict()

    def sumar(concepto, tipo, fecha, monto, doc=None):
        i = semana(fecha)
        if i is None or not monto:
            return
        f = filas.setdefault((tipo, concepto), {'concepto': concepto, 'tipo': tipo, 'valores': [D0] * semanas,
                                                'documentos': []})
        f['valores'][i] += monto
        if doc is not None and len(f['documentos']) < 200:
            f['documentos'].append({'doc': doc, 'fecha': fecha, 'monto': monto, 'semana': i + 1})

    qs_cuentas = Cuenta.objects.filter(activo=True)
    if cuentas is not None:
        qs_cuentas = qs_cuentas.filter(pk__in=cuentas)
    saldo_inicial = sum((a_soles(c.saldo, c.moneda) for c in qs_cuentas), D0)

    for v in (Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
              .select_related('tercero')):
        if v.saldo > 0:
            sumar('Cobranzas vencidas' if (v.fecha_vencimiento or v.fecha_emision) < hoy else 'Cobranzas por vencer',
                  'INGRESO', v.fecha_vencimiento or v.fecha_emision, v.saldo_pen, v)
    for c in (Compra.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
              .select_related('tercero')):
        if c.saldo > 0:
            sumar('Pagos a proveedores vencidos' if (c.fecha_vencimiento or c.fecha_emision) < hoy
                  else 'Pagos a proveedores por vencer', 'EGRESO', c.fecha_vencimiento or c.fecha_emision, c.saldo_pen,
                  c)
    for l in Letra.objects.filter(estado__in=Letra.ABIERTAS).select_related('tercero'):
        if l.tipo == 'COBRAR':
            sumar('Letras por cobrar', 'INGRESO', l.fecha_vencimiento, a_soles(l.monto, l.moneda), l)
        else:
            sumar('Letras por pagar', 'EGRESO', l.fecha_vencimiento, a_soles(l.monto, l.moneda), l)
    for p in Cotizacion.objects.filter(tipo='PED', estado='APROBADO').select_related('tercero'):
        dias = p.tercero.dias_credito or 0
        sumar('Pedidos aprobados por facturar', 'INGRESO', p.fecha + timedelta(days=dias),
              a_soles(p.total, p.moneda), p)
    for oc in OrdenCompra.objects.filter(estado='APROBADO').select_related('tercero'):
        llegada = oc.fecha_entrega or oc.fecha
        sumar('Órdenes de compra aprobadas por facturar', 'EGRESO', llegada + timedelta(days=oc.dias_credito or 0),
              a_soles(oc.total, oc.moneda), oc)
    for cuota in (CuotaPrestamo.objects.filter(prestamo__estado='VIGENTE', fecha__lte=fin)
                  .select_related('prestamo')):
        if not cuota.pagada:
            sumar('Préstamos y leasing', 'EGRESO', cuota.fecha, a_soles(cuota.cuota, cuota.prestamo.moneda), cuota)
    ultima = (Planilla.objects.filter(tipo='MENSUAL', estado__in=['CALCULADA', 'CERRADA', 'PAGADA'])
              .order_by('-periodo').first())
    if ultima:
        t = ultima.totales()
        mes = date(hoy.year, hoy.month, 1)
        while mes <= fin:
            pago = _fin_de_mes(mes)
            if pago >= hoy:
                sumar('Planilla (neto)', 'EGRESO', pago, t['n'])
            sumar('Retenciones y aportes de planilla', 'EGRESO', pago + timedelta(days=15), t['d'] + t['a'])
            mes = pago + timedelta(days=1)

    ingresos = [sum((f['valores'][i] for f in filas.values() if f['tipo'] == 'INGRESO'), D0) for i in range(semanas)]
    egresos = [sum((f['valores'][i] for f in filas.values() if f['tipo'] == 'EGRESO'), D0) for i in range(semanas)]
    saldos, saldo = [], saldo_inicial
    for i in range(semanas):
        saldo += ingresos[i] - egresos[i]
        saldos.append(saldo)
    lista = sorted(filas.values(), key=lambda f: (f['tipo'] != 'INGRESO', f['concepto']))
    for f in lista:
        f['total'] = sum(f['valores'], D0)
    negativa = next((i for i, s in enumerate(saldos) if s < 0), None)
    return {'hoy': hoy, 'tc': tc, 'semanas': rangos, 'saldo_inicial': saldo_inicial, 'filas': lista,
            'ingresos': ingresos, 'egresos': egresos, 'saldos': saldos,
            'semana_negativa': rangos[negativa] if negativa is not None else None,
            'planilla_base': ultima}
