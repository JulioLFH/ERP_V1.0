"""v1.20: tercerización (maquila), calidad (plan, inspección, cuarentena) y mantenimiento (preventivos, repuestos)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from core.models import Almacen, Producto, Tercero

from . import planta, servicios
from .models import (CentroTrabajo, Equipo, InspeccionCalidad, ListaMateriales, OrdenMantenimiento,
                     OrdenProduccion, ParametroCalidad, PlanMantenimiento, VersionFabricacion)

D = Decimal
HOY = date.today()


class PlantaTest(TestCase):
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
        self.insumo = Producto.objects.get(codigo='P003')
        self.kit = Producto.objects.create(nombre='Kit maquilado', clase='PRODUCTO_TERMINADO', precio_venta=D('500'))
        receta = ListaMateriales.objects.create(producto=self.kit, codigo='V1', cantidad_base=D('1'))
        receta.componentes.create(producto=self.insumo, cantidad=D('1'))
        VersionFabricacion.objects.create(producto=self.kit, codigo='V1', lista=receta)
        self.receta = receta
        self.maquilador = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100130204',
                                                 nombre='MAQUILAS DEL SUR SAC')

    def test_maquila_envia_materiales_y_suma_el_servicio(self):
        stock_inicial = self.insumo.stock_en(self.almacen)
        o = OrdenProduccion.objects.create(producto=self.kit, lista=self.receta, cantidad=D('2'), fecha=HOY,
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen,
                                           maquilador=self.maquilador, costo_servicio=D('100'), creado_por=self.user)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        r = self.client.post(reverse('manufactura:orden_maquila', args=[o.pk]))
        self.assertEqual(r.status_code, 302)
        o.refresh_from_db()
        destino = Almacen.objects.get(uso='TERCEROS', tercero=self.maquilador)
        self.assertEqual(o.almacen_insumos, destino)
        self.assertEqual(self.insumo.stock_en(destino), D('2'))
        self.assertEqual(self.insumo.stock_en(self.almacen), stock_inicial - 2)
        servicios.terminar(o, self.user, D('2'), {}, {})
        o.refresh_from_db()
        # costo = 2 insumos + 100 del servicio, entre 2 kits
        self.assertEqual(o.costo_unitario, ((o.costo_materiales + D('100')) / 2).quantize(D('0.0001')))
        self.assertEqual(self.insumo.stock_en(destino), 0)
        self.assertEqual(centralizar_periodo(HOY.strftime('%Y%m'))['errores'], [])

    def test_inspeccion_rechazada_va_a_cuarentena(self):
        ParametroCalidad.objects.create(producto=self.insumo, nombre='Humedad', unidad='%', minimo=D('0'),
                                        maximo=D('14'))
        r = self.client.post(reverse('manufactura:inspeccion_nueva'), {
            'tipo': 'RECEPCION', 'fecha': HOY.isoformat(), 'producto': self.insumo.pk, 'lote': '',
            'cantidad': '1', 'almacen': self.almacen.pk})
        insp = InspeccionCalidad.objects.get()
        self.assertRedirects(r, reverse('manufactura:inspeccion', args=[insp.pk]))
        res = insp.resultados.get()
        with self.assertRaises(planta.ErrorPlanta):  # 15.2 % está fuera de especificación: no se aprueba
            planta.registrar_resultados(insp, {res.pk: (D('15.2'), '', None)}, 'APROBADO', '', self.user)
        planta.registrar_resultados(insp, {res.pk: (D('15.2'), '', None)}, 'RECHAZADO', 'Humedad alta', self.user)
        res.refresh_from_db()
        self.assertIs(res.conforme, False)
        antes = self.insumo.stock_en(self.almacen)
        planta.enviar_a_cuarentena(insp, D('1'), self.user)
        self.assertEqual(self.insumo.stock_en(self.almacen), antes - 1)
        self.assertEqual(self.insumo.stock_en(Almacen.especial('DESTRUCCION')), D('1'))

    def test_mantenimiento_preventivo_con_repuestos(self):
        centro = CentroTrabajo.objects.create(codigo='MOL', nombre='Molino')
        eq = Equipo.objects.create(codigo='EQ-01', nombre='Molino de martillos', centro=centro)
        plan = PlanMantenimiento.objects.create(equipo=eq, tarea='Cambio de martillos', frecuencia_dias=30,
                                                ultima_fecha=HOY - timedelta(days=29))
        creadas = planta.programar_preventivos(7, self.user)
        self.assertEqual(len(creadas), 1)
        self.assertEqual(planta.programar_preventivos(7, self.user), [])  # no duplica la abierta
        o = creadas[0]
        self.client.post(reverse('manufactura:orden_mant', args=[o.pk]), {
            'accion': 'repuesto', 'producto': self.insumo.pk, 'cantidad': '1', 'almacen': self.almacen.pk})
        antes = self.insumo.stock_en(self.almacen)
        self.client.post(reverse('manufactura:orden_mant', args=[o.pk]), {
            'accion': 'cerrar', 'trabajo_realizado': 'Se cambiaron los martillos', 'responsable': 'Técnico',
            'horas_parada': '3', 'costo_mano_obra': '50', 'costo_servicios': '0'})
        o.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual((o.estado, plan.ultima_fecha), ('CERRADA', HOY))
        self.assertEqual(self.insumo.stock_en(self.almacen), antes - 1)
        self.assertGreater(o.costo_total, D('50'))
        self.assertEqual(centralizar_periodo(HOY.strftime('%Y%m'))['errores'], [])

    def test_pantallas(self):
        for nombre in ('inspecciones', 'inspeccion_nueva', 'planes_calidad', 'equipos', 'equipo_nuevo', 'ordenes_mant',
                       'orden_mant_nueva'):
            self.assertEqual(self.client.get(reverse(f'manufactura:{nombre}')).status_code, 200, nombre)
        self.assertEqual(self.client.get(reverse('manufactura:plan_calidad', args=[self.insumo.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('ubicaciones')).status_code, 200)
