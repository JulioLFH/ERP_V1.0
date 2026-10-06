"""Inicio de sesión con bloqueo temporal por intentos fallidos."""
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.utils import timezone

from .models import IntentoAcceso

MAX_INTENTOS = 5
MINUTOS_BLOQUEO = 15


def _ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', ''))[:45]


def intentos_recientes(usuario):
    desde = timezone.now() - timedelta(minutes=MINUTOS_BLOQUEO)
    return IntentoAcceso.objects.filter(usuario__iexact=usuario, creado__gte=desde).count()


class LoginSeguroForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        'bloqueado': f'Usuario bloqueado temporalmente por {MAX_INTENTOS} intentos fallidos. Intente de nuevo en '
                     f'{MINUTOS_BLOQUEO} minutos o pida al administrador que cambie su contraseña.',
    }

    def clean(self):
        usuario = (self.data.get('username') or '').strip()
        if usuario and intentos_recientes(usuario) >= MAX_INTENTOS:
            raise forms.ValidationError(self.error_messages['bloqueado'], code='bloqueado')
        try:
            datos = super().clean()
        except forms.ValidationError:
            if usuario:
                IntentoAcceso.objects.create(usuario=usuario[:150], ip=_ip(self.request) if self.request else '')
                restantes = MAX_INTENTOS - intentos_recientes(usuario)
                if restantes <= 0:
                    raise forms.ValidationError(self.error_messages['bloqueado'], code='bloqueado')
                if restantes <= 2:
                    raise forms.ValidationError(f'Usuario o contraseña incorrectos. Le quedan {restantes} intento(s) '
                                                f'antes del bloqueo temporal.', code='invalid_login')
            raise
        IntentoAcceso.objects.filter(usuario__iexact=usuario).delete()
        return datos


def cambiar_empresa(request):
    """Multiempresa: cada empresa tiene sus propios usuarios, así que se cierra la sesión y se ingresa a la otra."""
    from django.contrib.auth import logout
    from django.shortcuts import redirect
    from django.urls import reverse

    from erp.empresas import empresas
    alias = request.POST.get('empresa', 'default')
    if request.method == 'POST':
        logout(request)
    destino = reverse('login')
    return redirect(f'{destino}?empresa={alias}' if alias in empresas() else destino)


class LoginSeguroView(LoginView):
    authentication_form = LoginSeguroForm

    def _empresa(self):
        from erp.empresas import empresas
        alias = self.request.POST.get('empresa') or self.request.GET.get('empresa') or \
            self.request.session.get('empresa') or 'default'
        return alias if alias in empresas() else 'default'

    def get_context_data(self, **kwargs):
        from erp.empresas import empresas, es_multiempresa, razon_social
        ctx = super().get_context_data(**kwargs)
        if es_multiempresa():
            ctx['empresas'] = [(alias, razon_social(alias)) for alias in empresas()]
            ctx['empresa_elegida'] = self._empresa()
        return ctx

    def post(self, request, *args, **kwargs):
        # multiempresa: el usuario se valida en la base de la empresa elegida
        from erp.empresas import activar, restaurar
        token = activar(self._empresa())
        try:
            return super().post(request, *args, **kwargs)
        finally:
            restaurar(token)

    def form_valid(self, form):
        """Con doble factor activo la sesión se abre recién después de validar el código."""
        from django.shortcuts import redirect

        from .doble_factor import SESION_PENDIENTE, tiene_doble_factor
        usuario = form.get_user()
        alias = self._empresa()
        if tiene_doble_factor(usuario):
            self.request.session.cycle_key()
            self.request.session['empresa'] = alias
            self.request.session[SESION_PENDIENTE] = {'id': usuario.pk, 'backend': usuario.backend,
                                                      'next': self.get_redirect_url()}
            return redirect('login_2fa')
        respuesta = super().form_valid(form)
        self.request.session['empresa'] = alias  # después de login(): si cambió de usuario, la sesión se limpió
        return respuesta
