"""Formularios del planeamiento y la gestión financiera (v1.84, FIN-02 a FIN-07)."""
from django import forms
from django.utils import timezone

from core.forms import BootstrapMixin, remoto
from core.models import Tercero

from .models import (Arrendamiento, CartaFianza, CesionFactura, Cuenta, DepositoPlazo, Forward, LineaCredito,
                     PartidaCaja)


def _terceros(campo):
    campo.queryset = Tercero.objects.filter(activo=True)
    remoto(campo, 'terceros')


def _bancos(campo):
    campo.queryset = Cuenta.objects.filter(activo=True, es_detracciones=False)


class PartidaCajaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PartidaCaja
        fields = ['fecha', 'tipo', 'concepto', 'moneda', 'monto', 'escenario', 'repetir_meses', 'notas']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'})}

    def clean_monto(self):
        m = self.cleaned_data['monto']
        if m <= 0:
            raise forms.ValidationError('Debe ser mayor que cero.')
        return m


class PropuestaForm(BootstrapMixin, forms.Form):
    cuenta = forms.ModelChoiceField(label='Banco desde el que se paga', queryset=Cuenta.objects.none())
    fecha = forms.DateField(label='Fecha de pago', initial=timezone.localdate,
                            widget=forms.DateInput(attrs={'type': 'date'}))
    hasta = forms.DateField(label='Vencimientos hasta', widget=forms.DateInput(attrs={'type': 'date'}))
    reserva = forms.DecimalField(label='Saldo mínimo a mantener', min_value=0, initial=0, max_digits=14,
                                 decimal_places=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True, tipo='BANCO', es_detracciones=False)


class LineaCreditoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = LineaCredito
        fields = ['banco', 'tipo', 'moneda', 'limite', 'tasa', 'vencimiento', 'activa', 'notas']
        widgets = {'vencimiento': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _terceros(self.fields['banco'])


class DepositoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = DepositoPlazo
        fields = ['banco', 'cuenta', 'numero', 'moneda', 'monto', 'tasa', 'inicio', 'vencimiento']
        widgets = {'inicio': forms.DateInput(attrs={'type': 'date'}),
                   'vencimiento': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _terceros(self.fields['banco'])
        _bancos(self.fields['cuenta'])


class CesionForm(BootstrapMixin, forms.ModelForm):
    cuenta = forms.ModelChoiceField(label='Banco donde abona la entidad', queryset=Cuenta.objects.none(),
                                    required=False)

    class Meta:
        model = CesionFactura
        fields = ['venta', 'tipo', 'entidad', 'linea', 'fecha', 'codigo_cavali', 'monto', 'interes', 'comision']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from ventas.models import Venta
        self.fields['venta'].queryset = (Venta.objects.filter(estado='REGISTRADO', tipo_comprobante='01')
                                         .select_related('tercero').order_by('-fecha_emision'))
        _terceros(self.fields['entidad'])
        _bancos(self.fields['cuenta'])
        self.fields['linea'].queryset = LineaCredito.objects.filter(activa=True)


class CartaFianzaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CartaFianza
        fields = ['banco', 'beneficiario', 'linea', 'tipo', 'numero', 'moneda', 'monto', 'comision_pct', 'emision',
                  'vencimiento', 'estado', 'notas']
        widgets = {'emision': forms.DateInput(attrs={'type': 'date'}),
                   'vencimiento': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _terceros(self.fields['banco'])
        _terceros(self.fields['beneficiario'])
        self.fields['linea'].queryset = LineaCredito.objects.filter(activa=True)

    def clean(self):
        d = super().clean()
        if d.get('emision') and d.get('vencimiento') and d['vencimiento'] <= d['emision']:
            self.add_error('vencimiento', 'Vence después de la emisión.')
        if d.get('linea') and d.get('monto') and self.instance.pk is None:
            from .financiamiento import validar_uso
            from .operaciones import ErrorTesoreria
            try:
                validar_uso(d['linea'], d['monto'], d.get('moneda'))
            except ErrorTesoreria as exc:
                self.add_error('linea', str(exc))
        return d


class ArrendamientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Arrendamiento
        fields = ['arrendador', 'descripcion', 'moneda', 'cuota', 'plazo', 'tasa', 'inicio', 'pago_anticipado',
                  'costos_directos', 'centro_costo']
        widgets = {'inicio': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _terceros(self.fields['arrendador'])


class ForwardForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Forward
        fields = ['banco', 'tipo', 'monto_usd', 'tc_pactado', 'fecha', 'vencimiento', 'estado', 'notas']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}),
                   'vencimiento': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _terceros(self.fields['banco'])
