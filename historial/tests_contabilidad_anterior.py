"""v1.23.3: contabilidad del sistema anterior en los libros del ERP hasta la fecha de corte."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from contabilidad import reportes
from contabilidad.automatico import generar_apertura
from contabilidad.centralizar import ErrorContable, centralizar_periodo, periodo_migrado
from contabilidad.models import Asiento, AsientoLinea, PeriodoContable
from core.models import Empresa

from . import contabilidad_anterior as ca
from .models import AsientoAnterior

D = Decimal
CORTE = date(2026, 2, 28)


def linea(fecha, diario, voucher, cuenta, debe=0, haber=0, glosa='', centro=''):
    return AsientoAnterior(fecha=fecha, periodo=fecha.strftime('%Y%m'), diario=diario, voucher=voucher, cuenta=cuenta,
                           cuenta_nombre=f'Cuenta {cuenta}', debe=D(debe), haber=D(haber), glosa=glosa,
                           centro_costo=centro)


class ContabilidadAnteriorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('conta', 'c@t.com', 'x')
        apertura = 'POR LOS SALDOS INICIALES AL APERTURAR EL EJERCICIO 2026'
        AsientoAnterior.objects.bulk_create([
            # 2025: ya resumido en la apertura de 2026 (no debe sumarse)
            linea(date(2025, 11, 5), 'Facturas de cliente', '000001', '12120001', 500),
            linea(date(2025, 11, 5), 'Facturas de cliente', '000001', '70111001', haber=500),
            # apertura del ejercicio 2026
            linea(date(2026, 1, 1), 'Operaciones varias', '000066', '10410004', 1000, glosa=apertura),
            linea(date(2026, 1, 1), 'Operaciones varias', '000066', '12120001', 3000, glosa=apertura),
            linea(date(2026, 1, 1), 'Operaciones varias', '000066', '24100001', 2000, glosa=apertura),
            linea(date(2026, 1, 1), 'Operaciones varias', '000066', '42120002', haber=2500, glosa=apertura),
            linea(date(2026, 1, 1), 'Operaciones varias', '000066', '50110000', haber=3500, glosa=apertura),
            # enero: una venta con su costo; febrero: un gasto con destino
            linea(date(2026, 1, 10), 'Facturas de cliente', '000010', '12120001', 1180),
            linea(date(2026, 1, 10), 'Facturas de cliente', '000010', '40111001', haber=180),
            linea(date(2026, 1, 10), 'Facturas de cliente', '000010', '70111001', haber=1000),
            linea(date(2026, 1, 31), 'Asientos Automáticos', '000001', '69111001', 600),
            linea(date(2026, 1, 31), 'Asientos Automáticos', '000001', '24100001', haber=600),
            linea(date(2026, 2, 3), 'Facturas de proveedores servicios', '000020', '63110001', 200),
            linea(date(2026, 2, 3), 'Facturas de proveedores servicios', '000020', '42120002', haber=200),
            linea(date(2026, 2, 3), 'Facturas de proveedores servicios', '000020', '95100001', 200),
            linea(date(2026, 2, 3), 'Facturas de proveedores servicios', '000020', '79110001', haber=200),
        ])

    def test_libros_identicos_hasta_el_corte_y_el_erp_despues(self):
        self.assertEqual(ca.periodo_apertura(), '202601')
        r = ca.importar(CORTE, log=lambda *_: None)
        self.assertEqual((r['asientos'], r['desde']), (4, '202601'))
        # cada cuenta igual al sistema anterior desde su apertura (sin duplicar 2025)
        for cuenta, saldo in (('12120001', 4180), ('42120002', -2700), ('70111001', -1000), ('10410004', 1000)):
            s = AsientoLinea.todas.filter(asiento__origen='ANTERIOR', cuenta__codigo=cuenta).aggregate(
                d=Sum('debe'), h=Sum('haber'))
            self.assertEqual(s['d'] - s['h'], D(saldo), cuenta)
        # el resultado es el de la contabilidad anterior: 1000 venta - 600 costo - 200 gasto
        self.assertEqual(reportes.estado_resultados('202601', '202602')[1], D('200'))
        sit = reportes.situacion_financiera('202602', '202601')
        self.assertEqual(sit['t']['diferencia'], 0)
        # al corte: facturas a las cuentas del ERP y existencias al kardex (sin stock en esa fecha)
        mig = Asiento.objects.get(origen='MIGRACION')
        lineas = {(l.cuenta.codigo, l.debe, l.haber) for l in mig.lineas.all()}
        self.assertIn(('1212', D('4180'), D('0')), lineas)
        self.assertIn(('4212', D('0'), D('2700')), lineas)
        self.assertIn(('24100001', D('0'), D('1400')), lineas)
        # hasta el corte: periodos cerrados y el ERP no genera asientos
        self.assertTrue(PeriodoContable.esta_cerrado('202602'))
        self.assertTrue(periodo_migrado('202602'))
        self.assertFalse(periodo_migrado('202603'))
        with self.assertRaises(ErrorContable):
            centralizar_periodo('202602')
        self.assertIsNone(generar_apertura())
        self.assertEqual(Empresa.actual().fecha_corte_contable, CORTE)
        # repetir no duplica
        ca.importar(CORTE, log=lambda *_: None)
        self.assertEqual(Asiento.objects.filter(origen='ANTERIOR').count(), 4)

    def test_corte_a_fin_de_mes_y_pantallas(self):
        with self.assertRaises(ca.ErrorMigracion):
            ca.importar(date(2026, 2, 15), log=lambda *_: None)
        ca.importar(CORTE, log=lambda *_: None)
        self.client.force_login(self.user)
        r = self.client.get(reverse('contabilidad:conciliacion_migracion'))
        self.assertContains(r, 'Facturas por cobrar')
        # el balance del sistema anterior parte de la apertura del ejercicio (no suma 2025)
        r = self.client.get(reverse('hist_balance'))
        self.assertEqual(r.context['desde'], '202601')
        fila = next(f for f in r.context['filas'] if f['cuenta'] == '12120001')
        self.assertEqual(fila['d'] - fila['h'], D('4180'))
