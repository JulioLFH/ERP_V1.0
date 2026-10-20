"""Correcciones del piloto (hoja de 42 puntos del 20/10/2026), v1.41: saldos negativos, aviso general del formulario,
absorción de mano de obra y CIF por centro y puesto, asignación masiva de centro de costo, personal desde la tablet,
envases, almacén con dirección, sugerencia sin fabricados, kardex con saldo y por correo, modelo de control, stock por
almacén con almacenes vacíos y condición de pago heredada. Un escenario por clase."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Almacen, Producto, Tercero

D = Decimal
HOY = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t41', 't41@t.pe', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.almacen = Almacen.principal()


class InventarioReportesTest(Base):
    """N.º 12 saldos negativos, 39 kardex con saldo y por correo, 41 modelo de control, 42 almacenes sin stock y 30
    sugerencia de compra sin productos fabricados."""

    def test_reportes(self):
        from inventario import reportes_masivos as rm
        from core.models import Kardex
        mp = Producto.objects.create(codigo='MP-N1', nombre='Leche', unidad='LTR')
        # un negativo heredado de antes del control: salida del 05 y el ingreso del 10
        Kardex.objects.create(producto=mp, almacen=self.almacen, fecha=HOY - timedelta(days=5), tipo='SALIDA',
                              cantidad=D('50'), saldo=D('-50'), referencia='Consumo antiguo')
        mp.mover_stock(D('100'), 'Ingreso', costo=D('1.8'), almacen=self.almacen)
        enc, filas, _ = rm.saldos_negativos(q='MP-N1')
        self.assertEqual(len(filas), 1)
        f = filas[0]
        self.assertEqual((f[enc.index('Peor saldo')], f[enc.index('Movimiento que lo dejó negativo')],
                          f[enc.index('Saldo de hoy')]), (D('-50'), 'Consumo antiguo', D('50')))
        movs = list(rm.kardex_masivo(HOY - timedelta(days=10), HOY, q='MP-N1'))
        self.assertEqual([m[rm.ENCABEZADOS_KARDEX.index('Saldo')] for m in movs], [D('-50'), D('50')])
        self.user.email = 'admin@empresa.pe'
        self.user.save()
        with patch('core.correo.CorreoConfig.actual') as cfg:
            cfg.return_value.activa = False  # sin correo configurado: no rompe (queda en el registro)
            self.client.get(reverse('inventario:reporte_masivo', args=['kardex']), {'ver': '1', 'formato': 'correo'})
        r = self.client.get(reverse('inventario:reporte_masivo', args=['negativos']), {'ver': '1'})
        self.assertContains(r, 'MP-N1')
        # 41: modelo de control del almacén
        self.almacen.modelo_control = 'CICLICO'
        self.almacen.save()
        self.assertEqual(self.almacen.get_modelo_control_display(), 'Cíclico ABC')
        # 42: almacenes activos sin stock
        Almacen.objects.create(codigo='PT01', nombre='Productos terminados', direccion='Av. 1', ubigeo='150101')
        r = self.client.get(reverse('inv_stock'))
        self.assertContains(r, 'sin stock: ')
        self.assertNotIn('Productos terminados', [a.nombre for a in r.context['almacenes']])
        r = self.client.get(reverse('inv_stock'), {'vacios': '1'})
        self.assertIn('Productos terminados', [a.nombre for a in r.context['almacenes']])
        self.assertEqual(r.context['n_activos'], Almacen.objects.filter(activo=True).count())
        # 30: lo que se fabrica no aparece en la sugerencia de compra
        from core.inventario import sugerencias_compra
        pt = Producto.objects.create(codigo='PT-N1', nombre='Yogur', clase='PRODUCTO_TERMINADO', punto_reorden=D('5'))
        insumo = Producto.objects.create(codigo='MP-N2', nombre='Azúcar', clase='MATERIA_PRIMA', punto_reorden=D('5'))
        sugeridos = {f['p'] for f in sugerencias_compra()}
        self.assertIn(insumo, sugeridos)
        self.assertNotIn(pt, sugeridos)


class AlmacenYGuiaTest(Base):
    """N.º 23: el almacén normal exige dirección y ubigeo (punto de partida de las guías); los virtuales no."""

    def test_almacen_con_direccion(self):
        from .forms import AlmacenForm
        datos = {'codigo': 'PT02', 'nombre': 'PT', 'direccion': '', 'ubigeo': '', 'codigo_sunat': '0000',
                 'uso': '', 'activo': 'on', 'modelo_control': ''}
        form = AlmacenForm(data=datos)
        self.assertFalse(form.is_valid())
        self.assertIn('punto de partida', str(form.errors['direccion']))
        self.assertTrue(AlmacenForm(data={**datos, 'uso': 'TRANSITO'}).is_valid())
        self.assertTrue(AlmacenForm(data={**datos, 'direccion': 'Av. Industrial 450', 'ubigeo': '150103'}).is_valid())


class ComprasFormularioTest(Base):
    """N.º 13 aviso general arriba del formulario y n.º 22 clasificación de envases y embalajes (604)."""

    def test_aviso_y_envases(self):
        from compras.models import Compra, OrdenCompra
        from contabilidad.models import CuentaDefecto
        self.assertEqual(CuentaDefecto.mapa()['compra_ENVASE'].codigo, '6041')
        self.assertIn(('ENVASE', 'Envases y embalajes'), Compra.CLASIFICACION)
        self.assertEqual(CuentaDefecto.mapa()['compra_ENVASE'].destino_debe.codigo, '2861')
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        oc = OrdenCompra.objects.create(numero='OC01-00000941', tercero=prov)  # pendiente
        r = self.client.post(reverse('compras:nuevo'), {
            'tipo_comprobante': '01', 'serie': 'F001', 'numero': '941', 'tercero': prov.pk,
            'fecha_emision': HOY.isoformat(), 'clasificacion': 'ENVASE', 'forma_pago': 'CONTADO', 'moneda': 'PEN',
            'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0',
            'percepcion_pct': '0', 'icbper': '0', 'orden_compra': oc.pk, 'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000',
            'items-0-producto': '', 'items-0-descripcion': 'Botellas', 'items-0-cantidad': '1',
            'items-0-precio_unitario': '10', 'items-0-descuento_pct': '0', 'items-0-afectacion': ''})
        self.assertContains(r, 'Revise los campos marcados')
        self.assertContains(r, 'Orden de compra: ')


class PlanillaYAbsorcionTest(Base):
    """N.º 16 asignación masiva de centro de costo y n.º 15/17 absorción separada en mano de obra y CIF, por puesto."""

    def test_asignacion_y_absorcion(self):
        from contabilidad.models import CentroCosto
        from planillas.models import Trabajador
        cc = CentroCosto.objects.create(codigo='CC-PL', nombre='Planta', tipo='PRODUCCION')
        ts = [Trabajador.objects.create(numero_doc=f'4100000{i}', apellido_paterno='SIN', nombres=f'CENTRO{i}',
                                        fecha_ingreso=date(2024, 1, 1), sueldo=D('1500')) for i in range(2)]
        r = self.client.get(reverse('planillas:trabajadores'), {'estado': 'sin_centro'})
        self.assertEqual({t.pk for t in r.context['page_obj']} & {t.pk for t in ts}, {t.pk for t in ts})
        self.client.post(reverse('planillas:trabajadores') + '?estado=sin_centro', {
            'accion': 'asignar_centro', 'centro_costo': cc.pk, 'trabajador': [t.pk for t in ts]})
        self.assertEqual(Trabajador.objects.filter(pk__in=[t.pk for t in ts], centro_costo=cc).count(), 2)
        r = self.client.get(reverse('costos:absorcion'))
        self.assertContains(r, 'costos aplicados')
        self.assertContains(r, 'Por puesto de trabajo')


class TabletPersonalTest(Base):
    """N.º 18: la tablet declara el personal (trabajador, rol, horas) con «Agregar trabajador» y al terminar la
    orden se precarga como la cuadrilla real."""

    def test_personal_desde_la_tablet(self):
        from planillas.models import Trabajador
        from produccion import servicios
        from produccion.models import (CentroTrabajo, HojaRuta, ListaMateriales, OrdenProduccion,
                                       VersionFabricacion)
        leche = Producto.objects.create(nombre='Leche', clase='MATERIA_PRIMA')
        leche.mover_stock(D('100'), 'Saldo', costo=D('2'), almacen=self.almacen)
        yogur = Producto.objects.create(nombre='Yogur', clase='PRODUCTO_TERMINADO')
        puesto = CentroTrabajo.objects.create(codigo='P-T', nombre='Envasado', costo_hora_mo=D('8'))
        ruta = HojaRuta.objects.create(codigo='R-T', nombre='Y', estado='APROBADA')
        ruta.operaciones.create(secuencia=10, centro=puesto, horas_unidad=D('0.5'), maquinistas=1)
        receta = ListaMateriales.objects.create(producto=yogur, codigo='Y1', cantidad_base=D('1'),
                                                vigente_desde=date(2025, 1, 1))
        receta.componentes.create(producto=leche, cantidad=D('1'))
        VersionFabricacion.objects.create(producto=yogur, codigo='Y1', lista=receta, hoja=ruta,
                                          vigente_desde=date(2025, 1, 1))
        o = OrdenProduccion.objects.create(producto=yogur, lista=receta, cantidad=D('4'), almacen_insumos=self.almacen,
                                           almacen_destino=self.almacen)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        obreros = [Trabajador.objects.create(numero_doc=f'4200000{i}', apellido_paterno='OBRERO', nombres=n,
                                             fecha_ingreso=date(2024, 1, 1), sueldo=D('1500'), tipo='OBRERO')
                   for i, n in enumerate(('UNO', 'DOS', 'TRES'))]
        h = o.horas.get()
        r = self.client.get(reverse('manufactura:planta_orden', args=[o.pk]))
        self.assertContains(r, 'Agregar trabajador')
        datos = {'hora': h.pk, 'buena': '4', 'merma': '', 'horas': '2', 'operario': '', 'nota': ''}
        for n, (t, rol) in enumerate(zip(obreros, ('MAQUINISTA', 'AYUDANTE', 'AYUDANTE'))):
            datos.update({f'pp_{n}_trab': t.pk, f'pp_{n}_rol': rol, f'pp_{n}_horas': '2'})
        self.client.post(reverse('manufactura:planta_orden', args=[o.pk]), datos)
        avance = o.avances.get()
        self.assertEqual(avance.personal.count(), 3)
        self.assertIn('OBRERO, UNO', avance.operario)
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        filas = r.context['horas'][0].filas_personal
        self.assertEqual(sorted((f.get('trabajador'), f['rol']) for f in filas if f.get('trabajador')),
                         sorted([(obreros[0].pk, 'MAQUINISTA'), (obreros[1].pk, 'AYUDANTE'),
                                 (obreros[2].pk, 'AYUDANTE')]))
        self.assertContains(r, f'<option value="{obreros[2].pk}" selected>')


class PedidoHeredaCondicionTest(Base):
    """N.º 20: el pedido sin condición de pago hereda la del cliente (y la factura, la del pedido)."""

    def test_hereda(self):
        from ventas.forms import CotizacionForm
        cliente = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        cliente.dias_credito = 45
        cliente.save()
        form = CotizacionForm(data={'tipo': 'PED', 'tercero': cliente.pk, 'fecha': HOY.isoformat(),
                                    'validez_dias': '15', 'vendedor': '', 'moneda': 'PEN', 'tipo_cambio': '1',
                                    'tipo_operacion': 'GRAVADA', 'condicion_pago': '', 'glosa': ''})
        self.assertTrue(form.is_valid(), form.errors)
        ped = form.save()
        self.assertEqual(ped.condicion_pago, 'Crédito 45 días')
        from ventas.views import condicion_a_forma_pago
        self.assertEqual(condicion_a_forma_pago(ped.condicion_pago), ('CREDITO', 45))
