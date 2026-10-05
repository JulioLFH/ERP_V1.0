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
    ingresar_almacen, almacen, centro_costo, cuenta_contable, tipo_cambio."""
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
    c = Compra(tipo_comprobante=xml['tipo'], serie=xml['serie'], numero=xml['numero'], tercero=proveedor,
               fecha_emision=xml['fecha'], moneda=xml['moneda'] if xml['moneda'] in ('PEN', 'USD') else 'PEN',
               tipo_cambio=tc, tipo_operacion=cabecera if cabecera in ('GRAVADA', 'EXONERADA', 'INAFECTA') else 'GRAVADA',
               clasificacion=opciones.get('clasificacion') or 'MERCADERIA',
               forma_pago=opciones.get('forma_pago') or 'CONTADO', ingresar_almacen=mueve,
               almacen=opciones.get('almacen') or Almacen.principal(), centro_costo=opciones.get('centro_costo'),
               cuenta_contable=opciones.get('cuenta_contable'), doc_referencia=xml.get('doc_referencia'),
               glosa=f'Importada del XML {nombre_archivo}'[:250])
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
    c.calcular_totales()
    c.save()
    if abs(c.total - xml['total']) > TOLERANCIA:
        raise ErrorImportacion(f'El total calculado ({c.total:,.2f}) no cuadra con el del XML ({xml["total"]:,.2f}): '
                               'revise percepciones, ICBPER u otros cargos y regístrela manualmente.')
    from .views import compras_views
    compras_views.al_guardar(c)
    archivo = guardar_archivo(None, usuario, datos=xml['xml'].encode('utf-8'), nombre=nombre_archivo, tipo='text/xml')
    vincular(c, archivo, usuario, 'XML de la factura electrónica')
    return c
