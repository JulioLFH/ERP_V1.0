from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from .models import Almacen, CorreoConfig, Empresa, FacturacionConfig, Producto, Serie, TipoCambio, Tercero


def periodo_cerrado(periodo):
    """True si el periodo contable (AAAAMM) está cerrado."""
    from contabilidad.models import PeriodoContable
    return bool(periodo) and PeriodoContable.esta_cerrado(periodo)


def validar_periodo_abierto(form, campo_fecha, periodo=None):
    fecha = form.cleaned_data.get(campo_fecha)
    periodo = periodo or (fecha.strftime('%Y%m') if fecha else '')
    if periodo_cerrado(periodo):
        form.add_error(campo_fecha, f'El periodo contable {periodo[4:]}/{periodo[:4]} está cerrado.')


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


class UbigeoMixin:
    """Departamento → provincia → distrito en cascada; el ubigeo (6 dígitos) se completa solo.

    Los selectores de departamento y provincia solo ayudan a elegir: el dato que se guarda es el ubigeo
    del distrito, validado contra la tabla del INEI (static/js/app.js los llena desde /ubigeos.json).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'ubigeo' not in self.fields:
            return
        from . import ubigeo
        actual = self.initial.get('ubigeo') or getattr(self.instance, 'ubigeo', '') or ''
        dep = forms.CharField(label='Departamento', required=False,
                              widget=forms.Select(attrs={'class': 'form-select form-select-sm',
                                                         'data-ubigeo-nivel': 'departamento'}))
        prov = forms.CharField(label='Provincia', required=False,
                               widget=forms.Select(attrs={'class': 'form-select form-select-sm',
                                                          'data-ubigeo-nivel': 'provincia'}))
        campo = self.fields['ubigeo']
        campo.label = 'Distrito'
        campo.help_text = (f'Ubigeo {actual}: {ubigeo.descripcion(actual)}' if ubigeo.existe(actual)
                           else 'Elija departamento, provincia y distrito: el ubigeo se completa solo')
        opciones = [('', '---------')] + ([(actual, ubigeo.tabla()[actual][2])] if ubigeo.existe(actual) else [])
        campo.widget = forms.Select(choices=opciones, attrs={
            'class': 'form-select form-select-sm', 'data-ubigeo-nivel': 'distrito', 'data-valor': actual})
        orden = []
        for nombre in self.fields:
            if nombre == 'ubigeo':
                orden += ['ubigeo_departamento', 'ubigeo_provincia']
            orden.append(nombre)
        self.fields['ubigeo_departamento'], self.fields['ubigeo_provincia'] = dep, prov
        self.order_fields(orden)

    def clean_ubigeo(self):
        from . import ubigeo
        valor = (self.cleaned_data.get('ubigeo') or '').strip()
        if valor and not ubigeo.existe(valor):
            raise forms.ValidationError('Ubigeo inexistente: elija el distrito de la lista.')
        return valor


class EmpresaForm(UbigeoMixin, BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Empresa
        fields = '__all__'
        widgets = {'token_tipo_cambio': forms.PasswordInput(render_value=True),
                   'sunat_client_secret': forms.PasswordInput(render_value=True)}

    def clean(self):
        datos = super().clean()
        if datos.get('fuente_tipo_cambio') == 'SBS' and not datos.get('token_tipo_cambio'):
            self.add_error('token_tipo_cambio', 'Para usar el tipo de cambio SBS indique el token de Decolecta.')
        return datos


def digito_ruc(primeros10):
    """Dígito verificador del RUC (módulo 11, pesos de SUNAT)."""
    suma = sum(int(d) * p for d, p in zip(primeros10, (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)))
    resto = 11 - suma % 11
    return {10: 0, 11: 1}.get(resto, resto)


def error_ruc(ruc):
    """Mensaje de error si el RUC no es válido para SUNAT; '' si es correcto."""
    if len(ruc) != 11 or not ruc.isdigit():
        return 'El RUC debe tener 11 dígitos.'
    if ruc[:2] not in ('10', '15', '16', '17', '20'):
        return 'El RUC debe empezar con 10, 15, 16, 17 o 20.'
    if int(ruc[10]) != digito_ruc(ruc[:10]):
        return f'RUC inválido: el dígito verificador debería ser {digito_ruc(ruc[:10])}. Revise el número.'
    return ''


class TerceroForm(UbigeoMixin, BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Tercero
        fields = '__all__'

    def clean(self):
        data = super().clean()
        doc, num = data.get('tipo_doc'), (data.get('numero_doc') or '').strip()
        if doc == '6' and error_ruc(num):
            self.add_error('numero_doc', error_ruc(num))
        if doc == '1' and (len(num) != 8 or not num.isdigit()):
            self.add_error('numero_doc', 'El DNI debe tener 8 dígitos.')
        return data


class ProductoForm(BootstrapMixin, forms.ModelForm):
    # pestañas del formulario (estilo Odoo): (clave, título, icono, campos)
    PESTANAS = [
        ('general', 'General', 'bi-info-circle',
         ['clase', 'codigo', 'nombre', 'unidad', 'marca', 'codigo_barras', 'peso', 'descripcion', 'activo']),
        ('compras', 'Compras', 'bi-bag', ['puede_comprarse', 'precio_compra', 'proveedor', 'unidad_compra']),
        ('ventas', 'Ventas', 'bi-receipt', ['puede_venderse', 'precio_venta']),
        ('contabilidad', 'Contabilidad', 'bi-journal-bookmark',
         ['cuenta_existencias', 'cuenta_compra', 'cuenta_venta', 'cuenta_costo']),
        ('planificacion', 'Planificación', 'bi-calendar-check',
         ['stock_minimo', 'punto_reorden', 'stock_maximo', 'lote_compra', 'tiempo_entrega', 'almacen_defecto']),
    ]

    class Meta:
        model = Producto
        exclude = ['stock', 'costo_promedio']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 2})}

    def clean_peso(self):
        peso = self.cleaned_data.get('peso') or Decimal('0')
        if peso < 0:
            raise forms.ValidationError('No puede ser negativo.')
        return peso

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['peso'].required = False
        from contabilidad.models import CuentaContable
        for campo in ('cuenta_existencias', 'cuenta_compra', 'cuenta_venta', 'cuenta_costo'):
            self.fields[campo].queryset = CuentaContable.objects.filter(imputable=True).order_by('codigo')
            self.fields[campo].help_text = 'Vacío = la del tipo de producto'
        self.fields['proveedor'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        self.fields['almacen_defecto'].queryset = Almacen.objects.filter(activo=True, uso='')
        if self.instance.pk:  # el tipo define el código y el inventario: no se cambia luego de crearlo
            self.fields['clase'].disabled = True
            self.fields['clase'].help_text = 'No se cambia después de creado (define el código y las cuentas)'
            self.fields['codigo'].required = True

    def pestanas(self):
        return [(clave, titulo, icono, [self[c] for c in campos if c in self.fields])
                for clave, titulo, icono, campos in self.PESTANAS]

    def clean(self):
        datos = super().clean()
        clase = datos.get('clase') or self.instance.clase
        if clase != 'ACTIVO' and datos.get('puede_venderse') and datos.get('precio_venta') is None:
            self.add_error('precio_venta', 'Indique el precio de venta.')
        if datos.get('stock_maximo') and datos.get('stock_minimo') and datos['stock_maximo'] < datos['stock_minimo']:
            self.add_error('stock_maximo', 'Debe ser mayor o igual al stock mínimo.')
        return datos


class SerieForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Serie
        fields = '__all__'


class AlmacenForm(UbigeoMixin, BootstrapMixin, forms.ModelForm):
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


class CorreoConfigForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CorreoConfig
        fields = '__all__'
        widgets = {'clave': forms.PasswordInput(render_value=True)}


class AjusteInventarioForm(BootstrapMixin, forms.Form):
    TIPOS = [('ENTRADA', 'Entrada (inventario inicial / sobrante)'), ('SALIDA', 'Salida (merma / faltante / consumo)')]
    producto = forms.ModelChoiceField(Producto.objects.none())
    almacen = forms.ModelChoiceField(Almacen.objects.none(), label='Almacén')
    CONCEPTOS = [('', 'Según el tipo'), ('INICIAL', 'Inventario inicial (apertura)'), ('SOBRANTE', 'Sobrante'),
                 ('MERMA', 'Merma o faltante'), ('CONSUMO', 'Consumo interno')]
    tipo = forms.ChoiceField(choices=TIPOS)
    concepto = forms.ChoiceField(choices=CONCEPTOS, required=False,
                                 help_text='Define la cuenta contable: inicial contra patrimonio (59), sobrante a '
                                           'otros ingresos (75), merma y consumo a gastos (65)')
    cantidad = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    costo_unitario = forms.DecimalField(label='Costo unitario (solo entradas)', max_digits=12, decimal_places=4,
                                        required=False, min_value=0)
    fecha = forms.DateField()
    motivo = forms.CharField(max_length=80)
    sustento = forms.FileField(label='Documento de sustento',
                               help_text='Obligatorio: acta de inventario, informe de merma, vale de consumo… '
                                         '(PDF, imagen, Excel; máx. 5 MB)')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, tipo='BIEN')
        self.initial.setdefault('almacen', Almacen.principal().pk)

    def clean_sustento(self):
        from .sustentos import ErrorSustento, validar_archivo
        archivo = self.cleaned_data['sustento']
        try:
            validar_archivo(archivo)
        except ErrorSustento as exc:
            raise forms.ValidationError(str(exc))
        return archivo

    def clean(self):
        data = super().clean()
        tipo, concepto = data.get('tipo'), data.get('concepto')
        if not concepto:
            data['concepto'] = 'SOBRANTE' if tipo == 'ENTRADA' else 'MERMA'
        elif tipo == 'ENTRADA' and concepto in ('MERMA', 'CONSUMO') or tipo == 'SALIDA' and concepto in (
                'INICIAL', 'SOBRANTE'):
            self.add_error('concepto', 'El concepto no corresponde al tipo de ajuste.')
        if tipo == 'ENTRADA' and data.get('costo_unitario') is None and data.get('producto') \
                and not data['producto'].costo_promedio:
            self.add_error('costo_unitario', 'Indique el costo unitario: el producto aún no tiene costo.')
        from inventario.cierre import error_cierre
        if error_cierre(data.get('fecha')):
            self.add_error('fecha', error_cierre(data.get('fecha')))
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
