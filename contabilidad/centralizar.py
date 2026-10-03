"""Centralización contable por periodo.

Genera los asientos de: compras (libro 08), ventas (libro 14), caja y bancos (libro 01), movimientos de
inventario desde el kardex y diferencia de cambio de las cuentas en dólares (libro 05). Los asientos
automáticos de un periodo se regeneran completos; los manuales y la apertura no se tocan.

Reglas que mantienen los libros iguales a los auxiliares:
- Los importes en soles de cada comprobante (total_pen, igv_pen, ...) y de cada cobro/pago (monto_doc_pen)
  se calculan una sola vez y los usan el registro, las cuentas por cobrar/pagar y la contabilidad.
- La compra de mercadería va a 2811 (por recibir) vía destino de la 6011; el ingreso al almacén según el
  kardex la pasa a 20111. Ventas, guías y ajustes mueven la 20111 por el valor del kardex, y un ajuste final
  deja la 20111 igual a la valorización del inventario al cierre del mes.
- Cada caja o banco usa su propia subcuenta; los cobros/pagos de documentos en dólares registran la
  diferencia de cambio, y al cierre se ajustan los saldos en dólares al tipo de cambio del día.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from compras.models import Compra
from core.models import Kardex, r2
from finanzas.models import Cuenta, Movimiento
from ventas.models import Venta

from .models import ORIGENES_FIJOS, Asiento, AsientoLinea, CuentaDefecto, PeriodoContable

D0 = Decimal('0')


class ErrorContable(Exception):
    pass


class Borrador:
    """Acumula líneas de un asiento y las graba cuadradas."""

    def __init__(self, asiento, moneda='PEN', tc=Decimal('1')):
        self.asiento = asiento
        self.lineas = []
        self.moneda = moneda
        self.tc = tc or Decimal('1')

    def add(self, cuenta, debe=D0, haber=D0, tercero=None, documento='', glosa='', centro_costo=None,
            importe_me=None):
        debe, haber = r2(debe), r2(haber)
        # importes negativos pasan al lado contrario
        if debe < 0:
            debe, haber = D0, haber - debe
        if haber < 0:
            debe, haber = debe - haber, D0
        if not debe and not haber:
            return None
        linea = AsientoLinea(cuenta=cuenta, debe=debe, haber=haber, tercero=tercero, documento=documento,
                             glosa=glosa[:200], centro_costo=centro_costo)
        if self.moneda == 'USD' and importe_me is not None:
            linea.debe_me = r2(abs(importe_me)) if debe else D0
            linea.haber_me = r2(abs(importe_me)) if haber else D0
        self.lineas.append(linea)
        return linea

    def neto(self, cuenta_aumenta, contra, importe, **kw):
        """Importe positivo: debe a cuenta_aumenta y haber a contra; negativo al revés."""
        if importe >= 0:
            self.add(cuenta_aumenta, debe=importe, **kw)
            self.add(contra, haber=importe, **kw)
        else:
            self.add(contra, debe=-importe, **kw)
            self.add(cuenta_aumenta, haber=-importe, **kw)

    def invertir(self):
        for l in self.lineas:
            l.debe, l.haber = l.haber, l.debe
            l.debe_me, l.haber_me = l.haber_me, l.debe_me

    def agregar_destinos(self):
        destinos = []
        for l in self.lineas:
            c = l.cuenta
            if c.destino_debe_id and c.destino_haber_id:
                neto = l.debe - l.haber
                if neto:
                    destinos.append(AsientoLinea(cuenta=c.destino_debe, debe=max(neto, D0), haber=max(-neto, D0),
                                                 glosa=f'Destino {c.codigo}', centro_costo=l.centro_costo,
                                                 es_destino=True))
                    destinos.append(AsientoLinea(cuenta=c.destino_haber, debe=max(-neto, D0), haber=max(neto, D0),
                                                 glosa=f'Destino {c.codigo}', es_destino=True))
        self.lineas.extend(destinos)

    def grabar(self):
        d = sum(l.debe for l in self.lineas)
        h = sum(l.haber for l in self.lineas)
        if d != h:
            raise ErrorContable(f'{self.asiento.glosa}: no cuadra (debe {d} / haber {h})')
        if not self.lineas:
            return None
        self.asiento.save()
        for l in self.lineas:
            l.asiento = self.asiento
        AsientoLinea.objects.bulk_create(self.lineas)
        return self.asiento


def _fin_mes(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    siguiente = date(anio + (mes == 12), 1 if mes == 12 else mes + 1, 1)
    return date.fromordinal(siguiente.toordinal() - 1)


def _rango(periodo):
    return date(int(periodo[:4]), int(periodo[4:]), 1), _fin_mes(periodo)


def cuenta_caja(cuenta_fin, cta):
    from .automatico import asegurar_subcuenta
    return asegurar_subcuenta(cuenta_fin)


# ---------------------------------------------------------------- ventas
def asiento_venta(v, cta):
    a = Asiento(fecha=v.fecha_emision, libro='14', origen='VENTA', venta=v, moneda=v.moneda,
                tipo_cambio=v.tc_efectivo,
                glosa=f'{v.get_tipo_comprobante_display()} {v.numero_completo} {v.tercero.nombre}'[:250])
    b = Borrador(a, v.moneda, v.tc_efectivo)
    doc = f'{v.tipo_comprobante} {v.numero_completo}'
    b.add(cta['cliente'], debe=v.total_pen, tercero=v.tercero, documento=doc, importe_me=v.total)
    b.add(cta['igv'], haber=v.igv_pen, documento=doc, importe_me=v.igv)
    b.add(cta['icbper'], haber=v.icbper_pen, documento=doc, importe_me=v.icbper)
    # ingresos: bienes vs servicios según el detalle (sin detalle -> bienes)
    ingreso = v.base_pen + v.nograv_pen
    subt = defaultdict(lambda: D0)
    for i in v.items.select_related('producto'):
        subt['servicios' if i.producto and i.producto.tipo == 'SERVICIO' else 'bienes'] += i.subtotal
    total_items = sum(subt.values(), D0)
    if not total_items:
        subt, total_items = {'bienes': Decimal('1')}, Decimal('1')
    restante = ingreso
    claves = list(subt)
    for n, k in enumerate(claves):
        parte = restante if n == len(claves) - 1 else r2(ingreso * subt[k] / total_items)
        restante -= parte
        b.add(cta[f'ventas_{k}'], haber=parte, documento=doc, importe_me=(v.total - v.igv) * subt[k] / total_items)
    if v.ret_pen:
        b.add(cta['igv_retencion'], debe=v.ret_pen, documento=doc, glosa='Retención de IGV del cliente')
        b.add(cta['cliente'], haber=v.ret_pen, tercero=v.tercero, documento=doc, glosa='Retención de IGV del cliente')
    if v.perc_pen:
        b.add(cta['cliente'], debe=v.perc_pen, tercero=v.tercero, documento=doc, glosa='Percepción cobrada')
        b.add(cta['igv_percepcion'], haber=v.perc_pen, documento=doc, glosa='Percepción por pagar')
    if v.es_nota_credito:
        b.invertir()
    return b.grabar()


# ---------------------------------------------------------------- compras
def asiento_compra(c, cta):
    a = Asiento(fecha=c.fecha_emision, libro='08', origen='COMPRA', compra=c, moneda=c.moneda,
                tipo_cambio=c.tc_efectivo,
                glosa=f'{c.get_tipo_comprobante_display()} {c.numero_completo} {c.tercero.nombre}'[:250])
    b = Borrador(a, c.moneda, c.tc_efectivo)
    doc = f'{c.tipo_comprobante} {c.numero_completo}'
    honorarios = c.tipo_comprobante == '02'
    base = c.total_pen - c.igv_pen
    cuenta_gasto = c.cuenta_contable or cta.get(f'compra_{c.clasificacion}') or cta['compra_GASTO']
    b.add(cuenta_gasto, debe=base, documento=doc, centro_costo=c.centro_costo, importe_me=c.total - c.igv)
    b.add(cta['igv'], debe=c.igv_pen, documento=doc, importe_me=c.igv)
    cuenta_prov = cta['honorarios_por_pagar'] if honorarios else cta['proveedor']
    if c.ret_pen:
        b.add(cta['retencion_cuarta'] if honorarios else cta['igv_retencion'], haber=c.ret_pen, documento=doc,
              glosa='Retención de renta de 4ta categoría' if honorarios else 'Retención de IGV al proveedor')
    if c.perc_pen:
        b.add(cta['igv_percepcion'], debe=c.perc_pen, documento=doc, glosa='Percepción de IGV')
    b.add(cuenta_prov, haber=c.total_pen - c.ret_pen + c.perc_pen, tercero=c.tercero, documento=doc,
          importe_me=c.total - c.retencion_monto + c.percepcion_monto)
    if c.es_nota_credito:
        b.invertir()
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- caja y bancos
def asiento_movimiento(m, cta, tc_func):
    if m.concepto == 'TRANSFERENCIA' and m.tipo == 'INGRESO' and m.transferencia_par_id:
        return None  # se contabiliza una sola vez desde el egreso
    usd = m.cuenta.moneda == 'USD'
    doc_obj = m.venta or m.compra
    tc_dia = tc_func(m.fecha) if usd else Decimal('1')
    a = Asiento(fecha=m.fecha, libro='01', origen='TESORERIA', movimiento=m, moneda=m.cuenta.moneda,
                tipo_cambio=tc_dia, glosa=f'{m.voucher} {m.get_concepto_display()} {m.glosa}'[:250])
    b = Borrador(a, m.cuenta.moneda, tc_dia)
    caja = cuenta_caja(m.cuenta, cta)
    # el monto está en la moneda de la cuenta; en soles al T.C. del día si la cuenta es en dólares
    importe_caja = r2(m.monto * tc_dia) if usd else m.monto
    doc = f'{doc_obj.tipo_comprobante} {doc_obj.numero_completo}' if doc_obj else m.numero_operacion
    tercero = m.tercero or (doc_obj.tercero if doc_obj else None)
    if m.cuenta_contable_id:
        contra = m.cuenta_contable
    elif m.venta_id:
        contra = cta['cliente']
    elif m.compra_id:
        contra = cta['honorarios_por_pagar'] if m.compra.tipo_comprobante == '02' else cta['proveedor']
    elif m.concepto == 'TRANSFERENCIA':
        par = Movimiento.objects.filter(transferencia_par=m).select_related('cuenta').first()
        contra = cuenta_caja(par.cuenta, cta) if par else cta['egreso_otro']
    elif m.concepto == 'ANTICIPO':
        contra = cta['mov_ANTICIPO_INGRESO'] if m.tipo == 'INGRESO' else cta['mov_ANTICIPO_EGRESO']
    else:
        contra = cta.get(f'mov_{m.concepto}') or (cta['ingreso_otro'] if m.tipo == 'INGRESO' else cta['egreso_otro'])
    # el documento se cancela por el importe en soles con su propio tipo de cambio
    importe_doc = m.monto_doc_pen if doc_obj and not m.cuenta_contable_id else importe_caja
    diferencia = importe_caja - importe_doc
    if m.tipo == 'INGRESO':
        b.add(caja, debe=importe_caja, documento=doc, importe_me=m.monto)
        b.add(contra, haber=importe_doc, tercero=tercero, documento=doc, centro_costo=m.centro_costo,
              importe_me=m.monto)
        if diferencia > 0:
            b.add(cta['dif_cambio_ganancia'], haber=diferencia, documento=doc, glosa='Diferencia de cambio')
        elif diferencia < 0:
            b.add(cta['dif_cambio_perdida'], debe=-diferencia, documento=doc, glosa='Diferencia de cambio')
    else:
        b.add(contra, debe=importe_doc, tercero=tercero, documento=doc, centro_costo=m.centro_costo,
              importe_me=m.monto)
        b.add(caja, haber=importe_caja, documento=doc, importe_me=m.monto)
        if diferencia > 0:
            b.add(cta['dif_cambio_perdida'], debe=diferencia, documento=doc, glosa='Diferencia de cambio')
        elif diferencia < 0:
            b.add(cta['dif_cambio_ganancia'], haber=-diferencia, documento=doc, glosa='Diferencia de cambio')
    b.agregar_destinos()
    return b.grabar()


def asiento_cambio_cierre(periodo, cta, tc_obj):
    """Ajusta las cuentas en dólares al tipo de cambio de cierre (compra, por ser activos)."""
    if tc_obj is None:
        return None
    desde, hasta = _rango(periodo)
    a = Asiento(fecha=hasta, libro='05', origen='CAMBIO',
                glosa=f'Diferencia de cambio al cierre {periodo} (T.C. compra {tc_obj.compra})')
    b = Borrador(a)
    for c in Cuenta.objects.filter(moneda='USD'):
        sub = cuenta_caja(c, cta)
        objetivo = r2(c.saldo_al(hasta) * tc_obj.compra)
        agg = AsientoLinea.objects.filter(cuenta=sub, asiento__fecha__lte=hasta).aggregate(d=Sum('debe'), h=Sum('haber'))
        libro = r2((agg['d'] or D0) - (agg['h'] or D0))
        dif = objetivo - libro
        if dif > 0:
            b.add(sub, debe=dif, glosa=f'Ajuste T.C. {c}')
            b.add(cta['dif_cambio_ganancia'], haber=dif, glosa=f'Ajuste T.C. {c}')
        elif dif < 0:
            b.add(cta['dif_cambio_perdida'], debe=-dif, glosa=f'Ajuste T.C. {c}')
            b.add(sub, haber=-dif, glosa=f'Ajuste T.C. {c}')
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- inventario
def asiento_inventario(periodo, cta):
    """Movimientos del kardex del mes y ajuste final para que la 20111 iguale la valorización."""
    from core.inventario import valor_inventario
    desde, hasta = _rango(periodo)
    grupos = defaultdict(lambda: D0)
    for k in Kardex.objects.filter(fecha__range=[desde, hasta]):
        valor = r2(k.cantidad * k.costo_unitario) * (1 if k.tipo == 'ENTRADA' else -1)  # variación del inventario
        if k.origen == 'COMPRA':
            grupos['recepcion'] += valor
        elif k.origen in ('VENTA', 'GUIA'):
            grupos['costo'] += valor
        elif k.origen == 'AJUSTE' and k.concepto == 'INICIAL':
            grupos['inicial'] += valor
        elif k.origen == 'AJUSTE' and k.concepto in ('MERMA', 'CONSUMO'):
            grupos['merma'] += valor
        else:  # sobrantes y movimientos sin origen
            grupos['sobrante' if valor > 0 else 'merma'] += valor
    a = Asiento(fecha=hasta, libro='05', origen='INVENTARIO', glosa=f'Inventario y costo de ventas {periodo} (kardex)')
    b = Borrador(a)
    merc = cta['mercaderias']
    contras = {'recepcion': (cta['mercaderia_por_recibir'], 'Ingreso al almacén de compras'),
               'costo': (cta['costo_ventas'], 'Costo de ventas y despachos'),
               'inicial': (cta['inventario_inicial'], 'Inventario inicial'),
               'merma': (cta['inventario_merma'], 'Mermas, faltantes y consumo'),
               'sobrante': (cta['inventario_sobrante'], 'Sobrantes de inventario')}
    for clave, (contra, glosa) in contras.items():
        if grupos[clave]:
            b.neto(merc, contra, grupos[clave], glosa=glosa)
    # ajuste por valuación: la 20111 debe quedar igual al inventario valorizado del kardex
    agg = AsientoLinea.objects.filter(cuenta=merc, asiento__fecha__lte=hasta).aggregate(d=Sum('debe'), h=Sum('haber'))
    libro = (agg['d'] or D0) - (agg['h'] or D0) + sum(l.debe - l.haber for l in b.lineas if l.cuenta_id == merc.pk)
    dif = r2(valor_inventario(hasta)[1] - libro)
    if dif > 0:
        b.neto(merc, cta['inventario_sobrante'], dif, glosa='Ajuste por valuación (costo promedio)')
    elif dif < 0:
        b.neto(merc, cta['inventario_merma'], dif, glosa='Ajuste por valuación (costo promedio)')
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- proceso del periodo
def centralizar_periodo(periodo):
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorContable(f'El periodo {periodo} está cerrado.')
    cta = CuentaDefecto.mapa()
    from core.tipo_cambio import obtener, venta_del_dia
    cache_tc = {}

    def tc_func(fecha):
        if fecha not in cache_tc:
            cache_tc[fecha] = venta_del_dia(fecha)
        return cache_tc[fecha]

    desde, hasta = _rango(periodo)
    resumen = {'compras': 0, 'ventas': 0, 'tesoreria': 0, 'costo': 0, 'errores': []}
    with transaction.atomic():
        Asiento.objects.filter(periodo=periodo).exclude(origen__in=ORIGENES_FIJOS).delete()
        for c in (Compra.objects.filter(periodo=periodo, estado='REGISTRADO')
                  .select_related('tercero', 'cuenta_contable', 'centro_costo').order_by('fecha_emision', 'id')):
            try:
                if asiento_compra(c, cta):
                    resumen['compras'] += 1
            except ErrorContable as exc:
                resumen['errores'].append(str(exc))
        for v in (Venta.objects.filter(periodo=periodo, estado='REGISTRADO').select_related('tercero')
                  .order_by('fecha_emision', 'id')):
            try:
                if asiento_venta(v, cta):
                    resumen['ventas'] += 1
            except ErrorContable as exc:
                resumen['errores'].append(str(exc))
        for m in (Movimiento.objects.filter(fecha__range=[desde, hasta])
                  .select_related('cuenta', 'venta', 'compra', 'tercero', 'cuenta_contable', 'centro_costo')
                  .order_by('fecha', 'id')):
            try:
                if asiento_movimiento(m, cta, tc_func):
                    resumen['tesoreria'] += 1
            except ErrorContable as exc:
                resumen['errores'].append(str(exc))
        try:
            if asiento_inventario(periodo, cta):
                resumen['costo'] = 1
            asiento_cambio_cierre(periodo, cta, obtener(hasta) if Cuenta.objects.filter(moneda='USD').exists()
                                  else None)
        except ErrorContable as exc:
            resumen['errores'].append(str(exc))
        PeriodoContable.objects.update_or_create(
            periodo=periodo, defaults={'fecha_centralizacion': timezone.now(), 'pendiente': False})
    return resumen
