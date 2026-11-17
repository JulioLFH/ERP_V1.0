"""Libro Caja y Bancos (R.S. 234-2006/SUNAT y estructuras del PLE).

- Formato 1.1 «Detalle de los movimientos del efectivo» (PLE 010100): las cuentas de tipo CAJA.
- Formato 1.2 «Detalle de los movimientos de la cuenta corriente» (PLE 010200): una sección por cada cuenta BANCO.

La fuente son los movimientos vigentes de Tesorería (los anulados no entran). Los importes van en soles: si la cuenta
es en dólares se toma el importe en S/ de la línea de caja de su asiento contable (T.C. del día de la operación) y,
si el movimiento aún no se contabilizó, el monto por el T.C. venta registrado para esa fecha.

El CUO es el número del asiento de tesorería del movimiento (sin guiones, como en el Libro Diario 5.1) y el
correlativo «M» + la posición de la línea de caja dentro del asiento, así se cruzan con el 5.1. La transferencia
recibida usa el asiento del egreso (se contabiliza una sola vez). Sin asiento, el CUO es el voucher del movimiento
(correlativo propio del sistema, único) con el correlativo M1.
"""
import re
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from core.models import Empresa, r2
from core.utils import fmt_fecha, txt_response

from .models import Cuenta, Movimiento

D0 = Decimal('0')

# Tabla 3 del PLE: entidades del sistema financiero (código de la entidad donde está la cuenta corriente)
TABLA_3_ENTIDADES = {'BCP': '02', 'INTERBANK': '03', 'SCOTIABANK': '09', 'BBVA': '11', 'BN': '18', 'OTRO': '99',
                     '': '99'}

# Tabla 1 del PLE: medios de pago
TABLA_1_MEDIOS = {
    'DEPOSITO': '001',       # Depósito en cuenta
    'TRANSFERENCIA': '003',  # Transferencia de fondos
    'YAPE_PLIN': '003',      # billeteras: transferencia de fondos (supuesto; SUNAT no tiene un código propio)
    'TARJETA': '005',        # Tarjeta de débito (supuesto: el sistema no distingue débito de crédito, 006)
    'CHEQUE': '007',         # Cheques con la cláusula «no negociable», «intransferibles», «no a la orden» u otra
    'EFECTIVO': '008',       # Efectivo, por operaciones en las que no existe obligación de usar medio de pago
}
MEDIO_OTROS = '999'

# El saldo inicial se informa en el TXT solo en enero, con correlativo «A» (asiento de apertura), como el resto de
# libros del PLE. Los demás meses el TXT lleva solo los movimientos del periodo (el saldo inicial queda en el Excel).
SALDO_INICIAL_EN_TXT_DE_ENERO = True

# ---------------------------------------------------------------------------------------------------------------
# Estructura del PLE 010100 (formato 1.1). Un registro por movimiento, campos separados por «|» y «|» al final.
# (n°, campo, cómo se llena)
ESTRUCTURA_PLE_010100 = (
    (1, 'Periodo', 'AAAAMM00'),
    (2, 'CUO', 'N° del asiento de tesorería sin guiones; sin asiento, el voucher; apertura: APERTURA + AAAA'),
    (3, 'Correlativo del asiento', 'M + posición de la línea de caja en el asiento; A + n en la apertura'),
    (4, 'Código de la cuenta contable', 'subcuenta de la caja (10x) al máximo nivel'),
    (5, 'Código de la unidad de operación', 'vacío (opcional)'),
    (6, 'Código del centro de costos', 'vacío (opcional; el centro es de la contrapartida, no de la caja)'),
    (7, 'Tipo de moneda de origen (tabla 4)', 'PEN / USD: moneda de la cuenta'),
    (8, 'Tipo de comprobante (tabla 10)', 'del comprobante cobrado o pagado; 00 = otros'),
    (9, 'Serie del comprobante', 'vacío si no hay comprobante'),
    (10, 'Número del comprobante', 'número del comprobante; si no hay, el N° de operación o el voucher'),
    (11, 'Fecha contable', 'dd/mm/aaaa'),
    (12, 'Fecha de vencimiento', 'vacío (opcional)'),
    (13, 'Fecha de la operación o emisión', 'dd/mm/aaaa'),
    (14, 'Glosa o descripción de la operación', 'glosa del movimiento (máx. 200; sin «|»)'),
    (15, 'Glosa referencial', 'vacío (opcional)'),
    (16, 'Movimientos del Debe', 'ingresos en S/, 2 decimales'),
    (17, 'Movimientos del Haber', 'egresos en S/, 2 decimales'),
    (18, 'Dato estructurado (código de libro, campos 1-3 del RV/RC)', 'vacío (opcional)'),
    (19, 'Estado de la operación', '1 = del periodo (no se generan 8 ni 9)'),
)

# Estructura del PLE 010200 (formato 1.2). Un registro por movimiento de cada cuenta corriente.
ESTRUCTURA_PLE_010200 = (
    (1, 'Periodo', 'AAAAMM00'),
    (2, 'CUO', 'igual que en el 010100'),
    (3, 'Correlativo del asiento', 'igual que en el 010100'),
    (4, 'Código de la entidad financiera (tabla 3)', 'según el banco de la cuenta; 99 = otros'),
    (5, 'Código de la cuenta bancaria del contribuyente', 'N° de cuenta (solo letras, números y guiones; máx. 30)'),
    (6, 'Fecha de la operación', 'dd/mm/aaaa'),
    (7, 'Medio de pago (tabla 1)', 'según el medio de pago del movimiento'),
    (8, 'Descripción de la operación bancaria', 'glosa (máx. 100; sin «|»)'),
    (9, 'Tipo de documento de identidad del girador o beneficiario (tabla 2)', 'del tercero; vacío si no hay'),
    (10, 'Número de documento de identidad del girador o beneficiario', 'del tercero; vacío si no hay'),
    (11, 'Apellidos y nombres, denominación o razón social del girador o beneficiario', 'máx. 100'),
    (12, 'Número de transacción bancaria, documento sustentatorio o control interno', 'N° de operación o voucher'),
    (13, 'Parte deudora de saldos y movimientos', 'ingresos en S/'),
    (14, 'Parte acreedora de saldos y movimientos', 'egresos en S/'),
    (15, 'Estado que identifica la oportunidad de la anotación', '1 = del periodo'),
)
CAMPOS_010100 = len(ESTRUCTURA_PLE_010100)
CAMPOS_010200 = len(ESTRUCTURA_PLE_010200)


# ---------------------------------------------------------------------------------------------------------------
def rango(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    return date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])


def periodo_de(request):
    valor = (request.GET.get('periodo') or '').replace('-', '')
    if len(valor) == 6 and valor.isdigit() and 1 <= int(valor[4:]) <= 12:
        return valor
    return timezone.localdate().strftime('%Y%m')


def _texto(valor, largo):
    return re.sub(r'\s+', ' ', str(valor or '').replace('|', ' ')).strip()[:largo]


def _es_transferencia_recibida(m):
    return m.concepto == 'TRANSFERENCIA' and m.tipo == 'INGRESO' and m.transferencia_par_id


def _asientos(movs):
    """{movimiento.pk: asiento de tesorería (o el del egreso, si es una transferencia recibida)}."""
    from contabilidad.models import Asiento, norma_excluida
    ids = {m.pk for m in movs} | {m.transferencia_par_id for m in movs if _es_transferencia_recibida(m)}
    por_mov = {}
    if ids:
        for a in (Asiento.objects.filter(origen='TESORERIA', movimiento_id__in=ids).exclude(norma=norma_excluida())
                  .prefetch_related('lineas__cuenta').order_by('id')):
            por_mov.setdefault(a.movimiento_id, a)
    salida = {}
    for m in movs:
        a = por_mov.get(m.transferencia_par_id) if _es_transferencia_recibida(m) else None
        salida[m.pk] = a or por_mov.get(m.pk)
    return salida


def _linea_caja(asiento, cuenta_contable_id, ingreso):
    """(n, línea) de la caja o banco dentro del asiento: la de su cuenta contable en el lado del movimiento."""
    if asiento is None or not cuenta_contable_id:
        return None, None
    lineas = list(asiento.lineas.all())
    for n, l in enumerate(lineas, 1):
        if l.cuenta_id == cuenta_contable_id and (l.debe if ingreso else l.haber) > 0:
            return n, l
    return None, None


def _contrapartida(asiento, n_caja, ingreso):
    """Cuenta contable asociada: la primera línea del otro lado del asiento (sin las de destino)."""
    if asiento is None:
        return None
    for n, l in enumerate(asiento.lineas.all(), 1):
        if n != n_caja and not l.es_destino and (l.haber if ingreso else l.debe) > 0:
            return l.cuenta
    return None


class _TC:
    """T.C. venta registrado por fecha, sin consultar a internet (si falta, el último anterior; sin dato, 1)."""

    def __init__(self):
        self.cache = {}

    def __call__(self, fecha):
        if fecha not in self.cache:
            from core.tipo_cambio import venta_del_dia
            self.cache[fecha] = venta_del_dia(fecha, consultar=False)
        return self.cache[fecha]


def _importe_soles(m, linea, tc):
    if linea is not None:
        return linea.debe or linea.haber
    return m.monto if m.cuenta.moneda == 'PEN' else r2(m.monto * tc(m.fecha))


def _tercero(m):
    doc = m.venta or m.compra or m.letra
    t = (m.tercero or (doc.tercero if doc else None) or (m.entrega.responsable if m.entrega_id else None)
         or (m.prestamo.entidad if m.prestamo_id else None) or (m.cheque.tercero if m.cheque_id else None))
    if t is not None:
        return t.tipo_doc, t.numero_doc, t.nombre
    if m.concepto == 'TRANSFERENCIA':  # entre cuentas propias: la misma empresa
        e = Empresa.actual()
        return '6', e.ruc, e.razon_social
    return '', '', ''


def _documento(m):
    """(tipo tabla 10, serie, número) del comprobante cobrado o pagado; '00' sin comprobante."""
    doc = m.venta or m.compra
    if doc is not None:
        return doc.tipo_comprobante, doc.serie, str(doc.numero)
    if m.letra_id:
        return '00', '', m.letra.numero
    return '00', '', (m.numero_operacion or m.voucher)


def _movimientos(cuenta, desde=None, hasta=None, antes_de=None):
    qs = (Movimiento.objects.filter(cuenta=cuenta)
          .select_related('cuenta', 'tercero', 'venta__tercero', 'compra__tercero', 'letra__tercero',
                          'entrega__responsable', 'prestamo__entidad', 'cheque__tercero', 'cuenta_contable')
          .order_by('fecha', 'id'))
    if antes_de:
        return qs.filter(fecha__lt=antes_de)
    return qs.filter(fecha__range=[desde, hasta])


def _saldo_inicial_soles(cuenta, desde, tc):
    """Saldo en S/ al cierre del periodo anterior. En soles: saldo inicial + movimientos anteriores. En dólares:
    el saldo inicial en S/ del asiento de apertura (o al T.C. del primer movimiento) + los movimientos anteriores
    en S/ (sin los ajustes de diferencia de cambio del cierre, que no son movimientos de tesorería)."""
    if cuenta.moneda == 'PEN':
        return cuenta.saldo_al(desde - timedelta(days=1))
    previos = list(_movimientos(cuenta, antes_de=desde))
    asientos = _asientos(previos)
    saldo = D0
    if cuenta.saldo_inicial:
        from django.db.models import Sum
        from contabilidad.models import AsientoLinea
        agg = AsientoLinea.objects.filter(asiento__origen='APERTURA', cuenta_id=cuenta.cuenta_contable_id).aggregate(
            d=Sum('debe'), h=Sum('haber')) if cuenta.cuenta_contable_id else {'d': None, 'h': None}
        if agg['d'] is not None or agg['h'] is not None:
            saldo = (agg['d'] or D0) - (agg['h'] or D0)
        else:
            primero = previos[0].fecha if previos else desde
            saldo = r2(cuenta.saldo_inicial * tc(primero))
    for m in previos:
        _, linea = _linea_caja(asientos[m.pk], cuenta.cuenta_contable_id, m.tipo == 'INGRESO')
        importe = _importe_soles(m, linea, tc)
        saldo += importe if m.tipo == 'INGRESO' else -importe
    return saldo


def bloque(cuenta, periodo, tc=None):
    """Sección del libro de una caja (1.1) o cuenta corriente (1.2) en el periodo AAAAMM."""
    tc = tc or _TC()
    desde, hasta = rango(periodo)
    movs = list(_movimientos(cuenta, desde, hasta))
    asientos = _asientos(movs)
    inicial = _saldo_inicial_soles(cuenta, desde, tc)
    inicial_me = cuenta.saldo_al(desde - timedelta(days=1))
    cc = cuenta.cuenta_contable
    filas, saldo, deudor, acreedor = [], inicial, D0, D0
    for m in movs:
        ingreso = m.tipo == 'INGRESO'
        asiento = asientos[m.pk]
        n, linea = _linea_caja(asiento, cuenta.cuenta_contable_id, ingreso)
        importe = _importe_soles(m, linea, tc)
        contra = _contrapartida(asiento, n, ingreso) or m.cuenta_contable
        tipo_doc, serie, numero = _documento(m)
        t_tipo, t_num, t_nombre = _tercero(m)
        doc = m.venta or m.compra or m.letra
        descripcion = m.glosa or f'{m.get_concepto_display()} {doc or ""}'.strip()
        fila = {
            'm': m, 'fecha': m.fecha,
            'cuo': asiento.numero.replace('-', '') if asiento and n else m.voucher,
            'correlativo': f'M{n}' if asiento and n else 'M1', 'asiento': asiento if n else None,
            'descripcion': descripcion, 'medio': TABLA_1_MEDIOS.get(m.medio_pago, MEDIO_OTROS),
            'medio_nombre': m.get_medio_pago_display(), 'tercero_tipo': t_tipo, 'tercero_doc': t_num,
            'tercero': t_nombre, 'operacion': m.numero_operacion or m.voucher,
            'contra_codigo': contra.codigo if contra else '', 'contra_nombre': contra.nombre if contra else '',
            'tipo_doc': tipo_doc, 'serie': serie, 'numero': numero,
            'deudor': importe if ingreso else D0, 'acreedor': D0 if ingreso else importe,
            'importe_me': m.monto if cuenta.moneda != 'PEN' else None,
        }
        saldo += fila['deudor'] - fila['acreedor']
        deudor += fila['deudor']
        acreedor += fila['acreedor']
        fila['saldo'] = saldo
        filas.append(fila)
    return {
        'cuenta': cuenta, 'formato': '1.1' if cuenta.tipo == 'CAJA' else '1.2',
        'entidad': TABLA_3_ENTIDADES.get(cuenta.banco, '99'), 'entidad_nombre': cuenta.get_banco_display() or 'Otro',
        'numero_cuenta': re.sub(r'[^0-9A-Za-z-]', '', cuenta.numero or '')[:30],
        'cuenta_contable': cc, 'usd': cuenta.moneda != 'PEN', 'filas': filas,
        'inicial': inicial, 'inicial_deudor': max(inicial, D0), 'inicial_acreedor': max(-inicial, D0),
        'inicial_me': inicial_me, 'final_me': cuenta.saldo_al(hasta),
        'deudor': deudor, 'acreedor': acreedor, 'final': inicial + deudor - acreedor,
        'total_deudor': max(inicial, D0) + deudor, 'total_acreedor': max(-inicial, D0) + acreedor,
    }


def libro(periodo, cuentas):
    """[bloque] de las cuentas con saldo o movimientos en el periodo: primero las cajas (1.1), luego bancos (1.2)."""
    tc = _TC()
    bloques = []
    for c in sorted(cuentas, key=lambda c: (c.tipo != 'CAJA', c.nombre)):
        b = bloque(c, periodo, tc)
        if b['filas'] or b['inicial'] or b['inicial_me'] or c.activo:
            bloques.append(b)
    return bloques


# ---------------------------------------------------------------- PLE
def nombre_ple(ruc, periodo, codigo, con_datos):
    """LE + RUC + AAAAMM + 00 + libro + 00 (oportunidad) + 1 (operativa) + contenido + 1 (soles) + 1 (PLE)."""
    return f'LE{ruc}{periodo}00{codigo}001{1 if con_datos else 0}11.txt'


def _apertura(periodo, b, k):
    return SALDO_INICIAL_EN_TXT_DE_ENERO and periodo[4:] == '01' and b['inicial'] and (f'APERTURA{periodo[:4]}', f'A{k}')


def lineas_010100(periodo, bloques):
    lineas, k = [], 0
    for b in bloques:
        if b['formato'] != '1.1':
            continue
        cc = b['cuenta_contable'].codigo if b['cuenta_contable'] else ''
        moneda = b['cuenta'].moneda
        desde = fmt_fecha(rango(periodo)[0])
        k += 1
        apertura = _apertura(periodo, b, k)
        if apertura:
            lineas.append('|'.join([f'{periodo}00', apertura[0], apertura[1], cc, '', '', moneda, '00', '', '',
                                    desde, '', desde, 'SALDO INICIAL', '', f'{b["inicial_deudor"]:.2f}',
                                    f'{b["inicial_acreedor"]:.2f}', '', '1']) + '|')
        for f in b['filas']:
            campos = [f'{periodo}00', _texto(f['cuo'], 40), f['correlativo'], cc, '', '', moneda, f['tipo_doc'],
                      _texto(f['serie'], 20), _texto(f['numero'], 20), fmt_fecha(f['fecha']), '',
                      fmt_fecha(f['fecha']), _texto(f['descripcion'], 200) or 'MOVIMIENTO DE CAJA', '',
                      f'{f["deudor"]:.2f}', f'{f["acreedor"]:.2f}', '', '1']
            lineas.append('|'.join(campos) + '|')
    return lineas


def lineas_010200(periodo, bloques):
    lineas, k = [], 0
    desde = fmt_fecha(rango(periodo)[0])
    for b in bloques:
        if b['formato'] != '1.2':
            continue
        k += 1
        apertura = _apertura(periodo, b, k)
        if apertura:
            lineas.append('|'.join([f'{periodo}00', apertura[0], apertura[1], b['entidad'], b['numero_cuenta'],
                                    desde, MEDIO_OTROS, 'SALDO INICIAL', '', '', '', 'APERTURA',
                                    f'{b["inicial_deudor"]:.2f}', f'{b["inicial_acreedor"]:.2f}', '1']) + '|')
        for f in b['filas']:
            campos = [f'{periodo}00', _texto(f['cuo'], 40), f['correlativo'], b['entidad'], b['numero_cuenta'],
                      fmt_fecha(f['fecha']), f['medio'], _texto(f['descripcion'], 100) or 'OPERACION BANCARIA',
                      f['tercero_tipo'], _texto(f['tercero_doc'], 15), _texto(f['tercero'], 100),
                      _texto(f['operacion'], 20), f'{f["deudor"]:.2f}', f'{f["acreedor"]:.2f}', '1']
            lineas.append('|'.join(campos) + '|')
    return lineas


# ---------------------------------------------------------------- Excel (formatos de la R.S. 234-2006/SUNAT)
TITULOS = {'1.1': 'LIBRO CAJA Y BANCOS - DETALLE DE LOS MOVIMIENTOS DEL EFECTIVO',
           '1.2': 'LIBRO CAJA Y BANCOS - DETALLE DE LOS MOVIMIENTOS DE LA CUENTA CORRIENTE'}
COLUMNAS = {
    '1.1': ['NÚMERO CORRELATIVO DEL REGISTRO O CÓDIGO ÚNICO DE LA OPERACIÓN', 'FECHA DE LA OPERACIÓN',
            'DESCRIPCIÓN DE LA OPERACIÓN', 'CÓDIGO', 'DENOMINACIÓN', 'DEUDOR', 'ACREEDOR'],
    '1.2': ['NÚMERO CORRELATIVO DEL REGISTRO O CÓDIGO ÚNICO DE LA OPERACIÓN', 'FECHA DE LA OPERACIÓN',
            'MEDIO DE PAGO (TABLA 1)', 'DESCRIPCIÓN DE LA OPERACIÓN',
            'APELLIDOS Y NOMBRES, DENOMINACIÓN O RAZÓN SOCIAL',
            'NÚMERO DE TRANSACCIÓN BANCARIA, DE DOCUMENTO SUSTENTATORIO O DE CONTROL INTERNO DE LA OPERACIÓN',
            'CÓDIGO', 'DENOMINACIÓN', 'DEUDOR', 'ACREEDOR'],
}
# grupos de la primera fila del encabezado: (texto, primera columna, última columna) desde 1
GRUPOS = {'1.1': [('CUENTA CONTABLE ASOCIADA', 4, 5), ('SALDOS Y MOVIMIENTOS', 6, 7)],
          '1.2': [('OPERACIONES BANCARIAS', 3, 6), ('CUENTA CONTABLE ASOCIADA', 7, 8), ('SALDOS Y MOVIMIENTOS', 9, 10)]}


def excel(periodo, bloques):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    empresa = Empresa.actual()
    wb = Workbook()
    wb.remove(wb.active)
    negrita = Font(bold=True)
    blanco = Font(bold=True, color='FFFFFF')
    relleno = PatternFill('solid', fgColor='1F4E79')
    borde = Border(*(Side(style='thin', color='BBBBBB'),) * 4)
    centro = Alignment(wrap_text=True, vertical='center', horizontal='center')
    for formato in ('1.1', '1.2'):
        del_formato = [b for b in bloques if b['formato'] == formato]
        if not del_formato and formato == '1.2' and wb.sheetnames:
            continue
        ws = wb.create_sheet(f'Formato {formato}')
        ws.append([f'FORMATO {formato}: "{TITULOS[formato]}"'])
        ws['A1'].font = Font(bold=True, size=12)
        for etiqueta, valor in [('PERIODO:', f'{periodo[4:]}/{periodo[:4]}'), ('RUC:', empresa.ruc),
                                ('APELLIDOS Y NOMBRES, DENOMINACIÓN O RAZÓN SOCIAL:', empresa.razon_social)]:
            ws.append([etiqueta, valor])
            ws.cell(ws.max_row, 1).font = negrita
        cols = COLUMNAS[formato]
        n = len(cols)
        if not del_formato:
            ws.append([])
            ws.append(['Sin cuentas de caja ni bancos con saldo o movimientos en el periodo.'])
        for b in del_formato:
            c = b['cuenta']
            ws.append([])
            datos = ([('ENTIDAD FINANCIERA:', f'{b["entidad"]} {b["entidad_nombre"]}'),
                      ('CÓDIGO DE LA CUENTA CORRIENTE:', c.numero or c.nombre)] if formato == '1.2' else
                     [('CAJA:', c.nombre)])
            datos.append(('CUENTA CONTABLE:', f'{b["cuenta_contable"].codigo} {b["cuenta_contable"].nombre}'
                          if b['cuenta_contable'] else ''))
            if b['usd']:
                datos.append(('MONEDA:', f'Dólares (US$ {b["inicial_me"]:,.2f} al inicio, US$ {b["final_me"]:,.2f} '
                                         f'al cierre). Importes en S/ al tipo de cambio de cada operación'))
            for etiqueta, valor in datos:
                ws.append([etiqueta, valor])
                ws.cell(ws.max_row, 1).font = negrita
            fila_grupo = ws.max_row + 1
            ws.append([''] * n)
            ws.append(cols)
            for col, nombre in enumerate(cols, 1):
                ws.cell(fila_grupo, col).value = nombre
            for texto, ini, fin in GRUPOS[formato]:
                for col in range(ini, fin + 1):
                    ws.cell(fila_grupo, col).value = None
                ws.cell(fila_grupo, ini).value = texto
                ws.merge_cells(start_row=fila_grupo, start_column=ini, end_row=fila_grupo, end_column=fin)
            agrupadas = {col for _, ini, fin in GRUPOS[formato] for col in range(ini, fin + 1)}
            for col in range(1, n + 1):
                if col not in agrupadas:  # la columna sin grupo ocupa las dos filas del encabezado
                    ws.cell(fila_grupo + 1, col).value = None
                    ws.merge_cells(start_row=fila_grupo, start_column=col, end_row=fila_grupo + 1, end_column=col)
            for fila in ws.iter_rows(min_row=fila_grupo, max_row=fila_grupo + 1, max_col=n):
                for celda in fila:
                    celda.font, celda.fill, celda.border, celda.alignment = blanco, relleno, borde, centro
            ws.row_dimensions[fila_grupo + 1].height = 45
            ws.append(['', '', 'SALDO INICIAL'] + [''] * (n - 5) + [b['inicial_deudor'], b['inicial_acreedor']])
            for f in b['filas']:
                if formato == '1.1':
                    ws.append([f['cuo'], fmt_fecha(f['fecha']), f['descripcion'], f['contra_codigo'],
                               f['contra_nombre'], f['deudor'], f['acreedor']])
                else:
                    ws.append([f['cuo'], fmt_fecha(f['fecha']), f['medio'], f['descripcion'], f['tercero'],
                               f['operacion'], f['contra_codigo'], f['contra_nombre'], f['deudor'], f['acreedor']])
            ws.append([''] * (n - 3) + ['TOTALES', b['total_deudor'], b['total_acreedor']])
            for celda in ws[ws.max_row]:
                celda.font = negrita
            ws.append([''] * (n - 3) + ['SALDO FINAL', max(b['final'], D0), max(-b['final'], D0)])
            for celda in ws[ws.max_row]:
                celda.font = negrita
        for fila in ws.iter_rows(min_row=1):
            for celda in fila:
                if isinstance(celda.value, Decimal):
                    celda.value = float(celda.value)
                    celda.number_format = '#,##0.00'
                elif isinstance(celda.value, str) and celda.value.startswith('='):
                    celda.data_type = 's'  # SEG-02: un texto con «=» queda como texto
        anchos = [22, 12, 40, 12, 30, 14, 14] if formato == '1.1' else [22, 12, 10, 36, 30, 22, 12, 28, 14, 14]
        for i, ancho in enumerate(anchos):
            ws.column_dimensions[chr(65 + i)].width = ancho
        ws.column_dimensions['A'].width = 34
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="Libro_caja_bancos_{periodo}.xlsx"'
    wb.save(resp)
    return resp


# ---------------------------------------------------------------- pantalla
def _contabilidad_al_dia(request):
    """Como las pantallas de Contabilidad: centraliza antes los periodos con cambios (los CUO son los asientos)."""
    from contabilidad import automatico
    if request.method == 'GET' and automatico.periodos_pendientes():
        for error in automatico.actualizar_pendientes(limite_segundos=8):
            messages.warning(request, error)
        if automatico.periodos_pendientes():
            messages.info(request, 'Aún hay periodos por contabilizar: los movimientos sin asiento usan su voucher '
                                   'como CUO. Recargue en unos segundos.')


@login_required
def libro_caja(request):
    from core.politicas import filtrar_cuentas, ver_saldos
    if not ver_saldos(request.user):
        messages.error(request, 'Su perfil no permite ver los saldos de caja y bancos.')
        return redirect('finanzas:movimientos')
    periodo = periodo_de(request)
    _contabilidad_al_dia(request)
    todas = list(filtrar_cuentas(Cuenta.objects.select_related('cuenta_contable'), request.user, 'pk'))
    elegida = request.GET.get('cuenta', '')
    formato = request.GET.get('formato')
    empresa = Empresa.actual()
    if formato in ('ple11', 'ple12'):  # el TXT es el libro completo del periodo (todas las cuentas permitidas)
        bloques = libro(periodo, todas)
        codigo, lineas = (('010100', lineas_010100(periodo, bloques)) if formato == 'ple11' else
                          ('010200', lineas_010200(periodo, bloques)))
        return txt_response(nombre_ple(empresa.ruc, periodo, codigo, bool(lineas)), lineas)
    cuentas = [c for c in todas if str(c.pk) == elegida] if elegida.isdigit() else todas
    bloques = libro(periodo, cuentas)
    if formato == 'excel':
        return excel(periodo, bloques)
    return render(request, 'finanzas/libro_caja.html', {
        'periodo': periodo, 'mes': f'{periodo[:4]}-{periodo[4:]}', 'empresa': empresa, 'cuentas': todas,
        'elegida': elegida, 'cajas': [b for b in bloques if b['formato'] == '1.1'],
        'bancos': [b for b in bloques if b['formato'] == '1.2']})
