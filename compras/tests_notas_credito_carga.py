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


class PropuestaSireMontosTest(Base):
    """v1.114: Detracción, Retención y Percepción como columnas propias del Excel de la propuesta (compras y ventas)."""
    RUC = '20519000222'

    def fila(self, tipo, numero, total, detr='', ret='', perc=''):
        return ['20100000001', 'MI EMPRESA', '202610', 'X', '06/10/2026', '', tipo, 'E001', '', numero, '', '6',
                self.RUC, 'PROVEEDOR SAC', round(total / 1.18, 2), round(total - total / 1.18, 2), total, 'PEN', 1,
                '', '', '', '', '', '', detr, ret, perc]

    def test_se_leen_validan_y_guardan_al_crear(self):
        cab = CAB_SIRE + ['Detracción', 'Retención', 'Percepción']
        regs = sire.leer(xlsx([cab, self.fila('01', 1, 1180, 141.6), self.fila('01', 2, 2360, '', 70.8),
                               self.fila('03', 3, 118, 10), self.fila('01', 4, 118, 100, 50)], 'p.xlsx'))
        self.assertEqual((regs[0]['detraccion'], regs[1]['retencion']), (D('141.6'), D('70.8')))
        errores = {r['numero']: r['montos_error'] for r in sire.montos(regs)}
        self.assertEqual((errores['1'], errores['2']), ('', ''))
        self.assertIn('solo va en facturas', errores['3'])
        self.assertIn('supera el total', errores['4'])
        claves = [f'{r["tipo"]}|{r["serie"]}|{r["numero"]}|{r["ruc"]}' for r in regs]
        creados, errores = sire.crear_faltantes('RCE', regs, '202610', claves)
        self.assertEqual((creados, len(errores)), (2, 2))
        c1, c2 = Compra.objects.get(numero='1', tercero__numero_doc=self.RUC), Compra.objects.get(
            numero='2', tercero__numero_doc=self.RUC)
        self.assertEqual((c1.detraccion_monto, c1.detraccion_pct), (D('141.60'), D('12.00')))
        self.assertEqual((c2.retencion_monto, c2.retencion_pct), (D('70.80'), D('3.00')))
        regs_venta = sire.leer(xlsx([cab, self.fila('01', 9, 1180, '', '', 23.6)], 'v.xlsx'))
        creados, _ = sire.crear_faltantes('RVIE', regs_venta, '202610', [f'01|E001|9|{self.RUC}'])
        self.assertEqual(Venta.objects.get(serie='E001', numero='9').percepcion_monto, D('23.60'))

    def test_el_excel_del_cruce_trae_las_tres_columnas(self):
        regs = sire.leer(xlsx([CAB_SIRE, self.fila('01', 1, 1180)], 'p.xlsx'))
        for propias in (True, False):  # compras y ventas
            enc, filas = sire.filas_cruce_completo([], regs, sire.cruzar('RCE', regs, '202610'), propias=propias)
            self.assertEqual(enc[-5:-2], ['Detracción', 'Retención', 'Percepción'])
            self.assertEqual(enc[-1], 'Fecha de constancia')
            self.assertEqual(len(filas[0]), len(enc))
        rojas = sire.celdas_en_rojo(enc, filas)  # las de montos y constancia vacías no se marcan en rojo
        self.assertFalse([j for _, j in rojas if j >= len(enc) - 5])

    def test_constancia_de_detraccion(self):
        """v1.114.1: N° de operación y fecha del depósito de la detracción, en la propuesta y en la carga masiva."""
        from datetime import date
        cab = CAB_SIRE + ['Detracción', 'Retención', 'Percepción', 'Constancia de detracción (N° de operación)',
                          'Fecha de constancia']
        regs = sire.leer(xlsx([cab, self.fila('01', 1, 1180, 141.6) + [987654, '20/10/2026'],
                               self.fila('01', 2, 1180, 141.6) + ['555', ''],
                               self.fila('01', 3, 1180) + ['777', '20/10/2026']], 'p.xlsx'))
        self.assertEqual((regs[0]['det_constancia'], regs[0]['det_fecha']), ('987654', date(2026, 10, 20)))
        errores = {r['numero']: r['montos_error'] for r in sire.montos(regs)}
        self.assertEqual(errores['1'], '')
        self.assertIn('N° de operación y su fecha', errores['2'])
        self.assertIn('no el monto de la detracción', errores['3'])
        datos = sire.de_json(sire.a_json(regs))  # se guarda en la propuesta y se vuelve a leer igual
        self.assertEqual(datos[0]['det_fecha'], date(2026, 10, 20))
        creados, _ = sire.crear_faltantes('RCE', regs, '202610', [f'01|E001|1|{self.RUC}'])
        c = Compra.objects.get(numero='1', tercero__numero_doc=self.RUC)
        self.assertEqual((c.detraccion_constancia, c.detraccion_fecha), ('987654', date(2026, 10, 20)))
        # Importar desde Excel
        cols = COLUMNAS[:15] + ['detraccion_constancia', 'detraccion_fecha'] + COLUMNAS[15:]
        fila = ['01', 'E009', 1, '10/10/2026', '', 6, 20600000101, 'X SAC', 'PEN', 1, 1000, 0, 180, 1180, 141.6,
                112233, '21/10/2026', 0, 0, '', '', '', '']
        r = self.client.post(reverse('compras:importar'), {'archivo': xlsx([cols, fila])})
        self.assertEqual(r.context['resultado']['errores'], [])
        c = Compra.objects.get(serie='E009')
        self.assertEqual((c.detraccion_constancia, c.detraccion_fecha), ('112233', date(2026, 10, 21)))


class PeriodoDelArchivoTest(Base):
    """v1.115: la propuesta subida debe ser del periodo que se carga; si no: «Se están cargando datos de otro periodo»."""
    RUC = '20519000333'

    def fila(self, periodo, numero):
        return ['20100000001', 'MI EMPRESA', periodo, 'X', '06/10/2026', '', '01', 'E001', '', numero, '', '6',
                self.RUC, 'PROVEEDOR SAC', 100, 18, 118, 'PEN', 1, '', '', '', '', '', '']

    def test_bloquea_otro_periodo_al_subir_aceptar_y_crear(self):
        from core.models import PropuestaSIRE
        regs = sire.leer(xlsx([CAB_SIRE, self.fila('202609', 1), self.fila(202610, 2)], 'p.xlsx'))
        self.assertEqual([r['periodo'] for r in regs], ['202609', '202610'])
        aviso = sire.otro_periodo(regs, '202610')
        self.assertIn('Se están cargando datos de otro periodo', aviso)
        self.assertIn('09/2026', aviso)
        self.assertEqual(sire.otro_periodo(regs[1:], '202610'), '')
        r = self.client.post(reverse('sire_propuestas'), {'libro': 'RCE', 'periodo': '2026-10',
                                                     'archivo': xlsx([CAB_SIRE, self.fila('202609', 1)], 'p.xlsx')},
                             follow=True)
        self.assertContains(r, 'Se están cargando datos de otro periodo')
        self.assertFalse(PropuestaSIRE.objects.exists())
        # una propuesta ya guardada con datos de otro periodo: no deja crear comprobantes
        p = PropuestaSIRE.objects.create(libro='RCE', periodo='202610', origen='ARCHIVO', estado='LISTA',
                                         registros=sire.a_json(regs))
        r = self.client.post(reverse('sire_propuesta', args=[p.pk]),
                             {'accion': 'crear', 'falta': [f'01|E001|1|{self.RUC}']}, follow=True)
        self.assertContains(r, 'Se están cargando datos de otro periodo')
        self.assertFalse(Compra.objects.filter(tercero__numero_doc=self.RUC).exists())


class PropuestaEnSolesTest(Base):
    """v1.115.2: la propuesta del RCE trae los importes en soles aunque el comprobante sea en dólares."""
    RUC = '20519000444'

    def test_dolares_se_guardan_en_su_moneda(self):
        cab = CAB_SIRE
        fila = ['20100000001', 'MI EMPRESA', '202610', 'X', '30/09/2026', '', '01', 'E001', '', 193, '', '6', self.RUC,
                'ARQUITECTOS SAC', 155767.50, 28038.15, 183805.65, 'USD', 3.450, '', '', '', '', '', '']
        regs = sire.leer(xlsx([cab, fila], 'p.xlsx'))
        creados, errores = sire.crear_faltantes('RCE', regs, '202610', [f'01|E001|193|{self.RUC}'])
        self.assertEqual((creados, errores), (1, []))
        c = Compra.objects.get(tercero__numero_doc=self.RUC)
        self.assertEqual((c.moneda, c.total, c.base_imponible, c.igv), ('USD', D('53277.00'), D('45150.00'), D('8127.00')))
        self.assertEqual(c.total_pen, D('183805.65'))  # en soles, igual que SUNAT
        r = sire.cruzar('RCE', regs, '202610')
        self.assertEqual((len(r['coinciden']), len(r['diferencias'])), (1, 0))
        from core import sire_envio
        fila_txt = sire_envio.generar('080400', '202610')['txt'].splitlines()[0].split('|')
        self.assertEqual(fila_txt[24], '183805.65')  # el archivo para SUNAT, también en soles


class RegistroComprasFormato81Test(Base):
    """v1.115.3: Excel del Registro de Compras con el formato 8.1 de la contabilidad (modelo de Aresol)."""

    def test_formato(self):
        from datetime import date

        from openpyxl import load_workbook
        prov = Tercero.objects.create(numero_doc='20519000555', nombre='PROVEEDOR USD SAC', tipo='PROVEEDOR')
        f = Compra.objects.create(tercero=prov, tipo_comprobante='01', serie='E001', numero='10', periodo='202610',
                                  fecha_emision=date(2026, 10, 5), moneda='USD', tipo_cambio=D('3.450'),
                                  base_imponible=D('45150.00'), igv=D('8127.00'), total=D('53277.00'),
                                  detraccion_monto=D('6393.24'), detraccion_constancia='319162082',
                                  detraccion_fecha=date(2026, 10, 27))
        Compra.objects.create(tercero=prov, tipo_comprobante='07', serie='E001', numero='11', periodo='202610',
                              fecha_emision=date(2026, 10, 6), base_imponible=D('100'), igv=D('18'), total=D('118'),
                              doc_referencia=f)
        r = self.client.get(reverse('compras:registro') + '?periodo=202610&formato=excel')
        ws = load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(ws.title, 'Periodo 10-2026')
        self.assertEqual((ws['A1'].value, ws['E2'].value), ('FORMATO 8.1: REGISTRO DE COMPRAS', 'Octubre-2026'))
        filas = {ws[f'G{i}'].value: i for i in range(10, ws.max_row + 1) if ws[f'G{i}'].value}
        i = filas['10']
        self.assertEqual((ws[f'K{i}'].value, ws[f'L{i}'].value, ws[f'T{i}'].value), (155767.5, 28038.15, 183805.65))
        self.assertEqual((ws[f'V{i}'].value, ws[f'W{i}'].value, ws[f'X{i}'].value), ('319162082', '27/10/2026', 3.45))
        self.assertEqual((ws['AC9'].value, ws[f'AC{i}'].value), ('IGV', 18.0))  # v1.115.4: % de IGV
        j = filas['11']  # nota de crédito: en negativo y con el comprobante que modifica
        self.assertEqual((ws[f'T{j}'].value, ws[f'Z{j}'].value, ws[f'AA{j}'].value, ws[f'AB{j}'].value),
                         (-118.0, '01', 'E001', '10'))
        self.assertEqual(ws[f'X{j}'].value, 0)
        total = ws.max_row
        self.assertIn('T O T A L E S', ws[f'J{total}'].value)
        self.assertTrue(ws[f'T{total}'].value.startswith('=SUM(T10:'))
        self.assertEqual(ws['A10'].font.name, 'Arial Narrow')


class PropuestaSinDatosTest(Base):
    """v1.115.3: al importar una propuesta sin comprobantes sale «No se encontraron datos»."""

    def test_archivo_sin_comprobantes(self):
        from core.models import PropuestaSIRE
        r = self.client.post(reverse('sire_propuestas'), {'libro': 'RCE', 'periodo': '2026-10',
                                                          'archivo': xlsx([CAB_SIRE], 'vacia.xlsx')}, follow=True)
        self.assertContains(r, 'No se encontraron datos')
        self.assertFalse(PropuestaSIRE.objects.exists())

    def test_propuesta_de_sunat_sin_comprobantes(self):
        from unittest.mock import patch

        from core.models import PropuestaSIRE
        from core.views_sire import _actualizar
        p = PropuestaSIRE.objects.create(libro='RCE', periodo='202610', ticket='123')
        terminado = {'terminado': True, 'error': False, 'descripcion': 'Terminado', 'registro': {'archivoReporte': []}}
        with patch('core.sire_api.estado_ticket', return_value=terminado):
            _actualizar(p)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'LISTA')
        self.assertIn('No se encontraron datos', p.mensaje)
        self.assertContains(self.client.get(reverse('sire_propuesta', args=[p.pk])), 'No se encontraron datos')


class OtrosCargosTest(Base):
    """v1.115.5: «Otros tributos y cargos» de la propuesta SIRE (p. ej. recargo al consumo) se guardan y el registro
    suma el total (antes la fila no cuadraba)."""
    RUC = '20609188732'

    def test_recargo_al_consumo(self):
        cab = CAB_SIRE[:16] + ['Otros Trib/ Cargos'] + CAB_SIRE[16:]
        fila = ['20100000001', 'MI EMPRESA', '202608', 'X', '13/08/2026', '', '01', 'F002', '', 1545, '', '6', self.RUC,
                'RESTAURANTE SAC', 299.59, 31.45, 29.96, 361, 'PEN', 1, '', '', '', '', '', '']
        regs = sire.leer(xlsx([cab, fila], 'p.xlsx'))
        self.assertEqual(regs[0]['otros'], D('29.96'))
        creados, _ = sire.crear_faltantes('RCE', regs, '202608', [f'01|F002|1545|{self.RUC}'])
        c = Compra.objects.get(tercero__numero_doc=self.RUC)
        self.assertEqual((c.base_imponible, c.igv, c.otros_cargos, c.total), (D('299.59'), D('31.45'), D('29.96'), D('361')))
        self.assertEqual((c.base_pen, c.otros_pen), (D('299.59'), D('29.96')))  # antes la base en soles salía 329.55
        r = self.client.get(reverse('compras:registro') + '?periodo=202608')
        self.assertEqual(r.context['tot']['otros'], D('29.96'))
        t = r.context['tot']
        self.assertEqual(t['base'] + t['nograv'] + t['igv'] + t['icbper'] + t['otros'], t['total'])
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(self.client.get(reverse('compras:registro') + '?periodo=202608&formato=excel').content)).active
        self.assertEqual((ws['K10'].value, ws['L10'].value, ws['S10'].value, ws['T10'].value), (299.59, 31.45, 29.96, 361.0))
