"""Flujo de caja proyectado por semana (FIN-01) y presupuesto de caja mensual con escenarios (FIN-02).

Entradas: facturas de venta por cobrar, letras por cobrar y pedidos de venta aprobados aún no facturados (en la fecha
de entrega + la condición de pago, netos de detracción y de los anticipos del cliente). Las cotizaciones vigentes van
aparte, ponderadas por su probabilidad, y no suman al saldo.
Salidas: facturas de compra por pagar, letras por pagar, el saldo sin facturar de las órdenes de compra aprobadas,
cuotas de préstamos, leasing y arrendamientos, y la planilla (neto a fin de mes y retenciones y aportes el 15 del mes
siguiente, según la última planilla mensual calculada).
El presupuesto mensual suma el IGV y el pago a cuenta de la renta en su vencimiento de SUNAT, las gratificaciones
(julio y diciembre), la CTS (mayo y noviembre) y las partidas manuales, en tres escenarios.
Todo en soles al tipo de cambio de hoy, o solo los documentos de una moneda en esa moneda."""
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

D0 = Decimal('0')
UMBRAL_DETRACCION = Decimal('700')


def _fin_de_mes(d):
    siguiente = date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)
    return siguiente - timedelta(days=1)


def _mes_siguiente(d):
    return date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)


def _r2(v):
    return Decimal(v).quantize(Decimal('0.01'))


class _Motor:
    """Reúne los cobros y pagos esperados como eventos (fecha, tipo, concepto, monto, documento)."""

    def __init__(self, hoy, fin, moneda=None, cuentas=None):
        from core.tipo_cambio import venta_del_dia
        self.hoy, self.fin, self.moneda, self.cuentas = hoy, fin, moneda, cuentas
        self.tc = venta_del_dia(hoy)
        self.eventos, self.referenciales, self.planilla_base = [], [], None

    def monto(self, monto, moneda_doc):
        """Importe en la moneda del reporte; None si el documento es de otra moneda (reporte por moneda)."""
        if self.moneda:
            return (monto or D0) if moneda_doc == self.moneda else None
        return (monto or D0) * (self.tc if moneda_doc == 'USD' else 1)

    def sumar(self, concepto, tipo, fecha, monto, doc=None):
        if monto is None or not monto or (fecha is not None and fecha > self.fin):
            return
        self.eventos.append((fecha or self.hoy, tipo, concepto, monto, doc))

    def saldo_inicial(self):
        from .models import Cuenta
        qs = Cuenta.objects.filter(activo=True)
        if self.cuentas is not None:
            qs = qs.filter(pk__in=self.cuentas)
        return sum((m for m in (self.monto(c.saldo, c.moneda) for c in qs) if m is not None), D0)

    def documentos(self):
        from compras.models import Compra
        from ventas.models import Venta

        from .models import Letra
        for v in (Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
                  .select_related('tercero')):
            if v.saldo > 0:
                m = v.saldo_pen if not self.moneda else self.monto(v.saldo, v.moneda)
                fecha = v.fecha_vencimiento or v.fecha_emision
                self.sumar('Cobranzas vencidas' if fecha < self.hoy else 'Cobranzas por vencer', 'INGRESO', fecha, m, v)
        for c in (Compra.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
                  .select_related('tercero')):
            if c.saldo > 0:
                m = c.saldo_pen if not self.moneda else self.monto(c.saldo, c.moneda)
                fecha = c.fecha_vencimiento or c.fecha_emision
                self.sumar('Pagos a proveedores vencidos' if fecha < self.hoy else 'Pagos a proveedores por vencer',
                           'EGRESO', fecha, m, c)
        for l in Letra.objects.filter(estado__in=Letra.ABIERTAS).select_related('tercero'):
            self.sumar('Letras por cobrar' if l.tipo == 'COBRAR' else 'Letras por pagar',
                       'INGRESO' if l.tipo == 'COBRAR' else 'EGRESO', l.fecha_vencimiento, self.monto(l.monto, l.moneda),
                       l)

    def pedidos(self):
        """FIN-01: pedidos aprobados en la entrega + condición, netos de detracción y de los anticipos del cliente;
        cotizaciones vigentes aparte (ponderadas, referenciales)."""
        from core import parametros
        from ventas.models import Cotizacion
        from ventas.views import condicion_a_forma_pago

        from .operaciones import anticipos_disponibles
        bolsa = {}  # anticipos por aplicar de cada cliente (por moneda)

        def anticipos(tercero, moneda):
            clave = (tercero.pk, moneda)
            if clave not in bolsa:
                bolsa[clave] = sum((m.anticipo_disponible for m in anticipos_disponibles(tercero, 'INGRESO')
                                    if m.cuenta.moneda == moneda), D0)
            return clave

        for p in (Cotizacion.objects.filter(tipo='PED', estado='APROBADO').select_related('tercero')
                  .prefetch_related('items__producto').order_by('fecha', 'id')):
            _, dias = condicion_a_forma_pago(p.condicion_pago, p.tercero)
            total = p.total or D0
            total_pen = total * ((p.tipo_cambio or self.tc) if p.moneda == 'USD' else 1)
            pct = max((i.producto.detraccion_pct for i in p.items.all() if i.producto_id), default=D0)
            detraccion = _r2(total * pct / 100) if pct and total_pen >= UMBRAL_DETRACCION else D0
            clave = anticipos(p.tercero, p.moneda)
            usado = min(bolsa[clave], total - detraccion)
            bolsa[clave] -= usado
            neto = total - detraccion - usado
            p.flujo_detraccion, p.flujo_anticipo = detraccion, usado
            self.sumar('Pedidos aprobados por facturar (neto)', 'INGRESO',
                       (p.fecha_entrega or p.fecha) + timedelta(days=dias), self.monto(neto, p.moneda), p)
        prob_defecto = parametros.valor('FIN_PROB_COTIZACION_PCT')
        for c in (Cotizacion.objects.filter(tipo='COT', estado__in=['PENDIENTE', 'APROBADO'])
                  .select_related('tercero', 'oportunidad')):
            vence = c.fecha + timedelta(days=c.validez_dias or 0)
            if vence < self.hoy:
                continue
            prob = Decimal(c.oportunidad.prob) if c.oportunidad_id else prob_defecto
            if c.estado == 'APROBADO':  # aceptada por el cliente: falta el pedido
                prob = max(prob, Decimal('80'))
            _, dias = condicion_a_forma_pago(c.condicion_pago, c.tercero)
            m = self.monto(c.total, c.moneda)
            if m:
                self.referenciales.append((vence + timedelta(days=dias), 'Cotizaciones vigentes (ponderadas)',
                                           _r2(m * prob / 100), c))

    def compromisos(self):
        from compras.models import OrdenCompra
        from planillas.models import Planilla

        from .models import CuotaArrendamiento, CuotaPrestamo
        for oc in OrdenCompra.objects.filter(estado='APROBADO').select_related('tercero').prefetch_related('compras'):
            facturado = sum((c.total for c in oc.compras.all() if c.estado == 'REGISTRADO'
                             and c.tipo_comprobante not in ('07', '08')), D0)
            saldo = (oc.total or D0) - facturado
            if saldo > 0:
                llegada = oc.fecha_entrega or oc.fecha
                self.sumar('Órdenes de compra aprobadas por facturar', 'EGRESO',
                           llegada + timedelta(days=oc.dias_credito or 0), self.monto(saldo, oc.moneda), oc)
        for cuota in (CuotaPrestamo.objects.filter(prestamo__estado='VIGENTE', fecha__lte=self.fin)
                      .select_related('prestamo')):
            if not cuota.pagada:
                self.sumar('Préstamos y leasing', 'EGRESO', cuota.fecha, self.monto(cuota.cuota, cuota.prestamo.moneda),
                           cuota)
        for c in (CuotaArrendamiento.objects.filter(arrendamiento__estado='VIGENTE', fecha__gt=self.hoy,
                                                    fecha__lte=self.fin).select_related('arrendamiento')):
            self.sumar('Alquileres (arrendamientos NIIF 16)', 'EGRESO', c.fecha,
                       self.monto(c.cuota, c.arrendamiento.moneda), c)
        self.planilla_base = (Planilla.objects.filter(tipo='MENSUAL', estado__in=['CALCULADA', 'CERRADA', 'PAGADA'])
                              .order_by('-periodo').first())
        if self.planilla_base and self.moneda in (None, 'PEN'):
            t = self.planilla_base.totales()
            mes = date(self.hoy.year, self.hoy.month, 1)
            while mes <= self.fin:
                pago = _fin_de_mes(mes)
                if pago >= self.hoy:
                    self.sumar('Planilla (neto)', 'EGRESO', pago, t['n'])
                self.sumar('Retenciones y aportes de planilla', 'EGRESO', pago + timedelta(days=15), t['d'] + t['a'])
                mes = pago + timedelta(days=1)

    def partidas(self, escenario='BASE'):
        from .models import PartidaCaja
        for p in PartidaCaja.objects.filter(escenario__in=['', escenario]):
            fecha = p.fecha
            for _ in range(max(p.repetir_meses, 1)):
                if fecha > self.fin:
                    break
                if fecha >= self.hoy:
                    self.sumar(f'{p.concepto} (manual)', p.tipo, fecha, self.monto(p.monto, p.moneda), p)
                fecha = _sumar_meses(fecha, 1)

    def tributos_y_beneficios(self):
        """FIN-02: IGV y pago a cuenta de la renta en su vencimiento; gratificaciones y CTS en sus meses."""
        if self.moneda not in (None, 'PEN'):
            return
        from core import cronograma_sunat, parametros
        from core.models import Empresa
        empresa = Empresa.actual()
        ruc = empresa.ruc
        reales = {}
        mes = date(self.hoy.year, self.hoy.month, 1)
        for i in range(1, 4):  # los 3 meses cerrados anteriores
            mes = date(mes.year - (mes.month == 1), 12 if mes.month == 1 else mes.month - 1, 1)
            reales[f'{mes:%Y%m}'] = _impuestos_del_mes(mes)
        promedio_igv = sum((r[0] for r in reales.values()), D0) / 3
        promedio_ventas = sum((r[1] for r in reales.values()), D0) / 3
        coef = parametros.valor('FIN_RENTA_COEF_PCT') / 100
        periodo = date(self.hoy.year, self.hoy.month, 1)
        periodo = date(periodo.year - (periodo.month == 1), 12 if periodo.month == 1 else periodo.month - 1, 1)
        while True:
            clave = f'{periodo:%Y%m}'
            vence = cronograma_sunat.vencimiento(ruc, clave) or (_mes_siguiente(periodo) + timedelta(days=14))
            if vence > self.fin:
                break
            igv, ventas = reales.get(clave, (promedio_igv, promedio_ventas))
            if vence >= self.hoy:
                self.sumar('IGV por pagar', 'EGRESO', vence, _r2(max(igv, D0)))
                self.sumar('Pago a cuenta del impuesto a la renta', 'EGRESO', vence, _r2(max(ventas, D0) * coef))
            periodo = _mes_siguiente(periodo)
        from planillas.models import Trabajador
        factor = {'MICRO': Decimal('0'), 'PEQUENA': Decimal('0.5')}
        trabajadores = Trabajador.objects.filter(fecha_cese__isnull=True, sueldo__gt=0)
        grati = sum((t.sueldo * factor.get(t.regimen, Decimal('1')) for t in trabajadores), D0)
        for a in (self.hoy.year, self.hoy.year + 1):
            for m, concepto, monto in ((7, 'Gratificaciones (con bonificación extraordinaria)', grati * Decimal('1.09')),
                                       (12, 'Gratificaciones (con bonificación extraordinaria)', grati * Decimal('1.09')),
                                       (5, 'CTS (depósito semestral)', grati * 7 / 12),
                                       (11, 'CTS (depósito semestral)', grati * 7 / 12)):
                if date(a, m, 15) >= self.hoy:
                    self.sumar(concepto, 'EGRESO', date(a, m, 15), _r2(monto))


def _sumar_meses(fecha, meses):
    import calendar
    mes = fecha.month - 1 + meses
    anio, mes = fecha.year + mes // 12, mes % 12 + 1
    return date(anio, mes, min(fecha.day, calendar.monthrange(anio, mes)[1]))


def _impuestos_del_mes(mes):
    """(IGV neto a pagar, ventas netas) del mes según los comprobantes registrados."""
    from django.db.models import Sum

    from compras.models import Compra
    from ventas.models import Venta
    fin = _fin_de_mes(mes)

    def suma(qs, campo):
        return qs.aggregate(s=Sum(campo))['s'] or D0
    ventas = Venta.objects.filter(estado='REGISTRADO', fecha_emision__range=[mes, fin])
    compras = Compra.objects.filter(estado='REGISTRADO', periodo=f'{mes:%Y%m}')
    signo = lambda qs, campo: suma(qs.exclude(tipo_comprobante='07'), campo) - suma(qs.filter(tipo_comprobante='07'),
                                                                                     campo)
    igv = signo(ventas, 'igv') - signo(compras, 'igv')
    base = signo(ventas, 'base_imponible') + signo(ventas, 'no_gravado')
    return igv, base


def _tabla(eventos, n, indice):
    filas = OrderedDict()
    for fecha, tipo, concepto, monto, doc in eventos:
        i = indice(fecha)
        if i is None:
            continue
        f = filas.setdefault((tipo, concepto), {'concepto': concepto, 'tipo': tipo, 'valores': [D0] * n,
                                                'documentos': []})
        f['valores'][i] += monto
        if doc is not None and len(f['documentos']) < 200:
            f['documentos'].append({'doc': doc, 'fecha': fecha, 'monto': monto, 'semana': i + 1})
    lista = sorted(filas.values(), key=lambda f: (f['tipo'] != 'INGRESO', f['concepto']))
    for f in lista:
        f['total'] = sum(f['valores'], D0)
    return lista


def _totales(lista, n, saldo_inicial):
    ingresos = [sum((f['valores'][i] for f in lista if f['tipo'] == 'INGRESO'), D0) for i in range(n)]
    egresos = [sum((f['valores'][i] for f in lista if f['tipo'] == 'EGRESO'), D0) for i in range(n)]
    iniciales, saldos, saldo = [], [], saldo_inicial
    for i in range(n):
        iniciales.append(saldo)
        saldo += ingresos[i] - egresos[i]
        saldos.append(saldo)
    return ingresos, egresos, iniciales, saldos


def proyectar(semanas=12, hoy=None, cuentas=None, moneda=None):
    hoy = hoy or timezone.localdate()
    fin = hoy + timedelta(days=7 * semanas - 1)
    rangos = [(hoy + timedelta(days=7 * i), hoy + timedelta(days=7 * i + 6)) for i in range(semanas)]
    m = _Motor(hoy, fin, moneda, cuentas)
    m.documentos()
    m.pedidos()
    m.compromisos()
    m.partidas()

    def semana(fecha):
        if fecha is None or fecha <= hoy:
            return 0
        return (fecha - hoy).days // 7 if fecha <= fin else None

    saldo_inicial = m.saldo_inicial()
    lista = _tabla(m.eventos, semanas, semana)
    ingresos, egresos, iniciales, saldos = _totales(lista, semanas, saldo_inicial)
    referencial = _tabla([(f, 'REFERENCIAL', c, v, d) for f, c, v, d in m.referenciales], semanas, semana)
    negativa = next((i for i, s in enumerate(saldos) if s < 0), None)
    return {'hoy': hoy, 'tc': m.tc, 'semanas': rangos, 'saldo_inicial': saldo_inicial, 'filas': lista,
            'ingresos': ingresos, 'egresos': egresos, 'saldos': saldos, 'saldos_iniciales': iniciales,
            'referenciales': referencial, 'moneda': moneda or 'PEN',
            'semana_negativa': rangos[negativa] if negativa is not None else None,
            'planilla_base': m.planilla_base}


ESCENARIOS = [('BASE', 'Base'), ('OPTIMISTA', 'Optimista'), ('PESIMISTA', 'Pesimista')]


def presupuesto_caja(meses=12, hoy=None, escenario='BASE', moneda=None, cuentas=None):
    """FIN-02: presupuesto de caja mensual (mes actual + los siguientes) en el escenario elegido."""
    from core import parametros
    hoy = hoy or timezone.localdate()
    inicio = date(hoy.year, hoy.month, 1)
    columnas = [_sumar_meses(inicio, i) for i in range(meses)]
    fin = _fin_de_mes(columnas[-1])
    m = _Motor(hoy, fin, moneda, cuentas)
    m.documentos()
    m.pedidos()
    m.compromisos()
    m.partidas(escenario)
    m.tributos_y_beneficios()
    factor = Decimal('1')
    if escenario == 'OPTIMISTA':
        factor += parametros.valor('FIN_ESC_OPTIMISTA_PCT') / 100
    elif escenario == 'PESIMISTA':
        factor -= parametros.valor('FIN_ESC_PESIMISTA_PCT') / 100
    eventos = [(f, t, c, _r2(v * factor) if t == 'INGRESO' and not c.endswith('(manual)') else v, d)
               for f, t, c, v, d in m.eventos]

    def mes(fecha):
        if fecha is None or fecha <= hoy:
            return 0
        return (fecha.year - inicio.year) * 12 + fecha.month - inicio.month if fecha <= fin else None

    saldo_inicial = m.saldo_inicial()
    lista = _tabla(eventos, meses, mes)
    ingresos, egresos, iniciales, saldos = _totales(lista, meses, saldo_inicial)
    referencial = _tabla([(f, 'REFERENCIAL', c, v, d) for f, c, v, d in m.referenciales], meses, mes)
    return {'hoy': hoy, 'tc': m.tc, 'meses': columnas, 'saldo_inicial': saldo_inicial, 'filas': lista,
            'ingresos': ingresos, 'egresos': egresos, 'saldos': saldos, 'saldos_iniciales': iniciales,
            'referenciales': referencial, 'escenario': escenario, 'moneda': moneda or 'PEN',
            'mes_negativo': next((columnas[i] for i, s in enumerate(saldos) if s < 0), None)}


def guardar(datos, usuario, moneda_param=''):
    """Foto del presupuesto para el real vs proyectado."""
    from .models import ProyeccionGuardada
    return ProyeccionGuardada.objects.create(
        escenario=datos['escenario'], moneda=moneda_param or 'TODO', creado_por=usuario,
        datos={f'{c:%Y%m}': {'ingresos': str(datos['ingresos'][i]), 'egresos': str(datos['egresos'][i])}
               for i, c in enumerate(datos['meses'])})


def real_del_mes(periodo, moneda=None):
    """(ingresos, egresos) reales de caja y bancos del mes (sin transferencias), en soles o en la moneda."""
    from core.tipo_cambio import venta_del_dia

    from .models import Movimiento
    a, mm = int(periodo[:4]), int(periodo[4:])
    desde = date(a, mm, 1)
    qs = Movimiento.objects.filter(fecha__range=[desde, _fin_de_mes(desde)]).exclude(concepto='TRANSFERENCIA')
    if moneda:
        qs = qs.filter(cuenta__moneda=moneda)
    tcs, ing, egr = {}, D0, D0
    for fecha, tipo, monto, mon in qs.values_list('fecha', 'tipo', 'monto', 'cuenta__moneda'):
        if mon == 'USD' and not moneda:
            if fecha not in tcs:
                tcs[fecha] = venta_del_dia(fecha)
            monto = monto * tcs[fecha]
        if tipo == 'INGRESO':
            ing += monto
        else:
            egr += monto
    return _r2(ing), _r2(egr)


def real_vs_proyectado(guardada, hoy=None):
    hoy = hoy or timezone.localdate()
    moneda = None if guardada.moneda == 'TODO' else guardada.moneda
    filas = []
    for periodo in sorted(guardada.datos):
        if periodo > f'{hoy:%Y%m}':
            break
        p = guardada.datos[periodo]
        pi, pe = Decimal(p['ingresos']), Decimal(p['egresos'])
        ri, re_ = real_del_mes(periodo, moneda)
        filas.append({'periodo': periodo, 'p_ingresos': pi, 'r_ingresos': ri, 'd_ingresos': ri - pi,
                      'p_egresos': pe, 'r_egresos': re_, 'd_egresos': re_ - pe,
                      'pct_ingresos': _r2((ri - pi) * 100 / pi) if pi else None,
                      'pct_egresos': _r2((re_ - pe) * 100 / pe) if pe else None})
    return filas
