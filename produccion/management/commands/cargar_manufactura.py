"""Carga el módulo de Manufactura desde el reporte de producción del sistema anterior (Odoo, report.simple.mrp).

    python manage.py cargar_manufactura "<carpeta con los Excel>" [--sin-ordenes] [--rehacer]

1. Recetas: por cada producto fabricado, la lista de materiales de su orden más reciente (terminada si la hay):
   los componentes planificados (Cantidad Planeada Componente) para la cantidad de la orden. Se crea la lista
   aprobada y su versión de fabricación V1 (sin hoja de ruta: el reporte no trae horas ni mano de obra).
2. Órdenes abiertas: las del historial del sistema anterior en progreso, borrador o por cerrar, por lo que falta
   producir, como órdenes confirmadas (no mueven stock hasta que se terminen en el ERP). Las de productos sin
   receta se informan y no se crean.
Se puede volver a correr: no duplica recetas ni órdenes (--rehacer vuelve a crear las recetas cargadas).
"""
import os
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

D0 = Decimal('0')
CODIGO_RECETA = 'ANT'  # recetas que vienen del sistema anterior
MARCA = '[Sistema anterior]'
ABIERTAS = ('en progreso', 'borrador', 'por cerrar', 'confirmado', 'confirmada')
COLUMNAS = {'orden': 'Orden de Fabricación', 'fecha_kardex': 'Fecha Kardex', 'fecha_prog': 'Fecha Programada',
            'producto': 'Producto Terminado', 'cantidad': 'Cantidad Requerida', 'producida': 'Cantidad Producida',
            'almacen': 'Almacén', 'estado': 'Estado', 'componente': 'Componente',
            'planeada': 'Cantidad Planeada Componente'}


def codigo_de(texto):
    m = re.match(r'^\s*\[([^\]]+)\]', str(texto or ''))
    return (m.group(1) if m else str(texto or '')).strip()


def _dec(v):
    try:
        return Decimal(str(v)) if v not in (None, '') else D0
    except ArithmeticError:
        return D0


def _fecha(v):
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def leer_reporte(carpeta):
    """{orden: {producto, cantidad, estado, fecha, almacen, componentes {codigo: cantidad planificada}}}."""
    import openpyxl
    archivos = sorted(f for f in os.listdir(carpeta) if f.lower().endswith('.xlsx') and not f.startswith('~$'))
    if not archivos:
        raise CommandError(f'No hay archivos .xlsx en {carpeta}')
    ordenes = {}
    for nombre in archivos:
        ws = openpyxl.load_workbook(os.path.join(carpeta, nombre), read_only=True, data_only=True).worksheets[0]
        filas = ws.iter_rows(values_only=True)
        encabezado = [str(c or '').strip() for c in next(filas)]
        try:
            idx = {k: encabezado.index(v) for k, v in COLUMNAS.items()}
        except ValueError as exc:
            raise CommandError(f'{nombre}: falta la columna {exc}') from exc
        for r in filas:
            ref = str(r[idx['orden']] or '').strip()
            if not ref:
                continue
            o = ordenes.setdefault(ref, {
                'producto': codigo_de(r[idx['producto']]), 'cantidad': _dec(r[idx['cantidad']]),
                'producida': _dec(r[idx['producida']]), 'estado': str(r[idx['estado']] or '').strip(),
                'fecha': _fecha(r[idx['fecha_kardex']]) or _fecha(r[idx['fecha_prog']]),
                'almacen': str(r[idx['almacen']] or '').strip(), 'componentes': {}})
            comp = codigo_de(r[idx['componente']])
            if comp:  # una fila por lote reservado: la cantidad planificada se repite
                o['componentes'][comp] = max(o['componentes'].get(comp, D0), _dec(r[idx['planeada']]))
    return ordenes


def elegir_referencia(lista, desde):
    """La orden cuya receta se toma: la proporción componente / cantidad que más se repite entre las órdenes del
    producto. Las órdenes divididas (-001, -002…) conservan lo planificado de la orden original, así que solo se usan
    si no hay otras; entre las de la proporción más frecuente, la más reciente (terminada primero)."""
    enteras = [x for x in lista if not re.search(r'-\d{3}$', x[0])] or lista
    firmas = defaultdict(list)
    for ref, o in enteras:
        firma = tuple(sorted((c, round(q / o['cantidad'], 5)) for c, q in o['componentes'].items() if q > 0))
        firmas[firma].append((ref, o))
    clave = lambda x: (x[1]['estado'].lower() == 'listo', x[1]['fecha'] or desde, x[0])  # noqa: E731
    mejores = max(firmas.values(), key=lambda g: (len(g), max(clave(x) for x in g)))
    return max(mejores, key=clave)


class Command(BaseCommand):
    help = 'Recetas y órdenes abiertas de manufactura desde el reporte de producción del sistema anterior'

    def add_arguments(self, parser):
        parser.add_argument('carpeta')
        parser.add_argument('--sin-ordenes', action='store_true', help='Solo las recetas')
        parser.add_argument('--rehacer', action='store_true', help='Vuelve a crear las recetas ya cargadas')

    def handle(self, carpeta, sin_ordenes=False, rehacer=False, **_):
        if not os.path.isdir(carpeta):
            raise CommandError(f'No existe la carpeta {carpeta}')
        ordenes = leer_reporte(carpeta)
        self.stdout.write(f'{len(ordenes)} órdenes leídas del reporte.')
        recetas = self.recetas(ordenes, rehacer)
        if not sin_ordenes:
            self.ordenes_abiertas(recetas)

    # ------------------------------------------------------------ recetas
    def _producto(self, codigo, cache):
        from core.models import Producto
        if codigo not in cache:
            cache[codigo] = (Producto.objects.filter(codigo=codigo).first() or
                             Producto.objects.filter(codigo=re.sub(r'archived$', '', codigo)).first())
        return cache[codigo]

    def recetas(self, ordenes, rehacer):
        from produccion.models import ListaMateriales, OrdenProduccion, VersionFabricacion
        por_producto = defaultdict(list)
        for ref, o in ordenes.items():
            if o['cantidad'] > 0 and o['componentes'] and o['estado'].lower() != 'cancelado':
                por_producto[o['producto']].append((ref, o))
        cache, creadas, existentes, sin_producto, faltan = {}, 0, 0, [], set()
        resultado = {}
        desde = date(2025, 1, 1)
        with transaction.atomic():
            for codigo, lista in sorted(por_producto.items()):
                producto = self._producto(codigo, cache)
                if not producto:
                    sin_producto.append(codigo)
                    continue
                ref, o = elegir_referencia(lista, desde)
                actual = ListaMateriales.objects.filter(producto=producto, codigo=CODIGO_RECETA).first()
                if actual and rehacer and not OrdenProduccion.objects.filter(lista=actual).exists():
                    VersionFabricacion.objects.filter(lista=actual).delete()
                    actual.delete()
                    actual = None
                if actual:
                    existentes += 1
                    resultado[producto.pk] = actual
                    continue
                lm = ListaMateriales.objects.create(
                    producto=producto, codigo=CODIGO_RECETA, cantidad_base=o['cantidad'].quantize(Decimal('0.01')),
                    estado='APROBADA', vigente_desde=desde,
                    observaciones=f'{MARCA} Lista de materiales de la orden {ref} ({o["estado"]}).')
                for comp, cantidad in sorted(o['componentes'].items()):
                    p = self._producto(comp, cache)
                    if not p:
                        faltan.add(comp)
                        continue
                    if p.pk == producto.pk or cantidad <= 0:
                        continue
                    lm.componentes.create(producto=p, cantidad=cantidad.quantize(Decimal('0.0001')))
                if not lm.componentes.exists():
                    lm.delete()
                    continue
                VersionFabricacion.objects.update_or_create(
                    producto=producto, codigo='V1',
                    defaults={'lista': lm, 'descripcion': 'Receta del sistema anterior', 'vigente_desde': desde,
                              'activa': True})
                resultado[producto.pk] = lm
                creadas += 1
        self.stdout.write(self.style.SUCCESS(f'Recetas: {creadas} creadas, {existentes} ya estaban.'))
        if sin_producto:
            self.stdout.write(self.style.WARNING(f'Productos fabricados que no están en el ERP: {", ".join(sin_producto)}'))
        if faltan:
            self.stdout.write(self.style.WARNING(f'Componentes que no están en el ERP: {", ".join(sorted(faltan))}'))
        return resultado

    # ------------------------------------------------------------ órdenes abiertas
    def _almacen(self, referencia, nombre, cache):
        from core.models import Almacen
        clave = (referencia.split('/')[0], nombre)
        if clave not in cache:
            cache[clave] = (Almacen.objects.filter(codigo__iexact=clave[0]).first() or
                            (Almacen.objects.filter(nombre__iexact=nombre).first() if nombre else None) or
                            Almacen.principal())
        return cache[clave]

    def ordenes_abiertas(self, recetas):
        from historial.models import OrdenFabricacionAnterior
        from produccion import servicios
        from produccion.models import OrdenProduccion
        hechas = set(OrdenProduccion.objects.filter(glosa__startswith=MARCA).values_list('glosa', flat=True))
        almacenes, creadas, ya, sin_receta, completas = {}, 0, 0, defaultdict(int), 0
        corte = date(2026, 9, 30)
        from core.models import Empresa
        corte = Empresa.actual().fecha_corte_contable or corte
        qs = (OrdenFabricacionAnterior.objects.exclude(producto__isnull=True)
              .select_related('producto').order_by('inicio', 'referencia'))
        for h in qs.iterator():
            if h.estado.strip().lower() not in ABIERTAS:
                continue
            glosa = f'{MARCA} {h.referencia} ({h.estado})'
            if glosa in hechas:
                ya += 1
                continue
            lista = recetas.get(h.producto_id)
            if not lista:
                sin_receta[h.producto.codigo] += 1
                continue
            falta = (h.cantidad or D0) - (h.producida or D0)
            if falta <= 0:
                completas += 1
                continue
            almacen = self._almacen(h.referencia, h.almacen, almacenes)
            with transaction.atomic():
                o = OrdenProduccion.objects.create(
                    producto=h.producto, lista=lista, version=lista.versiones.first(),
                    cantidad=falta.quantize(Decimal('0.01')), fecha=h.inicio.date() if h.inicio else corte,
                    almacen_insumos=almacen, almacen_destino=almacen, glosa=glosa)
                servicios.explotar(o)
                servicios.confirmar(o)
            creadas += 1
        self.stdout.write(self.style.SUCCESS(f'Órdenes abiertas: {creadas} creadas como confirmadas, {ya} ya estaban.'))
        if completas:
            self.stdout.write(f'{completas} abiertas ya tenían todo producido: no se crean.')
        if sin_receta:
            total = sum(sin_receta.values())
            self.stdout.write(self.style.WARNING(
                f'{total} órdenes abiertas de {len(sin_receta)} productos sin receta (no se fabricaron en el '
                f'reporte): no se crean.'))
