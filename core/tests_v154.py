"""v1.54, correcciones del piloto (etapa 2, compras): historial de precios de compra con alerta en la OC (44) y libro
de fuentes: proveedor fijo por periodo, bloqueado y homologado (45). Las tolerancias en S/ y % con pago bloqueado (47)
se prueban en proveedores/tests.py. Un escenario por clase."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, CompraItem, OrdenCompra, OrdenCompraItem, RegistroInfo

from .models import Empresa, Producto, Tercero

D = Decimal


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v154', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)
        self.prov_a = Tercero.objects.create(numero_doc='20100070970', nombre='PROVEEDOR A SAC', tipo='PROVEEDOR',
                                             tipo_doc='6')
        self.prov_b = Tercero.objects.create(numero_doc='20512345678', nombre='PROVEEDOR B SAC', tipo='PROVEEDOR',
                                             tipo_doc='6')
        self.p = Producto.objects.create(codigo='MP-154', nombre='INSUMO 154', tipo='BIEN', unidad='KGM')

    def compra(self, prov, numero, fecha, precio, moneda='PEN', tc='1'):
        c = Compra.objects.create(tercero=prov, tipo_comprobante='01', serie='F154', numero=str(numero),
                                  fecha_emision=fecha, moneda=moneda, tipo_cambio=D(tc))
        CompraItem.objects.create(documento=c, producto=self.p, descripcion='Insumo', cantidad=10,
                                  precio_unitario=D(precio))
        return c

    def oc(self, prov, precio='3.00'):
        oc = OrdenCompra.objects.create(numero=f'OC01-{OrdenCompra.objects.count() + 1540:08d}', tercero=prov,
                                        estado='PENDIENTE')
        OrdenCompraItem.objects.create(documento=oc, producto=self.p, descripcion='Insumo', cantidad=5,
                                       precio_unitario=D(precio))
        oc.calcular_totales()
        oc.save()
        return oc


class HistorialPreciosTest(Base):
    def test_historial_grafico_excel_y_alerta_en_la_oc(self):
        from compras import historial_precios as hp
        self.compra(self.prov_a, 1, date(2026, 8, 1), '3.00')
        self.compra(self.prov_a, 2, date(2026, 9, 1), '3.30')
        self.compra(self.prov_b, 3, date(2026, 9, 15), '1.00', moneda='USD', tc='3.500')
        filas = hp.historial(self.p)
        self.assertEqual([f['precio_pen'] for f in filas], [D('3.00'), D('3.30'), D('3.50')])
        self.assertEqual(filas[1]['variacion'], D('10.00'))  # +10% frente a la compra anterior del mismo proveedor
        self.assertIsNone(filas[2]['variacion'])  # primera compra al proveedor B
        r = self.client.get(reverse('compras:historial_precios'), {'producto': self.p.pk})
        self.assertContains(r, 'PROVEEDOR A SAC')
        self.assertContains(r, '<polyline', count=2)  # una línea por proveedor
        r = self.client.get(reverse('compras:historial_precios'), {'producto': self.p.pk, 'formato': 'excel'})
        self.assertIn('spreadsheet', r['Content-Type'])
        # la OC avisa si el precio supera el último pagado en más del % de la empresa (10 % por defecto)
        oc = self.oc(self.prov_a, precio='3.80')  # 3.80 vs 3.30 = +15.15 %
        self.assertTrue(hp.alertas_orden(oc))
        r = self.client.get(reverse('compras:oc_detalle', args=[oc.pk]))
        self.assertContains(r, 'más que el último precio pagado')
        self.assertFalse(hp.alertas_orden(self.oc(self.prov_a, precio='3.50')))  # +6 %: sin aviso
        Empresa.objects.update(alerta_precio_pct=0)
        self.assertFalse(hp.alertas_orden(oc))  # 0 = sin aviso


class LibroFuentesTest(Base):
    def test_fijo_bloqueado_y_homologado(self):
        from compras.aprobacion_oc import ErrorAprobacionOC, aprobar
        from compras.registro_info import fuente, problemas_orden
        hoy = timezone.localdate()
        a = RegistroInfo.objects.create(producto=self.p, proveedor=self.prov_a, preferido=True)
        b = RegistroInfo.objects.create(producto=self.p, proveedor=self.prov_b, fijo_desde=hoy - timedelta(5),
                                        fijo_hasta=hoy + timedelta(5))
        self.assertEqual(fuente(self.p), b)  # el fijo vigente manda sobre el preferido
        self.assertEqual(fuente(self.p, hoy + timedelta(30)), a)  # fuera del periodo, el preferido
        b.bloqueado, b.motivo_bloqueo = True, 'Lote rechazado por calidad'
        b.save()
        self.assertEqual(fuente(self.p), a)  # nunca uno bloqueado
        oc = self.oc(self.prov_b)
        bloqueos, _ = problemas_orden(oc)
        self.assertIn('Lote rechazado por calidad', bloqueos[0])
        with self.assertRaises(ErrorAprobacionOC):
            aprobar(oc, self.admin)
        self.assertContains(self.client.get(reverse('compras:oc_detalle', args=[oc.pk])), 'No se puede aprobar')
        # homologados: comprar a uno no homologado avisa (no bloquea)
        b.bloqueado = False
        b.save()
        a.homologado = True
        a.save()
        _, avisos = problemas_orden(self.oc(self.prov_b))
        self.assertIn('no está homologado', avisos[0])
        # el formulario exige motivo al bloquear y no deja bloquear al preferido
        r = self.client.post(reverse('compras:registro_info', args=[a.pk]), {
            'producto': self.p.pk, 'proveedor': self.prov_a.pk, 'moneda': 'PEN', 'plazo_entrega': 0,
            'cantidad_minima': '0', 'preferido': 'on', 'bloqueado': 'on', 'activo': 'on'})
        self.assertContains(r, 'Indique por qué se bloquea')
        self.assertContains(r, 'no puede ser el preferido')
