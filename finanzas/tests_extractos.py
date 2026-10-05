"""Conciliación bancaria automática con estados de cuenta de BCP, BBVA e Interbank (v1.17, punto 28)."""
import io
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from . import extractos
from .models import Cuenta, Extracto, Movimiento

D = Decimal


def xlsx(filas, nombre='extracto.xlsx'):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(nombre, buf.getvalue())


def bcp(lineas):
    return xlsx([['Movimientos de la cuenta'], ['Cuenta', '193-1234567-0-12'], [],
                 ['Fecha', 'Fecha valuta', 'Descripción operación', 'Monto', 'Saldo', 'Sucursal - agencia',
                  'Operación - Número', 'Operación - Hora', 'Usuario']] + lineas, 'bcp.xlsx')


class ExtractoTest(TestCase):
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
        self.cuenta = Cuenta.objects.create(nombre='BCP Conciliación', banco='BCP', tipo='BANCO',
                                            saldo_inicial=D('10000'))
        mov = lambda **k: Movimiento.objects.create(cuenta=self.cuenta, medio_pago='TRANSFERENCIA', **k)
        self.cobro = mov(fecha=date(2026, 9, 2), tipo='INGRESO', monto=D('1180'), numero_operacion='00456789',
                         glosa='Cobro F001-25')
        self.pago = mov(fecha=date(2026, 9, 3), tipo='EGRESO', monto=D('500'), glosa='Pago proveedor')
        self.cheque = mov(fecha=date(2026, 9, 4), tipo='EGRESO', monto=D('750'), numero_operacion='CH 88',
                          glosa='Cheque no cobrado')

    def test_bcp_concilia_por_operacion_y_fecha(self):
        archivo = bcp([
            ['02/09/2026', '02/09/2026', 'ABONO TRANSF CLIENTE', 1180, 11180, '193', '456789', '10:01', 'X'],
            [datetime(2026, 9, 5), datetime(2026, 9, 5), 'PAGO PROVEEDOR', -500, 10680, '193', '111', '10:02', 'X'],
            ['06/09/2026', '06/09/2026', 'COMISION MANTENIMIENTO', -12.5, 10667.5, '193', '', '', ''],
            ['07/09/2026', '07/09/2026', 'ABONO NO IDENTIFICADO', 300, 10967.5, '193', '999', '', ''],
        ])
        ext, repetidas, n = extractos.cargar(self.cuenta, archivo, self.user)
        self.assertEqual((ext.formato, ext.lineas.count(), repetidas, n), ('BCP', 4, 0, 2))
        self.assertEqual((ext.desde, ext.hasta, ext.saldo_final), (date(2026, 9, 2), date(2026, 9, 7), D('10967.50')))
        cobro = ext.lineas.get(monto=D('1180'))
        self.assertEqual((cobro.movimiento, cobro.regla), (self.cobro, 'Mismo importe y N° de operación'))
        self.assertEqual(ext.lineas.get(monto=D('-500')).movimiento, self.pago)  # fecha cercana (2 días)
        self.pago.refresh_from_db()
        self.assertTrue(self.pago.conciliado)
        self.assertFalse(Movimiento.objects.get(pk=self.cheque.pk).conciliado)  # en tránsito
        # comisiones: se registran como gasto bancario y quedan conciliadas
        r = self.client.post(reverse('finanzas:extracto_accion', args=[ext.pk]), {'accion': 'gastos'})
        self.assertRedirects(r, reverse('finanzas:extracto', args=[ext.pk]))
        comision = ext.lineas.get(monto=D('-12.50'))
        self.assertEqual((comision.estado, comision.movimiento.concepto), ('CREADA', 'GASTO_BANCARIO'))
        # el abono no identificado se ignora; deshacer vuelve a pendiente
        linea = ext.lineas.get(monto=D('300'))
        self.client.post(reverse('finanzas:extracto_accion', args=[ext.pk]), {'accion': 'ignorar', 'linea': linea.pk})
        linea.refresh_from_db()
        self.assertEqual(linea.estado, 'IGNORADA')
        self.client.post(reverse('finanzas:extracto_accion', args=[ext.pk]), {'accion': 'quitar', 'linea': cobro.pk})
        self.assertFalse(Movimiento.objects.get(pk=self.cobro.pk).conciliado)
        r = self.client.get(reverse('finanzas:extracto', args=[ext.pk]))
        self.assertContains(r, 'Cheque no cobrado')  # en tránsito
        # recargar el mismo archivo no duplica líneas
        _, repetidas, _ = extractos.cargar(self.cuenta, bcp([
            ['02/09/2026', '02/09/2026', 'ABONO TRANSF CLIENTE', 1180, 11180, '193', '456789', '10:01', 'X']]))
        self.assertEqual(repetidas, 1)

    def test_interbank_csv_cargo_abono_y_enlace_manual(self):
        csv = ('Cuenta Corriente Soles;200-300\n\n'
               'Fecha de operación;Fecha de proceso;Nro. de operación;Movimiento;Descripción;Canal;Cargo;Abono;'
               'Saldo contable\n'
               '04/09/2026;04/09/2026;0088;Cheque;CHEQUE COBRADO;Ventanilla;740,00;;9.260,00\n'
               '05/09/2026;05/09/2026;;ITF;IMPUESTO ITF;;0,04;;9.259,96\n')
        archivo = SimpleUploadedFile('interbank.csv', csv.encode('latin-1'))
        r = self.client.post(reverse('finanzas:extractos'), {'cuenta': self.cuenta.pk, 'archivo': archivo})
        ext = Extracto.objects.get(cuenta=self.cuenta)
        self.assertRedirects(r, reverse('finanzas:extracto', args=[ext.pk]))
        self.assertEqual(ext.formato, 'INTERBANK')
        cheque = ext.lineas.get(operacion='0088')
        self.assertEqual((cheque.monto, cheque.estado), (D('-740.00'), 'PENDIENTE'))  # importe distinto: manual
        self.assertEqual(ext.lineas.get(descripcion='IMPUESTO ITF').monto, D('-0.04'))
        self.client.post(reverse('finanzas:extracto_accion', args=[ext.pk]),
                         {'accion': 'enlazar', 'linea': cheque.pk, 'movimiento': self.cheque.pk})
        cheque.refresh_from_db()
        self.assertEqual((cheque.movimiento, cheque.regla), (self.cheque, 'Manual (diferencia -10.00)'))
        # no se enlaza un ingreso con un egreso
        itf = ext.lineas.get(descripcion='IMPUESTO ITF')
        self.client.post(reverse('finanzas:extracto_accion', args=[ext.pk]),
                         {'accion': 'enlazar', 'linea': itf.pk, 'movimiento': self.cobro.pk})
        itf.refresh_from_db()
        self.assertEqual(itf.estado, 'PENDIENTE')

    def test_bbva_y_archivo_invalido(self):
        archivo = xlsx([['BBVA - Consulta de movimientos'], [],
                        ['F. Operación', 'F. Valor', 'Código', 'Nº. Doc.', 'Concepto', 'Importe', 'Oficina'],
                        ['02/09/2026', '02/09/2026', '123', '456789', 'ABONO', '1,180.00', '0100']], 'bbva.xlsx')
        ext, _, n = extractos.cargar(self.cuenta, archivo)
        self.assertEqual((ext.formato, n, ext.lineas.get().movimiento), ('BBVA', 1, self.cobro))
        with self.assertRaises(extractos.ErrorExtracto):
            extractos.cargar(self.cuenta, xlsx([['nombre', 'edad'], ['Ana', 30]]))
        self.assertContains(self.client.get(reverse('finanzas:extractos')), 'BBVA')
