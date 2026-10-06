"""v1.20: importaciones: gastos vinculados prorrateados al costo de los productos y su contabilidad (6091 → 2811 →
inventario)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea
from core.models import Producto, Tercero

from . import importaciones
from .models import Compra, CompraItem, Importacion

D = Decimal
HOY = date.today()


class ImportacionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.exterior = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='0', numero_doc='EXT001',
                                               nombre='CHINA MACHINERY LTD')
        self.agente = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100049008',
                                             nombre='AGENCIA DE ADUANAS SAC')
        self.a = Producto.objects.create(nombre='Motor importado', clase='MERCADERIA')
        self.b = Producto.objects.create(nombre='Bomba importada', clase='MERCADERIA')

    def compra(self, tercero, items, numero, ingresar=True):
        c = Compra.objects.create(tercero=tercero, tipo_comprobante='01', serie='E001', numero=numero,
                                  fecha_emision=HOY, tipo_operacion='INAFECTA' if ingresar else 'GRAVADA',
                                  ingresar_almacen=ingresar, clasificacion='MERCADERIA' if ingresar else 'SERVICIO')
        for producto, cant, precio in items:
            CompraItem.objects.create(documento=c, producto=producto, descripcion=str(producto or 'Servicio'),
                                      cantidad=D(cant), precio_unitario=D(precio))
        c.calcular_totales()
        c.save()
        if ingresar:
            c.aplicar_stock()
        return c

    def test_prorrateo_por_valor_y_costo(self):
        factura = self.compra(self.exterior, [(self.a, '10', '300'), (self.b, '10', '100')], '1')
        costo_a = Producto.objects.get(pk=self.a.pk).costo_promedio
        flete = self.compra(self.agente, [(None, '1', '400')], '2', ingresar=False)
        r = self.client.post(reverse('compras:importacion_nueva'), {
            'descripcion': 'Motores y bombas', 'compra': factura.pk, 'dua': '118-2026-10-000123', 'metodo': 'VALOR'})
        imp = Importacion.objects.get()
        self.assertRedirects(r, reverse('compras:importacion', args=[imp.pk]))
        self.client.post(reverse('compras:importacion', args=[imp.pk]),
                         {'accion': 'gasto', 'concepto': 'FLETE', 'compra': flete.pk, 'monto': ''})
        flete.refresh_from_db()
        self.assertEqual(flete.cuenta_contable.codigo, '6091')
        filas = {f['producto'].pk: f['gasto'] for f in importaciones.prorrateo(imp)}
        self.assertEqual((filas[self.a.pk], filas[self.b.pk]), (D('300.00'), D('100.00')))  # 75% / 25% del FOB
        self.client.post(reverse('compras:importacion', args=[imp.pk]), {'accion': 'liquidar'})
        imp.refresh_from_db()
        self.assertEqual(imp.estado, 'LIQUIDADA')
        self.assertEqual(Producto.objects.get(pk=self.a.pk).costo_promedio, costo_a + D('30'))  # 300 / 10 unidades
        self.assertEqual(importaciones.liquidar(imp), [])  # volver a liquidar no duplica
        self.assertEqual(centralizar_periodo(HOY.strftime('%Y%m'))['errores'], [])
        agg = AsientoLinea.objects.filter(cuenta__codigo='2811').aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual((agg['d'] or 0) - (agg['h'] or 0), 0)  # la cuenta por recibir queda en cero

    def test_pantallas(self):
        for nombre in ('importaciones', 'importacion_nueva'):
            self.assertEqual(self.client.get(reverse(f'compras:{nombre}')).status_code, 200)
