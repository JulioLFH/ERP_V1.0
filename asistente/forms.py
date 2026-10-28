from django import forms

from core.forms import BootstrapMixin, remoto

from .models import ConfigAsistente, Defecto, FichaParametros, Manual, ParametroProceso, Proceso
from .servicios import MAQUINAS


def _equipos(tipos):
    from produccion.models import Equipo
    return Equipo.objects.filter(activo=True, tipo__in=tipos).order_by('codigo')


class FichaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = FichaParametros
        fields = ['producto', 'equipo', 'molde', 'proceso', 'nota']
        widgets = {'nota': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['equipo'].queryset = _equipos(MAQUINAS)
        self.fields['molde'].queryset = _equipos(('MOLDE',))
        self.fields['proceso'].queryset = Proceso.objects.filter(activo=True)
        remoto(self.fields['producto'], 'productos')


class ManualForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Manual
        fields = ['titulo', 'tipo', 'proceso', 'equipo', 'puesto', 'material', 'fabricante', 'documento',
                  'contenido', 'validado', 'activo']
        widgets = {'contenido': forms.Textarea(attrs={'rows': 8})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from produccion.models import CentroTrabajo, Equipo
        self.fields['equipo'].queryset = Equipo.objects.filter(activo=True).order_by('codigo')
        self.fields['puesto'].queryset = CentroTrabajo.objects.filter(activo=True)
        self.fields['proceso'].queryset = Proceso.objects.filter(activo=True)
        remoto(self.fields['material'], 'productos')
        remoto(self.fields['fabricante'], 'proveedores')


class ProcesoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Proceso
        fields = ['codigo', 'nombre', 'descripcion', 'puestos', 'activo']
        widgets = {'puestos': forms.SelectMultiple(attrs={'size': 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from produccion.models import CentroTrabajo
        self.fields['puestos'].queryset = CentroTrabajo.objects.filter(activo=True)
        self.fields['puestos'].required = False

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip().upper()


class ParametroForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ParametroProceso
        fields = ['nombre', 'unidad', 'orden']


class DefectoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Defecto
        fields = ['codigo', 'nombre', 'descripcion', 'sinonimos']

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip().upper()


class ConfigForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ConfigAsistente
        fields = ['umbral_uso_indebido', 'supervisor']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from colaboracion.servicios import usuarios_internos
        self.fields['supervisor'].queryset = usuarios_internos()

    def clean_umbral_uso_indebido(self):
        v = self.cleaned_data['umbral_uso_indebido']
        if not 1 <= v <= 50:
            raise forms.ValidationError('Entre 1 y 50 consultas.')
        return v
