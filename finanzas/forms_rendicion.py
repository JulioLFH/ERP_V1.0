from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from contabilidad.models import CentroCosto
from core.forms import BootstrapMixin, remoto

from .models import CategoriaGasto, LineaGasto, PoliticaGasto, ReglaAprobacion, Rendicion


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
                  'moneda', 'tipo_cambio', 'base', 'igv', 'total', 'categoria', 'centro_costo', 'proyecto', 'glosa',
                  'justificacion']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria'].queryset = CategoriaGasto.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        for campo in ('base', 'igv', 'tipo_cambio'):
            self.fields[campo].required = False
        if self.instance.pk and self.instance.alertas:
            self.fields['justificacion'].help_text = f'Fuera de política: {self.instance.alertas}'

    def clean_ruc(self):
        return (self.cleaned_data.get('ruc') or '').strip()

    def clean(self):
        d = super().clean()
        d['serie'] = (d.get('serie') or '').strip().upper()
        d['numero'] = (d.get('numero') or '').strip()
        total = d.get('total') or Decimal('0')
        if d.get('tipo_comprobante') == '01':
            if not d.get('base') and not d.get('igv') and total:
                d['base'] = (total / Decimal('1.18')).quantize(Decimal('0.01'))
                d['igv'] = total - d['base']
        else:
            d['base'], d['igv'] = total, Decimal('0')
        d['base'], d['igv'] = d.get('base') or Decimal('0'), d.get('igv') or Decimal('0')
        if d.get('moneda') == 'PEN':
            d['tipo_cambio'] = Decimal('1')
        elif not d.get('tipo_cambio') or d['tipo_cambio'] <= 0:
            from core.tipo_cambio import venta_del_dia
            d['tipo_cambio'] = venta_del_dia(d.get('fecha')) if d.get('fecha') else None
            if not d['tipo_cambio']:
                self.add_error('tipo_cambio', 'Indique el tipo de cambio.')
        return d


class CategoriaGastoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CategoriaGasto
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['cuenta_contable'], 'cuentas')


class PoliticaGastoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PoliticaGasto
        fields = '__all__'
        widgets = {'no_permitidas': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['no_permitidas'].widget.attrs.pop('class', None)


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
