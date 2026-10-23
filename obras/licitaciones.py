"""CRM para constructoras: la oportunidad del embudo comercial es una obra en «Propuesta / licitación» con los datos del
proceso (SEACE o privado), su cronograma con alertas, los requisitos del postor y el presupuesto de oferta. Mientras se
licita, el costo de preparar la propuesta va a su centro de costo bajo «PROPUESTAS» (gasto de ventas); al ganar, la
obra pasa a Adjudicada, su centro a «OBRAS» (costo de producción) y la oferta se copia como presupuesto contractual;
al perder se guarda el motivo y el precio ganador."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import HitoLicitacion, Licitacion, Obra

D0 = Decimal('0')
DIAS_AVISO = 3  # los hitos del cronograma avisan desde 3 días antes


class ErrorLicitacion(Exception):
    pass


def _centro_propuestas(obra):
    """El centro de la obra bajo «PROPUESTAS» (gasto de ventas) mientras se licita."""
    from contabilidad.models import CentroCosto
    padre, _ = CentroCosto.objects.get_or_create(codigo='PROPUESTAS', defaults={
        'nombre': 'Propuestas y licitaciones', 'tipo': 'VENTAS'})
    CentroCosto.objects.filter(pk=obra.centro_costo_id).update(padre=padre, tipo='VENTAS')


def _centro_obras(obra):
    from contabilidad.models import CentroCosto
    padre, _ = CentroCosto.objects.get_or_create(codigo='OBRAS', defaults={'nombre': 'Obras', 'tipo': 'PRODUCCION'})
    CentroCosto.objects.filter(pk=obra.centro_costo_id).update(padre=padre, tipo='PRODUCCION')


@transaction.atomic
def crear(nombre, cliente, usuario, oportunidad=None, **datos):
    """Obra en licitación con su licitación y su oportunidad en el CRM (si no viene, se crea en «Propuesta»)."""
    obra = Obra.objects.create(nombre=nombre[:250], cliente=cliente, estado='LICITACION',
                               tipo=datos.pop('tipo_obra', None) or 'PRIVADA')
    return iniciar(obra, usuario, oportunidad, **datos)


@transaction.atomic
def iniciar(obra, usuario, oportunidad=None, **datos):
    """Licitación de una obra en «Propuesta / licitación»: su centro de costo pasa a Propuestas y la oportunidad del
    CRM queda vinculada (en «Propuesta / cotización»)."""
    from ventas.models import Oportunidad
    _centro_propuestas(obra)
    if oportunidad is None:
        oportunidad = Oportunidad.objects.create(
            nombre=obra.nombre[:150], tercero=obra.cliente, etapa='PROPUESTA',
            responsable=usuario if usuario and usuario.is_authenticated else None,
            monto=datos.get('valor_referencial') or obra.monto_contratado or D0)
    elif oportunidad.etapa in ('PROSPECTO', 'CALIFICADO'):
        oportunidad.etapa = 'PROPUESTA'
        oportunidad.save(update_fields=['etapa', 'actualizado'])
    return Licitacion.objects.create(obra=obra, oportunidad=oportunidad, **datos)


def sincronizar_oportunidad(lic):
    """El monto de la oportunidad es la oferta (o el valor referencial mientras no hay oferta)."""
    o = lic.oportunidad
    if o is None:
        return
    oferta = lic.oferta
    monto = oferta.total if oferta and oferta.total else lic.valor_referencial
    if monto and o.monto != monto:
        o.monto = monto
        o.save(update_fields=['monto', 'actualizado'])


@transaction.atomic
def ganar(lic, fecha=None):
    from . import calculos
    if lic.resultado != 'EN_CURSO':
        raise ErrorLicitacion(f'La licitación ya está {lic.get_resultado_display().lower()}.')
    oferta = lic.oferta
    obra = lic.obra
    lic.resultado, lic.fecha_resultado = 'GANADA', fecha or timezone.localdate()
    lic.save(update_fields=['resultado', 'fecha_resultado'])
    obra.estado = 'ADJUDICADA'
    if oferta is not None:
        obra.monto_contratado = oferta.total
        if not obra.presupuestos.filter(tipo='CONTRACTUAL').exists():
            calculos.copiar(oferta, 'CONTRACTUAL')
    obra.save(update_fields=['estado', 'monto_contratado'])
    _centro_obras(obra)
    if lic.oportunidad_id:
        lic.oportunidad.etapa = 'GANADA'
        if oferta is not None:
            lic.oportunidad.monto = oferta.total
        lic.oportunidad.save(update_fields=['etapa', 'monto', 'actualizado'])
    return lic


@transaction.atomic
def perder(lic, motivo, detalle='', ganador='', precio_ganador=None, desierta=False, fecha=None):
    if lic.resultado != 'EN_CURSO':
        raise ErrorLicitacion(f'La licitación ya está {lic.get_resultado_display().lower()}.')
    if not desierta and (not motivo or motivo not in dict(Licitacion.MOTIVOS)):
        raise ErrorLicitacion('Indique el motivo por el que se perdió (precio, experiencia, plazo…).')
    lic.resultado = 'DESIERTA' if desierta else 'PERDIDA'
    lic.motivo_perdida, lic.detalle_resultado = ('' if desierta else motivo), detalle[:250]
    lic.ganador, lic.precio_ganador = ganador[:150], precio_ganador
    lic.fecha_resultado = fecha or timezone.localdate()
    lic.save(update_fields=['resultado', 'motivo_perdida', 'detalle_resultado', 'ganador', 'precio_ganador',
                            'fecha_resultado'])
    Obra.objects.filter(pk=lic.obra_id).update(estado='NO_ADJUDICADA')
    if lic.oportunidad_id:
        texto = 'Proceso desierto o cancelado' if desierta else dict(Licitacion.MOTIVOS)[motivo]
        extra = f'; ganó {ganador}' if ganador else ''
        extra += f' por S/ {precio_ganador:,.2f}' if precio_ganador else ''
        lic.oportunidad.etapa = 'PERDIDA'
        lic.oportunidad.motivo_perdida = f'{texto}{extra}{": " + detalle if detalle else ""}'[:200]
        lic.oportunidad.save(update_fields=['etapa', 'motivo_perdida', 'actualizado'])
    return lic


def costo_propuesta(obra):
    """Lo gastado en la propuesta: gastos por naturaleza con el centro de costo de la obra."""
    from contabilidad.models import AsientoLinea
    agg = AsientoLinea.objects.filter(centro_costo=obra.centro_costo, es_destino=False,
                                      cuenta__codigo__startswith='6').aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or D0) - (agg['h'] or D0)


def proximos_hitos(dias=DIAS_AVISO):
    hoy = timezone.localdate()
    return (HitoLicitacion.objects.filter(hecho=False, fecha__lte=hoy + timedelta(days=dias),
                                          licitacion__resultado='EN_CURSO')
            .select_related('licitacion__obra').order_by('fecha', 'hora'))


def reportes():
    """Cartera en licitación, tasa de éxito por tipo de obra, procedimiento y entidad, y cartera contratada por
    ejecutar (backlog: contratado menos facturado con el centro de la obra)."""
    from ventas.models import Venta
    lics = list(Licitacion.objects.select_related('obra__cliente', 'obra__centro_costo', 'oportunidad'))
    abiertas = [x for x in lics if x.resultado == 'EN_CURSO']
    hoy = timezone.localdate()
    for x in abiertas:
        x.proximo = x.hitos.filter(hecho=False, fecha__gte=hoy).first()
        x.pendientes = x.requisitos.filter(cumple=False).count()
        x.costo = costo_propuesta(x.obra)

    def tasa(clave):
        grupos = {}
        for x in lics:
            if x.resultado not in ('GANADA', 'PERDIDA'):
                continue
            g = grupos.setdefault(clave(x), {'nombre': clave(x), 'ganadas': 0, 'perdidas': 0, 'monto_ganado': D0})
            if x.resultado == 'GANADA':
                g['ganadas'] += 1
                g['monto_ganado'] += x.obra.monto_contratado
            else:
                g['perdidas'] += 1
        for g in grupos.values():
            g['tasa'] = (Decimal(g['ganadas']) * 100 / (g['ganadas'] + g['perdidas'])).quantize(Decimal('0.1'))
        return sorted(grupos.values(), key=lambda g: -g['tasa'])
    backlog = []
    for o in Obra.objects.filter(estado__in=('ADJUDICADA', 'EJECUCION', 'PARALIZADA')).select_related('cliente'):
        facturado = sum((v.total_pen for v in Venta.objects.filter(centro_costo=o.centro_costo, estado='REGISTRADO')
                         .exclude(tipo_comprobante='07')), D0)
        backlog.append({'obra': o, 'contratado': o.monto_contratado, 'facturado': facturado,
                        'por_ejecutar': max(o.monto_contratado - facturado, D0),
                        'avance': (facturado * 100 / o.monto_contratado).quantize(Decimal('0.1'))
                        if o.monto_contratado else None})
    ganadas = sum(1 for x in lics if x.resultado == 'GANADA')
    perdidas = sum(1 for x in lics if x.resultado == 'PERDIDA')
    return {'abiertas': abiertas, 'ganadas': ganadas, 'perdidas': perdidas,
            'tasa': (Decimal(ganadas) * 100 / (ganadas + perdidas)).quantize(Decimal('0.1')) if ganadas + perdidas
            else None,
            'por_tipo': tasa(lambda x: x.obra.get_tipo_display()),
            'por_procedimiento': tasa(lambda x: x.get_procedimiento_display()),
            'por_entidad': tasa(lambda x: x.obra.cliente.nombre if x.obra.cliente_id else 'Sin cliente'),
            'motivos': Counter(x.get_motivo_perdida_display() for x in lics if x.resultado == 'PERDIDA').most_common(),
            'backlog': backlog, 'total_backlog': sum((b['por_ejecutar'] for b in backlog), D0),
            'en_licitacion': sum((x.valor_referencial for x in abiertas), D0)}
