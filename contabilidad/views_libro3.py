"""Libro de Inventarios y Balances (formatos 3.x de la R.S. 234-2006/SUNAT) desde los asientos contables.

Los formatos de detalle muestran el saldo al cierre del periodo de las cuentas del formato, por cuenta, por tercero o
por documento (según el formato). Los formatos que son estados financieros enlazan a sus reportes."""
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Max, Min, Sum
from django.shortcuts import render

from core.utils import excel_response

from .models import AsientoLinea
from .views import al_dia

# código, título, prefijos de cuentas, agrupación ('documento', 'tercero', 'cuenta') o ruta del reporte
FORMATOS = [
    ('3.1', 'Estado de situación financiera', None, 'contabilidad:situacion'),
    ('3.2', 'Detalle del saldo de la cuenta 10 - Efectivo y equivalentes de efectivo', ('10',), 'cuenta'),
    ('3.3', 'Detalle del saldo de la cuenta 12 y 13 - Cuentas por cobrar comerciales', ('12', '13'), 'documento'),
    ('3.4', 'Detalle del saldo de la cuenta 14 - Cuentas por cobrar al personal, accionistas, directores y gerentes',
     ('14',), 'tercero'),
    ('3.5', 'Detalle del saldo de la cuenta 16 y 17 - Cuentas por cobrar diversas', ('16', '17'), 'tercero'),
    ('3.6', 'Detalle del saldo de la cuenta 19 - Estimación de cuentas de cobranza dudosa', ('19',), 'documento'),
    ('3.7', 'Detalle del saldo de la cuenta 20 y 21 - Mercaderías y productos terminados', None, 'inv_valorizacion'),
    ('3.8', 'Detalle del saldo de la cuenta 30 - Inversiones mobiliarias', ('30',), 'cuenta'),
    ('3.9', 'Detalle del saldo de la cuenta 34 - Intangibles', ('34', '39'), 'cuenta'),
    ('3.11', 'Detalle del saldo de la cuenta 41 - Remuneraciones y participaciones por pagar', ('41',), 'tercero'),
    ('3.12', 'Detalle del saldo de la cuenta 42 y 43 - Cuentas por pagar comerciales', ('42', '43'), 'documento'),
    ('3.13', 'Detalle del saldo de la cuenta 46 y 47 - Cuentas por pagar diversas', ('46', '47'), 'tercero'),
    ('3.14', 'Detalle del saldo de la cuenta 47 - Beneficios sociales de los trabajadores', ('415', '47'), 'tercero'),
    ('3.15', 'Detalle del saldo de la cuenta 37 y 49 - Activo y pasivo diferido', ('37', '49'), 'cuenta'),
    ('3.16', 'Detalle del saldo de la cuenta 50 - Capital', ('50',), 'cuenta'),
    ('3.17', 'Balance de comprobación', None, 'contabilidad:balance'),
    ('3.20', 'Estado de resultados', None, 'contabilidad:resultados'),
]
POR_CODIGO = {f[0]: f for f in FORMATOS}


def _ultimo_mes():
    hoy = date.today()
    return f'{hoy.year - (hoy.month == 1)}{(hoy.month - 2) % 12 + 1:02d}'


def detalle(formato, periodo):
    """[fila] con el saldo al cierre del periodo según la agrupación del formato."""
    _, _, prefijos, por = POR_CODIGO[formato]
    from django.db.models import Q
    filtro = Q()
    for p in prefijos:
        filtro |= Q(cuenta__codigo__startswith=p)
    qs = AsientoLinea.objects.filter(filtro, asiento__periodo__lte=periodo)
    campos = ['cuenta__codigo', 'cuenta__nombre']
    if por in ('tercero', 'documento'):
        campos += ['tercero__tipo_doc', 'tercero__numero_doc', 'tercero__nombre']
    if por == 'documento':
        campos += ['documento']
    filas = []
    for r in (qs.values(*campos).annotate(d=Sum('debe'), h=Sum('haber'), desde=Min('asiento__fecha'),
                                          hasta=Max('asiento__fecha'))
              .order_by(*campos)):
        saldo = (r['d'] or 0) - (r['h'] or 0)
        if saldo:
            r['saldo'] = saldo
            r['deudor'], r['acreedor'] = max(saldo, 0), max(-saldo, 0)
            filas.append(r)
    return filas


@login_required
@al_dia
def libro_inventarios(request):
    formato = request.GET.get('formato', '3.3')
    if formato not in POR_CODIGO:
        formato = '3.3'
    periodo = (request.GET.get('periodo') or _ultimo_mes()).replace('-', '')
    codigo, titulo, prefijos, por = POR_CODIGO[formato]
    filas = detalle(formato, periodo) if prefijos else []
    tot = {'d': sum(f['deudor'] for f in filas), 'a': sum(f['acreedor'] for f in filas)}
    if request.GET.get('formato_salida') == 'excel' and prefijos:
        enc = ['Cuenta', 'Denominación']
        if por in ('tercero', 'documento'):
            enc += ['Tipo doc.', 'N° documento', 'Apellidos y nombres / razón social']
        if por == 'documento':
            enc += ['Comprobante', 'Fecha de emisión']
        enc += ['Saldo deudor', 'Saldo acreedor']
        datos = []
        for f in filas:
            fila = [f['cuenta__codigo'], f['cuenta__nombre']]
            if por in ('tercero', 'documento'):
                fila += [f.get('tercero__tipo_doc') or '', f.get('tercero__numero_doc') or '',
                         f.get('tercero__nombre') or '']
            if por == 'documento':
                fila += [f.get('documento') or '', f['desde']]
            datos.append(fila + [f['deudor'], f['acreedor']])
        datos.append(['TOTAL', ''] + [''] * (len(enc) - 4) + [tot['d'], tot['a']])
        return excel_response(f'Libro_3_{formato}_{periodo}', f'FORMATO {codigo}: {titulo.upper()} AL {periodo[4:]}/'
                                                              f'{periodo[:4]}', enc, datos)
    return render(request, 'contabilidad/libro_inventarios.html', {
        'formatos': FORMATOS, 'formato': formato, 'titulo': titulo, 'periodo': periodo, 'por': por,
        'prefijos': prefijos, 'filas': filas, 'tot': tot, 'mes': f'{periodo[:4]}-{periodo[4:]}'})
