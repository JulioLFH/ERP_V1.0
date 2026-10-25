"""Control presupuestal del proyecto de servicios por rubro (SRV-11): presupuesto, comprometido (OC/OS aprobadas aún sin
factura), gastado y disponible. La OC que excede el disponible del rubro pide una aprobación adicional con motivo.

Criterio de lo gastado:
- Horas del personal: el costo de los partes de horas aprobados del personal en planilla.
- Especialistas, viajes, oficina y otros: lo contabilizado (facturas, recibos por honorarios, rendiciones, reparto) en
  el centro de costo del proyecto, en las cuentas del rubro. Los partes aprobados de independientes que aún no tienen
  recibo cuentan como comprometidos.
"""
from decimal import Decimal

from django.db.models import Q, Sum

from .models import RUBROS_PROYECTO, Obra, RubroProyecto

D0 = Decimal('0')
CUENTAS = {'ESPECIALISTAS': '632', 'VIAJES': '631', 'OFICINA': '634 635 636 656'}
EXCLUIDAS = ('62', '69', '79')  # personal (va por horas), costo de ventas y cargas imputables


class ErrorPresupuesto(Exception):
    pass


def prefijos(rubro):
    return (rubro.cuentas or CUENTAS.get(rubro.rubro, '')).split()


def _lineas(obra):
    from contabilidad.models import AsientoLinea

    from .models import CierreServicioProyecto, ParteHoras
    propios = set(ParteHoras.objects.filter(obra=obra, asiento_distribucion__isnull=False)
                  .values_list('asiento_distribucion_id', flat=True))
    propios |= set(CierreServicioProyecto.objects.filter(obra=obra, asiento__isnull=False)
                   .values_list('asiento_id', flat=True))
    return AsientoLinea.objects.filter(centro_costo_id=obra.centro_costo_id).exclude(asiento_id__in=propios)


def _contable(obra, rubro, otros_prefijos):
    if not obra.centro_costo_id:
        return D0
    qs = _lineas(obra)
    if rubro.rubro == 'OTROS' and not rubro.cuentas:
        qs = qs.filter(cuenta__codigo__startswith='6')
        for p in list(EXCLUIDAS) + otros_prefijos:
            qs = qs.exclude(cuenta__codigo__startswith=p)
    else:
        filtro = Q()
        for p in prefijos(rubro):
            filtro |= Q(cuenta__codigo__startswith=p)
        if not filtro:
            return D0
        qs = qs.filter(filtro)
    s = qs.aggregate(d=Sum('debe'), h=Sum('haber'))
    return (s['d'] or D0) - (s['h'] or D0)


def _base(doc):
    return (doc.total or D0) - (doc.igv or D0)


def comprometido_oc(obra, rubro, excluir=None):
    """Saldo sin facturar (sin IGV) de las OC/OS aprobadas del proyecto en el rubro."""
    if not obra.centro_costo_id:
        return D0
    from compras.models import OrdenCompra
    qs = OrdenCompra.objects.filter(centro_costo_id=obra.centro_costo_id, estado='APROBADO')
    qs = qs.filter(rubro_proyecto__in=['', 'OTROS']) if rubro == 'OTROS' else qs.filter(rubro_proyecto=rubro)
    if excluir is not None and excluir.pk:
        qs = qs.exclude(pk=excluir.pk)
    total = D0
    for oc in qs.prefetch_related('compras'):
        facturado = sum((_base(c) for c in oc.compras.all() if c.estado == 'REGISTRADO'
                         and c.tipo_comprobante not in ('07', '08')), D0)
        total += max(_base(oc) - facturado, D0)
    return total


def control(obra):
    """Filas por rubro con presupuesto, comprometido, gastado, disponible y % de uso, más el total."""
    rubros = {r.rubro: r for r in obra.rubros.all()}
    partes = obra.partes_horas.filter(estado='APROBADO')
    filas = []
    for clave, nombre in RUBROS_PROYECTO:
        r = rubros.get(clave) or RubroProyecto(obra=obra, rubro=clave)
        otros = [p for k, x in rubros.items() if k != 'OTROS' for p in prefijos(x)] + [
            p for k, v in CUENTAS.items() if k not in rubros for p in v.split()]
        comprometido = comprometido_oc(obra, clave)
        if clave == 'HORAS':
            gastado = sum((p.costo for p in partes.filter(trabajador__isnull=False)), D0)
        else:
            gastado = _contable(obra, r, otros)
            if clave == 'ESPECIALISTAS':  # horas aprobadas de independientes aún sin recibo
                independientes = sum((p.costo for p in partes.filter(trabajador__isnull=True)), D0)
                comprometido += max(independientes - gastado, D0)
        disponible = r.monto - comprometido - gastado
        filas.append({'rubro': clave, 'nombre': nombre, 'presupuesto': r.monto, 'cuentas': ' '.join(prefijos(r)),
                      'comprometido': comprometido, 'gastado': gastado, 'disponible': disponible,
                      'pct': ((comprometido + gastado) * 100 / r.monto).quantize(Decimal('0.1')) if r.monto else None,
                      'excedido': r.monto > 0 and disponible < 0 or (not r.monto and (comprometido + gastado) > 0
                                                                      and bool(rubros))})
    total = {k: sum((f[k] for f in filas), D0) for k in ('presupuesto', 'comprometido', 'gastado', 'disponible')}
    return {'filas': filas, 'total': total, 'tiene': bool(rubros)}


def guardar(obra, montos, cuentas=None):
    """montos: {rubro: Decimal}; cuentas: {rubro: 'prefijos'}. Un monto 0 sin cuentas quita el rubro."""
    cuentas = cuentas or {}
    for clave, _ in RUBROS_PROYECTO:
        monto = Decimal(montos.get(clave) or 0)
        if monto < 0:
            raise ErrorPresupuesto('El presupuesto de un rubro no puede ser negativo.')
        texto = ' '.join((cuentas.get(clave) or '').split())[:100]
        if any(not p.isdigit() for p in texto.split()):
            raise ErrorPresupuesto('Las cuentas del rubro son prefijos numéricos separados por espacio (ej. 631 6371).')
        if not monto and not texto:
            obra.rubros.filter(rubro=clave).delete()
            continue
        RubroProyecto.objects.update_or_create(obra=obra, rubro=clave, defaults={'monto': monto, 'cuentas': texto})
    total = obra.rubros.aggregate(s=Sum('monto'))['s'] or D0
    gastos = obra.rubros.exclude(rubro='HORAS').aggregate(s=Sum('monto'))['s'] or D0
    if obra.tipo == 'SERVICIO' and obra.gastos_presupuestados != gastos:
        Obra.objects.filter(pk=obra.pk).update(gastos_presupuestados=gastos)
    return total


def evaluar_oc(oc):
    """None si la OC no compromete el presupuesto de un proyecto de servicios; si no, un dict con el rubro, el monto
    de la OC, el disponible antes de ella y si lo excede."""
    if not oc.centro_costo_id:
        return None
    obra = Obra.objects.filter(centro_costo_id=oc.centro_costo_id, tipo='SERVICIO').first()
    if obra is None or not obra.rubros.exists():
        return None
    clave = oc.rubro_proyecto or 'OTROS'
    fila = next(f for f in control(obra)['filas'] if f['rubro'] == clave)
    disponible = fila['disponible']
    if oc.estado == 'APROBADO':  # ya está en el comprometido: el disponible antes de ella
        disponible += comprometido_oc(obra, clave) - comprometido_oc(obra, clave, excluir=oc)
    monto = _base(oc)
    return {'obra': obra, 'rubro': clave, 'nombre': fila['nombre'], 'monto': monto, 'disponible': disponible,
            'excede': monto > disponible}


def mensaje(ev):
    return (f'Proyecto {ev["obra"].codigo}, rubro {ev["nombre"]}: disponible S/ {ev["disponible"]:,.2f} y esta orden '
            f'suma S/ {ev["monto"]:,.2f} sin IGV' + (' — excede el presupuesto: necesita la aprobación adicional de '
                                                   'quien aprueba en Obras, con motivo.' if ev['excede'] else '.'))


def autorizar_exceso(oc, user, motivo):
    """Exige la aprobación adicional si la OC excede el disponible del rubro (llamado al aprobar la OC)."""
    ev = evaluar_oc(oc)
    if ev is None or not ev['excede'] or oc.exceso_autorizado_por_id:
        return
    from core.permisos import puede
    if not puede(user, 'obras.aprobar'):
        raise ErrorPresupuesto(mensaje(ev))
    if len((motivo or '').strip()) < 10:
        raise ErrorPresupuesto(mensaje(ev) + ' Indique el motivo en el comentario de la aprobación (mínimo 10 '
                                             'caracteres).')
    oc.exceso_autorizado_por, oc.exceso_motivo = user, motivo.strip()[:250]
    oc.save(update_fields=['exceso_autorizado_por', 'exceso_motivo'])
