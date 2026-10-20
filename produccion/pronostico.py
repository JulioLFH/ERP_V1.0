"""Pronóstico de demanda por producto con la historia de ventas mensual.

Métodos: promedio móvil de 3 meses, suavizamiento exponencial simple, Holt (nivel y tendencia) y estacional (mismo
mes del año anterior ajustado por la tendencia de los últimos 3 meses, si hay 24 meses de historia). Para cada
producto se elige el método con menor error medio absoluto al pronosticar, uno a uno, los últimos 3 meses conocidos.
El resultado se puede pasar al plan de demanda, que el MRP suma a los pedidos de venta."""
from datetime import date
from decimal import Decimal

from django.db.models import Case, F, Sum, When
from django.db.models.functions import TruncMonth
from django.utils import timezone

D0 = Decimal('0')
METODOS = {'PM3': 'Promedio móvil 3 meses', 'SES': 'Suavizamiento exponencial', 'HOLT': 'Holt (con tendencia)',
           'ESTACIONAL': 'Estacional (año anterior)'}


def _mes(d, delta):
    total = d.year * 12 + d.month - 1 + delta
    return date(total // 12, total % 12 + 1, 1)


def historia(productos, meses=24, hoy=None):
    """{producto_id: [cantidad vendida por mes]} de los `meses` meses completos anteriores al actual."""
    from ventas.models import VentaItem
    hoy = hoy or timezone.localdate()
    inicio = _mes(date(hoy.year, hoy.month, 1), -meses)
    fin = date(hoy.year, hoy.month, 1)
    lista_meses = [_mes(inicio, i) for i in range(meses)]
    datos = {p: [0.0] * meses for p in productos}
    filas = (VentaItem.objects.filter(producto_id__in=productos, documento__fecha_emision__gte=inicio,
                                      documento__fecha_emision__lt=fin)
             .exclude(documento__estado='ANULADO').exclude(documento__tipo_comprobante='08')
             .annotate(mes=TruncMonth('documento__fecha_emision'),
                       q=Case(When(documento__tipo_comprobante='07', then=-F('cantidad')), default=F('cantidad')))
             .values('producto_id', 'mes').annotate(t=Sum('q')))
    indice = {m: i for i, m in enumerate(lista_meses)}
    for f in filas:
        m = f['mes'].date() if hasattr(f['mes'], 'date') else f['mes']
        i = indice.get(date(m.year, m.month, 1))
        if i is not None:
            datos[f['producto_id']][i] += float(f['t'] or 0)
    return lista_meses, {p: [max(v, 0.0) for v in serie] for p, serie in datos.items()}


def _pm3(serie, h):
    base = serie[-3:] if len(serie) >= 3 else serie
    valor = sum(base) / len(base) if base else 0.0
    return [valor] * h


def _ses(serie, h, alfa=0.3):
    if not serie:
        return [0.0] * h
    nivel = serie[0]
    for x in serie[1:]:
        nivel = alfa * x + (1 - alfa) * nivel
    return [nivel] * h


def _holt(serie, h, alfa=0.3, beta=0.1):
    if len(serie) < 2:
        return _ses(serie, h)
    nivel, tendencia = serie[0], serie[1] - serie[0]
    for x in serie[1:]:
        anterior = nivel
        nivel = alfa * x + (1 - alfa) * (nivel + tendencia)
        tendencia = beta * (nivel - anterior) + (1 - beta) * tendencia
    return [max(nivel + tendencia * (k + 1), 0.0) for k in range(h)]


def _estacional(serie, h):
    if len(serie) < 15:
        return None
    reciente, anio_antes = sum(serie[-3:]), sum(serie[-15:-12])
    factor = reciente / anio_antes if anio_antes else 1.0
    return [max(serie[len(serie) - 12 + k % 12] * factor, 0.0) for k in range(h)]


FUNCIONES = {'PM3': _pm3, 'SES': _ses, 'HOLT': _holt, 'ESTACIONAL': _estacional}


def _error(metodo, serie, pruebas=3):
    """Error medio absoluto al pronosticar los últimos `pruebas` meses con la historia anterior a cada uno."""
    errores = []
    for k in range(pruebas, 0, -1):
        previa = serie[:-k]
        if len(previa) < 3:
            return None
        p = FUNCIONES[metodo](previa, 1)
        if p is None:
            return None
        errores.append(abs(p[0] - serie[-k]))
    return sum(errores) / len(errores) if errores else None


def pronosticar(serie, h=3, metodo=None):
    """(método, error medio absoluto, [pronóstico por mes]). Con `metodo` se usa ese; si no, el de menor error."""
    candidatos = [metodo] if metodo else list(FUNCIONES)
    mejor = None
    for m in candidatos:
        e = _error(m, serie)
        if e is None and metodo is None:
            continue
        if mejor is None or (e is not None and e < mejor[1]):
            mejor = (m, e if e is not None else 0.0)
    if mejor is None:
        mejor = ('PM3', 0.0)
    valores = FUNCIONES[mejor[0]](serie, h) or _pm3(serie, h)
    return mejor[0], mejor[1], [round(v, 2) for v in valores]


def tabla(productos, h=3, meses=24, hoy=None):
    """Filas de la pantalla: producto, historia reciente, método, error % y pronóstico de los próximos h meses."""
    from core.models import Producto
    hoy = hoy or timezone.localdate()
    lista_meses, datos = historia([p.pk for p in productos], meses, hoy)
    futuros = [_mes(date(hoy.year, hoy.month, 1), k) for k in range(h)]
    filas = []
    for p in productos:
        serie = datos[p.pk]
        if not any(serie):
            continue
        metodo, error, valores = pronosticar(serie, h)
        promedio = sum(serie[-3:]) / 3 or 1
        filas.append({'producto': p, 'historia': [round(x, 2) for x in serie[-6:]], 'metodo': metodo,
                      'metodo_nombre': METODOS[metodo], 'error_pct': round(error / promedio * 100, 1),
                      'pronostico': valores, 'total': round(sum(valores), 2)})
    return {'meses_historia': lista_meses[-6:], 'meses_futuros': futuros, 'filas': filas}


def pasar_al_plan(producto, meses, valores, metodo, usuario=None):
    """Reemplaza el pronóstico anterior del producto en esos meses por el nuevo (día 15 de cada mes)."""
    from .models import PlanDemanda
    for mes, valor in zip(meses, valores):
        PlanDemanda.objects.filter(producto=producto, fecha__year=mes.year, fecha__month=mes.month,
                                   nota__startswith='Pronóstico').delete()
        if valor and valor > 0:
            PlanDemanda.objects.create(producto=producto, cantidad=Decimal(str(valor)).quantize(Decimal('0.01')),
                                       fecha=date(mes.year, mes.month, 15), nota=f'Pronóstico ({METODOS[metodo]})',
                                       creado_por=usuario if usuario and usuario.is_authenticated else None)
