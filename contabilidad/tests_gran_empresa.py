"""v1.23: préstamos y leasing, libros paralelos NIIF / tributario, provisiones y cierre guiado, consolidación."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, Tercero
from erp.empresas import activar, restaurar
from finanzas import prestamos
from finanzas.models import Cuenta, Movimiento, Prestamo
from planillas.models import Parametro, Trabajador
from planillas.provisiones import provisiones_del_mes

from . import cierre, reportes
from .centralizar import centralizar_periodo
from .models import Asiento, AsientoLinea, CuentaContable, PeriodoContable, usar_norma

D = Decimal
HOY = date.today()
PERIODO = HOY.strftime('%Y%m')


class GranEmpresaContableTest(TestCase):
    databases = {'default', 'empresa2'}

    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('conta', 'c@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.banco = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100047218', nombre='BANCO X')
        self.cuenta = Cuenta.objects.filter(moneda='PEN', tipo='BANCO').first() or Cuenta.objects.create(
            nombre='Banco soles', tipo='BANCO')

    def _cuenta(self, codigo):
        return CuentaContable.objects.get(codigo=codigo)

    def _lineas(self, origen, codigo):
        return AsientoLinea.objects.filter(asiento__origen=origen, asiento__periodo=PERIODO, cuenta__codigo=codigo)

    def test_prestamo_cronograma_desembolso_y_cuota(self):
        p = prestamos.crear(Prestamo(
            tipo='PRESTAMO', entidad=self.banco, descripcion='Capital de trabajo', monto=D('12000'), tasa_anual=D('12'),
            plazo=12, fecha_desembolso=HOY - timedelta(days=1), primera_cuota=HOY + timedelta(days=29),
            cuenta=self.cuenta), self.user)
        cuotas = list(p.cuotas.all())
        self.assertEqual(sum(c.capital for c in cuotas), D('12000'))
        self.assertEqual(cuotas[-1].saldo, 0)
        self.assertEqual(len({c.capital + c.interes for c in cuotas[:-1]}), 1)  # cuota fija
        self.assertEqual(p.desembolso.monto, D('12000'))
        c1 = cuotas[0]
        r = self.client.post(reverse('finanzas:prestamo', args=[p.pk]), {'accion': 'pagar', 'cuota': c1.pk,
                                                                         'fecha': HOY.isoformat()})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(c1.pagada)
        self.assertEqual(p.saldo_capital, D('12000') - c1.capital)
        with self.assertRaises(prestamos.ErrorTesoreria):  # no se paga dos veces
            prestamos.pagar_cuota(c1, HOY, self.user)
        self.assertEqual(centralizar_periodo(PERIODO)['errores'], [])
        pago = Movimiento.objects.get(cuota=c1).asientos.get()
        lineas = {l.cuenta.codigo: l for l in pago.lineas.filter(es_destino=False)}
        self.assertEqual(lineas['4511'].debe, c1.capital)
        self.assertEqual(lineas['6731'].debe, c1.interes)
        # al anular el pago, la cuota vuelve a estar pendiente
        Movimiento.objects.filter(cuota=c1).update(estado='ANULADO')
        self.assertFalse(c1.pagada)

    def test_leasing_reconoce_el_bien(self):
        p = prestamos.crear(Prestamo(
            tipo='LEASING', entidad=self.banco, descripcion='Camión', monto=D('100000'), tasa_anual=D('10'),
            plazo=36, fecha_desembolso=HOY, primera_cuota=HOY + timedelta(days=30), opcion_compra=D('1000'),
            cuenta=self.cuenta), self.user)
        self.assertIsNone(p.desembolso)  # el banco paga al proveedor: no entra dinero
        self.assertGreater(p.cuotas.first().igv, 0)
        self.assertGreater(p.cuotas.last().capital, D('1000'))  # la última cuota lleva la opción de compra
        centralizar_periodo(PERIODO)
        self.assertEqual(self._lineas('PRESTAMO', '3223').get().debe, D('100000'))
        self.assertEqual(self._lineas('PRESTAMO', '4521').get().haber, D('100000'))
        self.assertEqual(self.client.get(reverse('finanzas:prestamos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('finanzas:prestamo', args=[p.pk])).status_code, 200)

    def test_libros_paralelos(self):
        a = Asiento.objects.create(fecha=HOY, libro='05', glosa='Depreciación tributaria acelerada', norma='TRIBUTARIO')
        AsientoLinea.objects.create(asiento=a, cuenta=self._cuenta('6814'), debe=D('500'), norma='TRIBUTARIO')
        AsientoLinea.objects.create(asiento=a, cuenta=self._cuenta('3913'), haber=D('500'), norma='TRIBUTARIO')
        niif = reportes.sumas_por_cuenta(PERIODO, PERIODO)
        with usar_norma('TRIBUTARIO'):
            trib = reportes.sumas_por_cuenta(PERIODO, PERIODO)
        self.assertEqual(trib['6814'][0] - niif.get('6814', (D('0'), D('0')))[0], D('500'))
        datos = cierre.conciliacion_normas(PERIODO, PERIODO)
        self.assertEqual(datos['dif_resultado'], D('-500'))
        self.assertEqual(a.lineas.count(), 2)  # el detalle del asiento muestra todas sus líneas
        mes = f'{PERIODO[:4]}-{PERIODO[4:]}'
        self.assertNotContains(self.client.get(reverse('contabilidad:diario') + f'?periodo={mes}'),
                               'Depreciación tributaria acelerada')
        self.assertContains(self.client.get(reverse('contabilidad:diario') + f'?periodo={mes}&norma=TRIBUTARIO'),
                            'Depreciación tributaria acelerada')
        self.assertContains(self.client.get(reverse('contabilidad:conciliacion_normas')), '3913')

    def test_provisiones_y_cierre_guiado(self):
        Trabajador.objects.create(numero_doc='40000001', apellido_paterno='PEREZ', nombres='ANA', sueldo=D('3000'),
                                  fecha_ingreso=date(HOY.year - 2, 1, 1), regimen='GENERAL')
        Trabajador.objects.create(numero_doc='40000002', apellido_paterno='RIOS', nombres='LUIS', sueldo=D('2400'),
                                  fecha_ingreso=date(HOY.year - 2, 1, 1), regimen='MICRO')
        datos = provisiones_del_mes(PERIODO)
        ana = next(f for f in datos['por_trabajador'] if f['t'].numero_doc == '40000001')
        luis = next(f for f in datos['por_trabajador'] if f['t'].numero_doc == '40000002')
        param = Parametro.objects.filter(anio__lte=HOY.year).order_by('-anio').first()
        bonif = 1 + (param.bonificacion_extraordinaria_pct if param else D('9')) / 100
        self.assertEqual(ana['gratificacion'], (D('500') * bonif).quantize(D('0.01')))
        self.assertEqual((ana['cts'], ana['vacaciones']), (D('291.67'), D('250.00')))
        self.assertEqual((luis['gratificacion'], luis['cts'], luis['vacaciones']), (0, 0, D('100.00')))
        r = self.client.get(reverse('contabilidad:cierre'))
        self.assertContains(r, 'Provisiones de beneficios sociales')
        self.client.post(reverse('contabilidad:cierre'), {'periodo': PERIODO, 'accion': 'provisionar'})
        self.assertTrue(PeriodoContable.objects.get(periodo=PERIODO).provisiones)
        self.client.post(reverse('contabilidad:cierre'), {'periodo': PERIODO, 'accion': 'centralizar'})
        self.assertEqual(self._lineas('PROVISION', '4115').get().haber, D('350.00'))
        self.client.post(reverse('contabilidad:cierre'), {'periodo': PERIODO, 'accion': 'cerrar', 'forzar': '1'})
        self.assertTrue(PeriodoContable.esta_cerrado(PERIODO))

    def test_consolidacion_elimina_operaciones_del_grupo(self):
        Empresa.objects.update(ruc='20100000001')
        token = activar('empresa2')
        try:
            from core.models import Empresa as E2
            E2.objects.update_or_create(pk=E2.objects.first().pk if E2.objects.exists() else None,
                                        defaults={'ruc': '20200000002', 'razon_social': 'HERMANA S.A.C.'})
        finally:
            restaurar(token)
        hermana = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20200000002', nombre='HERMANA SAC')
        m = Movimiento.objects.create(cuenta=self.cuenta, tipo='INGRESO', concepto='OTRO', tercero=hermana,
                                      monto=D('100'), fecha=HOY)
        centralizar_periodo(PERIODO)
        self.assertTrue(m.asientos.exists())
        datos = cierre.consolidar(PERIODO)
        principal = next(e for e in datos['empresas'] if e['alias'] == 'default')
        # el ingreso con la empresa hermana está en su resultado pero se elimina en el consolidado
        self.assertEqual(principal['eliminado'], D('100'))
        self.assertEqual(self.client.get(reverse('contabilidad:consolidacion')).status_code, 200)
