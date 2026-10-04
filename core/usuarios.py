"""Ajustes > Usuarios y permisos: alta de usuarios y asignación de módulos (grupos)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.shortcuts import get_object_or_404, redirect, render

from .forms import BootstrapMixin
from .modulos import GRUPO_COSTOS, GRUPOS, POR_CLAVE


class UsuarioForm(BootstrapMixin, forms.ModelForm):
    modulos = forms.MultipleChoiceField(
        label='Módulos permitidos', required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(clave, POR_CLAVE[clave]['nombre']) for clave in GRUPOS])
    es_admin = forms.BooleanField(label='Administrador (acceso a todos los módulos)', required=False)
    ver_costos = forms.BooleanField(
        label='Puede ver costos de inventario', required=False,
        help_text='Costo promedio, valorizado, kardex valorizado y valorización al cierre. Los administradores '
                  'siempre los ven.')
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
            self.initial['ver_costos'] = GRUPO_COSTOS in nombres
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
        from .models import IntentoAcceso
        IntentoAcceso.objects.filter(usuario__iexact=user.username).delete()  # el administrador lo desbloquea
        grupos = [Group.objects.get_or_create(name=GRUPOS[c])[0] for c in self.cleaned_data.get('modulos', [])]
        if self.cleaned_data.get('ver_costos'):
            grupos.append(Group.objects.get_or_create(name=GRUPO_COSTOS)[0])
        # conserva grupos ajenos a los módulos y al permiso de costos
        otros = user.groups.exclude(name__in=list(GRUPOS.values()) + [GRUPO_COSTOS])
        user.groups.set(list(otros) + grupos)
        return user


@login_required
def lista(request):
    usuarios = User.objects.prefetch_related('groups').order_by('-is_active', 'username')
    filas = []
    for u in usuarios:
        nombres = {g.name for g in u.groups.all()}
        filas.append({'u': u, 'modulos': ['Todos'] if u.is_superuser else
                      [POR_CLAVE[c]['nombre'] for c, n in GRUPOS.items() if n in nombres],
                      'ver_costos': u.is_superuser or GRUPO_COSTOS in nombres})
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
    usuario = get_object_or_404(User, pk=pk)
    if request.method == 'POST' and request.POST.get('accion') == 'quitar_2fa':
        # el usuario perdió el teléfono y sus códigos de respaldo: solo un administrador, con motivo
        from .auditoria import registrar
        from .models import SegundoFactor
        motivo = request.POST.get('motivo', '').strip()
        if not request.user.is_superuser:
            messages.error(request, 'Solo un administrador puede quitar la verificación en dos pasos.')
        elif len(motivo) < 10:
            messages.error(request, 'Indique el motivo (mínimo 10 caracteres).')
        else:
            SegundoFactor.objects.filter(usuario=usuario).delete()
            registrar('MODIFICAR', usuario, {'Doble factor': ['Activo', 'Quitado por el administrador']}, motivo)
            messages.success(request, f'Se quitó la verificación en dos pasos de {usuario.username}; '
                                      'deberá activarla de nuevo.')
        return redirect('usuario_editar', pk)
    return _guardar(request, usuario, 'Editar usuario')
