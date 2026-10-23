from django import forms
from django.contrib.auth import get_user_model

from core.forms import BootstrapMixin, remoto
from core.models import Tercero

from .models import Obra, Partida, Presupuesto, Recurso


class ObraForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Obra
        fields = ['codigo', 'nombre', 'cliente', 'tipo', 'sistema', 'contrato', 'moneda', 'monto_contratado',
                  'fecha_contrato', 'fecha_inicio', 'plazo_dias', 'direccion', 'ubigeo', 'residente', 'supervisor',
                  'estado', 'gastos_generales_pct', 'utilidad_pct', 'adelanto_directo_pct', 'adelanto_materiales_pct',
                  'retencion_garantia_pct']

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
        fields = ['item', 'descripcion', 'unidad', 'metrado', 'es_titulo', 'precio_fijo', 'precio']

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.presupuesto = presupuesto
        self.fields['metrado'].required = False
        self.fields['precio'].required = False

    def clean_item(self):
        import re
        item = self.cleaned_data['item'].strip().rstrip('.')
        if not re.fullmatch(r'\d{1,3}(\.\d{1,4})*', item):
            raise forms.ValidationError('Ítem con el formato 01, 01.01, 01.01.01…')
        repetido = Partida.objects.filter(presupuesto=self.presupuesto, item=item).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError('Ya existe una partida con ese ítem.')
        return item

    def clean(self):
        data = super().clean()
        data['metrado'] = data.get('metrado') or 0
        data['precio'] = data.get('precio') or 0
        if not data.get('es_titulo') and not data.get('unidad'):
            self.add_error('unidad', 'Indique la unidad de la partida (o márquela como título).')
        return data


class RecursoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Recurso
        fields = ['codigo', 'nombre', 'unidad', 'tipo', 'categoria_mo', 'precio', 'producto', 'equipo',
                  'indice_unificado', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['producto'], 'productos')
        if self.instance.pk:
            self.fields['codigo'].disabled = True


class ImportarForm(BootstrapMixin, forms.Form):
    archivo = forms.FileField(label='Excel del presupuesto (.xlsx)',
                              help_text='Exportado de S10 (hojas de presupuesto y de análisis) o la plantilla de Ceiba')
    tipo = forms.ChoiceField(choices=Presupuesto.TIPOS, initial='OFERTA')
    nombre = forms.CharField(max_length=250, required=False)
