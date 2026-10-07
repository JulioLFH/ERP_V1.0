"""Abastecimiento: licitaciones (solicitud de cotización a varios proveedores, cuadro comparativo y adjudicación)
y contratos marco (precios pactados por un periodo; las órdenes de compra consumen su saldo)."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import Serie

from .models import ContratoMarco, LineaLicitacion, OfertaLicitacion, OrdenCompra, OrdenCompraItem, PrecioOferta

D0 = Decimal('0')


class ErrorAbastecimiento(Exception):
    pass


def _numero(tipo, serie):
    s, n = Serie.siguiente(tipo, serie)
    return f'{s}-{n}'


# ---------------------------------------------------------------- licitaciones
def crear_licitacion(lic, lineas, usuario):
    """lineas: [(producto o None, descripción, cantidad)]."""
    lineas = [li for li in lineas if li[2] and li[2] > 0 and (li[0] or li[1])]
    if not lineas:
        raise ErrorAbastecimiento('Agregue al menos un ítem con cantidad.')
    with transaction.atomic():
        lic.numero, lic.creado_por = _numero('LIC', 'LIC'), usuario
        lic.save()
        for producto, descripcion, cantidad in lineas:
            LineaLicitacion.objects.create(licitacion=lic, producto=producto,
                                           descripcion=(descripcion or producto.nombre)[:250], cantidad=cantidad)
    return lic


@transaction.atomic
def registrar_oferta(lic, proveedor, precios, datos):
    """precios: {linea_id: precio unitario sin IGV} (las líneas sin precio no se cotizaron)."""
    if lic.estado != 'ABIERTA':
        raise ErrorAbastecimiento('La licitación ya no recibe ofertas.')
    precios = {k: v for k, v in precios.items() if v is not None}
    if any(v <= 0 for v in precios.values()):
        raise ErrorAbastecimiento('Los precios deben ser mayores a cero.')
    if not precios:
        raise ErrorAbastecimiento('Indique el precio de al menos un ítem.')
    oferta, _ = OfertaLicitacion.objects.update_or_create(licitacion=lic, proveedor=proveedor, defaults=datos)
    oferta.precios.all().delete()
    lineas = {li.pk for li in lic.lineas.all()}
    for linea_id, precio in precios.items():
        if linea_id in lineas:
            PrecioOferta.objects.create(oferta=oferta, linea_id=linea_id, precio_unitario=precio)
    return oferta


def cuadro(lic):
    """Cuadro comparativo: por ítem, el precio de cada oferta en soles y la mejor; por oferta, su total."""
    ofertas = list(lic.ofertas.select_related('proveedor').prefetch_related('precios'))
    precios = {(p.oferta_id, p.linea_id): p for o in ofertas for p in o.precios.all()}
    filas = []
    for li in lic.lineas.select_related('producto', 'adjudicada'):
        celdas = [precios.get((o.pk, li.pk)) for o in ofertas]
        validos = [c.precio_pen for c in celdas if c]
        mejor = min(validos) if validos else None
        filas.append({'l': li, 'celdas': [{'p': c, 'pen': c.precio_pen if c else None,
                                          'total': (c.precio_pen * li.cantidad).quantize(Decimal('0.01')) if c else None,
                                          'mejor': c is not None and c.precio_pen == mejor} for c in celdas],
                      'mejor': mejor})
    totales = []
    for i, o in enumerate(ofertas):
        total = sum((f['celdas'][i]['total'] for f in filas if f['celdas'][i]['total'] is not None), D0)
        cotizados = sum(1 for f in filas if f['celdas'][i]['p'])
        ganados = sum(1 for f in filas if f['celdas'][i]['mejor'])
        totales.append({'o': o, 'total': total, 'cotizados': cotizados, 'ganados': ganados})
    return ofertas, filas, totales


@transaction.atomic
def adjudicar(lic, eleccion, usuario, justificacion=''):
    """eleccion: {linea_id: oferta_id}. Las líneas sin elección no se compran. Genera una orden de compra
    pendiente (pasa por la aprobación normal) por proveedor ganador. Si no se elige la oferta más barata de una
    línea hay que justificarlo."""
    if lic.estado != 'ABIERTA':
        raise ErrorAbastecimiento('La licitación ya fue adjudicada o anulada.')
    ofertas = {o.pk: o for o in lic.ofertas.select_related('proveedor')}
    _, filas, _ = cuadro(lic)
    por_oferta, caro = defaultdict(list), False
    for f in filas:
        oferta_id = eleccion.get(f['l'].pk)
        if not oferta_id:
            continue
        if oferta_id not in ofertas:
            raise ErrorAbastecimiento('Oferta no válida.')
        idx = list(ofertas).index(oferta_id)
        celda = f['celdas'][idx]
        if celda['p'] is None:
            raise ErrorAbastecimiento(f'{ofertas[oferta_id]} no cotizó "{f["l"].descripcion}".')
        caro = caro or not celda['mejor']
        por_oferta[oferta_id].append((f['l'], celda['p']))
    if not por_oferta:
        raise ErrorAbastecimiento('Elija el proveedor ganador de al menos un ítem.')
    if caro and len((justificacion or '').strip()) < 10:
        raise ErrorAbastecimiento('Hay ítems adjudicados a un precio que no es el más bajo: justifique la decisión '
                                  '(calidad, plazo, condiciones…).')
    ordenes = []
    for oferta_id, lineas in por_oferta.items():
        o = ofertas[oferta_id]
        oc = OrdenCompra.objects.create(
            numero=_numero('OC', 'OC01'), tercero=o.proveedor, fecha=timezone.localdate(), centro_costo=lic.centro_costo,
            moneda=o.moneda, tipo_cambio=o.tipo_cambio, condicion_pago=o.condicion_pago[:100],
            fecha_entrega=timezone.localdate() + timedelta(days=o.plazo_entrega) if o.plazo_entrega else None,
            glosa=f'Adjudicada en la {lic}: {lic.descripcion}'[:500])
        for linea, precio in lineas:
            OrdenCompraItem.objects.create(documento=oc, producto=linea.producto, descripcion=linea.descripcion,
                                           cantidad=linea.cantidad, precio_unitario=precio.precio_unitario)
            linea.adjudicada, linea.orden_compra = o, oc
            linea.save(update_fields=['adjudicada', 'orden_compra'])
        oc.calcular_totales()
        oc.save()
        ordenes.append(oc)
    lic.estado, lic.adjudicado_por, lic.adjudicado_en = 'ADJUDICADA', usuario, timezone.now()
    lic.justificacion = (justificacion or '')[:300]
    lic.save()
    return ordenes


# ---------------------------------------------------------------- contratos marco
def crear_contrato(contrato, usuario):
    contrato.numero, contrato.creado_por = _numero('CMA', 'CM'), usuario
    contrato.save()
    return contrato


def consumo(contrato):
    """Por producto pactado: cantidad e importe pedidos en órdenes no anuladas del contrato y su saldo."""
    pedido = {pid: (c or D0, s or D0) for pid, c, s in (
        OrdenCompraItem.objects.filter(documento__contrato=contrato).exclude(documento__estado='ANULADO')
        .values_list('producto').annotate(c=Sum('cantidad'), s=Sum('subtotal')))}
    filas = []
    for li in contrato.lineas.select_related('producto'):
        cant, importe = pedido.get(li.producto_id, (D0, D0))
        filas.append({'l': li, 'pedido': cant, 'importe': importe,
                      'saldo': li.cantidad_maxima - cant if li.cantidad_maxima is not None else None,
                      'pct': (cant / li.cantidad_maxima * 100).quantize(Decimal('0.1'))
                      if li.cantidad_maxima else None})
    total = (OrdenCompra.objects.filter(contrato=contrato).exclude(estado='ANULADO')
             .aggregate(s=Sum('base_imponible'), n=Sum('no_gravado')))
    usado = (total['s'] or D0) + (total['n'] or D0)
    return filas, usado


def validar_orden(oc):
    """Errores de una orden contra su contrato: proveedor, vigencia, precios y topes (cantidad y monto)."""
    c = oc.contrato
    if c is None:
        return []
    errores = []
    if oc.tercero_id != c.proveedor_id:
        errores.append(f'El contrato {c.numero} es con {c.proveedor.nombre}.')
    if not c.vigente_en(oc.fecha):
        errores.append(f'El contrato {c.numero} no está vigente el {oc.fecha:%d/%m/%Y}.')
    pactados = {li.producto_id: li for li in c.lineas.all()}
    filas, usado = consumo(c)
    saldos = {f['l'].producto_id: f['saldo'] for f in filas}
    for i in oc.items.select_related('producto'):
        li = pactados.get(i.producto_id)
        if li is None:
            errores.append(f'{i.descripcion}: no está en el contrato.')
            continue
        if i.precio_unitario > li.precio_unitario:
            errores.append(f'{i.descripcion}: precio {i.precio_unitario:,.4f} mayor al pactado {li.precio_unitario:,.4f}.')
        if saldos.get(i.producto_id) is not None and saldos[i.producto_id] < 0:
            errores.append(f'{i.descripcion}: supera la cantidad máxima del contrato.')
    if c.monto_maximo is not None and usado > c.monto_maximo:
        errores.append(f'Las órdenes del contrato suman {usado:,.2f}, más que su monto máximo {c.monto_maximo:,.2f}.')
    return errores


def contratos_vigentes(proveedor=None, fecha=None):
    fecha = fecha or timezone.localdate()
    qs = ContratoMarco.objects.filter(estado='VIGENTE', fecha_inicio__lte=fecha, fecha_fin__gte=fecha)
    return qs.filter(proveedor=proveedor) if proveedor else qs
