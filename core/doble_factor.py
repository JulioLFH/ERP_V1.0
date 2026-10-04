"""Doble factor de autenticación (TOTP, RFC 6238) y recuperación de contraseña por correo."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_encode

from .models import Empresa, IntentoAcceso, SegundoFactor

PASO = 30  # segundos de validez de cada código
SESION_PENDIENTE = 'doble_factor_usuario'


# ---------------------------------------------------------------- TOTP
def nuevo_secreto():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _codigo(secreto, paso):
    clave = base64.b32decode(secreto + '=' * (-len(secreto) % 8))
    digest = hmac.new(clave, struct.pack('>Q', paso), hashlib.sha1).digest()
    corte = digest[-1] & 0x0F
    numero = struct.unpack('>I', digest[corte:corte + 4])[0] & 0x7FFFFFFF
    return f'{numero % 1_000_000:06d}'


def codigo_actual(secreto, instante=None):
    return _codigo(secreto, int((instante or time.time()) // PASO))


def verificar_totp(secreto, codigo, ultimo_paso=0, instante=None):
    """Paso aceptado (tolera ±1 paso de desfase del reloj) o None. Un código ya usado no vale de nuevo."""
    codigo = (codigo or '').replace(' ', '')
    if len(codigo) != 6 or not codigo.isdigit():
        return None
    actual = int((instante or time.time()) // PASO)
    for paso in (actual - 1, actual, actual + 1):
        if paso > ultimo_paso and hmac.compare_digest(_codigo(secreto, paso), codigo):
            return paso
    return None


def uri_aprovisionamiento(secreto, usuario):
    emisor = f'Ceiba ERP {Empresa.actual().razon_social}'[:60]
    return (f'otpauth://totp/{quote(emisor)}:{quote(usuario.username)}?secret={secreto}&issuer={quote(emisor)}'
            f'&digits=6&period={PASO}')


def nuevos_codigos_respaldo():
    """(códigos en claro para mostrar una sola vez, hashes para guardar)."""
    codigos = [f'{secrets.randbelow(10**8):08d}' for _ in range(8)]
    return codigos, [make_password(c) for c in codigos]


def usar_codigo_respaldo(sf, codigo):
    codigo = (codigo or '').replace(' ', '').replace('-', '')
    for h in sf.codigos_respaldo:
        if check_password(codigo, h):
            sf.codigos_respaldo = [x for x in sf.codigos_respaldo if x != h]
            sf.save(update_fields=['codigos_respaldo'])
            return True
    return False


def tiene_doble_factor(usuario):
    return SegundoFactor.objects.filter(usuario=usuario, activo=True).exists()


# ---------------------------------------------------------------- inicio de sesión (segundo paso)
def verificar_login(request):
    """Segundo paso del inicio de sesión: código de la app autenticadora o un código de respaldo."""
    from .acceso import MAX_INTENTOS, _ip, intentos_recientes
    pendiente = request.session.get(SESION_PENDIENTE)
    if not pendiente:
        return redirect('login')
    usuario = get_user_model().objects.filter(pk=pendiente.get('id'), is_active=True).first()
    if usuario is None or not tiene_doble_factor(usuario):
        request.session.pop(SESION_PENDIENTE, None)
        return redirect('login')
    error = ''
    if request.method == 'POST':
        if intentos_recientes(usuario.username) >= MAX_INTENTOS:
            request.session.pop(SESION_PENDIENTE, None)
            messages.error(request, 'Demasiados códigos incorrectos: usuario bloqueado temporalmente.')
            return redirect('login')
        sf = usuario.segundo_factor
        codigo = request.POST.get('codigo', '')
        paso = verificar_totp(sf.secreto, codigo, sf.ultimo_paso)
        if paso or usar_codigo_respaldo(sf, codigo):
            if paso:
                sf.ultimo_paso = paso
                sf.save(update_fields=['ultimo_paso'])
            siguiente = pendiente.get('next') or ''
            request.session.pop(SESION_PENDIENTE, None)
            IntentoAcceso.objects.filter(usuario__iexact=usuario.username).delete()
            auth_login(request, usuario, backend=pendiente.get('backend'))
            if not url_has_allowed_host_and_scheme(siguiente, {request.get_host()}, request.is_secure()):
                siguiente = reverse('home')
            return redirect(siguiente)
        IntentoAcceso.objects.create(usuario=usuario.username[:150], ip=_ip(request))
        error = 'Código incorrecto o vencido.'
    return render(request, 'registration/doble_factor.html', {'error': error, 'usuario': usuario})


# ---------------------------------------------------------------- configuración por el usuario
@login_required
def seguridad(request):
    """Activar o desactivar el doble factor de la propia cuenta."""
    sf = SegundoFactor.objects.filter(usuario=request.user).first()
    codigos = None
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if accion == 'iniciar':
        request.session['doble_factor_secreto'] = nuevo_secreto()
    elif accion == 'confirmar':
        secreto = request.session.get('doble_factor_secreto')
        paso = verificar_totp(secreto, request.POST.get('codigo')) if secreto else None
        if paso:
            codigos, hashes = nuevos_codigos_respaldo()
            sf, _ = SegundoFactor.objects.update_or_create(usuario=request.user, defaults={
                'secreto': secreto, 'activo': True, 'activado_en': timezone.now(), 'ultimo_paso': paso,
                'codigos_respaldo': hashes})
            request.session.pop('doble_factor_secreto', None)
            from .auditoria import registrar
            registrar('MODIFICAR', request.user, {'Doble factor': ['Inactivo', 'Activo']})
            messages.success(request, 'Doble factor activado. Guarde los códigos de respaldo en un lugar seguro.')
        else:
            messages.error(request, 'El código no coincide. Revise la hora del teléfono y vuelva a intentar.')
    elif accion == 'desactivar' and sf and sf.activo:
        if not request.user.check_password(request.POST.get('clave', '')) or not (
                verificar_totp(sf.secreto, request.POST.get('codigo'), sf.ultimo_paso)
                or usar_codigo_respaldo(sf, request.POST.get('codigo'))):
            messages.error(request, 'Contraseña o código incorrectos.')
        else:
            sf.delete()
            sf = None
            from .auditoria import registrar
            registrar('MODIFICAR', request.user, {'Doble factor': ['Activo', 'Inactivo']})
            messages.success(request, 'Doble factor desactivado.')
    elif accion == 'regenerar' and sf and sf.activo:
        if verificar_totp(sf.secreto, request.POST.get('codigo'), sf.ultimo_paso):
            codigos, sf.codigos_respaldo = nuevos_codigos_respaldo()
            sf.save(update_fields=['codigos_respaldo'])
            messages.success(request, 'Nuevos códigos de respaldo generados; los anteriores ya no sirven.')
        else:
            messages.error(request, 'Código incorrecto.')
    secreto = request.session.get('doble_factor_secreto') if not (sf and sf.activo) else None
    return render(request, 'core/seguridad.html', {
        'sf': sf, 'secreto': secreto, 'codigos': codigos,
        'uri': uri_aprovisionamiento(secreto, request.user) if secreto else '',
        'restantes': len(sf.codigos_respaldo) if sf and sf.activo else 0})


# ---------------------------------------------------------------- recuperación de contraseña
class RecuperarForm(forms.Form):
    usuario = forms.CharField(label='Usuario o correo electrónico', max_length=150)


def recuperar(request):
    """Envía al correo del usuario un enlace de un solo uso para crear una nueva contraseña. Siempre responde lo
    mismo, exista o no el usuario, para no revelar qué cuentas existen."""
    from . import correo
    from .acceso import _ip
    form = RecuperarForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        ip = _ip(request)
        clave_limite = f'recuperar:{ip}'[:150]
        desde = timezone.now() - timedelta(hours=1)
        if IntentoAcceso.objects.filter(usuario=clave_limite, creado__gte=desde).count() >= 5:
            messages.error(request, 'Demasiadas solicitudes. Intente de nuevo en una hora.')
            return redirect('recuperar')
        IntentoAcceso.objects.create(usuario=clave_limite, ip=ip)
        dato = form.cleaned_data['usuario'].strip()
        User = get_user_model()
        usuarios = User.objects.filter(is_active=True).exclude(email='').filter(
            username__iexact=dato) | User.objects.filter(is_active=True, email__iexact=dato)
        for u in usuarios.distinct():
            enlace = request.build_absolute_uri(reverse('recuperar_confirmar', kwargs={
                'uidb64': urlsafe_base64_encode(force_bytes(u.pk)), 'token': default_token_generator.make_token(u)}))
            try:
                correo.enviar([u.email], 'Recuperación de contraseña', 'registration/correo_recuperar.html',
                              {'usuario': u, 'enlace': enlace})
            except correo.ErrorCorreo:
                pass  # no se revela al visitante; el administrador ve el error en el registro del servidor
        return render(request, 'registration/recuperar_enviado.html')
    return render(request, 'registration/recuperar.html', {'form': form})
