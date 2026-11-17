"""Registro de Compras · Formato 8.1 en Excel (v1.115.3), con el diseño del formato que usa la contabilidad (modelo
«RCompras 202610 - Aresol - sunat.xlsx»): cabecera con periodo, RUC y razón social; títulos en 5 filas con celdas
combinadas; columnas A a AB más el % de IGV (AC); importes en soles (las notas de crédito en negativo); tipo de cambio 0 en soles; fila de
totales con fórmulas; Arial Narrow 8 y A4 horizontal ajustado a una página de ancho."""
import io
from decimal import Decimal

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from core.models import D0, Empresa, r2
from core.utils import fmt_fecha

MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Setiembre', 'Octubre',
         'Noviembre', 'Diciembre']
FUENTE = 'Arial Narrow'
NUMERO = '##,###,##0.00'
FINO = Side(style='thin')

# títulos (filas 5 a 9) tal como el modelo: (celda, texto) y combinaciones
TITULOS = {
    5: {'A': 'NÚMERO', 'B': 'FECHA DE', 'C': 'FECHA', 'D': 'COMPROBANTE DE PAGO', 'G': 'N° DEL', 'H': 'INFORMACIÓN DEL',
        'K': 'ADQUISICIONES GRAVADAS', 'M': 'ADQUISICIONES GRAVADAS DESTINADAS A', 'O': 'ADQUISICIONES GRAVADAS',
        'Q': 'VALOR', 'U': 'N° COMPROBANTE', 'V': 'CONSTANCIA DE DEPÓSITO',
        'Y': 'REFERENCIA DEL COMPROBANTE DE PAGO'},
    6: {'A': 'CORRELATIVO', 'B': 'EMISIÓN DEL', 'C': 'DE', 'D': 'O DOCUMENTO', 'G': 'COMPROBANTE', 'H': 'PROVEEDOR',
        'K': 'DESTINADAS A OPERACIONES', 'M': 'OPERACIONES GRAVADAS Y/O DE EXPORTACIÓN', 'O': 'DESTINADAS A OPERACIONES',
        'Q': 'DE LAS', 'S': 'OTROS', 'U': 'DE PAGO', 'V': 'DE DETRACCIÓN (3)', 'Y': 'O DOCUMENTO ORIGINAL QUE SE MODIFICA'},
    7: {'A': 'DEL REGISTRO O', 'B': 'COMPROBANTE', 'C': 'VENCIMIENTO', 'F': 'AÑO DE', 'G': 'O DOCUMENTO',
        'H': 'DOCUMENTO DE IDENTIDAD', 'J': 'APELLIDOS Y NOMBRES', 'K': 'GRAVADAS Y/O DE EXPORTACIÓN',
        'M': 'Y A OPERACIONES NO GRAVADAS', 'O': 'NO GRAVADAS', 'Q': 'ADQUISICIONES', 'S': 'TRIBUTOS Y',
        'T': 'IMPORTE', 'U': 'EMITIDO POR', 'W': 'FECHA', 'X': 'TIPO', 'AB': 'N° COMPROBANTE', 'AC': '%'},
    8: {'A': 'CÓDIGO ÚNICO', 'B': 'DE PAGO', 'C': 'O FECHA', 'D': 'TIPO', 'E': 'SERIE', 'F': 'EMISIÓN DE', 'H': 'TIPO',
        'I': 'NÚMERO', 'J': 'DENOMINACIÓN O RAZÓN SOCIAL', 'K': 'BASE', 'L': 'IGV', 'M': 'BASE', 'N': 'IGV',
        'O': 'BASE', 'P': 'IGV', 'Q': 'NO', 'R': 'ISC', 'S': 'CARGOS', 'T': 'TOTAL', 'U': 'SUJETO NO', 'V': 'NÚMERO',
        'W': 'DE', 'X': 'DE', 'Y': 'FECHA', 'Z': 'TIPO', 'AA': 'SERIE', 'AB': 'DE PAGO O', 'AC': 'DE'},
    9: {'A': 'DE LA OPERACIÓN', 'B': 'O DOCUMENTO', 'C': 'DE PAGO(1)', 'F': 'DUA O DSI', 'K': 'IMPONIBLE',
        'M': 'IMPONIBLE', 'O': 'IMPONIBLE', 'Q': 'GRAVADAS', 'U': 'DOMICILIADO(2)', 'W': 'EMISIÓN', 'X': 'CAMBIO',
        'AB': 'DOCUMENTO', 'AC': 'IGV'},
}
COMBINADAS = ['A1:D1', 'A2:D2', 'E2:J2', 'A3:D3', 'E3:J3', 'A4:D4', 'E4:O4', 'D5:F5', 'H5:J5', 'K5:L5', 'M5:N5',
              'O5:P5', 'V5:W5', 'Y5:AB5', 'D6:F6', 'H6:J6', 'K6:L6', 'M6:N6', 'O6:P6', 'V6:W6', 'Y6:AB6', 'H7:I7',
              'K7:L7', 'M7:N7', 'O7:P7']
ANCHOS = {'A': 11.7, 'B': 10.1, 'C': 9.1, 'D': 3.6, 'E': 4.3, 'F': 7.8, 'G': 10.1, 'H': 4.2, 'I': 8.1, 'J': 61.6,
          'K': 11.2, 'L': 9.5, 'M': 16.2, 'N': 9.5, 'O': 11.2, 'P': 9.5, 'Q': 10.2, 'R': 3.1, 'S': 8.0, 'T': 11.0,
          'U': 11.4, 'V': 11.8, 'W': 9.0, 'X': 5.4, 'Y': 9.0, 'Z': 3.6, 'AA': 4.3, 'AB': 11.4, 'AC': 5.5}
IMPORTES = 'KLMNOPQRST'  # columnas con formato de número y suma en la fila de totales
PRIMERA = 10


def fila(n, d):
    """Valores de las columnas A a AB de un comprobante (importes en soles; la nota de crédito en negativo)."""
    ref = d.doc_referencia
    anulado = d.estado == 'ANULADO'
    tc = d.tc_efectivo
    s = 0 if anulado else d.signo

    def soles(valor):
        return r2(s * (valor or D0) * tc)
    # DUA (50): la base del IGV de importación es el valor en aduana más los tributos (como el PLE 8.1)
    base = soles(d.base_imponible + (d.valor_aduana + d.no_gravado if d.es_dua else D0))
    igv = soles(d.igv)
    destino = {'GRAVADA': 'K', 'COMUN': 'M', 'NO_GRAVADA': 'O'}.get(getattr(d, 'destino_igv', ''), 'K')
    columnas = {
        'A': f'40.01.{n:05d}', 'B': fmt_fecha(d.fecha_emision), 'C': fmt_fecha(d.fecha_vencimiento or d.fecha_emision),
        'D': d.tipo_comprobante, 'E': d.serie, 'F': str(d.fecha_emision.year) if d.es_dua else '', 'G': d.numero,
        'H': d.tercero.tipo_doc, 'I': d.tercero.numero_doc, 'J': d.tercero.nombre,
        'K': None, 'L': None, 'M': None, 'N': None, 'O': None, 'P': None,
        'Q': soles(D0 if d.es_dua else d.no_gravado) or None, 'R': Decimal('0'), 'S': soles(d.icbper) or None,
        'T': soles(d.total), 'U': '',
        'V': d.detraccion_constancia or '', 'W': fmt_fecha(d.detraccion_fecha) if d.detraccion_fecha else '',
        'X': d.tipo_cambio if d.moneda != 'PEN' else Decimal('0'),
        'Y': fmt_fecha(ref.fecha_emision) if ref else '', 'Z': ref.tipo_comprobante if ref else '',
        'AA': ref.serie if ref else '', 'AB': ref.numero if ref else '',
        # v1.115.4: % de IGV del comprobante (IGV / base gravada; 18, 10 en restaurantes MYPE…); vacío sin gravadas
        'AC': (r2(d.igv * 100 / d.base_imponible) if d.base_imponible and d.igv else None),
    }
    siguiente = get_column_letter(_columna(destino) + 1)
    if base or igv:  # como el modelo: base e IGV solo en la columna de su destino; sin gravadas, en blanco
        columnas[destino], columnas[siguiente] = base, igv
    return columnas


def _columna(letra):
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letra)


def libro(periodo, docs, empresa=None):
    """Workbook del registro de compras del periodo con el formato 8.1."""
    empresa = empresa or Empresa.actual()
    wb = Workbook()
    ws = wb.active
    ws.title = f'Periodo {periodo[4:]}-{periodo[:4]}'
    normal, negrita = Font(name=FUENTE, size=8), Font(name=FUENTE, size=8, bold=True)
    ws['A1'], ws['A2'], ws['A3'], ws['A4'] = ('FORMATO 8.1: REGISTRO DE COMPRAS', 'PERÍODO:', 'RUC:',
                                              'APELLIDOS Y NOMBRES, DENOMINACIÓN O RAZÓN SOCIAL:')
    ws['E2'] = f'{MESES[int(periodo[4:]) - 1]}-{periodo[:4]}'
    ws['E3'], ws['E4'] = empresa.ruc, empresa.razon_social
    for celda in ('A1', 'A2', 'A3', 'A4', 'E4'):
        ws[celda].font = negrita
    for celda in ('E2', 'E3'):
        ws[celda].font = normal
    for numero_fila, textos in TITULOS.items():
        for letra, texto in textos.items():
            ws[f'{letra}{numero_fila}'] = texto
    for rango in COMBINADAS:
        ws.merge_cells(rango)
    for numero_fila in range(5, 10):  # caja de títulos: bordes a los lados, arriba en la 5 y abajo en la 9
        for c in range(1, 30):
            celda = ws.cell(numero_fila, c)
            celda.font = normal
            celda.alignment = Alignment(horizontal='center', vertical='center')
            celda.border = Border(left=FINO, right=FINO, top=FINO if numero_fila in (5, 8) else None,
                                  bottom=FINO if numero_fila == 9 else None)
    n = 0
    for n, d in enumerate(docs, 1):
        valores = fila(n, d)
        r = PRIMERA + n - 1
        for letra, valor in valores.items():
            celda = ws[f'{letra}{r}']
            celda.value = float(valor) if isinstance(valor, Decimal) else valor
            celda.font = normal
            celda.border = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
            if letra in IMPORTES:
                celda.number_format = NUMERO
            elif letra == 'X':
                celda.number_format = '##0.000'
            elif letra == 'AC':
                celda.number_format = '0.00'
    total = PRIMERA + n
    ws[f'J{total}'] = '----------------T O T A L E S--------------'
    ws[f'J{total}'].font = negrita
    for letra in IMPORTES:
        celda = ws[f'{letra}{total}']
        celda.value = f'=SUM({letra}{PRIMERA}:{letra}{total - 1})' if n else 0
        celda.font, celda.number_format = negrita, NUMERO
        celda.border = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
    for letra, ancho in ANCHOS.items():
        ws.column_dimensions[letra].width = ancho
    for r in range(1, total + 1):
        ws.row_dimensions[r].height = 11.25
    ws.page_setup.orientation, ws.page_setup.paperSize = 'landscape', 9
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.page_margins.left = ws.page_margins.right = ws.page_margins.top = ws.page_margins.bottom = 0.23
    ws.print_title_rows = '5:9'
    return wb


def respuesta(periodo, docs):
    empresa = Empresa.actual()
    salida = io.BytesIO()
    libro(periodo, docs, empresa).save(salida)
    resp = HttpResponse(salida.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="RCompras {periodo} - {empresa.ruc}.xlsx"'
    return resp
