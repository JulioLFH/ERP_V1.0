from django import forms

from core.forms import BootstrapMixin, remoto
from core.models import Almacen, Producto, Tercero

from .models import (CentroTrabajo, Equipo, InspeccionCalidad, OrdenMantenimiento, ParametroCalidad,
                     PlanMantenimiento, RepuestoOrden)


class InspeccionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = InspeccionCalidad
        fields = ['tipo', 'fecha', 'producto', 'lote', 'cantidad', 'almacen', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(tipo='BIEN', es_plantilla=False)
        remoto(self.fields['producto'], 'productos_bienes')


ParametrosFormSet = forms.inlineformset_factory(
    Producto, ParametroCalidad, fields=['nombre', 'unidad', 'minimo', 'maximo', 'especificacion', 'orden'], extra=3,
    can_delete=True)


class EquipoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Equipo
        fields = ['codigo', 'nombre', 'centro', 'activo_fijo', 'marca_modelo', 'ubicacion', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['centro'].queryset = CentroTrabajo.objects.filter(activo=True)


class PlanForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PlanMantenimiento
        fields = ['tarea', 'frecuencia_dias', 'ultima_fecha']


class OrdenMantenimientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenMantenimiento
        fields = ['equipo', 'tipo', 'fecha_programada', 'descripcion', 'responsable', 'proveedor']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['equipo'].queryset = Equipo.objects.filter(activo=True)
        self.fields['proveedor'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        remoto(self.fields['proveedor'], 'proveedores')


class CierreMantenimientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenMantenimiento
        fields = ['trabajo_realizado', 'responsable', 'horas_parada', 'costo_mano_obra', 'costo_servicios']
        widgets = {'trabajo_realizado': forms.Textarea(attrs={'rows': 2})}


class RepuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = RepuestoOrden
        fields = ['producto', 'cantidad', 'almacen']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(tipo='BIEN', es_plantilla=False)
        remoto(self.fields['producto'], 'productos_bienes')
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True, uso='')

    def clean_cantidad(self):
        c = self.cleaned_data['cantidad']
        if c <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return c
