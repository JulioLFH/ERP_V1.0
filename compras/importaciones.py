"""Costeo de importaciones: los gastos vinculados (flete, seguro, ad valorem, agente, almacenaje…) se prorratean a
los productos de las facturas comerciales y se suman a su costo (como las diferencias de precio: lo que sigue en stock
revaloriza el costo promedio; lo ya vendido va al costo de ventas).

Cada gasto se reparte con su propio método (o el de la importación): por valor FOB, cantidad, peso o un reparto
manual en % por producto. Mientras un concepto no tiene su factura real, se usa su monto estimado; al llegar la real,
volver a liquidar ajusta la diferencia (IMP-04).

Contabilidad: cada gasto se registra en la 6091 (costos vinculados), cuyo destino lo deja en la 2811 (por recibir);
la liquidación lo pasa de la 2811 al inventario (o al costo de ventas)."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import Serie, r2

from .models import GastoEstimado, GastoImportacion, Importacion
from .precios import _aplicar

D0 = Decimal('0')
CLAVE = {'VALOR': 'fob', 'CANTIDAD': 'cantidad', 'PESO': 'peso'}


class ErrorImportacion(Exception):
    pass


def crear(imp):
    if imp.compra_id is None and imp.orden_compra_id is None:
        raise ErrorImportacion('Indique la orden de compra o la factura comercial.')
    s, n = Serie.siguiente('IMP', 'IM')
    imp.numero = f'{s}-{n}'
    imp.save()
    return imp


def _cuenta_vinculados():
    from contabilidad import pcge
    from contabilidad.models import CuentaContable
    return CuentaContable.objects.filter(codigo=pcge.codigo('6091')).first()


@transaction.atomic
def agregar_gasto(imp, concepto, monto, descripcion='', compra=None, movimiento=None, metodo=''):
    if compra is None and movimiento is None:
        raise ErrorImportacion('Indique la factura del gasto (o la DUA) o el pago de caja / bancos.')
    if compra is not None and compra in imp.facturas_comerciales:
        raise ErrorImportacion('La factura comercial no es un gasto: elija la factura del flete, agente, la DUA, etc.')
    if compra is not None and GastoImportacion.objects.filter(compra=compra).exists():
        raise ErrorImportacion(f'{compra} ya está en una importación.')
    if monto is None:  # la DUA aporta sus tributos al costo (ad valorem): su IGV es crédito fiscal
        monto = (compra.total_pen - compra.igv_pen) if compra is not None else movimiento.monto
    if monto <= 0:
        raise ErrorImportacion('El monto debe ser mayor a cero.')
    vinculados = _cuenta_vinculados()
    # el gasto va a la 6091 (y por su destino a la 2811) para que la liquidación lo lleve al costo
    if compra is not None and compra.cuenta_contable_id is None and vinculados:
        compra.cuenta_contable = vinculados
        compra.save(update_fields=['cuenta_contable'])
    if movimiento is not None and movimiento.cuenta_contable_id is None and vinculados:
        movimiento.cuenta_contable = vinculados
        movimiento.save(update_fields=['cuenta_contable'])
    return GastoImportacion.objects.create(importacion=imp, concepto=concepto, compra=compra, movimiento=movimiento,
                                           monto=monto, descripcion=descripcion[:150], metodo=metodo or '')


def estimar(imp, concepto, monto, metodo=''):
    """Fija (o quita con 0) el gasto estimado de un concepto."""
    if monto is None or monto < 0:
        raise ErrorImportacion('Monto estimado inválido.')
    if not monto:
        GastoEstimado.objects.filter(importacion=imp, concepto=concepto).delete()
        return None
    est, _ = GastoEstimado.objects.update_or_create(importacion=imp, concepto=concepto,
                                                    defaults={'monto': monto, 'metodo': metodo or ''})
    return est


def _lineas(imp):
    """{producto_id: {'producto', 'compra', 'cantidad' (unidad de almacén), 'fob' S/, 'peso', 'unidades'}} de las
    facturas comerciales (o de la OC mientras no hay factura)."""
    filas = {}
    fuentes = [(c, c.items.filter(producto__isnull=False).select_related('producto'), c.tc_efectivo)
               for c in imp.facturas_comerciales]
    if not fuentes and imp.orden_compra_id:
        oc = imp.orden_compra
        tc = oc.tipo_cambio if oc.moneda == 'USD' else Decimal('1')
        fuentes = [(None, oc.items.filter(producto__isnull=False).select_related('producto'), tc)]
    for compra, items, tc in fuentes:
        for i in items:
            f = filas.setdefault(i.producto_id, {'producto': i.producto, 'compra': compra, 'cantidad': D0, 'fob': D0,
                                                 'peso': D0})
            cant = i.producto.a_stock(i.cantidad)
            f['cantidad'] += cant
            f['fob'] += r2(i.subtotal * tc)
            f['peso'] += cant * (i.producto.peso or D0)
    return filas


def conceptos(imp):
    """[(concepto, monto, metodo, es_estimado)]: la factura real de cada concepto o, si aún no llega, su estimado."""
    reales = defaultdict(list)
    for g in imp.gastos.all():
        reales[g.concepto].append(g)
    salida = [(c, g.monto, g.metodo or imp.metodo, False) for c, gs in reales.items() for g in gs]
    salida += [(e.concepto, e.monto, e.metodo or imp.metodo, True) for e in imp.estimados.all()
               if e.concepto not in reales]
    return salida


def avisos(imp, filas=None):
    """Advertencias del prorrateo: productos sin peso cuando se reparte por peso y unidades distintas por cantidad."""
    filas = filas if filas is not None else _lineas(imp)
    metodos = {m for _, _, m, _ in conceptos(imp)} | {imp.metodo}
    salida = []
    if 'PESO' in metodos:
        sin_peso = [f['producto'] for f in filas.values() if not f['peso']]
        if sin_peso:
            salida.append(('PESO', 'Sin peso registrado (no reciben gastos repartidos por peso): ' +
                           ', '.join(f'{p.codigo} {p.nombre}' for p in sin_peso), sin_peso))
    if 'CANTIDAD' in metodos:
        unidades = {f['producto'].unidad for f in filas.values()}
        if len(unidades) > 1:
            salida.append(('CANTIDAD', f'El reparto por cantidad suma unidades distintas ({", ".join(sorted(unidades))}): '
                                       f'conviene repartir por valor FOB o por peso.', []))
    if imp.metodo == 'MANUAL' or 'MANUAL' in metodos:
        total = sum((Decimal(str(v)) for v in (imp.reparto_manual or {}).values()), D0)
        if total != Decimal('100'):
            salida.append(('MANUAL', f'El reparto manual suma {total:,.2f} % (debe sumar 100 %).', []))
    return salida


def _repartir(monto, metodo, filas, manual):
    """{producto_id: parte} del monto con el método; None si no hay base para repartir."""
    ids = sorted(filas)
    if metodo == 'MANUAL':
        pesos = {pid: Decimal(str(manual.get(str(pid), 0) or 0)) for pid in ids}
    else:
        pesos = {pid: filas[pid][CLAVE[metodo]] for pid in ids}
    total = sum(pesos.values(), D0)
    if not total:
        return None
    partes, restante = {}, monto
    con_base = [pid for pid in ids if pesos[pid]]
    for pid in ids:
        if not pesos[pid]:
            partes[pid] = D0
            continue
        parte = restante if pid == con_base[-1] else r2(monto * pesos[pid] / total)
        restante -= parte
        partes[pid] = parte
    return partes


def prorrateo(imp):
    """[{'producto', 'compra', 'cantidad', 'fob', 'peso', 'gasto', 'estimado', 'unitario_gasto'}]: lo que cada producto
    recibe de cada concepto (real o estimado) según su método."""
    filas = _lineas(imp)
    if not filas:
        raise ErrorImportacion('La factura comercial (o la OC) no tiene productos.')
    gasto, estimado = defaultdict(lambda: D0), defaultdict(lambda: D0)
    sin_base = []
    for concepto, monto, metodo, es_estimado in conceptos(imp):
        partes = _repartir(Decimal(monto), metodo, filas, imp.reparto_manual or {})
        if partes is None:
            sin_base.append(f'{dict(GastoImportacion.CONCEPTOS).get(concepto, concepto)} '
                            f'({dict(Importacion.METODOS).get(metodo, metodo).lower()})')
            continue
        for pid, parte in partes.items():
            gasto[pid] += parte
            if es_estimado:
                estimado[pid] += parte
    if sin_base:
        raise ErrorImportacion('No se puede repartir ' + '; '.join(sin_base) + ': ningún producto tiene esa base '
                               '(peso o % manual). Corrija los productos o cambie el método de ese gasto.')
    salida = []
    for pid in sorted(filas):
        f = filas[pid]
        salida.append({**f, 'gasto': gasto[pid], 'estimado': estimado[pid],
                       'unitario_gasto': (gasto[pid] / f['cantidad']).quantize(Decimal('0.0001'))
                       if f['cantidad'] else D0})
    return salida


@transaction.atomic
def liquidar(imp):
    """Aplica al costo lo que falte de los gastos (reales o estimados); se vuelve a liquidar cuando llegan las
    facturas reales o se agregan gastos, y solo se aplica la diferencia."""
    from inventario.cierre import error_cierre
    if not imp.gastos.exists() and not imp.estimados.exists():
        raise ErrorImportacion('Registre los gastos de la importación (o sus estimados).')
    facturas = imp.facturas_comerciales
    if not facturas:
        raise ErrorImportacion('Registre la factura comercial de la importación antes de liquidar.')
    if imp.en_transito:
        raise ErrorImportacion('La mercadería sigue en tránsito: registre su ingreso al almacén (levante) antes de '
                               'liquidar.')
    if error_cierre(timezone.localdate()):
        raise ErrorImportacion(error_cierre(timezone.localdate()))
    hechos = defaultdict(lambda: D0)
    for a in imp.ajustes.all():
        hechos[a.producto_id] += a.diferencia
    aplicados = []
    for f in prorrateo(imp):
        pendiente = f['gasto'] - hechos[f['producto'].pk]
        if abs(pendiente) >= Decimal('0.01'):
            aplicados.append(_aplicar(f['compra'] or facturas[0], f['producto'], f['cantidad'], pendiente,
                                      importacion=imp))
    imp.estado, imp.fecha_liquidacion = 'LIQUIDADA', timezone.localdate()
    imp.save(update_fields=['estado', 'fecha_liquidacion'])
    return aplicados
