"""Genera la «Auditoría del proceso de venta» (Excel) con la versión del sistema: casos de prueba para marcar el
resultado (Pasa / Falla / Observado) y una hoja de resumen por etapa.

Los casos están en casos.py.

Uso: python scripts/auditoria_ventas/generar.py [carpeta_salida]   (por defecto Diseño del ERP/Auditoria)
"""
import os
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.formatting.rule import DataBarRule, FormulaRule  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from casos import CASOS, ETAPAS, REVISAR  # noqa: E402

VERSION = settings.ERP_VERSION
FECHA = date.today().strftime('%d/%m/%Y')
SALIDA = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / 'Diseño del ERP' / 'Auditoria'
XLSX = SALIDA / 'Auditoria del proceso de venta - Ceiba ERP.xlsx'
F, VERDE = 'Arial', '1E6B3A'
ESTADOS = ['Pendiente', 'Pasa', 'Falla', 'Observado', 'No aplica']
COLUMNAS = [('ID', 7), ('Etapa', 18), ('Caso', 28), ('Pasos', 48), ('Datos de prueba', 22),
            ('Resultado esperado', 48), ('Control / riesgo', 26), ('Prioridad', 9), ('Tipo', 13),
            ('Resultado obtenido', 34), ('Estado', 11), ('Auditor', 13), ('Fecha', 11), ('Evidencia / observación', 34)]
COL_ESTADO, COL_FECHA = 11, 13


def casos(wb):
    ws = wb.active
    ws.title = 'Casos'
    ws['A1'] = f'Auditoría del proceso de venta — Ceiba ERP v{VERSION}'
    ws['A1'].font = Font(name=F, size=16, bold=True, color=VERDE)
    ws['A2'] = (f'Generado el {FECHA}. De la cotización a la cobranza: {len(CASOS)} casos. «Punto a revisar» = '
                f'detectado al leer el código; la prueba lo confirma o lo descarta.')
    ws['A2'].font = Font(name=F, size=10, italic=True, color='595959')
    for celda, texto in (('A3', 'Empresa:'), ('F3', 'Auditor:')):
        ws[celda] = texto
        ws[celda].font = Font(name=F, bold=True)
    for celda in ('B3', 'G3'):
        ws[celda].fill = PatternFill('solid', fgColor='FFF2CC')
        ws[celda].font = Font(name=F)
    ws.merge_cells('B3:D3')
    ws['A4'] = ('Llene las columnas en amarillo: Resultado obtenido, Estado (lista), Auditor, Fecha y Evidencia. '
                'Ejemplo: Resultado «No guardó, mostró el mensaje esperado» · Estado «Pasa» · Evidencia «captura '
                'V-01.png».')
    ws['A4'].font = Font(name=F, size=9, color='595959')
    fe = 6
    borde = Border(*(Side(style='thin', color='BFBFBF'),) * 4)
    for c, (titulo, ancho) in enumerate(COLUMNAS, 1):
        celda = ws.cell(fe, c, titulo)
        celda.font = Font(name=F, bold=True, color='FFFFFF')
        celda.fill = PatternFill('solid', fgColor=VERDE)
        celda.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        celda.border = borde
        ws.column_dimensions[get_column_letter(c)].width = ancho
    for n, caso in enumerate(CASOS, fe + 1):
        valores = list(caso) + ['', 'Pendiente', '', None, '']
        for c, valor in enumerate(valores, 1):
            celda = ws.cell(n, c, valor)
            celda.font = Font(name=F, size=9, bold=(c == 3))
            celda.alignment = Alignment(vertical='top', wrap_text=True)
            celda.border = borde
            if c >= 10:
                celda.fill = PatternFill('solid', fgColor='FFFBEA')
            if c == 9 and valor == REVISAR:
                celda.fill = PatternFill('solid', fgColor='FCE4D6')
                celda.font = Font(name=F, size=9, bold=True, color='C55A11')
        ws.cell(n, COL_FECHA).number_format = 'DD/MM/YYYY'
    ult = fe + len(CASOS)
    letra_estado = get_column_letter(COL_ESTADO)
    dv = DataValidation(type='list', formula1='"' + ','.join(ESTADOS) + '"', allow_blank=True)
    dv.add(f'{letra_estado}{fe + 1}:{letra_estado}{ult}')
    ws.add_data_validation(dv)
    letra_fecha = get_column_letter(COL_FECHA)
    dvf = DataValidation(type='date', operator='greaterThan', formula1='DATE(2000,1,1)', allow_blank=True)
    dvf.add(f'{letra_fecha}{fe + 1}:{letra_fecha}{ult}')
    ws.add_data_validation(dvf)
    rango = f'A{fe + 1}:{get_column_letter(len(COLUMNAS))}{ult}'
    regla = f'${letra_estado}{fe + 1}'
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'{regla}="Pasa"'], font=Font(color='006100'),
                                                     fill=PatternFill('solid', fgColor='C6EFCE')))
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'{regla}="Falla"'], font=Font(color='9C0006'),
                                                     fill=PatternFill('solid', fgColor='FFC7CE')))
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'{regla}="Observado"'],
                                                     fill=PatternFill('solid', fgColor='FFEB9C')))
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'{regla}="No aplica"'],
                                                     font=Font(color='A6A6A6', strike=True)))
    ws.freeze_panes = ws.cell(fe + 1, 4)
    ws.auto_filter.ref = f'A{fe}:{get_column_letter(len(COLUMNAS))}{ult}'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'{fe}:{fe}'
    return fe, ult


def resumen(wb, fe, ult):
    av = wb.create_sheet('Resumen')
    av['A1'] = f'Resumen de la auditoría — v{VERSION}'
    av['A1'].font = Font(name=F, size=16, bold=True, color=VERDE)
    av['A2'] = 'Se calcula solo con el Estado de cada caso en la hoja «Casos».'
    av['A2'].font = Font(name=F, size=10, color='595959')
    cab = ['Etapa', 'Casos', 'Pasa', 'Falla', 'Observado', 'No aplica', 'Pendiente', 'Avance %']
    fila_cab = 4
    for c, titulo in enumerate(cab, 1):
        celda = av.cell(fila_cab, c, titulo)
        celda.font = Font(name=F, bold=True, color='FFFFFF')
        celda.fill = PatternFill('solid', fgColor=VERDE)
        celda.alignment = Alignment(horizontal='center')
    etapa = f"Casos!$B${fe + 1}:$B${ult}"
    estado = f"Casos!${get_column_letter(COL_ESTADO)}${fe + 1}:${get_column_letter(COL_ESTADO)}${ult}"
    filas = ETAPAS + ['Total']
    for k, nombre in enumerate(filas, fila_cab + 1):
        av.cell(k, 1, nombre)
        if nombre == 'Total':
            primera, ultima = fila_cab + 1, k - 1
            for c in range(2, 8):
                letra = get_column_letter(c)
                av.cell(k, c, f'=SUM({letra}{primera}:{letra}{ultima})')
        else:
            av.cell(k, 2, f'=COUNTIF({etapa},A{k})')
            for c, est in zip(range(3, 7), ['Pasa', 'Falla', 'Observado', 'No aplica']):
                av.cell(k, c, f'=COUNTIFS({etapa},A{k},{estado},"{est}")')
            av.cell(k, 7, f'=B{k}-SUM(C{k}:F{k})')
        av.cell(k, 8, f'=IF(B{k}-F{k}=0,0,(C{k}+D{k}+E{k})/(B{k}-F{k}))')
        av.cell(k, 8).number_format = '0.0%'
        for c in range(1, 9):
            av.cell(k, c).font = Font(name=F, bold=(nombre == 'Total'))
    total = fila_cab + len(filas)
    av.conditional_formatting.add(f'H{fila_cab + 1}:H{total - 1}', DataBarRule(
        start_type='num', start_value=0, end_type='num', end_value=1, color='63BE7B'))
    av.conditional_formatting.add(f'D{fila_cab + 1}:D{total}', FormulaRule(
        formula=[f'D{fila_cab + 1}>0'], font=Font(color='9C0006', bold=True), fill=PatternFill('solid',
                                                                                              fgColor='FFC7CE')))
    av[f'A{total + 2}'] = 'Avance % = casos ejecutados (Pasa + Falla + Observado) / casos que aplican.'
    av[f'A{total + 2}'].font = Font(name=F, size=9, italic=True, color='595959')
    av[f'A{total + 3}'] = 'Fallas abiertas:'
    av[f'A{total + 3}'].font = Font(name=F, bold=True)
    av[f'B{total + 3}'] = f'=D{total}'
    av[f'B{total + 3}'].font = Font(name=F, bold=True, color='9C0006')
    for c, ancho in zip('ABCDEFGH', (30, 8, 8, 8, 11, 10, 10, 10)):
        av.column_dimensions[c].width = ancho


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    fe, ult = casos(wb)
    resumen(wb, fe, ult)
    wb.calculation.fullCalcOnLoad = True  # el resumen se calcula al abrir (openpyxl no guarda los resultados)
    wb.save(XLSX)
    print(f'{XLSX} ({len(CASOS)} casos, v{VERSION})')


if __name__ == '__main__':
    main()
