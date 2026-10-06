from decimal import Decimal

from django import forms

from core.forms import remoto, BootstrapMixin, validar_periodo_abierto
from core.models import Tercero
from core.sustentos import SustentoField

from .models import Cheque, Cuenta, EntregaRendir, GastoRendicion, Movimiento


MAX_SUSTENTO = 5 * 1024 * 1024
TIPOS_SUSTENTO = ('.pdf', '.png', '.jpg', '.jpeg', '.xlsx', '.xls')


def leer_sustento(archivo):
    """(bytes, nombre, tipo) del documento de sustento o ValidationError."""
    if archivo.size > MAX_SUSTENTO:
        raise forms.ValidationError('El sustento supera los 5 MB.')
    if not archivo.name.lower().endswith(TIPOS_SUSTENTO):
        raise forms.ValidationError('Adjunte un PDF, imagen (JPG/PNG) o Excel.')
    return archivo.read(), archivo.name[:150], (archivo.content_type or '')[:100]


class CuentaForm(BootstrapMixin, forms.ModelForm):
    """El saldo inicial se registra al crear la cuenta con su sustento; luego solo se regulariza (administrador)."""
    motivo_saldo = forms.CharField(label='Motivo / origen del saldo inicial', max_length=300, required=False,
                                   help_text='Ej. Saldo según extracto BCP al 31/07/2026')
    sustento = forms.FileField(label='Sustento del saldo inicial', required=False,
                               help_text='Extracto bancario, acta de arqueo de caja, etc. (PDF, imagen o Excel, '
                                         'máx. 5 MB). Obligatorio si hay saldo inicial')

    class Meta:
        model = Cuenta
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:  # ya creada: el saldo inicial no se edita aquí
            self.fields['saldo_inicial'].disabled = True
            self.fields['saldo_inicial'].help_text = ('Para corregirlo use "Regularizar saldo inicial" (solo '
                                                      'administradores, con sustento).')
            del self.fields['motivo_saldo'], self.fields['sustento']

    def clean(self):
        data = super().clean()
        inicial = data.get('saldo_inicial')
        admite = data.get('tipo') == 'BANCO' and data.get('permite_sobregiro')
        if inicial is not None and inicial < 0 and not admite:
            self.add_error('saldo_inicial', 'El saldo inicial no puede ser negativo.')
        if not self.instance.pk and inicial:
            if len((data.get('motivo_saldo') or '').strip()) < 10:
                self.add_error('motivo_saldo', 'Indique el motivo u origen del saldo inicial (mínimo 10 caracteres).')
            if not data.get('sustento'):
                self.add_error('sustento', 'Adjunte el documento que sustenta el saldo inicial.')
            else:
                try:
                    self.sustento_leido = leer_sustento(data['sustento'])
                except forms.ValidationError as exc:
                    self.add_error('sustento', exc)
        return data

    def save(self, commit=True):
        nueva = not self.instance.pk
        cuenta = super().save(commit)
        if nueva and cuenta.saldo_inicial and getattr(self, 'sustento_leido', None):
            from .models import SaldoInicialCambio
            datos, nombre, tipo = self.sustento_leido
            SaldoInicialCambio.objects.create(cuenta=cuenta, anterior=0, nuevo=cuenta.saldo_inicial,
                                              motivo=self.cleaned_data['motivo_saldo'].strip(), sustento=datos,
                                              sustento_nombre=nombre, sustento_tipo=tipo,
                                              usuario=getattr(self, 'usuario', None))
        return cuenta


class RegularizarSaldoForm(BootstrapMixin, forms.Form):
    nuevo = forms.DecimalField(label='Nuevo saldo inicial', max_digits=14, decimal_places=2)
    motivo = forms.CharField(label='Motivo de la regularización', max_length=300, widget=forms.Textarea(
        attrs={'rows': 2}), help_text='Explique por qué cambia y qué documento lo respalda')
    sustento = forms.FileField(label='Documento de sustento', help_text='PDF, imagen o Excel, máx. 5 MB')

    def __init__(self, *args, cuenta=None, **kwargs):
        self.cuenta = cuenta
        super().__init__(*args, **kwargs)

    def clean_motivo(self):
        motivo = self.cleaned_data['motivo'].strip()
        if len(motivo) < 15:
            raise forms.ValidationError('Explique el motivo (mínimo 15 caracteres).')
        return motivo

    def clean_sustento(self):
        self.sustento_leido = leer_sustento(self.cleaned_data['sustento'])
        return self.cleaned_data['sustento']

    def clean(self):
        data = super().clean()
        nuevo, c = data.get('nuevo'), self.cuenta
        if nuevo is None:
            return data
        if nuevo == c.saldo_inicial and c.cambios_saldo_inicial.exists():
            # igual monto solo para documentar un saldo registrado antes de exigir sustento
            self.add_error('nuevo', 'Es igual al saldo inicial actual.')
        if nuevo < 0 and not c.admite_negativo:
            self.add_error('nuevo', 'El saldo inicial no puede ser negativo.')
        elif not c.admite_negativo:
            negativo = c.primer_negativo(saldo_inicial=nuevo)
            if negativo:
                self.add_error('nuevo', f'Con este saldo inicial la cuenta quedaría en {c.simbolo} {negativo[1]:,.2f} '
                                        f'el {negativo[0]:%d/%m/%Y}.')
        from contabilidad.automatico import fecha_inicio
        from contabilidad.models import PeriodoContable
        periodo = fecha_inicio().strftime('%Y%m')
        if PeriodoContable.esta_cerrado(periodo):
            self.add_error(None, f'El periodo contable de apertura ({periodo[4:]}/{periodo[:4]}) está cerrado: el '
                                 f'saldo inicial no se puede modificar. Registre la corrección con un asiento o un '
                                 f'movimiento de caja/banco en un periodo abierto.')
        return data


class MovimientoForm(BootstrapMixin, forms.ModelForm):
    """Ingresos y egresos sin comprobante (gastos, caja chica, préstamos...): el sustento es obligatorio."""
    sustento = SustentoField(help_text='Obligatorio: recibo, boleta, voucher del banco, planilla, etc. '
                                       '(PDF, imagen, Excel; máx. 5 MB)')

    class Meta:
        model = Movimiento
        fields = ['cuenta', 'fecha', 'tipo', 'concepto', 'medio_pago', 'numero_operacion', 'tercero', 'monto',
                  'cuenta_contable', 'centro_costo', 'glosa']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True)
        remoto(self.fields['tercero'], 'terceros')
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
    sustento = SustentoField(required=False, label='Sustento (voucher, constancia de detracción)')

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
    sustento = SustentoField(required=False, label='Sustento (voucher de la transferencia o depósito)')

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
        if not data.get('numero_operacion') and not data.get('sustento'):
            self.add_error('numero_operacion', 'Indique el N° de operación o adjunte el voucher como sustento.')
        if data.get('origen') and data.get('monto'):
            error = data['origen'].error_sobregiro(data['monto'], fecha=data.get('fecha'))
            if error:
                self.add_error('monto', error)
        return data


class AnularMovimientoForm(forms.Form):
    motivo = forms.CharField(max_length=250)

    def clean_motivo(self):
        motivo = self.cleaned_data['motivo'].strip()
        if len(motivo) < 10:
            raise forms.ValidationError('Explique el motivo de la anulación (mínimo 10 caracteres).')
        return motivo


def _cuentas_activas(campo):
    campo.queryset = Cuenta.objects.filter(activo=True)


class EntregaForm(BootstrapMixin, forms.ModelForm):
    """Nueva entrega a rendir o fondo de caja chica: se registra con el egreso que entrega el dinero."""
    cuenta = forms.ModelChoiceField(Cuenta.objects.filter(activo=True), label='Sale de (caja / banco)')
    monto = forms.DecimalField(label='Monto entregado', max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    medio_pago = forms.ChoiceField(choices=Movimiento.MEDIOS, initial='TRANSFERENCIA')
    numero_operacion = forms.CharField(label='N° operación / cheque', required=False)

    class Meta:
        model = EntregaRendir
        fields = ['tipo', 'responsable', 'fecha', 'motivo', 'cuenta', 'monto', 'medio_pago', 'numero_operacion',
                  'monto_fondo', 'centro_costo', 'cuenta_contable']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _cuentas_activas(self.fields['cuenta'])
        self.fields['responsable'].queryset = Tercero.objects.filter(activo=True)
        remoto(self.fields['responsable'], 'terceros')
        remoto(self.fields['cuenta_contable'], 'cuentas', perezoso=True)
        self.fields['monto_fondo'].required = False

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        if data.get('cuenta'):
            self.instance.moneda = data['cuenta'].moneda
        return data


class GastoRendicionForm(BootstrapMixin, forms.ModelForm):
    sustento = SustentoField(required=False, help_text='Foto o PDF del ticket, recibo o planilla de movilidad')

    class Meta:
        model = GastoRendicion
        fields = ['fecha', 'tipo_documento', 'numero_documento', 'proveedor', 'descripcion', 'cuenta_contable',
                  'centro_costo', 'monto']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['cuenta_contable'], 'cuentas')

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        return data


class ChequeForm(BootstrapMixin, forms.ModelForm):
    """Cheque recibido en cartera (p. ej. diferido) antes de depositarlo."""

    class Meta:
        model = Cheque
        fields = ['numero', 'banco_emisor', 'tercero', 'moneda', 'monto', 'fecha_emision', 'fecha_pago', 'cuenta',
                  'glosa']
        labels = {'cuenta': 'Cuenta donde se depositará', 'tercero': 'Cliente (girador)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].required = True
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True)
        remoto(self.fields['tercero'], 'clientes')
        _cuentas_activas(self.fields['cuenta'])

    def clean_monto(self):
        monto = self.cleaned_data['monto']
        if monto <= 0:
            raise forms.ValidationError('El monto debe ser mayor a cero.')
        return monto


class ImportarExtractoForm(BootstrapMixin, forms.Form):
    cuenta = forms.ModelChoiceField(Cuenta.objects.filter(activo=True))
    archivo = forms.FileField(label='Archivo Excel (.xlsx)')
    conciliado = forms.BooleanField(label='Marcar movimientos como conciliados', required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True)
