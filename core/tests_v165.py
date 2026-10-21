"""v1.65, checklist v1.56 12:53: lecturas de odómetro u horómetro con parte diario aprobado y uso proyectado
(FLO-02), y componentes por niveles con planes por km u horas que generan la OT y la SolPed del repuesto (FLO-03)."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import SolicitudPedido
from contabilidad.models import CentroCosto
from flota import lecturas
from flota.models import ComponenteEquipo, LecturaEquipo
from produccion import planta
from produccion.models import Equipo, PlanMantenimiento

from .models import Producto

D = Decimal
HOY = timezone.localdate()


class LecturasYPlanesPorUsoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v165', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)

    def test_parte_aprobado_proyeccion_y_preventivo_por_km(self):
        eq = Equipo.objects.create(codigo='CAM-01', nombre='Camión 1', tipo='VEHICULO', placa='ABC999', medidor='KM',
                                   centro_costo=CentroCosto.objects.filter(activo=True).first())
        lecturas.registrar(eq, D('8000'), HOY - timedelta(days=40), self.admin, 'OT')  # con documento: aprobada
        # parte diario: queda por aprobar; una lectura menor a la anterior no se acepta (todo el lote)
        r = self.client.post(reverse('flota:parte'), {'fecha': HOY.isoformat(), f'valor_{eq.pk}': '7.500'})
        self.assertContains(r, 'menor a la anterior')
        self.assertEqual(LecturaEquipo.objects.filter(origen='PARTE').count(), 0)
        self.client.post(reverse('flota:parte'), {'fecha': HOY.isoformat(), f'valor_{eq.pk}': '10,000'})
        lectura = LecturaEquipo.objects.get(origen='PARTE')
        self.assertEqual((lectura.estado, lectura.valor), ('PENDIENTE', D('10000')))
        eq.refresh_from_db()
        self.assertEqual(eq.lectura_actual, D('8000'))
        self.client.post(reverse('flota:lecturas'), {'lectura': [lectura.pk], 'accion': 'aprobar'})
        eq.refresh_from_db()
        self.assertEqual(eq.lectura_actual, D('10000'))
        self.assertEqual(lecturas.uso_diario(eq), D('50'))  # 2,000 km en 40 días
        self.assertEqual(lecturas.fecha_proyectada(eq, D('11000')), HOY + timedelta(days=21))
        # componente con repuesto y plan cada 5,000 km: la última a los 5,500 → toca a los 10,500 (tolerancia 10 %)
        filtro = Producto.objects.create(codigo='REP-FIL', nombre='Filtro de aceite', tipo='BIEN', precio_compra=D('35'))
        motor = ComponenteEquipo.objects.create(equipo=eq, nivel='SISTEMA', nombre='Motor')
        pieza = ComponenteEquipo.objects.create(equipo=eq, padre=motor, nivel='PIEZA', nombre='Filtro de aceite',
                                                repuesto=filtro, cantidad_repuesto=D('2'))
        self.assertEqual(pieza.ruta, 'Motor › Filtro de aceite')
        plan = PlanMantenimiento.objects.create(equipo=eq, componente=pieza, tarea='Cambio de filtro',
                                                frecuencia_dias=None, frecuencia_uso=D('5000'),
                                                ultima_lectura=D('5500'))
        self.assertEqual(plan.falta_uso, D('500'))
        self.assertTrue(plan.vence(HOY))
        ots = planta.programar_preventivos(7, self.admin)
        self.assertEqual(len(ots), 1)
        ot = ots[0]
        self.assertIn('10,500', ot.descripcion)
        self.assertEqual([(r.producto, r.cantidad) for r in ot.repuestos.all()], [(filtro, D('2'))])
        sp = SolicitudPedido.objects.get(orden_mantenimiento=ot)  # sin stock del repuesto: se pide
        self.assertEqual((sp.origen, sp.lineas.get().producto), ('MANTENIMIENTO', filtro))
        self.assertEqual(planta.programar_preventivos(7, self.admin), [])  # no duplica la abierta
        r = self.client.get(reverse('flota:equipo', args=[eq.pk]))
        self.assertContains(r, 'Componentes')
        self.assertContains(r, 'a los 10500')
