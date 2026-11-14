"""Primera instalación: cuando el sistema no tiene ningún usuario, la pantalla de ingreso lleva a este asistente para
registrar la empresa principal (RUC y razón social) y crear el primer administrador, que queda como usuario
corporativo del grupo (entra al Panel del grupo y crea las demás empresas).

Seguridad: el asistente solo existe mientras no hay usuarios, y solo se abre desde el mismo equipo del servidor o con
la clave de instalación (variable de entorno CLAVE_INSTALACION), para que nadie más se adueñe de un servidor recién
instalado."""
import os

from django import forms
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.password_validation import validate_password
from django.http import Http404
from django.shortcuts import redirect, render

from .forms import BootstrapMixin, error_ruc

LOCALES = {'127.0.0.1', '::1', 'localhost'}


def pendiente():
    """¿El sistema aún no tiene usuarios (instalación nueva)?"""
    try:
        return not get_user_model().objects.using('default').exists()
    except Exception:  # base sin tablas: primero hay que migrar
        return False


def permitido(request):
    clave = os.environ.get('CLAVE_INSTALACION', '')
    if clave:
        return request.GET.get('clave') == clave or request.POST.get('clave') == clave
    return request.META.get('REMOTE_ADDR', '') in LOCALES


class InstalacionForm(BootstrapMixin, forms.Form):
    razon_social = forms.CharField(label='Razón social de la empresa principal', max_length=200)
    ruc = forms.CharField(label='RUC', max_length=11)
    direccion = forms.CharField(label='Dirección fiscal', max_length=200, required=False)
    usuario = forms.CharField(label='Usuario del administrador', max_length=150, initial='admin')
    nombre = forms.CharField(label='Nombres y apellidos', max_length=150)
    email = forms.EmailField(label='Correo')
    clave1 = forms.CharField(label='Contraseña', widget=forms.PasswordInput)
    clave2 = forms.CharField(label='Repetir contraseña', widget=forms.PasswordInput)

    def clean_ruc(self):
        ruc = self.cleaned_data['ruc'].strip()
        if error_ruc(ruc):
            raise forms.ValidationError(error_ruc(ruc))
        return ruc

    def clean(self):
        datos = super().clean()
        if datos.get('clave1') != datos.get('clave2'):
            self.add_error('clave2', 'Las contraseñas no coinciden.')
        elif datos.get('clave1'):
            try:
                validate_password(datos['clave1'])
            except forms.ValidationError as exc:
                self.add_error('clave1', exc)
        return datos


def instalar(request):
    if not pendiente():
        raise Http404
    if not permitido(request):
        puede_solicitar = bool(os.environ.get('CLAVE_INSTALACION') and os.environ.get('CORREO_INSTALACION'))
        return render(request, 'registration/instalacion.html',
                      {'bloqueado': True, 'puede_solicitar': puede_solicitar}, status=403)
    form = InstalacionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        from django.db import transaction

        from erp.empresas import activar, restaurar

        from .models import Empresa
        d = form.cleaned_data
        token = activar('default')
        try:
            with transaction.atomic(using='default'):
                if not pendiente():  # otro lo completó mientras tanto
                    raise Http404
                e = Empresa.actual()
                e.ruc, e.razon_social = d['ruc'], d['razon_social'].strip().upper()
                if d.get('direccion'):
                    e.direccion = d['direccion']
                e.save()
                partes = d['nombre'].strip().split(' ', 1)
                u = get_user_model().objects.create_superuser(
                    d['usuario'].strip(), d['email'], d['clave1'], first_name=partes[0],
                    last_name=partes[1] if len(partes) > 1 else '')
            from django.core.cache import cache
            cache.delete('razon_social_default')
            auth_login(request, u, backend='django.contrib.auth.backends.ModelBackend')
            request.session['empresa'] = 'default'
            from .grupo import marcar
            marcar(request, u, 'default')
        finally:
            restaurar(token)
        return redirect('grupo_panel')
    return render(request, 'registration/instalacion.html', {'form': form})


class SolicitarClaveForm(BootstrapMixin, forms.Form):
    correo = forms.EmailField(label='Correo autorizado para esta instalación')


def solicitar_clave(request):
    """«Volver a solicitar clave»: envía la clave de instalación al correo autorizado (variable de entorno
    CORREO_INSTALACION). Si el correo escrito no es ese, pide ingresar el correcto; la clave solo viaja a ese correo,
    así que confirmar cuál es no la expone. Máximo 5 pedidos por hora desde la misma dirección."""
    if not pendiente():
        raise Http404
    clave = os.environ.get('CLAVE_INSTALACION', '')
    autorizado = os.environ.get('CORREO_INSTALACION', '').strip().lower()
    if not clave or not autorizado:
        raise Http404
    form = SolicitarClaveForm(request.POST or None)
    enviado = False
    if request.method == 'POST' and form.is_valid():
        from django.core.cache import cache

        from . import correo
        from .acceso import _ip
        llave = f'instalacion-clave:{_ip(request)}'
        if cache.get(llave, 0) >= 5:
            form.add_error(None, 'Demasiadas solicitudes. Intente de nuevo en una hora.')
        else:
            cache.set(llave, cache.get(llave, 0) + 1, 3600)
            if form.cleaned_data['correo'].strip().lower() != autorizado:
                form.add_error('correo', 'Ese correo no está autorizado para esta instalación. '
                                         'Ingrese nuevamente el correo correcto.')
            else:
                enlace = request.build_absolute_uri(f"{redirect('instalacion').url}?clave={clave}")
                try:
                    correo.enviar([autorizado], 'Ceiba ERP · Clave de instalación',
                                  'registration/correo_clave_instalacion.html',
                                  {'clave': clave, 'enlace': enlace, 'dominio': request.get_host()})
                    enviado = True
                except correo.ErrorCorreo as exc:
                    import logging
                    logging.getLogger('django.request').error('Clave de instalación sin enviar: %s', exc)
                    form.add_error(None, 'No se pudo enviar el correo. Avise al administrador de Ceiba ERP.')
    return render(request, 'registration/instalacion_solicitar.html', {'form': form, 'enviado': enviado})
