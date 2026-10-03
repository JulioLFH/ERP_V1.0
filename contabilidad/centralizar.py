"""Centralización contable automática por periodo.

Genera los asientos de: compras (libro 08), ventas (libro 14), movimientos de caja y bancos
(libro 01) y costo de ventas desde el kardex (libro 05). Los asientos automáticos de un periodo
se regeneran completos cada vez; los manuales no se tocan. Las cuentas de gasto con destino
(6x → 9x / 79, 6011 → 20111 / 6111) agregan sus líneas de destino en el mismo asiento.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from compras.models import Compra
from core.models import Kardex, r2
from finanzas.models import Movimiento
from ventas.models import Venta

from .models import Asiento, AsientoLinea, CuentaDefecto, PeriodoContable

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
            return
        linea = AsientoLinea(cuenta=cuenta, debe=debe, haber=haber, tercero=tercero, documento=documento,
                             glosa=glosa[:200], centro_costo=centro_costo)
        if self.moneda == 'USD' and importe_me is not None:
            linea.debe_me = r2(importe_me) if debe else D0
            linea.haber_me = r2(importe_me) if haber else D0
        self.lineas.append(linea)

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

    def ajustar_redondeo(self, linea_ajuste):
        """Diferencias de céntimos por tipo de cambio van a la línea indicada (normalmente el tercero)."""
        dif = sum(l.debe for l in self.lineas) - sum(l.haber for l in self.lineas)
        if dif and abs(dif) <= Decimal('0.10') and linea_ajuste is not None:
            if linea_ajuste.debe:
                linea_ajuste.debe -= dif
            else:
                linea_ajuste.haber += dif

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


# ---------------------------------------------------------------- ventas
def asiento_venta(v, cta):
    tc = v.tipo_cambio if v.moneda == 'USD' else Decimal('1')
    a = Asiento(fecha=v.fecha_emision, libro='14', origen='VENTA', venta=v, moneda=v.moneda, tipo_cambio=tc,
                glosa=f'{v.get_tipo_comprobante_display()} {v.numero_completo} {v.tercero.nombre}'[:250])
    b = Borrador(a, v.moneda, tc)
    doc = f'{v.tipo_comprobante} {v.numero_completo}'
    total, igv, icb = r2(v.total * tc), r2(v.igv * tc), r2(v.icbper * tc)
    base = total - igv - icb
    b.add(cta['cliente'], debe=total, tercero=v.tercero, documento=doc, importe_me=v.total)
    linea_cliente = b.lineas[-1] if b.lineas else None
    b.add(cta['igv'], haber=igv, documento=doc, importe_me=v.igv)
    b.add(cta['icbper'], haber=icb, documento=doc, importe_me=v.icbper)
    # ingresos: bienes vs servicios según el detalle (sin detalle → bienes)
    subt = defaultdict(lambda: D0)
    for i in v.items.select_related('producto'):
        subt['servicios' if i.producto and i.producto.tipo == 'SERVICIO' else 'bienes'] += i.subtotal
    total_items = sum(subt.values(), D0)
    if not total_items:
        subt, total_items = {'bienes': Decimal('1')}, Decimal('1')
    restante = base
    claves = list(subt)
    for n, k in enumerate(claves):
        parte = restante if n == len(claves) - 1 else r2(base * subt[k] / total_items)
        restante -= parte
        b.add(cta[f'ventas_{k}'], haber=parte, documento=doc, importe_me=v.total * subt[k] / total_items)
    if v.retencion_monto:
        ret = r2(v.retencion_monto * tc)
        b.add(cta['igv_retencion'], debe=ret, documento=doc, glosa='Retención de IGV del cliente')
        b.add(cta['cliente'], haber=ret, tercero=v.tercero, documento=doc, glosa='Retención de IGV del cliente')
    b.ajustar_redondeo(linea_cliente)
    if v.es_nota_credito:
        b.invertir()
    return b.grabar()


# ---------------------------------------------------------------- compras
def asiento_compra(c, cta):
    tc = c.tipo_cambio if c.moneda == 'USD' else Decimal('1')
    a = Asiento(fecha=c.fecha_emision, libro='08', origen='COMPRA', compra=c, moneda=c.moneda, tipo_cambio=tc,
                glosa=f'{c.get_tipo_comprobante_display()} {c.numero_completo} {c.tercero.nombre}'[:250])
    b = Borrador(a, c.moneda, tc)
    doc = f'{c.tipo_comprobante} {c.numero_completo}'
    honorarios = c.tipo_comprobante == '02'
    total, igv = r2(c.total * tc), r2(c.igv * tc)
    base = total - igv
    cuenta_gasto = c.cuenta_contable or cta.get(f'compra_{c.clasificacion}') or cta['compra_GASTO']
    b.add(cuenta_gasto, debe=base, documento=doc, centro_costo=c.centro_costo, importe_me=c.total - c.igv)
    b.add(cta['igv'], debe=igv, documento=doc, importe_me=c.igv)
    cuenta_prov = cta['honorarios_por_pagar'] if honorarios else cta['proveedor']
    ret = r2(c.retencion_monto * tc)
    perc = r2(c.percepcion_monto * tc)
    if ret:
        b.add(cta['retencion_cuarta'] if honorarios else cta['igv_retencion'], haber=ret, documento=doc,
              glosa='Retención de renta de 4ta categoría' if honorarios else 'Retención de IGV al proveedor')
    if perc:
        b.add(cta['igv_percepcion'], debe=perc, documento=doc, glosa='Percepción de IGV')
    b.add(cuenta_prov, haber=total - ret + perc, tercero=c.tercero, documento=doc,
          importe_me=c.total - c.retencion_monto + c.percepcion_monto)
    linea_prov = b.lineas[-1]
    b.ajustar_redondeo(linea_prov)
    if c.es_nota_credito:
        b.invertir()
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- caja y bancos
def _cuenta_caja(cuenta_fin, cta):
    if cuenta_fin.cuenta_contable_id:
        return cuenta_fin.cuenta_contable
    if cuenta_fin.es_detracciones:
        return cta['detracciones']
    return cta['caja'] if cuenta_fin.tipo == 'CAJA' else cta['bancos']


def asiento_movimiento(m, cta, tc_func):
    if m.concepto == 'TRANSFERENCIA' and m.tipo == 'INGRESO' and m.transferencia_par_id:
        return None  # se contabiliza una sola vez desde el egreso
    tc = tc_func(m.fecha) if m.cuenta.moneda == 'USD' else Decimal('1')
    a = Asiento(fecha=m.fecha, libro='01', origen='TESORERIA', movimiento=m, moneda=m.cuenta.moneda, tipo_cambio=tc,
                glosa=f'{m.voucher} {m.get_concepto_display()} {m.glosa}'[:250])
    b = Borrador(a, m.cuenta.moneda, tc)
    importe = r2(m.monto * tc)
    caja = _cuenta_caja(m.cuenta, cta)
    doc_obj = m.venta or m.compra
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
        contra = _cuenta_caja(par.cuenta, cta) if par else cta['egreso_otro']
    elif m.concepto == 'ANTICIPO':
        contra = cta['mov_ANTICIPO_INGRESO'] if m.tipo == 'INGRESO' else cta['mov_ANTICIPO_EGRESO']
    else:
        contra = cta.get(f'mov_{m.concepto}') or (cta['ingreso_otro'] if m.tipo == 'INGRESO' else cta['egreso_otro'])
    if m.tipo == 'INGRESO':
        b.add(caja, debe=importe, documento=doc, importe_me=m.monto)
        b.add(contra, haber=importe, tercero=tercero, documento=doc, centro_costo=m.centro_costo, importe_me=m.monto)
    else:
        b.add(contra, debe=importe, tercero=tercero, documento=doc, centro_costo=m.centro_costo, importe_me=m.monto)
        b.add(caja, haber=importe, documento=doc, importe_me=m.monto)
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- costo de ventas
def asiento_costo_ventas(periodo, cta):
    desde, hasta = _rango(periodo)
    costo = D0
    for k in Kardex.objects.filter(origen='VENTA', fecha__range=[desde, hasta]):
        valor = k.cantidad * k.costo_unitario
        costo += valor if k.tipo == 'SALIDA' else -valor
    costo = r2(costo)
    if not costo:
        return None
    a = Asiento(fecha=hasta, libro='05', origen='COSTO', glosa=f'Costo de ventas del periodo {periodo} (kardex)')
    b = Borrador(a)
    b.add(cta['costo_ventas'], debe=costo)
    b.add(cta['mercaderias'], haber=costo)
    if costo < 0:
        b.lineas = []
        b.add(cta['mercaderias'], debe=-costo)
        b.add(cta['costo_ventas'], haber=-costo)
    return b.grabar()


# ---------------------------------------------------------------- proceso del periodo
def centralizar_periodo(periodo):
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorContable(f'El periodo {periodo} está cerrado.')
    cta = CuentaDefecto.mapa()
    from core.tipo_cambio import venta_del_dia
    cache_tc = {}

    def tc_func(fecha):
        if fecha not in cache_tc:
            cache_tc[fecha] = venta_del_dia(fecha)
        return cache_tc[fecha]

    desde, hasta = _rango(periodo)
    resumen = {'compras': 0, 'ventas': 0, 'tesoreria': 0, 'costo': 0, 'errores': []}
    with transaction.atomic():
        Asiento.objects.filter(periodo=periodo).exclude(origen='MANUAL').delete()
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
        if asiento_costo_ventas(periodo, cta):
            resumen['costo'] = 1
        PeriodoContable.objects.update_or_create(periodo=periodo,
                                                 defaults={'fecha_centralizacion': timezone.now()})
    ajustes = Kardex.objects.filter(origen='AJUSTE', fecha__range=[desde, hasta]).count()
    if ajustes:
        resumen['errores'].append(f'Hay {ajustes} ajuste(s) de inventario en el periodo: regístrelos con un '
                                  'asiento manual (ej. inventario inicial contra 59, mermas a 6599).')
    return resumen
