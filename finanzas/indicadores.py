"""Tablero del gerente financiero (FIN-08): liquidez, actividad (DSO, DPO, DIO, ciclo de conversión del efectivo),
endeudamiento, cobertura, EBITDA, márgenes y rentabilidad, con su tendencia de 12 meses y semáforos configurables
(parámetros FIN_SEM_*); y los estados financieros comparativos con análisis vertical y horizontal.

Los saldos salen de la contabilidad (balance acumulado al cierre de cada mes) y los flujos de los últimos 12 meses."""
from decimal import Decimal

from contabilidad import reportes
from contabilidad.reportes import _neto, meses_entre, sumas_por_cuenta
from core.models import r2

D0 = Decimal('0')


def _menos_meses(periodo, n):
    a, m = int(periodo[:4]), int(periodo[4:]) - n
    while m < 1:
        a, m = a - 1, m + 12
    return f'{a}{m:02d}'


def _div(a, b, factor=1):
    return r2(Decimal(a) * factor / b) if b else None


def _flujo(s, prefijos, excluir=()):
    return -_neto(s, prefijos, excluir)


def datos(periodo):
    """Saldos al cierre del mes y flujos de los 12 meses que terminan en él."""
    saldo = sumas_por_cuenta('190001', periodo)
    flujos = sumas_por_cuenta(_menos_meses(periodo, 11), periodo)
    caja = _neto(saldo, ('10',))
    cxc = _neto(saldo, ('12', '13')) + _neto(saldo, ('19',))
    otras_cxc = _neto(saldo, ('14', '16', '17', '18'))
    inventarios = _neto(saldo, ('20', '21', '22', '23', '24', '25', '26', '27', '28', '29'))
    activo_corriente = caja + _neto(saldo, ('11',)) + cxc + otras_cxc + inventarios
    activo_total = _neto(saldo, ('1', '2', '3'))
    cxp = -_neto(saldo, ('42', '43'))
    deuda_financiera = -_neto(saldo, ('45',))
    porcion_lp = _porcion_no_corriente(periodo, deuda_financiera)
    pasivo_total = -_neto(saldo, ('4',))
    pasivo_corriente = pasivo_total - porcion_lp - (-_neto(saldo, ('49',)))
    resultado_acum = reportes.resultado_ejercicio(sumas_por_cuenta(f'{periodo[:4]}01', periodo))
    patrimonio = -_neto(saldo, ('5',)) + resultado_acum
    lineas, neta = reportes.estado_resultados(None, None, sumas=flujos)
    valores = {n: v for n, v, _ in lineas}
    ventas = valores.get('Ventas netas', D0)
    costo = -valores.get('Costo de ventas', D0)
    bruta = valores.get('UTILIDAD BRUTA', D0)
    operativa = valores.get('UTILIDAD OPERATIVA', D0)
    intereses = _neto(flujos, ('67',))
    depreciacion = _neto(flujos, ('681', '682', '683'))
    compras = _neto(flujos, ('60',)) or costo
    # FIN-09: rotaciones con el promedio de los saldos comerciales al inicio y al cierre de los 12 meses: por cobrar
    # solo facturas y letras (121, 123), sin los anticipos de clientes (122); por pagar 421 y 423
    inicio = sumas_por_cuenta('190001', _menos_meses(periodo, 12))
    cxc_com = lambda s: max(_neto(s, ('121', '123')), D0)
    cxp_com = lambda s: max(-_neto(s, ('421', '423')), D0)
    inv = lambda s: max(_neto(s, ('20', '21', '22', '23', '24', '25', '26', '27', '28', '29')), D0)
    return {'periodo': periodo, 'caja': caja, 'cxc': cxc, 'inventarios': inventarios,
            'cxc_prom': (cxc_com(saldo) + cxc_com(inicio)) / 2, 'cxp_prom': (cxp_com(saldo) + cxp_com(inicio)) / 2,
            'inv_prom': (inv(saldo) + inv(inicio)) / 2,
            'activo_corriente': activo_corriente, 'activo_total': activo_total, 'cxp': cxp,
            'pasivo_corriente': pasivo_corriente, 'pasivo_total': pasivo_total, 'patrimonio': patrimonio,
            'deuda_financiera': deuda_financiera, 'ventas': ventas, 'costo': costo, 'bruta': bruta,
            'operativa': operativa, 'neta': neta, 'intereses': intereses, 'depreciacion': depreciacion,
            'ebitda': operativa + depreciacion, 'compras': compras}


def _porcion_no_corriente(periodo, deuda):
    """Capital de los préstamos que vence después de 12 meses (en soles), como máximo la deuda contable."""
    import calendar
    from datetime import date

    from .prestamos import deuda_corriente
    a, m = int(periodo[:4]), int(periodo[4:])
    fin = date(a, m, calendar.monthrange(a, m)[1])
    no_corriente = sum((f['no_corriente'] for f in deuda_corriente(fin, consultar=False)), D0)
    return min(no_corriente, max(deuda, D0))


INDICADORES = [
    # clave, nombre, unidad, semáforo (parámetro, 'min' o 'max')
    ('liquidez', 'Liquidez corriente', 'veces', ('FIN_SEM_LIQUIDEZ_MIN', 'min')),
    ('acida', 'Prueba ácida', 'veces', ('FIN_SEM_ACIDA_MIN', 'min')),
    ('capital_trabajo', 'Capital de trabajo', 'S/', None),
    ('dso', 'Días de cobro (DSO)', 'días', ('FIN_SEM_DSO_MAX', 'max')),
    ('dpo', 'Días de pago (DPO)', 'días', None),
    ('dio', 'Días de inventario (DIO)', 'días', None),
    ('ccc', 'Ciclo de conversión del efectivo', 'días', None),
    ('endeudamiento', 'Endeudamiento (pasivo / activo)', '%', ('FIN_SEM_ENDEUDAMIENTO_MAX', 'max')),
    ('deuda_ebitda', 'Deuda financiera / EBITDA', 'veces', None),
    ('cobertura', 'Cobertura de intereses (EBIT / intereses)', 'veces', ('FIN_SEM_COBERTURA_MIN', 'min')),
    ('ebitda', 'EBITDA (12 meses)', 'S/', None),
    ('margen_bruto', 'Margen bruto', '%', None),
    ('margen_operativo', 'Margen operativo', '%', None),
    ('margen_neto', 'Margen neto', '%', None),
    ('roe', 'ROE (utilidad / patrimonio)', '%', None),
    ('roa', 'ROA (utilidad / activo)', '%', None),
]


def calcular(d):
    # FIN-09: sin ventas, compras o costo en los 12 meses (o sin saldo) el indicador no aplica: «—»
    dso = _div(d['cxc_prom'], d['ventas'], 365) if d['ventas'] > 0 else None
    dpo = _div(d['cxp_prom'], d['compras'], 365) if d['compras'] > 0 else None
    dio = _div(d['inv_prom'], d['costo'], 365) if d['costo'] > 0 and d['inv_prom'] > 0 else None
    return {
        'liquidez': _div(d['activo_corriente'], d['pasivo_corriente']),
        'acida': _div(d['activo_corriente'] - d['inventarios'], d['pasivo_corriente']),
        'capital_trabajo': d['activo_corriente'] - d['pasivo_corriente'],
        'dso': dso, 'dpo': dpo, 'dio': dio,
        'ccc': dso + (dio or D0) - (dpo or D0) if dso is not None else None,
        'endeudamiento': _div(d['pasivo_total'], d['activo_total'], 100),
        'deuda_ebitda': _div(d['deuda_financiera'], d['ebitda']) if d['ebitda'] > 0 else None,
        'cobertura': _div(d['operativa'], d['intereses']),
        'ebitda': d['ebitda'],
        'margen_bruto': _div(d['bruta'], d['ventas'], 100),
        'margen_operativo': _div(d['operativa'], d['ventas'], 100),
        'margen_neto': _div(d['neta'], d['ventas'], 100),
        'roe': _div(d['neta'], d['patrimonio'], 100) if d['patrimonio'] > 0 else None,
        'roa': _div(d['neta'], d['activo_total'], 100),
    }


def semaforo(clave, valor, fecha=None):
    from core import parametros
    regla = next((r for k, _, _, r in INDICADORES if k == clave), None)
    if regla is None or valor is None:
        return ''
    umbral = parametros.valor(regla[0], fecha)
    if regla[1] == 'min':
        return 'verde' if valor >= umbral else ('amarillo' if valor >= umbral * Decimal('0.8') else 'rojo')
    return 'verde' if valor <= umbral else ('amarillo' if valor <= umbral * Decimal('1.2') else 'rojo')


def tablero(periodo, meses=12):
    """Indicadores del mes con su semáforo y la tendencia mensual."""
    columnas = meses_entre(_menos_meses(periodo, meses - 1), periodo)
    base = [datos(p) for p in columnas]
    por_mes = [calcular(d) for d in base]
    actual = por_mes[-1]
    filas = [{'clave': k, 'nombre': n, 'unidad': u, 'valor': actual[k], 'semaforo': semaforo(k, actual[k]),
              'tendencia': [m[k] for m in por_mes]} for k, n, u, _ in INDICADORES]
    return {'periodo': periodo, 'meses': columnas, 'filas': filas, 'datos': base[-1]}


# ---------------------------------------------------------------- análisis vertical y horizontal
def resultados_comparativos(desde, hasta):
    """Estado de resultados mes a mes con el % sobre ventas (vertical) y la variación con el mes anterior
    (horizontal)."""
    meses, filas = reportes.resultados_mensual(desde, hasta)
    ventas = next((f['valores'] for f in filas if f['nombre'] == 'Ventas netas'), [D0] * len(meses))
    for f in filas:
        f['vertical'] = [_div(v, ventas[i], 100) for i, v in enumerate(f['valores'])]
        f['horizontal'] = [None] + [_div(f['valores'][i] - f['valores'][i - 1], abs(f['valores'][i - 1]), 100)
                                    if f['valores'][i - 1] else None for i in range(1, len(meses))]
    return meses, filas


def situacion_comparativa(periodos):
    """Estado de situación resumido al cierre de cada periodo: vertical (% del activo total) y horizontal (variación
    con la columna anterior)."""
    rubros = [('Efectivo y equivalentes', 'caja'), ('Cuentas por cobrar comerciales', 'cxc'),
              ('Inventarios', 'inventarios'), ('ACTIVO CORRIENTE', 'activo_corriente'),
              ('ACTIVO TOTAL', 'activo_total'), ('Cuentas por pagar comerciales', 'cxp'),
              ('Obligaciones financieras', 'deuda_financiera'), ('PASIVO CORRIENTE', 'pasivo_corriente'),
              ('PASIVO TOTAL', 'pasivo_total'), ('PATRIMONIO', 'patrimonio')]
    cols = [datos(p) for p in periodos]
    filas = []
    for nombre, k in rubros:
        valores = [c[k] for c in cols]
        filas.append({'nombre': nombre, 'valores': valores,
                      'vertical': [_div(v, cols[i]['activo_total'], 100) for i, v in enumerate(valores)],
                      'horizontal': [None] + [_div(valores[i] - valores[i - 1], abs(valores[i - 1]), 100)
                                              if valores[i - 1] else None for i in range(1, len(valores))]})
    return filas
