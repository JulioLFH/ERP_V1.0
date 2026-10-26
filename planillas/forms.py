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
        ('Datos laborales', ['fecha_ingreso', 'fecha_cese', 'motivo_cese', 'puesto', 'cargo', 'tipo', 'regimen',
                             'categoria_civil', 'centro_costo', 'sueldo', 'asignacion_familiar', 'turno', 'usuario']),
        ('Pensiones', ['sistema_pensiones', 'afp', 'comision_afp', 'cuspp']),
        ('Quinta categoría: datos del año antes del sistema', ['quinta_anio', 'quinta_remuneracion_previa',
                                                              'quinta_retencion_previa']),
        ('Cuentas bancarias', ['banco', 'cuenta_sueldo', 'banco_cts', 'cuenta_cts']),
    ]

    # con vínculo vigente, estos datos cambian solo con un movimiento de personal (fecha efectiva, motivo,
    # aprobación e historial)
    POR_MOVIMIENTO = ('sueldo', 'puesto', 'centro_costo', 'cargo', 'fecha_cese', 'motivo_cese')

    def __init__(self, *args, ver_sueldos=True, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        from .models import Puesto
        if not ver_sueldos:  # sin el permiso «ve sueldos» el campo no se muestra ni se cambia (SEG-08: ni sus cuentas)
            for campo in ('sueldo', 'banco', 'cuenta_sueldo', 'banco_cts', 'cuenta_cts'):
                self.fields.pop(campo, None)
        self.fields['afp'].queryset = AFP.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['puesto'].queryset = Puesto.objects.filter(activo=True)
        ocupados = Trabajador.objects.exclude(usuario=None).exclude(pk=self.instance.pk).values('usuario')
        self.fields['usuario'].queryset = get_user_model().objects.exclude(pk__in=ocupados).order_by('username')
        # su sueldo y cargas (62) se reparten por centro de costo
        self.fields['centro_costo'].required = True
        if self.instance.pk and self.instance.fecha_ingreso:
            for campo in [c for c in self.POR_MOVIMIENTO if c in self.fields]:
                self.fields[campo].disabled = True
                self.fields[campo].help_text = 'Se cambia con un movimiento de personal (Planillas › Movimientos).'

    def secciones(self):
        return [(titulo, [self[c] for c in campos if c in self.fields]) for titulo, campos in self.SECCIONES]

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
        if d.get('regimen') == 'CONSTRUCCION' and not d.get('categoria_civil'):
            self.add_error('categoria_civil', 'Indique si es operario, oficial o peón (define su jornal).')
        self._validar_datos(d, doc, num)
        return d

    def _validar_datos(self, d, doc, num):
        """PLA-17: documento, edad, sueldo mínimo, pensiones, cuentas y montos de quinta con valores reales."""
        import re
        from datetime import date

        from django.utils import timezone
        hoy = timezone.localdate()
        if doc in ('04', '07') and not (9 <= len(num) <= 12 and num.isalnum()):
            self.add_error('numero_doc', 'El carné de extranjería o pasaporte tiene de 9 a 12 letras o números.')
        nace, ingreso = d.get('fecha_nacimiento'), d.get('fecha_ingreso')
        if nace and (nace > hoy or nace.year < 1920):
            self.add_error('fecha_nacimiento', 'Fecha de nacimiento no válida.')
        elif nace and ingreso and ingreso <= nace:
            self.add_error('fecha_ingreso', 'No puede ser anterior al nacimiento.')
        elif nace and ingreso and not self.fields['fecha_ingreso'].disabled:
            mayoria = date(nace.year + 18, nace.month, 28 if (nace.month, nace.day) == (2, 29) else nace.day)
            if ingreso < mayoria:
                self.add_error('fecha_nacimiento', 'Tendría menos de 18 años al ingresar: la planilla de Ceiba no '
                                                   'maneja el régimen de adolescentes (autorización del MTPE).')
        if ingreso and (ingreso.year < 1950 or ingreso > hoy.replace(year=hoy.year + 1)):
            self.add_error('fecha_ingreso', 'Fecha de ingreso no válida.')
        sueldo = d.get('sueldo')
        if ('sueldo' in self.fields and not self.fields['sueldo'].disabled and sueldo is not None
                and d.get('regimen') != 'CONSTRUCCION'):
            from .models import Parametro
            p = Parametro.objects.filter(anio__lte=(ingreso or hoy).year).order_by('-anio').first()
            if p is not None and sueldo < p.rmv:
                self.add_error('sueldo', f'Menor a la remuneración mínima vital (S/ {p.rmv:,.2f} en {p.anio}).')
        sistema = d.get('sistema_pensiones')
        if sistema != 'AFP' and d.get('afp'):
            self.add_error('afp', 'Solo para trabajadores del sistema privado (AFP): déjelo vacío.')
        cuspp = (d.get('cuspp') or '').strip()
        if sistema == 'AFP' and cuspp and not (len(cuspp) == 12 and cuspp.isalnum()):
            self.add_error('cuspp', 'El CUSPP tiene 12 letras y números.')
        for campo in ('cuenta_sueldo', 'cuenta_cts'):
            valor = (d.get(campo) or '').strip()
            if campo in self.fields and valor and not re.fullmatch(r'[\d\-\s]{6,30}', valor):
                self.add_error(campo, 'Solo números y guiones.')
        for campo in ('quinta_remuneracion_previa', 'quinta_retencion_previa'):
            if (d.get(campo) or 0) < 0:
                self.add_error(campo, 'No puede ser negativo.')
        if d.get('quinta_anio') and not 2000 <= d['quinta_anio'] <= hoy.year:
            self.add_error('quinta_anio', 'Año no válido.')


class PlanillaForm(BootstrapMixin, forms.ModelForm):
    mes = forms.CharField(label='Periodo', widget=forms.TextInput(attrs={'type': 'month'}))

    class Meta:
        model = Planilla
        fields = ['tipo', 'mes', 'fecha_pago', 'observaciones']
        widgets = {'fecha_pago': forms.DateInput(attrs={'type': 'date'}),
                   'observaciones': forms.Textarea(attrs={'rows': 2})}

    def clean(self):
        d = super().clean()
        from contabilidad.automatico import periodo_valido
        from django.utils import timezone
        periodo = (d.get('mes') or '').replace('-', '')
        # PLA-15 y PLA-16: mes 01-12 (antes «2026-13» daba error 500), desde el 2000 y no futuro
        if not periodo_valido(periodo) or periodo < '200001':
            self.add_error('mes', 'Periodo no válido: elija un mes (AAAA-MM) desde el 2000.')
            return d
        if periodo > timezone.localdate().strftime('%Y%m'):
            self.add_error('mes', 'No se crea una planilla de un mes futuro.')
            return d
        from core.forms import periodo_cerrado
        if periodo_cerrado(periodo):
            self.add_error('mes', f'El periodo contable {periodo[4:]}/{periodo[:4]} está cerrado.')
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
