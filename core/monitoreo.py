"""Monitoreo: errores del sistema, reportes de los usuarios y estado del servidor.

- Cada error 500 se guarda como incidencia (los iguales se agrupan por su firma y se cuentan) y se avisa por
  correo a los administradores de la empresa, como máximo una vez por hora por cada error distinto.
- «Reportar problema» (menú del usuario) crea una incidencia con la página en la que estaba.
- /salud/ responde si cada base de datos contesta y no tiene migraciones pendientes (para un monitor de caídas).
- Los errores también quedan en logs/errores.log (ver LOGGING en settings).
"""
import contextvars
import hashlib
import logging
import threading
import traceback
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

log = logging.getLogger(__name__)
AVISO_CADA = timedelta(hours=1)


# ---------------------------------------------------------------- errores del sistema
def _firma(exc, ruta):
    marcos = [m for m in traceback.extract_tb(exc.__traceback__) if '.venv' not in m.filename
              and 'site-packages' not in m.filename]
    ultimo = f'{marcos[-1].filename.rsplit("ERP", 1)[-1]}:{marcos[-1].lineno}' if marcos else ''
    return hashlib.sha1(f'{type(exc).__name__}|{ultimo}|{ruta}'.encode()).hexdigest()


def registrar_error(sender, request=None, **kwargs):
    """Señal got_request_exception: guarda o suma la incidencia y avisa. Nunca debe causar otro error."""
    import sys
    exc = sys.exc_info()[1]
    if exc is None or request is None:
        return
    try:
        from .models import Incidencia
        match = getattr(request, 'resolver_match', None)
        ruta = match.route if match else request.path
        firma = _firma(exc, ruta)
        usuario = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        ahora = timezone.now()
        inc = Incidencia.objects.filter(firma=firma, tipo='ERROR').exclude(estado='DESCARTADA').first()
        if inc:
            inc.veces += 1
            inc.ultima_vez = ahora
            inc.url = request.get_full_path()[:500]
            if inc.estado == 'RESUELTA':  # volvió a ocurrir
                inc.estado = 'ABIERTA'
            inc.save(update_fields=['veces', 'ultima_vez', 'url', 'estado'])
        else:
            inc = Incidencia.objects.create(
                tipo='ERROR', usuario=usuario, url=request.get_full_path()[:500],
                modulo=(getattr(request, 'modulo_actual', None) or '')[:40],
                descripcion=f'{type(exc).__name__}: {exc}'[:2000], detalle=traceback.format_exc()[-20000:],
                navegador=request.META.get('HTTP_USER_AGENT', '')[:300], firma=firma, ultima_vez=ahora)
        if inc.avisado_en is None or ahora - inc.avisado_en > AVISO_CADA:
            inc.avisado_en = ahora
            inc.save(update_fields=['avisado_en'])
            _avisar(inc)
    except Exception:  # la base puede estar caída: queda al menos en el archivo de errores
        log.exception('No se pudo registrar la incidencia del error')


def _avisar(inc):
    """Correo a los administradores (superusuarios con correo) en segundo plano: no demora la respuesta."""
    from django.contrib.auth.models import User
    destinos = list(User.objects.filter(is_superuser=True, is_active=True).exclude(email='')
                    .values_list('email', flat=True))
    if not destinos:
        return
    contexto = {'inc': inc, 'version': settings.ERP_VERSION}

    def enviar():
        from .correo import ErrorCorreo, enviar as enviar_correo
        asunto = (f'[Ceiba ERP] Error del sistema: {inc.descripcion[:80]}' if inc.tipo == 'ERROR'
                  else f'[Ceiba ERP] Problema reportado N° {inc.pk}')
        try:
            enviar_correo(destinos, asunto, 'core/correo_incidencia.html', contexto)
        except ErrorCorreo as exc:
            log.warning('Aviso de incidencia no enviado: %s', exc)
        except Exception:
            log.exception('Aviso de incidencia no enviado')

    def en_hilo():
        from django.db import connections
        try:
            enviar()
        finally:
            connections.close_all()  # solo las conexiones de este hilo

    if getattr(settings, 'MONITOREO_SINCRONO', False):  # pruebas
        enviar()
    else:
        ctx = contextvars.copy_context()  # conserva la empresa activa en el hilo
        threading.Thread(target=ctx.run, args=(en_hilo,), daemon=True).start()


# ---------------------------------------------------------------- reportar un problema
@login_required
def reportar(request):
    from .models import Incidencia
    volver = request.POST.get('url', '')
    if not url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        volver = '/'
    if request.method == 'POST':
        texto = request.POST.get('descripcion', '').strip()
        if len(texto) < 10:
            messages.error(request, 'Cuente con un poco más de detalle qué pasó (al menos 10 caracteres).')
            return redirect(volver)
        inc = Incidencia.objects.create(
            tipo='REPORTE', usuario=request.user, url=volver[:500], modulo=request.session.get('modulo', '')[:40],
            descripcion=texto[:5000], navegador=request.META.get('HTTP_USER_AGENT', '')[:300])
        _avisar(inc)
        messages.success(request, f'Gracias: su reporte quedó registrado con el número {inc.pk}. El administrador '
                                  f'lo revisará.')
    return redirect(volver)


# ---------------------------------------------------------------- bandeja del administrador
@login_required
def incidencias(request):
    from .models import Incidencia
    qs = Incidencia.objects.select_related('usuario', 'revisado_por')
    estado = request.GET.get('estado', 'pendientes')
    if estado == 'pendientes':
        qs = qs.filter(estado__in=['ABIERTA', 'REVISION'])
    elif estado:
        qs = qs.filter(estado=estado)
    if request.GET.get('tipo'):
        qs = qs.filter(tipo=request.GET['tipo'])
    resumen = {e: Incidencia.objects.filter(estado=e).count() for e, _ in Incidencia.ESTADOS}
    return render(request, 'core/incidencias.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'estado': estado,
        'estados': Incidencia.ESTADOS, 'tipos': Incidencia.TIPOS, 'resumen': resumen})


@login_required
def incidencia(request, pk):
    from .models import Incidencia
    inc = get_object_or_404(Incidencia.objects.select_related('usuario', 'revisado_por'), pk=pk)
    if request.method == 'POST':
        nuevo = request.POST.get('estado')
        if nuevo in dict(Incidencia.ESTADOS):
            inc.estado = nuevo
            inc.nota = request.POST.get('nota', '').strip()[:5000]
            inc.revisado_por = request.user
            inc.save(update_fields=['estado', 'nota', 'revisado_por'])
            messages.success(request, f'Incidencia {inc.pk}: {inc.get_estado_display().lower()}.')
        return redirect('incidencias')
    return render(request, 'core/incidencia.html', {'inc': inc, 'estados': Incidencia.ESTADOS})


# ---------------------------------------------------------------- salud del servidor
@never_cache
def salud(request):
    """Estado de cada base (responde y sin migraciones pendientes). 200 si todo está bien, 503 si no. No muestra
    datos de la empresa: sirve para un monitor de caídas externo."""
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor
    from erp import empresas
    bases, ok = {}, True
    for alias in empresas.empresas():
        try:
            conexion = connections[alias]
            with conexion.cursor() as c:
                c.execute('SELECT 1')
            ejecutor = MigrationExecutor(conexion)
            pendientes = len(ejecutor.migration_plan(ejecutor.loader.graph.leaf_nodes()))
            bases[alias] = 'ok' if not pendientes else f'{pendientes} migraciones pendientes'
            ok = ok and not pendientes
        except Exception as exc:
            bases[alias] = f'sin conexión ({type(exc).__name__})'
            ok = False
    return JsonResponse({'estado': 'ok' if ok else 'falla', 'version': settings.ERP_VERSION,
                         'hora': timezone.now().isoformat(timespec='seconds'), 'bases': bases},
                        status=200 if ok else 503, json_dumps_params={'ensure_ascii': False})
