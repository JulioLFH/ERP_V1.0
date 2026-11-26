"""App del operador en el celular: una acción por pantalla («Escanear» y luego «Confirmar»), letra grande, nada que
digitar (salvo el DNI de quien recibe), verde con sonido si está bien y rojo con el motivo si no, y sin señal guarda
todo en el teléfono y lo envía al volver la conexión."""
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.permisos import puede
from core.sustentos import ErrorSustento

from . import servicios as srv
from .xml_guias import ErrorXML


def _json(datos, status=200):
    return JsonResponse(datos, status=status, json_dumps_params={'ensure_ascii': False})


@login_required
def movil(request):
    datos = {
        'usuario': request.user.get_full_name() or request.user.username,
        'pasos': [{'clave': k, 'nombre': v} for k, v in srv.PASOS.items()],
        'pide_foto': srv.PIDE_FOTO,
        'puede': puede(request.user, 'operador_logistico.operar'),
        'urls': {'pendientes': reverse('operador_logistico:movil_pendientes'),
                 'verificar': reverse('operador_logistico:movil_verificar'),
                 'registrar': reverse('operador_logistico:movil_registrar'),
                 'sistema': reverse('operador_logistico:tablero')},
    }
    return render(request, 'operador_logistico/movil.html', {'datos': datos, 'version': settings.ERP_VERSION})


@login_required
@require_GET
def pendientes(request):
    paso = request.GET.get('paso', '')
    return _json({'paso': paso, 'items': srv.pendientes(request.user, paso)})


def _ident(request):
    valor = request.POST.get('id', '')
    return int(valor) if valor.isdigit() else None


@login_required
@require_POST
def verificar(request):
    try:
        info = srv.verificar(request.user, request.POST.get('paso', ''), request.POST.get('qr', ''), _ident(request))
    except srv.ErrorOperador as exc:
        return _json({'ok': False, 'repetida': exc.repetida, 'mensaje': str(exc)})
    return _json({'ok': True, **info})


def _cuando(texto):
    """Hora en que el operador escaneó (si el envío se demoró por falta de señal). Nunca en el futuro."""
    try:
        valor = datetime.fromisoformat((texto or '').replace('Z', '+00:00'))
    except ValueError:
        return None
    if timezone.is_naive(valor):
        valor = timezone.make_aware(valor)
    ahora = timezone.now()
    return valor if valor <= ahora else ahora


@login_required
@require_POST
def registrar(request):
    if not puede(request.user, 'operador_logistico.operar'):
        raise PermissionDenied
    P = request.POST
    try:
        mensaje = srv.registrar(request.user, P.get('paso', ''), P.get('qr', ''), _ident(request),
                                request.FILES.get('foto'), P.get('dni', ''), P.get('firma', ''), P.get('latitud'),
                                P.get('longitud'), _cuando(P.get('cuando')))
    except srv.ErrorOperador as exc:
        # un envío repetido (se reintentó sin señal) ya quedó registrado: para el celular está bien
        return _json({'ok': exc.repetida, 'repetida': exc.repetida, 'mensaje': str(exc), 'id_local': P.get('id_local')})
    except (ErrorSustento, ErrorXML) as exc:
        return _json({'ok': False, 'mensaje': str(exc), 'id_local': P.get('id_local')})
    return _json({'ok': True, 'mensaje': mensaje, 'id_local': P.get('id_local')})
