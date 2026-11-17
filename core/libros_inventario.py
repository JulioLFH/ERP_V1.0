"""Registro del inventario permanente: formato 12.1 (unidades físicas) y 13.1 (valorizado) de SUNAT."""
import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from .models import D0, Empresa, Kardex, Producto, TIPO_COMPROBANTE, r2
from .utils import _fin_de_mes
from django.utils import timezone

# Tabla 5: tipo de existencia
TIPO_EXISTENCIA = {'MERCADERIA': '01', 'PRODUCTO_TERMINADO': '02', 'MATERIA_PRIMA': '03', 'SEMIELABORADO': '02',
                   'SUMINISTRO': '05', 'ENVASE': '04'}
# Tabla 6: unidad de medida (los códigos del sistema ya son los de SUNAT)
_DOCS = sorted([(nombre, codigo) for codigo, nombre in TIPO_COMPROBANTE] +
               [('Guía de remisión remitente', '09'), ('Guía de remisión transportista', '31')],
               key=lambda x: -len(x[0]))
_SERIE_NUMERO = re.compile(r'\b([A-Z0-9]{4})-(\d{1,8})\b')


def documento(referencia):
    """(tipo tabla 10, serie, número) desde la referencia del kardex; '00' para documentos internos."""
    texto = re.sub(r'^(Reversión|Anulación)\s+', '', referencia or '')
    tipo = '00'
    for nombre, codigo in _DOCS:
        if nombre.lower() in texto.lower():
            tipo = codigo
            break
    m = _SERIE_NUMERO.search(texto)
    return tipo, (m.group(1) if m else ''), (m.group(2) if m else (referencia or '')[:20])


def registro(desde, hasta):
    """[{p, inicial, filas, totales}] de los productos con saldo o movimientos en el rango."""
    productos = {p.pk: p for p in Producto.objects.filter(tipo='BIEN')}
    previos = {}
    for k in Kardex.objects.filter(fecha__lt=desde).order_by('producto_id', 'fecha', 'id').only(
            'producto_id', 'saldo', 'costo_promedio'):
        previos[k.producto_id] = (k.saldo, k.costo_promedio)
    movimientos = {}
    for k in Kardex.objects.filter(fecha__range=[desde, hasta]).select_related('almacen').order_by('fecha', 'id'):
        movimientos.setdefault(k.producto_id, []).append(k)
    bloques = []
    for pid in sorted(set(previos) | set(movimientos), key=lambda x: productos[x].codigo if x in productos else ''):
        p = productos.get(pid)
        if p is None:
            continue
        cant, costo = previos.get(pid, (D0, D0))
        if not cant and pid not in movimientos:
            continue
        inicial = {'cantidad': cant, 'costo': costo, 'total': r2(cant * costo)}
        filas = []
        tot = {'ent_cant': D0, 'ent_total': D0, 'sal_cant': D0, 'sal_total': D0}
        valor_anterior = inicial['total']
        for k in movimientos.get(pid, []):
            tipo, serie, numero = documento(k.referencia)
            # ajuste de valor sin cantidad (tabla 12 = 99, ej. gastos de importación): su total es lo que cambió el
            # saldo valorizado (IMP-06)
            total = r2(k.cantidad * k.costo_unitario) if k.cantidad else r2(k.saldo * k.costo_promedio) - valor_anterior
            valor_anterior = r2(k.saldo * k.costo_promedio)
            entrada = k.tipo == 'ENTRADA'
            fila = {'k': k, 'tipo_doc': tipo, 'serie': serie, 'numero': numero, 'operacion': k.codigo_sunat or '99',
                    'ent_cant': k.cantidad if entrada else D0, 'sal_cant': D0 if entrada else k.cantidad,
                    'ent_costo': k.costo_unitario if entrada else D0, 'sal_costo': D0 if entrada else k.costo_unitario,
                    'ent_total': total if entrada else D0, 'sal_total': D0 if entrada else total,
                    'saldo_cant': k.saldo, 'saldo_costo': k.costo_promedio,
                    'saldo_total': r2(k.saldo * k.costo_promedio)}
            for clave in tot:
                tot[clave] += fila[clave]
            filas.append(fila)
        final = filas[-1] if filas else None
        tot['saldo_cant'] = final['saldo_cant'] if final else cant
        tot['saldo_total'] = final['saldo_total'] if final else inicial['total']
        bloques.append({'p': p, 'tipo_existencia': TIPO_EXISTENCIA.get(p.clase, '99'), 'inicial': inicial,
                        'filas': filas, 'totales': tot})
    return bloques


def excel(bloques, desde, hasta, valorizado):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    empresa = Empresa.actual()
    formato = '13.1' if valorizado else '12.1'
    titulo = ('REGISTRO DE INVENTARIO PERMANENTE VALORIZADO - DETALLE DEL INVENTARIO VALORIZADO' if valorizado else
              'REGISTRO DEL INVENTARIO PERMANENTE EN UNIDADES FÍSICAS - DETALLE DEL INVENTARIO PERMANENTE EN '
              'UNIDADES FÍSICAS')
    wb = Workbook()
    ws = wb.active
    ws.title = f'Formato {formato}'
    negrita = Font(bold=True)
    encabezado = PatternFill('solid', fgColor='1F4E79')
    blanco = Font(bold=True, color='FFFFFF')
    borde = Border(*(Side(style='thin', color='BBBBBB'),) * 4)
    ws.append([f'FORMATO {formato}: {titulo}'])
    ws['A1'].font = Font(bold=True, size=12)
    periodo = (f'{desde:%m/%Y}' if (desde.year, desde.month) == (hasta.year, hasta.month)
               else f'{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}')
    for etiqueta, valor in [('PERIODO:', periodo), ('RUC:', empresa.ruc),
                            ('APELLIDOS Y NOMBRES, DENOMINACIÓN O RAZÓN SOCIAL:', empresa.razon_social),
                            ('ESTABLECIMIENTO (1):', empresa.direccion or 'Todos los almacenes')]:
        ws.append([etiqueta, valor])
        ws.cell(ws.max_row, 1).font = negrita
    if valorizado:
        ws.append(['MÉTODO DE VALUACIÓN:', 'Promedio ponderado móvil'])
        ws.cell(ws.max_row, 1).font = negrita
    cols = (['Fecha', 'Tipo (tabla 10)', 'Serie', 'Número', 'Tipo de operación (tabla 12)'] +
            (['Entradas cant.', 'Entradas costo unit.', 'Entradas costo total', 'Salidas cant.', 'Salidas costo unit.',
              'Salidas costo total', 'Saldo cant.', 'Saldo costo unit.', 'Saldo costo total'] if valorizado else
             ['Entradas', 'Salidas', 'Saldo final']))
    for b in bloques:
        p = b['p']
        ws.append([])
        for etiqueta, valor in [('CÓDIGO DE LA EXISTENCIA:', p.codigo), ('TIPO (TABLA 5):', b['tipo_existencia']),
                                ('DESCRIPCIÓN:', p.nombre), ('CÓDIGO DE LA UNIDAD DE MEDIDA (TABLA 6):', p.unidad)]:
            ws.append([etiqueta, valor])
            ws.cell(ws.max_row, 1).font = negrita
        ws.append(cols)
        for c in ws[ws.max_row]:
            c.font, c.fill, c.border = blanco, encabezado, borde
            c.alignment = Alignment(wrap_text=True, vertical='center')
        ini = b['inicial']
        ws.append(['', '00', '', '', '16', ini['cantidad'], ini['costo'], ini['total'], 0, 0, 0, ini['cantidad'],
                   ini['costo'], ini['total']] if valorizado else ['', '00', '', '', '16', ini['cantidad'], 0,
                                                                   ini['cantidad']])
        ws.cell(ws.max_row, 1).value = 'Saldo inicial'
        for f in b['filas']:
            base = [f['k'].fecha.strftime('%d/%m/%Y'), f['tipo_doc'], f['serie'], f['numero'], f['operacion']]
            if valorizado:
                ws.append(base + [f['ent_cant'], f['ent_costo'], f['ent_total'], f['sal_cant'], f['sal_costo'],
                                  f['sal_total'], f['saldo_cant'], f['saldo_costo'], f['saldo_total']])
            else:
                ws.append(base + [f['ent_cant'], f['sal_cant'], f['saldo_cant']])
        t = b['totales']
        if valorizado:
            ws.append(['', '', '', '', 'TOTALES', ini['cantidad'] + t['ent_cant'], '', ini['total'] + t['ent_total'],
                       t['sal_cant'], '', t['sal_total'], t['saldo_cant'], '', t['saldo_total']])
        else:
            ws.append(['', '', '', '', 'TOTALES', ini['cantidad'] + t['ent_cant'], t['sal_cant'], t['saldo_cant']])
        for c in ws[ws.max_row]:
            c.font = negrita
    for fila in ws.iter_rows(min_row=1):
        for c in fila:
            if isinstance(c.value, Decimal):
                c.value = float(c.value)
    for letra, ancho in zip('ABCDEFGHIJKLMN', [16, 12, 8, 12, 14] + [13] * 9):
        ws.column_dimensions[letra].width = ancho
    ws.column_dimensions['A'].width = 28
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="Formato_{formato}_{desde:%Y%m}.xlsx"'
    wb.save(resp)
    return resp


# ---------------------------------------------------------------- PLE 12.1 (120100) y 13.1 (130100)
# Libros mensuales del PLE con las mismas filas que el Excel (registro()). Supuestos (no validados con el PLE):
# - Estructuras del Anexo 2 de la R.S. 286-2009/SUNAT y modificatorias: 19 campos (12.1) y 27 campos (13.1); los
#   campos de libre utilización no se envían.
# - Catálogo propio (tabla 13 = 9) y sin código de existencia OSCE/GS1 (campos 8 y 9 vacíos).
# - Establecimiento anexo: el código SUNAT del almacén del movimiento ('0000' = domicilio fiscal y para el saldo
#   inicial); el registro suma todos los almacenes, como el Excel.
# - El saldo inicial va como primera fila de cada producto (tipo de operación 16, documento '00', correlativo A),
#   igual que en el Excel, cuando hay cantidad o valor al inicio del mes.
# - Serie o número de documento vacíos van como '0'; cantidades e importes con 2 decimales (el PLE admite
#   hasta 2 decimales como mínimo común en estos campos); método de valuación 1 = promedio ponderado (tabla 14).
CODIGO_PLE = {'12.1': '120100', '13.1': '130100'}
CATALOGO_PROPIO = '9'     # tabla 13: otros (catálogo propio)
METODO_VALUACION = '1'    # tabla 14: promedio ponderado
ESTADO_OPERACION = '1'    # la operación corresponde al periodo
CAMPOS_12_1 = [
    'periodo',            # 1  AAAAMM00
    'cuo',                # 2  código único de la operación (movimiento del kardex)
    'correlativo',        # 3  A (saldo inicial) / M (movimiento) + número
    'establecimiento',    # 4  código de establecimiento anexo (4 dígitos)
    'catalogo',           # 5  código del catálogo utilizado (tabla 13)
    'tipo_existencia',    # 6  tipo de existencia (tabla 5)
    'codigo',             # 7  código propio de la existencia
    'catalogo_osce',      # 8  catálogo del código de existencia (OSCE / GS1), opcional
    'codigo_osce',        # 9  código de existencia (OSCE / GS1), opcional
    'fecha',              # 10 fecha de emisión del documento (dd/mm/aaaa)
    'tipo_doc',           # 11 tipo de documento (tabla 10)
    'serie',              # 12 serie del documento
    'numero',             # 13 número del documento
    'operacion',          # 14 tipo de operación (tabla 12)
    'descripcion',        # 15 descripción de la existencia
    'unidad',             # 16 unidad de medida (tabla 6)
    'ent_cant',           # 17 entradas en unidades físicas
    'sal_cant',           # 18 salidas en unidades físicas
    'estado',             # 19 estado de la operación
]
CAMPOS_13_1 = CAMPOS_12_1[:16] + [
    'metodo',             # 17 método de valuación (tabla 14)
    'ent_cant',           # 18 entradas: cantidad
    'ent_costo',          # 19 entradas: costo unitario
    'ent_total',          # 20 entradas: costo total
    'sal_cant',           # 21 salidas: cantidad
    'sal_costo',          # 22 salidas: costo unitario
    'sal_total',          # 23 salidas: costo total
    'saldo_cant',         # 24 saldo final: cantidad
    'saldo_costo',        # 25 saldo final: costo unitario
    'saldo_total',        # 26 saldo final: costo total
    'estado',             # 27 estado de la operación
]


def _txt(valor, largo, vacio=''):
    return ' '.join(str(valor or '').replace('|', ' ').split())[:largo].strip() or vacio


def _num(valor):
    return f'{(valor or D0):.2f}'


def nombre_ple(ruc, desde, formato, con_datos):
    """LE + RUC + AAAAMM + 00 + libro + 00 (oportunidad) + operaciones + contenido + moneda + PLE (como el 5.1)."""
    return f'LE{ruc}{desde:%Y%m}00{CODIGO_PLE[formato]}001{"1" if con_datos else "0"}11.txt'


def lineas_ple(bloques, desde, valorizado):
    campos = CAMPOS_13_1 if valorizado else CAMPOS_12_1
    periodo = f'{desde:%Y%m}00'
    lineas = []
    for b in bloques:
        p = b['p']
        comun = {'periodo': periodo, 'catalogo': CATALOGO_PROPIO, 'tipo_existencia': b['tipo_existencia'],
                 'codigo': _txt(p.codigo, 24), 'catalogo_osce': '', 'codigo_osce': '',
                 'descripcion': _txt(p.nombre, 80, '-'), 'unidad': p.unidad, 'metodo': METODO_VALUACION,
                 'estado': ESTADO_OPERACION}
        ini = b['inicial']
        filas = []
        if ini['cantidad'] or ini['total']:
            filas.append(dict(comun, cuo=f'SI{p.pk}', correlativo='A1', establecimiento='0000',
                              fecha=desde.strftime('%d/%m/%Y'), tipo_doc='00', serie='0', numero='0',
                              operacion='16', ent_cant=_num(ini['cantidad']), ent_costo=_num(ini['costo']),
                              ent_total=_num(ini['total']), sal_cant=_num(D0), sal_costo=_num(D0),
                              sal_total=_num(D0), saldo_cant=_num(ini['cantidad']), saldo_costo=_num(ini['costo']),
                              saldo_total=_num(ini['total'])))
        for f in b['filas']:
            k = f['k']
            numero = re.sub(r'[^A-Za-z0-9-]', '', f['numero'] or '')[:20]
            filas.append(dict(comun, cuo=f'K{k.pk}', correlativo='M1',
                              establecimiento=(k.almacen.codigo_sunat if k.almacen_id else '') or '0000',
                              fecha=k.fecha.strftime('%d/%m/%Y'), tipo_doc=f['tipo_doc'] or '00',
                              serie=_txt(f['serie'], 20, '0'), numero=numero or '0', operacion=f['operacion'],
                              **{c: _num(f[c]) for c in ('ent_cant', 'ent_costo', 'ent_total', 'sal_cant',
                                                         'sal_costo', 'sal_total', 'saldo_cant', 'saldo_costo',
                                                         'saldo_total')}))
        lineas += ['|'.join(fila[c] for c in campos) + '|' for fila in filas]
    return lineas


def ple(bloques, desde, valorizado):
    """Archivo TXT del PLE 12.1 / 13.1 del mes."""
    from .utils import txt_response
    lineas = lineas_ple(bloques, desde, valorizado)
    formato = '13.1' if valorizado else '12.1'
    return txt_response(nombre_ple(Empresa.actual().ruc, desde, formato, bool(lineas)), lineas)


@login_required
def libro(request):
    from .inventario import _sin_permiso_costos
    from .modulos import puede_ver_costos
    valorizado = request.GET.get('formato_sunat', '12.1') == '13.1'
    if valorizado and not puede_ver_costos(request.user):
        return _sin_permiso_costos(request)
    hoy = timezone.localdate()
    from .utils import anio_param
    anio = anio_param(request)
    mes = request.GET.get('mes', str(hoy.month))
    if mes != 'anual' and not (mes.isdigit() and 1 <= int(mes) <= 12):
        mes = str(hoy.month)
    if mes == 'anual':
        desde, hasta = date(anio, 1, 1), date(anio, 12, 31)
    else:
        desde = date(anio, int(mes), 1)
        hasta = _fin_de_mes(desde)
    if request.GET.get('formato') == 'txt' and mes == 'anual':
        from django.contrib import messages
        from django.shortcuts import redirect
        messages.warning(request, 'El TXT del PLE es mensual: elija un mes.')
        params = request.GET.copy()
        params.pop('formato')
        return redirect(f'{request.path}?{params.urlencode()}')
    bloques = registro(desde, hasta)
    if request.GET.get('formato') == 'txt':  # PLE 12.1 (120100) / 13.1 (130100)
        return ple(bloques, desde, valorizado)
    if request.GET.get('descargar'):
        return excel(bloques, desde, hasta, valorizado)
    return render(request, 'inventario/libro_sunat.html', {
        'bloques': bloques, 'valorizado': valorizado, 'anio': anio, 'mes': mes, 'desde': desde, 'hasta': hasta,
        'anios': range(hoy.year, 2015, -1), 'meses': [(str(i), f'{i:02d}') for i in range(1, 13)],
        'puede_valorizado': puede_ver_costos(request.user)})
