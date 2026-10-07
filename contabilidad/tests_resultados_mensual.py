"""Estado de resultados mes a mes (v1.25): una columna por mes, por función y por naturaleza."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import reportes
from .models import Asiento, AsientoLinea, CuentaContable

D = Decimal
ANIO = date.today().year - 1


class ResultadosMensualTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        c = {x.codigo: x for x in CuentaContable.objects.filter(codigo__in=['70111', '6399', '1041', '6211'])}
        for mes, venta, gasto in ((1, '1000', '300'), (2, '500', '700'), (3, '0', '100')):
            a = Asiento.objects.create(fecha=date(ANIO, mes, 15), glosa=f'Mes {mes}')
            AsientoLinea.objects.create(asiento=a, cuenta=c['1041'], debe=D(venta) - D(gasto) if D(venta) > D(gasto)
                                        else 0, haber=D(gasto) - D(venta) if D(gasto) > D(venta) else 0)
            if D(venta):
                AsientoLinea.objects.create(asiento=a, cuenta=c['70111'], haber=D(venta))
            AsientoLinea.objects.create(asiento=a, cuenta=c['6399'], debe=D(gasto))

    def test_columnas_por_mes_suman_el_total(self):
        desde, hasta = f'{ANIO}01', f'{ANIO}12'
        for vista in ('funcion', 'naturaleza'):
            meses, filas = reportes.resultados_mensual(desde, hasta, vista)
            self.assertEqual(len(meses), 12)
            resultado = next(f for f in filas if f['nombre'] == 'RESULTADO DEL EJERCICIO')
            self.assertEqual(resultado['valores'][:3], [D('700'), D('-200'), D('-100')])
            self.assertEqual(sum(resultado['valores'], D('0')), resultado['total'])
            self.assertEqual(resultado['total'], D('400'))
        _, neta_nat = reportes.estado_resultados_naturaleza(desde, hasta)
        _, neta_fun = reportes.estado_resultados(desde, hasta)
        self.assertEqual(neta_nat, neta_fun)

    def test_pantalla_y_excel(self):
        url = reverse('contabilidad:resultados') + f'?desde={ANIO}-01&hasta={ANIO}-12&comparar=meses'
        r = self.client.get(url)
        self.assertContains(r, 'desglose mensual')
        self.assertContains(r, f'12/{ANIO}')
        self.assertContains(self.client.get(url + '&vista=naturaleza'), 'Gastos de personal')
        self.assertEqual(self.client.get(url + '&formato=excel').status_code, 200)
