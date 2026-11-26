from django import forms

from core.forms import BootstrapMixin
from core.models import Tercero

from .models import ClienteLogistico, Consolidado, ConfigOperador, EstadoLogistico, SolicitudRecojo


class SolicitudForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = SolicitudRecojo
        fields = ['cliente', 'almacen', 'bultos', 'contacto', 'fecha_recojo', 'operador', 'mensaje']
        widgets = {'mensaje': forms.Textarea(attrs={'rows': 3}), 'fecha_recojo': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})}
        labels = {'almacen': 'Almacén de origen (vacío = el de la ficha del cliente)', 'mensaje': 'Indicaciones'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth.models import User
        self.fields['cliente'].queryset = Tercero.objects.filter(activo=True, tipo__in=('CLIENTE', 'AMBOS'))
        self.fields['operador'].queryset = User.objects.filter(is_active=True).order_by('username')
        self.fields['operador'].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields['almacen'].required = False


class ClienteLogisticoForm(BootstrapMixin, forms.ModelForm):
    correos = forms.CharField(label='Correos autorizados para pedir recojos', required=False,
                              widget=forms.Textarea(attrs={'rows': 3}),
                              help_text='Uno por línea. Un correo de estas direcciones crea una solicitud de recojo.')
    celulares = forms.CharField(label='Celulares autorizados (WhatsApp)', required=False,
                                widget=forms.Textarea(attrs={'rows': 3}),
                                help_text='Uno por línea. Se comparan por sus últimos 9 dígitos (con o sin +51).')

    class Meta:
        model = ClienteLogistico
        fields = ['servicio_logistico', 'almacen_direccion', 'almacen_ubigeo', 'almacen_referencia', 'contacto']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            autorizados = list(self.instance.autorizados.all())
            self.initial['correos'] = '\n'.join(a.valor for a in autorizados if a.tipo == 'CORREO')
            self.initial['celulares'] = '\n'.join(a.valor for a in autorizados if a.tipo == 'CELULAR')

    @staticmethod
    def _lineas(texto):
        return [x.strip() for x in (texto or '').replace(',', '\n').replace(';', '\n').splitlines() if x.strip()]

    def clean_correos(self):
        correos = self._lineas(self.cleaned_data.get('correos'))
        validar = forms.EmailField().clean
        for c in correos:
            try:
                validar(c)
            except forms.ValidationError:
                raise forms.ValidationError(f'«{c}» no es un correo válido.')
        return correos

    def clean_celulares(self):
        from .models import clave_celular
        celulares = self._lineas(self.cleaned_data.get('celulares'))
        for c in celulares:
            if len(clave_celular(c)) < 9:
                raise forms.ValidationError(f'«{c}» no tiene 9 dígitos.')
        return celulares

    def clean(self):
        datos = super().clean()
        from .models import RemitenteAutorizado
        for tipo, campo in (('CORREO', 'correos'), ('CELULAR', 'celulares')):
            for valor in datos.get(campo) or []:
                otro = RemitenteAutorizado.objects.filter(tipo=tipo, clave=RemitenteAutorizado.normalizar(tipo, valor)) \
                    .exclude(cliente=self.instance.pk if self.instance.pk else None).select_related('cliente__tercero') \
                    .first()
                if otro is not None and otro.cliente_id != self.instance.pk:
                    self.add_error(campo, f'«{valor}» ya está autorizado para {otro.cliente.tercero.nombre}.')
        return datos

    def save(self, commit=True):
        from .models import RemitenteAutorizado
        obj = super().save(commit=commit)
        nuevos = {('CORREO', RemitenteAutorizado.normalizar('CORREO', v)): v for v in self.cleaned_data['correos']}
        nuevos.update({('CELULAR', RemitenteAutorizado.normalizar('CELULAR', v)): v
                       for v in self.cleaned_data['celulares']})
        for a in obj.autorizados.all():
            if (a.tipo, a.clave) not in nuevos:
                a.delete()
            else:
                nuevos.pop((a.tipo, a.clave))
        for (tipo, _), valor in nuevos.items():
            RemitenteAutorizado.objects.create(cliente=obj, tipo=tipo, valor=valor)
        return obj


class EstadoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = EstadoLogistico
        fields = ['nombre', 'color', 'icono', 'sigla', 'inicial', 'orden', 'disparador', 'activo']
        widgets = {'color': forms.TextInput(attrs={'type': 'color'})}


class ConfigForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ConfigOperador
        fields = ['almacen', 'serie_consolidada', 'serie_ultimo_tramo', 'motivo_traslado', 'descripcion_motivo',
                  'ultimo_tramo_por']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from logistica.models import MOTIVOS_TRASLADO
        self.fields['motivo_traslado'] = forms.ChoiceField(label='Motivo de traslado (catálogo 20)',
                                                           choices=MOTIVOS_TRASLADO,
                                                           help_text=self.Meta.model._meta.get_field(
                                                               'motivo_traslado').help_text)
        self.fields['motivo_traslado'].widget.attrs['class'] = 'form-select'


class EmitirForm(BootstrapMixin, forms.Form):
    agencia = forms.ModelChoiceField(label='Agencia de destino', queryset=Tercero.objects.none(), required=False,
                                     help_text='Vacío = la propia empresa recibe en destino')
    llegada_ubigeo = forms.CharField(label='Ubigeo de llegada', max_length=6, required=False,
                                     help_text='Vacío = el de la agencia')
    llegada_direccion = forms.CharField(label='Dirección de llegada', max_length=250, required=False,
                                        help_text='Vacío = la de la agencia')
    transportista = forms.ModelChoiceField(label='Transportista (servicio de transporte)',
                                           queryset=Tercero.objects.none(), required=False)
    fecha = forms.DateField(label='Inicio del traslado', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['agencia'].queryset = Tercero.objects.filter(activo=True).order_by('nombre')
        self.fields['transportista'].queryset = Tercero.objects.filter(activo=True, tipo__in=('PROVEEDOR', 'AMBOS')) \
            .order_by('nombre')


class TransportistaForm(BootstrapMixin, forms.Form):
    transportista = forms.ModelChoiceField(label='Transportista', queryset=Tercero.objects.none(), required=False)
    tipo = forms.ChoiceField(label='Documento', choices=Consolidado.DOCS_TRANSPORTISTA)
    numero = forms.CharField(label='Serie-número', max_length=30, required=False)
    fecha = forms.DateField(label='Fecha', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}), required=False)
    peso = forms.DecimalField(label='Peso (kg)', max_digits=12, decimal_places=2, required=False, min_value=0)
    bultos = forms.IntegerField(label='Bultos', required=False, min_value=0)
    qr = forms.CharField(label='Texto del QR (si lo escaneó)', required=False, widget=forms.HiddenInput)
    archivo = forms.FileField(label='Foto o PDF del documento', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['transportista'].queryset = Tercero.objects.filter(activo=True, tipo__in=('PROVEEDOR', 'AMBOS')) \
            .order_by('nombre')
