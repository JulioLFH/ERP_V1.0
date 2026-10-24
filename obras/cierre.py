"""Cierre de la obra: para pasarla a Liquidada o Cerrada su almacén debe quedar en cero (los sobrantes se trasladan
con guía al almacén central, se devuelven al proveedor o se dan de baja con aprobación); entonces el almacén se
cierra (solo admite ajustes de inventario) y el centro de costo se desactiva. Reabrirla exige el permiso «Reabrir
obras liquidadas» y queda en la auditoría con su motivo."""
from decimal import Decimal

from django.db import transaction

from .models import Obra

D0 = Decimal('0')
CERRADAS = ('LIQUIDADA', 'CERRADA')


class ErrorCierre(Exception):
    pass


def sobrantes(obra):
    """[(producto, cantidad, costo promedio, valor)] que quedan en el almacén de la obra."""
    from core.models import StockAlmacen, r2
    if not obra.almacen_id:
        return []
    filas = []
    for s in (StockAlmacen.objects.filter(almacen_id=obra.almacen_id).exclude(cantidad=0)
              .select_related('producto').order_by('producto__nombre')):
        p = s.producto
        filas.append((p, s.cantidad, p.costo_promedio, r2(s.cantidad * p.costo_promedio)))
    return filas


def sobrantes_por_obra():
    """Obras con stock en su almacén y su valor (para liquidarlas)."""
    salida = []
    for obra in Obra.objects.exclude(almacen=None).select_related('almacen').order_by('codigo'):
        filas = sobrantes(obra)
        if filas:
            salida.append({'obra': obra, 'filas': filas, 'valor': sum((f[3] for f in filas), D0)})
    return salida


def validar_cierre(obra):
    filas = sobrantes(obra)
    if filas:
        texto = ', '.join(f'{p.nombre} ({c:,.2f})' for p, c, _, _ in filas[:4])
        raise ErrorCierre(f'El almacén de la obra tiene sobrantes por S/ {sum((f[3] for f in filas), D0):,.2f} '
                          f'({texto}{"…" if len(filas) > 4 else ""}): trasládelos al almacén central, devuélvalos o '
                          f'déles de baja antes de liquidar la obra.')


@transaction.atomic
def cerrar(obra, usuario=None):
    """Bloquea el almacén (solo ajustes) y desactiva el centro de costo."""
    from contabilidad.models import CentroCosto
    from core.auditoria import registrar
    from core.models import Almacen
    validar_cierre(obra)
    if obra.almacen_id:
        Almacen.objects.filter(pk=obra.almacen_id).update(cerrado=True)
    if obra.centro_costo_id:
        CentroCosto.objects.filter(pk=obra.centro_costo_id).update(activo=False)
    registrar('MODIFICAR', obra, {'estado': obra.estado, 'almacen': 'cerrado', 'centro_costo': 'desactivado'},
              f'Obra {obra.get_estado_display().lower()}: almacén y centro de costo cerrados')


@transaction.atomic
def reabrir(obra, usuario, motivo):
    from contabilidad.models import CentroCosto
    from core.auditoria import registrar
    from core.models import Almacen
    from core.permisos import puede
    if not puede(usuario, 'obras.reabrir'):
        raise ErrorCierre('Reabrir una obra liquidada o cerrada requiere el permiso «Reabrir obras liquidadas».')
    if not (motivo or '').strip():
        raise ErrorCierre('Indique el motivo para reabrir la obra.')
    if obra.almacen_id:
        Almacen.objects.filter(pk=obra.almacen_id).update(cerrado=False)
    if obra.centro_costo_id:
        CentroCosto.objects.filter(pk=obra.centro_costo_id).update(activo=True)
    registrar('MODIFICAR', obra, {'estado': obra.estado, 'almacen': 'reabierto', 'centro_costo': 'activo'},
              f'Obra reabierta: {motivo.strip()}'[:250])
