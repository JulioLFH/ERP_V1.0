"""Estado del portal de proveedores para la pantalla de entrada y su tablero (PORT-08): lo que espera a Compras."""
from datetime import timedelta

from django.db.models import Max
from django.urls import reverse
from django.utils import timezone


def resumen():
    """[(clave, etiqueta, cantidad, url, nivel)] con los contadores del portal, más el último ingreso."""
    from compras.models import ConformidadServicio, OrdenCompra

    from .models import AccesoProveedor, FacturaProveedor
    facturas = reverse('compras:portal_facturas')
    vivas = FacturaProveedor.objects.exclude(estado='RECHAZADA')
    hace_30 = timezone.now() - timedelta(days=30)
    accesos = AccesoProveedor.objects.filter(usuario__is_active=True)
    filas = [
        ('por_revisar', 'Facturas por revisar', FacturaProveedor.objects.filter(estado='ENVIADA').count(),
         f'{facturas}?estado=ENVIADA', 'warning'),
        ('fuera', 'Fuera de tolerancia', FacturaProveedor.objects.filter(estado='ENVIADA').exclude(
            fuera_tolerancia='').count(), f'{facturas}?estado=ENVIADA&filtro=fuera', 'danger'),
        ('observadas', 'Observadas por SUNAT', vivas.filter(estado_sunat='OBSERVADO').count(),
         f'{facturas}?estado=&filtro=sunat', 'danger'),
        ('rechazadas', 'Rechazadas (30 días)', FacturaProveedor.objects.filter(
            estado='RECHAZADA', revisado_en__gte=hace_30).count(), f'{facturas}?estado=RECHAZADA', 'secondary'),
        ('hes', 'HES por liberar', ConformidadServicio.objects.filter(estado='POR_LIBERAR').count(),
         reverse('compras:hes_lista') + '?vista=todas&estado=POR_LIBERAR', 'warning'),
        ('oc', 'OC enviadas sin confirmar', OrdenCompra.objects.filter(estado_proveedor='ENVIADA').exclude(
            estado='ANULADO').count(), reverse('compras:oc_lista') + '?proveedor=ENVIADA', 'info'),
        ('accesos', 'Accesos activos', accesos.count(), reverse('compras:portal_accesos'), 'secondary'),
    ]
    ultimo = accesos.aggregate(u=Max('usuario__last_login'))['u']
    return filas, ultimo


def pendientes(filas):
    """Total de lo que requiere acción de Compras (para el globo del ícono)."""
    return sum(n for clave, _, n, _, _ in filas if clave in ('por_revisar', 'observadas', 'hes', 'oc'))
