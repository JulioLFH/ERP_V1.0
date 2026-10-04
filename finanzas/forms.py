from django import forms

from core.forms import BootstrapMixin, validar_periodo_abierto
from core.models import Tercero

from .models import Cuenta, Movimiento


class CuentaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Cuenta
        fields = '__all__'

    def clean(self):
        data = super().clean()
        inicial = data.get('saldo_inicial')
        admite = data.get('tipo') == 'BANCO' and data.get('permite_sobregiro')
        if inicial is not None and inicial < 0 and not admite:
            self.add_error('saldo_inicial', 'El saldo inicial no puede ser negativo.')
        elif inicial is not None and self.instance.pk and not admite:
            negativo = self.instance.primer_negativo(saldo_inicial=inicial)
            if negativo:
                self.add_error('saldo_inicial', f'Con este saldo inicial la cuenta quedaría en '
                                                f'{self.instance.simbolo} {negativo[1]:,.2f} el {negativo[0]:%d/%m/%Y}.')
        return data


class MovimientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Movimiento
        fields = ['cuenta', 'fecha', 'tipo', 'concepto', 'medio_pago', 'numero_operacion', 'tercero', 'monto',
                  'cuenta_contable', 'centro_costo', 'glosa']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True)
        self.fields['concepto'].choices = [c for c in Movimiento.CONCEPTOS if c[0] not in ('COBRANZA', 'PAGO',
                                                                                           'TRANSFERENCIA')]

    def clean_monto(self):
        monto = self.cleaned_data['monto']
        if monto <= 0:
            raise forms.ValidationError('El monto debe ser mayor a cero.')
        return monto

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        if data.get('tipo') == 'EGRESO' and data.get('cuenta') and data.get('monto'):
            error = data['cuenta'].error_sobregiro(data['monto'], excluir=self.instance, fecha=data.get('fecha'))
            if error:
                self.add_error('monto', error)
        return data


class OperacionForm(BootstrapMixin, forms.Form):
    """Cabecera de cobranzas / pagos (individuales o grupales)."""
    cuenta = forms.ModelChoiceField(Cuenta.objects.filter(activo=True))
    fecha = forms.DateField()
    medio_pago = forms.ChoiceField(choices=Movimiento.MEDIOS, initial='TRANSFERENCIA')
    numero_operacion = forms.CharField(label='N° operación / cheque', required=False)
    es_detraccion = forms.BooleanField(label='Es depósito de detracción', required=False)
    glosa = forms.CharField(required=False)

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        return data


class TransferenciaForm(BootstrapMixin, forms.Form):
    origen = forms.ModelChoiceField(Cuenta.objects.filter(activo=True), label='Cuenta origen')
    destino = forms.ModelChoiceField(Cuenta.objects.filter(activo=True), label='Cuenta destino')
    fecha = forms.DateField()
    monto = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    numero_operacion = forms.CharField(label='N° operación', required=False)
    glosa = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['origen'].queryset = Cuenta.objects.filter(activo=True)
        self.fields['destino'].queryset = Cuenta.objects.filter(activo=True)

    def clean(self):
        data = super().clean()
        if data.get('origen') and data.get('origen') == data.get('destino'):
            raise forms.ValidationError('Las cuentas de origen y destino deben ser distintas.')
        if data.get('origen') and data.get('destino') and data['origen'].moneda != data['destino'].moneda:
            raise forms.ValidationError('Las cuentas deben ser de la misma moneda. Para cambiar soles a dólares '
                                        'registre un egreso y un ingreso con el tipo de cambio pactado.')
        validar_periodo_abierto(self, 'fecha')
        if data.get('origen') and data.get('monto'):
            error = data['origen'].error_sobregiro(data['monto'], fecha=data.get('fecha'))
            if error:
                self.add_error('monto', error)
        return data


class ImportarExtractoForm(BootstrapMixin, forms.Form):
    cuenta = forms.ModelChoiceField(Cuenta.objects.filter(activo=True))
    archivo = forms.FileField(label='Archivo Excel (.xlsx)')
    conciliado = forms.BooleanField(label='Marcar movimientos como conciliados', required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True)
