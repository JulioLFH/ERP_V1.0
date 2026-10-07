"""Costeo por actividades (ABC): el gasto real de los centros de costo (contabilidad, cuentas 62-68) pasa a las
actividades según el % que consume cada una y de las actividades a los productos según su inductor del periodo."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum

from .models import ActividadABC, HoraOrden, OrdenProduccion

D0 = Decimal('0')


def gasto_real(centro, periodo):
    """Gasto del centro de costo (y sus hijos) en el periodo: debe - haber de las cuentas 62 a 68."""
    from contabilidad.models import AsientoLinea
    qs = AsientoLinea.objects.filter(asiento__periodo=periodo, es_destino=False,
                                     centro_costo_id__in=centro.descendientes_ids(), cuenta__codigo__regex=r'^6[2-8]')
    s = qs.aggregate(d=Sum('debe'), h=Sum('haber'))
    return (s['d'] or D0) - (s['h'] or D0)


def inductores(periodo):
    """{inductor: {producto_id: cantidad}} del periodo."""
    from contabilidad.centralizar import _rango
    from ventas.models import VentaItem

    from .models import InspeccionCalidad
    desde, hasta = _rango(periodo)
    terminadas = OrdenProduccion.objects.filter(estado='TERMINADA', fecha_fin__range=[desde, hasta])
    datos = {
        'ORDENES': terminadas.values_list('producto').annotate(n=Count('id')),
        'UNIDADES': terminadas.values_list('producto').annotate(n=Sum('cantidad_producida')),
        'HORAS': HoraOrden.objects.filter(orden__in=terminadas).values_list('orden__producto')
        .annotate(n=Sum('horas_real')),
        'INSPECCIONES': InspeccionCalidad.objects.filter(fecha__range=[desde, hasta]).values_list('producto')
        .annotate(n=Count('id')),
        'DESPACHOS': VentaItem.objects.filter(
            documento__estado='REGISTRADO', documento__fecha_emision__range=[desde, hasta],
            producto__isnull=False).exclude(documento__tipo_comprobante__in=['07', '08'])
        .values_list('producto').annotate(n=Count('id')),
    }
    return {k: {pid: Decimal(n or 0) for pid, n in v} for k, v in datos.items()}


def costeo(periodo):
    """Actividades con su costo, inductor y tasa; productos con el costo de cada actividad y su unitario ABC frente a
    la mano de obra y CIF absorbidos por sus órdenes (costeo tradicional por horas)."""
    from contabilidad.centralizar import _rango
    from core.models import Producto
    cantidades = inductores(periodo)
    gastos = {}
    actividades, asignado = [], defaultdict(lambda: defaultdict(lambda: D0))
    uso_centros = defaultdict(lambda: D0)
    for act in ActividadABC.objects.filter(activo=True).prefetch_related('recursos__centro_costo'):
        costo = D0
        for r in act.recursos.all():
            if r.centro_costo_id not in gastos:
                gastos[r.centro_costo_id] = gasto_real(r.centro_costo, periodo)
            costo += (gastos[r.centro_costo_id] * r.porcentaje / 100).quantize(Decimal('0.01'))
            uso_centros[r.centro_costo] += r.porcentaje
        base = cantidades[act.inductor]
        total = sum(base.values(), D0)
        tasa = (costo / total).quantize(Decimal('0.0001')) if total else None
        if tasa is not None:
            for pid, n in base.items():
                asignado[pid][act.pk] = (tasa * n).quantize(Decimal('0.01'))
        actividades.append({'a': act, 'costo': costo, 'total_inductor': total, 'tasa': tasa,
                            'sin_base': bool(costo and not total)})
    desde, hasta = _rango(periodo)
    terminadas = OrdenProduccion.objects.filter(estado='TERMINADA', fecha_fin__range=[desde, hasta])
    tradicional = {pid: (mo or D0) + (cif or D0) for pid, mo, cif in terminadas.values_list('producto').annotate(
        mo=Sum('costo_mano_obra'), cif=Sum('costo_cif'))}
    unidades = cantidades['UNIDADES']
    productos = []
    for p in Producto.objects.filter(pk__in=set(asignado) | set(tradicional)).order_by('nombre'):
        por_act = [asignado[p.pk].get(a['a'].pk, D0) for a in actividades]
        total = sum(por_act, D0)
        u = unidades.get(p.pk, D0)
        productos.append({'p': p, 'por_actividad': por_act, 'total': total, 'unidades': u,
                          'unitario': (total / u).quantize(Decimal('0.0001')) if u else None,
                          'tradicional': tradicional.get(p.pk, D0),
                          'tradicional_unit': (tradicional.get(p.pk, D0) / u).quantize(Decimal('0.0001')) if u else None})
    excedidos = [(cc, pct) for cc, pct in uso_centros.items() if pct > 100]
    return {'actividades': actividades, 'productos': productos, 'excedidos': excedidos,
            'total': sum((a['costo'] for a in actividades), D0),
            'asignado': sum((p['total'] for p in productos), D0)}
