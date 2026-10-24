"""Versiones del presupuesto que vienen del S10 (OBR-16): se reimporta la nueva meta, el adicional o el deductivo como
nueva versión de la obra, se compara partida por partida (subpresupuesto e ítem) y recurso por recurso (código), se
marca el motivo con la resolución que lo aprueba y, al aprobarla, lo ejecutado contra la versión anterior (avance,
valorizaciones, programación, tareo, partes de equipo y subcontratos) pasa a la nueva sin perderse. Los pedidos,
ingresos y consumos van por el centro de costo de la obra: no dependen de la versión."""
from collections import OrderedDict
from decimal import Decimal

from django.db import transaction

from .models import (AvancePartida, LineaSubcontrato, LineaTareo, LineaValorizacion, ParteEquipo, Partida,
                     Presupuesto, ProgramacionPartida, RecursoAPU)

D0 = Decimal('0')
# (modelo, campo, descripción) de lo que se registra contra una partida
REGISTROS = [(AvancePartida, 'partida', 'avance físico'), (LineaValorizacion, 'partida', 'valorizaciones'),
             (ProgramacionPartida, 'partida', 'cronograma'), (LineaTareo, 'partida', 'tareo'),
             (ParteEquipo, 'partida', 'partes de equipo'), (LineaSubcontrato, 'partida', 'subcontratos')]


def anterior_de(p):
    """La versión aprobada anterior del mismo tipo (o la anterior en número)."""
    qs = Presupuesto.objects.filter(obra=p.obra, tipo=p.tipo, version__lt=p.version).order_by('-version')
    return qs.filter(estado='APROBADO').first() or qs.first()


def _partidas(p):
    return OrderedDict((x.clave, x) for x in p.partidas.select_related('subpresupuesto').order_by(
        'subpresupuesto__codigo', 'orden'))


def _recursos(p):
    """{clave de partida: {código del recurso: (cantidad, recurso)}}."""
    salida = {}
    for l in RecursoAPU.objects.filter(partida__presupuesto=p).select_related('recurso', 'partida__subpresupuesto'):
        salida.setdefault(l.partida.clave, {})[l.recurso.codigo] = (l.cantidad, l.recurso)
    return salida


def con_registros(partidas):
    """{partida_id: [descripciones]} de las partidas que tienen algo registrado."""
    ids = [p.pk for p in partidas]
    salida = {}
    for modelo, campo, nombre in REGISTROS:
        for pid in modelo.objects.filter(**{f'{campo}__in': ids}).values_list(campo, flat=True).distinct():
            salida.setdefault(pid, []).append(nombre)
    return salida


def comparar(anterior, nuevo):
    """Altas, bajas y cambios (metrado, precio, descripción o unidad) por partida y los cambios de los análisis por
    recurso; con el efecto en el total y las bajas que tienen registros."""
    a, n = _partidas(anterior), _partidas(nuevo)
    altas = [x for k, x in n.items() if k not in a and not x.es_titulo]
    bajas = [x for k, x in a.items() if k not in n and not x.es_titulo]
    cambios = []
    for k, x in n.items():
        if k not in a or x.es_titulo:
            continue
        y = a[k]
        motivos = []
        if x.metrado != y.metrado:
            motivos.append(f'metrado {y.metrado:,.2f} → {x.metrado:,.2f}')
        if x.precio != y.precio:
            motivos.append(f'precio {y.precio:,.2f} → {x.precio:,.2f}')
        if x.descripcion.strip().lower() != y.descripcion.strip().lower():
            motivos.append('descripción')
        if (x.unidad or '').lower() != (y.unidad or '').lower():
            motivos.append(f'unidad {y.unidad} → {x.unidad}')
        if motivos:
            cambios.append({'antes': y, 'ahora': x, 'motivos': motivos, 'diferencia': x.parcial - y.parcial})
    ra, rn = _recursos(anterior), _recursos(nuevo)
    recursos = []
    for k in sorted(set(ra) & set(rn)):
        viejos, nuevos = ra[k], rn[k]
        for codigo in sorted(set(viejos) | set(nuevos)):
            antes, ahora = viejos.get(codigo), nuevos.get(codigo)
            if antes and ahora and antes[0] == ahora[0]:
                continue
            recursos.append({'partida': n[k], 'recurso': (ahora or antes)[1],
                             'antes': antes[0] if antes else None, 'ahora': ahora[0] if ahora else None})
    registros = con_registros(bajas)
    return {
        'anterior': anterior, 'nuevo': nuevo, 'altas': altas, 'bajas': bajas, 'cambios': cambios,
        'recursos': recursos,
        'bajas_con_registros': [(x, registros[x.pk]) for x in bajas if x.pk in registros],
        'total_altas': sum((x.parcial for x in altas), D0), 'total_bajas': sum((x.parcial for x in bajas), D0),
        'total_cambios': sum((c['diferencia'] for c in cambios), D0),
        'diferencia': nuevo.costo_directo - anterior.costo_directo,
        'diferencia_total': nuevo.total - anterior.total,
    }


@transaction.atomic
def trasladar_registros(nuevo):
    """Al aprobar una versión, lo registrado contra las partidas de la versión anterior del mismo tipo pasa a la
    partida equivalente (mismo subpresupuesto e ítem) de la nueva. Devuelve (trasladados, partidas sin equivalente
    con registros)."""
    anterior = anterior_de(nuevo)
    if anterior is None:
        return 0, []
    destino = _partidas(nuevo)
    origen = list(anterior.partidas.select_related('subpresupuesto'))
    n, sin_destino = 0, []
    registros = con_registros(origen)
    for p in origen:
        if p.pk not in registros:
            continue
        nueva = destino.get(p.clave)
        if nueva is None or nueva.es_titulo:
            sin_destino.append(p)
            continue
        for modelo, campo, _ in REGISTROS:
            n += modelo.objects.filter(**{campo: p}).update(**{campo: nueva})
    return n, sin_destino


def historial(obra):
    """Versiones de la obra por tipo, con su motivo, resolución, quién la aprobó y la diferencia con la anterior."""
    filas = []
    previas = {}
    for p in obra.presupuestos.select_related('aprobado_por').order_by('tipo', 'version'):
        previa = previas.get(p.tipo)
        filas.append({'p': p, 'diferencia': (p.total - previa.total) if previa else None, 'previa': previa})
        previas[p.tipo] = p
    return sorted(filas, key=lambda f: (f['p'].tipo, -f['p'].version))


def partidas_equivalentes(presupuesto, claves):
    """{clave: partida} del presupuesto para las claves indicadas."""
    return {x.clave: x for x in Partida.objects.filter(presupuesto=presupuesto).select_related('subpresupuesto')
            if x.clave in claves}
