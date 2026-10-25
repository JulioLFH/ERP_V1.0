"""Reglas del reparto: qué se puede despachar, armado de la hoja, entregas en ruta, cierre y efectividad."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import D0, HojaReparto, Parada

ACTIVAS = ('PROGRAMADA', 'EN_RUTA')


class ErrorReparto(Exception):
    pass


def _ocupadas():
    """Paradas que ya cuentan (programadas, en ruta o entregadas): sus guías y pedidos no se vuelven a programar.
    Lo no entregado vuelve a quedar disponible para otra hoja."""
    return Parada.objects.exclude(hoja__estado='ANULADA').exclude(estado='NO_ENTREGADO')


def guias_disponibles(fecha, dias=15):
    from logistica.models import GuiaRemision
    return (GuiaRemision.objects.filter(tipo='09', estado='EMITIDA', fecha_traslado__gte=fecha - timedelta(days=dias))
            .exclude(pk__in=_ocupadas().exclude(guia=None).values('guia_id'))
            .select_related('destinatario').order_by('fecha_traslado', 'id'))


def pedidos_disponibles(fecha, dias=30):
    """Pedidos aprobados (o ya facturados) que aún no salen a reparto."""
    from ventas.models import Cotizacion
    return (Cotizacion.objects.filter(tipo='PED', estado__in=['APROBADO', 'ATENDIDO'],
                                      fecha__gte=fecha - timedelta(days=dias))
            .exclude(pk__in=_ocupadas().exclude(pedido=None).values('pedido_id'))
            .select_related('tercero').order_by('fecha', 'id'))


def _siguiente_orden(hoja):
    return (hoja.paradas.aggregate(m=Max('orden'))['m'] or 0) + 1


def agregar(hoja, guias=(), pedidos=()):
    if hoja.estado not in ACTIVAS:
        raise ErrorReparto('La hoja ya está cerrada o anulada.')
    orden = _siguiente_orden(hoja)
    nuevas = []
    with transaction.atomic():
        for g in guias:
            peso = g.peso_bruto * (1000 if g.unidad_peso == 'TNE' else 1)
            nuevas.append(Parada(hoja=hoja, orden=orden, guia=g, cliente=g.destinatario,
                                 direccion=g.llegada_direccion[:250], contacto=g.destinatario.telefono[:120],
                                 bultos=g.numero_bultos, peso=peso))
            orden += 1
        for p in pedidos:
            contado = 'contado' in (p.condicion_pago or '').lower()
            nuevas.append(Parada(hoja=hoja, orden=orden, pedido=p, cliente=p.tercero,
                                 direccion=(p.tercero.direccion or 'Sin dirección registrada')[:250],
                                 contacto=p.tercero.telefono[:120], cobrar=p.total if contado else D0))
            orden += 1
        Parada.objects.bulk_create(nuevas)
    return len(nuevas)


def reordenar(hoja, ids):
    """Nuevo orden de visita: lista de ids de paradas de la hoja."""
    validas = set(hoja.paradas.values_list('pk', flat=True))
    for n, pk in enumerate([i for i in ids if i in validas], 1):
        Parada.objects.filter(pk=pk).update(orden=n)


def quitar(parada):
    if parada.estado != 'PENDIENTE' or parada.hoja.estado not in ACTIVAS:
        raise ErrorReparto('Solo se quitan paradas pendientes de una hoja activa.')
    parada.delete()


def iniciar(hoja):
    if hoja.estado != 'PROGRAMADA':
        raise ErrorReparto('La hoja no está programada.')
    if not hoja.paradas.exists():
        raise ErrorReparto('Agregue al menos una parada.')
    hoja.estado, hoja.salida = 'EN_RUTA', timezone.now()
    hoja.save(update_fields=['estado', 'salida'])


def _decimal(texto):
    try:
        return Decimal(str(texto or '0').replace(',', '.'))
    except InvalidOperation:
        raise ErrorReparto('Monto no válido.') from None


def registrar_entrega(parada, estado, motivo='', recibido_por='', cobrado='0', observacion='', latitud=None,
                      longitud=None):
    if parada.hoja.estado not in ACTIVAS:
        raise ErrorReparto('La hoja ya está cerrada o anulada.')
    if estado not in ('ENTREGADO', 'PARCIAL', 'NO_ENTREGADO'):
        raise ErrorReparto('Elija el resultado de la entrega.')
    if estado == 'NO_ENTREGADO' and (not motivo or motivo not in dict(Parada.MOTIVOS)):
        raise ErrorReparto('Indique el motivo por el que no se entregó.')
    if estado == 'PARCIAL' and not (observacion or '').strip():
        raise ErrorReparto('Anote qué se entregó y qué no.')
    if estado != 'NO_ENTREGADO' and not (recibido_por or '').strip():
        raise ErrorReparto('Anote quién recibió.')
    monto = _decimal(cobrado)
    if monto < 0:
        raise ErrorReparto('Monto no válido.')
    with transaction.atomic():
        if parada.hoja.estado == 'PROGRAMADA':  # la primera entrega marca la salida a ruta
            iniciar(parada.hoja)
        parada.estado, parada.motivo = estado, motivo if estado == 'NO_ENTREGADO' else ''
        parada.recibido_por = (recibido_por or '').strip()[:120]
        parada.observacion = (observacion or '').strip()[:250]
        parada.cobrado, parada.entregado_en = monto, timezone.now()
        try:  # ubicación del celular al registrar (si el conductor la permitió)
            parada.latitud = round(Decimal(str(latitud)), 6) if latitud else None
            parada.longitud = round(Decimal(str(longitud)), 6) if longitud else None
        except InvalidOperation:
            parada.latitud = parada.longitud = None
        parada.save()


def cerrar(hoja):
    """Cierra la hoja al volver: lo que quedó pendiente pasa a «No entregado» (vuelve a estar disponible)."""
    if hoja.estado not in ACTIVAS:
        raise ErrorReparto('La hoja ya está cerrada o anulada.')
    with transaction.atomic():
        hoja.paradas.filter(estado='PENDIENTE').update(estado='NO_ENTREGADO', motivo='TIEMPO',
                                                       entregado_en=timezone.now())
        hoja.estado, hoja.retorno = 'CERRADA', timezone.now()
        hoja.save(update_fields=['estado', 'retorno'])


def anular(hoja):
    if hoja.estado not in ACTIVAS:
        raise ErrorReparto('La hoja ya está cerrada o anulada.')
    if hoja.paradas.exclude(estado='PENDIENTE').exists():
        raise ErrorReparto('La hoja ya tiene entregas registradas: ciérrela en lugar de anularla.')
    hoja.estado = 'ANULADA'
    hoja.save(update_fields=['estado'])


def hojas_del_conductor(user, hoy=None):
    hoy = hoy or timezone.localdate()
    return (HojaReparto.objects.filter(estado__in=ACTIVAS, fecha__lte=hoy, conductor__trabajador__usuario=user)
            .select_related('vehiculo', 'conductor'))


def resumen(hoja):
    c = Counter(hoja.paradas.values_list('estado', flat=True))
    total = sum(c.values())
    hechas = c['ENTREGADO'] + c['PARCIAL']
    return {'total': total, 'entregadas': c['ENTREGADO'], 'parciales': c['PARCIAL'], 'no': c['NO_ENTREGADO'],
            'pendientes': c['PENDIENTE'], 'efectividad': round(hechas * 100 / total) if total else 0}


def reporte(desde, hasta):
    """Efectividad de entregas del rango por conductor y motivos de no entrega."""
    paradas = (Parada.objects.filter(hoja__fecha__range=(desde, hasta)).exclude(hoja__estado='ANULADA')
               .select_related('hoja__conductor', 'cliente'))
    por_conductor, motivos, clientes = {}, Counter(), Counter()
    for p in paradas:
        nombre = str(p.hoja.conductor) if p.hoja.conductor_id else 'Sin conductor'
        fila = por_conductor.setdefault(nombre, Counter())
        fila['total'] += 1
        fila[p.estado] += 1
        fila['cobrado'] += p.cobrado
        if p.estado == 'NO_ENTREGADO':
            motivos[p.get_motivo_display()] += 1
            clientes[p.cliente.nombre] += 1
    filas = []
    for nombre, f in sorted(por_conductor.items()):
        hechas = f['ENTREGADO'] + f['PARCIAL']
        filas.append({'conductor': nombre, 'total': f['total'], 'entregadas': f['ENTREGADO'], 'parciales': f['PARCIAL'],
                      'no': f['NO_ENTREGADO'], 'pendientes': f['PENDIENTE'], 'cobrado': f['cobrado'] or D0,
                      'efectividad': round(hechas * 100 / f['total']) if f['total'] else 0})
    total = sum(f['total'] for f in filas)
    hechas = sum(f['entregadas'] + f['parciales'] for f in filas)
    return {'filas': filas, 'motivos': motivos.most_common(), 'clientes': clientes.most_common(10),
            'total': total, 'efectividad': round(hechas * 100 / total) if total else 0,
            'hojas': HojaReparto.objects.filter(fecha__range=(desde, hasta)).exclude(estado='ANULADA').count()}
