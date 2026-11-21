"""Carga masiva de XML de comprobantes electrónicos de compra (v1.115.7): trae los PRODUCTOS (líneas del XML).

La propuesta del SIRE solo trae totales por comprobante; los productos están en el XML que firma el proveedor. Con un
ZIP (o varios archivos) de XML:
- si la compra ya está registrada (p. ej. creada desde la propuesta SIRE) y no tiene productos, se le agregan las
  líneas del XML SIN cambiar sus totales (si el total del XML no cuadra con el registrado, se avisa);
- si ya tiene productos, no se toca;
- si no está registrada, se registra completa con sus productos (como «Registrar desde XML»).
Cada línea se relaciona con un producto del ERP por el código del proveedor, compras anteriores o el nombre; las que
no se reconocen quedan con su descripción para asignarles el producto después. No mueve almacén ni toca SUNAT.
"""
import io
import zipfile
from decimal import Decimal

from django.db import transaction

from core.models import Tercero
from proveedores.xml_ubl import ErrorXML, leer

from . import importar_xml
from .models import Compra, CompraItem

MAX_ARCHIVOS = 500
TOLERANCIA = Decimal('1.00')


def archivos_xml(subidos):
    """[(nombre, bytes)] de los XML: archivos .xml sueltos y los que vienen dentro de ZIP (incluso un ZIP de ZIP,
    como los descarga SUNAT: un ZIP por comprobante). Omite las constancias de SUNAT (R-*.xml)."""
    salida = []

    def agregar(nombre, datos, nivel=0):
        base = nombre.replace('\\', '/').rsplit('/', 1)[-1]
        if base.upper().startswith('R-'):
            return
        if base.lower().endswith('.zip') or datos[:2] == b'PK':
            if nivel > 1:
                return
            try:
                with zipfile.ZipFile(io.BytesIO(datos)) as z:
                    for n in z.namelist():
                        if not n.endswith('/'):
                            agregar(n, z.read(n), nivel + 1)
            except zipfile.BadZipFile:
                salida.append((base, b''))
        elif base.lower().endswith('.xml'):
            salida.append((base, datos))

    for f in subidos:
        agregar(f.name, f.read())
    return salida[:MAX_ARCHIVOS]


def _compra_registrada(xml):
    proveedor = Tercero.objects.filter(tipo_doc='6', numero_doc=xml['ruc_emisor']).first()
    if proveedor is None:
        return None, None
    numero = str(xml['numero'])
    compra = (Compra.objects.filter(tercero=proveedor, tipo_comprobante=xml['tipo'], serie=xml['serie'],
                                    numero__in={numero, numero.lstrip('0') or '0', numero.zfill(8)})
              .exclude(estado='ANULADO').first())
    return proveedor, compra


@transaction.atomic
def completar_productos(compra, xml):
    """Agrega a la compra las líneas del XML con su producto sugerido, sin recalcular los totales registrados."""
    sin_producto = 0
    for l in xml['lineas']:
        producto, _ = importar_xml._producto_sugerido(l, compra.tercero)
        sin_producto += producto is None
        afectacion = l['afectacion'] if l['afectacion'] and l['afectacion'] != compra.tipo_operacion else ''
        item = CompraItem(documento=compra, producto=producto, descripcion=(l['descripcion'] or '-')[:250],
                          cantidad=l['cantidad'] or 1, precio_unitario=l['precio'], afectacion=afectacion)
        CompraItem.objects.bulk_create([item])  # sin save(): los totales de la compra no cambian
    from core.auditoria import registrar
    registrar('MODIFICAR', compra, {'Productos': ['', f'{len(xml["lineas"])} línea(s) del XML']},
              'Productos completados desde el XML del comprobante')
    return sin_producto


def procesar(subidos, opciones, usuario=None):
    """Procesa los XML. Devuelve {'filas': [...], 'resumen': {...}}; cada fila: archivo, comprobante, proveedor,
    accion ('completada' | 'creada' | 'ya_tenia' | 'error'), lineas, sin_producto, aviso."""
    from core.sustentos import guardar_archivo, vincular
    filas = []
    for nombre, datos in archivos_xml(subidos):
        fila = {'archivo': nombre, 'comprobante': '', 'proveedor': '', 'accion': 'error', 'lineas': 0,
                'sin_producto': 0, 'aviso': '', 'compra': None}
        filas.append(fila)
        try:
            xml = leer(datos, nombre)
        except ErrorXML as exc:
            fila['aviso'] = str(exc)
            continue
        fila['comprobante'] = f'{xml["tipo"]} {xml["serie"]}-{xml["numero"]}'
        fila['proveedor'] = f'{xml["ruc_emisor"]} {xml["nombre_emisor"]}'.strip()
        fila['lineas'] = len(xml['lineas'])
        proveedor, compra = _compra_registrada(xml)
        try:
            if compra is not None:
                fila['compra'] = compra
                if compra.items.exists():
                    fila['accion'], fila['aviso'] = 'ya_tenia', 'La compra ya tiene productos: no se tocó.'
                    continue
                fila['sin_producto'] = completar_productos(compra, xml)
                fila['accion'] = 'completada'
                archivo = guardar_archivo(None, usuario, datos=xml['xml'].encode('utf-8'), nombre=nombre,
                                          tipo='text/xml')
                vincular(compra, archivo, usuario, 'XML de la factura electrónica')
                if abs((xml['total'] or 0) - compra.total) > TOLERANCIA:
                    fila['aviso'] = (f'El total del XML ({xml["total"]:,.2f}) no cuadra con el registrado '
                                     f'({compra.total:,.2f}): revise la compra.')
                continue
            analisis = importar_xml.analizar(datos, nombre)
            # v1.117.0: si el XML cita una orden de compra aprobada del proveedor, la factura se conecta a ella
            oc, motivo = importar_xml.orden_sugerida(analisis, analisis['proveedor'])
            oc = oc if motivo == 'citada en el XML' else None
            importar_xml.aplicar_orden(analisis, oc)
            productos = {i: l['producto'] for i, l in enumerate(analisis['lineas'])}
            base = {**opciones, 'ingresar_almacen': False}
            try:
                compra = importar_xml.registrar(analisis, productos, {**base, 'orden_compra': oc}, usuario, nombre)
            except importar_xml.ErrorImportacion as exc:
                if oc is None:
                    raise
                compra = importar_xml.registrar(analisis, productos, base, usuario, nombre)
                fila['aviso'] = f'No se conectó a la orden {oc.numero}: {exc}'
            fila['compra'], fila['accion'] = compra, 'creada'
            fila['sin_producto'] = sum(1 for p in productos.values() if p is None)
        except importar_xml.ErrorImportacion as exc:
            fila['aviso'] = str(exc)
        except Exception as exc:  # un XML con datos que el ERP no acepta: se informa y se sigue con los demás
            fila['aviso'] = f'No se pudo procesar: {exc}'
    resumen = {k: sum(1 for f in filas if f['accion'] == k) for k in ('completada', 'creada', 'ya_tenia', 'error')}
    resumen['sin_producto'] = sum(f['sin_producto'] for f in filas)
    resumen['total'] = len(filas)
    return {'filas': filas, 'resumen': resumen}
