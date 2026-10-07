"""Carga de manufactura desde el reporte de producción del sistema anterior (v1.24.1)."""
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.models import Almacen, Producto
from historial.models import OrdenFabricacionAnterior

from . import servicios
from .management.commands.cargar_manufactura import COLUMNAS
from .models import ListaMateriales, OrdenProduccion

D = Decimal


class CargaManufacturaTest(TestCase):
    def setUp(self):
        Almacen.objects.get_or_create(codigo='LPROD', defaults={'nombre': 'Lacteos Producción'})
        self.pote = Producto.objects.create(codigo='PP1', nombre='Pote etiquetado', clase='SEMIELABORADO')
        self.vaso = Producto.objects.create(codigo='E1', nombre='Vaso', clase='SUMINISTRO')
        self.etiqueta = Producto.objects.create(codigo='E2', nombre='Etiqueta', clase='SUMINISTRO')
        self.carpeta = tempfile.mkdtemp()
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(list(COLUMNAS.values()))

        def orden(ref, estado, cantidad, planeada, fecha):
            for comp in ('[E1] Vaso', '[E2] Etiqueta'):
                fila = {'orden': ref, 'fecha_kardex': fecha, 'fecha_prog': fecha, 'producto': '[PP1] Pote etiquetado',
                        'cantidad': cantidad, 'producida': cantidad if estado == 'Listo' else 0, 'almacen':
                        'Lacteos Producción', 'estado': estado, 'componente': comp, 'planeada': planeada}
                ws.append([fila[k] for k in COLUMNAS])
                ws.append([fila[k] for k in COLUMNAS])  # otra fila por lote reservado: no se suma dos veces
        orden('LPROD/MO/1', 'Listo', 1000, 1000, datetime(2026, 8, 1))
        orden('LPROD/MO/2', 'Listo', 500, 500, datetime(2026, 8, 5))
        orden('LPROD/MO/3-001', 'Listo', 300, 800, datetime(2026, 9, 1))  # dividida: conserva lo planificado
        wb.save(os.path.join(self.carpeta, 'reporte.xlsx'))
        OrdenFabricacionAnterior.objects.create(referencia='LPROD/MO/3-002', producto=self.pote, codigo='PP1',
                                                cantidad=D('500'), producida=D('0'), estado='En progreso',
                                                almacen='Lacteos Producción')
        OrdenFabricacionAnterior.objects.create(referencia='LPROD/MO/9', producto=self.vaso, codigo='E1',
                                                cantidad=D('10'), producida=D('0'), estado='Borrador')
        hecha = OrdenFabricacionAnterior.objects.create(
            referencia='LPROD/MOLPROD/1-001', producto=self.pote, codigo='PP1', cantidad=D('1000'),
            producida=D('1000'), estado='Listo', almacen='Lacteos Producción', costo_materiales=D('250'),
            fecha_kardex=datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
        hecha.consumos.create(producto=self.vaso, codigo='E1', requerida=D('1010'), reservada=D('1000'))
        hecha.consumos.create(producto=self.vaso, codigo='E1', requerida=D('1010'), reservada=D('10'))  # otro lote
        OrdenFabricacionAnterior.objects.create(referencia='LPROD/MO/7', producto=self.vaso, codigo='E1',
                                                cantidad=D('5'), producida=D('0'), estado='Cancelado')

    def test_recetas_y_ordenes_abiertas_sin_duplicar(self):
        call_command('cargar_manufactura', self.carpeta, verbosity=0)
        lm = ListaMateriales.objects.get(producto=self.pote)
        # proporción 1:1 de las órdenes enteras (la dividida tenía 800 planificados para 300)
        self.assertEqual({c.producto.codigo: c.cantidad / lm.cantidad_base for c in lm.componentes.all()},
                         {'E1': 1, 'E2': 1})
        self.assertTrue(lm.versiones.filter(activa=True).exists())
        o = OrdenProduccion.objects.get(es_historica=False)
        self.assertEqual((o.estado, o.cantidad, o.almacen_insumos.codigo), ('CONFIRMADA', D('500'), 'LPROD'))
        self.assertEqual({c.producto.codigo: c.cantidad_plan for c in o.consumos.all()}, {'E1': 500, 'E2': 500})
        self.assertIn('LPROD/MO/3-002', o.glosa)
        # terminada: con su costo y consumo real, sin operación de almacén; no se anula en el ERP
        t = OrdenProduccion.objects.get(es_historica=True, estado='TERMINADA')
        self.assertEqual((t.numero, t.cantidad_producida, t.costo_unitario, t.operacion_id),
                         ('LPROD/MO/1-001', D('1000'), D('0.25'), None))
        self.assertEqual({c.producto.codigo: (c.cantidad_plan, c.cantidad_real) for c in t.consumos.all()},
                         {'E1': (D('1000'), D('1010'))})
        with self.assertRaises(servicios.ErrorProduccion):
            servicios.anular(t, None, 'Prueba de anulación de una orden anterior')
        # cancelada: anulada; el vaso no tiene receta: se le crea una de referencia obsoleta (sin versión)
        c = OrdenProduccion.objects.get(es_historica=True, estado='ANULADA')
        self.assertEqual((c.producto, c.lista.estado), (self.vaso, 'OBSOLETA'))
        self.assertFalse(c.lista.versiones.exists())
        # la abierta del vaso (sin receta real) no se crea; volver a correr no duplica nada
        self.assertEqual(OrdenProduccion.objects.count(), 3)
        call_command('cargar_manufactura', self.carpeta, verbosity=0)
        self.assertEqual((ListaMateriales.objects.count(), OrdenProduccion.objects.count()), (2, 3))
