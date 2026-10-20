"""Compras y fechas (v1.45): redondeo del proveedor a su propia cuenta, registro info (proveedores por producto) que
usan el MRP y las solicitudes, factura repartida en varios centros de costo, y fecha contable separada de la del
documento en almacén y producción. Un escenario por clase."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, CompraItem, DistribucionCompra, RegistroInfo
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento, CentroCosto, CuentaDefecto

from .models import Almacen, Kardex, Producto, Tercero

D = Decimal
HOY = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v145', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)
        self.prov = Tercero.objects.create(numero_doc='20600000145', nombre='PROVEEDOR UNO SAC', tipo='PROVEEDOR')

    def compra(self, numero, cantidad, precio, producto=None, proveedor=None, **extra):
        c = Compra.objects.create(tercero=proveedor or self.prov, serie='F145', numero=numero, fecha_emision=HOY,
                                  ingresar_almacen=False, **extra)
        CompraItem.objects.create(documento=c, producto=producto, descripcion='Servicio de prueba',
                                  cantidad=D(cantidad), precio_unitario=D(precio))
        c.calcular_totales()
        c.save()
        return c

    def lineas(self, compra):
        centralizar_periodo(HOY.strftime('%Y%m'))
        return list(Asiento.objects.get(compra=compra).lineas.select_related('cuenta', 'centro_costo'))


class RedondeoProveedorTest(Base):
    def test_total_del_proveedor_y_diferencia_a_su_cuenta(self):
        c = self.compra('1', '3', '33.3333', clasificacion='GASTO')
        calculado = c.total
        dif = c.cuadrar_con(calculado + D('0.04'), D('1'))
        c.save()
        self.assertEqual((dif, c.total, c.ajuste_redondeo), (D('0.04'), calculado + D('0.04'), D('0.04')))
        with self.assertRaises(ValueError):
            c.cuadrar_con(calculado + D('5'), D('1'))
        c.cuadrar_con(calculado + D('0.04'), D('1'))
        c.save()
        lineas = self.lineas(c)
        perdida = CuentaDefecto.mapa()['redondeo_perdida']
        redondeo = [l for l in lineas if l.cuenta_id == perdida.pk and l.glosa.startswith('Diferencia de redondeo')]
        self.assertEqual([l.debe for l in redondeo], [D('0.04')])
        proveedor = CuentaDefecto.mapa()['proveedor']
        self.assertEqual(sum(l.haber for l in lineas if l.cuenta_id == proveedor.pk), calculado + D('0.04'))
        self.assertEqual(sum(l.debe for l in lineas), sum(l.haber for l in lineas))
        self.assertContains(self.client.get(reverse('compras:detalle', args=[c.pk])), 'diferencia de redondeo')


class RegistroInfoTest(Base):
    def test_aprende_de_las_compras_y_elige_preferido(self):
        from compras.registro_info import proveedor_y_plazo
        from compras.views import compras_views
        producto = Producto.objects.create(nombre='Tornillo RI', clase='SUMINISTRO', tiempo_entrega=3)
        compras_views.al_guardar(self.compra('10', '100', '0.50', producto=producto, clasificacion='SUMINISTRO'))
        r = RegistroInfo.objects.get(producto=producto)
        self.assertEqual((r.proveedor, r.ultimo_precio, r.compras, r.preferido), (self.prov, D('0.5'), 1, True))
        otro = Tercero.objects.create(numero_doc='20600000146', nombre='PROVEEDOR DOS SAC', tipo='PROVEEDOR')
        self.client.post(reverse('compras:registro_info_nuevo'), {
            'producto': producto.pk, 'proveedor': otro.pk, 'moneda': 'PEN', 'precio': '0.45', 'plazo_entrega': '10',
            'cantidad_minima': '500', 'preferido': 'on', 'activo': 'on'})
        r.refresh_from_db()
        self.assertFalse(r.preferido)  # un solo preferido por producto
        self.assertEqual(proveedor_y_plazo(producto), (otro, 10, D('500')))
        r = self.client.get(reverse('compras:registros_info'), {'q': 'Tornillo'})
        self.assertContains(r, 'PROVEEDOR DOS SAC')
        self.assertContains(r, 'preferido')


class DistribucionCentrosTest(Base):
    def test_reparte_el_gasto_en_el_asiento(self):
        planta = CentroCosto.objects.create(codigo='CC-145A', nombre='Planta 145', tipo='PRODUCCION')
        ventas = CentroCosto.objects.create(codigo='CC-145B', nombre='Ventas 145', tipo='VENTAS')
        c = self.compra('20', '1', '1000', clasificacion='GASTO', centro_costo=planta)
        r = self.client.post(reverse('compras:distribuir', args=[c.pk]),
                             {'centro_costo': [planta.pk, ventas.pk], 'porcentaje': ['70', '20']}, follow=True)
        self.assertContains(r, 'deben sumar 100')
        self.assertFalse(DistribucionCompra.objects.exists())
        self.client.post(reverse('compras:distribuir', args=[c.pk]),
                         {'centro_costo': [planta.pk, ventas.pk], 'porcentaje': ['60', '40']})
        self.assertEqual(c.distribucion.count(), 2)
        gasto = [l for l in self.lineas(c) if l.debe and l.centro_costo_id in (planta.pk, ventas.pk)
                 and l.cuenta.codigo.startswith('6')]
        self.assertEqual({l.centro_costo.codigo: l.debe for l in gasto}, {'CC-145A': D('600'), 'CC-145B': D('400')})


class FechaDocumentoAlmacenTest(Base):
    def test_operacion_con_fecha_del_documento(self):
        from inventario.forms import OperacionForm
        from inventario.models import Operacion, TipoOperacion
        tipo = TipoOperacion.objects.filter(clase='INGRESO').first()
        form = OperacionForm(tipo=tipo)
        self.assertIn('fecha_documento', form.fields)
        self.assertEqual(Operacion._meta.get_field('fecha').verbose_name, 'Fecha contable')
        r = self.client.get(reverse('inventario:nueva') + f'?tipo={tipo.codigo}')
        self.assertContains(r, 'Fecha del documento')


class FechaContableProduccionTest(Base):
    def test_termina_con_otra_fecha_contable(self):
        from produccion import servicios
        from produccion.models import ListaMateriales, OrdenProduccion
        almacen = Almacen.principal()
        insumo = Producto.objects.get(codigo='P003')
        kit = Producto.objects.create(nombre='Kit 145', clase='PRODUCTO_TERMINADO', precio_venta=D('500'))
        receta = ListaMateriales.objects.create(producto=kit, codigo='V1', cantidad_base=D('1'))
        receta.componentes.create(producto=insumo, cantidad=D('1'))
        o = OrdenProduccion.objects.create(producto=kit, lista=receta, cantidad=D('2'), fecha=HOY,
                                           almacen_insumos=almacen, almacen_destino=almacen, creado_por=self.admin)
        servicios.explotar(o)
        servicios.confirmar(o, self.admin)
        ayer = HOY - timedelta(days=1)
        with self.assertRaises(servicios.ErrorProduccion):
            servicios.terminar(o, self.admin, D('2'), {}, {}, fecha_contable=HOY + timedelta(days=2))
        servicios.terminar(o, self.admin, D('2'), {}, {}, fecha_contable=ayer.isoformat())
        o.refresh_from_db()
        self.assertEqual((o.fecha_fin, timezone.localtime(o.terminado_en).date()), (ayer, HOY))
        self.assertEqual((o.operacion.fecha, o.operacion.fecha_documento), (ayer, HOY))
        self.assertTrue(Kardex.objects.filter(producto=kit, fecha=ayer, tipo='ENTRADA').exists())
