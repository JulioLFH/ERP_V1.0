from django import forms
from django.forms import inlineformset_factory

from core.forms import BootstrapMixin
from core.models import Empresa, Producto, Serie, Tercero
from ventas.models import Venta

from .models import Conductor, GuiaItem, GuiaRemision, Vehiculo

CAMPOS_COMUNES = ['fecha_emision', 'fecha_traslado', 'destinatario', 'partida_ubigeo', 'partida_direccion',
                  'llegada_ubigeo', 'llegada_direccion', 'vehiculo', 'conductor', 'peso_bruto', 'unidad_peso',
                  'numero_bultos', 'doc_relacionado', 'observaciones']


class GuiaBaseForm(BootstrapMixin, forms.ModelForm):
    tipo_guia = '09'
    serie_defecto = 'T001'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        terceros = Tercero.objects.filter(activo=True)
        for campo in ('destinatario', 'remitente', 'transportista'):
            if campo in self.fields:
                self.fields[campo].queryset = terceros
        if 'transportista' in self.fields:
            self.fields['transportista'].queryset = terceros.filter(tipo_doc='6')
        self.fields['vehiculo'].queryset = Vehiculo.objects.filter(activo=True)
        self.fields['conductor'].queryset = Conductor.objects.filter(activo=True)
        if 'venta' in self.fields:
            self.fields['venta'].queryset = Venta.objects.filter(estado='REGISTRADO').exclude(
                tipo_comprobante__in=['07', '08'])
        if self.instance.pk:
            self.fields['serie'].disabled = True
        self.fields['serie'].required = False
        self.fields['serie'].help_text = f'Vacío = {self.serie_defecto}'

    def clean(self):
        data = super().clean()
        for campo in ('partida_ubigeo', 'llegada_ubigeo'):
            valor = (data.get(campo) or '').strip()
            if valor and (len(valor) != 6 or not valor.isdigit()):
                self.add_error(campo, 'El ubigeo tiene 6 dígitos (ej. 150101 Lima).')
        if data.get('fecha_traslado') and data.get('fecha_emision') and data['fecha_traslado'] < data['fecha_emision']:
            self.add_error('fecha_traslado', 'El traslado no puede iniciar antes de la emisión.')
        if not self.instance.pk:
            from core.permisos import validar_serie
            error = validar_serie(data, self.tipo_guia, self.serie_defecto)
            if error:
                self.add_error('serie', error)
        return data

    def save(self, commit=True):
        guia = self.instance
        guia.tipo = self.tipo_guia
        if not guia.pk:
            guia.serie = (guia.serie or '').upper() or self.serie_defecto
            guia.serie, guia.numero = Serie.siguiente(self.tipo_guia, guia.serie)
        return super().save(commit)


class GuiaRemitenteForm(GuiaBaseForm):
    tipo_guia, serie_defecto = '09', 'T001'

    class Meta:
        model = GuiaRemision
        fields = ['serie', 'motivo_traslado', 'descripcion_motivo', 'modalidad', 'venta', 'compra',
                  'transportista', 'efecto_stock', 'almacen_origen', 'almacen_destino'] + CAMPOS_COMUNES
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}
        help_texts = {'destinatario': 'En traslados entre establecimientos (motivo 04 o 18) puede dejarlo vacío: '
                                      'se usa la propia empresa.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['destinatario'].required = False

    def clean(self):
        data = super().clean()
        motivo = data.get('motivo_traslado')
        if motivo in ('04', '18'):
            # SUNAT: en traslados entre establecimientos el destinatario es la misma empresa
            empresa = Empresa.actual()
            if data.get('destinatario') and data['destinatario'].numero_doc != empresa.ruc:
                self.add_error('destinatario', 'En el motivo 04/18 el destinatario debe ser la propia empresa '
                                               f'(RUC {empresa.ruc}). Deje el campo vacío.')
            else:
                data['destinatario'] = empresa.como_tercero()
                self.instance.destinatario = data['destinatario']
        elif not data.get('destinatario'):
            self.add_error('destinatario', 'Indique el destinatario.')
        venta = data.get('venta')
        if venta and data.get('efecto_stock') in ('SALIDA', 'TRASLADO'):
            if venta.stock_aplicado:
                self.add_error('efecto_stock', f'La {venta} ya descontó el stock: la guía debe ser '
                                               '"No mueve almacén" para no descontarlo dos veces.')
            otra = venta.guias.filter(estado='EMITIDA', efecto_stock='SALIDA').exclude(pk=self.instance.pk).first()
            if otra:
                self.add_error('efecto_stock', f'La {otra} ya sacó del almacén la mercadería de esta venta.')
        if motivo == '13' and not data.get('descripcion_motivo'):
            self.add_error('descripcion_motivo', 'Describa el motivo cuando es "13 Otros".')
        if data.get('modalidad') == '01' and not data.get('transportista'):
            self.add_error('transportista', 'En transporte público indique la empresa de transporte (RUC).')
        if data.get('modalidad') == '02':
            if not data.get('vehiculo'):
                self.add_error('vehiculo', 'En transporte privado indique el vehículo.')
            if not data.get('conductor'):
                self.add_error('conductor', 'En transporte privado indique el conductor.')
        efecto, origen, destino = data.get('efecto_stock'), data.get('almacen_origen'), data.get('almacen_destino')
        if efecto in ('SALIDA', 'TRASLADO') and not origen:
            self.add_error('almacen_origen', 'Indique el almacén de origen.')
        if efecto in ('ENTRADA', 'TRASLADO') and not destino:
            self.add_error('almacen_destino', 'Indique el almacén de destino.')
        if efecto == 'TRASLADO' and origen and origen == destino:
            self.add_error('almacen_destino', 'El destino debe ser distinto del origen.')
        from inventario.cierre import error_cierre
        if efecto in ('SALIDA', 'ENTRADA', 'TRASLADO') and error_cierre(data.get('fecha_traslado')):
            self.add_error('fecha_traslado', error_cierre(data.get('fecha_traslado')))
        if self.instance.pk and self.instance.stock_aplicado and error_cierre(self.instance.fecha_traslado):
            self.add_error(None, error_cierre(self.instance.fecha_traslado))
        return data


class GuiaTransportistaForm(GuiaBaseForm):
    tipo_guia, serie_defecto = '31', 'V001'

    class Meta:
        model = GuiaRemision
        fields = ['serie', 'remitente'] + CAMPOS_COMUNES
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}
        labels = {'doc_relacionado': 'Guía del remitente / doc. relacionado'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in ('remitente', 'vehiculo', 'conductor'):
            self.fields[campo].required = True


class GuiaItemForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = GuiaItem
        fields = ['producto', 'descripcion', 'unidad', 'cantidad']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False)
        self.fields['producto'].widget.attrs['class'] = 'form-select form-select-sm js-producto'
        self.fields['descripcion'].widget.attrs['class'] = 'form-control form-control-sm js-descripcion'
        self.fields['unidad'].widget.attrs['class'] = 'form-select form-select-sm js-unidad'
        self.fields['cantidad'].widget.attrs['class'] = 'form-control form-control-sm text-end js-cantidad'


def guia_formset(extra=1):
    return inlineformset_factory(GuiaRemision, GuiaItem, form=GuiaItemForm, fk_name='documento',
                                 extra=extra, can_delete=True, min_num=1, validate_min=True)


class VehiculoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Vehiculo
        fields = '__all__'

    def clean_placa(self):
        return self.cleaned_data['placa'].upper().replace(' ', '')


class ConductorForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Conductor
        fields = '__all__'
