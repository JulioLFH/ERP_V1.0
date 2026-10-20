"""Estado del servicio y disponibilidad: chequeo de cada componente (bases de datos, espacio en disco, respaldos,
correo y facturación electrónica), historial de chequeos y página pública /estado/ con la disponibilidad de los
últimos 30 y 90 días y las caídas. Se alimenta con el comando `chequear_servicio` (programarlo cada 5 minutos) y
con el monitor externo que consulta /salud/. No muestra datos de la empresa ni rutas del servidor."""
import os
import shutil
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

OPERATIVO, DEGRADADO, CAIDO = 'OPERATIVO', 'DEGRADADO', 'CAIDO'
NOMBRES = {'bases': 'Base de datos', 'disco': 'Espacio en disco', 'respaldo': 'Respaldo diario',
           'correo': 'Correo saliente', 'facturacion': 'Facturación electrónica'}
HORAS_RESPALDO = 26  # el respaldo es diario: más de 26 horas sin uno nuevo es una alerta


def carpeta_respaldos():
    return Path(os.environ.get('CARPETA_RESPALDOS') or Path(settings.BASE_DIR) / 'respaldos')


def chequear():
    """(ok, milisegundos, {componente: estado}, {componente: detalle})."""
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    from erp import empresas
    inicio = time.monotonic()
    estados, detalles = {}, {}
    caidas = []
    for alias in empresas.empresas():
        try:
            conexion = connections[alias]
            with conexion.cursor() as c:
                c.execute('SELECT 1')
            ejecutor = MigrationExecutor(conexion)
            if ejecutor.migration_plan(ejecutor.loader.graph.leaf_nodes()):
                caidas.append(f'{alias}: migraciones pendientes')
        except Exception as exc:  # cualquier falla de la base es una caída
            caidas.append(f'{alias}: sin conexión ({type(exc).__name__})')
    estados['bases'] = CAIDO if caidas else OPERATIVO
    detalles['bases'] = '; '.join(caidas) or f'{len(empresas.empresas())} base(s) responden'
    uso = shutil.disk_usage(settings.BASE_DIR)
    libre = uso.free / uso.total * 100
    estados['disco'] = CAIDO if libre < 3 else DEGRADADO if libre < 10 else OPERATIVO
    detalles['disco'] = f'{libre:.0f}% libre'
    carpeta = carpeta_respaldos()
    respaldos = sorted(carpeta.glob('*.dump'), key=lambda f: f.stat().st_mtime) if carpeta.is_dir() else []
    if respaldos:
        horas = (time.time() - respaldos[-1].stat().st_mtime) / 3600
        estados['respaldo'] = OPERATIVO if horas <= HORAS_RESPALDO else DEGRADADO
        detalles['respaldo'] = f'último hace {horas:.0f} h'
    else:
        estados['respaldo'], detalles['respaldo'] = DEGRADADO, 'sin respaldos registrados'
    from .models import CorreoConfig, FacturacionConfig
    estados['correo'] = OPERATIVO if CorreoConfig.actual().activa else DEGRADADO
    detalles['correo'] = 'configurado' if estados['correo'] == OPERATIVO else 'sin configurar'
    cfg = FacturacionConfig.actual()
    estados['facturacion'] = OPERATIVO if cfg.activa else DEGRADADO
    detalles['facturacion'] = cfg.get_proveedor_display() if cfg.activa else 'sin configurar'
    ms = int((time.monotonic() - inicio) * 1000)
    return estados['bases'] != CAIDO, ms, estados, detalles


def registrar(minimo_minutos=4):
    """Guarda un chequeo (no más de uno cada `minimo_minutos`). Devuelve el chequeo guardado o el último."""
    from .models import ChequeoServicio
    ultimo = ChequeoServicio.objects.first()
    if ultimo and timezone.now() - ultimo.fecha < timedelta(minutes=minimo_minutos):
        return ultimo
    ok, ms, estados, _ = chequear()
    ChequeoServicio.objects.filter(fecha__lt=timezone.now() - timedelta(days=120)).delete()
    return ChequeoServicio.objects.create(ok=ok, milisegundos=ms, componentes=estados)


def disponibilidad(dias):
    from .models import ChequeoServicio
    qs = ChequeoServicio.objects.filter(fecha__gte=timezone.now() - timedelta(days=dias))
    total = qs.count()
    return round(qs.filter(ok=True).count() / total * 100, 3) if total else None


def caidas(dias=90):
    """Periodos con el servicio caído: [{desde, hasta, minutos}] (consecutivos)."""
    from .models import ChequeoServicio
    periodos, actual = [], None
    for c in ChequeoServicio.objects.filter(fecha__gte=timezone.now() - timedelta(days=dias)).order_by('fecha'):
        if not c.ok and actual is None:
            actual = {'desde': c.fecha, 'hasta': c.fecha}
        elif not c.ok:
            actual['hasta'] = c.fecha
        elif actual is not None:
            actual['hasta'] = c.fecha
            periodos.append(actual)
            actual = None
    if actual is not None:
        actual['hasta'] = None
        periodos.append(actual)
    for p in periodos:
        fin = p['hasta'] or timezone.now()
        p['minutos'] = max(int((fin - p['desde']).total_seconds() // 60), 1)
    return list(reversed(periodos))


def barras(dias=30):
    """Una barra por día: % de chequeos correctos (None = sin chequeos)."""
    from django.db.models import Count, Q
    from django.db.models.functions import TruncDate

    from .models import ChequeoServicio
    hoy = timezone.localdate()
    filas = {f['dia']: f for f in ChequeoServicio.objects.filter(fecha__date__gte=hoy - timedelta(days=dias - 1))
             .annotate(dia=TruncDate('fecha')).values('dia').annotate(total=Count('id'), ok=Count('id', filter=Q(ok=True)))}
    salida = []
    for k in range(dias - 1, -1, -1):
        dia = hoy - timedelta(days=k)
        f = filas.get(dia)
        salida.append({'dia': dia, 'pct': round(f['ok'] / f['total'] * 100, 1) if f else None})
    return salida
