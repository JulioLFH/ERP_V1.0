"""Calidad: valores de los indicadores (automáticos con los datos de Ceiba o ingresados con evidencia), tablero por
perspectiva con semáforo, acciones vencidas y cierre de no conformidades."""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Accion, Indicador, Medicion, NoConformidad

D0 = Decimal('0')


class ErrorCalidad(Exception):
    pass


def _rango(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    return date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])


def valor_automatico(fuente, periodo):
    """Valor del indicador automático en el mes (None si no hay datos)."""
    desde, hasta = _rango(periodo)
    if fuente == 'NC_ABIERTAS':
        return Decimal(NoConformidad.objects.filter(vigente=True, fecha__lte=hasta).exclude(
            estado='CERRADA', fecha_cierre__lte=hasta).count())
    if fuente == 'ACCIONES_VENCIDAS':
        return Decimal(Accion.objects.filter(plazo__lt=min(hasta, timezone.localdate()),
                                             estado__in=('PENDIENTE', 'EN_CURSO')).count())
    if fuente == 'DIAS_COBRO':
        from finanzas.models import Movimiento
        suma = peso = D0
        for m in Movimiento.objects.filter(tipo='INGRESO', concepto='COBRANZA', fecha__range=[desde, hasta],
                                           venta__isnull=False).select_related('venta'):
            suma += m.monto_doc_pen * (m.fecha - m.venta.fecha_emision).days
            peso += m.monto_doc_pen
        return (suma / peso).quantize(Decimal('0.1')) if peso else None
    if fuente in ('SST_IF', 'SST_IG', 'SST_IA'):
        from sst.servicios import indice_mes
        return indice_mes(fuente, periodo)
    if fuente == 'SST_EMO_VENCIDOS':
        from sst.models import ExamenMedico
        ultimos = {}
        for e in ExamenMedico.objects.filter(vigente=True, fecha__lte=hasta).order_by('fecha'):
            ultimos[e.trabajador_id] = e
        return Decimal(sum(1 for e in ultimos.values() if e.vence and e.vence < hasta))
    if fuente == 'OEE':
        from produccion.reportes_produccion import Filtros, oee
        filas = [f['oee'] for f in oee(Filtros({'desde': desde.isoformat(), 'hasta': hasta.isoformat()}))['filas']
                 if f['oee'] is not None]
        return (sum(filas, D0) / len(filas)).quantize(Decimal('0.01')) if filas else None
    return None


def valor(indicador, periodo):
    if indicador.fuente == 'MANUAL':
        m = Medicion.objects.filter(indicador=indicador, periodo=periodo).first()
        return m.valor if m else None
    return valor_automatico(indicador.fuente, periodo)


def tablero(periodo):
    """Por perspectiva: cada indicador activo con su valor del mes, la meta y el semáforo."""
    salida = []
    for clave, nombre in Indicador.PERSPECTIVAS:
        filas = []
        for ind in Indicador.objects.filter(activo=True, perspectiva=clave).select_related('area', 'responsable'):
            v = valor(ind, periodo)
            filas.append({'indicador': ind, 'valor': v, 'semaforo': ind.semaforo(v)})
        if filas:
            salida.append((nombre, filas))
    return salida


def serie(indicador, anio):
    """Valor por mes del año (para el detalle del indicador)."""
    hoy = timezone.localdate()
    filas = []
    for mes in range(1, 13):
        periodo = f'{anio}{mes:02d}'
        if date(anio, mes, 1) > hoy:
            break
        v = valor(indicador, periodo)
        filas.append({'periodo': periodo, 'valor': v, 'semaforo': indicador.semaforo(v)})
    return filas


def acciones_vencidas(modulo=None):
    qs = (Accion.objects.filter(estado__in=('PENDIENTE', 'EN_CURSO'), plazo__lt=timezone.localdate())
          .select_related('responsable', 'origen_tipo'))
    return [a for a in qs if modulo is None or a.modulo == modulo]


@transaction.atomic
def cerrar_nc(nc, eficaz):
    """Cierra la no conformidad cuando todas sus acciones están verificadas."""
    pendientes = nc.acciones.exclude(estado='VERIFICADA').count()
    if pendientes:
        raise ErrorCalidad(f'Faltan verificar {pendientes} acción(es) antes de cerrar la no conformidad.')
    if not nc.causa_raiz.strip():
        raise ErrorCalidad('Registre la causa raíz antes de cerrar.')
    nc.estado, nc.fecha_cierre, nc.eficaz = 'CERRADA', timezone.localdate(), eficaz
    NoConformidad.objects.filter(pk=nc.pk).update(estado='CERRADA', fecha_cierre=nc.fecha_cierre, eficaz=eficaz)
    return nc
