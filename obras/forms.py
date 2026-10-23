from django import forms
from django.contrib.auth import get_user_model

from core.forms import BootstrapMixin, remoto
from core.models import Tercero

from .models import Obra, Partida, Presupuesto, Recurso, Subpresupuesto


class ObraForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Obra
        fields = ['codigo', 'nombre', 'cliente', 'tipo', 'sistema', 'contrato', 'moneda', 'monto_contratado',
                  'fecha_contrato', 'fecha_inicio', 'plazo_dias', 'direccion', 'ubigeo', 'area_m2', 'residente',
                  'supervisor', 'estado', 'gastos_generales_pct', 'utilidad_pct', 'adelanto_directo_pct',
                  'adelanto_materiales_pct', 'retencion_garantia_pct']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cliente'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')
        self.fields['residente'].queryset = get_user_model().objects.filter(is_active=True).order_by('username')
        if self.instance.pk:
            self.fields['codigo'].disabled = True


class PresupuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Presupuesto
        fields = ['tipo', 'nombre', 'fecha_precios', 'gastos_generales_pct', 'utilidad_pct', 'igv_pct']


class PieForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Presupuesto
        fields = ['nombre', 'fecha_precios', 'gastos_generales_pct', 'utilidad_pct', 'igv_pct']


class PartidaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Partida
        fields = ['subpresupuesto', 'item', 'descripcion', 'unidad', 'metrado', 'es_titulo', 'precio_fijo', 'precio']

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.presupuesto = presupuesto
        self.fields['metrado'].required = False
        self.fields['precio'].required = False
        subs = Subpresupuesto.objects.filter(presupuesto=presupuesto)
        self.fields['subpresupuesto'].queryset = subs
        if subs.exists():
            self.fields['subpresupuesto'].required = True
            self.fields['subpresupuesto'].empty_label = None
        else:
            self.fields['subpresupuesto'].widget = forms.HiddenInput()

    def clean(self):
        import re
        data = super().clean()
        item = (data.get('item') or '').strip().rstrip('.')
        if item and not re.fullmatch(r'\d{1,3}(\.\d{1,4})*', item):
            self.add_error('item', 'Ítem con el formato 01, 01.01, 01.01.01…')
        elif item:
            data['item'] = item
            repetido = Partida.objects.filter(presupuesto=self.presupuesto, item=item,
                                              subpresupuesto=data.get('subpresupuesto')).exclude(pk=self.instance.pk)
            if repetido.exists():
                self.add_error('item', 'Ya existe una partida con ese ítem.')
        data['metrado'] = data.get('metrado') or 0
        data['precio'] = data.get('precio') or 0
        if not data.get('es_titulo') and not data.get('unidad'):
            self.add_error('unidad', 'Indique la unidad de la partida (o márquela como título).')
        return data


class SubpresupuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Subpresupuesto
        fields = ['codigo', 'nombre', 'es_gastos_generales']

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.presupuesto = presupuesto

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip()
        if Subpresupuesto.objects.filter(presupuesto=self.presupuesto, codigo=codigo).exclude(
                pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe un subpresupuesto con ese código.')
        return codigo


class RecursoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Recurso
        fields = ['codigo', 'nombre', 'unidad', 'tipo', 'categoria_mo', 'precio', 'producto', 'equipo',
                  'indice_unificado', 'es_subanalisis', 'rendimiento', 'jornada', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['producto'], 'productos')
        if self.instance.pk:
            self.fields['codigo'].disabled = True


class ImportarForm(BootstrapMixin, forms.Form):
    archivo = forms.FileField(label='Presupuesto (Excel .xlsx o .xls)',
                              help_text='Exportado de S10 (presupuesto, análisis, reporte del visor) o la plantilla de '
                                        'Ceiba')
    analisis = forms.FileField(label='Análisis de precios unitarios del S10 (opcional)', required=False,
                               help_text='El reporte de APU que el S10 exporta en CSV (.chr o .csv), si el Excel no los '
                                         'trae')
    tipo = forms.ChoiceField(choices=Presupuesto.TIPOS, initial='OFERTA')
    nombre = forms.CharField(max_length=250, required=False)
