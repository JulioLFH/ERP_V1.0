"""Fallas del flujo integral de punta a punta del 20/10/2026 (pedido → MRP → producción → venta → contabilidad):
COM-03, COM-04, VEN-09, VEN-10, LOG-03 y CON-05. Un escenario por clase."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, OrdenCompra, OrdenCompraItem
from inventario import servicios as inv
from inventario.models import Operacion, TipoOperacion
from ventas.models import Cotizacion, CotizacionItem, Venta

from .models import Almacen, Producto, Tercero

D = Decimal
HOY = timezone.localdate()


class Base(TestCase):
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
        self.almacen = Almacen.principal()
        self.prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        self.cliente = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()

    def oc_de_leche(self, cantidad='160.56'):
        self.leche = Producto.objects.create(codigo='MP-LCH', nombre='Leche fresca', clase='MATERIA_PRIMA',
                                             unidad='LTR')
        oc = OrdenCompra.objects.create(numero='OC01-00000903', tercero=self.prov, estado='APROBADO')
        OrdenCompraItem.objects.create(documento=oc, producto=self.leche, descripcion='Leche fresca',
                                       cantidad=D(cantidad), precio_unitario=D('1.80'))
        return oc

    def pedido(self, condicion='', estado='PENDIENTE'):
        p = Producto.objects.get(codigo='P004')
        ped = Cotizacion.objects.create(tipo='PED', numero='PD01-00000901', tercero=self.cliente,
                                        condicion_pago=condicion, estado=estado)
        CotizacionItem.objects.create(documento=ped, producto=p, descripcion=p.nombre, cantidad=D('200'),
                                      precio_unitario=D('12'))
        return ped


class RecepcionConDecimalesTest(Base):
    """COM-03: la recepción de una OC de 160.56 L se prellenaba con 160.560000 y el formulario la rechazaba."""

    def test_pendiente_con_dos_decimales(self):
        oc = self.oc_de_leche()
        op = Operacion(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), orden_compra=oc)
        fila = inv.items_desde_origen(op)[0]
        self.assertEqual(fila['cantidad'], D('160.56'))
        self.assertEqual(fila['cantidad'].as_tuple().exponent, -2)
        r = self.client.get(reverse('inventario:nueva') + f'?tipo=REC_COMPRA&oc={oc.pk}')
        self.assertContains(r, '160.56')
        self.assertNotContains(r, '160.560000')


class ClasificacionMateriaPrimaTest(Base):
    """COM-04: la factura de una OC de materia prima se clasifica «Materia prima e insumos» (cuenta 602)."""

    def test_clasificacion_y_cuenta(self):
        from contabilidad.models import CuentaDefecto
        mapa = CuentaDefecto.mapa()
        self.assertTrue(mapa['compra_MATERIA_PRIMA'].codigo.startswith('602'))
        self.assertTrue(mapa['compra_SUMINISTRO'].codigo.startswith('603'))
        oc = self.oc_de_leche()
        r = self.client.get(reverse('compras:nuevo') + f'?oc={oc.pk}')
        self.assertEqual(r.context['form'].initial['clasificacion'], 'MATERIA_PRIMA')
        self.assertContains(r, 'Materia prima e insumos')
        servicio = Producto.objects.create(nombre='Flete', clase='SERVICIO')
        OrdenCompraItem.objects.create(documento=oc, producto=servicio, descripcion='Flete', cantidad=D('1'),
                                       precio_unitario=D('50'))
        self.assertEqual(Compra.clasificacion_de(oc.items.select_related('producto')), 'MATERIA_PRIMA')


class PedidoAlCreditoTest(Base):
    """VEN-09: la venta emitida desde un pedido al crédito precargaba «Contado»."""

    def test_condicion_del_pedido(self):
        from ventas.views import condicion_a_forma_pago
        self.assertEqual(condicion_a_forma_pago('Crédito 30 días'), ('CREDITO', 30))
        self.assertEqual(condicion_a_forma_pago('CREDITO A 45 DIAS'), ('CREDITO', 45))
        self.assertEqual(condicion_a_forma_pago('Contado contra entrega'), ('CONTADO', 0))
        self.cliente.dias_credito = 15
        self.assertEqual(condicion_a_forma_pago('', self.cliente), ('CREDITO', 15))
        self.assertEqual(condicion_a_forma_pago('crédito', self.cliente), ('CREDITO', 15))
        ped = self.pedido('Crédito 30 días', estado='APROBADO')
        r = self.client.get(reverse('ventas:nuevo') + f'?cot={ped.pk}')
        inicial = r.context['form'].initial
        self.assertEqual((inicial['forma_pago'], inicial['fecha_vencimiento']), ('CREDITO', HOY + timedelta(days=30)))


class PedidoAprobadoEditadoTest(Base):
    """VEN-10: un pedido aprobado que se modifica vuelve a «Pendiente»; con factura emitida ya no se edita."""

    def test_vuelve_a_aprobacion(self):
        ped = self.pedido(estado='APROBADO')
        item = ped.items.get()
        r = self.client.get(reverse('ventas:cot_editar', args=[ped.pk]))
        self.assertContains(r, 'volverá a «Pendiente»')
        datos = {'tipo': 'PED', 'tercero': self.cliente.pk, 'fecha': HOY.isoformat(), 'validez_dias': '15',
                 'vendedor': '', 'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
                 'condicion_pago': '', 'glosa': '', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
                 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000', 'items-0-id': item.pk,
                 'items-0-producto': item.producto_id, 'items-0-descripcion': item.descripcion,
                 'items-0-cantidad': '400', 'items-0-precio_unitario': '12', 'items-0-descuento_pct': '0',
                 'items-0-afectacion': ''}
        r = self.client.post(reverse('ventas:cot_editar', args=[ped.pk]), datos)
        self.assertRedirects(r, reverse('ventas:cot_detalle', args=[ped.pk]))
        ped.refresh_from_db()
        self.assertEqual((ped.estado, ped.items.get().cantidad), ('PENDIENTE', D('400')))
        # con un comprobante emitido desde el pedido ya no se edita
        Venta.objects.create(tipo_comprobante='01', serie='F001', numero='901', tercero=self.cliente,
                             fecha_emision=HOY, cotizacion=ped)
        r = self.client.get(reverse('ventas:cot_editar', args=[ped.pk]))
        self.assertRedirects(r, reverse('ventas:cot_detalle', args=[ped.pk]))


class PuntoDePartidaTest(Base):
    """LOG-03: almacén sin dirección (PT01): la guía toma la del almacén principal y lo avisa."""

    def test_partida_del_principal(self):
        self.almacen.direccion, self.almacen.ubigeo = 'Av. Industrial 450, Ate', '150103'
        self.almacen.save()
        pt01 = Almacen.objects.create(codigo='PT01', nombre='Productos terminados')
        from logistica.views import punto_partida
        from .models import Empresa
        self.assertEqual(punto_partida(pt01, Empresa.actual()), ('150103', 'Av. Industrial 450, Ate'))
        p = Producto.objects.get(codigo='P004')
        v = Venta.objects.create(tipo_comprobante='01', serie='F001', numero='902', tercero=self.cliente,
                                 fecha_emision=HOY, almacen=pt01, descontar_stock=False)
        v.items.create(producto=p, descripcion=p.nombre, cantidad=D('2'), precio_unitario=D('10'))
        r = self.client.get(reverse('logistica:nueva') + f'?venta={v.pk}', follow=True)
        self.assertEqual(r.context['form'].initial['partida_direccion'], 'Av. Industrial 450, Ate')
        self.assertContains(r, 'no tiene dirección o ubigeo')
        # un almacén nuevo propone la dirección del principal
        r = self.client.get(reverse('almacen_nuevo'))
        self.assertEqual(r.context['form'].initial.get('direccion'), 'Av. Industrial 450, Ate')


class ResultadoPorLineaTest(Base):
    """CON-05: en cada línea el costo de ventas es el costo de lo vendido (69); compras y producción del periodo que
    aún no se venden van a «Sin línea / producción en curso» y el total sigue siendo el oficial."""

    def test_margen_por_linea(self):
        from contabilidad import reportes
        from contabilidad.centralizar import Borrador
        from contabilidad.models import Asiento, CentroBeneficio, CuentaContable

        def cuenta(prefijo):
            return CuentaContable.objects.filter(codigo__startswith=prefijo, imputable=True).order_by('codigo').first()
        lac = CentroBeneficio.objects.create(codigo='LAC', nombre='Lácteos')
        b = Borrador(Asiento(fecha=HOY, libro='05', origen='MANUAL', glosa='Prueba por línea'))
        b.add(cuenta('1212'), debe=D('500'))
        b.add(cuenta('70'), haber=D('500'), centro_beneficio=lac)  # venta
        b.add(cuenta('69'), debe=D('300'), centro_beneficio=lac)  # costo de lo vendido
        b.add(cuenta('21'), haber=D('300'))
        b.add(cuenta('60'), debe=D('200'))  # compra de leche, sin línea
        b.add(cuenta('4212'), haber=D('200'))
        b.add(cuenta('21'), debe=D('450'))  # producción almacenada, con la línea del producto
        b.add(cuenta('71'), haber=D('450'), centro_beneficio=lac)
        b.grabar()
        periodo = HOY.strftime('%Y%m')
        r = self.client.get(reverse('contabilidad:resultados_linea') + f'?desde={periodo[:4]}-{periodo[4:]}'
                                                                         f'&hasta={periodo[:4]}-{periodo[4:]}')
        tabla = {t['rubro']: t for t in r.context['tabla']}
        columnas = [pk for pk, _ in r.context['columnas']]
        col = columnas.index(lac.pk)
        self.assertEqual(tabla['Costo de ventas']['valores'][col], D('-300'))
        self.assertEqual(tabla['UTILIDAD BRUTA']['valores'][col], D('200'))
        oficial = {n: v for n, v, _ in reportes.estado_resultados(periodo, periodo)[0]}
        for rubro in ('Costo de ventas', 'UTILIDAD BRUTA', 'RESULTADO DEL EJERCICIO'):
            self.assertEqual(sum(tabla[rubro]['valores'], D('0')), oficial[rubro], rubro)
