"""Costeo de importaciones: los gastos vinculados (flete, seguro, ad valorem, agente, almacenaje…) se prorratean a
los productos de la factura comercial y se suman a su costo (como las diferencias de precio: lo que sigue en stock
revaloriza el costo promedio; lo ya vendido va al costo de ventas).

Contabilidad: cada gasto se registra en la 6091 (costos vinculados), cuyo destino lo deja en la 2811 (por recibir);
la liquidación lo pasa de la 2811 al inventario (o al costo de ventas)."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import Serie, r2

from .models import GastoImportacion, Importacion
from .precios import _aplicar

D0 = Decimal('0')


class ErrorImportacion(Exception):
    pass


def crear(imp):
    s, n = Serie.siguiente('IMP', 'IM')
    imp.numero = f'{s}-{n}'
    imp.save()
    return imp


def _cuenta_vinculados():
    from contabilidad.models import CuentaContable
    return CuentaContable.objects.filter(codigo='6091').first()


@transaction.atomic
def agregar_gasto(imp, concepto, monto, descripcion='', compra=None, movimiento=None):
    if compra is None and movimiento is None:
        raise ErrorImportacion('Indique la factura del gasto o el pago de caja / bancos.')
    if compra is not None and compra.pk == imp.compra_id:
        raise ErrorImportacion('La factura comercial no es un gasto: elija la factura del flete, agente, etc.')
    if compra is not None and GastoImportacion.objects.filter(compra=compra).exists():
        raise ErrorImportacion(f'{compra} ya está en una importación.')
    if monto is None:
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
                                           monto=monto, descripcion=descripcion[:150])


def prorrateo(imp):
    """[{'producto', 'cantidad' (unidad de almacén), 'fob', 'peso', 'base', 'gasto', 'unitario_gasto'}]."""
    c = imp.compra
    filas = defaultdict(lambda: {'cantidad': D0, 'fob': D0, 'peso': D0})
    productos = {}
    for i in c.items.filter(producto__isnull=False).select_related('producto'):
        f = filas[i.producto_id]
        productos[i.producto_id] = i.producto
        cant = i.producto.a_stock(i.cantidad)
        f['cantidad'] += cant
        f['fob'] += r2(i.subtotal * c.tc_efectivo)
        f['peso'] += cant * (i.producto.peso or D0)
    clave = {'VALOR': 'fob', 'CANTIDAD': 'cantidad', 'PESO': 'peso'}[imp.metodo]
    total_base = sum((f[clave] for f in filas.values()), D0)
    if not total_base:
        raise ErrorImportacion('No se puede prorratear: la factura no tiene productos'
                               + (' con peso registrado.' if imp.metodo == 'PESO' else '.'))
    gastos = Decimal(imp.total_gastos)
    salida, restante = [], gastos
    for n, (pid, f) in enumerate(sorted(filas.items())):
        parte = restante if n == len(filas) - 1 else r2(gastos * f[clave] / total_base)
        restante -= parte
        salida.append({'producto': productos[pid], 'cantidad': f['cantidad'], 'fob': f['fob'], 'peso': f['peso'],
                       'base': f[clave], 'gasto': parte,
                       'unitario_gasto': (parte / f['cantidad']).quantize(Decimal('0.0001')) if f['cantidad'] else D0})
    return salida


@transaction.atomic
def liquidar(imp):
    """Aplica al costo lo que falte de los gastos (se puede volver a liquidar si se agregan gastos después)."""
    from inventario.cierre import error_cierre
    if not imp.gastos.exists():
        raise ErrorImportacion('Registre los gastos de la importación.')
    if not (imp.compra.stock_aplicado or imp.compra.operaciones_inventario.filter(estado='CONFIRMADO').exists()
            or imp.compra.orden_compra_id):
        raise ErrorImportacion('La mercadería de la factura comercial aún no ingresa al almacén.')
    if error_cierre(timezone.localdate()):
        raise ErrorImportacion(error_cierre(timezone.localdate()))
    hechos = defaultdict(lambda: D0)
    for a in imp.ajustes.all():
        hechos[a.producto_id] += a.diferencia
    aplicados = []
    for f in prorrateo(imp):
        pendiente = f['gasto'] - hechos[f['producto'].pk]
        if abs(pendiente) >= Decimal('0.01'):
            aplicados.append(_aplicar(imp.compra, f['producto'], f['cantidad'], pendiente, importacion=imp))
    imp.estado, imp.fecha_liquidacion = 'LIQUIDADA', timezone.localdate()
    imp.save(update_fields=['estado', 'fecha_liquidacion'])
    return aplicados
