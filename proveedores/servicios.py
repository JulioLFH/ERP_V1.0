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
    """{producto_id: cantidad} ingresada al almacén para la orden (recepciones y facturas con ingreso directo)."""
    from inventario.models import OperacionItem
    total = defaultdict(lambda: D0)
    filas = OperacionItem.objects.filter(
        Q(operacion__orden_compra=oc) | Q(operacion__compra__orden_compra=oc), operacion__estado='CONFIRMADO',
        operacion__tipo__clase='INGRESO').values('producto_id').annotate(c=Sum('cantidad'))
    for f in filas:
        total[f['producto_id']] += f['c']
    for c in oc.compras.filter(estado='REGISTRADO', stock_aplicado=True).exclude(tipo_comprobante__in=['07', '08']):
        for i in c.items.filter(producto__isnull=False):
            total[i.producto_id] += i.cantidad
    return total


def facturado_por_item(oc, excluir=None):
    """{oc_item_id: cantidad} ya facturada por el portal (no rechazada) y {producto_id: cantidad} en compras
    registradas fuera del portal."""
    por_item = defaultdict(lambda: D0)
    qs = FacturaProveedor.objects.filter(orden_compra=oc).exclude(estado='RECHAZADA')
    if excluir is not None:
        qs = qs.exclude(pk=excluir.pk)
    for f in qs.prefetch_related('items'):
        for i in f.items.all():
            por_item[i.oc_item_id] += i.cantidad
    por_producto = defaultdict(lambda: D0)
    compras_portal = qs.filter(compra__isnull=False).values_list('compra_id', flat=True)
    for c in oc.compras.filter(estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08']).exclude(
            pk__in=list(compras_portal)):
        for i in c.items.filter(producto__isnull=False):
            por_producto[i.producto_id] += i.cantidad
    return por_item, por_producto


def lineas_por_facturar(oc):
    """Líneas de la orden con la cantidad que el proveedor puede facturar: solo lo que ya ingresó al almacén
    (recepciones confirmadas), menos lo ya facturado. Los servicios (no inventariables) se facturan por lo pedido."""
    recibido = recibido_por_producto(oc)
    por_item, por_producto = facturado_por_item(oc)
    disponible = dict(recibido)  # se reparte entre las líneas del mismo producto
    lineas = []
    for item in oc.items.select_related('producto').order_by('id'):
        if item.producto_id and item.producto.es_inventariable:
            base = min(item.cantidad, disponible.get(item.producto_id, D0))
            disponible[item.producto_id] = disponible.get(item.producto_id, D0) - base
        else:
            base = item.cantidad
        ya = por_item.get(item.pk, D0)
        if item.producto_id and por_producto.get(item.producto_id):
            usado = min(por_producto[item.producto_id], base - ya)
            por_producto[item.producto_id] -= usado
            ya += usado
        pendiente = base - ya
        lineas.append({'item': item, 'esperado': max(pendiente, D0), 'precio': item.precio_unitario,
                       'recibido': bool(item.producto_id and item.producto.es_inventariable)})
    return lineas


def tiene_ingreso(oc):
    """La orden ya tiene mercadería ingresada al almacén (requisito para cargar la factura)."""
    return bool(recibido_por_producto(oc)) or not oc.items.filter(producto__tipo='BIEN').exists()


# ---------------------------------------------------------------- registro por el proveedor
def errores_lineas(lineas, enviadas):
    """enviadas: {oc_item_id: (cantidad, precio)}. Valida las tolerancias de Ajustes > Empresa."""
    empresa = Empresa.actual()
    tol_c, tol_p = empresa.tolerancia_cantidad, empresa.tolerancia_precio
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
        if abs(cantidad - linea['esperado']) > tol_c:
            base = 'ingresada al almacén' if linea['recibido'] else 'pendiente de la orden'
            errores.append(f'{item.descripcion}: la cantidad {cantidad:,.2f} difiere de la {base} '
                           f'({linea["esperado"]:,.2f}) en más de ±{tol_c:,.2f}.')
        if abs(precio - linea['precio']) > tol_p:
            errores.append(f'{item.descripcion}: el precio {precio:,.4f} difiere del de la orden '
                           f'({linea["precio"]:,.4f}) en más de ±{tol_p:,.4f}.')
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
    empresa = Empresa.actual()
    errores = []
    valor, igv, calculado = totales(oc, enviadas, empresa.igv_tasa)
    if total_declarado is None:
        errores.append('Indique el monto total de la factura.')
    elif abs(total_declarado - calculado) > empresa.tolerancia_total:
        errores.append(f'El monto total de la factura ({oc.simbolo} {total_declarado:,.2f}) no cuadra con las '
                       f'cantidades y precios registrados ({oc.simbolo} {calculado:,.2f}; tolerancia '
                       f'±{empresa.tolerancia_total:,.2f}).')
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
            if abs(en_xml - registrada) > empresa.tolerancia_cantidad:
                errores.append(f'La cantidad total del XML ({en_xml:,.2f}) no cuadra con la registrada '
                               f'({registrada:,.2f}).')
        if abs(datos_xml['valor_venta'] - valor) > empresa.tolerancia_total:
            errores.append(f'El valor de venta del XML ({datos_xml["valor_venta"]:,.2f}) no cuadra con el '
                           f'registrado ({valor:,.2f}).')
    return errores


def registrar_factura(oc, usuario, cabecera, enviadas, archivos=None, datos_xml=None):
    """Crea la factura del proveedor (estado ENVIADA). Lanza ErrorPortal con los errores encontrados.

    cabecera: serie, numero, fecha_emision, total (monto de la factura), observaciones.
    archivos: {'pdf': bytes, 'pdf_nombre', 'xml_nombre'}; datos_xml: resultado de xml_ubl.leer().
    """
    archivos = archivos or {}
    if datos_xml:  # el XML manda sobre lo escrito
        cabecera = {**cabecera, 'serie': datos_xml['serie'], 'numero': datos_xml['numero'],
                    'fecha_emision': datos_xml['fecha'], 'total': datos_xml['total']}
    if not tiene_ingreso(oc):
        raise ErrorPortal([f'La mercadería de la orden {oc.numero} aún no ingresa al almacén: podrá cargar la '
                           f'factura cuando se registre la recepción.'])
    lineas = lineas_por_facturar(oc)
    errores = errores_lineas(lineas, enviadas)
    total_declarado = cabecera.get('total')
    if 'total' in cabecera or datos_xml:
        errores += errores_cuadre(oc, enviadas, total_declarado, datos_xml)
    serie, numero = cabecera['serie'].upper().strip(), cabecera['numero'].strip()
    if duplicada(oc.tercero, serie, numero):
        errores.append(f'La factura {serie}-{numero} ya fue registrada.')
    if errores:
        raise ErrorPortal(errores)
    empresa = Empresa.actual()
    with transaction.atomic():
        f = FacturaProveedor.objects.create(
            tercero=oc.tercero, orden_compra=oc, serie=serie, numero=numero,
            fecha_emision=cabecera['fecha_emision'], moneda=oc.moneda, total_declarado=total_declarado,
            pdf=archivos.get('pdf'), pdf_nombre=archivos.get('pdf_nombre', '')[:150],
            xml=datos_xml['xml'] if datos_xml else '', xml_nombre=archivos.get('xml_nombre', '')[:150],
            observaciones=cabecera.get('observaciones', ''), enviada_por=usuario)
        for linea in lineas:
            item = linea['item']
            cantidad, precio = enviadas.get(item.pk, (D0, D0))
            if cantidad:
                FacturaProveedorItem.objects.create(
                    factura=f, oc_item=item, producto=item.producto, descripcion=item.descripcion,
                    cantidad=cantidad, precio_unitario=precio, cantidad_esperada=linea['esperado'],
                    precio_orden=linea['precio'])
        f.calcular_totales(empresa.igv_tasa, gravada=oc.tipo_operacion == 'GRAVADA')
        f.save()
        r = validar_sunat(f, guardar=False)
        if r is not None and r['valido'] is False:
            raise ErrorPortal([f'SUNAT: {r["detalle"]}. Revise serie, número, fecha y total '
                               f'({f.simbolo} {f.total:,.2f}).'])
        f.save()
    return f


# ---------------------------------------------------------------- revisión en el ERP
def registrar_compra(factura, usuario):
    """Aprueba la factura del portal y la registra como compra (vence desde el ingreso de la mercadería)."""
    from compras.models import Compra, CompraItem, OrdenCompra
    from core.tipo_cambio import venta_del_dia
    from inventario.servicios import tiene_recepciones
    if factura.estado != 'ENVIADA':
        raise ErrorPortal(['La factura ya fue revisada.'])
    oc = factura.orden_compra
    if not tiene_ingreso(oc):
        raise ErrorPortal([f'La mercadería de la orden {oc.numero} ya no figura ingresada al almacén '
                           f'(¿se anuló la recepción?).'])
    if Compra.objects.filter(tercero=factura.tercero, tipo_comprobante=factura.tipo_comprobante,
                             serie=factura.serie, numero=factura.numero).exists():
        raise ErrorPortal([f'La compra {factura.numero_completo} ya está registrada.'])
    recibida = tiene_recepciones(orden=oc)
    with transaction.atomic():
        c = Compra(tipo_comprobante=factura.tipo_comprobante, serie=factura.serie, numero=factura.numero,
                   tercero=factura.tercero, fecha_emision=factura.fecha_emision, moneda=factura.moneda,
                   tipo_cambio=venta_del_dia(factura.fecha_emision) if factura.moneda == 'USD' else Decimal('1'),
                   tipo_operacion=oc.tipo_operacion, clasificacion='MERCADERIA', orden_compra=oc,
                   centro_costo=oc.centro_costo, forma_pago='CREDITO' if oc.dias_credito else 'CONTADO',
                   ingresar_almacen=not recibida, almacen=Almacen.principal(),
                   glosa=f'Factura registrada por el proveedor en el portal ({oc.numero})')
        c.save()
        for i in factura.items.all():
            CompraItem.objects.create(documento=c, producto=i.producto, descripcion=i.descripcion,
                                      cantidad=i.cantidad, precio_unitario=i.precio_unitario)
        c.calcular_totales()
        c.save()
        if c.ingresar_almacen:
            c.aplicar_stock()
        OrdenCompra.objects.filter(pk=oc.pk).exclude(estado='ANULADO').update(estado='ATENDIDO')
        oc.actualizar_vencimientos()
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
