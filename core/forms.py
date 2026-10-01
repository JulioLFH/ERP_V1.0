from django import forms
from django.forms import inlineformset_factory

from .models import Empresa, Producto, Serie, Tercero


class BootstrapMixin:
    """Aplica clases Bootstrap a todos los widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault('class', 'form-check-input')
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault('class', 'form-select form-select-sm')
            else:
                w.attrs.setdefault('class', 'form-control form-control-sm')
            if isinstance(field, forms.DateField):
                w.input_type = 'date'
                w.format = '%Y-%m-%d'


class FechaInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        kwargs.setdefault('format', '%Y-%m-%d')
        super().__init__(**kwargs)


class EmpresaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Empresa
        fields = '__all__'


class TerceroForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Tercero
        fields = '__all__'

    def clean(self):
        data = super().clean()
        doc, num = data.get('tipo_doc'), (data.get('numero_doc') or '').strip()
        if doc == '6' and (len(num) != 11 or not num.isdigit()):
            self.add_error('numero_doc', 'El RUC debe tener 11 dígitos.')
        if doc == '1' and (len(num) != 8 or not num.isdigit()):
            self.add_error('numero_doc', 'El DNI debe tener 8 dígitos.')
        return data


class ProductoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Producto
        exclude = ['stock', 'costo_promedio']


class SerieForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Serie
        fields = '__all__'


class ItemForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        fields = ['producto', 'descripcion', 'cantidad', 'precio_unitario']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True)
        self.fields['producto'].widget.attrs['class'] = 'form-select form-select-sm js-producto'
        self.fields['cantidad'].widget.attrs['class'] = 'form-control form-control-sm text-end js-cantidad'
        self.fields['precio_unitario'].widget.attrs['class'] = 'form-control form-control-sm text-end js-precio'
        self.fields['descripcion'].widget.attrs['class'] = 'form-control form-control-sm js-descripcion'


def item_formset(parent_model, item_model, extra=1):
    return inlineformset_factory(
        parent_model, item_model, form=ItemForm, fk_name='documento',
        extra=extra, can_delete=True, min_num=1, validate_min=True,
    )
