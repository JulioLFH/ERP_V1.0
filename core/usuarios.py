"""Ajustes > Usuarios y permisos: alta de usuarios y asignación de módulos (grupos)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.shortcuts import get_object_or_404, redirect, render

from .forms import BootstrapMixin
from .modulos import GRUPOS, POR_CLAVE


class UsuarioForm(BootstrapMixin, forms.ModelForm):
    modulos = forms.MultipleChoiceField(
        label='Módulos permitidos', required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(clave, POR_CLAVE[clave]['nombre']) for clave in GRUPOS])
    es_admin = forms.BooleanField(label='Administrador (acceso a todos los módulos)', required=False)
    clave1 = forms.CharField(label='Contraseña', required=False, widget=forms.PasswordInput)
    clave2 = forms.CharField(label='Repetir contraseña', required=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        labels = {'username': 'Usuario', 'first_name': 'Nombres', 'last_name': 'Apellidos', 'is_active': 'Activo'}
        help_texts = {'username': 'Sin espacios. Con este nombre ingresa al sistema.'}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        self.fields['modulos'].widget.attrs.pop('class', None)
        if self.instance.pk:
            nombres = set(self.instance.groups.values_list('name', flat=True))
            self.initial['modulos'] = [c for c, n in GRUPOS.items() if n in nombres]
            self.initial['es_admin'] = self.instance.is_superuser
            self.fields['clave1'].help_text = 'Déjela vacía para no cambiarla.'
        else:
            self.fields['clave1'].required = self.fields['clave2'].required = True

    def clean(self):
        data = super().clean()
        c1, c2 = data.get('clave1'), data.get('clave2')
        if c1 or c2:
            if c1 != c2:
                self.add_error('clave2', 'Las contraseñas no coinciden.')
            else:
                try:
                    validate_password(c1, self.instance)
                except forms.ValidationError as exc:
                    self.add_error('clave1', exc)
        if self.instance.pk and self.instance == self.editor:
            if not data.get('is_active'):
                self.add_error('is_active', 'No puede desactivar su propio usuario.')
            if self.instance.is_superuser and not data.get('es_admin'):
                self.add_error('es_admin', 'No puede quitarse a sí mismo el rol de administrador.')
        if not data.get('es_admin') and not data.get('modulos'):
            self.add_error('modulos', 'Asigne al menos un módulo.')
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_superuser = user.is_staff = bool(self.cleaned_data.get('es_admin'))
        if self.cleaned_data.get('clave1'):
            user.set_password(self.cleaned_data['clave1'])
        user.save()
        grupos = [Group.objects.get_or_create(name=GRUPOS[c])[0] for c in self.cleaned_data.get('modulos', [])]
        otros = user.groups.exclude(name__in=GRUPOS.values())  # conserva grupos ajenos a los módulos
        user.groups.set(list(otros) + grupos)
        return user


@login_required
def lista(request):
    usuarios = User.objects.prefetch_related('groups').order_by('-is_active', 'username')
    filas = []
    for u in usuarios:
        nombres = {g.name for g in u.groups.all()}
        filas.append({'u': u, 'modulos': ['Todos'] if u.is_superuser else
                      [POR_CLAVE[c]['nombre'] for c, n in GRUPOS.items() if n in nombres]})
    return render(request, 'core/usuarios.html', {'filas': filas})


def _guardar(request, usuario, titulo):
    form = UsuarioForm(request.POST or None, instance=usuario, editor=request.user)
    if request.method == 'POST' and form.is_valid():
        u = form.save()
        messages.success(request, f'Usuario {u.username} guardado.')
        return redirect('usuarios')
    return render(request, 'core/usuario_form.html', {'form': form, 'titulo': titulo,
                                                       'modulos_info': [POR_CLAVE[c] for c in GRUPOS]})


@login_required
def nuevo(request):
    return _guardar(request, User(is_active=True), 'Nuevo usuario')


@login_required
def editar(request, pk):
    return _guardar(request, get_object_or_404(User, pk=pk), 'Editar usuario')
