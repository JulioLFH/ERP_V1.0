"""Programación detallada de planta (capacidad finita), reporte de planta desde tablets y cambios de ingeniería."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import Serie

from .models import AvanceOrden, CambioIngenieria, ListaMateriales, OrdenProduccion, VersionFabricacion

D0 = Decimal('0')
HORIZONTE = 366  # días que se buscan como máximo para ubicar una operación


class ErrorAvanzado(Exception):
    pass


# ---------------------------------------------------------------- programación detallada (APS)
def programar(desde=None):
    """Programa las operaciones de las órdenes confirmadas y en proceso en sus puestos, con capacidad finita.

    - Se atienden por prioridad (1 = más urgente), fecha planificada y número.
    - Cada operación espera a la anterior de su orden; cada puesto trabaja solo sus horas del día (turnos × horas ×
      eficiencia) en sus días laborables y una operación a la vez.
    - Las horas ya reportadas desde la planta se descuentan de lo que falta.
    Devuelve [{o, ops, fin, limite, atrasada, sin_capacidad}] y {centro_id: [operaciones]}."""
    hoy = desde or timezone.localdate()
    usado = defaultdict(lambda: defaultdict(lambda: D0))  # puesto -> fecha -> horas ya ocupadas
    reportado = dict(AvanceOrden.objects.filter(hora__isnull=False).values_list('hora').annotate(s=Sum('horas')))
    ordenes = (OrdenProduccion.objects.filter(estado__in=['CONFIRMADA', 'EN_PROCESO'])
               .select_related('producto', 'version').prefetch_related('horas__centro')
               .order_by('prioridad', 'fecha', 'id'))
    salida, por_centro = [], defaultdict(list)
    for o in ordenes:
        cursor = (max(hoy, o.fecha), D0)  # no antes de hoy ni de su fecha planificada
        ops = []
        for h in sorted(o.horas.all(), key=lambda h: (h.secuencia or 0, h.id)):
            pendiente = max(h.horas_plan - (reportado.get(h.pk) or D0), D0)
            ubicado = _ubicar(h.centro, usado[h.centro_id], cursor, pendiente)
            if ubicado is None:
                ops.append({'h': h, 'centro': h.centro, 'pendiente': pendiente, 'sin_capacidad': True})
                continue
            inicio, fin, tramos = ubicado
            cursor = fin
            fila = {'h': h, 'centro': h.centro, 'orden': o, 'pendiente': pendiente, 'inicio': inicio[0],
                    'fin': fin[0], 'tramos': tramos}
            ops.append(fila)
            if tramos:
                por_centro[h.centro_id].append(fila)
        fin_orden = max((op['fin'] for op in ops if 'fin' in op), default=None)
        limite = o.fecha + timedelta(days=max((o.version.dias_fabricacion if o.version_id else 1) - 1, 0))
        salida.append({'o': o, 'ops': ops, 'fin': fin_orden, 'limite': limite,
                       'atrasada': bool(fin_orden and fin_orden > limite),
                       'sin_capacidad': any(op.get('sin_capacidad') for op in ops)})
    return salida, por_centro


def _ubicar(centro, usado, cursor, horas):
    """Ubica `horas` en el puesto desde el cursor (fecha, hora del día ya ocupada por la operación anterior).
    Devuelve (inicio, fin, tramos) con inicio/fin = (fecha, hora del día) y tramos = [(fecha, horas)]; None si el
    puesto no tiene capacidad."""
    dia, desde_h = cursor
    if horas <= 0:  # operación ya reportada completa: no ocupa el puesto
        return cursor, cursor, []
    capacidad = centro.capacidad_dia
    if capacidad <= 0 or not centro.dias_laborables:
        return None
    tramos, inicio = [], None
    for _ in range(HORIZONTE):
        if centro.laborable(dia):
            libre_desde = max(usado[dia], desde_h)
            disponible = capacidad - libre_desde
            if disponible > 0:
                toma = min(disponible, horas)
                inicio = inicio or (dia, libre_desde)
                usado[dia] = libre_desde + toma
                tramos.append((dia, toma))
                horas -= toma
                if horas <= 0:
                    return inicio, (dia, libre_desde + toma), tramos
        dia, desde_h = dia + timedelta(days=1), D0
    return None


def diagrama(por_centro, desde, dias=21):
    """Cuadro de Gantt por puesto: [{centro, celdas: [[(orden, horas)] por día]}] y la lista de días."""
    fechas = [desde + timedelta(days=i) for i in range(dias)]
    indice = {f: i for i, f in enumerate(fechas)}
    filas = []
    for ops in por_centro.values():
        centro = ops[0]['centro']
        celdas = [[] for _ in fechas]
        for op in ops:
            for fecha, horas in op['tramos']:
                if fecha in indice:
                    celdas[indice[fecha]].append((op['orden'], horas))
        filas.append({'centro': centro, 'celdas': [(f, c, sum((h for _, h in c), D0), centro.laborable(f))
                                                   for f, c in zip(fechas, celdas)]})
    filas.sort(key=lambda f: f['centro'].codigo)
    return filas, fechas


# ---------------------------------------------------------------- reporte de planta (tablets)
@transaction.atomic
def registrar_avance(orden, hora, buena, merma, horas, operario, nota, usuario):
    """Registra lo producido, la merma y las horas de una operación. La orden confirmada pasa a en proceso y las
    horas reales de la operación se actualizan con la suma reportada (así llegan al costeo al terminar)."""
    from . import servicios
    if orden.estado not in ('CONFIRMADA', 'EN_PROCESO'):
        raise ErrorAvanzado('La orden no está en producción.')
    buena, merma, horas = buena or D0, merma or D0, horas or D0
    if min(buena, merma, horas) < 0:
        raise ErrorAvanzado('Las cantidades y horas no pueden ser negativas.')
    if not (buena or merma or horas):
        raise ErrorAvanzado('Indique la cantidad producida, la merma o las horas.')
    if hora is not None and hora.orden_id != orden.pk:
        raise ErrorAvanzado('La operación no es de esta orden.')
    if orden.estado == 'CONFIRMADA':
        servicios.iniciar(orden)
    avance = AvanceOrden.objects.create(orden=orden, hora=hora, cantidad_buena=buena, cantidad_merma=merma,
                                        horas=horas, operario=(operario or '')[:80], nota=(nota or '')[:200],
                                        usuario=usuario)
    if hora is not None and horas:
        hora.horas_real = hora.avances.aggregate(s=Sum('horas'))['s'] or D0
        hora.save(update_fields=['horas_real'])
    return avance


def resumen_avance(orden):
    """Producido = lo bueno reportado en la última operación (o sin operación si la receta no tiene horas)."""
    avances = list(orden.avances.all())
    if not avances:
        return None
    ultima = orden.horas.order_by('-secuencia', '-id').first()
    en_ultima = [a for a in avances if ultima and a.hora_id == ultima.pk]
    base = en_ultima or [a for a in avances if a.hora_id is None]
    producido = sum((a.cantidad_buena for a in base), D0)
    merma = sum((a.cantidad_merma for a in avances), D0)
    return {'avances': avances, 'producido': producido, 'merma': merma,
            'horas': sum((a.horas for a in avances), D0),
            'pct': (producido / orden.cantidad * 100).quantize(Decimal('0.1')) if orden.cantidad else None}


# ---------------------------------------------------------------- cambios de ingeniería (ECO)
def copiar_lista(lista, observaciones):
    """Copia la receta como nuevo borrador con el siguiente código libre (V2, V3…)."""
    n = ListaMateriales.objects.filter(producto=lista.producto).count() + 1
    codigo = f'V{n}'
    while ListaMateriales.objects.filter(producto=lista.producto, codigo=codigo).exists():
        n += 1
        codigo = f'V{n}'
    nueva = ListaMateriales.objects.create(
        producto=lista.producto, codigo=codigo, cantidad_base=lista.cantidad_base, estado='BORRADOR',
        lote_min=lista.lote_min, lote_max=lista.lote_max, observaciones=observaciones[:500])
    for c in lista.componentes.all():
        nueva.componentes.create(producto_id=c.producto_id, cantidad=c.cantidad, merma=c.merma,
                                 operacion=c.operacion, almacen_id=c.almacen_id)
    for o in lista.operaciones.all():
        nueva.operaciones.create(centro_id=o.centro_id, descripcion=o.descripcion, horas=o.horas)
    return nueva


@transaction.atomic
def crear_cambio(cambio, usuario):
    if cambio.lista_actual.estado != 'APROBADA':
        raise ErrorAvanzado('Solo se cambian recetas aprobadas.')
    if CambioIngenieria.objects.filter(lista_actual=cambio.lista_actual,
                                       estado__in=('BORRADOR', 'POR_APROBAR')).exists():
        raise ErrorAvanzado('Esa receta ya tiene un cambio de ingeniería en curso.')
    serie, n = Serie.siguiente('ECO', 'ECO')
    cambio.numero, cambio.solicitado_por = f'{serie}-{n}', usuario
    cambio.lista_nueva = copiar_lista(cambio.lista_actual, f'Propuesta del cambio {serie}-{n}')
    cambio.save()
    return cambio


def diferencias(cambio):
    """Insumos agregados, quitados y modificados entre la receta actual y la propuesta (por unidad de lote)."""
    actual = {c.producto_id: c for c in cambio.lista_actual.componentes.select_related('producto')}
    nueva = {c.producto_id: c for c in cambio.lista_nueva.componentes.select_related('producto')} \
        if cambio.lista_nueva_id else {}
    filas = []
    for pid in list(actual) + [p for p in nueva if p not in actual]:
        a, n = actual.get(pid), nueva.get(pid)
        if a and n and (a.cantidad, a.merma) == (n.cantidad, n.merma):
            continue
        filas.append({'producto': (a or n).producto, 'antes': a.cantidad if a else None,
                      'despues': n.cantidad if n else None, 'merma_antes': a.merma if a else None,
                      'merma_despues': n.merma if n else None,
                      'tipo': 'Agregado' if not a else 'Quitado' if not n else 'Modificado'})
    base = (cambio.lista_actual.cantidad_base, cambio.lista_nueva.cantidad_base if cambio.lista_nueva_id else None)
    return filas, base


def enviar(cambio):
    if cambio.estado != 'BORRADOR':
        raise ErrorAvanzado('El cambio ya fue enviado.')
    filas, (base_antes, base_despues) = diferencias(cambio)
    if not filas and base_antes == base_despues:
        raise ErrorAvanzado('La receta propuesta es igual a la actual: edítela antes de enviarla a aprobación.')
    if not cambio.lista_nueva.componentes.exists():
        raise ErrorAvanzado('La receta propuesta no tiene insumos.')
    cambio.estado = 'POR_APROBAR'
    cambio.save(update_fields=['estado'])


def _hay_otro_usuario():
    return get_user_model().objects.filter(is_active=True).count() > 1


@transaction.atomic
def aprobar(cambio, usuario, comentario=''):
    """Aplica el cambio: la nueva receta queda aprobada desde la fecha efectiva, la actual vence el día anterior y
    cada versión de fabricación vigente se duplica con la nueva receta (las órdenes pasadas conservan la suya).
    Lo aprueba otra persona (segregación de funciones), salvo que la empresa tenga un solo usuario."""
    from core.auditoria import registrar
    if cambio.estado != 'POR_APROBAR':
        raise ErrorAvanzado('Solo se aprueban cambios enviados a aprobación.')
    if usuario.pk == cambio.solicitado_por_id and _hay_otro_usuario():
        raise ErrorAvanzado('Quien solicita el cambio no puede aprobarlo: debe aprobarlo otra persona.')
    actual, nueva, efectiva = cambio.lista_actual, cambio.lista_nueva, cambio.fecha_efectiva
    nueva.estado, nueva.vigente_desde = 'APROBADA', efectiva
    nueva.save()
    vence = efectiva - timedelta(days=1)
    if vence < actual.vigente_desde:
        actual.estado = 'OBSOLETA'
    else:
        actual.vigente_hasta = vence
    actual.save()
    for v in VersionFabricacion.objects.filter(lista=actual, activa=True):
        codigo, n = nueva.codigo[:10], 1
        while VersionFabricacion.objects.filter(producto=v.producto, codigo=codigo).exists():
            n += 1
            codigo = f'{nueva.codigo[:7]}-{n}'
        VersionFabricacion.objects.create(
            producto=v.producto, codigo=codigo, descripcion=f'{cambio.numero}: {v.descripcion}'[:120], lista=nueva,
            hoja=v.hoja, lote_min=v.lote_min, lote_max=v.lote_max, lote_costeo=v.lote_costeo, vigente_desde=efectiva,
            vigente_hasta=v.vigente_hasta, dias_fabricacion=v.dias_fabricacion)
        if vence < v.vigente_desde:
            v.activa = False
        else:
            v.vigente_hasta = vence
        v.save()
    cambio.estado, cambio.aprobado_por, cambio.aprobado_en = 'APROBADO', usuario, timezone.now()
    cambio.comentario = (comentario or '')[:250]
    cambio.save()
    registrar('MODIFICAR', actual, {'Cambio de ingeniería': [actual.codigo, f'{nueva.codigo} desde {efectiva}']})
    return cambio


@transaction.atomic
def rechazar(cambio, usuario, comentario):
    if cambio.estado not in ('BORRADOR', 'POR_APROBAR'):
        raise ErrorAvanzado('El cambio ya fue resuelto.')
    if len((comentario or '').strip()) < 5:
        raise ErrorAvanzado('Indique el motivo del rechazo.')
    cambio.estado, cambio.aprobado_por, cambio.aprobado_en = 'RECHAZADO', usuario, timezone.now()
    cambio.comentario = comentario[:250]
    cambio.save()
    if cambio.lista_nueva_id and cambio.lista_nueva.estado == 'BORRADOR':
        cambio.lista_nueva.estado = 'OBSOLETA'
        cambio.lista_nueva.save()
    return cambio
