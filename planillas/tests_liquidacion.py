"""v1.20: récord vacacional, vacaciones gozadas/vendidas en la planilla del mes, liquidación de beneficios
sociales al cese y costo de planilla por centro de costo."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea

from . import calculo, servicios
from .models import AFP, Planilla, Trabajador, Vacacion

D = Decimal


class LiquidacionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('rrhh', 'r@h.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.ana = Trabajador.objects.create(
            numero_doc='45678912', apellido_paterno='QUISPE', nombres='ANA', fecha_ingreso=date(2024, 3, 1),
            sueldo=D('3000'), asignacion_familiar=True, sistema_pensiones='AFP',
            afp=AFP.objects.get(codigo='INTEGRA'))

    def test_record_vacacional(self):
        Vacacion.objects.create(trabajador=self.ana, fecha_inicio=date(2025, 4, 1), fecha_fin=date(2025, 4, 30))
        Vacacion.objects.create(trabajador=self.ana, tipo='VENTA', fecha_inicio=date(2026, 3, 1),
                                fecha_fin=date(2026, 3, 15))
        anios, trunco = calculo.record_vacacional(self.ana, date(2027, 3, 10))
        self.assertEqual([(a['gozados'], a['vendidos'], a['pendientes']) for a in anios],
                         [(30, 0, 0), (0, 15, 15), (0, 0, 30)])
        self.assertTrue(anios[1]['vencido'])  # el 2.° año venció el 28/02/2027 con 15 días sin gozar
        self.assertEqual(trunco['inicio'], date(2027, 3, 1))

    def test_vacaciones_pasan_a_la_planilla_del_mes(self):
        Vacacion.objects.create(trabajador=self.ana, fecha_inicio=date(2026, 9, 21), fecha_fin=date(2026, 10, 5))
        Vacacion.objects.create(trabajador=self.ana, tipo='VENTA', fecha_inicio=date(2026, 9, 1),
                                fecha_fin=date(2026, 9, 10))
        p = Planilla.objects.create(tipo='MENSUAL', periodo='202609')
        calculo.generar_filas(p)
        fila = p.filas.get(trabajador=self.ana)
        self.assertEqual((fila.dias_vacaciones, fila.dias_vacaciones_vendidas), (D('10'), D('10')))
        calculo.calcular(p)
        fila.refresh_from_db()
        # 20 días trabajados + 10 de goce + 10 vendidos pagados como remuneración vacacional
        self.assertEqual((fila.monto('BASICO'), fila.monto('VACACIONES')), (D('2000.00'), D('2000.00')))

    def test_liquidacion_de_beneficios_sociales(self):
        Vacacion.objects.create(trabajador=self.ana, fecha_inicio=date(2025, 4, 1), fecha_fin=date(2025, 4, 30))
        self.ana.fecha_cese, self.ana.motivo_cese = date(2026, 8, 15), 'Despido'
        self.ana.save()
        r = self.client.post(reverse('planillas:nueva'), {'tipo': 'LIQUIDACION', 'mes': '2026-08'})
        p = Planilla.objects.get(tipo='LIQUIDACION')
        self.assertRedirects(r, reverse('planillas:detalle', args=[p.pk]))
        fila = p.filas.get()
        fila.despido_arbitrario = True
        fila.save()
        calculo.calcular(p)
        fila.refresh_from_db()
        # remuneración computable 3000 + 113 = 3113
        self.assertEqual(fila.monto('GRATIF_TRUNCA'), D('518.83'))      # julio completo: 3113 / 6
        self.assertEqual(fila.monto('BONIF_TRUNCA'), D('46.69'))
        self.assertEqual(fila.monto('CTS'), D('907.96'))                # 3 meses y 15 días desde el 01/05
        self.assertEqual(fila.monto('VAC_TRUNCAS'), D('1426.79'))       # 5 meses y 15 días desde el 01/03
        self.assertEqual(fila.monto('VACACIONES'), D('3113.00'))        # 2.° año sin gozar
        self.assertEqual(fila.monto('INDEMN_VACACIONAL'), D('0'))       # aún no vencidas
        self.assertEqual(fila.monto('INDEMNIZACION'), D('11479.19'))    # 1.5 × 3113 × 885 / 360
        self.assertEqual(fila.monto('AFP_APORTE'), D('453.98'))         # 10% de lo remunerativo (4539.79)
        servicios.cerrar(p)
        r = centralizar_periodo('202608')
        self.assertEqual(r['errores'], [])
        self.assertTrue(AsientoLinea.objects.filter(cuenta__codigo='6293', debe=D('11479.19')).exists())
        boleta = self.client.get(reverse('planillas:boletas', args=[p.pk]))
        self.assertContains(boleta, 'Indemnización por despido arbitrario')

    def test_pantallas(self):
        for nombre in ('vacaciones', 'costo_centros'):
            self.assertEqual(self.client.get(reverse(f'planillas:{nombre}')).status_code, 200)
        r = self.client.post(reverse('planillas:vacaciones'), {
            'trabajador': self.ana.pk, 'tipo': 'VENTA', 'fecha_inicio': '2026-09-01', 'fecha_fin': '2026-09-20'})
        self.assertContains(r, 'como máximo 15 días')
