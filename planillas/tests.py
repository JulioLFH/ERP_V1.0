"""Planillas: cálculo mensual (ONP/AFP, quinta, EsSalud), gratificación, CTS, cierre, contabilidad, pago y PLAME."""
import io
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea, CentroCosto
from finanzas.models import Cuenta

from . import calculo, servicios
from .models import AFP, Planilla, Trabajador

D = Decimal


class PlanillaTest(TestCase):
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
        self.integra = AFP.objects.get(codigo='INTEGRA')
        self.planta = CentroCosto.objects.create(codigo='PL', nombre='Planta', tipo='PRODUCCION')
        self.ana = Trabajador.objects.create(
            numero_doc='45678912', apellido_paterno='QUISPE', apellido_materno='MAMANI', nombres='ANA',
            fecha_ingreso=date(2024, 3, 1), sueldo=D('3000'), asignacion_familiar=True, sistema_pensiones='AFP',
            afp=self.integra, cuspp='123456ABCDE1', centro_costo=self.planta)
        self.luis = Trabajador.objects.create(
            numero_doc='41234567', apellido_paterno='ROJAS', nombres='LUIS', fecha_ingreso=date(2025, 1, 16),
            sueldo=D('1500'), sistema_pensiones='ONP')

    def _planilla(self, tipo, periodo):
        p = Planilla.objects.create(tipo=tipo, periodo=periodo)
        calculo.generar_filas(p)
        calculo.calcular(p)
        return p

    def test_mensual_afp_onp_quinta_essalud_y_prorrateo(self):
        p = self._planilla('MENSUAL', '202501')
        ana = p.filas.get(trabajador=self.ana)
        # 3000 + asignación familiar 113 (10% de RMV 1130) = 3113
        self.assertEqual((ana.monto('BASICO'), ana.monto('ASIG_FAMILIAR'), ana.remuneracion_afecta),
                         (D('3000.00'), D('113.00'), D('3113.00')))
        self.assertEqual((ana.monto('AFP_APORTE'), ana.monto('AFP_PRIMA'), ana.monto('AFP_COMISION')),
                         (D('311.30'), D('42.65'), D('48.25')))
        self.assertEqual(ana.monto('ESSALUD'), D('280.17'))
        # quinta: 3113 × 12 + 2 gratificaciones × 3113 × 1.09 = 44,142.34 − 7 UIT (37,450) = 6,692.34 × 8% / 12
        self.assertEqual(ana.monto('QUINTA'), D('44.62'))
        self.assertEqual(ana.neto, D('3113.00') - D('311.30') - D('42.65') - D('48.25') - D('44.62'))
        # Luis ingresó el 16: 15 días; ONP 13%; EsSalud con base mínima la RMV
        luis = p.filas.get(trabajador=self.luis)
        self.assertEqual((luis.dias_laborados, luis.monto('BASICO'), luis.monto('ONP')),
                         (D('15'), D('750.00'), D('97.50')))
        self.assertEqual(luis.monto('ESSALUD'), D('101.70'))  # 9% de 1130
        self.assertEqual(luis.monto('QUINTA'), D('0'))

    def test_faltas_vacaciones_horas_extra(self):
        p = Planilla.objects.create(tipo='MENSUAL', periodo='202502')
        calculo.generar_filas(p)
        f = p.filas.get(trabajador=self.ana)
        f.dias_falta, f.dias_vacaciones, f.horas_extra_25 = D('2'), D('10'), D('4')
        f.save()
        calculo.calcular(p)
        f.refresh_from_db()
        self.assertEqual((f.monto('BASICO'), f.monto('VACACIONES')), (D('1800.00'), D('1000.00')))
        # valor hora (3000 + 113) / 30 / 8 = 12.9708 × 1.25 × 4 = 64.85
        self.assertEqual(f.monto('HORAS_EXTRA_25'), D('64.85'))

    def test_gratificacion_y_cts(self):
        grat = self._planilla('GRATIFICACION', '202507')
        ana = grat.filas.get(trabajador=self.ana)
        self.assertEqual((ana.monto('GRATIFICACION'), ana.monto('BONIF_EXTRA')), (D('3113.00'), D('280.17')))
        luis = grat.filas.get(trabajador=self.luis)  # ingresó el 16/01: 5 meses completos (feb-jun)
        self.assertEqual(luis.monto('GRATIFICACION'), D('1250.00'))
        cts = self._planilla('CTS', '202511')
        ana = cts.filas.get(trabajador=self.ana)
        # (3113 + 3113/6) / 12 × 6 meses = 1,815.92
        self.assertEqual(ana.monto('CTS'), D('1815.92'))
        # régimen de microempresa: sin gratificación ni CTS
        self.luis.regimen = 'MICRO'
        self.luis.save()
        grat2 = self._planilla('GRATIFICACION', '202512')
        self.assertEqual(grat2.filas.get(trabajador=self.luis).total_ingresos, D('0'))

    def test_quinta_resta_retenciones_previas(self):
        for mes in ('01', '02', '03', '04'):
            p = self._planilla('MENSUAL', f'2025{mes}')
            servicios.cerrar(p)
        abril = Planilla.objects.get(periodo='202504').filas.get(trabajador=self.ana)
        # (impuesto anual 535.39 − 3 × 44.62 retenido) / 9 = 44.61
        self.assertEqual(abril.monto('QUINTA'), D('44.61'))

    def test_quinta_con_datos_previos_al_sistema(self):
        # empieza a usar el sistema en setiembre: de enero a agosto percibió 8 × 3113 + gratificación de julio
        # (3113 + 9%) y le retuvieron 8 × 44.62
        self.ana.quinta_anio = 2025
        self.ana.quinta_remuneracion_previa = D('3113') * 8 + D('3393.17')
        self.ana.quinta_retencion_previa = D('356.96')
        self.ana.save()
        p = self._planilla('MENSUAL', '202509')
        # misma proyección anual (44,142.34 → impuesto 535.39); setiembre: (535.39 − 356.96) / 4
        self.assertEqual(p.filas.get(trabajador=self.ana).monto('QUINTA'), D('44.61'))

    def test_cierre_contabilidad_pago_y_plame(self):
        p = self._planilla('MENSUAL', '202509')
        servicios.cerrar(p)
        centralizar_periodo('202509')
        lineas = AsientoLinea.objects.filter(asiento__origen='PLANILLA', asiento__periodo='202509')
        self.assertTrue(lineas.exists())
        self.assertEqual(sum(l.debe for l in lineas), sum(l.haber for l in lineas))
        neto = lineas.get(cuenta__codigo='4111').haber
        self.assertEqual(neto, p.totales()['n'])
        self.assertEqual(lineas.get(cuenta__codigo='4031').haber, p.filas.get(trabajador=self.ana).monto('ESSALUD') +
                         p.filas.get(trabajador=self.luis).monto('ESSALUD'))
        self.assertTrue(lineas.filter(cuenta__codigo__startswith='90', es_destino=True).exists())  # planta → 90
        banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN').first()
        banco.permite_sobregiro = True
        banco.save()
        mov = servicios.pagar(p, banco, date(2025, 9, 30), self.admin)
        p.refresh_from_db()
        self.assertEqual((p.estado, mov.monto, mov.concepto), ('PAGADA', neto, 'PLANILLA'))
        with self.assertRaises(calculo.ErrorPlanilla):
            servicios.reabrir(p)
        mov.estado = 'ANULADO'
        mov.save()
        p.refresh_from_db()
        self.assertEqual(p.estado, 'CERRADA')
        nombre, datos = servicios.archivos_plame('202509', '20100000001')
        z = zipfile.ZipFile(io.BytesIO(datos))
        rem = z.read('0601202509' + '20100000001.rem').decode()
        self.assertIn('01|45678912|0121|3000.00|3000.00|', rem)
        self.assertIn('01|41234567|0607|195.00|195.00|', rem)
        self.assertIn('01|45678912|240|0|0|0|', z.read('0601202509' + '20100000001.jor').decode())

    def test_pantallas(self):
        r = self.client.post(reverse('planillas:nueva'), {'tipo': 'MENSUAL', 'mes': '2025-10'})
        p = Planilla.objects.get(periodo='202510')
        self.assertRedirects(r, reverse('planillas:detalle', args=[p.pk]))
        f = p.filas.get(trabajador=self.ana)
        datos = {'filas-TOTAL_FORMS': '2', 'filas-INITIAL_FORMS': '2', 'accion': 'calcular'}
        for i, fila in enumerate(p.filas.all()):
            datos.update({f'filas-{i}-id': fila.pk, f'filas-{i}-dias_laborados': '30', f'filas-{i}-dias_falta': '0',
                          f'filas-{i}-dias_vacaciones': '0', f'filas-{i}-dias_subsidio': '0',
                          f'filas-{i}-horas_extra_25': '0', f'filas-{i}-horas_extra_35': '0',
                          f'filas-{i}-otros_ingresos': '100' if fila == f else '0',
                          f'filas-{i}-ingresos_no_afectos': '0', f'filas-{i}-adelantos': '200' if fila == f else '0',
                          f'filas-{i}-otros_descuentos': '0'})
        self.client.post(reverse('planillas:detalle', args=[p.pk]), datos)
        f.refresh_from_db()
        self.assertEqual((f.monto('OTROS_AFECTOS'), f.monto('ADELANTO'), f.remuneracion_afecta),
                         (D('100.00'), D('200.00'), D('3213.00')))
        self.assertContains(self.client.get(reverse('planillas:detalle', args=[p.pk])), 'QUISPE')
        self.assertContains(self.client.get(reverse('planillas:boletas', args=[p.pk])), 'Neto a pagar')
        self.assertIn('spreadsheetml', self.client.get(reverse('planillas:detalle', args=[p.pk]),
                                                       {'formato': 'excel'})['Content-Type'])
        self.assertContains(self.client.get(reverse('planillas:trabajadores')), 'QUISPE MAMANI, ANA')
        self.assertEqual(self.client.get(reverse('planillas:configuracion')).status_code, 200)
        # validación: gratificación solo en julio o diciembre
        r = self.client.post(reverse('planillas:nueva'), {'tipo': 'GRATIFICACION', 'mes': '2025-08'})
        self.assertContains(r, 'julio o diciembre')
