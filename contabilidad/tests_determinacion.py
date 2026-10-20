"""Determinación de la cuenta contable (v1.35): nivel, columna del balance, moneda, tipo de cambio, análisis por
documento y cuenta analítica (centro de costo). Un escenario por clase."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import pcge, reportes
from .models import Asiento, AsientoLinea, CentroCosto, CuentaContable, CuentaDefecto

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

    def cuenta(self, prefijo):
        return CuentaContable.objects.filter(codigo__startswith=prefijo, imputable=True).order_by('codigo').first()

    def asiento(self, lineas, tipo_cambio='1'):
        """Asiento manual por la pantalla, con su sustento: lineas = [(cuenta, debe, haber, extras)]."""
        hoy = timezone.localdate()
        data = {'fecha': hoy.isoformat(), 'libro': '05', 'glosa': 'Prueba de determinación', 'moneda': 'PEN',
                'tipo_cambio': tipo_cambio, 'lineas-TOTAL_FORMS': str(len(lineas)), 'lineas-INITIAL_FORMS': '0',
                'lineas-MIN_NUM_FORMS': '0', 'lineas-MAX_NUM_FORMS': '1000',
                'sustento': SimpleUploadedFile('acta.pdf', b'%PDF-1.4 acta', content_type='application/pdf')}
        for i, (c, debe, haber, extras) in enumerate(lineas):
            data.update({f'lineas-{i}-cuenta': c.pk, f'lineas-{i}-debe': debe, f'lineas-{i}-haber': haber})
            data.update({f'lineas-{i}-{k}': v for k, v in extras.items()})
        return self.client.post(reverse('contabilidad:asiento_nuevo'), data)


class ValoresPorDefectoTest(Base):
    def test_cada_cuenta_trae_su_determinacion_segun_el_codigo(self):
        self.assertEqual((pcge.nivel('10'), pcge.nivel('104'), pcge.nivel('1041'), pcge.nivel('10411')), (1, 2, 3, 4))
        self.assertEqual(pcge.nombre_nivel('1041'), 'Divisionaria')
        cxc = CuentaContable.objects.get(codigo='1212')
        self.assertEqual((cxc.columna_balance, cxc.tipo_cambio, cxc.analisis, cxc.centro_costo_regla),
                         ('INVENTARIO', 'COMPRA', True, 'NO'))
        cxp = CuentaContable.objects.get(codigo='4212')
        self.assertEqual((cxp.tipo_cambio, cxp.analisis), ('VENTA', True))
        gasto = self.cuenta('63')
        self.assertEqual((gasto.columna_balance, gasto.centro_costo_regla, gasto.analisis),
                         ('NATURALEZA', 'OBLIGATORIO', False))
        self.assertEqual(self.cuenta('70').columna_balance, 'RESULTADO')
        self.assertEqual(self.cuenta('94').columna_balance, 'FUNCION')
        self.assertEqual(self.cuenta('79').columna_balance, 'SALDO')
        from finanzas.models import Cuenta
        usd = Cuenta.objects.filter(moneda='USD').exclude(cuenta_contable=None).first()
        self.assertEqual(usd.cuenta_contable.moneda, 'USD')  # la subcuenta del banco en dólares
        nueva = CuentaContable.objects.create(codigo='63999', nombre='Prueba')  # creada desde código: completa sola
        self.assertEqual((nueva.centro_costo_regla, nueva.columna_balance), ('OBLIGATORIO', 'NATURALEZA'))


class AsientoManualTest(Base):
    def test_analisis_centro_de_costo_y_dolares(self):
        cxc, caja = CuentaContable.objects.get(codigo='1212'), CuentaContable.objects.get(codigo='1011')
        r = self.asiento([(caja, '100', '', {}), (cxc, '', '100', {})])
        self.assertContains(r, 'se analiza por documento')  # la 12 exige el N° de documento
        r = self.asiento([(caja, '100', '', {}), (cxc, '', '100', {'documento': 'F001-55'})])
        self.assertEqual(r.status_code, 302)
        # la cuenta analítica sigue a la configuración: el gasto deja de exigir centro, la caja pasa a exigirlo
        gasto = self.cuenta('63')
        r = self.asiento([(gasto, '50', '', {}), (caja, '', '50', {})])
        self.assertContains(r, 'exige centro de costo')
        gasto.centro_costo_regla = 'OPCIONAL'
        gasto.save()
        caja.centro_costo_regla = 'OBLIGATORIO'
        caja.save()
        r = self.asiento([(gasto, '50', '', {}), (caja, '', '50', {})])
        self.assertContains(r, f'La cuenta {caja.codigo} exige centro de costo')
        centro = CentroCosto.objects.filter(activo=True).first()
        r = self.asiento([(gasto, '50', '', {}), (caja, '', '50', {'centro_costo': centro.pk})])
        self.assertEqual(r.status_code, 302)
        # cuenta en dólares: exige el tipo de cambio y guarda el importe en US$
        from finanzas.models import Cuenta
        banco_usd = Cuenta.objects.filter(moneda='USD').exclude(cuenta_contable=None).first().cuenta_contable
        lineas = [(banco_usd, '350', '', {}), (CuentaContable.objects.get(codigo='5011'), '', '350', {})]
        self.assertContains(self.asiento(lineas, tipo_cambio='1'), 'es en dólares')
        self.assertEqual(self.asiento(lineas, tipo_cambio='3.500').status_code, 302)
        linea = AsientoLinea.objects.filter(cuenta=banco_usd, asiento__origen='MANUAL').latest('id')
        self.assertEqual((linea.debe, linea.debe_me), (D('350.00'), D('100.00')))


class ColumnaDelBalanceTest(Base):
    def test_el_balance_usa_la_columna_de_cada_cuenta(self):
        hoy = timezone.localdate()
        periodo = hoy.strftime('%Y%m')
        a = Asiento.objects.create(fecha=hoy, libro='05', origen='MANUAL', glosa='Prueba')
        capital = CuentaContable.objects.get(codigo='5011')
        AsientoLinea.objects.create(asiento=a, cuenta=CuentaContable.objects.get(codigo='1011'), debe=D('1000'))
        AsientoLinea.objects.create(asiento=a, cuenta=capital, haber=D('1000'))
        filas, tot = reportes.balance_comprobacion(periodo, periodo)
        fila = next(f for f in filas if f['codigo'] == '5011')
        self.assertEqual((fila['columna'], fila.get('pasivo')), ('INVENTARIO', fila['sa']))
        pasivo = tot['pasivo']
        capital.columna_balance = 'SALDO'  # ya no pasa al balance general
        capital.save()
        filas, tot = reportes.balance_comprobacion(periodo, periodo)
        self.assertEqual(tot['pasivo'], pasivo - next(f for f in filas if f['codigo'] == '5011')['sa'])


class FormularioCuentaTest(Base):
    def test_nueva_cuenta_con_su_determinacion(self):
        url = reverse('contabilidad:cuenta_nueva')
        base = {'nombre': 'Servicio de vigilancia', 'naturaleza': 'DEUDORA', 'imputable': 'on', 'activo': 'on',
                'moneda': 'PEN', 'analisis': 'unknown'}
        CuentaContable.objects.filter(codigo__startswith='98').delete()
        r = self.client.post(url, {**base, 'codigo': '98991'})
        self.assertContains(r, 'Primero cree la cuenta 98')  # no cuelga de ninguna cuenta existente
        r = self.client.post(url, {**base, 'codigo': '10410099', 'nombre': 'Banco nuevo'})  # auxiliar bajo la 1041
        self.assertEqual(r.status_code, 302)
        padre = self.cuenta('639')
        codigo = padre.codigo + '9'
        r = self.client.post(url, {**base, 'codigo': codigo, 'columna_balance': 'SALDO',
                                   'centro_costo_regla': 'OPCIONAL', 'tipo_cambio': '', 'analisis': 'true'})
        self.assertEqual(r.status_code, 302)
        c = CuentaContable.objects.get(codigo=codigo)
        self.assertEqual((c.columna_balance, c.centro_costo_regla, c.tipo_cambio, c.analisis),
                         ('SALDO', 'OPCIONAL', 'COMPRA', True))  # lo vacío, automático según el código
        r = self.client.post(url, {**base, 'codigo': padre.codigo + '8', 'moneda': 'USD'})
        self.assertContains(r, 'se llevan en soles')
        r = self.client.get(reverse('contabilidad:plan') + f'?q={codigo}')
        self.assertContains(r, 'Subdivisionaria' if len(codigo) == 5 else 'nivel')
        self.assertContains(r, 'Solo saldo')
        self.assertContains(self.client.get(reverse('contabilidad:cuenta_editar', args=[c.pk])), 'Columna del balance')


class TipoCambioCierreTest(Base):
    def test_ajuste_al_cierre_usa_el_tipo_de_cambio_de_la_cuenta(self):
        from finanzas.models import Cuenta

        from .centralizar import asiento_cambio_cierre
        periodo = timezone.localdate().strftime('%Y%m')
        tc = SimpleNamespace(compra=D('3.400'), venta=D('3.500'))
        Cuenta.objects.filter(moneda='USD').update(activo=True)
        usd = list(Cuenta.objects.filter(moneda='USD').exclude(cuenta_contable=None))
        AsientoLinea.objects.filter(cuenta__in=[c.cuenta_contable for c in usd]).delete()
        for c in usd:
            CuentaContable.objects.filter(pk=c.cuenta_contable_id).update(tipo_cambio='VENTA')
        with patch.object(Cuenta, 'saldo_al', return_value=D('100')):
            a = asiento_cambio_cierre(periodo, CuentaDefecto.mapa(), tc)
        linea = a.lineas.get(cuenta=usd[0].cuenta_contable)
        self.assertEqual(linea.debe, D('350.00'))  # 100 US$ x 3.500 (venta), no 340 (compra)


class CargaMasivaTest(Base):
    def test_columnas_de_determinacion_en_el_excel(self):
        from core.carga_masiva import ErrorFila, _fila_cuenta
        f = _fila_cuenta({'codigo': '63998', 'nombre': 'Prueba', 'columna_balance': 'Saldo', 'moneda': 'Dólares',
                          'tipo_cambio': 'venta', 'analisis': 'Sí', 'centro_costo_regla': 'no lleva'}, False)
        self.assertEqual({k: f['datos'][k] for k in ('columna_balance', 'moneda', 'tipo_cambio', 'analisis',
                                                     'centro_costo_regla')},
                         {'columna_balance': 'SALDO', 'moneda': 'USD', 'tipo_cambio': 'VENTA', 'analisis': True,
                          'centro_costo_regla': 'NO'})
        with self.assertRaises(ErrorFila):
            _fila_cuenta({'codigo': '63997', 'nombre': 'X', 'columna_balance': 'Otra'}, False)
