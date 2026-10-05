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
            importe_me=None, centro_beneficio=None):
        debe, haber = r2(debe), r2(haber)
        # importes negativos pasan al lado contrario
        if debe < 0:
            debe, haber = D0, haber - debe
        if haber < 0:
            debe, haber = debe - haber, D0
        if not debe and not haber:
            return None
        if centro_beneficio is None and centro_costo is not None:
            centro_beneficio = centro_costo.beneficio  # la línea de negocio sale del centro de costo
        linea = AsientoLinea(cuenta=cuenta, debe=debe, haber=haber, tercero=tercero, documento=documento,
                             glosa=glosa[:200], centro_costo=centro_costo, centro_beneficio=centro_beneficio)
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
                    destino = _destino_por_centro(c.destino_debe, l.centro_costo)
                    destinos.append(AsientoLinea(cuenta=destino, debe=max(neto, D0), haber=max(-neto, D0),
                                                 glosa=f'Destino {c.codigo}', centro_costo=l.centro_costo,
                                                 centro_beneficio=l.centro_beneficio, es_destino=True))
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


_DESTINOS = {}


def _destino_por_centro(destino, centro):
    """Gastos de un centro de producción van al costo de producción (90), de ventas a la 95 y de administración a
    la 94, en vez del destino general de la cuenta (gastos financieros 97 se respetan)."""
    if centro is None or not destino.codigo.startswith(('94', '95')):
        return destino
    codigo = centro.DESTINO.get(centro.tipo)
    if not codigo or destino.codigo.startswith(codigo[:2]):
        return destino
    if codigo not in _DESTINOS:
        from .models import CuentaContable
        _DESTINOS[codigo] = CuentaContable.objects.filter(codigo=codigo).first()
    return _DESTINOS[codigo] or destino


def _fin_mes(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    siguiente = date(anio + (mes == 12), 1 if mes == 12 else mes + 1, 1)
    return date.fromordinal(siguiente.toordinal() - 1)


def _rango(periodo):
    return date(int(periodo[:4]), int(periodo[4:]), 1), _fin_mes(periodo)


def _repartir(total, pesos):
    """[(clave, parte)] de total proporcional a pesos {clave: peso}; el último lleva el redondeo."""
    suma = sum(pesos.values(), D0)
    claves = list(pesos)
    partes, restante = [], total
    for n, k in enumerate(claves):
        parte = restante if n == len(claves) - 1 else r2(total * pesos[k] / suma)
        restante -= parte
        partes.append((k, parte))
    return partes


def cuenta_caja(cuenta_fin, cta):
    from .automatico import asegurar_subcuenta
    return asegurar_subcuenta(cuenta_fin)


# ---------------------------------------------------------------- ventas
def asiento_saldo_inicial(d, cta, es_venta):
    """Documento pendiente de antes de usar el sistema: 12 (o 42) contra la apertura (5911)."""
    a = Asiento(fecha=d.fecha_emision, libro='05', origen='VENTA' if es_venta else 'COMPRA', moneda=d.moneda,
                tipo_cambio=d.tc_efectivo, glosa=f'Saldo inicial {d.get_tipo_comprobante_display()} '
                                                 f'{d.numero_completo} {d.tercero.nombre}'[:250],
                **({'venta': d} if es_venta else {'compra': d}))
    b = Borrador(a, d.moneda, d.tc_efectivo)
    doc = f'{d.tipo_comprobante} {d.numero_completo}'
    tercero_cta = cta['cliente'] if es_venta else (cta['honorarios_por_pagar'] if d.tipo_comprobante == '02'
                                                   else cta['proveedor'])
    if es_venta:
        b.add(tercero_cta, debe=d.total_pen, tercero=d.tercero, documento=doc, importe_me=d.total)
        b.add(cta['apertura_patrimonio'], haber=d.total_pen, documento=doc, glosa='Apertura: cuentas por cobrar')
    else:
        b.add(cta['apertura_patrimonio'], debe=d.total_pen, documento=doc, glosa='Apertura: cuentas por pagar')
        b.add(tercero_cta, haber=d.total_pen, tercero=d.tercero, documento=doc, importe_me=d.total)
    return b.grabar()


def asiento_venta(v, cta):
    if v.es_saldo_inicial:
        return asiento_saldo_inicial(v, cta, es_venta=True)
    a = Asiento(fecha=v.fecha_emision, libro='14', origen='VENTA', venta=v, moneda=v.moneda,
                tipo_cambio=v.tc_efectivo,
                glosa=f'{v.get_tipo_comprobante_display()} {v.numero_completo} {v.tercero.nombre}'[:250])
    b = Borrador(a, v.moneda, v.tc_efectivo)
    doc = f'{v.tipo_comprobante} {v.numero_completo}'
    b.add(cta['cliente'], debe=v.total_pen, tercero=v.tercero, documento=doc, importe_me=v.total)
    b.add(cta['igv'], haber=v.igv_pen, documento=doc, importe_me=v.igv)
    b.add(cta['icbper'], haber=v.icbper_pen, documento=doc, importe_me=v.icbper)
    # ingresos: cuenta de ventas de cada producto (sin producto o sin cuenta -> bienes / servicios)
    ingreso = v.base_pen + v.nograv_pen
    # el ingreso se separa por cuenta de ventas y por línea de negocio de cada producto (resultado por línea)
    centro = v.centro_costo
    linea_defecto = centro.beneficio if centro else None
    subt = defaultdict(lambda: D0)
    for i in v.items.select_related('producto__cuenta_venta', 'producto__centro_beneficio'):
        p = i.producto
        if p and p.cuenta_venta_id:
            cuenta = p.cuenta_venta
        else:
            cuenta = cta['ventas_servicios'] if p and p.tipo == 'SERVICIO' else cta['ventas_bienes']
        subt[(cuenta, (p.centro_beneficio if p and p.centro_beneficio_id else linea_defecto))] += i.subtotal
    if not sum(subt.values(), D0):
        subt = {(cta['ventas_bienes'], linea_defecto): Decimal('1')}
    total_items = sum(subt.values(), D0)
    for (cuenta, linea), parte in _repartir(ingreso, subt):
        b.add(cuenta, haber=parte, documento=doc, centro_costo=centro, centro_beneficio=linea,
              importe_me=(v.total - v.igv) * subt[(cuenta, linea)] / total_items)
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
    if c.es_saldo_inicial:
        return asiento_saldo_inicial(c, cta, es_venta=False)
    a = Asiento(fecha=c.fecha_emision, libro='08', origen='COMPRA', compra=c, moneda=c.moneda,
                tipo_cambio=c.tc_efectivo,
                glosa=f'{c.get_tipo_comprobante_display()} {c.numero_completo} {c.tercero.nombre}'[:250])
    b = Borrador(a, c.moneda, c.tc_efectivo)
    doc = f'{c.tipo_comprobante} {c.numero_completo}'
    honorarios = c.tipo_comprobante == '02'
    base = c.total_pen - c.igv_pen
    cuenta_gasto = c.cuenta_contable or cta.get(f'compra_{c.clasificacion}') or cta['compra_GASTO']
    # la cuenta elegida en el comprobante manda; si no, la cuenta de compra de cada producto
    pesos = defaultdict(lambda: D0)
    if not c.cuenta_contable_id:
        for i in c.items.select_related('producto__cuenta_compra'):
            pesos[i.producto.cuenta_compra if i.producto and i.producto.cuenta_compra_id else cuenta_gasto] += \
                i.subtotal
    if not sum(pesos.values(), D0):
        pesos = {cuenta_gasto: Decimal('1')}
    total_items = sum(pesos.values(), D0)
    for cuenta, parte in _repartir(base, pesos):
        b.add(cuenta, debe=parte, documento=doc, centro_costo=c.centro_costo,
              importe_me=(c.total - c.igv) * pesos[cuenta] / total_items)
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
    """Movimientos del kardex del mes y ajuste final para que cada cuenta de existencias (20, 21, 23, 24, 25)
    iguale la valorización de los productos que la usan.

    Cada producto aporta su juego de cuentas: existencias, costo de ventas y "por recibir" (destino de su
    cuenta de compra, ej. 6011 -> 2811, 6021 -> 2841).
    """
    from core.inventario import valor_inventario
    from core.models import Producto
    from inventario.models import TipoOperacion
    desde, hasta = _rango(periodo)
    tipos = {t.codigo: t for t in TipoOperacion.objects.select_related('cuenta_contable')}
    merc_def = cta['mercaderias']
    productos = {p.pk: p for p in Producto.objects.select_related(
        'cuenta_existencias', 'cuenta_costo', 'cuenta_compra__destino_debe', 'centro_beneficio')}

    def existencias(p):
        return p.cuenta_existencias if p and p.cuenta_existencias_id else merc_def

    def por_recibir(p):
        c = p.cuenta_compra if p and p.cuenta_compra_id else None
        return c.destino_debe if c and c.destino_debe_id and c.destino_debe.codigo.startswith('28') \
            else cta['mercaderia_por_recibir']

    def costo(p):
        return p.cuenta_costo if p and p.cuenta_costo_id else cta['costo_ventas']

    from .models import CuentaContable
    por_codigo = {c.codigo: c for c in CuentaContable.objects.filter(codigo__regex=r'^(61|71)')}
    # manufactura (PCGE): el insumo consumido va a la variación de existencias (61) y el producto obtenido a la
    # variación de la producción almacenada (71), según la cuenta de existencias de cada producto
    VARIACION = {'24': '6121', '25': '6132', '20': '6111', '23': '7131', '21': '7111'}
    PRODUCCION = {'23': '7131', '21': '7111'}

    def contra_manufactura(ex, entrada):
        codigo = (PRODUCCION.get(ex.codigo[:2], '7111') if entrada else VARIACION.get(ex.codigo[:2], '6111'))
        return por_codigo.get(codigo)

    grupos = defaultdict(lambda: D0)   # (existencias, contrapartida, glosa, línea) -> variación del inventario
    sin_contra = defaultdict(lambda: D0)  # traslados: solo cambian de almacén o de cuenta de existencias
    for k in Kardex.objects.filter(fecha__range=[desde, hasta]):
        p = productos.get(k.producto_id)
        ex = existencias(p)
        linea = p.centro_beneficio if p and p.centro_beneficio_id else None  # inventario y costo por línea
        valor = r2(k.cantidad * k.costo_unitario) * (1 if k.tipo == 'ENTRADA' else -1)
        if k.origen == 'OPERACION':
            tipo = tipos.get(k.concepto)
            if tipo and tipo.clase == 'MANUFACTURA' and contra_manufactura(ex, k.tipo == 'ENTRADA'):
                contra = contra_manufactura(ex, k.tipo == 'ENTRADA')
                glosa = 'Producción almacenada' if k.tipo == 'ENTRADA' else 'Consumo de insumos en producción'
                grupos[(ex, contra, glosa, linea)] += valor
                continue
            if k.cuenta_contra_id:  # consumo con su propia cuenta de gasto y centro de costo (requerimientos)
                grupos[(ex, k.cuenta_contra, f'Consumo: {k.cuenta_contra.nombre[:50]}', linea, k.centro_costo)] += valor
                continue
            if not (tipo and tipo.cuenta_contable_id):
                sin_contra[ex] += valor
                continue
            contra = tipo.cuenta_contable
            # las cuentas generales del tipo se reemplazan por las del producto
            if contra.pk == cta['mercaderia_por_recibir'].pk:
                contra = por_recibir(p)
            elif contra.pk == cta['costo_ventas'].pk:
                contra = costo(p)
            grupos[(ex, contra, f'Operaciones de inventario ({tipo.cuenta_contable.nombre[:50]})', linea,
                    k.centro_costo)] += valor
        elif k.origen == 'COMPRA':
            grupos[(ex, por_recibir(p), 'Ingreso al almacén de compras', linea)] += valor
        elif k.origen in ('VENTA', 'GUIA'):
            grupos[(ex, costo(p), 'Costo de ventas y despachos', linea)] += valor
        elif k.origen == 'AJUSTE' and k.concepto == 'INICIAL':
            grupos[(ex, cta['inventario_inicial'], 'Inventario inicial', linea)] += valor
        elif k.origen == 'AJUSTE' and k.concepto in ('MERMA', 'CONSUMO'):
            grupos[(ex, cta['inventario_merma'], 'Mermas, faltantes y consumo', linea)] += valor
        elif valor > 0:  # sobrantes y movimientos sin origen
            grupos[(ex, cta['inventario_sobrante'], 'Sobrantes de inventario', linea)] += valor
        else:
            grupos[(ex, cta['inventario_merma'], 'Mermas, faltantes y consumo', linea)] += valor
    a = Asiento(fecha=hasta, libro='05', origen='INVENTARIO', glosa=f'Inventario y costo de ventas {periodo} (kardex)')
    b = Borrador(a)
    for clave, valor in sorted(grupos.items(), key=lambda x: (x[0][0].codigo, x[0][1].codigo)):
        ex, contra, glosa, linea = clave[:4]
        centro = clave[4] if len(clave) > 4 else None
        if valor:
            b.neto(ex, contra, valor, glosa=glosa, centro_beneficio=linea or (centro.beneficio if centro else None),
                   centro_costo=centro)
    # diferencia de precio factura vs recepción: liquida la 28 contra el inventario (lo que sigue en stock) y el
    # costo de ventas (lo ya vendido o consumido)
    from compras.models import AjustePrecioCompra
    for aj in AjustePrecioCompra.objects.filter(fecha__range=[desde, hasta]).select_related('compra'):
        p = productos.get(aj.producto_id)
        doc = f'{aj.compra.tipo_comprobante} {aj.compra.numero_completo}'
        if aj.a_inventario:
            b.neto(existencias(p), por_recibir(p), aj.a_inventario, documento=doc, glosa='Diferencia de precio de compra')
        if aj.a_costo:
            b.neto(costo(p), por_recibir(p), aj.a_costo, documento=doc,
                   glosa='Diferencia de precio de compra (mercadería ya vendida)')
    # traslados entre cuentas de existencias (ej. manufactura: 2411 -> 2111); el redondeo va al ajuste final
    for ex, valor in sorted(sin_contra.items(), key=lambda x: x[0].codigo):
        b.add(ex, debe=valor, glosa='Traslados y manufactura')
    neto = sum((l.debe - l.haber for l in b.lineas), D0)
    if neto:
        b.add(cta['inventario_sobrante'] if neto > 0 else cta['inventario_merma'], debe=-neto,
              glosa='Redondeo de traslados y manufactura')
    # ajuste por valuación: cada cuenta de existencias debe quedar igual al inventario valorizado del kardex
    objetivo = defaultdict(lambda: D0)
    for fila in valor_inventario(hasta)[0]:
        objetivo[existencias(productos.get(fila['p'].pk))] += fila['valor']
    cuentas_ex = set(objetivo) | {merc_def} | {clave[0] for clave in grupos} | set(sin_contra)
    for ex in sorted(cuentas_ex, key=lambda c: c.codigo):
        agg = AsientoLinea.objects.filter(cuenta=ex, asiento__fecha__lte=hasta).aggregate(d=Sum('debe'), h=Sum('haber'))
        libro = (agg['d'] or D0) - (agg['h'] or D0) + sum(l.debe - l.haber for l in b.lineas if l.cuenta_id == ex.pk)
        dif = r2(objetivo[ex] - libro)
        if dif > 0:
            b.neto(ex, cta['inventario_sobrante'], dif, glosa='Ajuste por valuación (costo promedio)')
        elif dif < 0:
            b.neto(ex, cta['inventario_merma'], dif, glosa='Ajuste por valuación (costo promedio)')
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- activos fijos
def asiento_activos(periodo, cta):
    """Depreciación del mes (68 / 39 por centro de costo), reclasificación de activos comprados con otra cuenta
    y bajas (39 + costo neto 655 contra la cuenta del activo). El alta la registra la compra o la apertura."""
    from activos.models import ActivoFijo, Depreciacion
    desde, hasta = _rango(periodo)
    a = Asiento(fecha=hasta, libro='05', origen='ACTIVOS', glosa=f'Activos fijos {periodo}: depreciación y bajas')
    b = Borrador(a)
    for af in (ActivoFijo.objects.exclude(estado='ANULADO').filter(fecha_alta__range=[desde, hasta])
               .exclude(cuenta_origen=None).select_related('categoria__cuenta_activo', 'cuenta_origen')):
        if af.cuenta_origen_id != af.categoria.cuenta_activo_id:
            b.add(af.categoria.cuenta_activo, debe=af.valor, documento=af.codigo, glosa=f'Reclasificación {af}')
            b.add(af.cuenta_origen, haber=af.valor, documento=af.codigo, glosa=f'Reclasificación {af}')
    grupos = defaultdict(lambda: D0)
    for d in Depreciacion.objects.filter(periodo=periodo).select_related(
            'activo__categoria__cuenta_gasto', 'activo__categoria__cuenta_depreciacion', 'activo__centro_costo'):
        cat = d.activo.categoria
        if cat.cuenta_gasto_id and cat.cuenta_depreciacion_id:
            grupos[(cat.cuenta_gasto, cat.cuenta_depreciacion, d.activo.centro_costo)] += d.cuota
    for (gasto, acumulada, centro), valor in sorted(grupos.items(), key=lambda x: (x[0][0].codigo, x[0][1].codigo)):
        b.add(gasto, debe=valor, centro_costo=centro, glosa='Depreciación del mes')
        b.add(acumulada, haber=valor, glosa='Depreciación del mes')
    for af in (ActivoFijo.objects.filter(estado='BAJA', fecha_baja__range=[desde, hasta])
               .select_related('categoria__cuenta_activo', 'categoria__cuenta_depreciacion')
               .prefetch_related('depreciaciones')):
        dep = af.depreciacion_registrada if af.categoria.cuenta_depreciacion_id else D0
        glosa = f'Baja {af} ({af.get_motivo_baja_display()})'
        if dep:
            b.add(af.categoria.cuenta_depreciacion, debe=dep, documento=af.codigo, glosa=glosa)
        b.add(cta['baja_activo'], debe=af.valor - dep, documento=af.codigo, centro_costo=af.centro_costo, glosa=glosa)
        b.add(af.categoria.cuenta_activo, haber=af.valor, documento=af.codigo, glosa=glosa)
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- proceso del periodo
def centralizar_periodo(periodo):
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorContable(f'El periodo {periodo} está cerrado.')
    cta = CuentaDefecto.mapa()
    _DESTINOS.clear()
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
            asiento_activos(periodo, cta)
            asiento_cambio_cierre(periodo, cta, obtener(hasta) if Cuenta.objects.filter(moneda='USD').exists()
                                  else None)
        except ErrorContable as exc:
            resumen['errores'].append(str(exc))
        PeriodoContable.objects.update_or_create(
            periodo=periodo, defaults={'fecha_centralizacion': timezone.now(), 'pendiente': False})
    return resumen
