"""Cálculos de balance de comprobación y estados financieros a partir de AsientoLinea."""
from collections import OrderedDict
from decimal import Decimal

from django.db.models import Sum

from core.models import r2

from .models import AsientoLinea, CuentaContable

D0 = Decimal('0')


def lineas_rango(desde, hasta):
    return AsientoLinea.objects.filter(asiento__periodo__gte=desde, asiento__periodo__lte=hasta)


def sumas_por_cuenta(desde, hasta):
    """{codigo: (debe, haber)} de cuentas con movimiento en el rango de periodos."""
    filas = lineas_rango(desde, hasta).values('cuenta__codigo').annotate(d=Sum('debe'), h=Sum('haber'))
    return {f['cuenta__codigo']: (r2(f['d']), r2(f['h'])) for f in filas}


def _neto(sumas, prefijos, excluir=()):
    """Debe - haber de las cuentas que empiezan con algún prefijo."""
    total = D0
    for codigo, (d, h) in sumas.items():
        if codigo.startswith(prefijos) and not codigo.startswith(excluir):
            total += d - h
    return total


def balance_comprobacion(desde, hasta, nivel=None):
    sumas = sumas_por_cuenta(desde, hasta)
    if nivel:  # agrupar a nivel de cuenta mayor (2 dígitos) o subcuenta (3 dígitos)
        agrupado = {}
        for codigo, (d, h) in sumas.items():
            k = codigo[:nivel]
            ad, ah = agrupado.get(k, (D0, D0))
            agrupado[k] = (ad + d, ah + h)
        sumas = agrupado
    nombres = dict(CuentaContable.objects.filter(codigo__in=sumas).values_list('codigo', 'nombre'))
    filas, tot = [], {k: D0 for k in ('d', 'h', 'sd', 'sa', 'activo', 'pasivo', 'nat_p', 'nat_g', 'fun_p', 'fun_g')}
    for codigo in sorted(sumas):
        d, h = sumas[codigo]
        saldo = d - h
        f = {'codigo': codigo, 'nombre': nombres.get(codigo, ''), 'd': d, 'h': h,
             'sd': max(saldo, D0), 'sa': max(-saldo, D0)}
        clase = codigo[0]
        if clase in '12345':
            f['activo'], f['pasivo'] = f['sd'], f['sa']
        elif not codigo.startswith('79'):
            # inventario permanente: la 61 solo refleja compras, por eso el costo de ventas (69)
            # también entra en la columna por naturaleza
            if codigo.startswith(('60', '61', '62', '63', '64', '65', '66', '67', '68', '69')) or (
                    clase == '7' and not codigo.startswith('79')):
                f['nat_p'], f['nat_g'] = f['sd'], f['sa']
            if codigo.startswith('69') or clase == '9' or codigo.startswith('88') or (
                    clase == '7' and not codigo.startswith('79')):
                f['fun_p'], f['fun_g'] = f['sd'], f['sa']
            if codigo.startswith('88'):
                f['nat_p'], f['nat_g'] = f['sd'], f['sa']
        for k in tot:
            tot[k] += f.get(k, D0)
        filas.append(f)
    tot['res_bal'] = tot['activo'] - tot['pasivo']
    tot['res_nat'] = tot['nat_g'] - tot['nat_p']
    tot['res_fun'] = tot['fun_g'] - tot['fun_p']
    return filas, tot


def resultado_ejercicio(sumas):
    """Ingresos (7 sin 79) - costo de ventas (69) - gastos por naturaleza (62-68) - impuesto a la renta (88)."""
    ingresos = -_neto(sumas, ('7',), excluir=('79',))
    costo = _neto(sumas, ('69',))
    gastos = _neto(sumas, ('62', '63', '64', '65', '66', '67', '68'))
    renta = _neto(sumas, ('88',))
    return ingresos - costo - gastos - renta


def estado_resultados(desde, hasta):
    s = sumas_por_cuenta(desde, hasta)
    ventas = -_neto(s, ('70', '71', '72', '73', '74'))
    costo = _neto(s, ('69',))
    bruta = ventas - costo
    adm, vtas, fin = _neto(s, ('94',)), _neto(s, ('95',)), _neto(s, ('97',))
    otros_9 = _neto(s, ('9',), excluir=('94', '95', '97'))
    gastos_nat = _neto(s, ('62', '63', '64', '65', '66', '67', '68'))
    sin_destino = gastos_nat - (adm + vtas + fin + otros_9)
    otros_ing = -_neto(s, ('75', '76', '78'))
    operativa = bruta - adm - vtas - otros_9 - sin_destino + otros_ing
    ing_fin = -_neto(s, ('77',))
    antes = operativa + ing_fin - fin
    renta = _neto(s, ('88',))
    neta = antes - renta
    lineas = [
        ('Ventas netas', ventas, 'total'),
        ('Costo de ventas', -costo, ''),
        ('UTILIDAD BRUTA', bruta, 'subtotal'),
        ('Gastos de administración', -adm, ''),
        ('Gastos de ventas', -vtas, ''),
    ]
    if otros_9:
        lineas.append(('Otros gastos por función', -otros_9, ''))
    if sin_destino:
        lineas.append(('Gastos sin destino asignado', -sin_destino, 'alerta'))
    lineas += [
        ('Otros ingresos de gestión', otros_ing, ''),
        ('UTILIDAD OPERATIVA', operativa, 'subtotal'),
        ('Ingresos financieros', ing_fin, ''),
        ('Gastos financieros', -fin, ''),
        ('RESULTADO ANTES DE IMPUESTO A LA RENTA', antes, 'subtotal'),
        ('Impuesto a la renta', -renta, ''),
        ('RESULTADO DEL EJERCICIO', neta, 'total'),
    ]
    return lineas, neta


RUBROS_ACTIVO = [
    ('corriente', 'Efectivo y equivalentes de efectivo', ('10',)),
    ('corriente', 'Inversiones financieras', ('11',)),
    ('corriente', 'Cuentas por cobrar comerciales (neto)', ('12', '13', '19')),
    ('corriente', 'Otras cuentas por cobrar', ('14', '15', '16', '17')),
    ('corriente', 'Servicios y otros contratados por anticipado', ('18',)),
    ('corriente', 'Inventarios', ('20', '21', '22', '23', '24', '25', '26', '27', '28', '29')),
    ('no_corriente', 'Propiedad, planta y equipo (neto)', ('30', '31', '32', '33', '39')),
    ('no_corriente', 'Intangibles', ('34', '35')),
    ('no_corriente', 'Activo diferido y otros', ('36', '37', '38')),
]
RUBROS_PASIVO = [
    ('corriente', 'Tributos y aportes por pagar', ('40',)),
    ('corriente', 'Remuneraciones por pagar', ('41',)),
    ('corriente', 'Cuentas por pagar comerciales', ('42', '43')),
    ('corriente', 'Otras cuentas por pagar', ('44', '46')),
    ('corriente', 'Obligaciones financieras', ('45',)),
    ('no_corriente', 'Pasivo no corriente', ('47', '48', '49')),
]
RUBROS_PATRIMONIO = [
    ('Capital', ('50',)),
    ('Capital adicional y otros', ('51', '52', '53', '54', '55', '56', '57')),
    ('Reservas', ('58',)),
    ('Resultados acumulados', ('59',)),
]


def situacion_financiera(hasta, desde_ejercicio):
    """Saldos acumulados hasta el periodo; el resultado del ejercicio se calcula desde enero."""
    saldos = sumas_por_cuenta('000000', hasta)
    resultado = resultado_ejercicio(sumas_por_cuenta(desde_ejercicio, hasta))
    activo = {'corriente': [], 'no_corriente': []}
    pasivo = {'corriente': [], 'no_corriente': []}
    for grupo, nombre, prefijos in RUBROS_ACTIVO:
        v = _neto(saldos, prefijos)
        if v:
            activo[grupo].append((nombre, v))
    for grupo, nombre, prefijos in RUBROS_PASIVO:
        v = -_neto(saldos, prefijos)
        if v < 0 and prefijos == ('40',):  # saldo a favor (crédito fiscal) se muestra como activo
            activo['corriente'].append(('Crédito fiscal y saldos a favor', -v))
        elif v:
            pasivo[grupo].append((nombre, v))
    patrimonio = [(n, -_neto(saldos, p)) for n, p in RUBROS_PATRIMONIO if _neto(saldos, p)]
    # ejercicios anteriores aún no trasladados a la cuenta 59 con el asiento de cierre
    anterior = sumas_por_cuenta('000000', f'{int(desde_ejercicio) - 1:06d}')
    pendiente = -_neto(anterior, ('6', '7', '8', '9'))
    if pendiente:
        patrimonio.append(('Resultados de ejercicios anteriores (sin asiento de cierre)', pendiente))
    patrimonio.append(('Resultado del ejercicio', resultado))
    t = {
        'ac': sum((v for _, v in activo['corriente']), D0),
        'anc': sum((v for _, v in activo['no_corriente']), D0),
        'pc': sum((v for _, v in pasivo['corriente']), D0),
        'pnc': sum((v for _, v in pasivo['no_corriente']), D0),
        'pat': sum((v for _, v in patrimonio), D0),
    }
    t['activo'] = t['ac'] + t['anc']
    t['pasivo'] = t['pc'] + t['pnc']
    t['pasivo_pat'] = t['pasivo'] + t['pat']
    t['diferencia'] = t['activo'] - t['pasivo_pat']
    return {'activo': activo, 'pasivo': pasivo, 'patrimonio': patrimonio, 't': t}
