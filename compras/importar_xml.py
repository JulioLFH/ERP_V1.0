"""Registro de compras desde el XML de la factura electrónica del proveedor (UBL 2.1 SUNAT).

1) `analizar` lee el XML, valida que la factura sea para la empresa, busca (o propone crear) al proveedor, evita
   duplicados y sugiere el producto de cada línea: por código del proveedor, código de barras, compras anteriores al
   mismo proveedor con la misma descripción, o nombre igual.
2) `registrar` crea la compra con lo elegido, comprueba que el total cuadre con el XML y guarda el XML como sustento.
"""
from decimal import Decimal

from django.db import transaction

from core.models import Almacen, Empresa, Producto, Tercero
from proveedores.xml_ubl import ErrorXML, leer

from .models import Compra, CompraItem

D0 = Decimal('0')
TOLERANCIA = Decimal('0.10')


class ErrorImportacion(Exception):
    pass


def _producto_sugerido(linea, proveedor):
    codigo, desc = linea['codigo'].strip(), linea['descripcion'].strip()
    if codigo:
        p = Producto.objects.filter(activo=True, es_plantilla=False).filter(codigo__iexact=codigo).first() or \
            Producto.objects.filter(activo=True, es_plantilla=False, codigo_barras=codigo).first()
        if p:
            return p, 'código'
    if desc and proveedor:
        anterior = (CompraItem.objects.filter(documento__tercero=proveedor, descripcion__iexact=desc,
                                              producto__isnull=False, producto__activo=True)
                    .order_by('-documento__fecha_emision').select_related('producto').first())
        if anterior:
            return anterior.producto, 'compra anterior'
    if desc:
        p = Producto.objects.filter(activo=True, es_plantilla=False, nombre__iexact=desc).first()
        if p:
            return p, 'nombre'
    return None, ''


def _clave_oc(texto):
    """(serie, número) de un N° de orden: «OC01-00000901», «OC01-901» y «901» se comparan por su número."""
    import re
    t = (texto or '').strip().upper()
    m = re.match(r'^(.*?)[\s\-/]*0*(\d+)$', t)
    if not m:
        return ''.join(ch for ch in t if ch.isalnum()), ''
    return ''.join(ch for ch in m.group(1) if ch.isalnum()), m.group(2)


def _misma_orden(citada, numero):
    def plano(t):
        return ''.join(ch for ch in (t or '').upper() if ch.isalnum())
    if plano(citada) == plano(numero):
        return True
    serie_c, num_c = _clave_oc(citada)
    serie, num = _clave_oc(numero)
    if not num_c:
        return False
    return num_c == num and (not serie_c or serie.startswith(serie_c) or serie.endswith(serie_c))


def ordenes_candidatas(proveedor):
    """v1.117.0: órdenes de compra aprobadas del proveedor a las que se puede conectar la factura."""
    from .models import OrdenCompra
    if proveedor is None:
        return OrdenCompra.objects.none()
    return OrdenCompra.objects.filter(tercero=proveedor, estado__in=('APROBADO', 'ATENDIDO')).order_by('-fecha', '-id')


def orden_sugerida(xml, proveedor):
    """La orden que el proveedor cita en el XML (OrderReference) o, si solo hay una aprobada por facturar, esa."""
    candidatas = list(ordenes_candidatas(proveedor)[:50])
    citada = (xml.get('orden') or '').strip()
    if citada:
        for oc in candidatas:
            if _misma_orden(citada, oc.numero):
                return oc, 'citada en el XML'
    abiertas = [oc for oc in candidatas if oc.estado == 'APROBADO']
    if len(abiertas) == 1:
        return abiertas[0], 'única orden aprobada del proveedor'
    return None, ''


def _producto_de_orden(linea, i, n_lineas, items):
    """Producto de la orden para la línea del XML: misma descripción o código, o la misma posición si la factura
    tiene tantas líneas como la orden."""
    desc, codigo = linea['descripcion'].strip().lower(), linea['codigo'].strip().lower()
    for it in items:
        p = it.producto
        if p is None:
            continue
        if desc and desc in (it.descripcion.strip().lower(), p.nombre.strip().lower()):
            return p
        if codigo and codigo in ((p.codigo or '').lower(), (p.codigo_barras or '').lower()):
            return p
    if n_lineas == len(items) and items[i].producto_id:
        return items[i].producto
    return None


def aplicar_orden(xml, oc):
    """Completa con los productos de la orden las líneas del XML que no tenían sugerencia."""
    if oc is None:
        return xml
    items = list(oc.items.select_related('producto').order_by('id'))
    for i, l in enumerate(xml['lineas']):
        if l['producto'] is None and items:
            p = _producto_de_orden(l, i, len(xml['lineas']), items)
            if p is not None:
                l['producto'], l['criterio'] = p, f'orden {oc.numero}'
    return xml


def validar_orden(xml, oc, productos, ingresar):
    """Errores para registrar la factura contra la orden (los mismos controles que al registrarla a mano)."""
    errores = []
    proveedor = xml['proveedor']
    if oc.estado not in ('APROBADO', 'ATENDIDO'):
        errores.append(f'La orden {oc.numero} está «{oc.get_estado_display()}»: solo se facturan órdenes aprobadas.')
    if proveedor is not None and oc.tercero_id != proveedor.pk:
        errores.append(f'La orden {oc.numero} es de {oc.tercero.nombre}, no del emisor del XML.')
    if oc.moneda != (xml['moneda'] if xml['moneda'] in ('PEN', 'USD') else 'PEN'):
        errores.append(f'La orden {oc.numero} está en {oc.moneda} y la factura en {xml["moneda"]}.')
    if xml['tipo'] not in ('07', '08') and not errores:
        from .cruce import errores_cantidad
        lineas = [(p, xml['lineas'][i]['cantidad'] or 1) for i, p in productos.items() if p is not None]
        errores += errores_cantidad(oc, lineas, ingresar)
        from .solped import exige_conformidad
        if exige_conformidad(oc) and not oc.conformidades.filter(estado='LIBERADA').exists():
            errores.append(f'La orden {oc.numero} es de servicios: registre la conformidad del servicio (HES) y que '
                           f'se libere antes de registrar la factura.')
    return errores


def analizar(datos, nombre='factura.xml'):
    try:
        xml = leer(datos, nombre)
    except ErrorXML as exc:
        raise ErrorImportacion(str(exc))
    empresa = Empresa.actual()
    if xml['ruc_receptor'] and empresa.ruc and xml['ruc_receptor'] != empresa.ruc:
        raise ErrorImportacion(f'La factura está emitida al RUC {xml["ruc_receptor"]}, no a {empresa.ruc} '
                               f'({empresa.razon_social}).')
    if not xml['ruc_emisor']:
        raise ErrorImportacion('El XML no tiene el RUC del proveedor.')
    proveedor = Tercero.objects.filter(tipo_doc='6', numero_doc=xml['ruc_emisor']).first()
    tipo = xml['tipo'] if xml['tipo'] in dict(Compra._meta.get_field('tipo_comprobante').choices) else '01'
    if proveedor and Compra.objects.filter(tercero=proveedor, tipo_comprobante=tipo, serie=xml['serie'],
                                           numero=xml['numero']).exists():
        raise ErrorImportacion(f'La compra {xml["serie"]}-{xml["numero"]} de {proveedor.nombre} ya está registrada.')
    for l in xml['lineas']:
        l['producto'], l['criterio'] = _producto_sugerido(l, proveedor)
    referencia = None
    if xml['referencia'] and proveedor and '-' in xml['referencia']:
        serie, numero = xml['referencia'].split('-', 1)
        referencia = Compra.objects.filter(tercero=proveedor, serie=serie.strip().upper(),
                                           numero__in=[numero.strip(), numero.strip().lstrip('0')]).first()
    return {**xml, 'tipo': tipo, 'proveedor': proveedor, 'doc_referencia': referencia}


@transaction.atomic
def registrar(xml, productos, opciones, usuario=None, nombre_archivo='factura.xml'):
    """Crea la compra. productos: {índice de línea: Producto o None}; opciones: clasificacion, forma_pago,
    ingresar_almacen, almacen, centro_costo, cuenta_contable, tipo_cambio, orden_compra."""
    from core.sustentos import guardar_archivo, vincular
    from core.tipo_cambio import venta_del_dia
    proveedor = xml['proveedor']
    if proveedor is None:
        proveedor = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc=xml['ruc_emisor'],
                                           nombre=(xml['nombre_emisor'] or xml['ruc_emisor'])[:200])
    elif proveedor.tipo == 'CLIENTE':
        proveedor.tipo = 'AMBOS'
        proveedor.save(update_fields=['tipo'])
    if xml['tipo'] in ('07', '08') and not xml.get('doc_referencia'):
        raise ErrorImportacion(f'Registre primero la factura {xml["referencia"] or "de referencia"} que modifica '
                               'la nota.')
    tc = opciones.get('tipo_cambio') or (venta_del_dia(xml['fecha']) if xml['moneda'] == 'USD' else Decimal('1'))
    afectaciones = {l['afectacion'] for l in xml['lineas'] if l['afectacion']}
    cabecera = afectaciones.pop() if len(afectaciones) == 1 else 'GRAVADA'
    mueve = opciones.get('ingresar_almacen', False) and any(
        p and p.es_inventariable for p in productos.values())
    oc = opciones.get('orden_compra')  # v1.117.0: la factura del XML conectada a su orden de compra
    if oc is not None:
        from inventario.servicios import tiene_recepciones
        if tiene_recepciones(orden=oc):
            mueve = False  # la mercadería ya entró con la recepción de Inventario: no se ingresa dos veces
        errores = validar_orden({**xml, 'proveedor': proveedor}, oc, productos, mueve)
        if errores:
            raise ErrorImportacion(' '.join(errores))
        if not opciones.get('centro_costo'):
            opciones = {**opciones, 'centro_costo': oc.centro_costo}
    else:
        from core.models import Empresa
        if Empresa.actual().exigir_orden_compra and opciones.get('clasificacion') in Compra.DE_ALMACEN \
                and xml['tipo'] not in ('07', '08'):
            raise ErrorImportacion('Las compras de bienes de almacén se registran desde una orden de compra (Ajustes › '
                                   'Empresa › Exigir orden de compra): elija la orden de compra.')
    c = Compra(tipo_comprobante=xml['tipo'], serie=xml['serie'], numero=xml['numero'], tercero=proveedor,
               fecha_emision=xml['fecha'], moneda=xml['moneda'] if xml['moneda'] in ('PEN', 'USD') else 'PEN',
               tipo_cambio=tc, tipo_operacion=cabecera if cabecera in ('GRAVADA', 'EXONERADA', 'INAFECTA') else 'GRAVADA',
               clasificacion=opciones.get('clasificacion') or 'MERCADERIA',
               forma_pago=opciones.get('forma_pago') or 'CONTADO', ingresar_almacen=mueve,
               almacen=opciones.get('almacen') or Almacen.principal(), centro_costo=opciones.get('centro_costo'),
               cuenta_contable=opciones.get('cuenta_contable'), doc_referencia=xml.get('doc_referencia'),
               orden_compra=oc,
               glosa=(f'Importada del XML {nombre_archivo}' + (f' · OC {oc.numero}' if oc else ''))[:250])
    if xml['vencimiento']:
        from datetime import date
        try:
            c.fecha_vencimiento = date.fromisoformat(xml['vencimiento'])
        except ValueError:
            pass
    c.save()
    for i, l in enumerate(xml['lineas']):
        afectacion = l['afectacion'] if l['afectacion'] and l['afectacion'] != c.tipo_operacion else ''
        CompraItem.objects.create(documento=c, producto=productos.get(i), descripcion=l['descripcion'][:250] or '-',
                                  cantidad=l['cantidad'] or 1, precio_unitario=l['precio'], afectacion=afectacion)
    try:  # se registra el total del XML; el redondeo del proveedor (dentro de la tolerancia) va a su cuenta
        from .tolerancias import permitida
        c.cuadrar_con(xml['total'], max(permitida(proveedor, 'total', xml['total']), TOLERANCIA))
    except ValueError:
        c.calcular_totales()
        raise ErrorImportacion(f'El total calculado ({c.total:,.2f}) no cuadra con el del XML ({xml["total"]:,.2f}): '
                               'revise percepciones, ICBPER u otros cargos y regístrela manualmente.')
    c.save()
    from .views import compras_views
    compras_views.al_guardar(c)
    archivo = guardar_archivo(None, usuario, datos=xml['xml'].encode('utf-8'), nombre=nombre_archivo, tipo='text/xml')
    vincular(c, archivo, usuario, 'XML de la factura electrónica')
    return c
