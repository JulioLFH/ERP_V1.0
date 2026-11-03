"""Genera la «Guía de implementación y migración» (PDF) y su «Checklist de implementación» (Excel) con la versión del
sistema, para que ambos documentos coincidan siempre.

Los pasos están en contenido.py; las columnas de las plantillas (Anexo A) salen de core/carga_masiva.DEFINICIONES.
El PDF se imprime con Chrome o Edge sin ventana (como los manuales).

Uso: python scripts/guia_implementacion/generar.py [carpeta_salida]   (por defecto Diseño del ERP/Implementacion)
"""
import html
import os
import subprocess
import sys
import tempfile
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
from openpyxl.comments import Comment  # noqa: E402
from openpyxl.formatting.rule import DataBarRule, FormulaRule  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from contenido import (ANEXO, BASICA, COMPLETA, CRONOGRAMA, ERRORES, INTERMEDIA, NIVELES, PASOS,  # noqa: E402
                       PREVIO, PRINCIPIOS, USO_CARGA)
from core.carga_masiva import DEFINICIONES  # noqa: E402

NAVEGADORES = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
               r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
               r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
               '/usr/bin/google-chrome', '/usr/bin/chromium']
CHROME = next((n for n in NAVEGADORES if Path(n).exists()), None)
VERSION = settings.ERP_VERSION
FECHA = date.today().strftime('%d/%m/%Y')
SALIDA = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / 'Diseño del ERP' / 'Implementacion'
PDF = SALIDA / 'Guia de implementacion y migracion - Ceiba ERP.pdf'
XLSX = SALIDA / 'Checklist de implementacion - Ceiba ERP.xlsx'
ISOTIPO = (RAIZ / 'static' / 'img' / 'ceiba-isotipo.svg').read_text(encoding='utf-8')
ISOTIPO = ISOTIPO[ISOTIPO.index('<svg'):]
ORDEN = [PREVIO, BASICA, INTERMEDIA, COMPLETA]
SECCION = {PREVIO: '2. Paso previo (todos los niveles): configuración inicial', BASICA: '3. Migración BÁSICA',
           INTERMEDIA: '4. Migración INTERMEDIA', COMPLETA: '5. Migración COMPLETA'}


def e(texto):
    return html.escape(str(texto), quote=False)


# ---------------------------------------------------------------- PDF
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap');
:root { --selva:#0F3D2E; --hoja:#1F7A52; --sol:#E3A72F; --bruma:#F4F6F2; --tinta:#1d2b25; --gris:#5f6f67;
        --linea:#d9e1dc; }
@page { size: A4; margin: 16mm 15mm 18mm 15mm;
  @bottom-left { content: "Ceiba ERP v__V__ · Guía de implementación y migración de datos"; font: 8pt 'Sora', 'Segoe UI', sans-serif; color: #5f6f67; }
  @bottom-right { content: "Página " counter(page) " de " counter(pages); font: 8pt 'Sora', 'Segoe UI', sans-serif; color: #5f6f67; } }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; font-family: 'Sora', 'Segoe UI', Arial, sans-serif; font-size: 9.4pt; line-height: 1.45; color: var(--tinta); }
.portada { background: var(--selva); color: #fff; border-radius: 10px; padding: 18px 22px; display: flex; gap: 18px; align-items: center; }
.portada svg { width: 74px; height: 64px; background: #fff; border-radius: 8px; padding: 6px; flex: none; }
.portada .serie { font-size: 8.5pt; letter-spacing: .08em; text-transform: uppercase; color: #c6d6cd; }
.portada h1 { margin: 2px 0 4px; font-size: 22pt; line-height: 1.15; }
.portada .desc { color: #e3ece7; font-size: 10pt; }
.portada .meta { margin-top: 6px; font-size: 8.5pt; color: var(--sol); }
h2 { color: var(--selva); font-size: 13.5pt; margin: 18px 0 8px; padding-bottom: 4px; border-bottom: 2px solid var(--hoja); break-after: avoid; }
h3 { font-size: 10.5pt; margin: 12px 0 4px; color: var(--hoja); break-after: avoid; }
p { margin: 4px 0 8px; }
ol, ul { margin: 4px 0 8px; padding-left: 20px; } li { margin: 2px 0; }
table { width: 100%; border-collapse: collapse; margin: 4px 0 10px; font-size: 8.8pt; }
thead { display: table-header-group; }
th { text-align: left; background: var(--bruma); color: var(--selva); font-weight: 600; padding: 5px 7px; border-bottom: 1px solid var(--linea); }
td { padding: 4px 7px; border-bottom: 1px solid var(--linea); vertical-align: top; }
tr { break-inside: avoid; }
.caja { width: 16px; } .caja span { display: inline-block; width: 11px; height: 11px; border: 1.4px solid var(--hoja); border-radius: 2px; margin-top: 2px; }
.cod { font-weight: 700; color: var(--hoja); white-space: nowrap; }
.etapa { display: inline-block; font-size: 7.3pt; padding: 0 6px; border-radius: 9px; background: #e3f1e9; color: var(--hoja); font-weight: 600; }
.e-Verificación { background: #fdf0d8; color: #8a5f0c; } .e-Historial { background: #e7ecf8; color: #34509a; }
.e-Arranque, .e-Cierre { background: #fbe4e1; color: #a23a2c; }
.donde { color: var(--gris); font-size: 8.2pt; }
.objetivo { background: var(--bruma); border-left: 4px solid var(--hoja); padding: 6px 12px; border-radius: 0 6px 6px 0; margin: 6px 0; }
.nota { color: var(--gris); font-size: 8.5pt; }
.aviso { background: #fdf6e7; border-left: 3px solid var(--sol); padding: 5px 10px; border-radius: 0 6px 6px 0; font-size: 8.8pt; }
td.clave { font-family: Consolas, monospace; font-size: 8pt; color: var(--gris); }
.si { color: var(--hoja); font-weight: 700; text-align: center; }
.salto { break-before: page; }
""".replace('__V__', VERSION)


def tabla_pasos(nivel):
    filas = []
    for cod, etapa, paso, donde, que, req, resp, plantilla in PASOS[nivel]['pasos']:
        extra = f' <span class="nota">· plantilla «{e(DEFINICIONES[plantilla]["titulo"])}»</span>' if plantilla else ''
        filas.append(
            f'<tr><td class="caja"><span></span></td><td class="cod">{cod}</td>'
            f'<td><b>{e(paso)}</b> <span class="etapa e-{e(etapa)}">{e(etapa)}</span><br>'
            f'<span class="donde">{e(donde)}</span>{extra}</td><td>{e(que)}</td>'
            f'<td>{e(req)}</td><td>{e(resp)}</td></tr>')
    return ('<table><thead><tr><th></th><th>N°</th><th>Paso y dónde</th><th>Qué hacer / qué debe cumplirse</th>'
            '<th>Requisito</th><th>Responsable</th></tr></thead><tbody>' + ''.join(filas) + '</tbody></table>')


def anexo():
    partes = ['<h2 class="salto">Anexo A. Plantillas de carga masiva (columnas)</h2>'
              '<p>Plantillas de Ajustes › Configuración › Carga masiva (Excel) en esta versión. «Oblig.» marca los datos '
              'obligatorios; el encabezado puede ser el título o la clave (en gris). Las fechas van dd/mm/aaaa.</p>']
    for nivel, claves in ANEXO:
        partes.append(f'<h3 style="font-size:11.5pt;color:var(--selva)">Nivel: {e(nivel)}</h3>')
        for clave in claves:
            d = DEFINICIONES[clave]
            filas = ''.join(
                f'<tr><td><b>{e(titulo)}</b></td><td class="clave">{e(c)}</td><td class="si">{"Sí" if obl else ""}</td>'
                f'<td>{e(ej)}</td><td>{e(ayuda)}</td></tr>' for c, titulo, obl, ej, ayuda in d['columnas'])
            partes.append(f'<h3>{e(d["titulo"])}</h3><p class="nota">{e(d["descripcion"])}</p>'
                          '<table><thead><tr><th>Columna</th><th>Clave</th><th>Oblig.</th><th>Ejemplo</th>'
                          f'<th>Ayuda</th></tr></thead><tbody>{filas}</tbody></table>')
    return ''.join(partes)


def guia_html():
    niveles = ''.join(f'<tr><td><b>{e(n)}</b></td><td>{e(a)}</td><td>{e(m)}</td><td>{e(d)}</td></tr>'
                      for n, a, m, d in NIVELES)
    partes = [f"""
<div class="portada">{ISOTIPO}
  <div><div class="serie">Ceiba ERP</div><h1>Guía de implementación y migración de datos</h1>
  <div class="desc">Panorama · paso a paso por niveles (básica, intermedia y completa) con casillas para marcar ·
  plantillas de carga masiva.</div>
  <div class="meta">Versión {VERSION} · {FECHA}</div></div>
</div>
<table style="margin-top:12px"><thead><tr><th>Nivel</th><th>Para qué sirve</th><th>Módulos que quedan operativos</th>
<th>Duración referencial</th></tr></thead><tbody>{niveles}</tbody></table>
<p class="aviso">Esta guía va con la <b>Checklist de implementación - Ceiba ERP.xlsx</b> (misma versión
{VERSION}): los mismos pasos con su código, para marcar el estado, el responsable y las fechas y ver el avance por
nivel. En el papel, marque la casilla de cada paso al terminarlo.</p>
<h2>1. Panorama de la implementación</h2>
<p>Implementar Ceiba ERP es llevar al sistema la información con la que la empresa opera hoy: sus maestros
(productos, clientes, proveedores, cuentas, centros de costo, almacenes…) y sus saldos a una fecha de corte (stock,
cuentas por cobrar y por pagar, caja y bancos). Desde el día siguiente al corte todo se registra en Ceiba. La
implementación se hace por niveles y cada nivel incluye al anterior: no se empieza la intermedia sin terminar la
básica.</p>
<h3>1.1 Principios para que la migración salga bien</h3><ul>"""]
    partes += [f'<li><b>{e(t)}:</b> {e(d)}</li>' for t, d in PRINCIPIOS]
    partes.append('</ul><p class="nota">Cada lista de maestros tiene su botón «Importar desde Excel», que lleva a la '
                  'carga masiva del tipo correspondiente.</p><h3>1.2 Cómo se usa la carga masiva</h3><ol>')
    partes += [f'<li>{e(p)}</li>' for p in USO_CARGA]
    partes.append('</ol>')
    for nivel in ORDEN:
        datos = PASOS[nivel]
        partes.append(f'<h2 class="salto">{e(SECCION[nivel])}</h2><div class="objetivo"><b>Objetivo:</b> '
                      f'{e(datos["objetivo"])}</div>')
        if datos.get('pedir'):
            partes.append(f'<p><b>Información a pedir al sistema anterior:</b> {e(datos["pedir"])}</p>')
        if nivel == PREVIO:
            partes.append('<p class="nota">Si el grupo tiene varias empresas, cada una tiene su propia base de datos: '
                          'repita toda la guía en cada empresa, eligiéndola al ingresar.</p>')
        partes.append(tabla_pasos(nivel))
        if datos.get('nota'):
            partes.append(f'<p class="aviso">{e(datos["nota"])}</p>')
    crono = ''.join(f'<tr><td>{e(s)}</td><td>{e(a)}</td><td>{e(r)}</td></tr>' for s, a, r in CRONOGRAMA)
    errores = ''.join(f'<tr><td><b>{e(m)}</b></td><td>{e(c)}</td><td>{e(s)}</td></tr>' for m, c, s in ERRORES)
    partes.append(f"""<h2 class="salto">6. Cronograma y responsables sugeridos</h2>
<table><thead><tr><th>Semana</th><th>Actividad</th><th>Responsable</th></tr></thead><tbody>{crono}</tbody></table>
<h3>6.1 Errores frecuentes y cómo resolverlos</h3>
<table><thead><tr><th>Mensaje o problema</th><th>Causa</th><th>Solución</th></tr></thead><tbody>{errores}</tbody></table>""")
    partes.append(anexo())
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Guía de implementación — Ceiba ERP'
            f'</title><style>{CSS}</style></head><body>{"".join(partes)}</body></html>')


def imprimir_pdf():
    if not CHROME:
        sys.exit('No se encontró Chrome ni Edge para imprimir el PDF.')
    trabajo = Path(tempfile.mkdtemp(prefix='guia_implementacion_'))
    h = trabajo / 'guia.html'
    h.write_text(guia_html(), encoding='utf-8')
    subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--virtual-time-budget=8000',
                    f'--print-to-pdf={PDF}', h.as_uri()], check=True, capture_output=True, timeout=120)
    print('ok', PDF.name)


# ---------------------------------------------------------------- Excel
F = 'Arial'
VERDE = '0F3D2E'
FINO = Side(style='thin', color='BFBFBF')
BORDE = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
COLOR_NIVEL = {PREVIO: 'EDEDED', BASICA: 'E2EFDA', INTERMEDIA: 'DDEBF7', COMPLETA: 'FCE4D6'}


def checklist():
    wb = Workbook()
    ws = wb.active
    ws.title = 'Checklist'
    ws.sheet_view.showGridLines = False
    ws['A1'] = f'Ceiba ERP · Checklist de implementación · versión {VERSION}'
    ws['A1'].font = Font(name=F, size=16, bold=True, color=VERDE)
    ws['A2'] = (f'Mismos pasos y códigos que la «Guía de implementación y migración de datos» v{VERSION} '
                f'(generada el {FECHA}). Marque el estado de cada paso a medida que avanza.')
    ws['A2'].font = Font(name=F, size=10, italic=True, color='595959')
    ws.merge_cells('B3:D3')
    for celda, texto in (('A3', 'Empresa:'), ('F3', 'Fecha de corte:')):
        ws[celda] = texto
        ws[celda].font = Font(name=F, bold=True)
        ws[celda].alignment = Alignment(horizontal='right')
    for celda in ('B3', 'G3'):
        ws[celda].fill = PatternFill('solid', fgColor='FFF2CC')
        ws[celda].border = BORDE
        ws[celda].font = Font(name=F)
    ws['G3'].number_format = 'dd/mm/yyyy'
    ws['A4'] = ('Complete solo las celdas amarillas (empresa y fecha de corte) y las columnas Responsable asignado, '
                'Estado, Fechas y Observaciones. Estado: Pendiente · En curso · Hecho · No aplica.')
    ws['A4'].font = Font(name=F, size=9, color='595959')

    enc = ['N°', 'Nivel', 'Etapa', 'Paso', 'Dónde (menú)', 'Qué hacer / qué debe cumplirse', 'Requisito previo',
           'Responsable sugerido', 'Responsable asignado', 'Estado', 'Fecha plan', 'Fecha real', 'Observaciones']
    anchos = [7, 11, 13, 34, 38, 52, 16, 20, 18, 12, 12, 12, 30]
    fe = 6
    for i, (t, w) in enumerate(zip(enc, anchos), 1):
        c = ws.cell(fe, i, t)
        c.font = Font(name=F, bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=VERDE)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = BORDE
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[fe].height = 30

    r = fe
    for nivel in ORDEN:
        for cod, etapa, paso, donde, que, req, resp, _ in PASOS[nivel]['pasos']:
            r += 1
            for i, v in enumerate([cod, nivel, etapa, paso, donde, que, req, resp, '', 'Pendiente', None, None, ''], 1):
                c = ws.cell(r, i, v)
                c.font = Font(name=F, size=9, bold=(i == 4))
                c.alignment = Alignment(vertical='top', wrap_text=True,
                                        horizontal='center' if i in (1, 2, 10, 11, 12) else None)
                c.border = BORDE
                if i == 2:
                    c.fill = PatternFill('solid', fgColor=COLOR_NIVEL[nivel])
                if i >= 9:
                    c.fill = PatternFill('solid', fgColor='FFFBEA')
                if i in (11, 12):
                    c.number_format = 'dd/mm/yyyy'
    ult = r

    dv = DataValidation(type='list', formula1='"Pendiente,En curso,Hecho,No aplica"', allow_blank=True)
    dv.error, dv.errorTitle = 'Elija Pendiente, En curso, Hecho o No aplica.', 'Estado'
    ws.add_data_validation(dv)
    dv.add(f'J{fe + 1}:J{ult}')
    dvf = DataValidation(type='date', operator='greaterThan', formula1='DATE(2000,1,1)', allow_blank=True)
    dvf.error = 'Ingrese una fecha dd/mm/aaaa.'
    ws.add_data_validation(dvf)
    dvf.add(f'K{fe + 1}:L{ult}')
    dvf.add('G3')

    rango = f'A{fe + 1}:M{ult}'
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'$J{fe + 1}="Hecho"'], font=Font(color='006100'),
                                                     fill=PatternFill('solid', fgColor='C6EFCE')))
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'$J{fe + 1}="En curso"'],
                                                     fill=PatternFill('solid', fgColor='FFEB9C')))
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'$J{fe + 1}="No aplica"'],
                                                     font=Font(color='A6A6A6', strike=True)))
    ws.conditional_formatting.add(f'K{fe + 1}:K{ult}', FormulaRule(
        formula=[f'AND(K{fe + 1}<>"",K{fe + 1}<TODAY(),$J{fe + 1}<>"Hecho",$J{fe + 1}<>"No aplica")'],
        fill=PatternFill('solid', fgColor='FFC7CE'), font=Font(color='9C0006', bold=True)))
    ws[f'K{fe}'].comment = Comment('Se pinta de rojo si la fecha ya pasó y el paso no está Hecho.', 'Ceiba ERP')
    ws.freeze_panes = f'E{fe + 1}'
    ws.auto_filter.ref = f'A{fe}:M{ult}'
    ws.print_title_rows = f'{fe}:{fe}'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    av = wb.create_sheet('Avance', 0)
    av.sheet_view.showGridLines = False
    av['A1'] = 'Avance de la implementación'
    av['A1'].font = Font(name=F, size=16, bold=True, color=VERDE)
    av['A2'] = f'Ceiba ERP versión {VERSION}'
    av['A3'] = '=IF(Checklist!B3="","Empresa: (pendiente)","Empresa: "&Checklist!B3)'
    av['A4'] = '=IF(Checklist!G3="","Fecha de corte: (pendiente)","Fecha de corte: "&TEXT(Checklist!G3,"dd/mm/yyyy"))'
    for celda in ('A2', 'A3', 'A4'):
        av[celda].font = Font(name=F, size=10, color='595959')
    for i, t in enumerate(['Nivel', 'Pasos', 'Hechos', 'En curso', 'Pendientes', 'No aplica', '% avance'], 1):
        c = av.cell(6, i, t)
        c.font = Font(name=F, bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=VERDE)
        c.alignment = Alignment(horizontal='center')
        c.border = BORDE
    n = f'Checklist!$B${fe + 1}:$B${ult}'
    est = f'Checklist!$J${fe + 1}:$J${ult}'
    for k, nivel in enumerate(ORDEN, 7):
        av.cell(k, 1, nivel).fill = PatternFill('solid', fgColor=COLOR_NIVEL[nivel])
        av.cell(k, 2, f'=COUNTIF({n},A{k})')
        av.cell(k, 3, f'=COUNTIFS({n},A{k},{est},"Hecho")')
        av.cell(k, 4, f'=COUNTIFS({n},A{k},{est},"En curso")')
        av.cell(k, 5, f'=B{k}-C{k}-D{k}-F{k}')
        av.cell(k, 6, f'=COUNTIFS({n},A{k},{est},"No aplica")')
        av.cell(k, 7, f'=IF(B{k}-F{k}=0,0,C{k}/(B{k}-F{k}))')
    av['A11'] = 'Total'
    for col in 'BCDEF':
        av[f'{col}11'] = f'=SUM({col}7:{col}10)'
    av['G11'] = '=IF(B11-F11=0,0,C11/(B11-F11))'
    for fila in av.iter_rows(min_row=7, max_row=11, max_col=7):
        for c in fila:
            c.font = Font(name=F, bold=(c.row == 11))
            c.border = BORDE
            if c.column > 1:
                c.alignment = Alignment(horizontal='center')
        fila[6].number_format = '0%'
    av.conditional_formatting.add('G7:G11', DataBarRule(start_type='num', start_value=0, end_type='num', end_value=1,
                                                        color='63BE7B'))
    av['A13'] = ('El % de avance cuenta los pasos «Hecho» sobre los que aplican (sin «No aplica»). Cada nivel incluye '
                 'al anterior: no empiece la intermedia sin terminar la básica.')
    av['A13'].font = Font(name=F, size=9, italic=True, color='595959')
    av['A14'] = '=IF(AND(B8>0,C8+F8=B8),"Nivel básico terminado: la empresa ya puede operar.","")'
    av['A14'].font = Font(name=F, size=10, bold=True, color='006100')
    for col, w in zip('ABCDEFG', [14, 9, 9, 10, 12, 11, 11]):
        av.column_dimensions[col].width = w
    wb.save(XLSX)
    print('ok', XLSX.name, f'({ult - fe} pasos)')


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    imprimir_pdf()
    checklist()


main()
