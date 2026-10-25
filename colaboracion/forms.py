from django import forms
from django.utils import timezone

from core.forms import BootstrapMixin, remoto
from core.models import Tercero

from .models import Canal, Evento, Pendiente, Proyecto, Tarea
from .servicios import usuarios_internos


def _usuarios(campo, vacio=None):
    campo.queryset = usuarios_internos()
    campo.label_from_instance = lambda u: u.get_full_name() or u.username
    if vacio is not None:
        campo.empty_label = vacio


class CanalForm(BootstrapMixin, forms.ModelForm):
    integrantes = forms.ModelMultipleChoiceField(queryset=None, required=False, label='Miembros',
                                                 help_text='En un canal cerrado, solo ellos lo ven')

    class Meta:
        model = Canal
        fields = ['nombre', 'descripcion', 'publico']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _usuarios(self.fields['integrantes'])
        self.fields['integrantes'].widget.attrs['size'] = 8


class EventoForm(BootstrapMixin, forms.ModelForm):
    fecha = forms.DateField(label='Fecha')
    hora_inicio = forms.TimeField(label='Desde', required=False, widget=forms.TimeInput(attrs={'type': 'time'}))
    hora_fin = forms.TimeField(label='Hasta', required=False, widget=forms.TimeInput(attrs={'type': 'time'}))
    fecha_fin = forms.DateField(label='Hasta el día', required=False, help_text='Solo si dura varios días')
    field_order = ['titulo', 'fecha', 'hora_inicio', 'hora_fin', 'todo_el_dia', 'fecha_fin', 'lugar', 'visibilidad',
                   'invitados', 'descripcion']

    class Meta:
        model = Evento
        fields = ['titulo', 'todo_el_dia', 'lugar', 'invitados', 'visibilidad', 'descripcion']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _usuarios(self.fields['invitados'])
        self.fields['invitados'].widget.attrs['size'] = 6
        e = self.instance
        if e.pk:
            ini = timezone.localtime(e.inicio)
            self.initial.update(fecha=ini.date(), hora_inicio=None if e.todo_el_dia else ini.time())
            if e.fin:
                fin = timezone.localtime(e.fin)
                self.initial.update(hora_fin=None if e.todo_el_dia else fin.time(),
                                    fecha_fin=fin.date() if fin.date() != ini.date() else None)

    def clean(self):
        from datetime import datetime, time
        d = super().clean()
        fecha = d.get('fecha')
        if not fecha:
            return d
        todo = d.get('todo_el_dia') or not d.get('hora_inicio')
        ini = datetime.combine(fecha, time.min if todo else d['hora_inicio'])
        dia_fin = d.get('fecha_fin') or fecha
        if dia_fin < fecha:
            self.add_error('fecha_fin', 'Debe ser igual o posterior a la fecha.')
            return d
        fin = None
        if d.get('hora_fin') and not todo:
            fin = datetime.combine(dia_fin, d['hora_fin'])
        elif dia_fin != fecha or todo:
            fin = datetime.combine(dia_fin, time(23, 59))
        if fin and fin < ini:
            self.add_error('hora_fin', 'Debe ser posterior al inicio.')
        self.instance.todo_el_dia = bool(todo)
        self.instance.inicio = timezone.make_aware(ini)
        self.instance.fin = timezone.make_aware(fin) if fin else None
        return d


class PendienteForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Pendiente
        fields = ['titulo', 'fecha', 'prioridad', 'asignado_a', 'detalle', 'enlace']
        widgets = {'detalle': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _usuarios(self.fields['asignado_a'])

    def clean_enlace(self):
        enlace = (self.cleaned_data.get('enlace') or '').strip()
        if enlace and not enlace.startswith('/'):  # solo pantallas del propio sistema (sin enlaces externos)
            raise forms.ValidationError('Use la dirección de una pantalla del sistema, que empieza con «/».')
        return enlace


class ProyectoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = ['nombre', 'cliente', 'obra', 'responsable', 'miembros', 'fecha_inicio', 'fecha_fin', 'estado',
                  'descripcion']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _usuarios(self.fields['responsable'], 'Sin jefe de proyecto')
        _usuarios(self.fields['miembros'])
        self.fields['miembros'].widget.attrs['size'] = 8
        self.fields['cliente'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')

    def clean(self):
        d = super().clean()
        if d.get('fecha_inicio') and d.get('fecha_fin') and d['fecha_fin'] < d['fecha_inicio']:
            self.add_error('fecha_fin', 'Debe ser posterior al inicio.')
        return d


class TareaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Tarea
        fields = ['titulo', 'responsable', 'fecha_limite', 'prioridad', 'etapa', 'horas_estimadas', 'descripcion']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _usuarios(self.fields['responsable'], 'Sin responsable')
