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
    # LUC-01: el archivo del manual se sube al crearlo (PDF o TXT): Lucho lee su texto por secciones
    archivo = forms.FileField(label='Archivo del manual (PDF o TXT)', required=False,
                              help_text='Lucho lee su texto y cita la sección y la página (máx. 5 MB). Los puntos '
                                        'clave quedan como resumen opcional.')

    class Meta:
        model = Manual
        # LUC-07: «Validado» no está aquí: lo marca otra persona con el permiso de aprobar, desde la ficha del manual
        fields = ['titulo', 'tipo', 'proceso', 'equipo', 'puesto', 'material', 'fabricante', 'documento',
                  'contenido', 'activo']
        widgets = {'contenido': forms.Textarea(attrs={'rows': 8})}

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if archivo:
            from core.sustentos import ErrorSustento, validar_archivo
            if not archivo.name.lower().endswith(('.pdf', '.txt')):
                raise forms.ValidationError('Suba el manual en PDF o en texto (.txt).')
            try:
                validar_archivo(archivo)
            except ErrorSustento as exc:
                raise forms.ValidationError(str(exc))
        return archivo

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['contenido'].label = 'Puntos clave (resumen opcional)'
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
