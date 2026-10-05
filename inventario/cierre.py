"""Cierre de kardex: bloquea los movimientos de almacén hasta la fecha de corte y guarda la valorización."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

D0 = Decimal('0')


class KardexCerrado(Exception):
    """Se intentó mover el almacén en una fecha con el kardex cerrado."""


def _fin_de_mes(anio, mes):
    siguiente = date(anio + (mes == 12), 1 if mes == 12 else mes + 1, 1)
    return siguiente - timedelta(days=1)


def ultimo_cierre():
    from .models import CierreKardex
    return CierreKardex.objects.filter(estado='CERRADO').order_by('-fecha_corte').first()


def fecha_cierre():
    """Fecha hasta la que el kardex está cerrado (None si no hay cierres)."""
    c = ultimo_cierre()
    return c.fecha_corte if c else None


def error_cierre(fecha):
    """Mensaje si la fecha cae en un periodo con el kardex cerrado; '' si se puede mover el almacén."""
    if isinstance(fecha, str):  # fechas que llegan del formulario como texto (AAAA-MM-DD)
        fecha = date.fromisoformat(fecha) if fecha else None
    corte = fecha_cierre()
    if corte and fecha and fecha <= corte:
        return (f'El kardex está cerrado hasta el {corte:%d/%m/%Y}: no se pueden registrar ni revertir movimientos '
                f'de almacén con fecha {fecha:%d/%m/%Y} (Inventario > Cierre de kardex).')
    return ''


def validar_fecha(fecha):
    mensaje = error_cierre(fecha)
    if mensaje:
        raise KardexCerrado(mensaje)


def saldos_al(corte):
    """[{producto, almacen, cantidad, costo, valor}] del inventario por almacén al corte (según el kardex)."""
    from core.models import Kardex, Producto, r2
    cantidades = defaultdict(lambda: D0)
    costo = {}
    for k in Kardex.objects.filter(fecha__lte=corte, producto__tipo='BIEN').order_by('fecha', 'id').only(
            'producto_id', 'almacen_id', 'tipo', 'cantidad', 'costo_promedio'):
        cantidades[(k.producto_id, k.almacen_id)] += k.cantidad if k.tipo == 'ENTRADA' else -k.cantidad
        costo[k.producto_id] = k.costo_promedio
    productos = Producto.objects.in_bulk({p for p, _ in cantidades})
    filas = []
    for (pid, aid), cantidad in sorted(cantidades.items(), key=lambda x: (productos[x[0][0]].codigo, x[0][1] or 0)):
        if cantidad == 0:
            continue
        c = costo.get(pid) or productos[pid].costo_promedio
        filas.append({'producto_id': pid, 'almacen_id': aid, 'cantidad': cantidad, 'costo': c, 'valor': r2(cantidad * c)})
    return filas


def periodo_siguiente():
    """Primer periodo que se puede cerrar: el mes siguiente al último cierre, o el del primer movimiento."""
    from core.models import Kardex
    corte = fecha_cierre()
    if corte:
        base = corte + timedelta(days=1)
    else:
        primero = Kardex.objects.order_by('fecha').values_list('fecha', flat=True).first()
        if not primero:
            return None
        base = primero
    return base.strftime('%Y%m')


def errores_cierre(periodo):
    from .models import Operacion
    try:
        anio, mes = int(periodo[:4]), int(periodo[4:])
        corte = _fin_de_mes(anio, mes)
    except (ValueError, TypeError):
        return ['Periodo inválido (AAAAMM).'], None
    errores = []
    actual = fecha_cierre()
    if actual and corte <= actual:
        errores.append(f'El kardex ya está cerrado hasta el {actual:%d/%m/%Y}.')
    if corte >= timezone.localdate():
        errores.append(f'El mes {periodo[4:]}/{periodo[:4]} aún no termina.')
    borradores = Operacion.objects.filter(estado='BORRADOR', fecha__lte=corte)
    if borradores.exists():
        errores.append(f'Hay {borradores.count()} operación(es) de inventario en borrador con fecha hasta el '
                       f'{corte:%d/%m/%Y}: confírmelas o elimínelas antes de cerrar.')
    return errores, corte


def cerrar(periodo, usuario, observaciones=''):
    """Cierra el kardex hasta el fin del periodo (los meses anteriores quedan cerrados también)."""
    from .models import CierreKardex, SaldoCierre
    errores, corte = errores_cierre(periodo)
    if errores:
        raise KardexCerrado(' '.join(errores))
    filas = saldos_al(corte)
    with transaction.atomic():
        cierre = CierreKardex.objects.create(
            periodo=periodo, fecha_corte=corte, observaciones=observaciones[:250],
            cerrado_por=usuario if usuario and usuario.is_authenticated else None,
            unidades=sum((f['cantidad'] for f in filas), D0), valor_total=sum((f['valor'] for f in filas), D0))
        SaldoCierre.objects.bulk_create([SaldoCierre(cierre=cierre, **f) for f in filas])
    return cierre


def reabrir(cierre, usuario, motivo):
    if cierre.estado != 'CERRADO' or cierre != ultimo_cierre():
        raise KardexCerrado('Solo se puede reabrir el último cierre vigente.')
    if len((motivo or '').strip()) < 5:
        raise KardexCerrado('Indique el motivo de la reapertura (mínimo 5 caracteres).')
    cierre.estado, cierre.motivo_reapertura = 'REABIERTO', motivo.strip()[:250]
    cierre.reabierto_por = usuario if usuario and usuario.is_authenticated else None
    cierre.reabierto_en = timezone.now()
    cierre.save()
