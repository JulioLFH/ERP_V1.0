"""Libro Caja y Bancos: formato 1.1 (efectivo) y 1.2 (cuenta corriente), Excel y TXT del PLE (010100 / 010200)."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from contabilidad import pcge
from contabilidad.models import Asiento
from core.models import Empresa, Tercero, TipoCambio

from . import libro_caja
from .models import Cuenta, Movimiento


class LibroCajaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('lc', 'lc@t.com', 'x')
        cls.empresa = Empresa.actual()
        cls.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970',
                                             nombre='CLIENTE CAJA SAC')
        cls.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100047218',
                                          nombre='PROVEEDOR CAJA SAC')

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(pcge.limpiar_detalle)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.700'), D('3.750')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.user)
        self.caja = Cuenta.objects.create(tipo='CAJA', nombre='Caja principal', moneda='PEN', saldo_inicial=D('1000'))
        self.banco = Cuenta.objects.create(tipo='BANCO', nombre='BCP soles', banco='BCP', numero='191-1234567-0-12',
                                           moneda='PEN', saldo_inicial=D('5000'))
        mov = self.mov
        # enero 2025
        mov(self.caja, date(2025, 1, 10), 'INGRESO', '500', medio_pago='EFECTIVO', glosa='Venta al contado')
        mov(self.caja, date(2025, 1, 20), 'EGRESO', '200', medio_pago='EFECTIVO', glosa='Pago de luz')
        mov(self.banco, date(2025, 1, 15), 'INGRESO', '3000', numero_operacion='0001234', tercero=self.cliente,
            glosa='Aporte del cliente')
        mov(self.banco, date(2025, 1, 25), 'EGRESO', '1000', medio_pago='CHEQUE', numero_operacion='00055',
            tercero=self.prov, glosa='Pago con cheque')
        # febrero 2025
        self.egreso_caja = mov(self.caja, date(2025, 2, 5), 'EGRESO', '100', medio_pago='EFECTIVO', glosa='Movilidad')
        mov(self.banco, date(2025, 2, 8), 'INGRESO', '250', medio_pago='DEPOSITO', numero_operacion='DEP77',
            tercero=self.cliente, glosa='Depósito de cliente')
        mov(self.banco, date(2025, 2, 18), 'EGRESO', '50', concepto='GASTO_BANCARIO', numero_operacion='COM01',
            glosa='Comisión mantenimiento')
        mov(self.banco, date(2025, 2, 20), 'EGRESO', '999', numero_operacion='ANUL999', glosa='Pago anulado',
            estado='ANULADO')

    def mov(self, cuenta, fecha, tipo, monto, **kw):
        kw.setdefault('medio_pago', 'TRANSFERENCIA')
        kw.setdefault('concepto', 'OTRO')
        return Movimiento.objects.create(cuenta=cuenta, fecha=fecha, tipo=tipo, monto=D(monto), **kw)

    def bloques(self, periodo, cuentas=None):
        return {b['cuenta'].pk: b for b in libro_caja.libro(periodo, cuentas or [self.caja, self.banco])}

    # ------------------------------------------------------------ saldos
    def test_saldos_por_periodo_sin_anulados(self):
        ene = self.bloques('202501')
        self.assertEqual(ene[self.caja.pk]['inicial'], D('1000'))
        self.assertEqual(ene[self.caja.pk]['final'], D('1300'))
        self.assertEqual(ene[self.banco.pk]['inicial'], D('5000'))
        self.assertEqual(ene[self.banco.pk]['final'], D('7000'))
        feb = self.bloques('202502')
        caja, banco = feb[self.caja.pk], feb[self.banco.pk]
        self.assertEqual((caja['formato'], banco['formato']), ('1.1', '1.2'))
        self.assertEqual((caja['inicial'], caja['deudor'], caja['acreedor'], caja['final']),
                         (D('1300'), D('0'), D('100'), D('1200')))
        self.assertEqual((banco['inicial'], banco['deudor'], banco['acreedor'], banco['final']),
                         (D('7000'), D('250'), D('50'), D('7200')))
        self.assertEqual((banco['total_deudor'], banco['total_acreedor']), (D('7250'), D('50')))
        self.assertEqual([f['saldo'] for f in banco['filas']], [D('7250'), D('7200')])
        self.assertNotIn('ANUL999', [f['operacion'] for f in banco['filas']])  # el anulado no entra
        self.assertEqual(banco['entidad'], '02')  # tabla 3: Banco de Crédito
        self.assertEqual([f['medio'] for f in banco['filas']], ['001', '003'])  # tabla 1: depósito, transferencia

    def test_cuenta_en_dolares_en_soles(self):
        TipoCambio.objects.create(fecha=date(2025, 1, 2), compra=D('3.690'), venta=D('3.700'))
        TipoCambio.objects.create(fecha=date(2025, 2, 10), compra=D('3.790'), venta=D('3.800'))
        usd = Cuenta.objects.create(tipo='BANCO', nombre='BBVA dólares', banco='BBVA', numero='0011-0661-0100012345',
                                    moneda='USD', saldo_inicial=D('100'))
        self.mov(usd, date(2025, 2, 10), 'INGRESO', '50', glosa='Cobro en dólares')
        b = self.bloques('202502', [usd])[usd.pk]
        self.assertEqual(b['inicial_me'], D('100'))
        self.assertEqual(b['final_me'], D('150'))
        self.assertEqual(b['inicial'], D('370.00'))  # 100 x 3.700 (último T.C. registrado)
        self.assertEqual(b['filas'][0]['deudor'], D('190.00'))  # 50 x 3.800 del día
        self.assertEqual(b['final'], D('560.00'))
        self.assertEqual(b['entidad'], '11')

    # ------------------------------------------------------------ pantalla y Excel
    def test_pantalla(self):
        r = self.client.get(reverse('finanzas:libro_caja'), {'periodo': '2025-02'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Formato 1.1')
        self.assertContains(r, '191-1234567-0-12')
        self.assertNotContains(r, 'ANUL999')
        r = self.client.get(reverse('finanzas:libro_caja'), {'periodo': '2025-02', 'cuenta': self.caja.pk})
        self.assertEqual(len(r.context['cajas']), 1)
        self.assertEqual(r.context['bancos'], [])

    def test_excel(self):
        from openpyxl import load_workbook
        r = self.client.get(reverse('finanzas:libro_caja'), {'periodo': '2025-02', 'formato': 'excel'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('Libro_caja_bancos_202502.xlsx', r['Content-Disposition'])
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ['Formato 1.1', 'Formato 1.2'])

        def valores(ws):
            return [[c.value for c in fila] for fila in ws.iter_rows()]

        def fila_de(filas, texto):
            return next(f for f in filas if texto in f)

        h11, h12 = valores(wb['Formato 1.1']), valores(wb['Formato 1.2'])
        self.assertIn('LIBRO CAJA Y BANCOS - DETALLE DE LOS MOVIMIENTOS DEL EFECTIVO', h11[0][0])
        self.assertIn('DETALLE DE LOS MOVIMIENTOS DE LA CUENTA CORRIENTE', h12[0][0])
        self.assertEqual(fila_de(h11, 'PERIODO:')[1], '02/2025')
        self.assertEqual(fila_de(h11, 'RUC:')[1], self.empresa.ruc)
        self.assertEqual(fila_de(h11, 'APELLIDOS Y NOMBRES, DENOMINACIÓN O RAZÓN SOCIAL:')[1],
                         self.empresa.razon_social)
        self.assertTrue(fila_de(h11, 'CUENTA CONTABLE ASOCIADA'))
        self.assertTrue(fila_de(h11, 'DESCRIPCIÓN DE LA OPERACIÓN'))
        self.assertEqual(fila_de(h12, 'CÓDIGO DE LA CUENTA CORRIENTE:')[1], '191-1234567-0-12')
        self.assertEqual(fila_de(h12, 'ENTIDAD FINANCIERA:')[1], '02 BCP')
        self.assertTrue(fila_de(h12, 'MEDIO DE PAGO (TABLA 1)'))
        # caja: saldo inicial 1300 (deudor) + egreso 100
        t11 = fila_de(h11, 'TOTALES')
        self.assertEqual(t11[-2:], [1300, 100])
        self.assertEqual(fila_de(h11, 'SALDO FINAL')[-2:], [1200, 0])
        t12 = fila_de(h12, 'TOTALES')
        self.assertEqual(t12[-2:], [7250, 50])
        self.assertEqual(fila_de(h12, 'SALDO INICIAL')[-2:], [7000, 0])
        self.assertFalse(any('ANUL999' in f for f in h12))

    # ------------------------------------------------------------ TXT del PLE
    def txt(self, periodo, formato):
        r = self.client.get(reverse('finanzas:libro_caja'), {'periodo': periodo, 'formato': formato})
        self.assertEqual(r.status_code, 200)
        nombre = r['Content-Disposition'].split('filename="')[1].rstrip('"')
        return nombre, [l for l in r.content.decode('utf-8').split('\r\n') if l]

    def test_txt_ple_nombre_y_campos(self):
        ruc = self.empresa.ruc
        nombre, lineas = self.txt('2025-02', 'ple11')
        self.assertEqual(nombre, f'LE{ruc}20250200010100001111.txt')
        self.assertEqual(len(lineas), 1)
        campos = lineas[0].split('|')
        self.assertEqual(campos[-1], '')  # termina en «|»
        self.assertEqual(len(campos) - 1, libro_caja.CAMPOS_010100)
        self.assertEqual(campos[0], '20250200')
        self.assertEqual(campos[3], self.caja.cuenta_contable.codigo)
        self.assertEqual((campos[15], campos[16], campos[18]), ('0.00', '100.00', '1'))
        # el CUO es el asiento de tesorería del movimiento (centralizado al abrir la pantalla)
        asiento = Asiento.objects.filter(movimiento=self.egreso_caja, origen='TESORERIA').first()
        self.assertIsNotNone(asiento)
        self.assertEqual(campos[1], asiento.numero.replace('-', ''))
        self.assertRegex(campos[2], r'^M\d+$')

        nombre, lineas = self.txt('2025-02', 'ple12')
        self.assertEqual(nombre, f'LE{ruc}20250200010200001111.txt')
        self.assertEqual(len(lineas), 2)  # sin el anulado
        for linea in lineas:
            self.assertEqual(len(linea.split('|')) - 1, libro_caja.CAMPOS_010200)
            self.assertNotIn('ANUL999', linea)
        deposito = lineas[0].split('|')
        self.assertEqual(deposito[3:8], ['02', '191-1234567-0-12', '08/02/2025', '001', 'Depósito de cliente'])
        self.assertEqual(deposito[8:14], ['6', '20100070970', 'CLIENTE CAJA SAC', 'DEP77', '250.00', '0.00'])

    def test_txt_enero_con_apertura_y_periodo_sin_movimientos(self):
        _, lineas = self.txt('2025-01', 'ple12')
        self.assertEqual(len(lineas), 3)  # saldo inicial (A) + 2 movimientos
        apertura = lineas[0].split('|')
        self.assertEqual((apertura[1], apertura[2], apertura[12]), ('APERTURA2025', 'A1', '5000.00'))
        nombre, lineas = self.txt('2025-05', 'ple11')
        self.assertEqual(nombre, f'LE{self.empresa.ruc}20250500010100001011.txt')  # sin información
        self.assertEqual(lineas, [])
