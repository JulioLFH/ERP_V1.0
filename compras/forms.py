from django import forms

from core.forms import BootstrapMixin
from core.models import Tercero

from .models import Compra, OrdenCompra


class CompraForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ['tipo_comprobante', 'serie', 'numero', 'tercero', 'fecha_emision', 'fecha_vencimiento', 'periodo',
                  'clasificacion', 'forma_pago', 'moneda', 'tipo_cambio', 'tipo_operacion', 'detraccion_pct',
                  'retencion_pct', 'percepcion_pct', 'icbper', 'orden_compra', 'doc_referencia',
                  'ingresar_almacen', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2})}
        labels = {'tercero': 'Proveedor'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        self.fields['serie'].required = True
        self.fields['numero'].required = True
        self.fields['orden_compra'].queryset = OrdenCompra.objects.exclude(estado='ANULADO')
        self.fields['doc_referencia'].queryset = Compra.objects.filter(
            estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08'])

    def clean(self):
        data = super().clean()
        if data.get('tipo_comprobante') in ('07', '08') and not data.get('doc_referencia'):
            self.add_error('doc_referencia', 'Indique el comprobante que modifica la nota.')
        return data


class OrdenCompraForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenCompra
        fields = ['tercero', 'fecha', 'fecha_entrega', 'moneda', 'tipo_cambio', 'tipo_operacion',
                  'condicion_pago', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2})}
        labels = {'tercero': 'Proveedor'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])

    def save(self, commit=True):
        if not self.instance.numero:
            from core.models import Serie
            serie, numero = Serie.siguiente('OC', 'OC01')
            self.instance.numero = f'{serie}-{numero}'
        return super().save(commit)
