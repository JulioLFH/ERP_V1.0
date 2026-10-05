"""Factor de conversión de compra (v1.17, punto 24): se compra en cajas de 12 y se almacena por unidad."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.inventario import en_camino, sugerencias_compra
from core.models import Almacen, Producto, Tercero
from inventario import servicios as inv
from inventario.models import Operacion, TipoOperacion
from proveedores.servicios import lineas_por_facturar

from .models import Compra, CompraItem, OrdenCompra, OrdenCompraItem

D = Decimal
HOY = date.today()


class FactorCompraTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        self.gaseosa = Producto.objects.create(nombre='Gaseosa 500 ml', clase='MERCADERIA', unidad='NIU',
                                               unidad_compra='BX', factor_compra=D('12'), proveedor=self.prov,
                                               precio_compra=D('24'), punto_reorden=D('24'), stock_maximo=D('60'))

    def test_factura_con_ingreso_directo_convierte_cantidad_y_costo(self):
        c = Compra.objects.create(tercero=self.prov, serie='F001', numero='1212', fecha_emision=HOY)
        CompraItem.objects.create(documento=c, producto=self.gaseosa, descripcion='Gaseosa caja x12', cantidad=3,
                                  precio_unitario=D('24'))  # 3 cajas a S/ 24
        c.calcular_totales()
        c.save()
        c.aplicar_stock()
        self.gaseosa.refresh_from_db()
        self.assertEqual((self.gaseosa.stock, self.gaseosa.costo_promedio), (D('36'), D('2.0000')))
        c.revertir_stock()
        self.assertEqual(Producto.objects.get(pk=self.gaseosa.pk).stock, D('0'))

    def test_orden_recepcion_portal_y_reposicion(self):
        oc = OrdenCompra.objects.create(numero='OC01-00001212', tercero=self.prov, estado='APROBADO')
        OrdenCompraItem.objects.create(documento=oc, producto=self.gaseosa, descripcion='Gaseosa', cantidad=5,
                                       precio_unitario=D('24'))
        self.assertEqual(en_camino()[self.gaseosa.pk], D('60'))  # 5 cajas = 60 unidades por llegar
        rec = Operacion(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), orden_compra=oc)
        self.assertEqual(inv.pendientes(rec)[self.gaseosa.pk], (D('60'), D('2.0000')))
        op = Operacion.objects.create(tipo=rec.tipo, fecha=HOY, orden_compra=oc, almacen_destino=Almacen.principal())
        op.items.create(producto=self.gaseosa, cantidad=D('24'), costo_unitario=D('2'))  # llegaron 2 cajas
        inv.confirmar(op, self.user)
        # el proveedor factura en cajas: puede facturar 2 de las 5
        linea = lineas_por_facturar(oc)[0]
        self.assertEqual(linea['esperado'], D('2'))
        self.assertEqual(en_camino()[self.gaseosa.pk], D('36'))  # faltan 3 cajas
        # sugerencia de compra en cajas
        oc.estado = 'ANULADO'
        oc.save()
        fila = next(f for f in sugerencias_compra() if f['p'] == self.gaseosa)
        self.assertEqual((fila['cantidad'], fila['unidades']), (D('3'), D('36')))  # (60 − 24) / 12 = 3 cajas
