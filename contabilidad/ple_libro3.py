"""PLE del Libro de Inventarios y Balances (formatos 3.x, Anexo 2 de la R.S. 286-2009/SUNAT y modificatorias).

Arma los TXT con los mismos saldos que la pantalla y el Excel de views_libro3 (asientos contables al cierre del
periodo, en soles). Supuestos (no validados con el validador del PLE):
- Periodo del archivo y de cada fila: AAAAMMDD de la fecha de corte (último día del mes pedido); el libro anual es
  al 31/12, oportunidad 01. Otro mes: oportunidad 07 (libre propósito) salvo que se elija otra (tabla de oportunidades
  de presentación del Anexo 2: 01 al 31/12, 02 al 31/01, 03 al 30/06, 04 suspensión o modificación de pagos a cuenta,
  05 reorganización o extinción, 06 liquidación o cese, 07 libre propósito).
- Nombre: LE + RUC + AAAAMMDD + libro (6) + oportunidad (2) + operaciones (1) + contenido (1/0) + moneda (1 = soles)
  + 1 (generado por el PLE), como el 5.1 de views._diario_ple (allí el día y la oportunidad van en 00).
- CUO y correlativo de las filas de detalle: el asiento (sin guiones) y la posición «M<n>» de la primera línea que
  formó el saldo, igual que en el Libro Diario 5.1 del ERP, para que el saldo se ubique en el diario.
- Sin tercero: tipo de documento 0, número «-» y «VARIOS». Importes con 2 decimales; el saldo va en el sentido natural
  de la cuenta (cobrar: deudor positivo; pagar y provisiones: acreedor positivo), un saldo contrario sale negativo.
- Estados financieros (3.1, 3.18, 3.20): catálogo 01 (sector diversas, individual) y rubros de la Tabla 22 según el
  catálogo SMV recordado; los códigos de rubro son TENTATIVOS (ver RUBROS_*) y deben verificarse contra la Tabla 22
  vigente. En 3.18 manda el «Rubro SMV» configurado en cada rubro del EFE; RUBROS_3_18 es solo el respaldo.
- No se generan: 3.8 (títulos, valor nominal y cantidades no se registran), 3.10, 3.19 y 3.23 a 3.25.
"""
import calendar
import io
import re
import zipfile
from collections import defaultdict
from datetime import date
from decimal import ROUND_DOWN, Decimal

from core.models import Empresa

from .models import AsientoLinea, CuentaContable

D0 = Decimal('0')
ESTADO = '1'          # la operación corresponde al periodo
CATALOGO_EEFF = '01'  # tabla 22: sector diversas - individual
SIN_DOC = ('0', '-', 'VARIOS')

# ---------------------------------------------------------------- estructuras (orden de campos del PLE)
ESTRUCTURAS = {
    # 3.1 Estado de situación financiera; 3.18 Flujos de efectivo; 3.20 Estado de resultados (misma estructura)
    '030100': ['periodo',      # 1 AAAAMMDD
               'catalogo',     # 2 código del catálogo (tabla 22)
               'rubro',        # 3 código del rubro del estado financiero (tabla 22)
               'saldo',        # 4 saldo del rubro
               'estado'],      # 5 estado de la operación
    # 3.2 Detalle del saldo de la cuenta 10 - efectivo y equivalentes
    '030200': ['periodo',      # 1
               'cuenta',       # 2 código de la cuenta contable (al máximo nivel usado)
               'entidad',      # 3 entidad financiera (tabla 3); 99 = caja / otros
               'numero',       # 4 número de la cuenta bancaria («-» en caja)
               'moneda',       # 5 tipo de moneda (tabla 4)
               'deudor',       # 6 saldo deudor
               'acreedor',     # 7 saldo acreedor
               'estado'],      # 8
    # 3.3 Cuentas por cobrar comerciales (12 y 13) - una fila por comprobante
    '030300': ['periodo', 'cuo', 'correlativo',
               'tipo_doc',     # 4 tipo de documento de identidad del cliente (tabla 2)
               'numero_doc',   # 5 número de documento
               'nombre',       # 6 apellidos y nombres, denominación o razón social
               'fecha',        # 7 fecha de emisión del comprobante (dd/mm/aaaa)
               'monto',        # 8 monto de la cuenta por cobrar
               'estado'],      # 9
    # 3.4 Cuentas por cobrar a accionistas, personal, directores y gerentes (14) - por tercero
    '030400': ['periodo', 'cuo', 'correlativo', 'tipo_doc', 'numero_doc', 'nombre',
               'fecha',        # 7 fecha de inicio de la operación
               'monto',        # 8 monto de la cuenta por cobrar
               'estado'],
    # 3.5 Cuentas por cobrar diversas (16 y 17) - por tercero
    '030500': ['periodo', 'cuo', 'correlativo', 'tipo_doc', 'numero_doc', 'nombre',
               'fecha',        # 7 fecha de emisión del comprobante (primer movimiento del saldo)
               'monto',        # 8 monto pendiente de cobro
               'estado'],
    # 3.6 Estimación de cuentas de cobranza dudosa (19) - por comprobante
    '030600': ['periodo', 'cuo', 'correlativo', 'tipo_doc', 'numero_doc', 'nombre',
               'tipo_cp',      # 7 tipo de comprobante (tabla 10)
               'serie',        # 8
               'numero_cp',    # 9
               'fecha',        # 10 fecha de emisión
               'monto',        # 11 monto de la provisión (saldo acreedor)
               'estado'],      # 12
    # 3.7 Mercaderías y productos terminados (20 y 21) - por producto, según el kardex valorizado al corte
    '030700': ['periodo',
               'catalogo',     # 2 catálogo utilizado (tabla 13: 9 = propio)
               'tipo_existencia',  # 3 tabla 5
               'codigo',       # 4 código propio de la existencia
               'catalogo_osce',    # 5 catálogo del código de existencia (OSCE / GS1), opcional
               'codigo_osce',  # 6 código de existencia en ese catálogo, opcional
               'descripcion',  # 7
               'unidad',       # 8 unidad de medida (tabla 6)
               'cantidad',     # 9
               'costo',        # 10 costo unitario
               'total',        # 11 costo total
               'estado'],      # 12
    # 3.9 Intangibles (34) y su amortización acumulada (392 / 396 PCGE 2026) - por cuenta
    '030900': ['periodo', 'cuo', 'correlativo',
               'fecha',        # 4 fecha de inicio de la operación
               'descripcion',  # 5 descripción del intangible
               'tipo',         # 6 tipo de intangible (tabla 7)
               'valor',        # 7 valor contable del intangible
               'amortizacion',  # 8 amortización contable acumulada
               'estado'],      # 9
    # 3.11 Remuneraciones y participaciones por pagar (41) - por trabajador
    '031100': ['periodo', 'cuo', 'correlativo',
               'cuenta',       # 4 código de la cuenta contable
               'tipo_doc', 'numero_doc',
               'codigo',       # 7 código del trabajador (su número de documento)
               'nombre',       # 8
               'saldo',        # 9 saldo final
               'estado'],      # 10
    # 3.12 Proveedores (42 y 43) - por comprobante (la fecha va antes del nombre en esta estructura)
    '031200': ['periodo', 'cuo', 'correlativo', 'tipo_doc', 'numero_doc',
               'fecha',        # 6 fecha de emisión del comprobante
               'nombre',       # 7
               'monto',        # 8 monto de la cuenta por pagar
               'estado'],      # 9
    # 3.13 Cuentas por pagar diversas (46 y 47) - por tercero
    '031300': ['periodo', 'cuo', 'correlativo', 'tipo_doc', 'numero_doc',
               'fecha',        # 6 fecha de emisión (primer movimiento del saldo)
               'nombre',       # 7
               'cuenta',       # 8 código de la cuenta contable
               'descripcion',  # 9 descripción de la obligación (nombre de la cuenta)
               'monto',        # 10 monto pendiente de pago
               'estado'],      # 11
    # 3.14 Beneficios sociales de los trabajadores (415 y 47) - misma estructura que la 3.11
    '031400': ['periodo', 'cuo', 'correlativo', 'cuenta', 'tipo_doc', 'numero_doc', 'codigo', 'nombre', 'saldo',
               'estado'],
    # 3.15 Activo y pasivo diferido (37 y 49) - por cuenta
    '031500': ['periodo', 'cuo', 'correlativo',
               'tipo_cp',      # 4 tipo de comprobante (tabla 10; 00 = sin comprobante)
               'serie',        # 5
               'numero_cp',    # 6
               'cuenta',       # 7 código de la cuenta contable
               'concepto',     # 8 concepto u operación
               'saldo',        # 9 saldo final
               'adiciones',    # 10 adiciones (diferencias tributarias: 0, no se registran)
               'deducciones',  # 11 deducciones (ídem)
               'estado'],      # 12
    # 3.16.1 Detalle del saldo de la cuenta 50 - capital
    '031601': ['periodo',
               'capital',      # 2 importe del capital social o participaciones al corte
               'valor_nominal',  # 3 valor nominal por acción o participación
               'suscritas',    # 4 número de acciones o participaciones suscritas
               'pagadas',      # 5 número de acciones o participaciones pagadas
               'estado'],      # 6
    # 3.16.2 Estructura de la participación accionaria (líneas de la 50 con tercero)
    '031602': ['periodo',
               'tipo_doc', 'numero_doc',  # 2-3 accionista o socio
               'tipo_accion',  # 4 tipo de acciones (tabla 16: 1 con derecho a voto, 3 participaciones)
               'nombre',       # 5
               'acciones',     # 6 número de acciones o participaciones
               'porcentaje',   # 7 porcentaje de participación
               'estado'],      # 8
    # 3.17 Balance de comprobación - por cuenta imputable con saldo o movimiento
    '031700': ['periodo',
               'cuenta',       # 2 código de la cuenta contable
               'si_debe', 'si_haber',      # 3-4 saldos iniciales (saldo al cierre del año anterior)
               'mov_debe', 'mov_haber',    # 5-6 movimientos del ejercicio (enero al periodo)
               'sum_debe', 'sum_haber',    # 7-8 sumas del mayor
               'saldo_deudor', 'saldo_acreedor',  # 9-10 saldos al corte
               'transf_debe', 'transf_haber',     # 11-12 transferencias y cancelaciones (0: ya están en el mayor)
               'activo', 'pasivo',         # 13-14 cuentas de balance: activo / pasivo y patrimonio
               'perdidas', 'ganancias',    # 15-16 resultado por naturaleza: pérdidas / ganancias
               'adiciones', 'deducciones',  # 17-18 adiciones y deducciones (0)
               'estado'],      # 19
}
ESTRUCTURAS['031800'] = ESTRUCTURAS['032000'] = ESTRUCTURAS['030100']

# formato del libro -> archivos del PLE (3.16 tiene dos estructuras)
PLE_DE_FORMATO = {
    '3.1': ['030100'], '3.2': ['030200'], '3.3': ['030300'], '3.4': ['030400'], '3.5': ['030500'],
    '3.6': ['030600'], '3.7': ['030700'], '3.9': ['030900'], '3.11': ['031100'], '3.12': ['031200'],
    '3.13': ['031300'], '3.14': ['031400'], '3.15': ['031500'], '3.16': ['031601', '031602'],
    '3.17': ['031700'], '3.18': ['031800'], '3.20': ['032000'],
}
ETIQUETAS = {'031601': '3.16.1 capital', '031602': '3.16.2 accionistas'}
OPORTUNIDADES = [('01', '01 · Al 31 de diciembre'), ('02', '02 · Al 31 de enero'), ('03', '03 · Al 30 de junio'),
                 ('04', '04 · Suspensión o modificación de pagos a cuenta'),
                 ('05', '05 · Reorganización o extinción'), ('06', '06 · Liquidación, cierre o cese'),
                 ('07', '07 · Libre propósito')]

# tabla 3: entidades financieras (bancos de finanzas.Cuenta); sin banco = 99
ENTIDADES = {'BCP': '02', 'INTERBANK': '03', 'SCOTIABANK': '09', 'BBVA': '11', 'BN': '18', 'OTRO': '99'}

# ---------------------------------------------------------------- rubros de la tabla 22 (TENTATIVOS: verificar)
# 3.1: rubro de reportes.situacion_financiera -> código; varios rubros del ERP pueden ir al mismo código (se suman)
RUBROS_3_1 = {
    'Efectivo y equivalentes de efectivo': '1D0101', 'Inversiones financieras': '1D0102',
    'Cuentas por cobrar comerciales (neto)': '1D0103', 'Otras cuentas por cobrar': '1D0104',
    'Inventarios': '1D0107', 'Crédito fiscal y saldos a favor': '1D0109',
    'Servicios y otros contratados por anticipado': '1D0110',
    'Costos por asignar (clase 9 sin su contrapartida 79)': '1D0110',
    'Propiedad, planta y equipo (neto)': '1D0205', 'Intangibles': '1D0206', 'Activo diferido y otros': '1D0209',
    'Obligaciones financieras': '1D0301', 'Cuentas por pagar comerciales': '1D0302',
    'Otras cuentas por pagar': '1D0303', 'Tributos y aportes por pagar': '1D0303',
    'Remuneraciones por pagar': '1D0305', 'Pasivo no corriente': '1D0403',
    'Capital': '1D0701', 'Capital adicional y otros': '1D0702', 'Reservas': '1D0705',
    'Resultados acumulados': '1D0706', 'Resultados de ejercicios anteriores (sin asiento de cierre)': '1D0706',
    'Resultado del ejercicio': '1D0706',
}
# rubro no previsto -> «otros» de su grupo; y los totales
OTROS_3_1 = {'ac': '1D0110', 'anc': '1D0209', 'pc': '1D0308', 'pnc': '1D0403', 'pat': '1D0707'}
TOTALES_3_1 = [('ac', '1D01ST'), ('anc', '1D02ST'), ('activo', '1D020T'), ('pc', '1D03ST'), ('pnc', '1D04ST'),
               ('pasivo', '1D040T'), ('pat', '1D07ST'), ('pasivo_pat', '1D070T')]
# 3.20: línea de reportes.estado_resultados (por función) -> código
RUBROS_3_20 = {
    'Ventas netas': '2D01ST', 'Costo de ventas': '2D0201',
    'Merma anormal de producción (gasto del periodo, NIC 2)': '2D0201',
    'Producción en proceso y costos de producción no absorbidos': '2D0201',
    'UTILIDAD BRUTA': '2D02ST', 'Gastos de ventas': '2D0301', 'Gastos de administración': '2D0302',
    'Otros ingresos de gestión': '2D0304', 'Gastos sin destino asignado': '2D0305',
    'UTILIDAD OPERATIVA': '2D03ST', 'Ingresos financieros': '2D0401', 'Gastos financieros': '2D0402',
    'RESULTADO ANTES DE IMPUESTO A LA RENTA': '2D04ST', 'Impuesto a la renta': '2D0501',
    'RESULTADO DEL EJERCICIO': '2D07ST',
}
OTROS_3_20 = '2D0305'
# 3.18: respaldo cuando el rubro del EFE no tiene «Rubro SMV» (rubros de flujo_semilla, método directo)
RUBROS_3_18 = {
    'OP-E01': '3D0101', 'OP-E03': '3D0103', 'OP-E02': '3D0105', 'OP-S01': '3D0106', 'OP-S03': '3D0107',
    'OP-S02': '3D0109', 'OP-S04': '3D0110', 'OP-S06': '3D0111', 'OP-S05': '3D0112', 'OP-S07': '3D0113',
    'OP-S08': '3D0114',
    'IN-E01': '3D0201', 'IN-E02': '3D0202', 'IN-E03': '3D0203', 'IN-E04': '3D0204', 'IN-S01': '3D0205',
    'IN-S02': '3D0206', 'IN-S03': '3D0207', 'IN-S04': '3D0208',
    'FI-E01': '3D0301', 'FI-E02': '3D0302', 'FI-E03': '3D0303', 'FI-S01': '3D0304', 'FI-S02': '3D0305',
    'FI-S03': '3D0306', 'FI-S04': '3D0307', 'FI-S05': '3D0308',
}
OTROS_3_18 = {'OPERACION': '3D0111', 'INVERSION': '3D0208', 'FINANCIAMIENTO': '3D0308'}
TOTALES_3_18 = {'OPERACION': '3D01ST', 'INVERSION': '3D02ST', 'FINANCIAMIENTO': '3D03ST'}
NETO_3_18 = [('neto', '3D0401'), ('efecto_tc', '3D0402'), ('inicial', '3D0403'), ('final', '3D04ST')]
# tabla 7 (tipo de intangible) por la subcuenta de la 34: 341 concesiones ... 347 plusvalía, 348/349 otros
TIPO_INTANGIBLE = {'1': '01', '2': '02', '3': '03', '4': '04', '5': '05', '6': '06', '7': '07'}
OTRO_INTANGIBLE = '08'

_DOCUMENTO = re.compile(r'^(\w{2})\s+([A-Z0-9]{1,20})-(\S{1,20})$')


# ---------------------------------------------------------------- utilidades
def _txt(valor, largo=100, vacio=''):
    return re.sub(r'[\s|]+', ' ', str(valor or '')).strip()[:largo].strip() or vacio


def _m(valor):
    v = Decimal(valor or 0).quantize(Decimal('0.01'))
    return f'{v:.2f}' if v else '0.00'  # sin «-0.00»


def _f(d):
    return d.strftime('%d/%m/%Y') if d else '01/01/0001'


def corte_de(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def oportunidad_de(periodo, elegida=None):
    if elegida in dict(OPORTUNIDADES):
        return elegida
    return '01' if periodo[4:6] == '12' else '07'


def nombre_archivo(ruc, corte, codigo, oportunidad, con_datos):
    """LE + RUC + AAAAMMDD + libro + oportunidad + operaciones (1) + contenido + moneda (1 = soles) + PLE (1)."""
    return f'LE{ruc}{corte:%Y%m%d}{codigo}{oportunidad}1{"1" if con_datos else "0"}11.txt'


def linea(codigo, fila):
    campos = ESTRUCTURAS[codigo]
    faltan = set(campos) - set(fila)
    if faltan:
        raise KeyError(f'PLE {codigo}: faltan los campos {sorted(faltan)}')
    return '|'.join(str(fila[c]) for c in campos) + '|'


def _tercero(f):
    if not f.get('tercero__numero_doc'):
        return SIN_DOC
    return (f.get('tercero__tipo_doc') or '0', _txt(f['tercero__numero_doc'], 15, '-'),
            _txt(f.get('tercero__nombre'), 100, 'VARIOS'))


def _cuo(filas):
    """{id de línea: (CUO, correlativo)} de la primera línea de cada saldo, como en el Libro Diario 5.1."""
    ids = [f['linea'] for f in filas if f.get('linea')]
    info = {}
    for i in range(0, len(ids), 900):
        for l in AsientoLinea.todas.filter(id__in=ids[i:i + 900]).values('id', 'asiento_id', 'asiento__numero'):
            info[l['id']] = l
    asientos = sorted({l['asiento_id'] for l in info.values()})
    orden = defaultdict(list)
    for i in range(0, len(asientos), 900):
        for a, l in (AsientoLinea.todas.filter(asiento_id__in=asientos[i:i + 900]).order_by('id')
                     .values_list('asiento_id', 'id')):
            orden[a].append(l)
    return {lid: (i['asiento__numero'].replace('-', ''), f'M{orden[i["asiento_id"]].index(lid) + 1}')
            for lid, i in info.items()}


def _detalle(formato, ctx):
    from .views_libro3 import detalle
    filas = detalle(formato, ctx['periodo'])
    cuos = _cuo(filas)
    for f in filas:
        f['cuo'], f['correlativo'] = cuos.get(f.get('linea'), ('0', 'M1'))
    return filas


def _base(f, ctx):
    tipo, numero, nombre = _tercero(f)
    return {'periodo': ctx['fecha'], 'cuo': f['cuo'], 'correlativo': f['correlativo'], 'tipo_doc': tipo,
            'numero_doc': numero, 'nombre': nombre, 'estado': ESTADO}


# ---------------------------------------------------------------- formatos de detalle
def _3_2(ctx):
    from finanzas.models import Cuenta
    bancos = {}
    for c in Cuenta.objects.exclude(cuenta_contable=None).select_related('cuenta_contable').order_by('-activo', 'pk'):
        bancos.setdefault(c.cuenta_contable.codigo, c)
    filas = []
    for f in _detalle('3.2', ctx):
        b = bancos.get(f['cuenta__codigo'])
        es_banco = b is not None and b.tipo == 'BANCO'
        filas.append({'periodo': ctx['fecha'], 'cuenta': f['cuenta__codigo'],
                      'entidad': ENTIDADES.get(b.banco, '99') if es_banco else '99',
                      'numero': _txt(b.numero, 30, '-') if es_banco else '-',
                      'moneda': (b.moneda if b else '') or 'PEN',
                      'deudor': _m(f['deudor']), 'acreedor': _m(f['acreedor']), 'estado': ESTADO})
    return filas


def _cobrar(formato):
    def construir(ctx):
        return [dict(_base(f, ctx), fecha=_f(f['desde']), monto=_m(f['saldo'])) for f in _detalle(formato, ctx)]
    return construir


def _3_6(ctx):
    filas = []
    for f in _detalle('3.6', ctx):
        m = _DOCUMENTO.match(f.get('documento') or '')
        tipo, serie, numero = m.groups() if m else ('00', '-', _txt(f.get('documento'), 20, '-'))
        filas.append(dict(_base(f, ctx), tipo_cp=tipo, serie=serie, numero_cp=numero, fecha=_f(f['desde']),
                          monto=_m(-f['saldo'])))
    return filas


def _3_7(ctx):
    from core.inventario import valor_inventario
    from core.libros_inventario import TIPO_EXISTENCIA
    filas_inv, _ = valor_inventario(ctx['corte'])
    cuentas = dict(CuentaContable.objects.filter(
        pk__in={f['p'].cuenta_existencias_id for f in filas_inv if f['p'].cuenta_existencias_id})
        .values_list('pk', 'codigo'))
    filas = []
    for f in filas_inv:
        p = f['p']
        cuenta = cuentas.get(p.cuenta_existencias_id)
        if not (cuenta.startswith(('20', '21')) if cuenta else p.clase in ('MERCADERIA', 'PRODUCTO_TERMINADO')):
            continue
        filas.append({'periodo': ctx['fecha'], 'catalogo': '9', 'tipo_existencia': TIPO_EXISTENCIA.get(p.clase, '99'),
                      'codigo': _txt(p.codigo, 24, '-'), 'catalogo_osce': '', 'codigo_osce': '',
                      'descripcion': _txt(p.nombre, 80, '-'), 'unidad': p.unidad, 'cantidad': _m(f['cantidad']),
                      'costo': _m(f['costo']), 'total': _m(f['valor']), 'estado': ESTADO})
    return filas


def _3_9(ctx):
    from . import pcge
    amortizacion = '396' if pcge.plan() == pcge.PCGE2026 else '392'
    filas = []
    for f in _detalle('3.9', ctx):
        c = f['cuenta__codigo']
        if c.startswith('34'):
            tipo, valor, amort = TIPO_INTANGIBLE.get(c[2:3], OTRO_INTANGIBLE), f['saldo'], D0
        elif c.startswith(amortizacion):  # la 39 de PPE (depreciación) no es de este formato
            sub = c[4:5] if amortizacion == '396' else ''
            tipo, valor, amort = TIPO_INTANGIBLE.get(sub, OTRO_INTANGIBLE), D0, -f['saldo']
        else:
            continue
        filas.append({'periodo': ctx['fecha'], 'cuo': f['cuo'], 'correlativo': f['correlativo'],
                      'fecha': _f(f['desde']), 'descripcion': _txt(f['cuenta__nombre'], 100, c), 'tipo': tipo,
                      'valor': _m(valor), 'amortizacion': _m(amort), 'estado': ESTADO})
    return filas


def _trabajadores(formato):
    def construir(ctx):
        filas = []
        for f in _detalle(formato, ctx):
            b = _base(f, ctx)
            filas.append(dict(b, cuenta=f['cuenta__codigo'], codigo=b['numero_doc'], saldo=_m(-f['saldo'])))
        return filas
    return construir


def _3_12(ctx):
    return [dict(_base(f, ctx), fecha=_f(f['desde']), monto=_m(-f['saldo'])) for f in _detalle('3.12', ctx)]


def _3_13(ctx):
    return [dict(_base(f, ctx), fecha=_f(f['desde']), cuenta=f['cuenta__codigo'],
                 descripcion=_txt(f['cuenta__nombre'], 100, '-'), monto=_m(-f['saldo']))
            for f in _detalle('3.13', ctx)]


def _3_15(ctx):
    return [{'periodo': ctx['fecha'], 'cuo': f['cuo'], 'correlativo': f['correlativo'], 'tipo_cp': '00',
             'serie': '-', 'numero_cp': '-', 'cuenta': f['cuenta__codigo'],
             'concepto': _txt(f['cuenta__nombre'], 100, '-'),
             'saldo': _m(f['saldo'] if f['cuenta__codigo'].startswith('37') else -f['saldo']),
             'adiciones': _m(D0), 'deducciones': _m(D0), 'estado': ESTADO} for f in _detalle('3.15', ctx)]


def _capital(ctx):
    """(capital al corte, suscripciones por cobrar, valor nominal)."""
    from django.db.models import Sum

    from . import pcge

    def saldo(prefijo):
        a = AsientoLinea.objects.filter(cuenta__codigo__startswith=prefijo,
                                        asiento__periodo__lte=ctx['periodo']).aggregate(d=Sum('debe'), h=Sum('haber'))
        return (a['d'] or D0) - (a['h'] or D0)
    try:
        nominal = Decimal(str(ctx.get('valor_nominal') or '1'))
    except ArithmeticError:
        nominal = Decimal('1')
    return -saldo('50'), max(saldo(pcge.codigo('1421')), D0), nominal if nominal > 0 else Decimal('1')


def _acciones(importe, nominal):
    return (importe / nominal).quantize(Decimal('1'), rounding=ROUND_DOWN)


def _3_16_1(ctx):
    capital, por_cobrar, nominal = _capital(ctx)
    if not capital:
        return []
    return [{'periodo': ctx['fecha'], 'capital': _m(capital), 'valor_nominal': _m(nominal),
             'suscritas': str(_acciones(capital, nominal)), 'pagadas': str(_acciones(capital - por_cobrar, nominal)),
             'estado': ESTADO}]


def _3_16_2(ctx):
    capital, _, nominal = _capital(ctx)
    from django.db.models import Sum
    por_socio = defaultdict(lambda: D0)
    nombres = {}
    for r in (AsientoLinea.objects.filter(cuenta__codigo__startswith='50', asiento__periodo__lte=ctx['periodo'])
              .exclude(tercero=None).values('tercero__tipo_doc', 'tercero__numero_doc', 'tercero__nombre')
              .annotate(d=Sum('debe'), h=Sum('haber')).order_by('tercero__numero_doc')):
        clave = (r['tercero__tipo_doc'] or '0', r['tercero__numero_doc'])
        por_socio[clave] += (r['h'] or D0) - (r['d'] or D0)
        nombres[clave] = r['tercero__nombre']
    filas = []
    for (tipo, numero), importe in por_socio.items():
        if importe <= 0:
            continue
        filas.append({'periodo': ctx['fecha'], 'tipo_doc': tipo, 'numero_doc': _txt(numero, 15, '-'),
                      'tipo_accion': '1', 'nombre': _txt(nombres[(tipo, numero)], 100, '-'),
                      'acciones': str(_acciones(importe, nominal)),
                      'porcentaje': _m(importe * 100 / capital) if capital > 0 else _m(D0), 'estado': ESTADO})
    return filas


# ---------------------------------------------------------------- balance de comprobación y estados financieros
def balance_comprobacion(periodo):
    """Filas de la 3.17 como dicts de Decimal: saldo al cierre del año anterior + movimientos de enero al periodo."""
    from . import pcge, reportes
    anio = periodo[:4]
    inicial = reportes.sumas_por_cuenta('000000', f'{int(anio) - 1}12')
    mov = reportes.sumas_por_cuenta(f'{anio}01', periodo)
    columnas = dict(CuentaContable.objects.filter(codigo__in=set(inicial) | set(mov))
                    .values_list('codigo', 'columna_balance'))
    filas = []
    for codigo in sorted(set(inicial) | set(mov)):
        d0, h0 = inicial.get(codigo, (D0, D0))
        s0 = d0 - h0
        si_d, si_h = max(s0, D0), max(-s0, D0)
        md, mh = mov.get(codigo, (D0, D0))
        sd, sh = si_d + md, si_h + mh
        saldo = sd - sh
        if not (si_d or si_h or md or mh):
            continue
        f = {'cuenta': codigo, 'si_debe': si_d, 'si_haber': si_h, 'mov_debe': md, 'mov_haber': mh,
             'sum_debe': sd, 'sum_haber': sh, 'saldo_deudor': max(saldo, D0), 'saldo_acreedor': max(-saldo, D0),
             'transf_debe': D0, 'transf_haber': D0, 'activo': D0, 'pasivo': D0, 'perdidas': D0, 'ganancias': D0,
             'adiciones': D0, 'deducciones': D0}
        columna = columnas.get(codigo) or pcge.columna_por_defecto(codigo)
        if columna == 'INVENTARIO':
            f['activo'], f['pasivo'] = f['saldo_deudor'], f['saldo_acreedor']
        if columna in ('NATURALEZA', 'RESULTADO'):
            f['perdidas'], f['ganancias'] = f['saldo_deudor'], f['saldo_acreedor']
        filas.append(f)
    _cancelar_costo_de_ventas(filas, columnas)
    return filas


def _cuenta_de_variacion(filas, prefijo):
    """Fila de la cuenta de variación (61 o 71) que recibe el costo de ventas: la de mercaderías / productos
    terminados con movimiento o, si no hay, la primera imputable del plan (se agrega la fila)."""
    preferidos = (prefijo + '11', prefijo + '1', prefijo)
    for preferido in preferidos:
        f = next((f for f in filas if f['cuenta'].startswith(preferido)), None)
        if f:
            return f
    for preferido in preferidos:
        c = (CuentaContable.objects.filter(codigo__startswith=preferido, imputable=True, activo=True)
             .order_by('codigo').values_list('codigo', flat=True).first())
        if c:
            f = {k: D0 for k in filas[0] if k != 'cuenta'} if filas else {}
            f['cuenta'] = c
            filas.append(f)
            filas.sort(key=lambda x: x['cuenta'])
            return f
    return None


def _cancelar_costo_de_ventas(filas, columnas):
    """v1.117.2, hoja de trabajo del PCGE: el costo de ventas (69) va por función; en las columnas de transferencias
    y cancelaciones se cancela contra la variación de existencias (61, mercaderías) o de la producción (71,
    productos), y con eso llega al resultado por naturaleza (columnas 15-16)."""
    from . import pcge
    traslado = {'61': D0, '71': D0}
    costo = [f for f in filas if f['cuenta'].startswith('69')
             and (columnas.get(f['cuenta']) or pcge.columna_por_defecto(f['cuenta'])) not in ('NATURALEZA', 'RESULTADO')]
    for f in costo:
        traslado[pcge.variacion_del_costo_de_ventas(f['cuenta'])] += f['saldo_deudor'] - f['saldo_acreedor']
    for prefijo, importe in traslado.items():
        if not importe:
            continue
        destino = _cuenta_de_variacion(filas, prefijo)
        if destino is None:  # sin cuenta de variación en el plan: el costo queda por naturaleza en la 69
            for f in costo:
                if pcge.variacion_del_costo_de_ventas(f['cuenta']) == prefijo:
                    f['perdidas'], f['ganancias'] = f['saldo_deudor'], f['saldo_acreedor']
            continue
        for f in costo:
            if pcge.variacion_del_costo_de_ventas(f['cuenta']) == prefijo:
                f['transf_haber'] += f['saldo_deudor']
                f['transf_debe'] += f['saldo_acreedor']
        destino['transf_debe'] += max(importe, D0)
        destino['transf_haber'] += max(-importe, D0)
        neto = (destino['saldo_deudor'] - destino['saldo_acreedor'] + destino['transf_debe']
                - destino['transf_haber'])
        destino['perdidas'], destino['ganancias'] = max(neto, D0), max(-neto, D0)


def _3_17(ctx):
    return [dict({k: (v if k == 'cuenta' else _m(v)) for k, v in f.items()}, periodo=ctx['fecha'], estado=ESTADO)
            for f in balance_comprobacion(ctx['periodo'])]


def _rubros(ctx, importes):
    """[(código, importe)] -> filas 030100 sumando los códigos repetidos (en el orden en que aparecen)."""
    suma = {}
    for codigo, importe in importes:
        suma[codigo] = suma.get(codigo, D0) + importe
    return [{'periodo': ctx['fecha'], 'catalogo': CATALOGO_EEFF, 'rubro': c, 'saldo': _m(v), 'estado': ESTADO}
            for c, v in suma.items()]


def _3_1(ctx):
    from . import reportes
    d = reportes.situacion_financiera(ctx['periodo'], f'{ctx["periodo"][:4]}01')
    grupos = [('ac', d['activo']['corriente']), ('anc', d['activo']['no_corriente']),
              ('pc', d['pasivo']['corriente']), ('pnc', d['pasivo']['no_corriente']), ('pat', d['patrimonio'])]
    importes = []
    totales = dict(TOTALES_3_1)
    for grupo, rubros in grupos:
        importes += [(RUBROS_3_1.get(n, OTROS_3_1[grupo]), v) for n, v in rubros]
        importes.append((totales[grupo], d['t'][grupo]))
        if grupo == 'anc':
            importes.append((totales['activo'], d['t']['activo']))
        if grupo == 'pnc':
            importes.append((totales['pasivo'], d['t']['pasivo']))
    importes.append((totales['pasivo_pat'], d['t']['pasivo_pat']))
    return _rubros(ctx, importes)


def _3_18(ctx):
    from . import flujo_efectivo
    e = flujo_efectivo.estado(f'{ctx["periodo"][:4]}01', ctx['periodo'])
    importes = []
    for s in e['secciones']:
        for f in s['filas']:
            rb = f['rubro']
            importes.append(((rb.rubro_smv or '').strip() or RUBROS_3_18.get(rb.codigo, OTROS_3_18[rb.actividad]),
                             f['importe']))
        importes.append((TOTALES_3_18[s['actividad']], s['total']))
    valores = {'neto': e['neto'], 'efecto_tc': e['efecto_tc'] + (e.get('migracion') or D0),
               'inicial': e['inicial'], 'final': e['final']}
    importes += [(c, valores[k]) for k, c in NETO_3_18]
    return _rubros(ctx, importes)


def _3_20(ctx):
    from . import reportes
    lineas, _ = reportes.estado_resultados(f'{ctx["periodo"][:4]}01', ctx['periodo'])
    return _rubros(ctx, [(RUBROS_3_20.get(n, OTROS_3_20), v) for n, v, _ in lineas])


CONSTRUCTORES = {
    '030100': _3_1, '030200': _3_2, '030300': _cobrar('3.3'), '030400': _cobrar('3.4'), '030500': _cobrar('3.5'),
    '030600': _3_6, '030700': _3_7, '030900': _3_9, '031100': _trabajadores('3.11'), '031200': _3_12,
    '031300': _3_13, '031400': _trabajadores('3.14'), '031500': _3_15, '031601': _3_16_1, '031602': _3_16_2,
    '031700': _3_17, '031800': _3_18, '032000': _3_20,
}
REQUIEREN_COSTOS = {'030700'}  # el kardex valorizado: solo con el permiso de ver costos


# ---------------------------------------------------------------- archivos
def contexto(periodo, oportunidad=None, valor_nominal=None):
    corte = corte_de(periodo)
    return {'periodo': periodo, 'corte': corte, 'fecha': corte.strftime('%Y%m%d'),
            'oportunidad': oportunidad_de(periodo, oportunidad), 'valor_nominal': valor_nominal}


def generar(codigo, ctx):
    """(nombre del archivo, [líneas]) de un formato del PLE."""
    lineas = [linea(codigo, f) for f in CONSTRUCTORES[codigo](ctx)]
    ruc = Empresa.actual().ruc
    return nombre_archivo(ruc, ctx['corte'], codigo, ctx['oportunidad'], bool(lineas)), lineas


def generar_zip(ctx, con_costos=True):
    """(nombre del ZIP, bytes) con todos los formatos 3.x que se generan; sin el permiso de costos va sin el 3.7."""
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, 'w', zipfile.ZIP_DEFLATED) as z:
        for codigo in CONSTRUCTORES:
            if codigo in REQUIEREN_COSTOS and not con_costos:
                continue
            nombre, lineas = generar(codigo, ctx)
            z.writestr(nombre, '\r\n'.join(lineas) + ('\r\n' if lineas else ''))
    ruc = Empresa.actual().ruc
    return f'LE{ruc}{ctx["fecha"]}_libro_inventarios_balances.zip', memoria.getvalue()
