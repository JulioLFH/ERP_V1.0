from django import forms

from core.forms import BootstrapMixin, validar_periodo_abierto
from core.models import Serie, Tercero

from .models import Cotizacion, Venta

SERIES_DEFECTO = {'01': 'F001', '03': 'B001', '07': 'FC01', '08': 'FD01', '12': 'T001', '00': 'NV01'}


class VentaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Venta
        fields = ['tipo_comprobante', 'serie', 'numero', 'tercero', 'fecha_emision', 'fecha_vencimiento',
                  'forma_pago', 'moneda', 'tipo_cambio', 'tipo_operacion', 'detraccion_pct', 'retencion_pct',
                  'percepcion_pct', 'icbper', 'vendedor', 'cotizacion', 'doc_referencia', 'motivo_nota',
                  'descontar_stock', 'almacen', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2})}
        labels = {'tercero': 'Cliente', 'numero': 'Número (vacío = automático)'}
        help_texts = {'serie': 'Vacío = serie por defecto del tipo'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tipo_comprobante'].choices = [c for c in self.fields['tipo_comprobante'].choices
                                                   if c[0] in ('01', '03', '07', '08', '12', '00')]
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        self.fields['serie'].widget.attrs['list'] = 'series-venta'
        self.fields['cotizacion'].queryset = Cotizacion.objects.exclude(estado='ANULADO')
        self.fields['doc_referencia'].queryset = Venta.objects.filter(
            estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08'])
        self.series = Serie.objects.filter(activo=True, tipo__in=['01', '03', '07', '08', '12', '00'])
        if self.instance.pk:
            # no se renumeran comprobantes emitidos
            self.fields['serie'].disabled = True
            self.fields['numero'].disabled = True
            self.fields['tipo_comprobante'].disabled = True

    def clean(self):
        data = super().clean()
        tipo = data.get('tipo_comprobante')
        if tipo in ('07', '08') and not data.get('doc_referencia'):
            self.add_error('doc_referencia', 'Indique el comprobante que modifica la nota.')
        if tipo in ('07', '08') and not data.get('motivo_nota'):
            self.add_error('motivo_nota', 'Indique el motivo de la nota.')
        tercero = data.get('tercero')
        if tipo == '01' and tercero and tercero.tipo_doc != '6':
            self.add_error('tercero', 'Las facturas requieren un cliente con RUC.')
        validar_periodo_abierto(self, 'fecha_emision', self.instance.periodo if self.instance.pk else None)
        return data

    def validate_unique(self):
        # el número se asigna en save() cuando está vacío
        if self.instance.numero:
            super().validate_unique()

    def save(self, commit=True):
        venta = self.instance
        if not venta.pk:
            tipo = venta.tipo_comprobante
            venta.serie = (venta.serie or '').upper() or (
                Serie.objects.filter(tipo=tipo, activo=True).values_list('serie', flat=True).first()
                or SERIES_DEFECTO.get(tipo, 'S001'))
            if not venta.numero:
                venta.serie, venta.numero = Serie.siguiente(tipo, venta.serie)
        return super().save(commit)


class CotizacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Cotizacion
        fields = ['tipo', 'tercero', 'fecha', 'validez_dias', 'vendedor', 'moneda', 'tipo_cambio',
                  'tipo_operacion', 'condicion_pago', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2})}
        labels = {'tercero': 'Cliente'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])

    def save(self, commit=True):
        if not self.instance.numero:
            tipo = self.instance.tipo
            serie, numero = Serie.siguiente(tipo, 'CT01' if tipo == 'COT' else 'PD01')
            self.instance.numero = f'{serie}-{numero}'
        return super().save(commit)
