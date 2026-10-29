"""Control del ingreso por IP (Ajustes › IP permitidas e intentos de ingreso).

Con el control activo solo entran las IP (o redes) de la lista; las demás reciben «Acceso no autorizado» y quedan en
el listado de intentos, desde donde el administrador las permite con un clic. Esta misma PC (127.0.0.1) siempre entra,
para que nadie se quede fuera de su propio servidor. Todo se guarda en la base principal (vale para todas las
empresas). La IP es la de la conexión (REMOTE_ADDR): la cabecera X-Forwarded-For solo se usa con CONFIAR_PROXY=1
(servidor detrás de un proxy propio), porque cualquiera puede escribirla."""
import ipaddress
import logging
import os
import threading
import time

from django.http import HttpResponseForbidden
from django.template.loader import render_to_string
from django.utils import timezone

log = logging.getLogger(__name__)

LOCALES = ('127.0.0.1', '::1')
REFRESCO = 30          # segundos que dura en memoria la lista de IP permitidas
REGISTRO_CADA = 60     # una escritura por IP y minuto como máximo (las demás visitas se suman en memoria)

_estado = {'hora': 0.0, 'activo': False, 'redes': [], 'aprobados': set(), 'version_minima': ''}
_pendientes = {}       # ip -> [veces, bloqueos, ruta, navegador, usuario, permitida, última escritura]
_candado = threading.Lock()


def ip_cliente(request):
    if os.environ.get('CONFIAR_PROXY') == '1':
        reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        if reenviada:
            return reenviada[:45]
    return (request.META.get('REMOTE_ADDR') or '')[:45]


def red_valida(texto):
    """La red (ip_network) del texto o ValueError: «190.12.34.56», «192.168.1.0/24», «2001:db8::/32»."""
    return ipaddress.ip_network((texto or '').strip(), strict=False)


def invalidar():
    _estado['hora'] = 0.0


def _cargar():
    if time.monotonic() - _estado['hora'] < REFRESCO:
        return _estado
    from .models import ControlIp, DispositivoApp, IpPermitida
    try:
        control = ControlIp.actual()
        activo, minima = control.activo, control.version_minima_app
        redes = []
        for texto in IpPermitida.objects.using('default').values_list('ip', flat=True):
            try:
                redes.append(red_valida(texto))
            except ValueError:
                continue
        aprobados = set(DispositivoApp.objects.using('default').filter(estado='APROBADO')
                        .values_list('codigo', flat=True))
    except Exception:  # base sin migrar o no disponible: no se bloquea a nadie
        activo, redes, aprobados, minima = False, [], set(), ''
    _estado.update(hora=time.monotonic(), activo=activo, redes=redes, aprobados=aprobados, version_minima=minima)
    return _estado


def permitida(ip, estado=None):
    estado = estado or _cargar()
    if not estado['activo'] or ip in LOCALES:
        return True
    try:
        direccion = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(direccion.version == red.version and direccion in red for red in estado['redes'])


def registrar(ip, request, entro, usuario=''):
    """Suma la visita de la IP; escribe en la base como máximo una vez por minuto por IP (y siempre si se rechaza
    por primera vez en ese minuto)."""
    if not ip:
        return
    ahora = time.monotonic()
    with _candado:
        fila = _pendientes.setdefault(ip, [0, 0, '', '', '', True, 0.0])
        fila[0] += 1
        fila[1] += 0 if entro else 1
        fila[2] = request.path[:300]
        fila[3] = request.META.get('HTTP_USER_AGENT', '')[:300]
        fila[4] = usuario or fila[4]
        fila[5] = entro
        if ahora - fila[6] < REGISTRO_CADA:
            return
        veces, bloqueos, ruta, navegador, nombre, _, _ = fila
        _pendientes[ip] = [0, 0, ruta, navegador, nombre, entro, ahora]
    try:
        from django.db.models import F

        from .models import AccesoIp
        actualizado = AccesoIp.objects.using('default').filter(ip=ip).update(
            ultima_vez=timezone.now(), veces=F('veces') + veces, bloqueos=F('bloqueos') + bloqueos,
            ultima_ruta=ruta, navegador=navegador, permitida=entro, **({'usuario': nombre} if nombre else {}))
        if not actualizado:
            AccesoIp.objects.using('default').create(ip=ip, ultima_vez=timezone.now(), veces=veces,
                                                     bloqueos=bloqueos, ultima_ruta=ruta, navegador=navegador,
                                                     usuario=nombre, permitida=entro)
    except Exception:  # el registro nunca impide atender la página
        log.exception('No se pudo registrar el acceso de %s', ip)


def olvidar(ip):
    """Al permitir o borrar una IP del listado, lo acumulado en memoria se descarta."""
    with _candado:
        _pendientes.pop(ip, None)


class ControlIpMiddleware:
    """Rechaza las IP que no están en la lista cuando el control está activo y registra todas las que llegan. Un
    celular aprobado con la app Ceiba entra desde cualquier IP con su pase (core/app_movil.py); la autorización de
    la app se atiende siempre, porque es la que entrega ese pase."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import app_movil
        ip = ip_cliente(request)
        celular = None
        if not permitida(ip) and request.path != app_movil.RUTA:
            celular = app_movil.codigo_con_pase(request, _cargar())
            if celular is None:
                registrar(ip, request, False, request.POST.get('username', '') if request.method == 'POST' else '')
                es_app = 'CeibaApp/' in request.META.get('HTTP_USER_AGENT', '')
                return HttpResponseForbidden(render_to_string('core/ip_no_autorizada.html',
                                                              {'ip': ip, 'es_app': es_app}))
        response = self.get_response(request)
        if ip not in LOCALES and not request.path.startswith('/static/'):
            usuario = getattr(request, 'user', None)
            nombre = usuario.get_username() if usuario is not None and usuario.is_authenticated else ''
            registrar(ip, request, True, nombre)
            if celular:
                app_movil.marcar_uso(celular, nombre, ip)
        return response
