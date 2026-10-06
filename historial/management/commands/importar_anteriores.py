"""Importa lo que falta del sistema anterior (Odoo) desde sus Excel, en bloques.

    python manage.py importar_anteriores "<carpeta>" [--solo oc,pedidos,fabricacion,kardex,contable,posiciones]
                                         [--rehacer] [--reporte anteriores.xlsx]

- Órdenes de compra -> Compras (documentos del sistema anterior; las que siguen abiertas quedan aprobadas por lo
  pendiente de recibir). Los proveedores se buscan por nombre (y su RUC en el Excel de contactos).
- Pedidos y cotizaciones de venta -> Ventas (de PRODUCTORA DE ALIMENTOS UNO; se omite la otra empresa).
- Órdenes de fabricación con sus consumos y costos, kardex y asientos contables -> Historial (solo consulta: no
  cambian el stock ni la contabilidad de Ceiba, que parten de los saldos iniciales).
- Posiciones presupuestarias -> Historial (agrupación de cuentas).

Es idempotente: lo ya importado se omite (con --rehacer se borra lo importado de esos pasos y se vuelve a cargar).
Graba en bloques, así que también puede ejecutarse contra la base de la nube.
"""
import os
import re
import time
import unicodedata
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max, Sum

from core.management.commands.migrar_excels import _dec, _fecha, _txt

D0 = Decimal('0')
IGV = Decimal('0.18')
MARCA = '[Sistema anterior]'
EMPRESA = 'PRODUCTORA DE ALIMENTOS UNO'
PASOS = ['posiciones', 'oc', 'pedidos', 'fabricacion', 'kardex', 'contable', 'compras', 'por_pagar', 'bancos']
CUENTAS_POR_PAGAR = r'^(4212|424)'  # facturas emitidas y honorarios (las 4211 son provisiones sin comprobante)
CUENTAS_PUENTE = {'1041002', '1041003', '1041004', '10300010', '1051001'}  # transitorias: no son cuentas de dinero
BANCOS = [('BBVA', 'BBVA'), ('BCP', 'BCP'), ('INTERBANK', 'INTERBANK'), ('SCOTIABANK', 'SCOTIABANK'),
          ('NACION', 'BN'), ('NACIÓN', 'BN')]
DIARIOS_COMPRA = ['Facturas de proveedores', 'Facturas de proveedores servicios', 'Recibos por Honorarios']
TIPOS_COMPRA = {'01', '02', '03', '07', '08', '12', '14'}
ARCHIVOS = {
    'posiciones': ['Data_Posiciones_Presupuestarias.xlsx'],
    'oc': ['Data_OrdenCompra_Scraping_2026.xlsx', 'Data_Contactos_API.xlsx'],
    'pedidos': ['Data_OrdenVenta_API_2026.xlsx'],
    'fabricacion': ['Data_Orden_de_Fabricación_Scraping_2026.xlsx', 'Reporte de Producción (report.simple.mrp).xlsx'],
    'kardex': ['Data_Kardex_API_2025.xlsx', 'Data_Kardex_API_2026.xlsx'],
    'contable': ['Data_Contable_2026.xlsx'],
    'compras': [],  # se arma desde los asientos ya importados (paso contable)
    'por_pagar': [],
    'bancos': [],
}
MESES = {m: i for i, m in enumerate(['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
                                     'septiembre', 'octubre', 'noviembre', 'diciembre'], 1)}
MESES['setiembre'] = 9
SOCIEDADES = [('SOCIEDAD ANONIMA CERRADA', 'SAC'), ('SOCIEDAD COMERCIAL DE RESPONSABILIDAD LIMITADA', 'SRL'),
              ('EMPRESA INDIVIDUAL DE RESPONSABILIDAD LIMITADA', 'EIRL'), ('SOCIEDAD ANONIMA ABIERTA', 'SAA'),
              ('SOCIEDAD ANONIMA', 'SA'), ('S A C', 'SAC'), ('S R L', 'SRL'), ('E I R L', 'EIRL'), ('S A A', 'SAA'),
              ('S A', 'SA')]
LOTE = 5000


def nombre_clave(texto):
    """Razón social comparable: sin tildes, puntos ni formas societarias escritas de distinta manera."""
    t = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().upper()
    t = t.split(',')[0]  # 'EMPRESA S.A.C., contacto' -> 'EMPRESA S.A.C.'
    t = ' ' + ' '.join(re.sub(r'[.\-\'"&]', ' ', t).split()) + ' '
    for largo, corto in SOCIEDADES:
        t = t.replace(f' {largo} ', f' {corto} ')
    return ' '.join(t.split())


def fecha_flexible(v):
    """Fechas como datetime, '31/03/2026 23:42:22', '2026-03-31' o 'martes, 4 de noviembre de 2025'."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    texto = _txt(v)
    m = re.search(r'(\d{1,2}) de (\w+) de (\d{4})', texto.lower())
    if m and m.group(2) in MESES:
        return date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
    return _fecha(texto[:10])


def fecha_hora(v):
    """Fecha y hora con la zona horaria del sistema (las del archivo son hora de Lima)."""
    from django.utils import timezone
    if not isinstance(v, datetime):
        texto, v = _txt(v), None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%Y-%m-%d'):
            try:
                v = datetime.strptime(texto[:19], fmt)
                break
            except ValueError:
                pass
    if v is None:
        return None
    return timezone.make_aware(v) if timezone.is_naive(v) else v


def codigo_de(texto):
    """'[E001516] CAJA CORRUGADA' -> ('E001516', 'CAJA CORRUGADA')."""
    texto = _txt(texto)
    m = re.match(r'\[([^\]]+)\]\s*(.*)', texto)
    return (m.group(1).strip().upper(), m.group(2).strip()) if m else ('', texto)


class Command(BaseCommand):
    help = 'Importa órdenes de compra, pedidos, fabricación, kardex y asientos del sistema anterior.'

    def add_arguments(self, parser):
        parser.add_argument('carpeta')
        parser.add_argument('--solo', default=','.join(PASOS))
        parser.add_argument('--rehacer', action='store_true')
        parser.add_argument('--reporte', default='anteriores.xlsx')
        parser.add_argument('--dias-abiertas', type=int, default=120,
                            help='Órdenes de compra con saldo por recibir más antiguas se importan cerradas')

    def handle(self, carpeta, solo, rehacer, reporte, dias_abiertas, **_):
        self.dias_abiertas = dias_abiertas
        pasos = [p.strip() for p in solo.split(',') if p.strip()]
        desconocidos = set(pasos) - set(PASOS)
        if desconocidos:
            raise CommandError(f'Pasos desconocidos: {desconocidos}. Use {",".join(PASOS)}')
        for p in pasos:
            for archivo in ARCHIVOS[p]:
                if not os.path.exists(os.path.join(carpeta, archivo)):
                    raise CommandError(f'No existe {archivo} en {carpeta}')
        self.carpeta, self.rehacer, self.inicio = carpeta, rehacer, time.time()
        self.obs, self.resumen = [], OrderedDict()
        from core.models import Producto
        self.productos = dict(Producto.objects.values_list('codigo', 'pk'))
        for p in PASOS:
            if p in pasos:
                with transaction.atomic():
                    getattr(self, f'paso_{p}')()
        self._reporte(reporte)
        for k, v in self.resumen.items():
            self.stdout.write(f'  {k}: {v}')
        self.stdout.write(self.style.SUCCESS(f'Listo en {time.time() - self.inicio:.0f} s. Observaciones: '
                                             f'{os.path.abspath(reporte)}'))

    # ---------------------------------------------------------------- utilidades
    def log(self, texto):
        self.stdout.write(f'[{time.time() - self.inicio:6.0f}s] {texto}')

    def filas(self, nombre, columnas=None):
        from openpyxl import load_workbook
        wb = load_workbook(os.path.join(self.carpeta, nombre), read_only=True, data_only=True)
        it = wb.worksheets[0].iter_rows(values_only=True)
        enc = [_txt(c) for c in next(it)]
        if columnas:
            faltan = [c for c in columnas if c not in enc]
            if faltan:
                raise CommandError(f'{nombre}: faltan las columnas {faltan}')
            ix = [enc.index(c) for c in columnas]
            for f in it:
                yield dict(zip(columnas, (f[i] if i < len(f) else None for i in ix)))
        else:
            for f in it:
                yield dict(zip(enc, f))
        wb.close()

    def observar(self, paso, referencia, texto):
        self.obs.append((paso, referencia, texto))

    def _bulk(self, modelo, objetos, ignorar=False):
        for i in range(0, len(objetos), LOTE):
            modelo.objects.bulk_create(objetos[i:i + LOTE], ignore_conflicts=ignorar)

    # ---------------------------------------------------------------- posiciones presupuestarias
    def paso_posiciones(self):
        from historial.models import PosicionPresupuestaria
        if self.rehacer:
            PosicionPresupuestaria.objects.all().delete()
        objetos = [PosicionPresupuestaria(nombre=_txt(r['Nombre de la Posición Presupuestaria'])[:100],
                                          cuenta=_txt(r['Código'])[:12], cuenta_nombre=_txt(r['Nombre de la Cuenta'])[:200])
                   for r in self.filas('Data_Posiciones_Presupuestarias.xlsx') if _txt(r['Código'])]
        antes = PosicionPresupuestaria.objects.count()
        self._bulk(PosicionPresupuestaria, objetos, ignorar=True)
        self.resumen['Posiciones presupuestarias'] = PosicionPresupuestaria.objects.count() - antes
        self.log(f'Posiciones presupuestarias: {self.resumen["Posiciones presupuestarias"]}')

    # ---------------------------------------------------------------- órdenes de compra
    def _proveedores(self, nombres):
        """{nombre del Excel: tercero_id}; crea con su RUC (del Excel de contactos) a los que no existen."""
        from core.models import Tercero
        por_clave = {}
        for pk, nombre, tipo in Tercero.objects.values_list('pk', 'nombre', 'tipo'):
            por_clave.setdefault(nombre_clave(nombre), pk)
        contactos = {}
        for r in self.filas('Data_Contactos_API.xlsx', ['nombre_completo', 'nombre', 'tipo_documento',
                                                       'numero_documento', 'direccion_completa']):
            doc = re.sub(r'\s', '', _txt(r['numero_documento']))
            if doc:
                for n in (r['nombre_completo'], r['nombre']):
                    contactos.setdefault(nombre_clave(n), (doc, _txt(r['tipo_documento']), _txt(r['nombre_completo']),
                                                           _txt(r['direccion_completa'])))
        resultado, creados = {}, 0
        por_doc = dict(Tercero.objects.values_list('numero_doc', 'pk'))
        for nombre in nombres:
            clave = nombre_clave(nombre)
            if clave in por_clave:
                resultado[nombre] = por_clave[clave]
                continue
            if clave in contactos:
                doc, tipo_doc, completo, direccion = contactos[clave]
                if doc in por_doc:
                    resultado[nombre] = por_doc[doc]
                    continue
                t = Tercero.objects.create(
                    tipo='PROVEEDOR', tipo_doc={'RUC': '6', 'DNI': '1'}.get(tipo_doc, '0'), numero_doc=doc[:15],
                    nombre=(completo or nombre)[:200], direccion=direccion[:250])
                por_doc[t.numero_doc] = por_clave[clave] = resultado[nombre] = t.pk
                creados += 1
                continue
            self.observar('Órdenes de compra', nombre, 'Proveedor no encontrado (ni por nombre ni en contactos): '
                                                       'sus órdenes no se importaron')
        Tercero.objects.filter(pk__in=resultado.values(), tipo='CLIENTE').update(tipo='AMBOS')
        return resultado, creados

    def paso_oc(self):
        from compras.models import OrdenCompra, OrdenCompraItem
        from contabilidad.models import CentroCosto
        if self.rehacer:
            OrdenCompra.objects.filter(glosa__startswith=MARCA).delete()
        existentes = set(OrdenCompra.objects.values_list('numero', flat=True))
        ordenes = OrderedDict()
        for r in self.filas('Data_OrdenCompra_Scraping_2026.xlsx'):
            ref = _txt(r['Referencia de la Orden'])
            if ref:
                ordenes.setdefault(ref, []).append(r)
        self.log(f'Órdenes de compra en el archivo: {len(ordenes)}')
        proveedores, creados = self._proveedores({_txt(l[0]['Proveedor']) for l in ordenes.values()})
        centros = dict(CentroCosto.objects.values_list('codigo', 'pk'))
        cabeceras, detalle, omitidas = [], [], 0
        for ref, lineas in ordenes.items():
            r = lineas[0]
            if ref in existentes:
                omitidas += 1
                continue
            tercero = proveedores.get(_txt(r['Proveedor']))
            if tercero is None:
                continue
            moneda = _txt(r['Moneda'])
            if moneda not in ('PEN', 'USD'):
                self.observar('Órdenes de compra', ref, f'Moneda {moneda}: no se importó')
                continue
            fecha = fecha_flexible(r['Fecha de Confirmación/Fecha Creación']) or date.today()
            estado_odoo, abierta = _txt(r['Estado']), False
            if estado_odoo == 'Solicitud de cotización':
                estado = 'ANULADO'
            elif (estado_odoo == 'Orden de compra' and (date.today() - fecha).days <= self.dias_abiertas
                  and any(_dec(l['Pendiente por recibir']) > 0 for l in lineas)):
                estado, abierta = 'APROBADO', True  # reciente con saldo por recibir: sigue abierta en Ceiba
            else:
                estado = 'ATENDIDO'
                if any(_dec(l['Pendiente por recibir']) > 0 for l in lineas) and estado_odoo == 'Orden de compra':
                    self.observar('Órdenes de compra', ref, f'Del {fecha:%d/%m/%Y} con saldo por recibir: se cerró '
                                                            f'(más de {self.dias_abiertas} días)')
            items, base, inafecto = [], D0, D0
            for l in lineas:
                ordenada, pendiente = _dec(l['Cantidad']), _dec(l['Pendiente por recibir'])
                cantidad = pendiente if abierta else ordenada
                if cantidad <= 0:
                    continue
                codigo, nombre = codigo_de(l['Producto'])
                codigo = _txt(l['Codigo Material']).upper() or codigo
                precio = _dec(l['Precio Unitario'])
                sub, total = _dec(l['Sub Total sin igv']), _dec(l['Total'])
                gravado = total > sub + Decimal('0.01')
                descripcion = nombre or codigo or 'Sin descripción'
                if abierta and pendiente != ordenada:
                    descripcion = f'{descripcion} (pendiente de {ordenada.normalize():f}; recibido ' \
                                  f'{_dec(l["Cantidad Recibida"]).normalize():f})'
                subtotal = (cantidad * precio).quantize(Decimal('0.01'))
                if gravado:
                    base += subtotal
                else:
                    inafecto += subtotal
                items.append(dict(producto_id=self.productos.get(codigo), descripcion=descripcion[:250],
                                  cantidad=cantidad, precio_unitario=precio.quantize(Decimal('0.0001')),
                                  subtotal=subtotal, afectacion='' if gravado else 'INAFECTA'))
            if not items:
                continue
            igv = (base * IGV).quantize(Decimal('0.01'))
            cc = re.match(r'\[(\d+)\]', _txt(r['Distribución Analítica']))
            dias = re.match(r'(\d+)', _txt(r['Condición de Pago']))
            glosa = (f'{MARCA} Comprador: {_txt(r["Comprador"])} · {_txt(r["Tipo de Compra"])} · origen '
                     f'{_txt(r["Documento Origen"])} · almacén {_txt(r["Almacen de Entrega"])} · entrega '
                     f'{_txt(r["Estado de entrega"])} · facturación {_txt(r["Estado de Facturación"])}')
            cabeceras.append(OrdenCompra(
                numero=ref[:20], fecha=fecha, tercero_id=tercero, estado=estado, moneda=moneda,
                tipo_cambio=Decimal('1'), tipo_operacion='GRAVADA', base_imponible=base, inafecto=inafecto,
                no_gravado=inafecto, igv=igv, total=base + igv + inafecto,
                condicion_pago=_txt(r['Condición de Pago'])[:100], glosa=glosa,
                fecha_entrega=fecha_flexible(r['Entrega esperada']),
                centro_costo_id=centros.get(cc.group(1)) if cc else None,
                dias_credito=int(dias.group(1)) if dias else 0))
            detalle.append(items)
        self._documentos(OrdenCompra, OrdenCompraItem, cabeceras, detalle)
        self.resumen['Órdenes de compra'] = (f'{len(cabeceras)} importadas ({sum(1 for c in cabeceras if c.estado == "APROBADO")} '
                                             f'abiertas por lo pendiente de recibir); {omitidas} ya estaban; '
                                             f'{creados} proveedores creados con su RUC')
        self.log(f'Órdenes de compra: {self.resumen["Órdenes de compra"]}')

    def _documentos(self, Modelo, ModeloItem, cabeceras, detalle):
        """Graba cabeceras e ítems en bloques (sin señales: no son operaciones nuevas)."""
        for i in range(0, len(cabeceras), 1000):
            bloque = cabeceras[i:i + 1000]
            Modelo.objects.bulk_create(bloque)
            if bloque[0].pk is None:  # bases sin RETURNING: se recuperan por número
                ids = dict(Modelo.objects.filter(numero__in=[c.numero for c in bloque]).values_list('numero', 'pk'))
                for c in bloque:
                    c.pk = ids[c.numero]
            items = [ModeloItem(documento_id=c.pk, **it) for c, its in zip(bloque, detalle[i:i + 1000]) for it in its]
            self._bulk(ModeloItem, items)

    # ---------------------------------------------------------------- pedidos de venta
    def paso_pedidos(self):
        from core.models import Tercero
        from ventas.models import Cotizacion, CotizacionItem
        if self.rehacer:
            Cotizacion.objects.filter(glosa__startswith=MARCA).delete()
        existentes = set(Cotizacion.objects.values_list('numero', flat=True))
        pedidos = OrderedDict()
        otra_empresa = 0
        for r in self.filas('Data_OrdenVenta_API_2026.xlsx'):
            if EMPRESA not in _txt(r['compañia']).upper():
                otra_empresa += 1
                continue
            pedidos.setdefault(_txt(r['referencia']), []).append(r)
        self.log(f'Pedidos y cotizaciones en el archivo: {len(pedidos)}')
        por_doc = dict(Tercero.objects.values_list('numero_doc', 'pk'))
        cabeceras, detalle, omitidos = [], [], 0
        for ref, lineas in pedidos.items():
            r = lineas[0]
            if not ref or ref in existentes:
                omitidos += 1
                continue
            tercero = por_doc.get(_txt(r['nro_documento_cliente']))
            if tercero is None:
                self.observar('Pedidos de venta', ref, f'Cliente {_txt(r["nro_documento_cliente"])} no existe')
                continue
            estado_odoo = _txt(r['estado'])
            tipo = 'COT' if estado_odoo == 'Cotización' else 'PED'
            estado = {'Orden de venta': 'ATENDIDO', 'Cancelada': 'ANULADO'}.get(estado_odoo, 'ANULADO')
            items, base, gratuito = [], D0, False
            for l in lineas:
                cantidad = _dec(l['cantidad'])
                if cantidad <= 0:
                    continue
                precio, desc = _dec(l['precio_unitario']), _dec(l['descuento'])
                descripcion = (_txt(l['producto']) or _txt(l['cod_producto']) or 'Sin descripción')[:250]
                if _txt(l['impuestos']) in ('IGV-TRG', 'TRF-GRAT'):
                    descripcion = f'{descripcion} (bonificación, valor ref. {precio})'[:250]
                    precio, gratuito = D0, True
                subtotal = (cantidad * precio * (1 - desc / 100)).quantize(Decimal('0.01'))
                base += subtotal
                items.append(dict(producto_id=self.productos.get(_txt(l['cod_producto']).upper()),
                                  descripcion=descripcion, cantidad=cantidad,
                                  precio_unitario=precio.quantize(Decimal('0.0001')), descuento_pct=desc,
                                  subtotal=subtotal))
            if not items:
                continue
            igv = (base * IGV).quantize(Decimal('0.01'))
            glosa = (f'{MARCA} {estado_odoo} · lista {_txt(r["lista_de_precio"])} · entrega '
                     f'{_txt(r["direccion_de_entrega"])[:80]}' + (' · con bonificaciones' if gratuito else ''))
            cabeceras.append(Cotizacion(
                numero=ref[:20], tipo=tipo, fecha=fecha_flexible(r['fecha_orden']) or date.today(), tercero_id=tercero,
                estado=estado, vendedor=_txt(r['vendedor'])[:80], moneda='USD' if _txt(r['moneda']) == 'USD' else 'PEN',
                tipo_cambio=_dec(r['tipo_de_cambio']) or Decimal('1'), tipo_operacion='GRAVADA', base_imponible=base,
                igv=igv, total=base + igv, condicion_pago=_txt(r['termino_de_pago'])[:100], glosa=glosa))
            detalle.append(items)
        self._documentos(Cotizacion, CotizacionItem, cabeceras, detalle)
        self.resumen['Pedidos y cotizaciones de venta'] = (f'{len(cabeceras)} importados; {omitidos} ya estaban; '
                                                           f'{otra_empresa} líneas de otra empresa omitidas')
        self.log(f'Pedidos: {self.resumen["Pedidos y cotizaciones de venta"]}')

    # ---------------------------------------------------------------- fabricación
    def paso_fabricacion(self):
        from historial.models import ConsumoAnterior, OrdenFabricacionAnterior
        if self.rehacer:
            OrdenFabricacionAnterior.objects.all().delete()
        existentes = set(OrdenFabricacionAnterior.objects.values_list('referencia', flat=True))
        ordenes = OrderedDict()
        for r in self.filas('Data_Orden_de_Fabricación_Scraping_2026.xlsx'):
            ref = _txt(r['Referencia'])
            if not ref or ref in existentes:
                continue
            codigo, nombre = codigo_de(r['Producto'])
            ordenes[ref] = OrdenFabricacionAnterior(
                referencia=ref[:40], producto_id=self.productos.get(codigo), codigo=codigo[:30],
                descripcion=nombre[:200], lista_materiales=_txt(r['Lista de materiales'])[:200],
                lote=_txt(r['Número de serie/lote'])[:60], inicio=fecha_hora(r['Fecha de Inicio de Fabricación']),
                fin=fecha_hora(r['Fecha de Final de Fabricación']), fecha_kardex=fecha_hora(r['Fecha Kardex']),
                cantidad=_dec(r['Cantidad a producir']), producida=_dec(r['Cantidad en producción']),
                unidad=_txt(r['Unidad de medida del producto'])[:20], estado=_txt(r['Estado'])[:30],
                responsable=_txt(r['Responsable'])[:80])
        consumos = defaultdict(list)
        for r in self.filas('Reporte de Producción (report.simple.mrp).xlsx'):
            ref = _txt(r['Orden de Fabricación'])
            if not ref or ref in existentes:
                continue
            of = ordenes.get(ref)
            if of is None:
                codigo, nombre = codigo_de(r['Producto Terminado'])
                of = ordenes[ref] = OrdenFabricacionAnterior(
                    referencia=ref[:40], producto_id=self.productos.get(codigo), codigo=codigo[:30],
                    descripcion=nombre[:200], fecha_kardex=fecha_hora(r['Fecha Kardex']),
                    inicio=fecha_hora(r['Fecha Programada']), cantidad=_dec(r['Cantidad Requerida']),
                    producida=_dec(r['Cantidad Producida']), estado=_txt(r['Estado'])[:30],
                    responsable=_txt(r['Responsable'])[:80], lote=_txt(r['Lote/Serie Producida'])[:60])
            of.almacen = _txt(r['Almacén'])[:80]
            of.costo_materiales = _dec(r['Monto Materia Prima']).quantize(Decimal('0.01'))
            of.costo_mano_obra = _dec(r['Monto Mano Obra']).quantize(Decimal('0.01'))
            of.costo_indirecto = _dec(r['Monto Gasto Indirecto']).quantize(Decimal('0.01'))
            if r['Componente']:
                codigo, nombre = codigo_de(r['Componente'])
                consumos[ref].append(dict(producto_id=self.productos.get(codigo), codigo=codigo[:30],
                                          descripcion=nombre[:200], requerida=_dec(r['Cant. Requerida']),
                                          reservada=_dec(r['Cant. Reservada']),
                                          lotes=_txt(r['Lotes reservados'])[:200]))
        lista = list(ordenes.values())
        self._bulk(OrdenFabricacionAnterior, lista)
        ids = dict(OrdenFabricacionAnterior.objects.filter(referencia__in=list(consumos)).values_list('referencia', 'pk'))
        objetos = [ConsumoAnterior(orden_id=ids[ref], **c) for ref, cs in consumos.items() if ref in ids for c in cs]
        self._bulk(ConsumoAnterior, objetos)
        self.resumen['Órdenes de fabricación'] = f'{len(lista)} con {len(objetos)} consumos'
        self.log(f'Órdenes de fabricación: {self.resumen["Órdenes de fabricación"]}')

    # ---------------------------------------------------------------- kardex
    def paso_kardex(self):
        from historial.models import MovimientoAnterior
        if self.rehacer:
            MovimientoAnterior.objects.all().delete()
        columnas = ['id_movimiento_detalle', 'fecha_movimiento', 'almacen', 'transaccion', 'documento_origen',
                    'orden_fabricacion', 'orden_compra', 'guia_de_venta', 'guia_de_compra_o_traslado',
                    'factura/boleta', 'sku', 'sku_nombre', 'udme', 'Lote', 'fecha_de_vencimiento_de_lote',
                    'ingreso', 'salida', 'costo', 'nro_documento', 'contacto', 'usuario']
        vistos = set(MovimientoAnterior.objects.values_list('id_origen', flat=True))
        total = 0
        for archivo in ARCHIVOS['kardex']:
            bloque, n = [], 0
            for r in self.filas(archivo, columnas):
                ident = r['id_movimiento_detalle']
                try:
                    ident = int(ident)
                except (TypeError, ValueError):
                    continue
                if ident in vistos:
                    continue
                vistos.add(ident)
                fecha = fecha_flexible(r['fecha_movimiento'])
                if fecha is None:
                    self.observar('Kardex', ident, f'Fecha ilegible: {r["fecha_movimiento"]}')
                    continue
                codigo = _txt(r['sku']).upper()
                bloque.append(MovimientoAnterior(
                    id_origen=ident, fecha=fecha, almacen=_txt(r['almacen'])[:80],
                    transaccion=_txt(r['transaccion'])[:60], documento=_txt(r['documento_origen'])[:60],
                    orden_fabricacion=_txt(r['orden_fabricacion'])[:40], orden_compra=_txt(r['orden_compra'])[:40],
                    guia=(_txt(r['guia_de_venta']) or _txt(r['guia_de_compra_o_traslado']))[:40],
                    comprobante=_txt(r['factura/boleta'])[:40], producto_id=self.productos.get(codigo),
                    codigo=codigo[:30], descripcion=_txt(r['sku_nombre'])[:200], unidad=_txt(r['udme'])[:20],
                    lote=_txt(r['Lote'])[:60], vencimiento=fecha_flexible(r['fecha_de_vencimiento_de_lote']),
                    ingreso=_dec(r['ingreso']), salida=_dec(r['salida']),
                    costo=_dec(r['costo']).quantize(Decimal('0.000001')), contacto_doc=_txt(r['nro_documento'])[:20],
                    contacto=_txt(r['contacto'])[:200], usuario=_txt(r['usuario'])[:80]))
                if len(bloque) >= LOTE:
                    MovimientoAnterior.objects.bulk_create(bloque)
                    n += len(bloque)
                    bloque = []
                    if n % 50000 == 0:
                        self.log(f'  {archivo}: {n:,} movimientos')
            if bloque:
                MovimientoAnterior.objects.bulk_create(bloque)
                n += len(bloque)
            total += n
            self.log(f'Kardex {archivo}: {n:,}')
        self.resumen['Kardex del sistema anterior'] = f'{total:,} movimientos'

    # ---------------------------------------------------------------- contabilidad
    def paso_contable(self):
        from historial.models import AsientoAnterior
        if self.rehacer:
            AsientoAnterior.objects.all().delete()
        if AsientoAnterior.objects.exists():
            self.resumen['Asientos del sistema anterior'] = 'ya estaban importados (use --rehacer para volver a cargar)'
            return
        columnas = ['diario', 'voucher', 'fecha_contable', 'nro_documento', 'contacto', 'tipo_de_documento',
                    'numero_comprobante', 'referencia', 'glosa', 'etiqueta', 'CtaCont', 'DescCtaCont',
                    'debito_soles', 'credito_soles', 'Centro_costo', 'usuario', 'PeriodoMes']
        bloque, n, debe, haber = [], 0, D0, D0
        for r in self.filas('Data_Contable_2026.xlsx', columnas):
            fecha = fecha_flexible(r['fecha_contable'])
            if fecha is None:
                continue
            periodo_mes = fecha  # la columna PeriodoMes del archivo es el trimestre, no el mes
            glosa = ' · '.join(x for x in (_txt(r['glosa']), _txt(r['etiqueta']), _txt(r['referencia'])) if x)
            d, h = _dec(r['debito_soles']), _dec(r['credito_soles'])
            debe, haber = debe + d, haber + h
            centro = _txt(r['Centro_costo'])
            bloque.append(AsientoAnterior(
                diario=_txt(r['diario'])[:80], voucher=_txt(r['voucher'])[:30], fecha=fecha,
                periodo=periodo_mes.strftime('%Y%m'), contacto_doc=_txt(r['nro_documento'])[:20],
                contacto=_txt(r['contacto'])[:200], tipo_comprobante=_txt(r['tipo_de_documento'])[:60],
                comprobante=_txt(r['numero_comprobante'])[:40], glosa=glosa[:250], cuenta=_txt(r['CtaCont'])[:12],
                cuenta_nombre=_txt(r['DescCtaCont'])[:200], debe=d.quantize(Decimal('0.01')),
                haber=h.quantize(Decimal('0.01')), centro_costo='' if centro == 'SIN_CC' else centro[:80],
                usuario=_txt(r['usuario'])[:80]))
            if len(bloque) >= LOTE:
                AsientoAnterior.objects.bulk_create(bloque)
                n += len(bloque)
                bloque = []
                if n % 50000 == 0:
                    self.log(f'  asientos: {n:,}')
        if bloque:
            AsientoAnterior.objects.bulk_create(bloque)
            n += len(bloque)
        if abs(debe - haber) > Decimal('1'):
            self.observar('Contabilidad', '', f'El archivo no cuadra: debe {debe:,.2f} vs haber {haber:,.2f}')
        self.resumen['Asientos del sistema anterior'] = (f'{n:,} líneas · debe S/ {debe:,.2f} · haber '
                                                         f'S/ {haber:,.2f}')
        self.log(f'Asientos: {self.resumen["Asientos del sistema anterior"]}')

    # ---------------------------------------------------------------- facturas de compra (desde los asientos)
    def paso_compras(self):
        """Facturas, recibos por honorarios y notas de proveedores del sistema anterior, armadas desde sus asientos
        (diarios de proveedores y honorarios): base por cuenta, IGV (4011), retenciones (otras 40) y total. Quedan
        como históricas: se ven en Compras y en los reportes, no van al registro de compras ni a la contabilidad
        de Ceiba y no tienen saldo por pagar."""
        from compras.models import Compra, CompraItem
        from core.models import Tercero
        from historial.models import AsientoAnterior
        if not AsientoAnterior.objects.filter(diario__in=DIARIOS_COMPRA).exists():
            self.resumen['Facturas de compra'] = 'sin asientos de proveedores: importe primero el paso contable'
            return
        if self.rehacer:
            Compra.objects.filter(es_historico=True, es_saldo_inicial=False).delete()
        grupos = OrderedDict()
        for a in AsientoAnterior.objects.filter(diario__in=DIARIOS_COMPRA).order_by('fecha', 'id').iterator(
                chunk_size=10000):
            m = re.match(r'\((\d+)\)', a.tipo_comprobante)
            tipo_origen = m.group(1) if m else '00'
            clave = (a.contacto_doc, tipo_origen, a.comprobante)
            grupos.setdefault(clave, []).append(a)
        self.log(f'Comprobantes de compra en los asientos: {len(grupos)}')
        por_doc = dict(Tercero.objects.values_list('numero_doc', 'pk'))
        existentes = set(Compra.objects.values_list('tercero_id', 'tipo_comprobante', 'serie', 'numero'))
        cabeceras, detalle, creados, omitidos = [], [], 0, 0
        for (doc, tipo_origen, comprobante), lineas in grupos.items():
            if not doc or not comprobante:
                self.observar('Facturas de compra', comprobante or lineas[0].voucher, 'Sin proveedor o sin número')
                continue
            signo = -1 if tipo_origen in ('07', '97') else 1
            por_cuenta, igv, retencion = OrderedDict(), D0, D0
            for a in lineas:
                if a.cuenta.startswith('42'):
                    continue
                if a.cuenta.startswith('4011'):
                    igv += (a.debe - a.haber) * signo
                elif a.cuenta.startswith('40'):
                    retencion += (a.haber - a.debe) * signo
                else:
                    f = por_cuenta.setdefault(a.cuenta, {'monto': D0, 'nombre': a.cuenta_nombre,
                                                         'glosa': a.glosa.split(' · ')[0]})
                    f['monto'] += (a.debe - a.haber) * signo
            base = sum((f['monto'] for f in por_cuenta.values()), D0)
            if base + igv <= 0:
                self.observar('Facturas de compra', comprobante, f'Importe cero o negativo ({base + igv}): no se armó')
                continue
            if doc not in por_doc:
                t = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6' if len(doc) == 11 else '1' if len(doc) == 8
                                           else '0', numero_doc=doc[:15], nombre=(lineas[0].contacto or doc)[:200])
                por_doc[t.numero_doc] = t.pk
                creados += 1
            tercero = por_doc[doc]
            tipo = tipo_origen if tipo_origen in TIPOS_COMPRA else ('07' if tipo_origen == '97' else '00')
            serie, _, numero = comprobante.rpartition('-') if '-' in comprobante else ('', '', comprobante)
            serie = serie.upper()[-4:]
            numero = (numero.lstrip('0') or '0')[-10:]
            if (tercero, tipo, serie, numero) in existentes:
                omitidos += 1
                continue
            existentes.add((tercero, tipo, serie, numero))
            primera = next(iter(por_cuenta), '')
            clasificacion = ('HONORARIOS' if tipo == '02' else 'MERCADERIA' if primera.startswith('60') else
                             'ACTIVO_FIJO' if primera.startswith(('33', '34')) else 'GASTO')
            gravada = igv > 0
            fecha = min(a.fecha for a in lineas)
            base_imponible, no_gravado = (base, D0) if gravada else (D0, base)
            total = base + igv
            glosa = f'{MARCA} {lineas[0].diario}' + (f' · tipo {tipo_origen}' if tipo != tipo_origen else '')
            cabeceras.append(Compra(
                tipo_comprobante=tipo, serie=serie, numero=numero, tercero_id=tercero, fecha_emision=fecha,
                periodo=fecha.strftime('%Y%m'), forma_pago='CREDITO', moneda='PEN', tipo_cambio=Decimal('1'),
                tipo_operacion='GRAVADA' if gravada else 'INAFECTA', clasificacion=clasificacion,
                base_imponible=base_imponible, no_gravado=no_gravado, inafecto=no_gravado, igv=igv, total=total,
                retencion_monto=retencion, total_pen=total, base_pen=base_imponible, nograv_pen=no_gravado,
                igv_pen=igv, ret_pen=retencion, ingresar_almacen=False, es_historico=True, glosa=glosa))
            detalle.append([dict(descripcion=f'{cuenta} {f["nombre"]} · {f["glosa"]}'[:250], cantidad=1,
                                 precio_unitario=f['monto'], subtotal=f['monto'],
                                 afectacion='' if gravada else 'INAFECTA')
                            for cuenta, f in por_cuenta.items() if f['monto']])
        for i in range(0, len(cabeceras), 1000):
            bloque = cabeceras[i:i + 1000]
            Compra.objects.bulk_create(bloque)
            if bloque[0].pk is None:
                for c in bloque:
                    c.pk = Compra.objects.get(tercero_id=c.tercero_id, tipo_comprobante=c.tipo_comprobante,
                                              serie=c.serie, numero=c.numero).pk
            self._bulk(CompraItem, [CompraItem(documento_id=c.pk, **it)
                                    for c, its in zip(bloque, detalle[i:i + 1000]) for it in its])
        total = sum((c.total for c in cabeceras), D0)
        self.resumen['Facturas de compra'] = (f'{len(cabeceras)} armadas desde los asientos (S/ {total:,.2f}); '
                                              f'{omitidos} ya estaban; {creados} proveedores creados')
        self.log(f'Facturas de compra: {self.resumen["Facturas de compra"]}')

    # ---------------------------------------------------------------- cuentas por pagar (saldos iniciales)
    def paso_por_pagar(self):
        """Saldo de cada proveedor en las cuentas 4212 y 424 al cierre de los asientos, asignado a sus comprobantes
        con saldo, del más reciente al más antiguo: el total por proveedor cuadra con el libro. El comprobante
        queda como saldo inicial (lo ya pagado en el sistema anterior se descuenta)."""
        from compras.models import Compra, CompraItem
        from core.models import Tercero
        from historial.models import AsientoAnterior
        if not AsientoAnterior.objects.exists():
            self.resumen['Por pagar'] = 'sin asientos: importe primero el paso contable'
            return
        if self.rehacer:
            Compra.objects.filter(es_saldo_inicial=True, es_historico=False, glosa__startswith=MARCA).delete()
            Compra.objects.filter(es_saldo_inicial=True, es_historico=True).update(es_saldo_inicial=False,
                                                                                    pagado_anterior=D0)
        elif Compra.objects.filter(es_saldo_inicial=True).exists():
            self.resumen['Por pagar'] = 'ya estaba cargado (use --rehacer para volver a calcularlo)'
            return
        por_proveedor, por_doc, datos_doc = defaultdict(Decimal), defaultdict(Decimal), {}
        for a in AsientoAnterior.objects.filter(cuenta__regex=CUENTAS_POR_PAGAR).order_by('fecha', 'id').iterator(
                chunk_size=10000):
            neto = a.haber - a.debe
            por_proveedor[a.contacto_doc] += neto
            por_doc[(a.contacto_doc, a.comprobante)] += neto
            datos_doc.setdefault((a.contacto_doc, a.comprobante), (a.fecha, a.tipo_comprobante, a.contacto,
                                                                  a.cuenta, a.cuenta_nombre))
        terceros = dict(Tercero.objects.values_list('numero_doc', 'pk'))
        compras = {(c.tercero_id, c.tipo_comprobante, c.serie, c.numero): c
                   for c in Compra.objects.filter(estado='REGISTRADO')}
        actualizados, nuevos, total, a_favor = 0, [], D0, D0
        for doc_prov, saldo_prov in por_proveedor.items():
            if saldo_prov < Decimal('0.05'):
                if saldo_prov < Decimal('-0.05'):
                    a_favor += -saldo_prov
                    self.observar('Por pagar', doc_prov, f'Saldo a favor de la empresa S/ {-saldo_prov:,.2f} '
                                                         '(anticipos o pagos de más): no es deuda')
                continue
            restante = saldo_prov
            documentos = sorted(((k, v) for k, v in por_doc.items() if k[0] == doc_prov and v > Decimal('0.05')),
                                key=lambda x: datos_doc[x[0]][0], reverse=True)
            for (doc, comprobante), saldo_doc in documentos:
                if restante <= Decimal('0.05'):
                    break
                pendiente = min(saldo_doc, restante).quantize(Decimal('0.01'))
                restante -= pendiente
                total += pendiente
                fecha, tipo_texto, nombre, cuenta, cuenta_nombre = datos_doc[(doc, comprobante)]
                m = re.match(r'\((\d+)\)', tipo_texto)
                tipo = m.group(1) if m and m.group(1) in TIPOS_COMPRA else '00'
                serie, _, numero = comprobante.rpartition('-') if '-' in comprobante else ('', '', comprobante or '0')
                serie, numero = serie.upper()[-4:], (numero.lstrip('0') or '0')[-10:]
                if doc not in terceros:
                    t = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6' if len(doc) == 11 else '0',
                                               numero_doc=doc[:15] or '-', nombre=(nombre or doc)[:200])
                    terceros[t.numero_doc] = t.pk
                tercero = terceros[doc]
                compra = compras.get((tercero, tipo, serie, numero))
                if compra is not None and compra.neto >= pendiente:
                    # la 42 guarda el neto (sin retenciones): lo pagado antes es el neto menos lo pendiente
                    Compra.objects.filter(pk=compra.pk).update(
                        es_saldo_inicial=True, pagado_anterior=compra.neto - pendiente,
                        fecha_vencimiento=compra.fecha_vencimiento or compra.fecha_emision)
                    actualizados += 1
                    continue
                if compra is not None:  # el libro dice más de lo que muestra la factura: se registra aparte
                    numero = f'{numero[-8:]}S'
                nuevos.append((Compra(
                    tipo_comprobante=tipo, serie=serie, numero=numero, tercero_id=tercero, fecha_emision=fecha,
                    fecha_vencimiento=fecha, periodo=fecha.strftime('%Y%m'), forma_pago='CREDITO', moneda='PEN',
                    tipo_cambio=Decimal('1'), tipo_operacion='INAFECTA', clasificacion='GASTO', inafecto=pendiente,
                    no_gravado=pendiente, total=pendiente, total_pen=pendiente, nograv_pen=pendiente,
                    ingresar_almacen=False, es_saldo_inicial=True,
                    glosa=f'{MARCA} Saldo por pagar según {cuenta} {cuenta_nombre}'), comprobante))
            if restante > Decimal('0.05'):
                self.observar('Por pagar', doc_prov, f'S/ {restante:,.2f} del saldo sin comprobante identificable')
        for compra, comprobante in nuevos:
            compra.save()
            CompraItem.objects.create(documento=compra, descripcion=f'Saldo pendiente {comprobante}'[:250],
                                      cantidad=1, precio_unitario=compra.total)
        provisiones = AsientoAnterior.objects.filter(cuenta__startswith='4211').aggregate(d=Sum('debe'), h=Sum('haber'))
        if provisiones['h']:
            self.observar('Por pagar', '4211', f'Provisiones sin comprobante (facturas no emitidas) '
                                               f'S/ {provisiones["h"] - provisiones["d"]:,.2f}: no se cargan como deuda')
        self.resumen['Por pagar'] = (f'S/ {total:,.2f} en {actualizados + len(nuevos)} comprobantes '
                                     f'({actualizados} facturas ya cargadas, {len(nuevos)} nuevas); saldo a favor de '
                                     f'proveedores S/ {a_favor:,.2f} (ver observaciones)')
        self.log(f'Por pagar: {self.resumen["Por pagar"]}')

    # ---------------------------------------------------------------- caja y bancos
    def paso_bancos(self):
        """Una cuenta de caja o banco por cada cuenta 10 con saldo, enlazada a su cuenta contable, con el saldo del
        libro como saldo inicial (las de moneda extranjera se convierten al tipo de cambio de la fecha)."""
        from contabilidad import automatico
        from contabilidad.models import CuentaContable
        from core.tipo_cambio import venta_del_dia
        from finanzas.models import Cuenta
        from historial.models import AsientoAnterior
        filas = (AsientoAnterior.objects.filter(cuenta__startswith='10').values('cuenta', 'cuenta_nombre')
                 .annotate(d=Sum('debe'), h=Sum('haber')).order_by('cuenta'))
        if not filas:
            self.resumen['Caja y bancos'] = 'sin asientos: importe primero el paso contable'
            return
        corte = AsientoAnterior.objects.aggregate(f=Max('fecha'))['f']
        tc = venta_del_dia(corte)
        plan = {c.codigo: c for c in CuentaContable.objects.filter(codigo__startswith='10')}
        creadas, total = 0, D0
        for r in filas:
            codigo, nombre, saldo = r['cuenta'], r['cuenta_nombre'], (r['d'] or D0) - (r['h'] or D0)
            if codigo in CUENTAS_PUENTE:
                if saldo:
                    self.observar('Caja y bancos', codigo, f'{nombre}: cuenta puente con saldo S/ {saldo:,.2f}; '
                                                           'no es una cuenta de dinero (regularícela)')
                continue
            if not saldo:
                continue
            usd = ' ME ' in f' {nombre.upper()} '
            nombre_u = unicodedata.normalize('NFKD', nombre.upper()).encode('ascii', 'ignore').decode()
            banco = next((b for clave, b in BANCOS if clave in nombre_u), 'OTRO')
            if codigo not in plan:
                plan[codigo] = CuentaContable.objects.create(codigo=codigo, nombre=nombre[:200], naturaleza='DEUDORA')
            cuenta = Cuenta.objects.filter(cuenta_contable=plan[codigo]).first()
            if cuenta is None:
                cuenta = Cuenta(cuenta_contable=plan[codigo])
                creadas += 1
            cuenta.nombre = nombre[:100]
            cuenta.tipo = 'CAJA' if codigo.startswith('101') else 'BANCO'
            cuenta.banco = '' if cuenta.tipo == 'CAJA' else banco
            cuenta.numero = (re.findall(r'\d{4,}', nombre) or [''])[-1]
            cuenta.moneda = 'USD' if usd else 'PEN'
            cuenta.es_detracciones = codigo.startswith('1042')
            cuenta.saldo_inicial = (saldo / tc).quantize(Decimal('0.01')) if usd else saldo
            cuenta.permite_sobregiro = saldo < 0
            cuenta.save()
            total += saldo
            if saldo < 0:
                self.observar('Caja y bancos', codigo, f'{nombre}: saldo negativo S/ {saldo:,.2f} en el libro anterior '
                                                       '(revise con el estado de cuenta)')
        automatico.generar_apertura()
        self.resumen['Caja y bancos'] = (f'{creadas} cuentas creadas; saldo total S/ {total:,.2f} al '
                                         f'{corte:%d/%m/%Y} (dólares al T.C. {tc})')
        self.log(f'Caja y bancos: {self.resumen["Caja y bancos"]}')

    # ---------------------------------------------------------------- reporte
    def _reporte(self, ruta):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = 'Resumen'
        for k, v in self.resumen.items():
            ws.append([k, str(v)])
        h = wb.create_sheet('Observaciones')
        h.append(['Paso', 'Referencia', 'Observación'])
        for o in self.obs:
            h.append([str(x) for x in o])
        wb.save(ruta)
