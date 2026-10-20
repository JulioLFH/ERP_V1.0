"""Fallas del flujo integral del 20/10/2026 en planta y planilla: MAN-10 (tercera persona en la cuadrilla), MAN-11
(paradas con horas futuras, fuera de la orden o cruzadas; operario de planilla) y PLA-06/07 (gasto de personal por
centro de costo). Un escenario por clase."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import CentroCosto
from core.models import Almacen, Producto
from planillas.models import Trabajador

from . import servicios
from .models import CentroTrabajo, HojaRuta, ListaMateriales, MotivoParada, OrdenProduccion, Parada, VersionFabricacion

D = Decimal


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
        self.planta = CentroCosto.objects.create(codigo='CC-LAC', nombre='Planta lácteos', tipo='PRODUCCION')
        self.leche = Producto.objects.create(nombre='Leche', clase='MATERIA_PRIMA')
        self.leche.mover_stock(D('100'), 'Saldo de prueba', costo=D('2'), almacen=self.almacen)
        self.yogur = Producto.objects.create(nombre='Yogur', clase='PRODUCTO_TERMINADO')
        self.puesto = CentroTrabajo.objects.create(codigo='P-ENV', nombre='Envasado', costo_hora_mo=D('8'),
                                                   centro_costo=self.planta)
        ruta = HojaRuta.objects.create(codigo='R-Y', nombre='Yogur', estado='APROBADA')
        ruta.operaciones.create(secuencia=10, centro=self.puesto, descripcion='Envasado', horas_unidad=D('0.5'),
                                maquinistas=1, ayudantes=0)
        self.receta = ListaMateriales.objects.create(producto=self.yogur, codigo='Y1', cantidad_base=D('1'),
                                                     vigente_desde=date(2025, 1, 1))
        self.receta.componentes.create(producto=self.leche, cantidad=D('1'))
        VersionFabricacion.objects.create(producto=self.yogur, codigo='Y1', lista=self.receta, hoja=ruta,
                                          vigente_desde=date(2025, 1, 1))
        self.operarios = [Trabajador.objects.create(numero_doc=f'4000010{n}', apellido_paterno='PRUEBA', nombres=nombre,
                                                    fecha_ingreso=date(2024, 1, 1), sueldo=D('1500'), tipo='OBRERO')
                          for n, nombre in enumerate(('UNO', 'DOS', 'CUATRO'))]

    def orden(self):
        o = OrdenProduccion.objects.create(producto=self.yogur, lista=self.receta, cantidad=D('4'),
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        return o


class CuadrillaDeTresTest(Base):
    """MAN-10 y PLA-06/07: cuadrilla prevista de 1 persona; se terminan con 3 operarios (botón «Agregar persona») y el
    gasto de planilla de quienes no tienen centro va al centro del puesto donde trabajaron."""

    def test_tres_operarios_y_centro_por_horas(self):
        o = self.orden()
        h = o.horas.get()
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        self.assertContains(r, 'js-agregar-hh')
        self.assertContains(r, f'hh_{h.pk}_1_rol')  # la prevista (1) y una libre
        self.assertNotContains(r, f'hh_{h.pk}_2_rol')
        datos = {'cantidad_producida': '4', f'hora_{h.pk}': '2', 'fecha': '', f'consumo_{o.consumos.get().pk}': '4'}
        for n, (rol, t) in enumerate((('MAQUINISTA', self.operarios[0]), ('AYUDANTE', self.operarios[1]),
                                      ('AYUDANTE', self.operarios[2]))):  # la fila 2 la agrega el botón
            datos.update({f'hh_{h.pk}_{n}_rol': rol, f'hh_{h.pk}_{n}_trab': t.pk, f'hh_{h.pk}_{n}_horas': '2'})
        self.client.post(reverse('manufactura:orden_terminar', args=[o.pk]), datos)
        o.refresh_from_db()
        self.assertEqual(o.estado, 'TERMINADA')
        self.assertEqual(sorted(o.horas.get().personal.values_list('trabajador__nombres', flat=True)),
                         ['CUATRO', 'DOS', 'UNO'])
        from planillas.servicios import centros_de_trabajadores
        centros = centros_de_trabajadores(self.operarios, o.fecha_fin.strftime('%Y%m'))
        self.assertTrue(all(centros[t.pk] == {self.planta: D('2')} for t in self.operarios), centros)
        sin_horas = Trabajador.objects.create(numero_doc='40000199', apellido_paterno='SIN', nombres='HORAS',
                                              fecha_ingreso=date(2024, 1, 1), sueldo=D('1500'))
        self.assertEqual(centros_de_trabajadores([sin_horas], o.fecha_fin.strftime('%Y%m'))[sin_horas.pk], {})


class PlanillaSinCentroTest(Base):
    """PLA-07: la planilla no se cierra con trabajadores sin centro de costo ni horas en las órdenes."""

    def test_cierre_exige_centro(self):
        from planillas import calculo
        from planillas.models import Planilla
        from planillas import servicios as pla
        hoy = timezone.localdate()
        periodo = hoy.strftime('%Y%m')
        p = Planilla.objects.create(tipo='MENSUAL', periodo=periodo)
        calculo.generar_filas(p)
        calculo.calcular(p)
        p.refresh_from_db()
        with self.assertRaisesMessage(calculo.ErrorPlanilla, 'sin centro de costo'):
            pla.cerrar(p)
        Trabajador.objects.filter(centro_costo__isnull=True).update(centro_costo=self.planta)
        pla.cerrar(p)
        self.assertEqual(Planilla.objects.get(pk=p.pk).estado, 'CERRADA')


class ParadaValidadaTest(Base):
    """MAN-11: la parada no acepta horas futuras, ni fuera de la orden o de su puesto, ni cruzada con otra; el
    operario se elige de la planilla."""

    def test_reglas_de_la_parada(self):
        from .views_reportes import ParadaForm
        motivo = MotivoParada.objects.filter(activo=True, exige_equipo=False).first()
        o = self.orden()
        ahora = timezone.localtime().replace(second=0, microsecond=0)
        base = {'centro': self.puesto.pk, 'motivo': motivo.pk, 'operario': self.operarios[0].nombre_completo,
                'nota': ''}

        def form(inicio, fin, **extra):
            return ParadaForm(data={**base, 'inicio': inicio.strftime('%Y-%m-%dT%H:%M'),
                                    'fin': fin.strftime('%Y-%m-%dT%H:%M') if fin else '', **extra})
        f = form(ahora + timedelta(hours=2), ahora + timedelta(hours=3))
        self.assertFalse(f.is_valid())
        self.assertIn('hora futura', str(f.errors))
        otro = CentroTrabajo.objects.create(codigo='P-PRO', nombre='Proceso')
        f = form(ahora - timedelta(hours=1), ahora - timedelta(minutes=30), orden=o.pk, centro=otro.pk)
        self.assertFalse(f.is_valid())
        self.assertIn('no pasa por el puesto', str(f.errors))
        o.terminado_en = ahora - timedelta(hours=2)
        o.save(update_fields=['terminado_en'])
        f = form(ahora - timedelta(hours=1), ahora - timedelta(minutes=30), orden=o.pk)
        self.assertFalse(f.is_valid())
        self.assertIn('debe ser anterior', str(f.errors))
        f = form(ahora - timedelta(hours=1), ahora - timedelta(minutes=30))
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        f = form(ahora - timedelta(minutes=45), ahora - timedelta(minutes=10))
        self.assertFalse(f.is_valid())
        self.assertIn('Se cruza con otra parada', str(f.errors))
        self.assertEqual(Parada.objects.count(), 1)
        opciones = [v for v, _ in ParadaForm().fields['operario'].widget.choices]
        self.assertIn(self.operarios[0].nombre_completo, opciones)
