"""SST: entrega de EPP como salida de almacén, plazos de notificación, índices de frecuencia, gravedad y
accidentabilidad y vencimientos (EMO, SCTR, equipos de emergencia, reposición de EPP, monitoreos)."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import parametros

from .models import (EntregaEPP, EquipoEmergencia, Evento, ExamenMedico, HorasTrabajadas, LineaEPP, Monitoreo,
                     PermisoTrabajo, Sctr)

D0 = Decimal('0')
MILLON = Decimal('1000000')


class ErrorSST(Exception):
    pass


# ---------------------------------------------------------------- EPP
def tipo_epp():
    """Tipo de operación de inventario «Entrega de EPP al trabajador» (salida al gasto, como un consumo)."""
    from inventario.models import TipoOperacion
    t = TipoOperacion.objects.filter(codigo='EPP').first()
    if t is None:
        base = TipoOperacion.objects.filter(codigo__in=['CONS_REQ', 'CONS_INT']).order_by('codigo').first()
        t = TipoOperacion.objects.create(
            codigo='EPP', nombre='Entrega de EPP al trabajador', clase='SALIDA', origen='', requiere_costo=False,
            cuenta_contable=base.cuenta_contable if base else None, codigo_sunat='99', icono='bi-shield-check',
            orden=86)
    return t


@transaction.atomic
def entregar_epp(entrega, usuario):
    """Salida del almacén de los EPP de la entrega, con el centro de costo del trabajador (o el de la obra)."""
    from inventario import servicios as inv
    from inventario.models import Operacion
    if entrega.operacion_id:
        raise ErrorSST('Esta entrega ya salió del almacén.')
    lineas = list(entrega.lineas.select_related('producto'))
    if not lineas:
        raise ErrorSST('Agregue los EPP que se entregan.')
    t = entrega.trabajador
    centro = (entrega.obra.centro_costo if entrega.obra_id and entrega.obra.centro_costo_id else t.centro_costo)
    op = Operacion.objects.create(
        tipo=tipo_epp(), fecha=entrega.fecha, almacen_origen=entrega.almacen, centro_costo=centro,
        referencia=f'EPP {t.numero_doc}'[:60], creado_por=usuario if usuario and usuario.is_authenticated else None,
        glosa=f'Entrega de EPP a {t.nombre_completo} ({t.numero_doc})'[:500])
    for l in lineas:
        op.items.create(producto=l.producto, cantidad=l.cantidad)
    try:
        inv.confirmar(op, usuario)
    except inv.ErrorOperacion as exc:
        raise ErrorSST(str(exc)) from exc
    entrega.operacion = op
    EntregaEPP.objects.filter(pk=entrega.pk).update(operacion=op)
    return op


# ---------------------------------------------------------------- notificación de accidentes
def _habiles(desde, dias):
    fecha, n = desde, 0
    while n < dias:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            n += 1
    return fecha


def plazo_notificacion(evento):
    """Fecha límite para notificar a la autoridad o None si el evento no se notifica."""
    if evento.tipo in ('ACC_MORTAL', 'INC_PELIGROSO'):
        horas = int(parametros.valor('SST_NOTIF_MORTAL_HORAS', evento.fecha))
        return evento.fecha + timedelta(days=max((horas + 23) // 24, 0))
    if evento.tipo == 'ENFERMEDAD':
        return _habiles(evento.fecha, int(parametros.valor('SST_NOTIF_ENFERMEDAD_DIAS', evento.fecha)))
    if evento.tipo == 'ACC_INCAPACITANTE':  # hasta el último día del mes siguiente
        anio, mes = (evento.fecha.year + 1, 1) if evento.fecha.month == 12 else (evento.fecha.year,
                                                                                  evento.fecha.month + 1)
        return date(anio, mes, monthrange(anio, mes)[1])
    return None


def sin_notificar():
    hoy = timezone.localdate()
    return [e for e in Evento.objects.filter(vigente=True, notificado_en=None,
                                             tipo__in=('ACC_MORTAL', 'INC_PELIGROSO', 'ENFERMEDAD',
                                                       'ACC_INCAPACITANTE')).select_related('trabajador')
            if e.plazo_notificacion and e.plazo_notificacion <= hoy + timedelta(days=2)]


# ---------------------------------------------------------------- índices
def horas_hombre(periodo, obra=None):
    """Horas-hombre del mes: las registradas o, si no hay, trabajadores con vínculo en el mes × horas por mes."""
    qs = HorasTrabajadas.objects.filter(periodo=periodo)
    qs = qs.filter(obra=obra) if obra is not None else qs.filter(obra=None)
    registradas = qs.aggregate(s=Sum('horas'))['s']
    if registradas:
        return registradas
    from planillas.models import Trabajador
    anio, mes = int(periodo[:4]), int(periodo[4:])
    desde, hasta = date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])
    trabajadores = Trabajador.objects.filter(fecha_ingreso__lte=hasta).exclude(fecha_cese__lt=desde)
    if obra is not None:
        trabajadores = trabajadores.filter(centro_costo_id=obra.centro_costo_id) if obra.centro_costo_id else \
            trabajadores.none()
    return trabajadores.count() * parametros.valor('SST_HORAS_MES', desde)


def indices(anio, obra=None):
    """Por mes y acumulado del año: accidentes incapacitantes (y mortales), días perdidos, horas-hombre e índices
    IF = accidentes × 1 000 000 / HHT, IG = días perdidos × 1 000 000 / HHT, IA = IF × IG / 1 000."""
    filas = []
    acum = {'accidentes': 0, 'dias': 0, 'hht': D0, 'incidentes': 0}
    for mes in range(1, 13):
        periodo = f'{anio}{mes:02d}'
        desde, hasta = date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])
        eventos = Evento.objects.filter(vigente=True, fecha__range=[desde, hasta])
        if obra is not None:
            eventos = eventos.filter(obra=obra)
        accidentes = eventos.filter(tipo__in=('ACC_INCAPACITANTE', 'ACC_MORTAL')).count()
        dias = eventos.filter(tipo__in=Evento.ACCIDENTES).aggregate(s=Sum('dias_descanso'))['s'] or 0
        incidentes = eventos.exclude(tipo__in=Evento.ACCIDENTES).count()
        hht = horas_hombre(periodo, obra)
        filas.append(_indice(periodo, accidentes, dias, hht, incidentes,
                             eventos.filter(tipo__in=Evento.ACCIDENTES).count()))
        acum['accidentes'] += accidentes
        acum['dias'] += dias
        acum['hht'] += hht
        acum['incidentes'] += incidentes
    total = _indice(str(anio), acum['accidentes'], acum['dias'], acum['hht'], acum['incidentes'],
                    sum(f['todos'] for f in filas))
    return filas, total


def _indice(periodo, accidentes, dias, hht, incidentes, todos):
    hht = Decimal(hht or 0)
    i_f = (Decimal(accidentes) * MILLON / hht).quantize(Decimal('0.01')) if hht else None
    i_g = (Decimal(dias) * MILLON / hht).quantize(Decimal('0.01')) if hht else None
    i_a = (i_f * i_g / 1000).quantize(Decimal('0.01')) if i_f is not None and i_g is not None else None
    return {'periodo': periodo, 'accidentes': accidentes, 'todos': todos, 'dias': dias, 'hht': hht,
            'incidentes': incidentes, 'if': i_f, 'ig': i_g, 'ia': i_a}


def indice_mes(clave, periodo):
    """Valor de un índice (SST_IF, SST_IG, SST_IA) del mes (para los indicadores de calidad)."""
    filas, _ = indices(int(periodo[:4]))
    fila = next(f for f in filas if f['periodo'] == periodo)
    return {'SST_IF': fila['if'], 'SST_IG': fila['ig'], 'SST_IA': fila['ia']}[clave]


# ---------------------------------------------------------------- vencimientos
def vencimientos(dias=30):
    """{tema: [(registro, fecha, detalle)]} de lo que vence en los próximos días o ya venció."""
    from planillas.models import Trabajador
    hoy = timezone.localdate()
    limite = hoy + timedelta(days=dias)
    salida = {}
    activos = Trabajador.objects.filter(fecha_ingreso__isnull=False).exclude(fecha_cese__lt=hoy)
    ultimos = {}
    for e in ExamenMedico.objects.filter(vigente=True, trabajador__in=activos).order_by('fecha'):
        ultimos[e.trabajador_id] = e
    salida['Exámenes médicos'] = [(e, e.vence, e.trabajador.nombre_completo) for e in ultimos.values()
                                  if e.vence and e.vence <= limite]
    salida['Trabajadores sin examen médico'] = [(t, None, t.nombre_completo) for t in activos
                                                if t.pk not in ultimos]
    sctr = {}
    for s in Sctr.objects.filter(trabajador__in=activos).order_by('hasta'):
        sctr[s.trabajador_id] = s
    salida['SCTR'] = [(s, s.hasta, f'{s.trabajador.nombre_completo} · {s.aseguradora}') for s in sctr.values()
                      if s.hasta <= limite]
    salida['Equipos de emergencia'] = [(e, e.vence, f'{e.get_tipo_display()} {e.codigo} · {e.donde}')
                                       for e in EquipoEmergencia.objects.filter(vigente=True, vence__lte=limite)
                                       .exclude(estado='BAJA')]
    reposiciones = []
    for l in LineaEPP.objects.filter(entrega__vigente=True, reposicion_dias__gt=0).select_related(
            'entrega__trabajador', 'producto'):
        if l.reponer_el <= limite and not LineaEPP.objects.filter(
                entrega__trabajador=l.entrega.trabajador, producto=l.producto,
                entrega__fecha__gt=l.entrega.fecha).exists():
            reposiciones.append((l.entrega, l.reponer_el, f'{l.producto.nombre} · {l.entrega.trabajador.nombre_completo}'))
    salida['Reposición de EPP'] = reposiciones
    salida['Monitoreos'] = [(m, m.proximo, f'{m.agente} · {m.donde}') for m in
                            Monitoreo.objects.filter(vigente=True, proximo__lte=limite)
                            if not Monitoreo.objects.filter(vigente=True, agente=m.agente, fecha__gt=m.fecha).exists()]
    salida['Permisos de trabajo abiertos'] = [(p, p.fecha, f'{p.get_tipo_display()} · {p.donde}') for p in
                                              PermisoTrabajo.objects.filter(vigente=True, cerrado=False,
                                                                            fecha__lt=hoy)]
    salida['Accidentes por notificar'] = [(e, e.plazo_notificacion, f'{e.get_tipo_display()} · {e.donde}')
                                          for e in sin_notificar()]
    return {k: sorted(v, key=lambda x: x[1] or hoy) for k, v in salida.items()}
