"""Cierre de costos según la NIC 2: liquidación del costo real del periodo y prueba del valor neto realizable.

Liquidación (como el cierre de costo real de SAP, con la capacidad normal de la NIC 2 párr. 13):
1. Por centro de costo de planta, el gasto real del periodo (62-68, contabilidad) se separa en mano de obra, CIF
   variable y CIF fijo (ComportamientoGasto, por prefijo de cuenta).
2. Va al producto: mano de obra + CIF variable + CIF fijo × (horas reales ÷ capacidad normal, tope 100 %). Lo demás
   (capacidad ociosa, o todo si no hubo producción) queda como gasto del periodo.
3. La diferencia contra lo ya cargado con las tarifas se reparte a las órdenes terminadas por sus horas reales.
4. Por producto, de abajo hacia arriba en la estructura: la parte de lo que sigue en stock revaloriza el costo
   promedio (21/23), la de lo consumido por otras órdenes del periodo pasa a ellas, y lo demás (vendido) va al
   costo de ventas (69). La contrapartida es la variación de la producción almacenada (71).
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import Kardex, Producto, r2

from .models import (ComportamientoGasto, ConsumoOrden, HoraOrden, LiquidacionCentro, LiquidacionCosto,
                     LiquidacionOrden, LiquidacionProducto, OrdenProduccion, PruebaVNR, PruebaVNRLinea)

D0 = Decimal('0')
# sin configuración: planillas = mano de obra; energía, agua y suministros = variables; lo demás de 63-68 = fijo
COMPORTAMIENTO_DEFECTO = [('62', 'MO'), ('6361', 'VARIABLE'), ('6363', 'VARIABLE'), ('656', 'VARIABLE'),
                          ('6', 'FIJO')]


class ErrorLiquidacion(Exception):
    pass


def reglas():
    filas = list(ComportamientoGasto.objects.values_list('prefijo', 'tipo')) or COMPORTAMIENTO_DEFECTO[:-1]
    return sorted(filas, key=lambda f: -len(f[0]))


def comportamiento(codigo, lista=None):
    for prefijo, tipo in (lista if lista is not None else reglas()):
        if codigo.startswith(prefijo):
            return tipo
    return 'FIJO'


def _validar_periodo(periodo):
    from contabilidad.centralizar import periodo_migrado
    from contabilidad.models import PeriodoContable
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorLiquidacion(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado en contabilidad.')
    if periodo_migrado(periodo):
        raise ErrorLiquidacion('El periodo es anterior a la fecha de corte: sus costos son los del sistema anterior.')


def _gasto_real(centro, periodo, lista):
    """{MO, VARIABLE, FIJO} del centro de costo (y los que dependen de él) en el periodo."""
    from contabilidad.models import AsientoLinea
    gasto = {'MO': D0, 'VARIABLE': D0, 'FIJO': D0}
    for f in (AsientoLinea.objects.filter(asiento__periodo=periodo, es_destino=False,
                                          centro_costo_id__in=centro.descendientes_ids(),
                                          cuenta__codigo__regex=r'^6[2-8]')
              .values('cuenta__codigo').annotate(d=Sum('debe'), h=Sum('haber'))):
        gasto[comportamiento(f['cuenta__codigo'], lista)] += (f['d'] or D0) - (f['h'] or D0)
    return gasto


def _fecha_ajuste(producto, hasta):
    """El ajuste queda como último movimiento del producto (la valorización toma el costo del último registro)."""
    ultimo = Kardex.objects.filter(producto=producto).order_by('-fecha').values_list('fecha', flat=True).first()
    return min(max(hasta, ultimo or hasta), timezone.localdate())


def _revalorizar(producto, importe, fecha, referencia):
    """Ajuste de valor sin cantidad: cambia el costo promedio de lo que hay en stock y lo deja en el kardex."""
    actual = Producto.objects.select_for_update().get(pk=producto.pk)
    if not importe or actual.stock <= 0:
        return None
    actual.costo_promedio = max(((actual.stock * actual.costo_promedio + importe) / actual.stock)
                                .quantize(Decimal('0.0001')), D0)
    actual.save(update_fields=['costo_promedio'])
    return Kardex.objects.create(producto=actual, almacen=None, fecha=fecha, tipo='ENTRADA', cantidad=D0,
                                 costo_unitario=D0, costo_promedio=actual.costo_promedio, saldo=actual.stock,
                                 origen='AJUSTE', concepto='LIQ_COSTO', referencia=referencia[:100],
                                 codigo_sunat='99')


def _recentralizar(periodos):
    from contabilidad.centralizar import centralizar_periodo
    from contabilidad.models import PeriodoContable
    for p in sorted(periodos):
        if not PeriodoContable.esta_cerrado(p):
            centralizar_periodo(p)


def anular(periodo, recentralizar=True):
    """Deshace la liquidación del periodo: quita los ajustes del kardex y devuelve el costo promedio."""
    from inventario.cierre import error_cierre
    liq = LiquidacionCosto.objects.filter(periodo=periodo).first()
    if not liq:
        return set()
    _validar_periodo(periodo)
    periodos = {periodo}
    with transaction.atomic():
        for lp in liq.productos.select_related('kardex', 'producto'):
            if error_cierre(lp.fecha):
                raise ErrorLiquidacion(f'{lp.producto.nombre}: el kardex del {lp.fecha:%d/%m/%Y} está cerrado.')
            periodos.add(lp.fecha.strftime('%Y%m'))
            if lp.kardex_id:
                lp.kardex.delete()
                actual = Producto.objects.select_for_update().get(pk=lp.producto_id)
                if actual.stock > 0:
                    actual.costo_promedio = max(((actual.stock * actual.costo_promedio - lp.a_inventario) /
                                                 actual.stock).quantize(Decimal('0.0001')), D0)
                    actual.save(update_fields=['costo_promedio'])
        OrdenProduccion.objects.filter(liquidaciones__liquidacion=liq).update(ajuste_liquidacion=D0)
        liq.delete()
        if recentralizar:
            _recentralizar(periodos)
    return periodos


def liquidar(periodo, usuario=None):
    """Liquida el costo real del periodo (rehace la liquidación anterior). Devuelve la LiquidacionCosto."""
    from contabilidad.centralizar import _rango, _repartir, centralizar_periodo
    from contabilidad.models import CentroCosto
    from inventario.cierre import error_cierre

    from .models import CentroTrabajo
    from .servicios import _niveles
    _validar_periodo(periodo)
    desde, hasta = _rango(periodo)
    if hasta >= timezone.localdate():
        raise ErrorLiquidacion('La liquidación es del cierre: hágala cuando termine el mes (con planillas, '
                               'provisiones y depreciación registradas).')
    with transaction.atomic():
        periodos = anular(periodo, recentralizar=False)
        centralizar_periodo(periodo)  # gasto real del periodo al día (planillas, provisiones, depreciación...)
        liq = LiquidacionCosto.objects.create(periodo=periodo,
                                              usuario=usuario if usuario and usuario.is_authenticated else None)
        ordenes = {o.pk: o for o in OrdenProduccion.objects.filter(
            estado='TERMINADA', es_historica=False, fecha_fin__range=[desde, hasta]).select_related('producto')}
        horas_por_centro = defaultdict(list)
        for h in HoraOrden.objects.filter(orden_id__in=ordenes, centro__centro_costo__isnull=False) \
                .select_related('centro'):
            horas_por_centro[h.centro.centro_costo_id].append(h)
        lista = reglas()
        centros = CentroCosto.objects.filter(pk__in=list(horas_por_centro)) | CentroCosto.objects.filter(
            tipo='PRODUCCION', pk__in=CentroTrabajo.objects.values('centro_costo'))
        mo_orden, cif_orden = defaultdict(lambda: D0), defaultdict(lambda: D0)
        for cc in centros.distinct().order_by('codigo'):
            gasto = _gasto_real(cc, periodo, lista)
            filas = horas_por_centro.get(cc.pk, [])
            horas = sum((h.horas_real for h in filas), D0)
            puestos = list(CentroTrabajo.objects.filter(centro_costo=cc, activo=True)
                           .values_list('horas_normales_mes', flat=True))
            normales = sum(puestos, D0) if puestos and all(puestos) else None
            factor = min(Decimal('1'), horas / normales) if normales else (Decimal('1') if horas else D0)
            lc = LiquidacionCentro.objects.create(
                liquidacion=liq, centro_costo=cc, horas=horas, horas_normales=normales, mo_real=r2(gasto['MO']),
                variable_real=r2(gasto['VARIABLE']), fijo_real=r2(gasto['FIJO']),
                fijo_inventariable=r2(gasto['FIJO'] * factor),
                absorbido_mo=sum((h.costo_mo for h in filas), D0), absorbido_cif=sum((h.costo_cif for h in filas), D0))
            if not horas:
                continue
            pesos = defaultdict(lambda: D0)
            for h in filas:
                pesos[h.orden_id] += h.horas_real
            pesos = {k: v for k, v in pesos.items() if v}
            for oid, parte in _repartir(lc.mo_real - lc.absorbido_mo, pesos):
                mo_orden[oid] += parte
            for oid, parte in _repartir(lc.variable_real + lc.fijo_inventariable - lc.absorbido_cif, pesos):
                cif_orden[oid] += parte
        # por producto, de los semielaborados (nivel más profundo) a los productos finales
        recibido = defaultdict(lambda: D0)
        por_producto = defaultdict(list)
        for o in ordenes.values():
            por_producto[o.producto_id].append(o)
        niveles = _niveles()
        for pid in sorted(por_producto, key=lambda p: -niveles[p]):
            propias = por_producto[pid]
            diferencia = sum((mo_orden[o.pk] + cif_orden[o.pk] + recibido[o.pk] for o in propias), D0)
            if not diferencia:
                continue
            producto = Producto.objects.select_for_update().get(pk=pid)
            producido = sum((o.cantidad_producida for o in propias), D0)
            en_stock = min(max(producto.stock, D0), producido)
            consumos = defaultdict(lambda: D0)
            for c in ConsumoOrden.objects.filter(orden_id__in=ordenes, producto_id=pid, cantidad_real__gt=0) \
                    .exclude(orden__producto_id=pid):
                consumos[c.orden_id] += c.cantidad_real
            consumido = min(producido - en_stock, sum(consumos.values(), D0))
            a_inv = r2(diferencia * en_stock / producido) if producido else D0
            a_prod = r2(diferencia * consumido / producido) if producido else D0
            a_costo = diferencia - a_inv - a_prod
            if a_prod:
                for oid, parte in _repartir(a_prod, dict(consumos)):
                    recibido[oid] += parte
            fecha = _fecha_ajuste(producto, hasta)
            if error_cierre(fecha):
                raise ErrorLiquidacion(f'{producto.nombre}: el kardex del {fecha:%d/%m/%Y} está cerrado. Liquide '
                                       'el costo antes de cerrar el kardex.')
            k = _revalorizar(producto, a_inv, fecha, f'Liquidación de costo real {periodo[4:]}/{periodo[:4]}')
            if a_inv and not k:  # sin stock al momento: todo al costo de ventas
                a_costo, a_inv = a_costo + a_inv, D0
            LiquidacionProducto.objects.create(
                liquidacion=liq, producto=producto, fecha=fecha, producido=producido, en_stock=en_stock,
                consumido=consumido, diferencia=diferencia, a_inventario=a_inv, a_produccion=a_prod, a_costo=a_costo,
                kardex=k)
            periodos.add(fecha.strftime('%Y%m'))
        for oid, o in ordenes.items():
            mo, cif, sub = mo_orden[oid], cif_orden[oid], recibido[oid]
            if mo or cif or sub:
                LiquidacionOrden.objects.create(liquidacion=liq, orden=o, mano_obra=mo, cif=cif, de_insumos=sub)
            OrdenProduccion.objects.filter(pk=oid).update(ajuste_liquidacion=mo + cif + sub)
        _recentralizar(periodos | {periodo})
    from core.auditoria import registrar
    registrar('CREAR', liq, {'Al producto': str(sum((c.diferencia for c in liq.centros.all()), D0)),
                             'Gasto del periodo': str(sum((c.gasto_periodo for c in liq.centros.all()), D0))})
    return liq


def resumen(liq):
    centros = list(liq.centros.select_related('centro_costo'))
    productos = list(liq.productos.select_related('producto'))
    return {
        'centros': centros, 'productos': productos,
        'ordenes': list(liq.ordenes.select_related('orden__producto')),
        'tot': {
            'real': sum((c.real for c in centros), D0), 'inventariable': sum((c.inventariable for c in centros), D0),
            'absorbido': sum((c.absorbido for c in centros), D0), 'diferencia': sum((c.diferencia for c in centros), D0),
            'gasto': sum((c.gasto_periodo for c in centros), D0),
            'a_inventario': sum((p.a_inventario for p in productos), D0),
            'a_costo': sum((p.a_costo for p in productos), D0),
        },
    }


# ---------------------------------------------------------------- valor neto realizable
def _precios_venta(desde, hasta):
    """{producto_id: (precio neto unitario en soles, n° de ventas)} de facturas y boletas del rango."""
    from ventas.models import VentaItem
    acum = defaultdict(lambda: [D0, D0, 0])
    for i in (VentaItem.objects.filter(documento__estado='REGISTRADO', documento__tipo_comprobante__in=['01', '03'],
                                       documento__fecha_emision__range=[desde, hasta], producto__isnull=False,
                                       cantidad__gt=0)
              .select_related('documento')):
        fila = acum[i.producto_id]
        fila[0] += i.subtotal * i.documento.tc_efectivo
        fila[1] += i.cantidad
        fila[2] += 1
    return {pid: ((v / q).quantize(Decimal('0.0001')), n) for pid, (v, q, n) in acum.items() if q}


def _deterioro_anterior(fecha, norma, excluir=None):
    """{producto_id: desvalorización acumulada} según la última prueba registrada antes de la fecha."""
    qs = PruebaVNR.objects.filter(fecha__lt=fecha, norma=norma)
    if excluir:
        qs = qs.exclude(pk=excluir)
    previa = qs.order_by('-fecha', '-id').first()
    if not previa:
        return {}
    return dict(previa.lineas.values_list('producto_id', 'deterioro'))


def calcular_vnr(fecha, gasto_venta=D0, dias=90, norma='NIIF', usar_lista=False):
    """Filas de la prueba al corte: costo del kardex vs precio de venta estimado − gastos de venta.
    Materias primas y suministros no se desvalorizan si el producto que se fabrica con ellos se vende sobre su costo
    (NIC 2 párr. 32): se prueban solo mercaderías, productos terminados y lo que tenga ventas."""
    from core.inventario import valor_inventario
    precios = _precios_venta(fecha - timedelta(days=dias), fecha)
    anterior = _deterioro_anterior(fecha, norma)
    filas = []
    vistos = set()
    for f in valor_inventario(fecha)[0]:
        p = f['p']
        if f['cantidad'] <= 0 or (p.clase not in ('MERCADERIA', 'PRODUCTO_TERMINADO') and p.pk not in precios):
            continue
        vistos.add(p.pk)
        if p.pk in precios:
            precio, n = precios[p.pk]
            fuente = f'{n} venta(s) en {dias} días'
        elif usar_lista and p.precio_venta:
            precio, fuente = p.precio_venta, 'Precio de lista (sin ventas)'
        else:  # sin ventas recientes el precio de lista puede no ser fiable: se revisa a mano
            precio, fuente = None, 'Sin ventas: revisar a mano'
        vnr = (precio * (1 - gasto_venta / 100)).quantize(Decimal('0.0001')) if precio else None
        deterioro = r2(f['cantidad'] * max(f['costo'] - vnr, D0)) if vnr is not None else D0
        filas.append({'p': p, 'cantidad': f['cantidad'], 'costo': f['costo'], 'valor': f['valor'], 'precio': precio,
                      'fuente': fuente, 'vnr': vnr, 'deterioro': deterioro,
                      'ajuste': deterioro - anterior.get(p.pk, D0)})
    for pid, previo in anterior.items():  # lo provisionado que ya salió del almacén se revierte
        if pid not in vistos and previo:
            p = Producto.objects.get(pk=pid)
            filas.append({'p': p, 'cantidad': D0, 'costo': D0, 'valor': D0, 'precio': None,
                          'fuente': 'Ya no está en stock', 'vnr': None, 'deterioro': D0, 'ajuste': -previo})
    filas.sort(key=lambda f: (-f['deterioro'], -abs(f['ajuste']), f['p'].nombre))
    return filas


def registrar_vnr(fecha, gasto_venta=D0, dias=90, norma='NIIF', usuario=None, usar_lista=False):
    periodo = fecha.strftime('%Y%m')
    _validar_periodo(periodo)
    if fecha > timezone.localdate():
        raise ErrorLiquidacion('La fecha de la prueba no puede ser futura.')
    if PruebaVNR.objects.filter(fecha__gte=fecha, norma=norma).exists():
        raise ErrorLiquidacion('Ya hay una prueba en esa fecha o posterior para ese libro: anúlela primero.')
    filas = calcular_vnr(fecha, gasto_venta, dias, norma, usar_lista)
    with transaction.atomic():
        prueba = PruebaVNR.objects.create(fecha=fecha, norma=norma, gasto_venta=gasto_venta, dias_precio=dias,
                                          usuario=usuario if usuario and usuario.is_authenticated else None)
        PruebaVNRLinea.objects.bulk_create([PruebaVNRLinea(
            prueba=prueba, producto=f['p'], cantidad=f['cantidad'], costo=f['costo'], precio=f['precio'] or D0,
            fuente=f['fuente'][:40], vnr=f['vnr'] or D0, deterioro=f['deterioro'], ajuste=f['ajuste'])
            for f in filas if f['deterioro'] or f['ajuste']])
        _recentralizar({periodo})
    from core.auditoria import registrar
    registrar('CREAR', prueba, {'Desvalorización acumulada': str(sum((f['deterioro'] for f in filas), D0)),
                                'Ajuste del periodo': str(sum((f['ajuste'] for f in filas), D0))})
    return prueba


def anular_vnr(prueba):
    from contabilidad.models import PeriodoContable
    periodo = prueba.fecha.strftime('%Y%m')
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorLiquidacion(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado en contabilidad.')
    if PruebaVNR.objects.filter(norma=prueba.norma, fecha__gt=prueba.fecha).exists():
        raise ErrorLiquidacion('Hay una prueba posterior: anule primero la más reciente.')
    with transaction.atomic():
        prueba.delete()
        _recentralizar({periodo})
