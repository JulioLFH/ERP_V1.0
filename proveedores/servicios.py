"""Reglas del portal de proveedores y de los correos a proveedores."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from core import correo, sunat_consulta
from core.models import Almacen, Empresa

from .models import FacturaProveedor, FacturaProveedorItem

D0 = Decimal('0')


class ErrorPortal(Exception):
    pass


# ---------------------------------------------------------------- cantidades
def recibido_por_producto(oc):
    """{producto_id: cantidad EN UNIDAD DE COMPRA} ingresada al almacén para la orden (recepciones y facturas con
    ingreso directo). Las recepciones están en unidad de almacén y se dividen entre el factor de compra."""
    return recibido_por_ordenes([oc.pk]).get(oc.pk, defaultdict(lambda: D0))


def recibido_por_ordenes(ids):
    """{orden_id: {producto_id: cantidad en unidad de compra}} de varias órdenes de compra con cuatro consultas en
    total (antes eran tres por orden: la campana y la sugerencia de compra tardaban segundos)."""
    from compras.models import CompraItem
    from inventario.models import OperacionItem
    ids = list(ids)
    total = defaultdict(lambda: defaultdict(lambda: D0))
    if not ids:
        return total
    ingresos = OperacionItem.objects.filter(operacion__estado='CONFIRMADO', operacion__tipo__clase='INGRESO')
    # una recepción cuenta para la orden a la que apunta, directamente o por la factura que la originó
    for campo in ('operacion__orden_compra_id', 'operacion__compra__orden_compra_id'):
        filtro = {f'{campo}__in': ids}
        if campo != 'operacion__orden_compra_id':
            filtro['operacion__orden_compra__isnull'] = True
        for f in (ingresos.filter(**filtro).values(campo, 'producto_id', 'producto__factor_compra')
                  .annotate(c=Sum('cantidad')).order_by()):
            factor = f['producto__factor_compra'] if f['producto__factor_compra'] and f['producto__factor_compra'] > 0 \
                else Decimal('1')
            total[f[campo]][f['producto_id']] += f['c'] / factor
    for f in (CompraItem.objects.filter(documento__orden_compra_id__in=ids, documento__estado='REGISTRADO',
                                        documento__stock_aplicado=True, producto__isnull=False)
              .exclude(documento__tipo_comprobante__in=['07', '08'])
              .values('documento__orden_compra_id', 'producto_id').annotate(c=Sum('cantidad')).order_by()):
        total[f['documento__orden_compra_id']][f['producto_id']] += f['c']
    return total


def _factor(producto):
    return producto.factor_compra if producto.factor_compra and producto.factor_compra > 0 else Decimal('1')


def fuentes_oc(oc):
    """Entregas de la orden pendientes de facturar (verificación contra la entrega, como la MIRO de SAP): cada línea
    de recepción del almacén (mercadería, en unidad de compra) y cada conformidad de servicio (HES) liberada, con lo
    que falta facturar. Lo facturado antes de guardar la entrega por línea (facturas antiguas del portal y compras
    registradas fuera del portal) se descuenta de las entregas más antiguas."""
    from compras.models import CompraItem, ConformidadServicio
    from inventario.models import OperacionItem
    vivas = FacturaProveedorItem.objects.exclude(factura__estado='RECHAZADA')
    por_rec = defaultdict(lambda: D0)
    por_hes = defaultdict(lambda: D0)
    antiguo_producto = defaultdict(lambda: D0)  # facturado sin entrega, por producto (mercadería)
    antiguo_item = defaultdict(lambda: D0)      # facturado sin entrega, por línea (servicios)
    portal = vivas.filter(Q(oc_item__documento=oc) | Q(orden_compra=oc)).select_related('oc_item__producto', 'factura')
    compras_portal = set()
    for i in portal:
        if i.factura.compra_id:
            compras_portal.add(i.factura.compra_id)
        if i.recepcion_item_id:
            por_rec[i.recepcion_item_id] += i.cantidad
        elif i.conformidad_id:
            por_hes[i.conformidad_id] += i.cantidad
        elif i.oc_item_id and i.oc_item.producto_id and i.oc_item.producto.es_inventariable:
            antiguo_producto[i.oc_item.producto_id] += i.cantidad
        elif i.oc_item_id:
            antiguo_item[i.oc_item_id] += i.cantidad
    for c in (CompraItem.objects.filter(documento__orden_compra=oc, documento__estado='REGISTRADO',
                                        producto__isnull=False)
              .exclude(documento__tipo_comprobante__in=['07', '08']).exclude(documento_id__in=compras_portal)
              .select_related('producto')):
        if c.producto.es_inventariable:
            antiguo_producto[c.producto_id] += c.cantidad
        else:
            item = oc.items.filter(producto=c.producto).first()
            if item is not None:
                antiguo_item[item.pk] += c.cantidad
    items = list(oc.items.select_related('producto').order_by('id'))
    capacidad = {i.pk: i.cantidad for i in items}
    fuentes = []

    def agregar(clave, item, cantidad, ya, **datos):
        pendiente = cantidad - ya
        if pendiente > 0:
            fuentes.append({'clave': clave, 'oc': oc, 'item': item, 'esperado': pendiente.quantize(Decimal('0.01')),
                            'precio': item.precio_neto.quantize(Decimal('0.0001')), 'descripcion': item.descripcion,
                            'recepcion': None, 'recepcion_item': None, 'hes': None, 'guia': '', **datos})

    recepciones = (OperacionItem.objects.filter(operacion__estado='CONFIRMADO', operacion__tipo__clase='INGRESO')
                   .filter(Q(operacion__orden_compra=oc) |
                           Q(operacion__compra__orden_compra=oc, operacion__orden_compra__isnull=True))
                   .select_related('operacion', 'producto').order_by('operacion__fecha', 'operacion_id', 'id'))
    for ri in recepciones:
        recibido = ri.cantidad / _factor(ri.producto)
        ya = por_rec.get(ri.pk, D0)
        for item in items:
            if recibido <= 0:
                break
            if item.producto_id != ri.producto_id or capacidad[item.pk] <= 0:
                continue
            parte = min(recibido, capacidad[item.pk])
            capacidad[item.pk] -= parte
            recibido -= parte
            ya_parte = min(ya, parte)
            ya -= ya_parte
            usado = min(antiguo_producto.get(item.producto_id, D0), parte - ya_parte)
            antiguo_producto[item.producto_id] = antiguo_producto.get(item.producto_id, D0) - usado
            agregar(f'R{ri.pk}-{item.pk}', item, parte, ya_parte + usado, recepcion=ri.operacion, recepcion_item=ri,
                    guia=ri.operacion.referencia, fecha=ri.operacion.fecha, recibido=True)
    servicios_oc = [i for i in items if not (i.producto_id and i.producto.es_inventariable)]
    for hes in (ConformidadServicio.objects.filter(orden_compra=oc, estado='LIBERADA')
                .order_by('fecha', 'id')):
        lineas = [hes.oc_item] if hes.oc_item_id else servicios_oc
        for item in lineas:
            cantidad = hes.cantidad if hes.oc_item_id else (item.cantidad * hes.porcentaje / 100)
            ya = por_hes.get(hes.pk, D0) if hes.oc_item_id else D0
            usado = min(antiguo_item.get(item.pk, D0), max(cantidad - ya, D0))
            antiguo_item[item.pk] = antiguo_item.get(item.pk, D0) - usado
            agregar(f'H{hes.pk}-{item.pk}', item, cantidad, ya + usado, hes=hes, guia=hes.numero, fecha=hes.fecha,
                    recibido=False)
    return fuentes


def ordenes_facturables(tercero):
    from compras.models import OrdenCompra
    return (OrdenCompra.objects.filter(tercero=tercero, estado__in=('APROBADO', 'ATENDIDO'))
            .exclude(estado_proveedor='RECHAZADA')
            .filter(Q(recepciones__estado='CONFIRMADO') | Q(conformidades__estado='LIBERADA') |
                    Q(compras__operaciones_inventario__estado='CONFIRMADO'))
            .distinct().order_by('fecha', 'id'))


def fuentes_proveedor(tercero, ocs=None):
    """Todas las entregas pendientes de facturar del proveedor (varias órdenes de compra)."""
    fuentes = []
    for oc in (ocs if ocs is not None else ordenes_facturables(tercero)):
        fuentes += fuentes_oc(oc)
    return fuentes


def lineas_por_facturar(oc):
    """Líneas de la orden con la cantidad que el proveedor puede facturar: lo recibido en el almacén (mercadería) o
    lo conformado en HES liberadas (servicios), menos lo ya facturado."""
    fuentes = fuentes_oc(oc)
    lineas = []
    for item in oc.items.select_related('producto').order_by('id'):
        propias = [f for f in fuentes if f['item'].pk == item.pk]
        lineas.append({'item': item, 'esperado': sum((f['esperado'] for f in propias), D0),
                       'precio': item.precio_neto.quantize(Decimal('0.0001')), 'fuentes': propias,
                       'recibido': bool(item.producto_id and item.producto.es_inventariable)})
    return lineas


def tiene_ingreso(oc):
    """La orden ya tiene algo que facturar: mercadería ingresada al almacén o una HES liberada."""
    return bool(recibido_por_producto(oc)) or oc.conformidades.filter(estado='LIBERADA').exists()


# ---------------------------------------------------------------- registro por el proveedor
def errores_lineas(lineas, enviadas, proveedor=None, fuera=None):
    """enviadas: {oc_item_id: (cantidad, precio)}. Valida las tolerancias (de la empresa o del proveedor, en importe y
    en %). Una cantidad fuera de tolerancia es error; un precio fuera de tolerancia, si se pasa la lista «fuera», se
    anota ahí (la factura se recibe para revisión y su pago queda bloqueado) y si no, es error."""
    from compras import tolerancias
    errores, alguna = [], False
    for linea in lineas:
        item = linea['item']
        cantidad, precio = enviadas.get(item.pk, (D0, D0))
        if not cantidad:
            continue
        alguna = True
        if cantidad < 0 or precio < 0:
            errores.append(f'{item.descripcion}: cantidad y precio deben ser positivos.')
            continue
        if linea['recibido'] and linea['esperado'] <= 0:
            errores.append(f'{item.descripcion}: no tiene cantidades ingresadas al almacén pendientes de facturar.')
            continue
        if abs(cantidad - linea['esperado']) > tolerancias.permitida(proveedor, 'cantidad', linea['esperado']):
            base = 'ingresada al almacén' if linea['recibido'] else 'pendiente de la orden'
            errores.append(f'{item.descripcion}: la cantidad {cantidad:,.2f} difiere de la {base} '
                           f'({linea["esperado"]:,.2f}) en más de {tolerancias.texto(proveedor, "cantidad")}.')
        if abs(precio - linea['precio']) > tolerancias.permitida(proveedor, 'precio', linea['precio']):
            texto = (f'{item.descripcion}: el precio {precio:,.4f} difiere del de la orden ({linea["precio"]:,.4f}) '
                     f'en más de {tolerancias.texto(proveedor, "precio")}.')
            (fuera if fuera is not None else errores).append(texto)
    if not alguna:
        errores.append('Indique la cantidad facturada de al menos un producto.')
    return errores


def duplicada(tercero, serie, numero, excluir=None):
    from compras.models import Compra
    numero = numero.lstrip('0') or '0'
    qs = FacturaProveedor.objects.filter(tercero=tercero, serie__iexact=serie).exclude(estado='RECHAZADA')
    if excluir is not None:
        qs = qs.exclude(pk=excluir.pk)
    if any((f.numero.lstrip('0') or '0') == numero for f in qs):
        return True
    return any((c.numero.lstrip('0') or '0') == numero for c in Compra.objects.filter(
        tercero=tercero, tipo_comprobante='01', serie__iexact=serie).exclude(estado='ANULADO'))


def validar_sunat(factura, guardar=True):
    """Consulta la factura en SUNAT. Devuelve el resultado o None si la API no está configurada."""
    if not sunat_consulta.configurada():
        return None
    try:
        r = sunat_consulta.validar(factura.tercero.numero_doc, factura.tipo_comprobante, factura.serie,
                                   factura.numero, factura.fecha_emision,
                                   factura.total_declarado if factura.total_declarado is not None else factura.total)
        factura.estado_sunat = 'VALIDO' if r['valido'] else 'OBSERVADO'
        factura.sunat_detalle = r['detalle'][:300]
    except sunat_consulta.ErrorConsulta as exc:
        r = {'valido': None, 'detalle': str(exc)}
        factura.estado_sunat, factura.sunat_detalle = 'ERROR', str(exc)[:300]
    factura.sunat_consultado_en = timezone.now()
    if guardar and factura.pk:
        factura.save(update_fields=['estado_sunat', 'sunat_detalle', 'sunat_consultado_en'])
    return r


def totales(oc, enviadas, tasa_igv):
    """(valor venta, igv, total) calculados con las líneas que registra el proveedor."""
    from core.models import r2
    subtotal = sum((r2(c * p) for c, p in enviadas.values() if c), D0)
    igv = r2(subtotal * tasa_igv / 100) if oc.tipo_operacion == 'GRAVADA' else D0
    return subtotal, igv, subtotal + igv


def errores_cuadre(oc, enviadas, total_declarado, datos_xml=None):
    """La factura debe cuadrar: cantidades (contra la orden / lo recibido y contra el XML) y monto total."""
    from compras import tolerancias
    empresa = Empresa.actual()
    errores = []
    valor, igv, calculado = totales(oc, enviadas, empresa.igv_tasa)
    tol_total = tolerancias.permitida(oc.tercero, 'total', calculado)
    if total_declarado is None:
        errores.append('Indique el monto total de la factura.')
    elif abs(total_declarado - calculado) > tol_total:
        errores.append(f'El monto total de la factura ({oc.simbolo} {total_declarado:,.2f}) no cuadra con las '
                       f'cantidades y precios registrados ({oc.simbolo} {calculado:,.2f}; tolerancia '
                       f'{tolerancias.texto(oc.tercero, "total")}).')
    if datos_xml:
        if datos_xml['ruc_emisor'] and datos_xml['ruc_emisor'] != oc.tercero.numero_doc:
            errores.append(f'El XML fue emitido por el RUC {datos_xml["ruc_emisor"]}, no por {oc.tercero.nombre} '
                           f'(RUC {oc.tercero.numero_doc}).')
        if datos_xml['ruc_receptor'] and datos_xml['ruc_receptor'] != empresa.ruc:
            errores.append(f'La factura está emitida al RUC {datos_xml["ruc_receptor"]}, no a {empresa.razon_social} '
                           f'(RUC {empresa.ruc}).')
        if datos_xml['tipo'] != '01':
            errores.append('El XML no corresponde a una factura (tipo 01).')
        if datos_xml['moneda'] != oc.moneda:
            errores.append(f'La factura está en {datos_xml["moneda"]} y la orden en {oc.moneda}.')
        if datos_xml['lineas']:
            en_xml = sum((l['cantidad'] for l in datos_xml['lineas']), D0)
            registrada = sum((c for c, _ in enviadas.values()), D0)
            if abs(en_xml - registrada) > tolerancias.permitida(oc.tercero, 'cantidad', registrada):
                errores.append(f'La cantidad total del XML ({en_xml:,.2f}) no cuadra con la registrada '
                               f'({registrada:,.2f}).')
        if abs(datos_xml['valor_venta'] - valor) > tolerancias.permitida(oc.tercero, 'total', valor):
            errores.append(f'El valor de venta del XML ({datos_xml["valor_venta"]:,.2f}) no cuadra con el '
                           f'registrado ({valor:,.2f}).')
    return errores


def _repartir(lineas, enviadas):
    """Factura por línea de la orden (flujo de una OC): reparte lo facturado de cada línea entre sus entregas, de la
    más antigua a la más reciente; lo que excede (dentro de la tolerancia) va a la última."""
    salida = []
    for linea in lineas:
        item = linea['item']
        cantidad, precio = enviadas.get(item.pk, (D0, D0))
        if not cantidad:
            continue
        fuentes, resto = linea.get('fuentes') or [], cantidad
        for n, f in enumerate(fuentes):
            parte = resto if n == len(fuentes) - 1 else min(resto, f['esperado'])
            if parte > 0:
                salida.append((f, item, parte, precio, f['esperado'], linea['precio']))
                resto -= parte
        if resto > 0 and not fuentes:
            salida.append((None, item, resto, precio, linea['esperado'], linea['precio']))
    return salida


def _crear(oc, usuario, cabecera, lineas, total_declarado, archivos, datos_xml, fuera):
    """Graba la factura (ENVIADA) con sus líneas [(fuente, oc_item, cantidad, precio, esperado, precio_orden)]."""
    empresa = Empresa.actual()
    with transaction.atomic():
        f = FacturaProveedor.objects.create(
            tercero=oc.tercero, orden_compra=oc, serie=cabecera['serie'], numero=cabecera['numero'],
            fecha_emision=cabecera['fecha_emision'], moneda=oc.moneda, total_declarado=total_declarado,
            pdf=archivos.get('pdf'), pdf_nombre=archivos.get('pdf_nombre', '')[:150],
            xml=datos_xml['xml'] if datos_xml else '', xml_nombre=archivos.get('xml_nombre', '')[:150],
            observaciones=cabecera.get('observaciones', ''), enviada_por=usuario,
            fuera_tolerancia=' '.join(fuera)[:500])
        for fuente, item, cantidad, precio, esperado, precio_orden in lineas:
            FacturaProveedorItem.objects.create(
                factura=f, oc_item=item, producto=item.producto, descripcion=item.descripcion, cantidad=cantidad,
                precio_unitario=precio, cantidad_esperada=esperado, precio_orden=precio_orden,
                orden_compra=item.documento, recepcion=fuente['recepcion'] if fuente else None,
                recepcion_item=fuente['recepcion_item'] if fuente else None,
                conformidad=fuente['hes'] if fuente else None, guia=(fuente['guia'] if fuente else '')[:60])
        f.calcular_totales(empresa.igv_tasa, gravada=oc.tipo_operacion == 'GRAVADA')
        f.save()
        r = validar_sunat(f, guardar=False)
        if r is not None and r['valido'] is False:
            raise ErrorPortal([f'SUNAT: {r["detalle"]}. Revise serie, número, fecha y total '
                               f'({f.simbolo} {f.total:,.2f}).'])
        f.save()
    return f


def _cabecera(cabecera, datos_xml):
    if datos_xml:  # el XML manda sobre lo escrito
        cabecera = {**cabecera, 'serie': datos_xml['serie'], 'numero': datos_xml['numero'],
                    'fecha_emision': datos_xml['fecha'], 'total': datos_xml['total']}
    return {**cabecera, 'serie': cabecera['serie'].upper().strip(), 'numero': cabecera['numero'].strip()}


def registrar_factura(oc, usuario, cabecera, enviadas, archivos=None, datos_xml=None):
    """Factura de una orden por línea (estado ENVIADA); cada línea se reparte entre sus recepciones o HES. Lanza
    ErrorPortal con los errores encontrados.

    cabecera: serie, numero, fecha_emision, total (monto de la factura), observaciones.
    archivos: {'pdf': bytes, 'pdf_nombre', 'xml_nombre'}; datos_xml: resultado de xml_ubl.leer().
    """
    archivos = archivos or {}
    cabecera = _cabecera(cabecera, datos_xml)
    if not tiene_ingreso(oc):
        raise ErrorPortal([f'La orden {oc.numero} aún no tiene mercadería ingresada al almacén ni servicios con '
                           f'conformidad liberada: podrá cargar la factura cuando se registren.'])
    lineas = lineas_por_facturar(oc)
    fuera = []  # precios fuera de tolerancia: se recibe para revisión de Compras y el pago queda bloqueado
    errores = errores_lineas(lineas, enviadas, oc.tercero, fuera)
    total_declarado = cabecera.get('total')
    if 'total' in cabecera or datos_xml:
        errores += errores_cuadre(oc, enviadas, total_declarado, datos_xml)
    if duplicada(oc.tercero, cabecera['serie'], cabecera['numero']):
        errores.append(f'La factura {cabecera["serie"]}-{cabecera["numero"]} ya fue registrada.')
    if errores:
        raise ErrorPortal(errores)
    return _crear(oc, usuario, cabecera, _repartir(lineas, enviadas), total_declarado, archivos, datos_xml, fuera)


def registrar_factura_entregas(tercero, usuario, cabecera, enviadas, archivos=None, datos_xml=None):
    """Factura desde las entregas (recepciones y HES liberadas), de una o varias órdenes de compra del proveedor con
    la misma moneda y tipo de operación. enviadas: {clave de la entrega: (cantidad, precio)}. Ninguna línea puede
    superar lo recibido en su recepción o lo conformado en su HES."""
    from compras import tolerancias
    archivos = archivos or {}
    cabecera = _cabecera(cabecera, datos_xml)
    fuentes = {f['clave']: f for f in fuentes_proveedor(tercero)}
    errores, fuera, lineas = [], [], []
    for clave, (cantidad, precio) in enviadas.items():
        if not cantidad:
            continue
        f = fuentes.get(clave)
        if f is None:
            errores.append('Una de las entregas elegidas ya no está pendiente de facturar: vuelva a cargar la página.')
            continue
        origen = f['recepcion'].numero if f['recepcion'] else f['hes'].numero
        if cantidad < 0 or precio < 0:
            errores.append(f'{f["descripcion"]} ({origen}): cantidad y precio deben ser positivos.')
            continue
        if cantidad > f['esperado']:
            texto = 'recibido en la recepción' if f['recepcion'] else 'conformado en la HES'
            errores.append(f'{f["descripcion"]}: factura {cantidad:,.2f} y lo {texto} {origen} pendiente de '
                           f'facturar es {f["esperado"]:,.2f}.')
        if abs(precio - f['precio']) > tolerancias.permitida(tercero, 'precio', f['precio']):
            fuera.append(f'{f["descripcion"]} ({f["oc"].numero}): el precio {precio:,.4f} difiere del de la orden '
                         f'({f["precio"]:,.4f}) en más de {tolerancias.texto(tercero, "precio")}.')
        lineas.append((f, f['item'], cantidad, precio, f['esperado'], f['precio']))
    if not lineas and not errores:
        errores.append('Marque al menos una recepción o conformidad a facturar.')
    ocs = list(dict.fromkeys(l[0]['oc'] for l in lineas))
    if len({o.moneda for o in ocs}) > 1:
        errores.append('Las órdenes elegidas tienen distinta moneda: facture cada moneda por separado.')
    if len({o.tipo_operacion for o in ocs}) > 1:
        errores.append('Las órdenes elegidas tienen distinto tipo de operación (gravada / no gravada): facture por '
                       'separado.')
    if errores:
        raise ErrorPortal(errores)
    oc = ocs[0]
    total_declarado = cabecera.get('total')
    por_linea = {n: (l[2], l[3]) for n, l in enumerate(lineas)}
    errores += errores_cuadre(oc, por_linea, total_declarado, datos_xml)
    if duplicada(tercero, cabecera['serie'], cabecera['numero']):
        errores.append(f'La factura {cabecera["serie"]}-{cabecera["numero"]} ya fue registrada.')
    if errores:
        raise ErrorPortal(errores)
    return _crear(oc, usuario, cabecera, lineas, total_declarado, archivos, datos_xml, fuera)


# ---------------------------------------------------------------- revisión en el ERP
def registrar_compra(factura, usuario):
    """Aprueba la factura del portal y la registra como compra (vence desde el ingreso de la mercadería)."""
    from compras.models import Compra, CompraItem, OrdenCompra
    from core.tipo_cambio import venta_del_dia
    from inventario.servicios import tiene_recepciones
    if factura.estado != 'ENVIADA':
        raise ErrorPortal(['La factura ya fue revisada.'])
    oc = factura.orden_compra
    items = list(factura.items.select_related('orden_compra', 'recepcion', 'conformidad'))
    ocs = list(dict.fromkeys([oc] + [i.orden_compra for i in items if i.orden_compra_id]))
    for o in ocs:
        if not tiene_ingreso(o):
            raise ErrorPortal([f'La orden {o.numero} ya no figura con mercadería ingresada al almacén ni con '
                               f'conformidad liberada (¿se anuló la recepción o la HES?).'])
    for i in items:
        if i.recepcion_id and i.recepcion.estado != 'CONFIRMADO':
            raise ErrorPortal([f'La recepción {i.recepcion.numero} de la línea «{i.descripcion}» ya no está '
                               f'confirmada.'])
        if i.conformidad_id and i.conformidad.estado != 'LIBERADA':
            raise ErrorPortal([f'La conformidad {i.conformidad.numero} de la línea «{i.descripcion}» ya no está '
                               f'liberada.'])
    if Compra.objects.filter(tercero=factura.tercero, tipo_comprobante=factura.tipo_comprobante,
                             serie=factura.serie, numero=factura.numero).exists():
        raise ErrorPortal([f'La compra {factura.numero_completo} ya está registrada.'])
    recibida = any(tiene_recepciones(orden=o) for o in ocs)
    lineas_oc = [i for o in ocs for i in o.items.select_related('producto')]
    with transaction.atomic():
        c = Compra(tipo_comprobante=factura.tipo_comprobante, serie=factura.serie, numero=factura.numero,
                   tercero=factura.tercero, fecha_emision=factura.fecha_emision, moneda=factura.moneda,
                   tipo_cambio=venta_del_dia(factura.fecha_emision) if factura.moneda == 'USD' else Decimal('1'),
                   tipo_operacion=oc.tipo_operacion, orden_compra=oc,
                   clasificacion=Compra.clasificacion_de(lineas_oc),
                   centro_costo=oc.centro_costo, forma_pago='CREDITO' if oc.dias_credito else 'CONTADO',
                   ingresar_almacen=not recibida, almacen=Almacen.principal(),
                   glosa=f'Factura registrada por el proveedor en el portal ({", ".join(o.numero for o in ocs)})')
        c.save()
        for i in factura.items.all():
            CompraItem.objects.create(documento=c, producto=i.producto, descripcion=i.descripcion,
                                      cantidad=i.cantidad, precio_unitario=i.precio_unitario)
        c.calcular_totales()
        if factura.total_declarado:  # el total de la factura del proveedor (ya validado contra la tolerancia)
            from compras.tolerancias import permitida
            try:
                c.cuadrar_con(factura.total_declarado, permitida(factura.tercero, 'total', c.total))
            except ValueError:
                c.ajuste_redondeo = D0
                c.calcular_totales()
        if factura.fuera_tolerancia:  # Compras la aprobó, pero Finanzas no la paga hasta que se libere
            c.pago_bloqueado = True
            c.motivo_bloqueo_pago = f'Precio fuera de la tolerancia: {factura.fuera_tolerancia}'[:300]
        c.save()
        from compras.registro_info import aprender
        aprender(c)
        if c.ingresar_almacen:
            c.aplicar_stock()
        OrdenCompra.objects.filter(pk__in=[o.pk for o in ocs]).exclude(estado='ANULADO').update(estado='ATENDIDO')
        for o in ocs:
            o.actualizar_vencimientos()
        factura.estado, factura.compra = 'APROBADA', c
        factura.revisado_por, factura.revisado_en = usuario, timezone.now()
        factura.save()
    return c


def rechazar(factura, usuario, motivo):
    if factura.estado != 'ENVIADA':
        raise ErrorPortal(['La factura ya fue revisada.'])
    if len((motivo or '').strip()) < 5:
        raise ErrorPortal(['Indique el motivo del rechazo (mínimo 5 caracteres).'])
    factura.estado, factura.motivo_rechazo = 'RECHAZADA', motivo.strip()[:300]
    factura.revisado_por, factura.revisado_en = usuario, timezone.now()
    factura.save()


# ---------------------------------------------------------------- correos
def correo_proveedor(tercero):
    """Correos del proveedor: su ficha y los usuarios del portal."""
    correos = [tercero.email] + [a.usuario.email for a in tercero.accesos_portal.select_related('usuario')]
    vistos, lista = set(), []
    for c in correos:
        if c and c.lower() not in vistos:
            vistos.add(c.lower())
            lista.append(c)
    return lista


def enviar_orden(oc, request):
    """Envía la orden al proveedor con el enlace para aceptarla o rechazarla."""
    destinos = correo_proveedor(oc.tercero)
    enlace = request.build_absolute_uri(reverse('portal:oc_aceptacion', args=[oc.token_aceptacion()]))
    correo.enviar(destinos, f'Orden de compra {oc.numero} - {Empresa.actual().razon_social}',
                  'proveedores/correo_orden.html',
                  {'oc': oc, 'items': oc.items.all(), 'enlace': enlace,
                   'portal': request.build_absolute_uri(reverse('portal:inicio'))})
    oc.estado_proveedor = 'ACEPTADA' if oc.estado_proveedor == 'ACEPTADA' else 'ENVIADA'
    oc.enviada_en, oc.enviada_a = timezone.now(), ', '.join(destinos)[:200]
    oc.save(update_fields=['estado_proveedor', 'enviada_en', 'enviada_a'])
    return destinos


def enviar_conformidad(op, request):
    """Conformidad de recepción (aceptación de la mercadería) al proveedor de la orden de compra."""
    from inventario.servicios import orden_de
    oc = orden_de(op)
    tercero = oc.tercero if oc else (op.compra.tercero if op.compra_id else None)
    if tercero is None:
        raise correo.ErrorCorreo('La recepción no tiene orden de compra ni proveedor.')
    destinos = correo_proveedor(tercero)
    vence = None
    if oc and oc.dias_credito is not None:
        vence = op.fecha + timedelta(days=oc.dias_credito)
    correo.enviar(destinos, f'Conformidad de recepción {op.numero}' + (f' - OC {oc.numero}' if oc else ''),
                  'proveedores/correo_conformidad.html',
                  {'op': op, 'oc': oc, 'items': op.items.select_related('producto'), 'vence': vence,
                   'portal': request.build_absolute_uri(reverse('portal:inicio'))})
    op.conformidad_enviada_en, op.conformidad_enviada_a = timezone.now(), ', '.join(destinos)[:200]
    op.save(update_fields=['conformidad_enviada_en', 'conformidad_enviada_a'])
    return destinos
