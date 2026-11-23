"""Pantallas de la API: documentación y claves de acceso. v1.117.2: solo para el administrador (acceso a todos los
módulos), que ve y revoca las claves de todos los usuarios."""
from datetime import date
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .api import ENDPOINTS
from .forms import BootstrapMixin
from .models import ApiToken
from django.utils import timezone


class ApiTokenForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ApiToken
        fields = ['nombre', 'solo_lectura', 'expira']
        widgets = {'expira': forms.DateInput(attrs={'type': 'date'})}

    def clean_expira(self):
        expira = self.cleaned_data.get('expira')
        if expira and expira <= timezone.localdate():
            raise forms.ValidationError('Debe ser una fecha futura.')
        return expira


def solo_administrador(vista):
    @wraps(vista)
    def envuelta(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied('Las claves de API solo las administra el administrador del sistema.')
        return vista(request, *args, **kwargs)
    return envuelta


@login_required
@solo_administrador
def docs(request):
    return render(request, 'core/api_docs.html', {
        'endpoints': ENDPOINTS, 'base': request.build_absolute_uri('/api/v1/')})


@login_required
@solo_administrador
def claves(request):
    form = ApiTokenForm(request.POST or None)
    nueva = None
    if request.method == 'POST' and request.POST.get('revocar'):
        token = get_object_or_404(ApiToken, pk=request.POST['revocar'])
        token.activo = False
        token.save(update_fields=['activo'])
        messages.info(request, f'Clave "{token.nombre}" revocada.')
        return redirect('api_claves')
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        token, nueva = ApiToken.crear(request.user, d['nombre'], d['solo_lectura'], d['expira'])
        form = ApiTokenForm()
    return render(request, 'core/api_claves.html', {
        'form': form, 'nueva': nueva,
        'tokens': ApiToken.objects.select_related('usuario').order_by('-activo', 'usuario__username', 'nombre')})
