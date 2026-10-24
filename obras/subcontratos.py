"""Subcontratos de la obra (OBR-20): contrato por partidas del presupuesto (precios unitarios o suma alzada) con su
orden de servicio, valorización mensual del subcontratista por metrado, amortización del adelanto, fondo de garantía
retenido y su devolución, detracción de los contratos de construcción, control frente a la meta y las conformidades de
servicio (HES) generadas desde cada valorización.

- El adelanto al subcontratista se paga en Tesorería como anticipo a proveedores (422) con el centro de costo de la
  obra y se aplica a sus facturas; la valorización amortiza la parte proporcional.
- El fondo de garantía se retiene de cada factura del subcontratista (aplicación: Debe 4212 / Haber 467 Depósitos
  recibidos en garantía) y se le devuelve al final (Tesorería, egreso contra la 467)."""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import LineaSubcontrato, LineaValSubcontrato, Subcontrato, ValorizacionSubcontrato

D0 = Decimal('0')


class ErrorSubcontrato(Exception):
    pass


def r2(v):
    return Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _usuario(u):
    return u if u is not None and getattr(u, 'is_authenticated', False) else None


@transaction.atomic
def crear(obra, proveedor, descripcion, lineas, usuario, modalidad='PRECIOS_UNITARIOS', adelanto_pct=D0,
          garantia_pct=Decimal('5'), detraccion=True, fecha=None):
    """lineas: [(partida o None, descripción, unidad, metrado, precio)]."""
    lineas = [l for l in lineas if l[3] and l[3] > 0]
    if proveedor is None:
        raise ErrorSubcontrato('Elija el subcontratista.')
    if not lineas:
        raise ErrorSubcontrato('Agregue al menos una partida con su metrado y precio.')
    if not (descripcion or '').strip():
        raise ErrorSubcontrato('Indique el objeto del subcontrato.')
    for partida, *_ in lineas:
        if partida is not None and partida.presupuesto.obra_id != obra.pk:
            raise ErrorSubcontrato(f'La partida {partida} no es de la obra.')
    sc = Subcontrato.objects.create(obra=obra, proveedor=proveedor, descripcion=descripcion.strip()[:250],
                                    modalidad=modalidad, fecha=fecha or timezone.localdate(),
                                    adelanto_pct=adelanto_pct or D0, garantia_pct=garantia_pct or D0,
                                    detraccion=detraccion, creado_por=_usuario(usuario))
    for partida, desc, unidad, metrado, precio in lineas:
        LineaSubcontrato.objects.create(subcontrato=sc, partida=partida,
                                        descripcion=(desc or (partida.descripcion if partida else ''))[:250],
                                        unidad=(unidad or (partida.unidad if partida else ''))[:12],
                                        metrado=metrado, precio=precio or D0)
    return sc


def producto_subcontrato():
    from contabilidad.models import CuentaDefecto
    from core.models import Producto
    p = Producto.objects.filter(codigo='SUBOBRA').first()
    if p is None:
        p = Producto(codigo='SUBOBRA', nombre='Subcontrato de obra', clase='SERVICIO', unidad='ZZ',
                     puede_venderse=False, detraccion_codigo='030', detraccion_pct=Decimal('4'))
        p.cuenta_compra = CuentaDefecto.mapa().get('compra_SERVICIO')
        p.save()
    return p


@transaction.atomic
def emitir_orden(sc, usuario):
    """Orden de servicio (pasa por la aprobación de órdenes de compra) con una línea por partida subcontratada."""
    from compras.models import OrdenCompra, OrdenCompraItem
    from compras.solped import _numero
    if sc.estado != 'BORRADOR':
        raise ErrorSubcontrato('El subcontrato ya tiene orden de servicio.')
    if sc.obra.centro_costo_id is None:
        raise ErrorSubcontrato('La obra no tiene centro de costo.')
    producto = producto_subcontrato()
    oc = OrdenCompra.objects.create(
        numero=_numero('OC', 'OC01'), tercero=sc.proveedor, fecha=timezone.localdate(), centro_costo=sc.obra.centro_costo,
        comprador=_usuario(usuario), condicion_pago='Por valorización mensual',
        glosa=f'{sc}: {sc.descripcion} – obra {sc.obra.codigo} {sc.obra.nombre}'[:500])
    for l in sc.lineas.all():
        l.oc_item = OrdenCompraItem.objects.create(
            documento=oc, producto=producto, descripcion=f'{l.descripcion} ({l.unidad})'[:250], cantidad=l.metrado,
            precio_unitario=l.precio)
        l.save(update_fields=['oc_item'])
    oc.calcular_totales()
    oc.save()
    sc.orden_compra, sc.estado = oc, 'VIGENTE'
    sc.save(update_fields=['orden_compra', 'estado'])
    return oc


def adelanto_entregado(sc):
    """Anticipos pagados al subcontratista con el centro de costo de la obra (Tesorería, sin IGV en el monto)."""
    from finanzas.models import Movimiento
    return Movimiento.objects.filter(tipo='EGRESO', concepto='ANTICIPO', tercero=sc.proveedor,
                                     centro_costo=sc.obra.centro_costo, estado='VIGENTE').aggregate(
        s=Sum('monto'))['s'] or D0


def amortizado(sc, excluir=None):
    qs = sc.valorizaciones.exclude(estado='ANULADA')
    if excluir is not None and excluir.pk:
        qs = qs.exclude(pk=excluir.pk)
    return qs.aggregate(s=Sum('amortizacion'))['s'] or D0


def valorizado_por_linea(sc, excluir=None):
    qs = LineaValSubcontrato.objects.filter(valorizacion__subcontrato=sc).exclude(valorizacion__estado='ANULADA')
    if excluir is not None and excluir.pk:
        qs = qs.exclude(valorizacion=excluir)
    return defaultdict(lambda: D0, {k: v for k, v in qs.values('linea').annotate(s=Sum('metrado')).values_list(
        'linea', 's')})


@transaction.atomic
def valorizar(sc, periodo, metrados, usuario):
    """Valorización del subcontratista del mes: metrados {línea_id: metrado}; adelanto y fondo de garantía en
    proporción. No supera el metrado de cada línea."""
    from core.models import Empresa
    if sc.estado != 'VIGENTE':
        raise ErrorSubcontrato('El subcontrato debe estar vigente (con su orden de servicio).')
    if not (len(periodo or '') == 6 and periodo.isdigit()):
        raise ErrorSubcontrato('Indique el mes que se valoriza.')
    if sc.valorizaciones.filter(estado='BORRADOR').exists():
        raise ErrorSubcontrato('Hay una valorización en borrador: apruébela o anúlela.')
    previo = valorizado_por_linea(sc)
    filas, excesos = [], []
    for l in sc.lineas.all():
        m = Decimal(metrados.get(l.pk) or 0)
        if not m:
            continue
        if m < 0 or previo[l.pk] + m > l.metrado:
            excesos.append(f'{l.descripcion[:40]} (acumulado {previo[l.pk] + m:,.2f} de {l.metrado:,.2f})')
        filas.append((l, m))
    if excesos:
        raise ErrorSubcontrato('Supera el metrado del subcontrato: ' + '; '.join(excesos[:5]))
    if not filas:
        raise ErrorSubcontrato('Indique el metrado del mes en al menos una partida.')
    v = ValorizacionSubcontrato.objects.create(subcontrato=sc, periodo=periodo)
    for l, m in filas:
        LineaValSubcontrato.objects.create(valorizacion=v, linea=l, metrado=m, monto=r2(m * l.precio))
    v.bruto = sum((r2(m * l.precio) for l, m in filas), D0)
    saldo = max(adelanto_entregado(sc) - amortizado(sc, excluir=v), D0)
    v.amortizacion = min(r2(v.bruto * sc.adelanto_pct / 100), saldo)
    igv = Empresa.actual().igv_tasa / 100
    v.garantia = r2((v.bruto - v.amortizacion) * (1 + igv) * sc.garantia_pct / 100)
    v.save()
    return v


@transaction.atomic
def aprobar(v, usuario):
    """Aprueba la valorización y genera una conformidad de servicio (HES, en borrador) por línea para que el área
    adjunte el informe y la envíe a liberación; la factura del subcontratista se registra contra ellas."""
    from compras import hes as srv_hes
    from compras.models import ConformidadServicio
    if v.estado != 'BORRADOR':
        raise ErrorSubcontrato('La valorización ya fue aprobada o anulada.')
    sc = v.subcontrato
    if sc.orden_compra is None or sc.orden_compra.estado not in ('APROBADO', 'ATENDIDO'):
        raise ErrorSubcontrato('La orden de servicio del subcontrato aún no está aprobada en Compras.')
    from .valorizaciones import rango
    desde, hasta = rango(v.periodo)
    for lv in v.lineas.select_related('linea__oc_item'):
        if lv.linea.oc_item_id is None:
            continue
        h = ConformidadServicio(orden_compra=sc.orden_compra, oc_item=lv.linea.oc_item, cantidad=lv.metrado,
                                descripcion=f'Valorización {v.numero} ({v.periodo[4:]}/{v.periodo[:4]}): '
                                            f'{lv.linea.descripcion}'[:250],
                                periodo_desde=desde, periodo_hasta=hasta, lugar=sc.obra.direccion[:150])
        try:
            lv.hes = srv_hes.registrar(h, usuario)
        except srv_hes.ErrorHES as exc:
            raise ErrorSubcontrato(str(exc))
        lv.save(update_fields=['hes'])
    v.estado, v.aprobado_por, v.aprobado_en = 'APROBADA', _usuario(usuario), timezone.now()
    v.save(update_fields=['estado', 'aprobado_por', 'aprobado_en'])
    return v


@transaction.atomic
def anular(v):
    if v.estado != 'BORRADOR':
        raise ErrorSubcontrato('Solo se anula una valorización en borrador (las aprobadas tienen HES).')
    v.estado = 'ANULADA'
    v.save(update_fields=['estado'])
    return v


@transaction.atomic
def retener_garantia(v, compra, usuario):
    """Aplica el fondo de garantía de la valorización a la factura del subcontratista (Debe 4212 / Haber 467)."""
    from finanzas.models import Aplicacion
    if v.estado != 'APROBADA' or not v.garantia:
        raise ErrorSubcontrato('La valorización no tiene fondo de garantía por retener.')
    if compra.tercero_id != v.subcontrato.proveedor_id or compra.estado != 'REGISTRADO':
        raise ErrorSubcontrato('Elija una factura vigente del subcontratista.')
    if Aplicacion.objects.filter(origen='GARANTIA', compra=compra).exists():
        raise ErrorSubcontrato(f'{compra} ya tiene el fondo de garantía retenido.')
    return Aplicacion.objects.create(origen='GARANTIA', fecha=compra.fecha_emision, compra=compra,
                                     monto_doc=min(v.garantia, compra.total), creado_por=_usuario(usuario),
                                     glosa=f'Fondo de garantía: {v}'[:250])


def garantia(sc):
    """(retenido en facturas, devuelto, saldo) del fondo de garantía del subcontratista en la obra."""
    from contabilidad.models import CuentaDefecto
    from finanzas.models import Aplicacion, Movimiento
    retenido = Aplicacion.objects.filter(origen='GARANTIA', compra__tercero=sc.proveedor,
                                         compra__centro_costo=sc.obra.centro_costo).aggregate(
        s=Sum('monto_doc'))['s'] or D0
    cuenta = CuentaDefecto.mapa().get('subcontrato_garantia')
    devuelto = Movimiento.objects.filter(tipo='EGRESO', tercero=sc.proveedor, cuenta_contable=cuenta,
                                         centro_costo=sc.obra.centro_costo, estado='VIGENTE').aggregate(
        s=Sum('monto'))['s'] or D0
    return retenido, devuelto, retenido - devuelto


@transaction.atomic
def devolver_garantia(sc, monto, fecha, cuenta, usuario, numero_operacion=''):
    from contabilidad.models import CuentaDefecto
    from finanzas.models import Movimiento
    _, _, saldo = garantia(sc)
    monto = r2(monto or 0)
    if monto <= 0 or monto > saldo:
        raise ErrorSubcontrato(f'La devolución debe ser mayor a cero y hasta el saldo del fondo (S/ {saldo:,.2f}).')
    if cuenta is None:
        raise ErrorSubcontrato('Elija la cuenta de la que sale el pago.')
    return Movimiento.objects.create(
        cuenta=cuenta, fecha=fecha or timezone.localdate(), tipo='EGRESO', concepto='OTRO',
        medio_pago='TRANSFERENCIA', numero_operacion=(numero_operacion or '')[:40], tercero=sc.proveedor,
        monto=monto, cuenta_contable=CuentaDefecto.mapa()['subcontrato_garantia'],
        centro_costo=sc.obra.centro_costo, creado_por=_usuario(usuario),
        glosa=f'Devolución del fondo de garantía {sc}'[:250])


def control(sc):
    """Por línea: contratado, valorizado acumulado, saldo y la meta de la partida (parcial de la meta)."""
    from .valorizaciones import presupuesto_meta
    meta = presupuesto_meta(sc.obra)
    meta_por_clave = {x.clave: x for x in meta.partidas.select_related('subpresupuesto')} if meta else {}
    previo = valorizado_por_linea(sc)
    filas = []
    for l in sc.lineas.select_related('partida__subpresupuesto'):
        m = meta_por_clave.get(l.partida.clave) if l.partida_id else None
        filas.append({'linea': l, 'contratado': l.monto, 'valorizado_metrado': previo[l.pk],
                      'valorizado': r2(previo[l.pk] * l.precio), 'saldo_metrado': l.metrado - previo[l.pk],
                      'meta': m.parcial if m else None,
                      'desvio': (l.monto - m.parcial) if m else None})
    return filas
