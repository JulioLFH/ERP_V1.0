from django import forms

from core.forms import BootstrapMixin, remoto

from .models import (AsignacionConductor, CargaCombustible, ComponenteEquipo, DocumentoFlota, GastoEquipo,
                     PrestamoEquipo, Viaje)


def _equipos(campo, **filtro):
    from produccion.models import Equipo
    campo.queryset = Equipo.objects.filter(activo=True, **filtro).exclude(estado='BAJA')


def _conductores(campo):
    from logistica.models import Conductor
    campo.queryset = Conductor.objects.filter(activo=True)


class CargaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CargaCombustible
        fields = ['equipo', 'fecha', 'vale', 'grifo', 'conductor', 'tarjeta', 'galones', 'precio_galon', 'lectura',
                  'tanque_lleno', 'centro_costo', 'observacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CentroCosto
        from core.models import Tercero
        _equipos(self.fields['equipo'])
        _conductores(self.fields['conductor'])
        self.fields['grifo'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        remoto(self.fields['grifo'], 'terceros')
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)


class GastoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = GastoEquipo
        fields = ['fecha', 'tipo', 'descripcion', 'documento', 'importe']


class AsignacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AsignacionConductor
        fields = ['conductor', 'desde', 'observacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _conductores(self.fields['conductor'])


class ViajeForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Viaje
        fields = ['equipo', 'conductor', 'salida', 'km_salida', 'origen', 'destino', 'observacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _equipos(self.fields['equipo'])
        _conductores(self.fields['conductor'])


class PrestamoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PrestamoEquipo
        fields = ['equipo', 'cliente', 'entrega', 'devolucion_prevista', 'ubicacion', 'condiciones']
        widgets = {'condiciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Tercero
        _equipos(self.fields['equipo'], aplica_prestamo=True)
        self.fields['cliente'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')


class EquipoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        from produccion.models import Equipo
        model = Equipo
        fields = ['codigo', 'nombre', 'tipo', 'estado', 'placa', 'marca_modelo', 'serie', 'anio', 'combustible',
                  'medidor', 'rendimiento_esperado', 'centro', 'centro_costo', 'activo_fijo', 'responsable',
                  'ubicacion', 'aplica_prestamo', 'criticidad', 'ubicacion_tecnica', 'garantia_hasta',
                  'valor_reposicion', 'presupuesto_anual', 'es_instrumento', 'rango_tolerancia', 'calibracion_dias',
                  'activo']
        widgets = {'garantia_hasta': forms.DateInput(attrs={'type': 'date'})}

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


class ComponenteForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ComponenteEquipo
        fields = ['nivel', 'padre', 'codigo', 'nombre', 'repuesto', 'cantidad_repuesto', 'fecha_ultimo_cambio',
                  'lectura_ultimo_cambio']

    def __init__(self, *args, equipo=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Producto
        self.fields['padre'].queryset = (equipo.componentes.filter(activo=True) if equipo is not None and equipo.pk
                                         else ComponenteEquipo.objects.none())
        self.fields['repuesto'].queryset = Producto.objects.filter(tipo='BIEN', activo=True)
        remoto(self.fields['repuesto'], 'productos_bienes')
        if equipo is None or not equipo.medidor:
            del self.fields['lectura_ultimo_cambio']
