from decimal import Decimal

from django import forms

from core.forms import remoto, BootstrapMixin, validar_periodo_abierto
from core.models import Tercero
from core.sustentos import SustentoField

from .models import Cheque, Cuenta, EntregaRendir, GastoRendicion, Movimiento, Prestamo


MAX_SUSTENTO = 5 * 1024 * 1024
TIPOS_SUSTENTO = ('.pdf', '.png', '.jpg', '.jpeg', '.xlsx', '.xls')


def leer_sustento(archivo):
    """(bytes, nombre, tipo) del documento de sustento o ValidationError."""
    if archivo.size > MAX_SUSTENTO:
        raise forms.ValidationError('El sustento supera los 5 MB.')
    if not archivo.name.lower().endswith(TIPOS_SUSTENTO):
        raise forms.ValidationError('Adjunte un PDF, imagen (JPG/PNG) o Excel.')
    return archivo.read(), archivo.name[:150], (archivo.content_type or '')[:100]


CONFIRMAR_REPETIDO = 'confirmar_repetido'


def confirmar_repetido_field():
    return forms.BooleanField(label='Confirmo que el N° de operación se repite', required=False,
                              help_text='Solo si el banco usó el mismo número en otra operación')


def validar_tesoreria(form, cuenta_campo='cuenta', excluir=None):
    """FIN-11: fecha desde el 2000 y no futura, y aviso del N° de operación ya usado en la misma cuenta (un voucher
    registrado dos veces duplica el pago o la cobranza): se graba solo si el usuario lo confirma."""
    from django.utils import timezone
    d = form.cleaned_data
    fecha = d.get('fecha')
    if fecha and fecha > timezone.localdate():
        form.add_error('fecha', 'No puede ser futura: registre la operación el día que ocurre.')
    elif fecha and fecha.year < 2000:
        form.add_error('fecha', 'Fecha no válida (desde el 2000).')
    cuenta, numero = d.get(cuenta_campo), (d.get('numero_operacion') or '').strip()
    if cuenta and numero and not d.get(CONFIRMAR_REPETIDO):
        otro = (Movimiento.objects.filter(cuenta=cuenta, numero_operacion__iexact=numero, estado='VIGENTE')
                .exclude(pk=getattr(excluir, 'pk', None)).first())
        if otro is not None:
            form.add_error('numero_operacion', f'El N° de operación {numero} ya está en el movimiento {otro.voucher} '
                                               f'del {otro.fecha:%d/%m/%Y} (S/ {otro.monto:,.2f}). Si es correcto, '
                                               f'marque «Confirmo que el N° de operación se repite».')


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
        # FIN-11: número de cuenta único y CCI de 20 dígitos
        numero = (data.get('numero') or '').strip()
        if numero:
            import re
            if not re.fullmatch(r'[\d\-\s]{6,40}', numero):
                self.add_error('numero', 'Solo números y guiones.')
            elif Cuenta.objects.filter(numero=numero, banco=data.get('banco', '')).exclude(pk=self.instance.pk).exists():
                self.add_error('numero', 'Ya existe una cuenta con este número en el mismo banco.')
        cci = (data.get('cci') or '').replace('-', '').replace(' ', '')
        if cci and not (len(cci) == 20 and cci.isdigit()):
            self.add_error('cci', 'El CCI tiene 20 dígitos.')
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
                  'cuenta_contable', 'centro_costo', 'rubro_flujo', 'glosa']

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        from contabilidad.models import RubroFlujo
        self.fields['rubro_flujo'].queryset = RubroFlujo.objects.filter(activo=True)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True)
        remoto(self.fields['tercero'], 'terceros')
        self.fields['concepto'].choices = [c for c in Movimiento.CONCEPTOS if c[0] not in ('COBRANZA', 'PAGO',
                                                                                           'TRANSFERENCIA')]
        self.fields[CONFIRMAR_REPETIDO] = confirmar_repetido_field()

    def clean_monto(self):
        monto = self.cleaned_data['monto']
        if monto <= 0:
            raise forms.ValidationError('El monto debe ser mayor a cero.')
        return monto

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        if data.get('tipo') == 'EGRESO':  # FIN-13 (antes que el saldo: add_error quita el monto de cleaned_data)
            from contabilidad.models import CuentaDefecto
            self._validar_egreso_manual(data, data.get('cuenta_contable') or CuentaDefecto.mapa().get(
                f'mov_{data.get("concepto")}'))
        if data.get('tipo') == 'EGRESO' and data.get('cuenta') and data.get('monto'):
            error = data['cuenta'].error_sobregiro(data['monto'], excluir=self.instance, fecha=data.get('fecha'))
            if error:
                self.add_error('monto', error)
        # gasto (62 a 65 y 68) por la cuenta elegida o la del concepto: el centro de costo es obligatorio
        from contabilidad.models import CuentaDefecto
        from contabilidad.pcge import exige_centro
        cuenta = data.get('cuenta_contable') or CuentaDefecto.mapa().get(f'mov_{data.get("concepto")}')
        if cuenta and exige_centro(cuenta) and not data.get('centro_costo'):
            self.add_error('centro_costo', f'Obligatorio: el movimiento va a la cuenta {cuenta.codigo}, que exige '
                                           f'centro de costo.')
        validar_tesoreria(self, excluir=self.instance)
        elegida = data.get('cuenta_contable')
        if elegida and (elegida.codigo[:3] in ('101', '104')
                        or Cuenta.objects.filter(cuenta_contable=elegida).exists()):  # FIN-11: entre cuentas propias
            self.add_error('cuenta_contable', f'La {elegida.codigo} es caja o banco: para mover dinero entre sus '
                                              f'cuentas use Tesorería › Transferencia.')
        caja = data.get('cuenta')
        if (caja and caja.es_detracciones and data.get('tipo') == 'EGRESO'
                and data.get('concepto') not in ('TRIBUTOS', 'DETRACCION')):
            self.add_error('concepto', 'La cuenta de detracciones del Banco de la Nación solo paga tributos a SUNAT '
                                       '(o se libera a su banco con la solicitud de liberación).')
        return data

    def _validar_egreso_manual(self, data, cuenta):
        """FIN-13: un egreso sin comprobante de compra no va a existencias ni a compras (2x, 60, 61: eso es una
        compra con su factura) y sobre el tope (TES_EGRESO_MANUAL_TOPE) solo lo registra quien aprueba pagos."""
        if cuenta and cuenta.codigo[:1] == '2' or cuenta and cuenta.codigo[:2] in ('60', '61'):
            self.add_error('cuenta_contable', f'La {cuenta.codigo} es de existencias o compras: regístrelo como '
                                              f'compra (con su factura y la recepción en el almacén), no como egreso '
                                              f'manual.')
        if self.usuario is None or not data.get('monto'):
            return
        from core import parametros
        from core.permisos import puede
        tope = parametros.valor('TES_EGRESO_MANUAL_TOPE', data.get('fecha'))
        monto = data['monto']
        if data.get('cuenta') and data['cuenta'].moneda == 'USD':
            from core.tipo_cambio import venta_del_dia
            monto = monto * (venta_del_dia(data.get('fecha')) or 1)
        if tope and monto > tope and not puede(self.usuario, 'tesoreria.aprobar_pagos'):
            self.add_error('monto', f'Un egreso manual de más de S/ {tope:,.2f} lo registra la gerencia (permiso '
                                    f'«Aprobar la propuesta de pagos») o va por una compra y la propuesta de pagos.')


class OperacionForm(BootstrapMixin, forms.Form):
    """Cabecera de cobranzas / pagos (individuales o grupales)."""
    cuenta = forms.ModelChoiceField(Cuenta.objects.filter(activo=True))
    fecha = forms.DateField()
    medio_pago = forms.ChoiceField(choices=Movimiento.MEDIOS, initial='TRANSFERENCIA')
    numero_operacion = forms.CharField(label='N° operación / cheque', required=False)
    es_detraccion = forms.BooleanField(label='Es depósito de detracción', required=False)
    glosa = forms.CharField(required=False)
    sustento = SustentoField(required=False, label='Sustento (voucher, constancia de detracción)')
    confirmar_repetido = confirmar_repetido_field()

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        validar_tesoreria(self)
        return data


class OperacionMultipleForm(BootstrapMixin, forms.Form):
    """Cabecera de la cobranza / pago múltiple: la cuenta del saldo y la de las detracciones."""
    cuenta = forms.ModelChoiceField(Cuenta.objects.filter(activo=True, es_detracciones=False),
                                    label='Cuenta (saldos)')
    cuenta_detraccion = forms.ModelChoiceField(Cuenta.objects.filter(activo=True), required=False,
                                               label='Cuenta (detracciones)')
    fecha = forms.DateField()
    medio_pago = forms.ChoiceField(choices=Movimiento.MEDIOS, initial='TRANSFERENCIA')
    numero_operacion = forms.CharField(label='N° operación (saldos)', required=False)
    glosa = forms.CharField(required=False)
    sustento = SustentoField(required=False, label='Sustento (voucher, constancias)')
    confirmar_repetido = confirmar_repetido_field()

    def __init__(self, *args, modo='pago', **kwargs):
        super().__init__(*args, **kwargs)
        if modo == 'cobranza':  # el cliente deposita la detracción en la cuenta del BN de la empresa
            self.fields['cuenta_detraccion'].queryset = Cuenta.objects.filter(activo=True, es_detracciones=True)
            self.fields['cuenta_detraccion'].help_text = 'Su cuenta de detracciones del Banco de la Nación'
        else:  # la empresa deposita desde su banco en la cuenta del BN del proveedor
            self.fields['cuenta_detraccion'].queryset = Cuenta.objects.filter(activo=True, es_detracciones=False,
                                                                              moneda='PEN')
            self.fields['cuenta_detraccion'].help_text = 'Desde la que paga las detracciones (en soles)'

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        validar_tesoreria(self)
        return data


class TransferenciaForm(BootstrapMixin, forms.Form):
    origen = forms.ModelChoiceField(Cuenta.objects.filter(activo=True), label='Cuenta origen')
    destino = forms.ModelChoiceField(Cuenta.objects.filter(activo=True), label='Cuenta destino')
    fecha = forms.DateField()
    monto = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    numero_operacion = forms.CharField(label='N° operación', required=False)
    glosa = forms.CharField(required=False)
    sustento = SustentoField(required=False, label='Sustento (voucher de la transferencia o depósito)')
    confirmar_repetido = confirmar_repetido_field()

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
        validar_tesoreria(self, cuenta_campo='origen')
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


class PrestamoForm(BootstrapMixin, forms.ModelForm):
    comision_cuota = forms.DecimalField(label='Comisiones y seguros por cuota', required=False, min_value=0,
                                        max_digits=14, decimal_places=2, initial=0,
                                        help_text='Seguro de desgravamen, portes, etc. (se suman a cada cuota)')

    class Meta:
        model = Prestamo
        fields = ['tipo', 'entidad', 'descripcion', 'referencia', 'moneda', 'monto', 'tasa_anual', 'plazo',
                  'meses_entre_cuotas', 'fecha_desembolso', 'primera_cuota', 'sistema', 'meses_gracia',
                  'tipo_gracia', 'cuotas_dobles', 'linea', 'opcion_compra', 'cuenta', 'cuenta_activo', 'centro_costo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entidad'].queryset = Tercero.objects.filter(activo=True)
        remoto(self.fields['entidad'], 'terceros')
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True)
        from contabilidad.models import CentroCosto, CuentaContable
        self.fields['cuenta_activo'].queryset = CuentaContable.objects.filter(imputable=True, codigo__startswith='32')
        self.fields['centro_costo'].queryset = CentroCosto.para_formulario(self)

    def clean(self):
        data = super().clean()
        if data.get('tipo') == 'PRESTAMO' and data.get('opcion_compra'):
            self.add_error('opcion_compra', 'Solo en leasing.')
        if data.get('monto') is not None and data['monto'] <= 0:
            self.add_error('monto', 'Debe ser mayor a cero.')
        if data.get('plazo') == 0:
            self.add_error('plazo', 'Indique el número de cuotas.')
        return data


class ImportarExtractoForm(BootstrapMixin, forms.Form):
    cuenta = forms.ModelChoiceField(Cuenta.objects.filter(activo=True))
    archivo = forms.FileField(label='Archivo Excel (.xlsx)')
    conciliado = forms.BooleanField(label='Marcar movimientos como conciliados', required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True)
