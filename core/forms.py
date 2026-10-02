from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from .models import Almacen, Empresa, FacturacionConfig, Producto, Serie, TipoCambio, Tercero


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
        for nombre in ('almacen', 'almacen_origen', 'almacen_destino'):
            if nombre in self.fields:
                self.fields[nombre].queryset = Almacen.objects.filter(activo=True)
        if 'almacen' in self.fields and not self.initial.get('almacen') and not getattr(getattr(self, 'instance', None), 'almacen_id', 1):
            self.initial['almacen'] = Almacen.principal().pk


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


class AlmacenForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Almacen
        fields = '__all__'


class TipoCambioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TipoCambio
        fields = ['fecha', 'compra', 'venta']


class FacturacionConfigForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = FacturacionConfig
        fields = '__all__'
        widgets = {'token': forms.PasswordInput(render_value=True)}


class AjusteInventarioForm(BootstrapMixin, forms.Form):
    TIPOS = [('ENTRADA', 'Entrada (inventario inicial / sobrante)'), ('SALIDA', 'Salida (merma / faltante / consumo)')]
    producto = forms.ModelChoiceField(Producto.objects.none())
    almacen = forms.ModelChoiceField(Almacen.objects.none(), label='Almacén')
    tipo = forms.ChoiceField(choices=TIPOS)
    cantidad = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    costo_unitario = forms.DecimalField(label='Costo unitario (solo entradas)', max_digits=12, decimal_places=4,
                                        required=False, min_value=0)
    fecha = forms.DateField()
    motivo = forms.CharField(max_length=80)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, tipo='BIEN')
        self.initial.setdefault('almacen', Almacen.principal().pk)

    def clean(self):
        data = super().clean()
        p, alm = data.get('producto'), data.get('almacen')
        if data.get('tipo') == 'SALIDA' and p and alm:
            disponible = p.stocks.filter(almacen=alm).values_list('cantidad', flat=True).first() or 0
            if data.get('cantidad') and data['cantidad'] > disponible:
                self.add_error('cantidad', f'Stock disponible en {alm}: {disponible}')
        return data


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
