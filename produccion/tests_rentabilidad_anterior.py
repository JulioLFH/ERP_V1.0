"""Rentabilidad con ventas del sistema anterior: el costo sale del kardex de ese sistema (v1.26.3)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from core.models import Producto, Tercero
from historial.models import MovimientoAnterior
from ventas.models import Venta, VentaItem

from .costos import rentabilidad

D = Decimal


class RentabilidadAnteriorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)

    def test_costo_del_kardex_anterior(self):
        cliente = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS']).first()
        p = Producto.objects.create(nombre='Yogurt histórico', clase='PRODUCTO_TERMINADO')
        fecha = date(2025, 8, 15)
        v = Venta.objects.create(tercero=cliente, serie='FF01', numero='123', fecha_emision=fecha, es_historico=True)
        VentaItem.objects.create(documento=v, producto=p, descripcion=p.nombre, cantidad=10, precio_unitario=D('5'))
        v.calcular_totales()
        v.save()
        # el kardex anterior guarda el costo total de la línea: 10 unidades costaron 30
        MovimientoAnterior.objects.create(id_origen=1, fecha=fecha, almacen='PT', transaccion='Salida por Ventas',
                                          comprobante='FF01-00000123', producto=p, codigo=p.codigo,
                                          descripcion=p.nombre, salida=D('10'), costo=D('30'))
        filas, total = rentabilidad(fecha, fecha, 'producto')
        fila = next(f for f in filas if f['nombre'] == p.nombre)
        self.assertEqual((fila['ventas'], fila['costo'], fila['margen']), (D('50.00'), D('30.00'), D('20.00')))
        self.assertEqual(total['sin_costo'], 0)
        # sin su salida en el kardex anterior: costo cero y aviso
        MovimientoAnterior.objects.all().delete()
        _, total = rentabilidad(fecha, fecha, 'producto')
        self.assertEqual(total['sin_costo'], 1)
