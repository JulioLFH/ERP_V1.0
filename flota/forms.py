from django import forms

from core.forms import BootstrapMixin

from .models import DocumentoFlota


class EquipoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        from produccion.models import Equipo
        model = Equipo
        fields = ['codigo', 'nombre', 'tipo', 'estado', 'placa', 'marca_modelo', 'serie', 'anio', 'combustible',
                  'medidor', 'rendimiento_esperado', 'centro', 'centro_costo', 'activo_fijo', 'responsable',
                  'ubicacion', 'aplica_prestamo', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CentroCosto
        from planillas.models import Trabajador
        from produccion.models import CentroTrabajo
        self.fields['centro'].queryset = CentroTrabajo.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['responsable'].queryset = Trabajador.objects.filter(fecha_cese__isnull=True)

    def clean_placa(self):
        return self.cleaned_data['placa'].replace(' ', '').upper()

    def clean(self):
        datos = super().clean()
        if datos.get('tipo') == 'VEHICULO' and not datos.get('placa'):
            self.add_error('placa', 'Un vehículo necesita su placa (la usan las guías de remisión).')
        return datos


class DocumentoForm(BootstrapMixin, forms.ModelForm):
    archivo_subido = forms.FileField(label='Archivo (PDF o imagen)', required=False)

    class Meta:
        model = DocumentoFlota
        fields = ['tipo', 'numero', 'emisor', 'categoria', 'emision', 'vencimiento', 'costo', 'bloquea']

    def __init__(self, *args, para_conductor=False, **kwargs):
        super().__init__(*args, **kwargs)
        if para_conductor:
            self.fields['tipo'].choices = [c for c in DocumentoFlota.TIPOS if c[0] in ('LICENCIA', 'OTRO')]
        else:
            self.fields['tipo'].choices = [c for c in DocumentoFlota.TIPOS if c[0] != 'LICENCIA']
            del self.fields['categoria']
