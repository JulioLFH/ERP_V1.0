from datetime import date

from django import forms

from contabilidad.models import CentroCosto, CuentaContable
from core.forms import remoto, BootstrapMixin
from finanzas.models import Cuenta

from .models import AFP, ConceptoPlanilla, FilaPlanilla, Parametro, Planilla, Trabajador, Vacacion


class TrabajadorForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Trabajador
        fields = '__all__'
        widgets = {f: forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
                   for f in ('fecha_nacimiento', 'fecha_ingreso', 'fecha_cese')}

    SECCIONES = [
        ('Datos personales', ['tipo_doc', 'numero_doc', 'apellido_paterno', 'apellido_materno', 'nombres',
                              'fecha_nacimiento', 'sexo', 'email', 'telefono', 'direccion']),
        ('Datos laborales', ['fecha_ingreso', 'fecha_cese', 'motivo_cese', 'cargo', 'tipo', 'regimen',
                             'centro_costo', 'sueldo', 'asignacion_familiar']),
        ('Pensiones', ['sistema_pensiones', 'afp', 'comision_afp', 'cuspp']),
        ('Quinta categoría: datos del año antes del sistema', ['quinta_anio', 'quinta_remuneracion_previa',
                                                              'quinta_retencion_previa']),
        ('Cuentas bancarias', ['banco', 'cuenta_sueldo', 'banco_cts', 'cuenta_cts']),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['afp'].queryset = AFP.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)

    def secciones(self):
        return [(titulo, [self[c] for c in campos]) for titulo, campos in self.SECCIONES]

    def clean(self):
        d = super().clean()
        doc, num = d.get('tipo_doc'), (d.get('numero_doc') or '').strip()
        if doc == '01' and (len(num) != 8 or not num.isdigit()):
            self.add_error('numero_doc', 'El DNI debe tener 8 dígitos.')
        if d.get('sistema_pensiones') == 'AFP' and not d.get('afp'):
            self.add_error('afp', 'Indique la AFP.')
        if d.get('fecha_cese') and d.get('fecha_ingreso') and d['fecha_cese'] < d['fecha_ingreso']:
            self.add_error('fecha_cese', 'No puede ser anterior al ingreso.')
        if d.get('sueldo') is not None and d['sueldo'] < 0:
            self.add_error('sueldo', 'No puede ser negativo.')
        return d


class PlanillaForm(BootstrapMixin, forms.ModelForm):
    mes = forms.CharField(label='Periodo', widget=forms.TextInput(attrs={'type': 'month'}))

    class Meta:
        model = Planilla
        fields = ['tipo', 'mes', 'fecha_pago', 'observaciones']
        widgets = {'fecha_pago': forms.DateInput(attrs={'type': 'date'}),
                   'observaciones': forms.Textarea(attrs={'rows': 2})}

    def clean(self):
        d = super().clean()
        periodo = (d.get('mes') or '').replace('-', '')
        if len(periodo) != 6 or not periodo.isdigit():
            self.add_error('mes', 'Periodo no válido.')
            return d
        mes, tipo = int(periodo[4:]), d.get('tipo')
        if tipo == 'GRATIFICACION' and mes not in (7, 12):
            self.add_error('mes', 'La gratificación corresponde a julio o diciembre.')
        if tipo == 'CTS' and mes not in (5, 11):
            self.add_error('mes', 'La CTS se deposita en mayo o noviembre.')
        if tipo == 'LIQUIDACION':
            from .calculo import rango
            desde, hasta = rango(periodo)
            if not Trabajador.objects.filter(fecha_cese__range=[desde, hasta]).exists():
                self.add_error('mes', 'Ningún trabajador tiene fecha de cese en ese mes: regístrela en su ficha.')
        if Planilla.objects.filter(tipo=tipo, periodo=periodo).exists():
            self.add_error('mes', 'Ya existe esa planilla.')
        self.instance.periodo = periodo
        return d


class FilaForm(forms.ModelForm):
    # encabezados cortos de la tabla de la planilla
    COLUMNAS = ['Días', 'Faltas', 'Vacac.', 'D.M.', 'Vac. vendidas', 'HE 25%', 'HE 35%', 'Otros afectos',
                'No afectos', 'Adelantos', 'Otros desc.']

    class Meta:
        model = FilaPlanilla
        fields = ['dias_laborados', 'dias_falta', 'dias_vacaciones', 'dias_subsidio', 'dias_vacaciones_vendidas',
                  'horas_extra_25', 'horas_extra_35', 'otros_ingresos', 'ingresos_no_afectos', 'adelantos',
                  'otros_descuentos']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.update({'class': 'form-control form-control-sm text-end px-1', 'step': '0.01',
                                   'min': '0'})
        self.fields['dias_vacaciones_vendidas'].required = False

    def clean(self):
        d = super().clean()
        d['dias_vacaciones_vendidas'] = d.get('dias_vacaciones_vendidas') or 0  # opcional: vacío = 0
        if any((d.get(c) or 0) < 0 for c in self.Meta.fields):
            raise forms.ValidationError('No se aceptan valores negativos.')
        if (d.get('dias_laborados') or 0) > 30:
            self.add_error('dias_laborados', 'Máximo 30.')
        usados = sum((d.get(c) or 0) for c in ('dias_falta', 'dias_vacaciones', 'dias_subsidio'))
        if usados > (d.get('dias_laborados') or 0):
            raise forms.ValidationError('Faltas + vacaciones + descanso médico superan los días del periodo.')
        return d


FilasFormSet = forms.modelformset_factory(FilaPlanilla, form=FilaForm, extra=0)


class FilaLiquidacionForm(forms.ModelForm):
    COLUMNAS = ['Despido arbitrario', 'Otros afectos', 'Adelantos / préstamos', 'Otros desc.']

    class Meta:
        model = FilaPlanilla
        fields = ['despido_arbitrario', 'otros_ingresos', 'adelantos', 'otros_descuentos']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, f in self.fields.items():
            if nombre == 'despido_arbitrario':
                f.widget.attrs.update({'class': 'form-check-input'})
            else:
                f.widget.attrs.update({'class': 'form-control form-control-sm text-end px-1', 'step': '0.01',
                                       'min': '0'})

    def clean(self):
        d = super().clean()
        if any((d.get(c) or 0) < 0 for c in ('otros_ingresos', 'adelantos', 'otros_descuentos')):
            raise forms.ValidationError('No se aceptan valores negativos.')
        return d


LiquidacionFormSet = forms.modelformset_factory(FilaPlanilla, form=FilaLiquidacionForm, extra=0)


class VacacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Vacacion
        fields = ['trabajador', 'tipo', 'fecha_inicio', 'fecha_fin', 'anio_servicio', 'observaciones']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['trabajador'].queryset = Trabajador.objects.filter(fecha_cese__isnull=True)

    def clean(self):
        d = super().clean()
        ini, fin, t = d.get('fecha_inicio'), d.get('fecha_fin'), d.get('trabajador')
        if ini and fin:
            if fin < ini:
                self.add_error('fecha_fin', 'No puede ser anterior al inicio.')
            elif d.get('tipo') == 'VENTA' and (fin - ini).days + 1 > 15:
                self.add_error('fecha_fin', 'Se pueden vender como máximo 15 días por año.')
            elif t and t.fecha_ingreso and ini < t.fecha_ingreso:
                self.add_error('fecha_inicio', 'Es anterior a la fecha de ingreso del trabajador.')
            elif t:
                cruce = Vacacion.objects.filter(trabajador=t, tipo='GOCE', fecha_inicio__lte=fin, fecha_fin__gte=ini)
                if d.get('tipo') == 'GOCE' and cruce.exclude(pk=self.instance.pk).exists():
                    self.add_error('fecha_inicio', 'Se cruza con otras vacaciones registradas.')
        return d


class PagoForm(BootstrapMixin, forms.Form):
    cuenta = forms.ModelChoiceField(Cuenta.objects.none(), label='Pagar desde')
    fecha = forms.DateField(initial=date.today, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True, moneda='PEN')


class ParametroForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Parametro
        fields = '__all__'


class AFPForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AFP
        fields = '__all__'


class ConceptoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ConceptoPlanilla
        fields = ['nombre', 'codigo_plame', 'cuenta', 'cuenta_pasivo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = CuentaContable.objects.filter(imputable=True, activo=True)
        self.fields['cuenta'].queryset = qs
        remoto(self.fields['cuenta'], 'cuentas')
        self.fields['cuenta_pasivo'].queryset = qs.filter(codigo__startswith='4')
        remoto(self.fields['cuenta_pasivo'], 'cuentas')


ParametrosFormSet = forms.modelformset_factory(Parametro, form=ParametroForm, extra=1)
AFPFormSet = forms.modelformset_factory(AFP, form=AFPForm, extra=1)
ConceptosFormSet = forms.modelformset_factory(ConceptoPlanilla, form=ConceptoForm, extra=0)
