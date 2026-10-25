from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from contabilidad.models import CentroCosto
from core.forms import BootstrapMixin, remoto

from .models import (CategoriaGasto, ConfigRendicion, IndicadorImpuesto, LineaGasto, MapeoCuentaERP,
                     MotivoObservacion, PlantillaDistribucion, PlantillaDistribucionLinea, PoliticaGasto, Proyecto,
                     ReglaAprobacion, Rendicion, Sitio, TarjetaCorporativa)


class MultiplesArchivos(forms.ClearableFileInput):
    allow_multiple_selected = True


class FotosField(forms.FileField):
    """Varias fotos o PDF del comprobante (se validan con las reglas de los sustentos)."""
    widget = MultiplesArchivos

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('label', 'Fotos del comprobante')
        kwargs.setdefault('required', False)
        kwargs.setdefault('help_text', 'Una o varias fotos (o PDF). Desde el celular abre la cámara.')
        super().__init__(*args, **kwargs)
        self.widget.attrs.update({'accept': 'image/*,application/pdf', 'multiple': True})

    def clean(self, data, initial=None):
        from core.sustentos import ErrorSustento, validar_archivo
        archivos = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        for a in archivos:
            try:
                validar_archivo(a)
            except ErrorSustento as exc:
                raise forms.ValidationError(f'{a.name}: {exc}')
        return archivos


class RendicionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Rendicion
        fields = ['entrega', 'descripcion', 'fecha']

    def __init__(self, *args, entregas=None, **kwargs):
        super().__init__(*args, **kwargs)
        if entregas is not None:
            self.fields['entrega'].queryset = entregas
        self.fields['entrega'].label_from_instance = lambda e: (
            f'{e.numero} · {e.responsable.nombre} · {e.motivo[:40]} ({e.simbolo} {e.entregado:,.2f})')


class LineaGastoForm(BootstrapMixin, forms.ModelForm):
    fotos = FotosField()

    class Meta:
        model = LineaGasto
        fields = ['fecha', 'tipo_comprobante', 'ruc', 'razon_social', 'serie', 'numero', 'a_nombre_empresa',
                  'indicador', 'moneda', 'tipo_cambio', 'base', 'igv', 'total', 'kilometros', 'categoria',
                  'centro_costo', 'proyecto', 'sitio', 'destino', 'glosa', 'justificacion']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['indicador'].queryset = IndicadorImpuesto.objects.filter(activo=True)
        self.fields['indicador'].required = True
        self.fields['indicador'].help_text = 'Define el IGV y si da crédito fiscal (registro de compras)'
        self.fields['categoria'].queryset = CategoriaGasto.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['proyecto'].queryset = Proyecto.objects.filter(activo=True)
        self.fields['sitio'].queryset = Sitio.objects.filter(activo=True, proyecto__activo=True).select_related('proyecto')
        self.fields['sitio'].label_from_instance = lambda s: f'{s.proyecto.codigo} › {s}'
        for campo in ('base', 'igv', 'tipo_cambio', 'total', 'kilometros'):
            self.fields[campo].required = False
        if self.instance.pk and self.instance.alertas:
            self.fields['justificacion'].help_text = f'Fuera de política: {self.instance.alertas}'

    def clean_ruc(self):
        return (self.cleaned_data.get('ruc') or '').strip()

    def clean(self):
        d = super().clean()
        d['serie'] = (d.get('serie') or '').strip().upper()
        d['numero'] = (d.get('numero') or '').strip()
        km = d['kilometros'] = d.get('kilometros') or Decimal('0')
        if d.get('tipo_comprobante') == 'KM':
            tarifa = d['categoria'].tarifa_km if d.get('categoria') else Decimal('0')
            if not tarifa:
                self.add_error('categoria', 'Elija una categoría con tarifa por km.')
            if km <= 0:
                self.add_error('kilometros', 'Indique los kilómetros recorridos.')
            d['total'] = (km * tarifa).quantize(Decimal('0.01'))
        elif not d.get('total'):
            self.add_error('total', 'Indique el total.')
        total = d.get('total') or Decimal('0')
        if d.get('sitio') and not d.get('proyecto'):
            d['proyecto'] = d['sitio'].proyecto
        # el indicador de impuesto define el IGV: con tasa, base + IGV salen del total si no se indicaron
        ind = d.get('indicador')
        if ind is not None and ind.tasa > 0:
            if not d.get('base') and not d.get('igv') and total:
                d['base'] = (total / (1 + ind.tasa / 100)).quantize(Decimal('0.01'))
                d['igv'] = total - d['base']
            elif d.get('base') and not d.get('igv'):
                d['igv'] = (d['base'] * ind.tasa / 100).quantize(Decimal('0.01'))
        else:
            d['base'], d['igv'] = total, Decimal('0')
        d['base'], d['igv'] = d.get('base') or Decimal('0'), d.get('igv') or Decimal('0')
        if ind is not None and ind.tasa > 0:
            esperado = (d['base'] * ind.tasa / 100).quantize(Decimal('0.01'))
            if abs(esperado - d['igv']) > Decimal('0.10'):
                self.add_error('igv', f'Con «{ind}» el IGV debe ser {esperado} (base × {ind.tasa}%).')
        if ind is not None and ind.credito_fiscal and d.get('tipo_comprobante') != '01':
            self.add_error('indicador', 'Solo una factura da crédito fiscal.')
        if d.get('moneda') == 'PEN':
            d['tipo_cambio'] = Decimal('1')
        elif not d.get('tipo_cambio') or d['tipo_cambio'] <= 0:
            from core.tipo_cambio import venta_del_dia
            d['tipo_cambio'] = venta_del_dia(d.get('fecha')) if d.get('fecha') else None
            if not d['tipo_cambio']:
                self.add_error('tipo_cambio', 'Indique el tipo de cambio.')
        return d


class CategoriaGastoForm(BootstrapMixin, forms.ModelForm):
    vigente_desde = forms.DateField(label='La nueva cuenta rige desde', required=False,
                                    widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                                    help_text='Solo si cambia la cuenta: los gastos anteriores conservan la cuenta '
                                              'que tenían. Vacío = hoy')
    motivo_cambio = forms.CharField(label='Motivo del cambio de cuenta', required=False, max_length=250)

    class Meta:
        model = CategoriaGasto
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['cuenta_contable'], 'cuentas')
        if not self.instance.pk:
            del self.fields['vigente_desde'], self.fields['motivo_cambio']


class IndicadorImpuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = IndicadorImpuesto
        fields = '__all__'


class MotivoObservacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MotivoObservacion
        fields = '__all__'


class MapeoCuentaERPForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MapeoCuentaERP
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['cuenta'], 'cuentas')


class PlantillaDistribucionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PlantillaDistribucion
        fields = '__all__'


class PlantillaLineaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PlantillaDistribucionLinea
        fields = ['centro_costo', 'porcentaje']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)


class _PlantillaLineas(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        pct = [f.cleaned_data['porcentaje'] for f in self.forms
               if getattr(f, 'cleaned_data', None) and not f.cleaned_data.get('DELETE')
               and f.cleaned_data.get('centro_costo')]
        if pct and sum(pct) != 100:
            raise forms.ValidationError(f'Los porcentajes suman {sum(pct)}%: deben sumar 100%.')


PlantillaLineasFormSet = forms.inlineformset_factory(
    PlantillaDistribucion, PlantillaDistribucionLinea, form=PlantillaLineaForm, formset=_PlantillaLineas, extra=4,
    can_delete=True)


class PoliticaGastoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PoliticaGasto
        fields = '__all__'
        widgets = {'no_permitidas': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['no_permitidas'].widget.attrs.pop('class', None)


class ProyectoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        from core.forms import remoto
        remoto(self.fields['cliente'], 'clientes')


class SitioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Sitio
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['proyecto'].queryset = Proyecto.objects.filter(activo=True)


class TarjetaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TarjetaCorporativa
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Tercero
        self.fields['titular'].queryset = Tercero.objects.filter(activo=True, tipo_doc__in=['1', '4', '7'])
        self.fields['categoria_defecto'].queryset = CategoriaGasto.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        remoto(self.fields['cuenta_contable'], 'cuentas')

    def clean_ultimos4(self):
        v = (self.cleaned_data.get('ultimos4') or '').strip()
        if len(v) != 4 or not v.isdigit():
            raise forms.ValidationError('Los últimos 4 dígitos de la tarjeta.')
        return v


class ConfigRendicionForm(BootstrapMixin, forms.ModelForm):
    SECRETOS = ('ia_api_key', 'whatsapp_token', 'whatsapp_app_secret', 'correo_clave')

    class Meta:
        model = ConfigRendicion
        fields = '__all__'
        # SEG-06: que el navegador no llene estos secretos con el usuario y la clave de Ceiba
        widgets = {**{c: forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}) for c in
                      ('ia_api_key', 'whatsapp_token', 'whatsapp_app_secret', 'correo_clave')},
                   'correo_usuario': forms.TextInput(attrs={'autocomplete': 'off'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria_defecto'].queryset = CategoriaGasto.objects.filter(activo=True)
        for c in self.SECRETOS:
            if getattr(self.instance, c):
                self.fields[c].help_text = 'Registrado. Déjelo vacío para conservarlo.'
        for c in ('dia_corte', 'dias_aviso_corte', 'correo_puerto'):
            self.fields[c].required = False

    def clean_dia_corte(self):
        dia = self.cleaned_data.get('dia_corte') or 0
        if dia > 31:
            raise forms.ValidationError('Un día del mes (1 a 31), o 0 sin corte.')
        return dia

    def clean_dias_aviso_corte(self):
        v = self.cleaned_data.get('dias_aviso_corte')
        return 3 if v is None else v

    def clean_correo_puerto(self):
        return self.cleaned_data.get('correo_puerto') or 993

    def clean(self):
        d = super().clean()
        for c in self.SECRETOS:  # vacío = conservar el que ya estaba
            if not d.get(c):
                d[c] = getattr(self.instance, c)
        return d


class ReglaAprobacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ReglaAprobacion
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['aprobador'].queryset = get_user_model().objects.filter(is_active=True).order_by('username')
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)

    def clean(self):
        d = super().clean()
        if d.get('tipo') == 'USUARIO' and not d.get('aprobador'):
            self.add_error('aprobador', 'Elija el usuario que aprueba.')
        if d.get('tipo') == 'JEFE':
            d['aprobador'] = None
        return d
