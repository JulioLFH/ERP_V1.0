"""Presupuestos y estados financieros comparativos (v1.17, punto 27)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import reportes
from .models import Asiento, CentroCosto, CuentaContable, Presupuesto, PresupuestoLinea
from .presupuesto import ejecucion, generar_desde_real

D = Decimal


def asiento(fecha, lineas):
    a = Asiento.objects.create(fecha=fecha, glosa='Prueba')
    for cuenta, debe, haber, centro in lineas:
        a.lineas.create(cuenta=cuenta, debe=D(debe), haber=D(haber), centro_costo=centro)
    return a


class PresupuestoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        imputable = CuentaContable.objects.filter(imputable=True)
        self.ventas = imputable.filter(codigo__startswith='70').first()
        self.gasto = imputable.filter(codigo__startswith='63', destino_debe__isnull=False).first()
        self.caja = imputable.filter(codigo__startswith='10').first()
        self.d94 = self.gasto.destino_debe
        self.d79 = self.gasto.destino_haber
        self.adm = CentroCosto.objects.create(codigo='ADM', nombre='Administración', tipo='ADMINISTRACION')
        # año 2030: ventas 1000 en enero y 1200 en febrero; gasto 300 en enero
        asiento(date(2030, 1, 15), [(self.caja, 1000, 0, None), (self.ventas, 0, 1000, None)])
        asiento(date(2030, 2, 15), [(self.caja, 1200, 0, None), (self.ventas, 0, 1200, None)])
        asiento(date(2030, 1, 20), [(self.gasto, 300, 0, self.adm), (self.caja, 0, 300, None)])
        a = asiento(date(2030, 1, 20), [(self.d94, 300, 0, self.adm), (self.d79, 0, 300, None)])
        a.lineas.update(es_destino=True)

    def test_generar_desde_real_y_ejecucion(self):
        pres = Presupuesto.objects.create(anio=2031)
        self.assertEqual(generar_desde_real(pres, D('10')), 2)
        v = pres.lineas.get(cuenta=self.ventas)
        self.assertEqual((v.m01, v.m02, v.m03), (D('1100.00'), D('1320.00'), D('0')))
        g = pres.lineas.get(cuenta=self.gasto)
        self.assertEqual((g.centro_costo, g.m01), (self.adm, D('330.00')))
        # real 2031: ventas de enero 900 y gasto de enero 360
        asiento(date(2031, 1, 10), [(self.caja, 900, 0, None), (self.ventas, 0, 900, None)])
        asiento(date(2031, 1, 12), [(self.gasto, 360, 0, self.adm), (self.caja, 0, 360, None)])
        filas, tot = ejecucion(pres, hasta_mes=1)
        fv = next(f for f in filas if f['l'].cuenta == self.ventas)
        fg = next(f for f in filas if f['l'].cuenta == self.gasto)
        self.assertEqual((fv['pres'], fv['real'], fv['var']), (D('1100.00'), D('900'), D('-200.00')))
        self.assertEqual((fg['pres'], fg['real'], fg['pct']), (D('330.00'), D('360'), D('109.09')))
        self.assertEqual((tot['pres'], tot['real']), (D('770.00'), D('540')))  # resultado = ingresos − gastos

    def test_resultados_vs_anio_anterior_y_presupuesto(self):
        asiento(date(2031, 1, 10), [(self.caja, 1500, 0, None), (self.ventas, 0, 1500, None)])
        filas, rango = reportes.resultados_comparativo('203101', '203101', 'anio')
        self.assertEqual(rango, ('203001', '203001'))
        ventas = next(f for f in filas if f['nombre'] == 'Ventas netas')
        self.assertEqual((ventas['valor'], ventas['comp'], ventas['var'], ventas['pct']),
                         (D('1500'), D('1000'), D('500'), D('50.00')))
        # periodo previo (diciembre 2030, sin movimiento)
        self.assertEqual(reportes.resultados_comparativo('203101', '203101', 'previo')[1], ('203012', '203012'))
        # presupuesto: gasto por naturaleza llega a gastos de administración por su destino
        pres = Presupuesto.objects.create(anio=2031)
        PresupuestoLinea.objects.create(presupuesto=pres, cuenta=self.ventas, m01=D('2000'))
        PresupuestoLinea.objects.create(presupuesto=pres, cuenta=self.gasto, centro_costo=self.adm, m01=D('400'))
        filas, _ = reportes.resultados_comparativo('203101', '203101', 'presupuesto')
        por = {f['nombre']: f for f in filas}
        self.assertEqual(por['Ventas netas']['comp'], D('2000'))
        self.assertEqual(por['Gastos de administración']['comp'], D('-400'))
        self.assertEqual(por['RESULTADO DEL EJERCICIO']['comp'], D('1600'))
        self.assertNotIn('Gastos sin destino asignado', por)

    def test_pantallas_y_exportacion(self):
        r = self.client.get(reverse('contabilidad:resultados'), {'desde': '2031-01', 'hasta': '2031-01',
                                                                 'comparar': 'presupuesto'})
        self.assertContains(r, 'No hay presupuesto para el año')
        r = self.client.get(reverse('contabilidad:resultados'), {'desde': '2031-01', 'hasta': '2031-01',
                                                                 'comparar': 'anio', 'formato': 'excel'})
        self.assertIn('spreadsheetml', r['Content-Type'])
        r = self.client.get(reverse('contabilidad:situacion'), {'hasta': '2031-01', 'comparar': 'anio'})
        self.assertContains(r, 'TOTAL ACTIVO')
        r = self.client.get(reverse('contabilidad:situacion'), {'hasta': '2031-01', 'formato': 'excel'})
        self.assertIn('spreadsheetml', r['Content-Type'])
        # alta de presupuesto con líneas por la pantalla
        datos = {'anio': '2031', 'nombre': 'Original', 'estado': 'BORRADOR', 'principal': 'on', 'observaciones': '',
                 'lineas-TOTAL_FORMS': '1', 'lineas-INITIAL_FORMS': '0', 'lineas-0-cuenta': self.ventas.pk,
                 'lineas-0-m01': '500', 'lineas-0-m12': '700'}
        r = self.client.post(reverse('contabilidad:presupuesto_nuevo'), datos)
        pres = Presupuesto.objects.get(anio=2031, nombre='Original')
        self.assertRedirects(r, reverse('contabilidad:presupuesto_ejecucion', args=[pres.pk]))
        self.assertEqual(pres.lineas.get().total, D('1200'))
        self.assertEqual(self.client.get(reverse('contabilidad:presupuesto_ejecucion', args=[pres.pk]),
                                         {'formato': 'excel'}).status_code, 200)
        self.assertContains(self.client.get(reverse('contabilidad:presupuestos')), 'Original')
        # línea repetida (misma cuenta y centro): se rechaza
        datos.update({'nombre': 'Otro', 'lineas-TOTAL_FORMS': '2', 'lineas-1-cuenta': self.ventas.pk})
        self.assertEqual(self.client.post(reverse('contabilidad:presupuesto_nuevo'), datos).status_code, 200)
        # generar desde el real (2030) reemplaza las líneas
        self.client.post(reverse('contabilidad:presupuesto_generar', args=[pres.pk]), {'variacion': '0'})
        self.assertEqual(pres.lineas.count(), 2)
