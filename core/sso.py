"""Ingreso con la cuenta de la empresa (inicio de sesión único) con OpenID Connect (Microsoft Entra ID, Google Workspace u otro proveedor).

Flujo de código de autorización con PKCE: el sistema lleva al usuario a su proveedor, recibe el código, lo canjea
por el id_token (JWT firmado por el proveedor), verifica la firma con las llaves públicas del proveedor (JWKS), el
emisor, la audiencia, la vigencia y el nonce, y reconoce al usuario por su correo. Solo entran usuarios activos que
ya existen en el sistema con ese correo: el proveedor dice quién es; los módulos y permisos los da el sistema."""
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .cifrado import cifrar, descifrar
from .forms import BootstrapMixin
from .models import ProveedorSSO

SESION = 'sso_pendiente'


class ErrorSSO(Exception):
    pass


def _http_json(url, datos=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(datos).encode() if datos else None,
                                 headers={'Accept': 'application/json', 'User-Agent': 'Ceiba-ERP'},
                                 method='POST' if datos else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            detalle = json.loads(exc.read().decode('utf-8'))
            texto = detalle.get('error_description') or detalle.get('error') or detalle
        except ValueError:
            texto = exc.code
        raise ErrorSSO(f'El proveedor respondió con error: {texto}') from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ErrorSSO(f'No se pudo conectar con el proveedor: {exc}') from exc


def configuracion(proveedor):
    """Documento de descubrimiento OpenID (endpoints, emisor y llaves); en caché una hora."""
    clave = f'oidc-config:{proveedor.url_emisor}'
    datos = cache.get(clave)
    if datos is None:
        datos = _http_json(f'{proveedor.url_emisor}/.well-known/openid-configuration')
        cache.set(clave, datos, 3600)
    return datos


def _b64(texto):
    return base64.urlsafe_b64decode(texto + '=' * (-len(texto) % 4))


def _llaves(config):
    clave = f'oidc-jwks:{config["jwks_uri"]}'
    llaves = cache.get(clave)
    if llaves is None:
        llaves = _http_json(config['jwks_uri']).get('keys', [])
        cache.set(clave, llaves, 3600)
    return llaves


def verificar_id_token(token, proveedor, config, nonce):
    """Claims del id_token si la firma (RS256), el emisor, la audiencia, la vigencia y el nonce son correctos."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    try:
        cabecera_b64, cuerpo_b64, firma_b64 = token.split('.')
        cabecera, claims = json.loads(_b64(cabecera_b64)), json.loads(_b64(cuerpo_b64))
    except (ValueError, AttributeError) as exc:
        raise ErrorSSO('El proveedor devolvió un token ilegible.') from exc
    if cabecera.get('alg') != 'RS256':
        raise ErrorSSO(f'Algoritmo de firma no admitido: {cabecera.get("alg")}.')
    llave = next((k for k in _llaves(config) if k.get('kid') == cabecera.get('kid') and k.get('kty') == 'RSA'), None)
    if llave is None:
        raise ErrorSSO('No se encontró la llave pública con que el proveedor firmó el token.')
    publica = rsa.RSAPublicNumbers(int.from_bytes(_b64(llave['e']), 'big'),
                                   int.from_bytes(_b64(llave['n']), 'big')).public_key()
    try:
        publica.verify(_b64(firma_b64), f'{cabecera_b64}.{cuerpo_b64}'.encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise ErrorSSO('La firma del token no es válida.') from exc
    emisor = config.get('issuer', '').replace('{tenantid}', claims.get('tid', ''))
    if claims.get('iss') != emisor:
        raise ErrorSSO('El token no fue emitido por el proveedor configurado.')
    audiencia = claims.get('aud')
    if proveedor.client_id not in (audiencia if isinstance(audiencia, list) else [audiencia]):
        raise ErrorSSO('El token no es para esta aplicación.')
    ahora = time.time()
    if claims.get('exp', 0) < ahora - 60 or claims.get('nbf', 0) > ahora + 60:
        raise ErrorSSO('El token venció; vuelva a intentar.')
    if claims.get('nonce') != nonce:
        raise ErrorSSO('El token no corresponde a este inicio de sesión.')
    return claims


def correo_de(claims):
    return (claims.get('email') or claims.get('preferred_username') or claims.get('upn') or '').strip().lower()


def iniciar(request, pk):
    """Lleva al usuario a su proveedor (con estado, nonce y PKCE en la sesión)."""
    from erp.empresas import activar, empresas, restaurar
    alias = request.GET.get('empresa') or request.session.get('empresa') or 'default'
    alias = alias if alias in empresas() else 'default'
    token = activar(alias)
    try:
        proveedor = get_object_or_404(ProveedorSSO, pk=pk, activo=True)
        try:
            config = configuracion(proveedor)
        except ErrorSSO as exc:
            messages.error(request, str(exc))
            return redirect('login')
    finally:
        restaurar(token)
    verificador = secrets.token_urlsafe(48)
    estado, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    siguiente = request.GET.get('next', '')
    request.session[SESION] = {'proveedor': proveedor.pk, 'empresa': alias, 'estado': estado, 'nonce': nonce,
                               'verificador': verificador, 'next': siguiente}
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip('=')
    parametros = {'client_id': proveedor.client_id, 'response_type': 'code', 'scope': 'openid email profile',
                  'redirect_uri': request.build_absolute_uri(reverse('sso_callback')), 'state': estado,
                  'nonce': nonce, 'code_challenge': reto, 'code_challenge_method': 'S256', 'prompt': 'select_account'}
    return redirect(f'{config["authorization_endpoint"]}?{urllib.parse.urlencode(parametros)}')


def callback(request):
    from erp.empresas import activar, restaurar
    pendiente = request.session.pop(SESION, None)
    if not pendiente or request.GET.get('state') != pendiente.get('estado'):
        messages.error(request, 'El ingreso con la cuenta de la empresa no es válido o venció. Vuelva a intentar.')
        return redirect('login')
    if request.GET.get('error'):
        messages.error(request, f'El proveedor no autorizó el ingreso: {request.GET.get("error_description") or request.GET["error"]}')
        return redirect('login')
    token_db = activar(pendiente['empresa'])
    try:
        proveedor = get_object_or_404(ProveedorSSO, pk=pendiente['proveedor'], activo=True)
        try:
            config = configuracion(proveedor)
            respuesta = _http_json(config['token_endpoint'], {
                'grant_type': 'authorization_code', 'code': request.GET.get('code', ''),
                'redirect_uri': request.build_absolute_uri(reverse('sso_callback')),
                'client_id': proveedor.client_id, 'client_secret': descifrar(proveedor.client_secret),
                'code_verifier': pendiente['verificador']})
            claims = verificar_id_token(respuesta.get('id_token', ''), proveedor, config, pendiente['nonce'])
        except ErrorSSO as exc:
            messages.error(request, str(exc))
            return redirect('login')
        correo = correo_de(claims)
        if not correo or not proveedor.dominio_permitido(correo):
            messages.error(request, f'La cuenta {correo or "(sin correo)"} no pertenece a un dominio permitido.')
            return redirect('login')
        usuarios = get_user_model().objects.filter(email__iexact=correo, is_active=True)
        if usuarios.count() != 1:
            messages.error(request, f'No hay un usuario activo del sistema con el correo {correo}'
                                    f'{" (hay más de uno)" if usuarios.count() > 1 else ""}: pida al administrador que '
                                    f'lo registre en Ajustes › Usuarios.')
            return redirect('login')
        usuario = usuarios.get()
        request.session.cycle_key()
        auth_login(request, usuario, backend='django.contrib.auth.backends.ModelBackend')
        request.session['empresa'] = pendiente['empresa']
        request.session['sso'] = proveedor.tipo  # el proveedor ya exige su propia verificación
        from .grupo import marcar
        marcar(request, usuario, pendiente['empresa'])
        from .auditoria import registrar
        registrar('MODIFICAR', usuario, {'Inicio de sesión': ['', f'Corporativo ({proveedor.nombre})']})
    finally:
        restaurar(token_db)
    siguiente = pendiente.get('next') or ''
    if not url_has_allowed_host_and_scheme(siguiente, {request.get_host()}, request.is_secure()):
        siguiente = reverse('home')
    respuesta = redirect(siguiente)
    from .politicas import revisar_equipo
    revisar_equipo(request, usuario, respuesta)
    return respuesta


def proveedores_para(alias):
    from erp.empresas import activar, restaurar
    token = activar(alias)
    try:
        return list(ProveedorSSO.objects.filter(activo=True))
    finally:
        restaurar(token)


# ---------------------------------------------------------------- configuración (Ajustes)
class ProveedorSSOForm(BootstrapMixin, forms.ModelForm):
    client_secret_nuevo = forms.CharField(label='Secreto de la aplicación (client_secret)', required=False,
                                          widget=forms.PasswordInput, help_text='Se guarda cifrado. Vacío = no cambia')

    class Meta:
        model = ProveedorSSO
        fields = ['tipo', 'nombre', 'client_id', 'client_secret_nuevo', 'tenant', 'emisor', 'dominios', 'activo']

    def clean(self):
        datos = super().clean()
        if datos.get('tipo') == 'OIDC' and not datos.get('emisor'):
            self.add_error('emisor', 'Indique el emisor OpenID del proveedor.')
        if not (datos.get('client_secret_nuevo') or self.instance.client_secret):
            self.add_error('client_secret_nuevo', 'Indique el secreto de la aplicación.')
        return datos

    def save(self, commit=True):
        p = super().save(commit=False)
        if self.cleaned_data.get('client_secret_nuevo'):
            p.client_secret = cifrar(self.cleaned_data['client_secret_nuevo'])
        p.save()
        return p


@login_required
def lista(request):
    if not request.user.is_superuser:
        raise Http404
    return render(request, 'core/sso_lista.html', {
        'proveedores': ProveedorSSO.objects.all(),
        'redireccion': request.build_absolute_uri(reverse('sso_callback'))})


@login_required
def editar(request, pk=None):
    if not request.user.is_superuser:
        raise Http404
    p = get_object_or_404(ProveedorSSO, pk=pk) if pk else ProveedorSSO()
    form = ProveedorSSOForm(request.POST or None, instance=p)
    if request.method == 'POST' and form.is_valid():
        p = form.save()
        messages.success(request, f'Ingreso «{p.nombre}» guardado.')
        return redirect('sso_lista')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Ingreso con Microsoft / Google'})
