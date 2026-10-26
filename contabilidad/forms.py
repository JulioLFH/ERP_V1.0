from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory, modelformset_factory

from core.forms import remoto, BootstrapMixin
from core.models import Tercero
from core.sustentos import SustentoField

from .models import (Asiento, AsientoLinea, CentroBeneficio, CentroCosto, CuentaContable, CuentaDefecto, PeriodoContable,
                     Presupuesto, PresupuestoLinea)


def cuentas_imputables():
    return CuentaContable.objects.filter(imputable=True, activo=True)


class CuentaContableForm(BootstrapMixin, forms.ModelForm):
    """Cuenta contable con su determinación: nivel (según el código), columna del balance, moneda, tipo de cambio,
    análisis por documento y cuenta analítica (centro de costo). Lo que se deja en «automático» se completa según el
    código (contabilidad/pcge.py)."""
    AUTOMATICO = 'Automático según el código'

    class Meta:
        model = CuentaContable
        fields = ['codigo', 'nombre', 'naturaleza', 'imputable', 'columna_balance', 'moneda', 'tipo_cambio',
                  'analisis', 'centro_costo_regla', 'destino_debe', 'destino_haber', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['destino_debe'].queryset = cuentas_imputables().filter(codigo__regex=r'^(9|2)')
        self.fields['destino_haber'].queryset = cuentas_imputables().filter(codigo__regex=r'^(79|61)')
        for campo in ('columna_balance', 'tipo_cambio', 'centro_costo_regla'):
            self.fields[campo].choices = [('', self.AUTOMATICO)] + list(self.fields[campo].choices)[1:]
        selector = forms.NullBooleanSelect(attrs={'class': 'form-select'})
        selector.choices = [('unknown', self.AUTOMATICO), ('true', 'Sí: cada movimiento lleva el N° de documento'),
                            ('false', 'No')]
        self.fields['analisis'] = forms.NullBooleanField(
            label='Análisis (N° de documento)', required=False, widget=selector,
            help_text=CuentaContable._meta.get_field('analisis').help_text)
        self.fields['codigo'].help_text = ('Nivel según los dígitos: 2 cuenta (nivel 1), 3 subcuenta (2), '
                                           '4 divisionaria (3), 5 subdivisionaria (4)')
        if self.instance.pk:
            self.fields['codigo'].help_text = f'Nivel {self.instance.nivel}: {self.instance.nombre_nivel}'

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip()
        if not codigo.isdigit():
            raise forms.ValidationError('El código solo lleva dígitos.')
        if len(codigo) < 2:
            raise forms.ValidationError('El código tiene al menos 2 dígitos (nivel 1: cuenta).')
        if len(codigo) > 2 and not self.instance.pk:
            # debe colgar de alguna cuenta superior existente (la de un dígito menos o, en planes con auxiliares
            # largos como 10410001, una más arriba)
            superiores = [codigo[:i] for i in range(2, len(codigo))]
            if not CuentaContable.objects.filter(codigo__in=superiores).exists():
                raise forms.ValidationError(f'Primero cree la cuenta {codigo[:2]} (o una de nivel superior a la que '
                                            f'pertenezca esta cuenta).')
        return codigo

    def clean(self):
        data = super().clean()
        if data.get('moneda') == 'USD' and data.get('codigo', '')[:1] in '6789':
            self.add_error('moneda', 'Las cuentas de resultados y de destino se llevan en soles.')
        return data


class CentroCostoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CentroCosto
        fields = ['codigo', 'nombre', 'tipo', 'padre', 'centro_beneficio', 'responsable', 'responsable_usuario',
                  'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        self.fields['responsable_usuario'].queryset = get_user_model().objects.filter(is_active=True).order_by(
            'username')
        qs = CentroCosto.objects.filter(activo=True)
        if self.instance.pk:  # no puede depender de sí mismo ni de uno de sus dependientes (ciclo)
            qs = qs.exclude(pk__in=self.instance.descendientes_ids())
        self.fields['padre'].queryset = qs
        self.fields['centro_beneficio'].queryset = CentroBeneficio.objects.filter(activo=True)
        self.fields['centro_beneficio'].help_text = 'Vacío = el del centro del que depende'

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo']
        return codigo if self.instance.pk and codigo == self.instance.codigo else codigo_simple(codigo)


class CentroBeneficioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CentroBeneficio
        fields = ['codigo', 'nombre', 'responsable', 'activo']

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo']
        return codigo if self.instance.pk and codigo == self.instance.codigo else codigo_simple(codigo)


def codigo_simple(codigo):
    """CON-20: códigos de centros solo con letras, números, guion, punto o guion bajo (antes aceptaba «<b>X</b>»)."""
    import re
    codigo = (codigo or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', codigo):
        raise forms.ValidationError('Solo letras, números y guion (sin espacios ni símbolos).')
    return codigo


class PresupuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Presupuesto
        fields = ['anio', 'nombre', 'estado', 'principal', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def clean_anio(self):
        anio = self.cleaned_data['anio']
        if not 2000 <= anio <= 2100:
            raise forms.ValidationError('Año no válido.')
        return anio


class PresupuestoLineaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PresupuestoLinea
        fields = ['cuenta', 'centro_costo'] + PresupuestoLinea.MESES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = cuentas_imputables().filter(codigo__regex=r'^(6[2-9]|7[0-8]|88)')
        remoto(self.fields['cuenta'], 'cuentas')
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['centro_costo'].required = False
        for m in PresupuestoLinea.MESES:
            self.fields[m].required = False
            self.fields[m].widget.attrs.update({'class': 'form-control form-control-sm text-end px-1', 'step': '0.01'})

    def clean(self):
        data = super().clean()
        for m in PresupuestoLinea.MESES:
            if data.get(m) is None:
                data[m] = Decimal('0')
        return data


class _LineasPresupuesto(BaseInlineFormSet):
    def clean(self):
        super().clean()
        vistos = set()
        for f in self.forms:
            if not getattr(f, 'cleaned_data', None) or f.cleaned_data.get('DELETE') or not f.cleaned_data.get('cuenta'):
                continue
            clave = (f.cleaned_data['cuenta'].pk, getattr(f.cleaned_data.get('centro_costo'), 'pk', None))
            if clave in vistos:
                raise forms.ValidationError(f'La cuenta {f.cleaned_data["cuenta"].codigo} se repite con el mismo '
                                            'centro de costo: súmela en una sola fila.')
            vistos.add(clave)


LineasPresupuestoFormSet = inlineformset_factory(Presupuesto, PresupuestoLinea, form=PresupuestoLineaForm,
                                                 formset=_LineasPresupuesto, extra=1, can_delete=True)


class CuentaDefectoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CuentaDefecto
        fields = ['cuenta']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = cuentas_imputables()
        remoto(self.fields['cuenta'], 'cuentas')


CuentaDefectoFormSet = modelformset_factory(CuentaDefecto, form=CuentaDefectoForm, extra=0)


class AsientoForm(BootstrapMixin, forms.ModelForm):
    sustento = SustentoField(help_text='Obligatorio: documento que respalda el asiento (PDF, imagen, Excel; máx. 5 MB)')
    descripcion_sustento = forms.CharField(label='Descripción del sustento', max_length=200, required=False)

    class Meta:
        model = Asiento
        fields = ['fecha', 'libro', 'glosa', 'moneda', 'tipo_cambio', 'norma']
        widgets = {'moneda': forms.Select(choices=[('PEN', 'Soles'), ('USD', 'Dólares')])}
        labels = {'libro': 'Libro (subdiario)', 'norma': 'Norma (NIIF / tributaria)'}  # CON-20: antes ambos «Libro»

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['norma'].required = False  # lo normal: va a los dos libros

    def clean_norma(self):
        return self.cleaned_data.get('norma') or 'AMBOS'

    def clean(self):
        """CON-18 y CON-19: fecha en un periodo abierto, desde el 2000 y hasta el fin del mes en curso (provisiones de
        cierre); T.C. mayor a cero en dólares."""
        import calendar

        from django.utils import timezone
        data = super().clean()
        fecha, hoy = data.get('fecha'), timezone.localdate()
        fin_mes = hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1])
        if fecha and fecha.year < 2000:
            self.add_error('fecha', 'Fecha no válida (desde el 2000).')
        elif fecha and fecha > fin_mes:
            self.add_error('fecha', f'No puede ser posterior al {fin_mes:%d/%m/%Y} (fin del mes en curso).')
        elif fecha and PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
            self.add_error('fecha', f'El periodo {fecha:%m/%Y} está cerrado: registre el asiento en un periodo abierto.')
        if self.instance.pk and self.instance.periodo and PeriodoContable.esta_cerrado(self.instance.periodo):
            self.add_error(None, f'El asiento está en el periodo cerrado {self.instance.periodo[4:]}/'
                                 f'{self.instance.periodo[:4]}: no se modifica (use un extorno).')
        tc = data.get('tipo_cambio')
        if data.get('moneda', 'PEN') != 'PEN' and not (tc and tc > 0):
            self.add_error('tipo_cambio', 'En dólares indique el tipo de cambio (mayor a cero).')
        elif tc is not None and tc < 0:
            self.add_error('tipo_cambio', 'No puede ser negativo.')
        return data


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
        remoto(self.fields['cuenta'], 'cuentas')
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True)
        remoto(self.fields['tercero'], 'terceros')
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['debe'].widget.attrs['class'] = 'form-control form-control-sm text-end js-debe'
        self.fields['haber'].widget.attrs['class'] = 'form-control form-control-sm text-end js-haber'
        self.fields['debe'].required = self.fields['haber'].required = False

    def clean(self):
        data = super().clean()
        d, h = data.get('debe') or Decimal('0'), data.get('haber') or Decimal('0')
        data['debe'], data['haber'] = d, h
        if d < 0 or h < 0:
            raise forms.ValidationError('Los montos deben ser mayores a cero.')
        if d and h:
            raise forms.ValidationError('Una línea no puede tener debe y haber: use una línea para cada uno.')
        if data.get('cuenta') and not d and not h:
            raise forms.ValidationError('Ingrese el importe de la línea (mayor a cero).')
        from .pcge import admite_centro, exige_centro
        cuenta = data.get('cuenta')
        if cuenta and exige_centro(cuenta) and not data.get('centro_costo'):
            self.add_error('centro_costo', f'La cuenta {cuenta.codigo} exige centro de costo.')
        elif cuenta and data.get('centro_costo') and not admite_centro(cuenta):
            data['centro_costo'] = None  # la cuenta no lleva centro de costo (p. ej. las de balance)
        if cuenta and cuenta.analisis and not (data.get('documento') or '').strip():
            self.add_error('documento', f'La cuenta {cuenta.codigo} se analiza por documento: indique el número.')
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
            raise forms.ValidationError(f'El asiento no cuadra: debe {d:,.2f} / haber {h:,.2f} (diferencia S/ '
                                        f'{abs(d - h):,.2f}).')


def lineas_formset(extra=2):
    return inlineformset_factory(Asiento, AsientoLinea, form=AsientoLineaForm, formset=LineasFormSet,
                                 extra=extra, can_delete=True)
