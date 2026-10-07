"""Costeo de planta (v1.25): máquina y CIF por horas máquina, horas hombre por maquinista o ayudante al costo real
del trabajador, subproductos y coproductos (NIC 2 párr. 14) y fecha y hora de término."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Almacen, Producto
from planillas.models import Trabajador

from . import servicios
from .models import CentroTrabajo, HojaRuta, ListaMateriales, OrdenProduccion, VersionFabricacion

D = Decimal


class CostosPlantaTest(TestCase):
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
        self.leche = Producto.objects.create(nombre='Leche', clase='MATERIA_PRIMA')
        self.leche.mover_stock(D('100'), 'Saldo de prueba', costo=D('10'), almacen=self.almacen)
        self.queso = Producto.objects.create(nombre='Queso', clase='PRODUCTO_TERMINADO')
        self.suero = Producto.objects.create(nombre='Suero', clase='PRODUCTO_TERMINADO')
        self.crema = Producto.objects.create(nombre='Crema', clase='PRODUCTO_TERMINADO')
        self.puesto = CentroTrabajo.objects.create(codigo='PRN', nombre='Prensa', costo_hora_mo=D('10'),
                                                   costo_hora_maquina=D('4'), costo_hora_cif=D('6'))
        ruta = HojaRuta.objects.create(codigo='R-Q', nombre='Queso', estado='APROBADA')
        ruta.operaciones.create(secuencia=10, centro=self.puesto, descripcion='Prensado', horas_unidad=D('0.5'),
                                maquinistas=1, ayudantes=1)
        self.receta = ListaMateriales.objects.create(producto=self.queso, codigo='Q1', cantidad_base=D('1'),
                                                     vigente_desde=date(2025, 1, 1))
        self.receta.componentes.create(producto=self.leche, cantidad=D('1'))
        self.receta.subproductos.create(producto=self.suero, cantidad=D('0.5'), tipo='SUBPRODUCTO',
                                        valor_unitario=D('2'))
        self.receta.subproductos.create(producto=self.crema, cantidad=D('0.2'), tipo='COPRODUCTO',
                                        participacion=D('25'))
        VersionFabricacion.objects.create(producto=self.queso, codigo='Q1', lista=self.receta, hoja=ruta,
                                          vigente_desde=date(2025, 1, 1))
        self.operario = Trabajador.objects.create(numero_doc='40000099', apellido_paterno='QUISPE', nombres='ANA',
                                                  fecha_ingreso=date(2024, 1, 1), sueldo=D('2400'), tipo='OBRERO')

    def orden(self):
        o = OrdenProduccion.objects.create(producto=self.queso, lista=self.receta, cantidad=D('4'),
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        return o

    def test_costo_hora_del_trabajador_incluye_cargas_y_beneficios(self):
        r = D('2400')
        mensual = r * D('1.09') + r * 2 / 12 * D('1.09') + r * (1 + D(1) / 6) / 12 + r / 12 * D('1.09')
        self.assertEqual(self.operario.costo_hora(), (mensual / 240).quantize(D('0.0001')))
        self.operario.regimen = 'MICRO'
        self.assertLess(self.operario.costo_hora(), (mensual / 240).quantize(D('0.0001')))

    def test_horas_maquina_horas_hombre_y_subproductos(self):
        o = self.orden()
        h = o.horas.get()
        self.assertEqual((h.horas_plan, h.maquinistas, h.ayudantes), (D('2'), 1, 1))
        self.assertEqual(o.salidas.count(), 2)
        personal = {h.pk: [{'rol': 'MAQUINISTA', 'trabajador': self.operario.pk, 'horas': D('2')},
                           {'rol': 'AYUDANTE', 'trabajador': None, 'horas': D('2')}]}
        servicios.terminar(o, self.user, D('4'), {}, {}, personal=personal)
        o.refresh_from_db()
        tarifa = self.operario.costo_hora()
        mo = (2 * tarifa).quantize(D('0.01')) + 20
        self.assertEqual((o.costo_materiales, o.costo_maquina, o.costo_cif, o.costo_mano_obra),
                         (D('40.00'), D('8.00'), D('12.00'), mo))
        self.assertEqual({p.rol: p.costo_hora for p in o.horas.get().personal.all()},
                         {'MAQUINISTA': tarifa, 'AYUDANTE': D('10')})
        conjunto = D('40') + 8 + 12 + mo
        suero, crema = o.salidas.get(producto=self.suero), o.salidas.get(producto=self.crema)
        self.assertEqual((suero.cantidad_real, suero.costo_unitario), (D('2'), D('2')))  # 0.5 × 4 a S/ 2
        self.assertEqual(crema.costo_unitario, ((conjunto - 4) * D('0.25') / D('0.8')).quantize(D('0.0001')))
        principal = conjunto - 4 - crema.cantidad_real * crema.costo_unitario
        self.assertEqual(o.costo_unitario, (principal / 4).quantize(D('0.0001')))
        self.assertEqual((Producto.objects.get(pk=self.suero.pk).stock, Producto.objects.get(pk=self.crema.pk).stock),
                         (D('2'), D('0.8')))
        self.assertIsNotNone(o.terminado_en)
        v = {x.tipo: x.monto for x in o.variaciones.all()}
        self.assertEqual(sum(v.values()), o.costo_total - (o.costo_estandar_unit * o.cantidad_producida).quantize(
            D('0.01')))
        self.assertContains(self.client.get(reverse('manufactura:orden', args=[o.pk])), 'Subproductos y coproductos')
        self.assertContains(self.client.get(reverse('costos:hoja', args=[self.receta.pk])), 'Costo del producto principal')

    def test_terminar_desde_la_pantalla_con_horas_hombre_y_fecha_hora(self):
        o = self.orden()
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        self.assertContains(r, 'type="datetime-local"')
        self.assertContains(r, 'QUISPE')
        h = o.horas.get()
        s = o.salidas.get(producto=self.suero)
        datos = {'cantidad_producida': '4', f'hora_{h.pk}': '3', 'fecha': '',
                 f'hh_{h.pk}_0_rol': 'MAQUINISTA', f'hh_{h.pk}_0_trab': self.operario.pk, f'hh_{h.pk}_0_horas': '3',
                 f'hh_{h.pk}_1_rol': 'AYUDANTE', f'hh_{h.pk}_1_trab': '', f'hh_{h.pk}_1_horas': '1.5',
                 f'hh_{h.pk}_2_rol': 'AYUDANTE', f'hh_{h.pk}_2_trab': '', f'hh_{h.pk}_2_horas': '',
                 f'salida_{s.pk}': '1'}
        self.client.post(reverse('manufactura:orden_terminar', args=[o.pk]), datos)
        o.refresh_from_db()
        self.assertEqual(o.estado, 'TERMINADA')
        self.assertEqual((o.costo_maquina, o.costo_cif), (D('12.00'), D('18.00')))  # 3 h máquina
        self.assertEqual(sorted(o.horas.get().personal.values_list('rol', 'horas')),
                         [('AYUDANTE', D('1.50')), ('MAQUINISTA', D('3.00'))])
        self.assertEqual(o.salidas.get(pk=s.pk).cantidad_real, D('1'))
        self.assertEqual(o.terminado_en.date(), date.today())
