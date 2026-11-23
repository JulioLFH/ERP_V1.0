"""v1.117.2: balance de comprobación como la hoja de trabajo del PCGE. Por naturaleza van la clase 6 (60 a 68) y la
7; por función el costo de ventas (69), la clase 9 y la 7. El costo de ventas llega a la naturaleza cancelado contra
la variación de existencias (61, mercaderías) o de la producción (71, productos); las compras sin pasar al costo y los
gastos sin destino son costo por función. Los tres resultados son el mismo."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import pcge, ple_libro3, reportes
from .models import Asiento, AsientoLinea, CuentaContable

D = Decimal
ANIO = timezone.localdate().year - 1
MES = f'{ANIO}03'


class HojaDeTrabajoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t1172', 't@t.com', 'x')
        adm = pcge.funciones()['administracion']
        cls.adm = (CuentaContable.objects.filter(codigo__startswith=adm).order_by('-codigo').first()
                   or CuentaContable.objects.filter(codigo__startswith='9').order_by('-codigo').first())
        c = {x.codigo: x for x in CuentaContable.objects.filter(codigo__in=[
            '6011', '4212', '20111', '6111', '1041', '70111', '69111', '2111', '7111', '6921', '6211', '7911'])}

        def asiento(glosa, *lineas):
            a = Asiento.objects.create(fecha=date(ANIO, 3, 15), glosa=glosa)
            for cuenta, debe, haber in lineas:
                AsientoLinea.objects.create(asiento=a, cuenta=cuenta, debe=D(debe), haber=D(haber))
        asiento('Compra', (c['6011'], 1000, 0), (c['4212'], 0, 1000))
        asiento('Destino de la compra', (c['20111'], 1000, 0), (c['6111'], 0, 1000))
        asiento('Venta', (c['1041'], 1500, 0), (c['70111'], 0, 1500))
        asiento('Costo de la venta', (c['69111'], 600, 0), (c['20111'], 0, 600))
        asiento('Producción terminada', (c['2111'], 400, 0), (c['7111'], 0, 400))
        asiento('Costo de los productos vendidos', (c['6921'], 250, 0), (c['2111'], 0, 250))
        asiento('Sueldos', (c['6211'], 200, 0), (c['1041'], 0, 200))
        asiento('Destino de sueldos (parcial)', (cls.adm, 150, 0), (c['7911'], 0, 150))
        asiento('Compra sin destino (inventario periódico)', (c['6011'], 80, 0), (c['4212'], 0, 80))
        # resultado: 1500 + 400 − 1080 + 1000 − 200 − 600 − 250 = 770

    def test_columnas_por_defecto_y_cuentas_ya_creadas(self):
        self.assertEqual([pcge.columna_por_defecto(c) for c in ('6011', '6111', '6211', '69111', '9411', '70111', '7911')],
                         ['NATURALEZA', 'NATURALEZA', 'NATURALEZA', 'FUNCION', 'FUNCION', 'RESULTADO', 'SALDO'])
        guardadas = dict(CuentaContable.objects.filter(codigo__in=['6011', '6111', '69111', '6921'])
                         .values_list('codigo', 'columna_balance'))
        self.assertEqual(guardadas, {'6011': 'NATURALEZA', '6111': 'NATURALEZA', '69111': 'FUNCION', '6921': 'FUNCION'})
        self.assertEqual([pcge.variacion_del_costo_de_ventas(c) for c in ('69111', '6951', '6921', '6952')],
                         ['61', '61', '71', '71'])

    def test_balance_cancela_el_costo_y_los_tres_resultados_coinciden(self):
        filas, tot = reportes.balance_comprobacion(MES, MES)
        fila = {f['codigo']: f for f in filas}
        self.assertEqual((fila['69111'].get('nat_p', 0), fila['69111']['fun_p']), (0, D('600')))  # solo por función
        self.assertEqual((fila['6011']['nat_p'], fila['6011'].get('fun_p', 0)), (D('1080'), 0))  # solo naturaleza
        def ajuste(inicio):
            return next(a for a in tot['ajustes'] if a['nombre'].startswith(inicio))
        self.assertEqual(ajuste('Costo de ventas de mercaderías')['nat_p'], D('600'))
        self.assertEqual(ajuste('Costo de ventas de productos')['nat_p'], D('250'))
        self.assertEqual(ajuste('Compras y variación de existencias')['fun_p'], D('80'))
        self.assertEqual(ajuste('Gastos 62-68 sin destino')['fun_p'], D('50'))
        self.assertEqual((tot['res_bal'], tot['res_nat'], tot['res_fun']), (D('770'), D('770'), D('770')))
        for nivel in (2, 3):  # agrupado: las transferencias salen de las cuentas a detalle
            _, t = reportes.balance_comprobacion(MES, MES, nivel)
            self.assertEqual((t['res_bal'], t['res_nat'], t['res_fun']), (D('770'), D('770'), D('770')))

    def test_estado_por_naturaleza_sin_linea_de_costo_de_ventas(self):
        lineas, neta = reportes.estado_resultados_naturaleza(MES, MES)
        valores = {n: v for n, v, _ in lineas}
        self.assertNotIn('Costo de ventas', valores)
        self.assertEqual(valores['Compras y variación de existencias'], D('-680'))  # 1080 − 1000 + 600
        self.assertEqual(valores['Variación de la producción almacenada'], D('150'))  # 400 − 250
        self.assertEqual(neta, D('770'))
        self.assertEqual(reportes.estado_resultados(MES, MES)[1], D('770'))
        detalle = reportes.detalle_resultados(MES, MES, 'naturaleza')
        self.assertEqual(sum(f['importe'] for f in detalle['Compras y variación de existencias']), D('-680'))
        self.assertEqual(sum(f['importe'] for f in detalle['Variación de la producción almacenada']), D('150'))

    def test_ple_317_cancela_el_costo_en_transferencias(self):
        filas = {f['cuenta']: f for f in ple_libro3.balance_comprobacion(MES)}
        self.assertEqual((filas['69111']['transf_haber'], filas['69111']['perdidas']), (D('600'), 0))
        self.assertEqual((filas['6111']['transf_debe'], filas['6111']['ganancias']), (D('600'), D('400')))
        self.assertEqual((filas['6921']['transf_haber'], filas['7111']['transf_debe'], filas['7111']['ganancias']),
                         (D('250'), D('250'), D('150')))
        resultado = sum(f['ganancias'] - f['perdidas'] for f in filas.values())
        self.assertEqual(resultado, D('770'))
        self.assertEqual(sum(f['transf_debe'] for f in filas.values()), sum(f['transf_haber'] for f in filas.values()))

    def test_pantalla_y_excel(self):
        self.client.force_login(self.user)
        url = reverse('contabilidad:balance') + f'?desde={ANIO}-03&hasta={ANIO}-03'
        r = self.client.get(url)
        self.assertContains(r, 'cancelado contra la variación de existencias (61)')
        self.assertContains(r, 'Transferencias y cancelaciones de la hoja de trabajo')
        self.assertEqual(self.client.get(url + '&formato=excel').status_code, 200)
