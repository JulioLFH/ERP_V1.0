"""v1.113: carga masiva (Importar desde Excel) de compras y ventas con detracción, retención y percepción; notas de
crédito/débito amarradas al comprobante que modifican y guardadas en positivo aunque vengan en negativo (como las
muestra SUNAT), en la importación y al crear desde la propuesta SIRE."""
import io
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Tercero
from ventas.models import Venta

from . import sire
from .models import Compra

COLUMNAS = ['tipo', 'serie', 'numero', 'fecha', 'vencimiento', 'tipo_doc', 'ruc', 'nombre', 'moneda', 'tc', 'base',
            'no_gravado', 'igv', 'total', 'detraccion', 'retencion', 'percepcion', 'ref_tipo', 'ref_serie',
            'ref_numero', 'glosa']
# columnas de la propuesta RCE de SUNAT, con las del comprobante que modifica la nota
CAB_SIRE = ['RUC', 'Apellidos y Nombres o Razón social', 'Periodo', 'CAR SUNAT', 'Fecha de emisión', 'Fecha Vcto/Pago',
            'Tipo CP/Doc.', 'Serie del CDP', 'Año', 'Nro CP o Doc. Nro Inicial (Rango)', 'Nro Final (Rango)',
            'Tipo Doc Identidad', 'Nro Doc Identidad', 'Apellidos Nombres/ Razón  Social', 'BI Gravado DG',
            'IGV / IPM DG', 'Total CP', 'Moneda', 'Tipo de Cambio', 'Fecha Emisión Doc Modificado',
            'Tipo CP Modificado', 'Serie CP Modificado', 'COD. DAM O DSI', 'Nro CP Modificado', 'Tipo de Nota']


def xlsx(filas, nombre='carga.xlsx'):
    from openpyxl import Workbook
    libro = Workbook()
    for f in filas:
        libro.active.append(f)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile(nombre, salida.getvalue())


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_carga', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)

    def importar(self, app, filas):
        r = self.client.post(reverse(f'{app}:importar'), {'archivo': xlsx([COLUMNAS] + filas)})
        self.assertEqual(r.status_code, 200)
        return r.context['resultado']


class ImportarComprasTest(Base):
    def test_detraccion_retencion_y_nota_de_credito_amarrada_en_positivo(self):
        filas = [
            # la nota viene antes que su factura y en negativo, como en SUNAT: se procesa al final y se guarda en +
            ['07', 'E001', 5, '15/10/2026', '', 6, 20600000101, 'TRANSPORTES SUR SAC', 'PEN', 1, -100, 0, -18, -118,
             0, 0, 0, 1, 'E001', 120, 'Descuento'],
            ['01', 'E001', 120, '10/10/2026', '', 6, 20600000101, 'TRANSPORTES SUR SAC', 'PEN', 1, 1000, 0, 180, 1180,
             47.2, 0, 0, '', '', '', 'Flete'],
            ['01', 'F002', '00000033', '11/10/2026', '', 6, 20600000102, 'METALES SAC', 'PEN', 1, 2000, 0, 360,
             2360, 0, 70.8, 0, '', '', '', 'Agente de retención'],
        ]
        res = self.importar('compras', filas)
        self.assertEqual((res['ok'], res['errores']), (3, []))
        factura = Compra.objects.get(serie='E001', numero='120')
        self.assertEqual((factura.detraccion_monto, factura.detraccion_pct), (D('47.20'), D('4.00')))
        self.assertEqual(Compra.objects.get(serie='F002', numero='00000033').retencion_monto, D('70.80'))
        nota = Compra.objects.get(tipo_comprobante='07')
        self.assertEqual((nota.total, nota.base_imponible, nota.igv), (D('118'), D('100'), D('18')))
        self.assertEqual(nota.doc_referencia, factura)
        self.assertEqual(factura.total_documento, D('1062'))  # 1180 − 118: la nota se resta una sola vez

    def test_nota_sin_documento_o_con_uno_inexistente_no_se_importa(self):
        filas = [['07', 'E001', 6, '15/10/2026', '', 6, 20600000101, 'TRANSPORTES SUR SAC', 'PEN', 1, -100, 0, -18,
                  -118, 0, 0, 0, '', '', '', ''],
                 ['07', 'E001', 7, '15/10/2026', '', 6, 20600000101, 'TRANSPORTES SUR SAC', 'PEN', 1, -100, 0, -18,
                  -118, 0, 0, 0, '01', 'E001', 999, '']]
        res = self.importar('compras', filas)
        self.assertEqual(res['ok'], 0)
        self.assertIn('necesita el comprobante que modifica', res['errores'][0])
        self.assertIn('no está registrado', res['errores'][1])
        self.assertFalse(Compra.objects.filter(tipo_comprobante='07').exists())

    def test_validaciones_de_montos(self):
        filas = [['01', 'E001', 1, '10/10/2026', '', 6, 20600000101, 'X SAC', 'PEN', 1, 100, 0, 18, 118, 200, 0, 0,
                  '', '', '', ''],
                 ['03', 'B001', 1, '10/10/2026', '', 1, 40000001, 'JUAN', 'PEN', 1, 100, 0, 18, 118, 10, 0, 0, '',
                  '', '', '']]
        res = self.importar('compras', filas)
        self.assertEqual(res['ok'], 0)
        self.assertIn('supera el total', res['errores'][0])
        self.assertIn('solo va en facturas', res['errores'][1])

    def test_plantilla_trae_las_columnas_nuevas(self):
        from openpyxl import load_workbook
        r = self.client.get(reverse('compras:importar') + '?plantilla=1')
        hoja = load_workbook(io.BytesIO(r.content)).active
        encabezados = [c.value for c in next(hoja.iter_rows(max_row=10)) if c.value]
        if 'tipo' not in encabezados:  # la plantilla puede llevar un título antes de los encabezados
            encabezados = next([c.value for c in fila] for fila in hoja.iter_rows() if any(c.value == 'tipo' for c in fila))
        for columna in ('detraccion', 'retencion', 'percepcion', 'ref_tipo', 'ref_serie', 'ref_numero'):
            self.assertIn(columna, encabezados)


class ImportarVentasTest(Base):
    def test_percepcion_y_nota_de_credito(self):
        filas = [['01', 'F010', 50, '10/10/2026', '', 6, 20600000201, 'CLIENTE NORTE SAC', 'PEN', 1, 1000, 0, 180,
                  1180, 0, 0, 23.6, '', '', '', ''],
                 ['07', 'FC10', 3, '12/10/2026', '', 6, 20600000201, 'CLIENTE NORTE SAC', 'PEN', 1, -500, 0, -90,
                  -590, 0, 0, 0, '01', 'F010', 50, '']]
        res = self.importar('ventas', filas)
        self.assertEqual((res['ok'], res['errores']), (2, []))
        factura = Venta.objects.get(serie='F010')
        self.assertEqual(factura.percepcion_monto, D('23.60'))
        nota = Venta.objects.get(serie='FC10')
        self.assertEqual((nota.total, nota.doc_referencia), (D('590'), factura))
        self.assertEqual(factura.total_documento, D('590'))
        # la nota no puede superar lo que queda del comprobante
        res = self.importar('ventas', [['07', 'FC10', 4, '12/10/2026', '', 6, 20600000201, 'CLIENTE NORTE SAC',
                                        'PEN', 1, -1000, 0, -180, -1180, 0, 0, 0, '01', 'F010', 50, '']])
        self.assertIn('supera el saldo', res['errores'][0])


class PropuestaSireNotasTest(Base):
    RUC = '20519000111'

    def propuesta(self, filas):
        return sire.leer(xlsx([CAB_SIRE] + filas, 'propuesta.xlsx'))

    def fila(self, tipo, numero, base, igv, total, ref=('', '', ''), tipo_nota=''):
        return ['20100000001', 'MI EMPRESA', '202610', 'X', '06/10/2026', '', tipo, 'E001', '', numero, '', '6',
                self.RUC, 'ARQUITECTOS SAC', base, igv, total, 'PEN', 1, '30/09/2026' if ref[0] else '', ref[0],
                ref[1], '', ref[2], tipo_nota]

    def test_nota_en_negativo_se_lee_en_positivo_con_su_documento(self):
        regs = self.propuesta([self.fila('07', 41, -27611.14, -4970.01, -32581.15, ('01', 'E001', '194'), '01')])
        r = regs[0]
        self.assertEqual((r['base'], r['igv'], r['total']), (D('27611.14'), D('4970.01'), D('32581.15')))
        self.assertEqual((r['ref_tipo'], r['ref_serie'], r['ref_numero'], r['tipo_nota']), ('01', 'E001', '194', '01'))

    def test_nota_registrada_en_positivo_coincide_en_el_cruce(self):
        prov = Tercero.objects.create(numero_doc=self.RUC, nombre='ARQUITECTOS SAC', tipo='PROVEEDOR')
        Compra.objects.create(tipo_comprobante='07', serie='E001', numero='41', tercero=prov, periodo='202610',
                              fecha_emision='2026-10-06', base_imponible=D('27611.14'), igv=D('4970.01'),
                              total=D('32581.15'))
        regs = self.propuesta([self.fila('07', 41, -27611.14, -4970.01, -32581.15, ('01', 'E001', '194'))])
        res = sire.cruzar('RCE', regs, '202610')
        self.assertEqual((len(res['coinciden']), len(res['diferencias'])), (1, 0))  # antes: diferencia de 2 veces

    def test_crear_desde_la_propuesta_amarra_la_nota(self):
        regs = self.propuesta([self.fila('07', 41, -1000, -180, -1180, ('01', 'E001', '194'), '01'),
                               self.fila('07', 42, -50, -9, -59, ('01', 'E001', '999')),
                               self.fila('01', 194, 5000, 900, 5900)])
        claves = [f'{r["tipo"]}|{r["serie"]}|{r["numero"]}|{r["ruc"]}' for r in regs]
        creados, errores = sire.crear_faltantes('RCE', regs, '202610', claves)
        self.assertEqual(creados, 2)  # la factura y su nota; la 42 modifica un comprobante que no existe
        self.assertEqual(len(errores), 1)
        self.assertIn('E001-42: no se creó', errores[0])
        factura = Compra.objects.get(tipo_comprobante='01', serie='E001', numero='194')
        nota = Compra.objects.get(tipo_comprobante='07', numero='41')
        self.assertEqual((nota.total, nota.doc_referencia), (D('1180'), factura))
        self.assertEqual(factura.total_documento, D('4720'))
