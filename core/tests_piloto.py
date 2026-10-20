"""Correcciones del piloto del 19/10/2026 (Checklist_Ceiba_v1.34.0, hoja «Correcciones piloto») y del diagnóstico de
datos. Cada clase reproduce el caso tal como se describió. Un escenario por clase."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.forms import CompraForm
from compras.models import OrdenCompra, OrdenCompraItem
from inventario import servicios as inv
from inventario.models import Operacion, TipoOperacion

from .models import Almacen, Producto, Tercero
from .sustentos import adjuntar
from .utils import faltantes_stock

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

    def operacion(self, codigo, producto, cantidad, fecha, costo=None, **campos):
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo=codigo), fecha=fecha, **campos)
        op.items.create(producto=producto, cantidad=D(cantidad), costo_unitario=D(costo) if costo else None)
        if op.tipo.requiere_sustento:
            adjuntar(op, SimpleUploadedFile('acta.pdf', b'%PDF-1.4 acta', content_type='application/pdf'))
        return op


class KardexFechaAtrasadaTest(Base):
    """MAN-08: un consumo con fecha anterior al ingreso no puede dejar el kardex en negativo."""

    def test_consumo_antes_del_ingreso_se_rechaza(self):
        mp = Producto.objects.create(codigo='MP-001', nombre='Leche fresca', unidad='LTR')
        ingreso = self.operacion('AJ_ING', mp, '100', HOY - timedelta(days=9), costo='1.80',
                                 almacen_destino=self.almacen)
        inv.confirmar(ingreso, self.user)
        consumo = self.operacion('CONS_INT', mp, '50', HOY - timedelta(days=14), almacen_origen=self.almacen)
        errores = inv.errores_confirmacion(consumo)
        self.assertTrue(any('Stock insuficiente' in e and 'pueden salir 0.00' in e for e in errores), errores)
        with self.assertRaises(inv.ErrorOperacion):
            inv.confirmar(consumo, self.user)
        # con fecha posterior al ingreso sí procede
        consumo.fecha = HOY - timedelta(days=5)
        consumo.save()
        inv.confirmar(consumo, self.user)
        self.assertEqual(mp.stock_en(self.almacen), D('50'))
        # y ya no se puede meter otra salida atrasada que deje en negativo un día intermedio
        self.assertTrue(faltantes_stock([(mp, D('60'))], self.almacen, fecha=HOY - timedelta(days=7)))
        self.assertEqual(faltantes_stock([(mp, D('50'))], self.almacen, fecha=HOY - timedelta(days=7)), [])


class OrdenCompraSinAprobarTest(Base):
    """COM-01: no se recibe ni se factura una orden de compra pendiente de aprobación."""

    def test_recepcion_y_factura_exigen_oc_aprobada(self):
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        p = Producto.objects.get(codigo='P001')
        oc = OrdenCompra.objects.create(numero='OC01-00000501', tercero=prov)  # pendiente
        OrdenCompraItem.objects.create(documento=oc, producto=p, descripcion=p.nombre, cantidad=5,
                                       precio_unitario=D('100'))
        recepcion = self.operacion('REC_COMPRA', p, '5', HOY, costo='100', orden_compra=oc,
                                   almacen_destino=self.almacen)
        self.assertTrue(any('solo se recibe mercadería de órdenes aprobadas' in e
                            for e in inv.errores_confirmacion(recepcion)))
        datos = {'tipo_comprobante': '01', 'serie': 'F001', 'numero': '501', 'tercero': prov.pk, 'fecha_emision': HOY,
                 'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1',
                 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
                 'icbper': '0', 'orden_compra': oc.pk}
        form = CompraForm(data=datos)
        self.assertFalse(form.is_valid())
        self.assertIn('solo se facturan órdenes aprobadas', str(form.errors['orden_compra']))
        oc.estado = 'APROBADO'
        oc.save()
        self.assertEqual([e for e in inv.errores_confirmacion(recepcion) if 'aprobad' in e], [])
        self.assertNotIn('orden_compra', CompraForm(data=datos).errors)


class VencimientoCreditoTest(Base):
    """VEN-01: factura a crédito sin fecha de vencimiento = emisión + días de crédito."""

    def test_credito_toma_los_dias_del_cliente(self):
        from ventas.forms import VentaForm
        cliente = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        cliente.dias_credito = 30
        cliente.save()
        base = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': cliente.pk, 'fecha_emision': HOY,
                'forma_pago': 'CREDITO', 'cuotas': '1', 'dias_entre_cuotas': '30', 'moneda': 'PEN', 'tipo_cambio': '1',
                'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
                'icbper': '0', 'almacen': self.almacen.pk}
        form = VentaForm(data={**base, 'fecha_vencimiento': ''})
        form.is_valid()
        self.assertEqual(form.cleaned_data['fecha_vencimiento'], HOY + timedelta(days=30))
        form = VentaForm(data={**base, 'fecha_vencimiento': HOY.isoformat()})
        self.assertFalse(form.is_valid())
        self.assertIn('posterior a la emisión', str(form.errors['fecha_vencimiento']))
        cliente.dias_credito = 0  # sin días de crédito en el cliente: los días entre cuotas
        cliente.save()
        form = VentaForm(data={**base, 'fecha_vencimiento': '', 'dias_entre_cuotas': '45'})
        form.is_valid()
        self.assertEqual(form.cleaned_data['fecha_vencimiento'], HOY + timedelta(days=45))


class VersionFabricacionTest(Base):
    """MAN-02: la receta cargada por Excel crea su versión; sin versión, la orden lo explica."""

    def test_receta_por_excel_avisa_y_la_orden_explica(self):
        from produccion.models import VersionFabricacion
        from .carga_masiva import _cargar_maestro, _fila_receta
        pt = Producto.objects.create(codigo='PT-901', nombre='Yogur fresa 1 L', clase='PRODUCTO_TERMINADO',
                                     unidad='LTR')
        insumo = Producto.objects.create(codigo='MP-901', nombre='Fresa', clase='MATERIA_PRIMA', unidad='KGM')
        r = self.client.get(reverse('manufactura:orden_nueva') + f'?producto={pt.pk}')
        self.assertContains(r, 'no tiene versión de fabricación vigente')
        fila = _fila_receta({'producto': 'PT-901', 'receta': 'R1', 'cantidad_base': '1', 'insumo': 'MP-901',
                             'cantidad': '0.2'}, False)
        resumen = _cargar_maestro('recetas', [fila])
        self.assertIn('1 producto(s) aún sin versión de fabricación', resumen)
        self.assertIn('PT-901', resumen)
        # el enlace del aviso abre la versión con el producto y la receta ya elegidos
        r = self.client.get(reverse('manufactura:orden_nueva') + f'?producto={pt.pk}')
        self.assertContains(r, 'no tiene versión de fabricación vigente')
        r = self.client.get(reverse('manufactura:version_nueva') + f'?producto={pt.pk}')
        self.assertEqual(r.context['form'].initial.get('producto'), str(pt.pk))
        from produccion.models import ListaMateriales
        lista = ListaMateriales.objects.get(producto=pt)
        VersionFabricacion.objects.create(producto=pt, codigo='1', lista=lista)
        r = self.client.get(reverse('manufactura:orden_nueva') + f'?producto={pt.pk}')
        self.assertNotContains(r, 'no tiene versión de fabricación vigente')


class CargaMasivaTest(Base):
    """PLAT-04 y n.º 86: días laborables en cualquier formato y carga masiva de activos fijos."""

    def test_dias_laborables_y_activos(self):
        from .carga_masiva import ErrorFila, _dias_laborables, _fila_activo, cargar
        for valor in (123456, 123456.0, D('123456.00'), '1,2,3,4,5,6', '1-6', ''):
            self.assertEqual(_dias_laborables(valor), '123456', valor)
        self.assertEqual(_dias_laborables('1-5,7'), '123457')
        with self.assertRaises(ErrorFila):
            _dias_laborables('lunes')
        from activos.models import ActivoFijo, CategoriaActivo
        from contabilidad.models import CentroCosto
        cat = CategoriaActivo.objects.filter(deprecia=True).first()
        cc = CentroCosto.objects.filter(activo=True).first()
        fila = {'nombre': 'Pasteurizador 5000 L', 'categoria': cat.codigo, 'centro_costo': cc.codigo,
                'fecha_adquisicion': '15/03/2021', 'valor': '185000', 'vida_util_meses': '120',
                'dep_inicial': '101750', 'dep_inicial_hasta': '30/09/2026', 'serie': 'TP-88231'}
        datos = _fila_activo(fila, False)
        with self.assertRaises(ErrorFila):  # con depreciación pero sin «depreciado hasta»
            _fila_activo({**fila, 'dep_inicial_hasta': ''}, False)
        with self.assertRaises(ErrorFila):
            _fila_activo({**fila, 'dep_inicial': '200000'}, False)
        resumen = cargar('activos', [datos], self.user,
                         adjunto=(b'PK excel', 'activos.xlsx'))
        self.assertIn('1 activos fijos cargados', resumen)
        a = ActivoFijo.objects.get(serie='TP-88231')
        self.assertEqual((a.origen, a.valor, a.dep_inicial, a.centro_costo, a.fecha_uso.isoformat()),
                         ('SALDO_INICIAL', D('185000'), D('101750'), cc, '2021-03-15'))
        self.assertTrue(a.codigo)
        r = self.client.get(reverse('activos:lista'))
        self.assertContains(r, 'Importar desde Excel')


class VerificacionesTest(Base):
    """Puntos «Por verificar» del checklist: límite de crédito, lanzamiento masivo del MRP y UIT 2026."""

    def test_limite_de_credito_mrp_masivo_y_uit(self):
        from planillas.models import Parametro
        self.assertEqual(Parametro.objects.get(anio=2026).uit, D('5500'))  # n.º 9 del diagnóstico
        # límite de crédito (n.º 14): una venta al crédito que lo supera no se registra
        from ventas.models import Venta
        cliente = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        cliente.limite_credito, cliente.dias_credito = D('100'), 30
        cliente.save()
        p = Producto.objects.get(codigo='P004')
        antes = Venta.objects.count()
        r = self.client.post(reverse('ventas:nuevo'), {
            'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': cliente.pk, 'fecha_emision': HOY,
            'fecha_vencimiento': '', 'forma_pago': 'CREDITO', 'cuotas': '1', 'dias_entre_cuotas': '30',
            'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0',
            'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '',
            'almacen': self.almacen.pk, 'glosa': '', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000', 'items-0-producto': p.pk,
            'items-0-descripcion': p.nombre, 'items-0-cantidad': '1', 'items-0-precio_unitario': '500'})
        self.assertContains(r, 'límite S/ 100.00')
        self.assertEqual(Venta.objects.count(), antes)
        # MRP (n.º 77 y 82): una mercadería de reventa con demanda genera propuesta de COMPRA a su proveedor, y
        # todas las propuestas seleccionadas pasan de un solo clic a una solicitud de pedido (sin reglas de
        # liberación queda liberada) que Compras convierte en orden de compra
        from contabilidad.models import CentroCosto
        from produccion.models import PlanDemanda, PropuestaMRP
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        merc = [Producto.objects.create(codigo=f'ME-90{i}', nombre=f'Mercadería {i}', clase='MERCADERIA',
                                        proveedor=prov, precio_compra=D('10')) for i in (1, 2)]
        for m in merc:
            PlanDemanda.objects.create(producto=m, cantidad=D('40'), fecha=HOY + timedelta(days=10))
        self.client.post(reverse('manufactura:mrp'), {'accion': 'ejecutar',
                                                      'horizonte': (HOY + timedelta(days=30)).isoformat()})
        propuestas = PropuestaMRP.objects.filter(producto__in=merc)
        self.assertEqual({(p.producto.codigo, p.tipo) for p in propuestas}, {('ME-901', 'COMPRAR'),
                                                                           ('ME-902', 'COMPRAR')})
        self.client.post(reverse('manufactura:mrp'), {
            'accion': 'comprar', 'propuesta': [p.pk for p in propuestas],
            'centro_costo': CentroCosto.objects.filter(activo=True).first().pk})
        self.assertTrue(all(p.convertida for p in PropuestaMRP.objects.filter(producto__in=merc)))
        from compras.models import LineaSolicitud
        lineas = LineaSolicitud.objects.filter(producto__in=merc)
        self.assertEqual((lineas.count(), lineas.first().solicitud.estado), (2, 'LIBERADA'))
        self.client.post(reverse('compras:solped_bandeja'), {'linea': [l.pk for l in lineas], 'convertir': 'oc'})
        self.assertEqual(OrdenCompraItem.objects.filter(producto__in=merc).count(), 2)
