from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory, modelformset_factory

from core.forms import BootstrapMixin
from core.models import Tercero
from core.sustentos import SustentoField

from .models import Asiento, AsientoLinea, CentroBeneficio, CentroCosto, CuentaContable, CuentaDefecto, PeriodoContable


def cuentas_imputables():
    return CuentaContable.objects.filter(imputable=True, activo=True)


class CuentaContableForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CuentaContable
        fields = ['codigo', 'nombre', 'naturaleza', 'imputable', 'destino_debe', 'destino_haber', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['destino_debe'].queryset = cuentas_imputables().filter(codigo__regex=r'^(9|2)')
        self.fields['destino_haber'].queryset = cuentas_imputables().filter(codigo__regex=r'^(79|61)')

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip()
        if not codigo.isdigit():
            raise forms.ValidationError('El código solo lleva dígitos.')
        return codigo


class CentroCostoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CentroCosto
        fields = ['codigo', 'nombre', 'tipo', 'padre', 'centro_beneficio', 'responsable', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = CentroCosto.objects.filter(activo=True)
        if self.instance.pk:  # no puede depender de sí mismo ni de uno de sus dependientes (ciclo)
            qs = qs.exclude(pk__in=self.instance.descendientes_ids())
        self.fields['padre'].queryset = qs
        self.fields['centro_beneficio'].queryset = CentroBeneficio.objects.filter(activo=True)
        self.fields['centro_beneficio'].help_text = 'Vacío = el del centro del que depende'


class CentroBeneficioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CentroBeneficio
        fields = ['codigo', 'nombre', 'responsable', 'activo']


class CuentaDefectoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CuentaDefecto
        fields = ['cuenta']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = cuentas_imputables()


CuentaDefectoFormSet = modelformset_factory(CuentaDefecto, form=CuentaDefectoForm, extra=0)


class AsientoForm(BootstrapMixin, forms.ModelForm):
    sustento = SustentoField(help_text='Obligatorio: documento que respalda el asiento (PDF, imagen, Excel; máx. 5 MB)')
    descripcion_sustento = forms.CharField(label='Descripción del sustento', max_length=200, required=False)

    class Meta:
        model = Asiento
        fields = ['fecha', 'libro', 'glosa', 'moneda', 'tipo_cambio']
        widgets = {'moneda': forms.Select(choices=[('PEN', 'Soles'), ('USD', 'Dólares')])}


class ExtornoForm(BootstrapMixin, forms.Form):
    fecha = forms.DateField(label='Fecha del extorno', help_text='Debe estar en un periodo abierto')
    motivo = forms.CharField(label='Motivo del extorno', max_length=200, widget=forms.Textarea(attrs={'rows': 2}))
    sustento = SustentoField(required=False, label='Sustento (opcional)')

    def clean_fecha(self):
        fecha = self.cleaned_data['fecha']
        if PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
            raise forms.ValidationError('El periodo de esta fecha está cerrado.')
        return fecha

    def clean_motivo(self):
        motivo = self.cleaned_data['motivo'].strip()
        if len(motivo) < 10:
            raise forms.ValidationError('Explique el motivo (mínimo 10 caracteres).')
        return motivo

    def clean_fecha(self):
        fecha = self.cleaned_data['fecha']
        if PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
            raise forms.ValidationError('El periodo de esta fecha está cerrado.')
        return fecha


class AsientoLineaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AsientoLinea
        fields = ['cuenta', 'tercero', 'centro_costo', 'documento', 'glosa', 'debe', 'haber']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = cuentas_imputables()
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['debe'].widget.attrs['class'] = 'form-control form-control-sm text-end js-debe'
        self.fields['haber'].widget.attrs['class'] = 'form-control form-control-sm text-end js-haber'
        self.fields['debe'].required = self.fields['haber'].required = False

    def clean(self):
        data = super().clean()
        d, h = data.get('debe') or Decimal('0'), data.get('haber') or Decimal('0')
        data['debe'], data['haber'] = d, h
        if d < 0 or h < 0:
            raise forms.ValidationError('Use importes positivos.')
        if d and h:
            raise forms.ValidationError('Cada línea va al debe o al haber, no a ambos.')
        if data.get('cuenta') and not d and not h:
            raise forms.ValidationError('Ingrese el importe de la línea.')
        return data


class LineasFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        d = h = Decimal('0')
        n = 0
        for f in self.forms:
            if not f.cleaned_data or f.cleaned_data.get('DELETE') or not f.cleaned_data.get('cuenta'):
                continue
            n += 1
            d += f.cleaned_data['debe']
            h += f.cleaned_data['haber']
        if n < 2:
            raise forms.ValidationError('El asiento necesita al menos dos líneas.')
        if d != h:
            raise forms.ValidationError(f'El asiento no cuadra: debe {d:,.2f} / haber {h:,.2f}.')


def lineas_formset(extra=2):
    return inlineformset_factory(Asiento, AsientoLinea, form=AsientoLineaForm, formset=LineasFormSet,
                                 extra=extra, can_delete=True)
