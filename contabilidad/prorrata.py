"""Prorrata del crédito fiscal (Reglamento de la Ley del IGV, art. 6 num. 6.2): el IGV de las compras destinadas a
operaciones gravadas y no gravadas (comunes) da crédito en la proporción de las operaciones gravadas y exportaciones
sobre el total de operaciones de los últimos 12 meses, incluido el del registro. El IGV que no da crédito va al costo
o gasto de la compra. Si en esos 12 meses no hubo operaciones no gravadas, el coeficiente es 1."""
from decimal import Decimal

from django.db.models import Sum

from core.models import r2

D0 = Decimal('0')
_CACHE = {}


def limpiar():
    _CACHE.clear()


def _meses(periodo, n=12):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    salida = []
    for _ in range(n):
        salida.append(f'{anio}{mes:02d}')
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
    return salida


def operaciones(periodo):
    """(gravadas y exportaciones, no gravadas) del mes en soles, con las notas de crédito restando. Sin el terreno
    de la primera venta de inmuebles si el parámetro IGV_PRORRATA_EXCLUYE_TERRENO lo indica."""
    from core import parametros
    from ventas.models import Venta, VentaItem
    qs = Venta.objects.filter(periodo=periodo, estado='REGISTRADO', es_saldo_inicial=False, es_historico=False)
    gravadas = no_gravadas = D0
    for v in qs.only('tipo_comprobante', 'base_pen', 'nograv_pen', 'exonerado', 'inafecto', 'moneda',
                     'tipo_cambio'):
        signo = -1 if v.tipo_comprobante == '07' else 1
        exo_inaf = r2((v.exonerado + v.inafecto) * v.tc_efectivo)
        gravadas += signo * (v.base_pen + max(v.nograv_pen - exo_inaf, D0))  # exportación con las gravadas
        no_gravadas += signo * exo_inaf
    from datetime import date
    fecha = date(int(periodo[:4]), int(periodo[4:]), 1)
    if parametros.valor('IGV_PRORRATA_EXCLUYE_TERRENO', fecha):
        terreno = (VentaItem.objects.filter(documento__in=qs, producto__codigo='ANTINMUEBLE', afectacion='INAFECTA')
                   .values_list('documento__tipo_comprobante', 'subtotal', 'documento__moneda',
                                'documento__tipo_cambio'))
        for tipo, subtotal, moneda, tc in terreno:
            no_gravadas -= (-1 if tipo == '07' else 1) * r2(subtotal * (tc if moneda == 'USD' else 1))
    return gravadas, max(no_gravadas, D0)


def coeficiente(periodo):
    """Proporción (4 decimales) de operaciones gravadas y exportaciones de los 12 meses que terminan en el periodo."""
    from erp.empresas import actual
    clave = (actual(), periodo)
    if clave not in _CACHE:
        gravadas = no_gravadas = D0
        for p in _meses(periodo):
            g, n = operaciones(p)
            gravadas, no_gravadas = gravadas + g, no_gravadas + n
        total = gravadas + no_gravadas
        _CACHE[clave] = (Decimal('1') if not no_gravadas or total <= 0 else
                         max(min((gravadas / total).quantize(Decimal('0.0001')), Decimal('1')), D0))
    return _CACHE[clave]


def partir_igv(compra):
    """(IGV con crédito fiscal, IGV al costo) de la compra en soles según su destino."""
    destino = getattr(compra, 'destino_igv', 'GRAVADA')
    if destino == 'NO_GRAVADA':
        return D0, compra.igv_pen
    if destino == 'COMUN':
        credito = r2(compra.igv_pen * coeficiente(compra.periodo))
        return credito, compra.igv_pen - credito
    return compra.igv_pen, D0


def reporte(anio):
    """Por mes: operaciones gravadas y no gravadas del mes y de 12 meses, coeficiente y el IGV de las compras según
    su destino (crédito usado y lo que fue al costo)."""
    from compras.models import Compra
    filas = []
    for mes in range(1, 13):
        periodo = f'{anio}{mes:02d}'
        g, n = operaciones(periodo)
        compras = Compra.objects.filter(periodo=periodo, estado='REGISTRADO', es_saldo_inicial=False,
                                        es_historico=False).exclude(tipo_comprobante='91')
        igv = {d: D0 for d, _ in Compra.DESTINOS_IGV}
        for destino, tipo, monto in compras.values_list('destino_igv', 'tipo_comprobante').annotate(s=Sum('igv_pen')):
            igv[destino] += (-1 if tipo == '07' else 1) * (monto or D0)
        coef = coeficiente(periodo)
        comun_credito = r2(igv['COMUN'] * coef)
        filas.append({'periodo': periodo, 'gravadas': g, 'no_gravadas': n, 'coeficiente': coef,
                      'igv_gravada': igv['GRAVADA'], 'igv_comun': igv['COMUN'], 'igv_no_gravada': igv['NO_GRAVADA'],
                      'credito': igv['GRAVADA'] + comun_credito,
                      'al_costo': igv['NO_GRAVADA'] + igv['COMUN'] - comun_credito})
    return filas
