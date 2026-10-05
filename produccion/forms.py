from django import forms
from django.forms import inlineformset_factory

from core.forms import BootstrapMixin, validar_periodo_abierto
from core.models import Almacen, Producto

from .models import (DIAS, CentroTrabajo, ComponenteLista, HojaRuta, ListaMateriales, OperacionRuta,
                     OrdenProduccion, PlanDemanda, VersionFabricacion)

FABRICABLES = ['PRODUCTO_TERMINADO', 'SEMIELABORADO']


class CentroTrabajoForm(BootstrapMixin, forms.ModelForm):
    dias = forms.MultipleChoiceField(label='Días laborables', choices=DIAS, widget=forms.CheckboxSelectMultiple,
                                     required=False)

    class Meta:
        model = CentroTrabajo
        fields = ['codigo', 'nombre', 'tipo', 'costo_hora_mo', 'costo_hora_cif', 'centro_costo', 'horas_turno',
                  'turnos', 'eficiencia', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dias'].widget.attrs.pop('class', None)
        self.initial['dias'] = list(self.instance.dias_laborables or '123456')

    def clean_eficiencia(self):
        valor = self.cleaned_data['eficiencia']
        if valor is not None and not 0 < valor <= 200:
            raise forms.ValidationError('Entre 1 y 200 %.')
        return valor

    def save(self, commit=True):
        self.instance.dias_laborables = ''.join(sorted(self.cleaned_data.get('dias') or [])) or '123456'
        return super().save(commit)


class ListaMaterialesForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ListaMateriales
        fields = ['producto', 'codigo', 'cantidad_base', 'estado', 'vigente_desde', 'vigente_hasta', 'lote_min',
                  'lote_max', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, clase__in=FABRICABLES)
        self.fields['producto'].help_text = 'Solo productos terminados o semielaborados'

    def clean_cantidad_base(self):
        valor = self.cleaned_data['cantidad_base']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get('vigente_desde'), datos.get('vigente_hasta')
        if desde and hasta and hasta < desde:
            self.add_error('vigente_hasta', 'No puede ser anterior al inicio de la vigencia.')
        if datos.get('lote_min') and datos.get('lote_max') and datos['lote_max'] < datos['lote_min']:
            self.add_error('lote_max', 'Debe ser mayor o igual al lote desde.')
        return datos


class _FilaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, campo in self.fields.items():
            es_select = isinstance(campo.widget, forms.Select)
            campo.widget.attrs['class'] = 'form-select form-select-sm' if es_select else (
                'form-control form-control-sm' + ('' if nombre == 'descripcion' else ' text-end'))


class ComponenteForm(_FilaForm):
    class Meta:
        model = ComponenteLista
        fields = ['producto', 'cantidad', 'merma', 'operacion', 'almacen']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, tipo='BIEN')
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True, uso='')

    def clean_cantidad(self):
        valor = self.cleaned_data['cantidad']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor

    def clean_merma(self):
        valor = self.cleaned_data['merma']
        if valor is not None and not 0 <= valor < 100:
            raise forms.ValidationError('Entre 0 y 99.99 %.')
        return valor


ComponentesFormSet = inlineformset_factory(ListaMateriales, ComponenteLista, form=ComponenteForm, extra=0,
                                           can_delete=True, min_num=1, validate_min=True)


class HojaRutaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = HojaRuta
        fields = ['codigo', 'nombre', 'estado', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}


class OperacionRutaForm(_FilaForm):
    class Meta:
        model = OperacionRuta
        fields = ['secuencia', 'centro', 'descripcion', 'horas_preparacion', 'horas_unidad', 'horas_espera']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['centro'].queryset = CentroTrabajo.objects.filter(activo=True)
        self.fields['descripcion'].widget.attrs['class'] = 'form-control form-control-sm'

    def clean(self):
        datos = super().clean()
        if not self.cleaned_data.get('DELETE') and not (datos.get('horas_preparacion') or datos.get('horas_unidad')):
            self.add_error('horas_unidad', 'Indique horas de preparación o de ejecución.')
        return datos


class _OperacionesBase(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        secuencias = [f.cleaned_data.get('secuencia') for f in self.forms
                      if getattr(f, 'cleaned_data', None) and not f.cleaned_data.get('DELETE')]
        if len(secuencias) != len(set(secuencias)):
            raise forms.ValidationError('Hay números de operación repetidos.')


OperacionesRutaFormSet = inlineformset_factory(HojaRuta, OperacionRuta, form=OperacionRutaForm,
                                               formset=_OperacionesBase, extra=0, can_delete=True, min_num=1,
                                               validate_min=True)


class VersionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = VersionFabricacion
        fields = ['producto', 'codigo', 'descripcion', 'lista', 'hoja', 'lote_min', 'lote_max', 'lote_costeo',
                  'vigente_desde', 'vigente_hasta', 'dias_fabricacion', 'activa']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, clase__in=FABRICABLES)
        self.fields['lista'].queryset = ListaMateriales.objects.exclude(estado='OBSOLETA').select_related('producto')
        self.fields['hoja'].queryset = HojaRuta.objects.exclude(estado='OBSOLETA')

    def clean(self):
        datos = super().clean()
        producto, lista = datos.get('producto'), datos.get('lista')
        if producto and lista and lista.producto_id != producto.pk:
            self.add_error('lista', f'La lista de materiales no es de {producto.nombre}.')
        if datos.get('lote_max') is not None and datos.get('lote_min') is not None and \
                datos['lote_max'] < datos['lote_min']:
            self.add_error('lote_max', 'Debe ser mayor o igual al lote desde.')
        desde, hasta = datos.get('vigente_desde'), datos.get('vigente_hasta')
        if desde and hasta and hasta < desde:
            self.add_error('vigente_hasta', 'No puede ser anterior al inicio de la vigencia.')
        return datos


class OrdenProduccionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenProduccion
        fields = ['producto', 'cantidad', 'fecha', 'version', 'almacen_insumos', 'almacen_destino', 'centro_costo',
                  'glosa']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'glosa': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(
            activo=True, clase__in=FABRICABLES, versiones_fabricacion__activa=True).distinct()
        self.fields['version'].queryset = VersionFabricacion.objects.filter(activa=True).select_related('producto')
        self.fields['version'].required = False
        self.fields['version'].help_text = 'Vacío = la versión vigente para la cantidad y la fecha'
        normales = Almacen.objects.filter(activo=True, uso='')
        self.fields['almacen_insumos'].queryset = self.fields['almacen_destino'].queryset = normales
        if not self.instance.pk:
            self.initial.setdefault('almacen_insumos', Almacen.principal().pk)
            self.initial.setdefault('almacen_destino', Almacen.principal().pk)

    def clean_cantidad(self):
        valor = self.cleaned_data['cantidad']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor

    def clean(self):
        from .servicios import version_para
        datos = super().clean()
        validar_periodo_abierto(self, 'fecha')
        producto, version = datos.get('producto'), datos.get('version')
        cantidad, fecha = datos.get('cantidad'), datos.get('fecha')
        if not (producto and cantidad and fecha):
            return datos
        if version is None:
            version = version_para(producto, cantidad, fecha)
            if version is None:
                self.add_error('version', f'{producto.nombre} no tiene una versión de fabricación vigente para '
                                          f'{cantidad:,.2f} unidades al {fecha:%d/%m/%Y}.')
                return datos
            datos['version'] = version
        elif version.producto_id != producto.pk:
            self.add_error('version', f'La versión no es de {producto.nombre}.')
        elif not version.aplica(cantidad, fecha):
            self.add_error('version', 'La versión no está vigente para esa cantidad o fecha (rango de lote, vigencia '
                                      'o receta/ruta no aprobadas).')
        return datos

    def save(self, commit=True):
        self.instance.lista = self.cleaned_data['version'].lista
        return super().save(commit)


class PlanDemandaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PlanDemanda
        fields = ['producto', 'cantidad', 'fecha', 'nota']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, tipo='BIEN')
