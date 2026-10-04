from django import forms
from django.forms import inlineformset_factory

from core.forms import BootstrapMixin, validar_periodo_abierto
from core.models import Almacen, Producto

from .models import CentroTrabajo, ComponenteLista, ListaMateriales, OperacionLista, OrdenProduccion

FABRICABLES = ['PRODUCTO_TERMINADO', 'SEMIELABORADO']


class CentroTrabajoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CentroTrabajo
        fields = ['codigo', 'nombre', 'costo_hora_mo', 'costo_hora_cif', 'centro_costo', 'activo']


class ListaMaterialesForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ListaMateriales
        fields = ['producto', 'codigo', 'cantidad_base', 'activa', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, clase__in=FABRICABLES)
        self.fields['producto'].help_text = 'Solo productos terminados o semielaborados'

    def clean_cantidad_base(self):
        valor = self.cleaned_data['cantidad_base']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor


class _FilaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, campo in self.fields.items():
            es_select = isinstance(campo.widget, forms.Select)
            campo.widget.attrs['class'] = 'form-select form-select-sm' if es_select else (
                'form-control form-control-sm' + ('' if nombre == 'descripcion' else ' text-end'))


class ComponenteForm(_FilaForm):
    class Meta:
        model = ComponenteLista
        fields = ['producto', 'cantidad', 'merma']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, tipo='BIEN')

    def clean_cantidad(self):
        valor = self.cleaned_data['cantidad']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor

    def clean_merma(self):
        valor = self.cleaned_data['merma']
        if valor is not None and not 0 <= valor < 100:
            raise forms.ValidationError('Entre 0 y 99.99 %.')
        return valor


class OperacionListaForm(_FilaForm):
    class Meta:
        model = OperacionLista
        fields = ['centro', 'descripcion', 'horas']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['centro'].queryset = CentroTrabajo.objects.filter(activo=True)

    def clean_horas(self):
        valor = self.cleaned_data['horas']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor


ComponentesFormSet = inlineformset_factory(ListaMateriales, ComponenteLista, form=ComponenteForm, extra=0,
                                           can_delete=True, min_num=1, validate_min=True)
OperacionesFormSet = inlineformset_factory(ListaMateriales, OperacionLista, form=OperacionListaForm, extra=0,
                                           can_delete=True)


class OrdenProduccionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenProduccion
        fields = ['producto', 'lista', 'cantidad', 'fecha', 'almacen_insumos', 'almacen_destino', 'centro_costo',
                  'glosa']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'glosa': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, clase__in=FABRICABLES,
                                                                   recetas__activa=True).distinct()
        self.fields['lista'].queryset = ListaMateriales.objects.filter(activa=True).select_related('producto')
        self.fields['lista'].help_text = 'Receta vigente del producto'
        normales = Almacen.objects.filter(activo=True, uso='')
        self.fields['almacen_insumos'].queryset = self.fields['almacen_destino'].queryset = normales
        if not self.instance.pk:
            self.initial.setdefault('almacen_insumos', Almacen.principal().pk)
            self.initial.setdefault('almacen_destino', Almacen.principal().pk)

    def clean_cantidad(self):
        valor = self.cleaned_data['cantidad']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor

    def clean(self):
        datos = super().clean()
        validar_periodo_abierto(self, 'fecha')
        producto, lista = datos.get('producto'), datos.get('lista')
        if producto and lista and lista.producto_id != producto.pk:
            self.add_error('lista', f'La lista de materiales no corresponde a {producto.nombre}.')
        return datos
