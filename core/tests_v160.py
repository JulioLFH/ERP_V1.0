"""v1.60, checklist v1.56 (VEN-11): permisos y aprobaciones de ventas: pedido bajo la tarifa o con exceso de crédito
por el motor de aprobación (documento PEDIDO), venta bajo la tarifa, notas de crédito por devolución, cambio de
vendedor y reactivar una venta anulada que no llegó a SUNAT. Un escenario por clase."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Almacen, PerfilUsuario, Producto, ReglaAprobacion, StockAlmacen, Tercero
from .modulos import GRUPOS

D = Decimal
HOY = timezone.localdate().isoformat()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v160', 'a@a.pe', 'Clave-segura-9')
        cls.cliente = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        cls.p = Producto.objects.get(codigo='P004')
        cls.p.precio_venta = D('100')
        cls.p.save()
        cls.almacen = Almacen.principal()
        cls.p.mover_stock(D('50'), 'Inicial v160', costo=D('40'), almacen=cls.almacen)

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def usuario(self, nombre, acciones):
        u = User.objects.create_user(nombre, password='x')
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=acciones)
        return u

    def pedido(self, precio, condicion='Contado', vendedor=''):
        from ventas.models import Cotizacion, CotizacionItem
        cot = Cotizacion.objects.create(tipo='PED', numero=f'PD01-{Cotizacion.objects.count() + 900:08d}',
                                        tercero=self.cliente, condicion_pago=condicion, vendedor=vendedor)
        CotizacionItem.objects.create(documento=cot, producto=self.p, descripcion=self.p.nombre, cantidad=D('2'),
                                      precio_unitario=D(precio))
        cot.calcular_totales()
        cot.save()
        return cot

    def venta(self, precio, **extra):
        datos = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': self.cliente.pk,
                 'fecha_emision': HOY, 'fecha_vencimiento': '', 'forma_pago': 'CONTADO', 'cuotas': '1',
                 'dias_entre_cuotas': '30', 'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
                 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0',
                 'detraccion_codigo': '35', 'vendedor': '', 'almacen': self.almacen.pk, 'glosa': '',
                 'descontar_stock': 'on',
                 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
                 'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.p.pk, 'items-0-descripcion': self.p.nombre,
                 'items-0-cantidad': '2', 'items-0-precio_unitario': precio}
        datos.update(extra)
        return self.client.post(reverse('ventas:nuevo'), datos)


class AprobacionPedidoTest(Base):
    def test_pedido_bajo_tarifa_por_niveles_y_venta_desde_el_pedido(self):
        vendedor = self.usuario('vend_v160', ['ventas.emitir'])
        jefe = self.usuario('jefe_v160', ['ventas.emitir'])
        # sin reglas: un pedido a precio de lista lo aprueba el vendedor; bajo la tarifa exige «Aprobar pedidos»
        normal, bajo = self.pedido('100'), self.pedido('80')
        self.client.force_login(vendedor)
        self.client.post(reverse('ventas:cot_estado', args=[normal.pk]), {'estado': 'APROBADO'})
        normal.refresh_from_db()
        self.assertEqual(normal.estado, 'APROBADO')
        r = self.client.get(reverse('ventas:cot_detalle', args=[bajo.pk]))
        self.assertContains(r, 'Requiere aprobación')
        self.client.post(reverse('ventas:cot_estado', args=[bajo.pk]), {'estado': 'APROBADO'})
        bajo.refresh_from_db()
        self.assertEqual(bajo.estado, 'PENDIENTE')
        # con regla del motor para pedidos bajo la tarifa: aprueba el usuario de la regla
        ReglaAprobacion.objects.create(documento='PEDIDO', nivel=1, tipo='USUARIO', aprobador=jefe,
                                       aplica_a='BAJO_TARIFA')
        self.client.post(reverse('ventas:cot_estado', args=[bajo.pk]), {'estado': 'APROBADO'})
        bajo.refresh_from_db()
        self.assertEqual(bajo.estado, 'PENDIENTE')
        self.assertEqual(bajo.niveles[0]['usuario'], jefe.pk)
        self.client.force_login(jefe)
        self.client.post(reverse('ventas:cot_estado', args=[bajo.pk]), {'estado': 'APROBADO'})
        bajo.refresh_from_db()
        self.assertEqual(bajo.estado, 'APROBADO')
        self.assertIn('Bajo la tarifa', bajo.motivo_aprobacion)
        # la venta bajo la tarifa: el vendedor no puede sola; desde el pedido aprobado, sí
        from ventas.models import Venta
        self.client.force_login(vendedor)
        antes = Venta.objects.count()
        r = self.venta('80')
        self.assertContains(r, 'bajo la tarifa')
        self.assertEqual(Venta.objects.count(), antes)
        self.venta('80', cotizacion=bajo.pk)
        self.assertEqual(Venta.objects.count(), antes + 1)


class CreditoPedidoTest(Base):
    def test_pedido_que_excede_el_credito_aprobado_permite_la_venta(self):
        from ventas.models import Venta
        self.cliente.limite_credito, self.cliente.dias_credito = D('100'), 30
        self.cliente.save()
        ped = self.pedido('100', 'Crédito 30 días')
        self.client.force_login(self.admin)
        self.assertIn('Crédito', ' '.join(__import__('ventas.aprobacion_pedido', fromlist=['x']).motivos(ped)[1]))
        r = self.venta('100', forma_pago='CREDITO')
        self.assertContains(r, 'límite de crédito')
        self.client.post(reverse('ventas:cot_estado', args=[ped.pk]), {'estado': 'APROBADO'})
        ped.refresh_from_db()
        self.assertEqual(ped.estado, 'APROBADO')
        antes = Venta.objects.count()
        self.venta('100', forma_pago='CREDITO', cotizacion=ped.pk)
        self.assertEqual(Venta.objects.count(), antes + 1)


class PermisosVentaTest(Base):
    def test_devolucion_vendedor_y_reactivar(self):
        from ventas.models import Venta
        self.client.force_login(self.admin)
        self.venta('100', vendedor='Ana')
        v = Venta.objects.latest('id')
        # nota de crédito por devolución sin «devoluciones»
        u = self.usuario('nc_v160', ['ventas.emitir', 'ventas.notas'])
        self.client.force_login(u)
        r = self.venta('100', tipo_comprobante='07', doc_referencia=v.pk, motivo_nota='07')
        self.assertContains(r, 'por devolución')
        # cambio de vendedor en un pedido sin «vendedor»
        ped = self.pedido('100', vendedor='Ana')
        r = self.client.post(reverse('ventas:cot_editar', args=[ped.pk]), {
            'tipo': 'PED', 'tercero': self.cliente.pk, 'fecha': HOY, 'validez_dias': '15', 'vendedor': 'Luis',
            'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'condicion_pago': 'Contado',
            'glosa': '', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-id': ped.items.get().pk, 'items-0-producto': self.p.pk,
            'items-0-descripcion': self.p.nombre, 'items-0-cantidad': '2', 'items-0-precio_unitario': '100'})
        self.assertContains(r, 'Escoja una opción válida')  # VEN-22: el vendedor es un usuario de la lista
        ped.refresh_from_db()
        self.assertEqual(ped.vendedor, 'Ana')
        # reactivar: exige el permiso y repone la salida del almacén
        self.client.force_login(self.admin)
        self.client.post(reverse('ventas:anular', args=[v.pk]), {'motivo': 'Error de prueba'})
        v.refresh_from_db()
        self.assertEqual(v.estado, 'ANULADO')
        stock = StockAlmacen.objects.get(producto=self.p, almacen=self.almacen).cantidad
        self.client.force_login(u)
        r = self.client.post(reverse('ventas:reactivar', args=[v.pk]))
        self.assertIn(r.status_code, (403, 404))
        self.client.force_login(self.admin)
        self.client.post(reverse('ventas:reactivar', args=[v.pk]))
        v.refresh_from_db()
        self.assertEqual(v.estado, 'REGISTRADO')
        self.assertTrue(v.stock_aplicado)
        self.assertEqual(StockAlmacen.objects.get(producto=self.p, almacen=self.almacen).cantidad, stock - D('2'))
