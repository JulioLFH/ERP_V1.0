"""PLE del Libro de Inventarios y Balances (formatos 3.x): nombres de archivo, número de campos e importes."""
import io
import zipfile
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, Tercero

from . import ple_libro3, reportes
from .models import Asiento, AsientoLinea, CuentaContable

RUC = '20123456789'
P = '203212'          # cierre anual sin otros datos
FECHA = '20321231'


def cuenta(codigo):
    return CuentaContable.objects.filter(codigo=codigo).first() or CuentaContable.objects.create(
        codigo=codigo, nombre=f'Cuenta {codigo}', imputable=True)


def asiento(fecha, glosa, *lineas):
    a = Asiento.objects.create(fecha=fecha, glosa=glosa)
    for codigo, debe, haber, *resto in lineas:
        tercero, documento = (list(resto) + [None, ''][len(resto):])[:2]
        AsientoLinea.objects.create(asiento=a, cuenta=cuenta(codigo), debe=D(debe), haber=D(haber), tercero=tercero,
                                    documento=documento)
    return a


class PleLibro3Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        empresa = Empresa.actual()
        empresa.ruc = RUC
        empresa.save()
        cli = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20600000011', nombre='CLIENTE | SAC')
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000022', nombre='PROVEEDOR SAC')
        cls.trab = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='1', numero_doc='45678912', nombre='PEREZ ANA')
        socio = Tercero.objects.create(tipo='AMBOS', tipo_doc='1', numero_doc='40000001', nombre='SOCIO UNO')
        # año anterior: saldo inicial del balance de comprobación
        asiento(date(2031, 12, 15), 'Aporte inicial', ('1041', '500', '0'), ('5011', '0', '500', socio))
        asiento(date(2032, 1, 5), 'Aporte de capital', ('1041', '10000', '0'), ('5011', '0', '10000', socio))
        cls.venta = asiento(date(2032, 3, 10), 'Venta F001-1', ('1212', '1180', '0', cli, '01 F001-1'),
                            ('7011', '0', '1000'), ('40111', '0', '180'))
        asiento(date(2032, 4, 10), 'Compra E001-5', ('6011', '500', '0'), ('40111', '90', '0'),
                ('4212', '0', '590', prov, '01 E001-5'))
        asiento(date(2032, 5, 2), 'Préstamo al personal', ('1411', '300', '0', cls.trab), ('1041', '0', '300'))
        asiento(date(2032, 6, 30), 'Planilla', ('6211', '2000', '0'), ('4111', '0', '1700', cls.trab),
                ('4031', '0', '300'))
        asiento(date(2032, 7, 1), 'Software', ('3431', '1200', '0'), ('1041', '0', '1200'))
        asiento(date(2032, 12, 31), 'Amortización', ('6821', '100', '0'), ('3921', '0', '100'))
        asiento(date(2032, 12, 31), 'Cobranza dudosa', ('6841', '200', '0'), ('1911', '0', '200', cli, '01 F001-1'))

    def setUp(self):
        self.client.force_login(self.user)
        self.ctx = ple_libro3.contexto(P)

    def txt(self, codigo):
        nombre, lineas = ple_libro3.generar(codigo, self.ctx)
        return nombre, [l.split('|') for l in lineas]

    def test_nombres_de_archivo(self):
        self.assertEqual(self.txt('031700')[0], f'LE{RUC}{FECHA}03170001111' + '1.txt')
        self.assertEqual(self.txt('030700')[0], f'LE{RUC}{FECHA}03070001101' + '1.txt')  # sin inventario: vacío
        self.assertEqual(self.ctx['oportunidad'], '01')
        junio = ple_libro3.contexto('203206')
        self.assertEqual((junio['fecha'], junio['oportunidad']), ('20320630', '07'))
        self.assertEqual(ple_libro3.contexto('203206', '03')['oportunidad'], '03')
        nombre, _ = ple_libro3.generar('030300', ple_libro3.contexto('203202', '04'))
        self.assertEqual(nombre, f'LE{RUC}20320229030300' + '04101' + '1.txt')

    def test_numero_de_campos_y_periodo(self):
        for codigo in ple_libro3.CONSTRUCTORES:
            _, filas = self.txt(codigo)
            for f in filas:
                self.assertEqual(len(f), len(ple_libro3.ESTRUCTURAS[codigo]) + 1, (codigo, f))
                self.assertEqual((f[0], f[-1], f[-2]), (FECHA, '', '1'), (codigo, f))

    def test_detalles_de_cuentas_por_cobrar_y_pagar(self):
        _, cobrar = self.txt('030300')
        numero = self.venta.numero.replace('-', '')
        self.assertEqual(cobrar, [[FECHA, numero, 'M1', '6', '20600000011', 'CLIENTE SAC', '10/03/2032', '1180.00',
                                   '1', '']])
        _, pagar = self.txt('031200')
        self.assertEqual(pagar[0][3:9], ['6', '20600000022', '10/04/2032', 'PROVEEDOR SAC', '590.00', '1'])
        self.assertEqual(pagar[0][2], 'M3')  # tercera línea del asiento de la compra
        _, dudosa = self.txt('030600')
        self.assertEqual(dudosa[0][6:11], ['01', 'F001', '1', '31/12/2032', '200.00'])
        _, personal = self.txt('030400')
        self.assertEqual(personal[0][3:8], ['1', '45678912', 'PEREZ ANA', '02/05/2032', '300.00'])
        _, remuneraciones = self.txt('031100')
        self.assertEqual(remuneraciones[0][3:9], ['4111', '1', '45678912', '45678912', 'PEREZ ANA', '1700.00'])

    def test_caja_intangibles_y_capital(self):
        _, caja = self.txt('030200')
        self.assertEqual(caja, [[FECHA, '1041', '99', '-', 'PEN', '9000.00', '0.00', '1', '']])
        _, intangibles = self.txt('030900')
        self.assertEqual([(f[5], f[6], f[7]) for f in intangibles], [('03', '1200.00', '0.00'),
                                                                      ('08', '0.00', '100.00')])
        _, capital = self.txt('031601')
        self.assertEqual(capital, [[FECHA, '10500.00', '1.00', '10500', '10500', '1', '']])
        _, socios = self.txt('031602')
        self.assertEqual(socios, [[FECHA, '1', '40000001', '1', 'SOCIO UNO', '10500', '100.00', '1', '']])

    def test_balance_de_comprobacion_cuadra(self):
        filas = ple_libro3.balance_comprobacion(P)
        tot = {k: sum((f[k] for f in filas), D('0')) for k in filas[0] if k != 'cuenta'}
        self.assertEqual(tot['mov_debe'], tot['mov_haber'])
        self.assertEqual(tot['sum_debe'], tot['sum_haber'])
        self.assertEqual(tot['saldo_deudor'], tot['saldo_acreedor'])
        self.assertEqual((tot['si_debe'], tot['si_haber']), (D('500'), D('500')))
        # movimientos = balance de comprobación del año; saldos = el acumulado al corte
        _, anual = reportes.balance_comprobacion(f'{P[:4]}01', P)
        _, acumulado = reportes.balance_comprobacion('000000', P)
        self.assertEqual((tot['mov_debe'], tot['mov_haber']), (anual['d'], anual['h']))
        self.assertEqual((tot['saldo_deudor'], tot['saldo_acreedor']), (acumulado['sd'], acumulado['sa']))
        self.assertEqual((tot['activo'] - tot['pasivo']), tot['ganancias'] - tot['perdidas'])
        caja = next(f for f in filas if f['cuenta'] == '1041')
        self.assertEqual((caja['si_debe'], caja['mov_debe'], caja['mov_haber'], caja['saldo_deudor']),
                         (D('500'), D('10000'), D('1500'), D('9000')))
        _, txt = self.txt('031700')
        self.assertEqual(len(txt), len(filas))

    def test_estados_financieros(self):
        _, situacion = self.txt('030100')
        rubros = {f[2]: D(f[3]) for f in situacion}
        t = reportes.situacion_financiera(P, f'{P[:4]}01')['t']
        self.assertEqual((rubros['1D020T'], rubros['1D070T']), (t['activo'], t['pasivo_pat']))
        self.assertEqual(rubros['1D0101'], D('9000'))
        _, resultados = self.txt('032000')
        rubros = {f[2]: D(f[3]) for f in resultados}
        self.assertEqual(rubros['2D01ST'], D('1000'))
        self.assertEqual(rubros['2D07ST'], reportes.estado_resultados(f'{P[:4]}01', P)[1])
        _, flujo = self.txt('031800')
        self.assertIn('3D04ST', [f[2] for f in flujo])

    def test_descargas_desde_la_pantalla(self):
        url = reverse('contabilidad:libro_inventarios')
        r = self.client.get(url, {'formato': '3.17', 'periodo': '2032-12'})
        self.assertContains(r, 'value="031700"')
        self.assertContains(r, 'ZIP con todos los 3.x')
        r = self.client.get(url, {'formato': '3.17', 'periodo': '2032-12', 'ple': '031700'})
        self.assertIn('text/plain', r['Content-Type'])
        self.assertIn(f'LE{RUC}{FECHA}031700011111.txt', r['Content-Disposition'])
        r = self.client.get(url, {'formato': '3.3', 'periodo': '2032-06', 'oportunidad': '03', 'ple': '030300'})
        self.assertIn(f'LE{RUC}20320630030300031', r['Content-Disposition'])
        r = self.client.get(url, {'formato': '3.3', 'periodo': '2032-12', 'ple': 'zip'})
        self.assertEqual(r['Content-Type'], 'application/zip')
        nombres = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        self.assertEqual(len(nombres), len(ple_libro3.CONSTRUCTORES))
        self.assertIn(f'LE{RUC}{FECHA}031602011111.txt', nombres)
        self.assertEqual(self.client.get(url, {'formato': '3.3', 'ple': '030800'}).status_code, 404)
