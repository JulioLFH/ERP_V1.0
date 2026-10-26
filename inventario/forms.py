from django import forms
from django.forms import inlineformset_factory

from compras.models import Compra, OrdenCompra
from core.forms import remoto, BootstrapMixin, validar_periodo_abierto
from core.sustentos import SustentoField
from core.models import Almacen, Producto, Tercero
from ventas.models import Venta

from .models import Operacion, OperacionItem, TipoOperacion


class TipoOperacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TipoOperacion
        fields = ['codigo', 'nombre', 'clase', 'origen', 'requiere_costo', 'requiere_sustento', 'cuenta_contable',
                  'codigo_sunat',
                  'codigo_sunat_ingreso', 'almacen_origen', 'almacen_destino', 'icono', 'orden', 'activo']


class OperacionForm(BootstrapMixin, forms.ModelForm):
    """Cabecera: solo se muestran los campos que usa la clase del tipo de operación."""
    sustento = SustentoField(required=False, label='Documento de sustento',
                             help_text='Acta, informe, guía, etc. (PDF, imagen, Excel; máx. 5 MB)')
    motivo_fecha = forms.CharField(label='Motivo de la fecha de un mes anterior', max_length=150, required=False,
                                   help_text='Solo si la fecha contable es de un mes anterior (exige permiso)')

    class Meta:
        model = Operacion
        fields = ['fecha', 'fecha_documento', 'almacen_origen', 'almacen_destino', 'tercero', 'orden_compra', 'compra',
                  'venta', 'envio', 'orden_imputada', 'referencia', 'centro_costo', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2}),
                   'fecha_documento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, tipo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tipo = tipo = tipo or self.instance.tipo
        normales = Almacen.objects.filter(activo=True, uso='')
        quitar = []
        if tipo.usa_origen_almacen and tipo.clase != 'TRANSITO_RECEPCION':
            uso = 'DESTRUCCION' if tipo.codigo == 'SAL_DESTR' else ''
            self.fields['almacen_origen'].queryset = Almacen.objects.filter(activo=True, uso=uso) if uso else normales
            self.fields['almacen_origen'].required = True
        else:
            quitar.append('almacen_origen')
        if tipo.usa_destino_almacen:
            destino = Almacen.objects.filter(activo=True, uso='DESTRUCCION') if tipo.codigo == 'TRAS_DESTR' else normales
            self.fields['almacen_destino'].queryset = destino
            self.fields['almacen_destino'].required = True
            if tipo.clase == 'TRANSITO_ENVIO':
                self.fields['almacen_destino'].label = 'Almacén de destino final'
                self.fields['almacen_destino'].help_text = 'La mercadería queda en "Mercadería en tránsito" hasta ' \
                                                           'que se registre la recepción'
        else:
            quitar.append('almacen_destino')
        if tipo.clase == 'MANUFACTURA':
            self.fields['almacen_origen'].label = 'Almacén de insumos'
            self.fields['almacen_destino'].label = 'Almacén de productos terminados'
        # documentos de origen
        if tipo.origen == 'ORDEN_COMPRA':
            self.fields['orden_compra'].queryset = OrdenCompra.objects.exclude(estado='ANULADO').select_related(
                'tercero')
            self.fields['compra'].queryset = Compra.objects.filter(
                estado='REGISTRADO', stock_aplicado=False).exclude(tipo_comprobante__in=['07', '08'])
            self.fields['compra'].label = 'o factura de compra (sin orden)'
        elif tipo.origen == 'COMPRA':
            self.fields['compra'].queryset = Compra.objects.filter(estado='REGISTRADO').exclude(
                tipo_comprobante__in=['07', '08']).select_related('tercero')
            self.fields['compra'].required = True
        else:
            quitar += ['compra']
        if tipo.origen != 'ORDEN_COMPRA':
            quitar.append('orden_compra')
        if tipo.origen == 'VENTA':
            self.fields['venta'].queryset = Venta.objects.filter(estado='REGISTRADO').exclude(
                tipo_comprobante__in=['07', '08']).select_related('tercero')
            remoto(self.fields['venta'], 'ventas', perezoso=True)
            self.fields['venta'].required = True
        else:
            quitar.append('venta')
        if tipo.origen == 'TRANSITO':
            self.fields['envio'].queryset = Operacion.objects.filter(estado='CONFIRMADO',
                                                                     tipo__clase='TRANSITO_ENVIO')
            self.fields['envio'].required = True
        else:
            quitar.append('envio')
        if tipo.codigo in ('SAL_OP', 'DEV_OP'):  # MAN-15: la orden de producción a la que se imputa
            from produccion.models import OrdenProduccion
            self.fields['orden_imputada'].queryset = OrdenProduccion.objects.filter(
                estado__in=['CONFIRMADA', 'EN_PROCESO', 'TERMINADA']).select_related('producto').order_by('-id')
            self.fields['orden_imputada'].required = True
            self.fields['orden_imputada'].help_text = ('En proceso, o terminada con su mes abierto (kardex y '
                                                       'contabilidad): suma al costo real de la orden')
            remoto(self.fields['orden_imputada'], 'ordenes_produccion')
            quitar.append('centro_costo')
        else:
            quitar.append('orden_imputada')
        if tipo.origen or tipo.clase in ('TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION', 'MANUFACTURA'):
            quitar.append('tercero')  # el proveedor/cliente sale del documento de origen
        else:
            self.fields['tercero'].queryset = Tercero.objects.filter(activo=True)
            remoto(self.fields['tercero'], 'terceros')
            self.fields['tercero'].label = 'Proveedor / cliente / responsable (opcional)'
        for nombre in quitar:
            self.fields.pop(nombre, None)
        if tipo.requiere_sustento:
            self.fields['sustento'].help_text = ('Obligatorio para confirmar: ' +
                                                 'acta de inventario, de destrucción, informe, etc. (máx. 5 MB)')
        if not self.instance.pk:
            if 'almacen_origen' in self.fields and not self.initial.get('almacen_origen'):
                defecto = Almacen.especial('DESTRUCCION') if tipo.codigo == 'SAL_DESTR' else Almacen.principal()
                self.initial['almacen_origen'] = tipo.almacen_origen_id or defecto.pk
            if 'almacen_destino' in self.fields and not self.initial.get('almacen_destino'):
                if tipo.codigo == 'TRAS_DESTR':
                    defecto = Almacen.especial('DESTRUCCION')
                elif tipo.clase in ('TRASLADO', 'TRANSITO_ENVIO'):  # otro almacén distinto del de origen
                    defecto = normales.exclude(pk=self.initial.get('almacen_origen')).first() or Almacen.principal()
                else:
                    defecto = Almacen.principal()
                self.initial['almacen_destino'] = tipo.almacen_destino_id or defecto.pk

    def clean(self):
        from django.utils import timezone
        datos = super().clean()
        validar_periodo_abierto(self, 'fecha')
        hoy, fecha, doc = timezone.localdate(), datos.get('fecha'), datos.get('fecha_documento')
        # fecha contable: no futura; fecha del documento externo (guía del proveedor): no futura ni posterior a la
        # contable (ALM-08, ALM-12)
        if fecha and fecha > hoy:
            self.add_error('fecha', 'La fecha contable no puede ser futura.')
        elif fecha and fecha.year < 2000:  # I09
            self.add_error('fecha', 'Fecha no válida (desde el 2000).')
        if doc and doc > hoy:
            self.add_error('fecha_documento', 'La fecha del documento no puede ser futura.')
        elif doc and fecha and doc > fecha:
            self.add_error('fecha_documento', 'La fecha del documento no puede ser posterior a la fecha contable.')
        if fecha and fecha < hoy.replace(day=1) and not (datos.get('motivo_fecha') or '').strip():
            self.add_error('motivo_fecha', 'La fecha contable es de un mes anterior: indique el motivo.')
        if self.tipo.origen == 'ORDEN_COMPRA' and not (datos.get('orden_compra') or datos.get('compra')):
            self.add_error('orden_compra', 'Indique la orden de compra o la factura que se recibe.')
        if datos.get('orden_compra') and datos.get('compra'):
            self.add_error('compra', 'Elija solo uno: la orden de compra o la factura.')
        if datos.get('almacen_origen') and datos.get('almacen_origen') == datos.get('almacen_destino'):
            self.add_error('almacen_destino', 'Debe ser distinto del almacén de origen.')
        from contabilidad.pcge import exige_centro
        cuenta = self.tipo.cuenta_contable
        if (self.tipo.clase == 'SALIDA' and 'centro_costo' in self.fields and cuenta is not None
                and exige_centro(cuenta) and not datos.get('centro_costo')):  # INV-32: la salida va al gasto
            self.add_error('centro_costo', f'Obligatorio: «{self.tipo.nombre}» va al gasto ({cuenta.codigo}).')
        if self.tipo.codigo in ('ENT_OP', 'CONS_OP') and not self.instance.pk:  # MAN-18
            self.add_error(None, 'Esta operación la genera la orden de producción al declarar la producción.')
        if self.tipo.codigo == 'EPP' and not self.instance.pk:  # INV-32: con el trabajador y su cargo
            self.add_error(None, 'La entrega de EPP se registra en SST › Entrega de EPP: con el trabajador, su '
                                 'centro de costo y el cargo firmado (sale del almacén al confirmarla).')
        if self.tipo.codigo in ('SAL_OP', 'DEV_OP') and datos.get('orden_imputada') and fecha:
            from produccion.consumo import error_imputacion
            aviso = error_imputacion(datos['orden_imputada'], fecha)
            if aviso:
                self.add_error('orden_imputada', aviso)
        return datos


class OperacionItemForm(forms.ModelForm):
    class Meta:
        model = OperacionItem
        fields = ['producto', 'cantidad', 'costo_unitario', 'rol', 'lote', 'vencimiento', 'fabricacion', 'observacion']
        widgets = {'vencimiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'fabricacion': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lote'].widget.attrs.update({'class': 'form-control form-control-sm js-lote',
                                                 'placeholder': 'Lote / series'})
        self.fields['vencimiento'].widget.attrs['class'] = 'form-control form-control-sm js-vence'
        self.fields['fabricacion'].widget.attrs.update({'class': 'form-control form-control-sm', 'title': 'Fabricación'})
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, tipo='BIEN')
        self.fields['producto'].widget.attrs['class'] = 'form-select form-select-sm js-producto'
        remoto(self.fields['producto'], 'productos_bienes')
        self.fields['cantidad'].widget.attrs['class'] = 'form-control form-control-sm text-end js-cantidad'
        self.fields['costo_unitario'].widget.attrs['class'] = 'form-control form-control-sm text-end js-precio'
        self.fields['rol'].widget.attrs['class'] = 'form-select form-select-sm'
        self.fields['observacion'].widget.attrs['class'] = 'form-control form-control-sm'

    def clean_cantidad(self):
        cantidad = self.cleaned_data['cantidad']
        if cantidad is not None and cantidad <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return cantidad

    def clean_costo_unitario(self):
        """INV-31: el costo escrito se usa tal cual; vacío o cero = el costo promedio del producto."""
        costo = self.cleaned_data.get('costo_unitario')
        if costo is not None and costo < 0:
            raise forms.ValidationError('El costo no puede ser negativo (vacío = el costo promedio).')
        return costo or None

    def clean(self):
        datos = super().clean()
        validar_unidad_y_lote(self, datos.get('producto'), datos.get('cantidad'), datos.get('lote'))
        return datos


# INV-33: unidades que no se fraccionan y largo de cada código de lote o serie
UNIDADES_ENTERAS = {'NIU', 'UND', 'C62', 'SET', 'PR'}
LARGO_LOTE = 30


def validar_unidad_y_lote(form, producto, cantidad, lote, campo_cantidad='cantidad', campo_lote='lote'):
    if producto is None:
        return
    if (cantidad is not None and (producto.unidad or '').upper() in UNIDADES_ENTERAS
            and cantidad != cantidad.to_integral_value()):
        form.add_error(campo_cantidad, f'{producto.get_unidad_display()}: la cantidad va en números enteros.')
    largos = [c for c in (lote or '').replace(';', ',').split(',') if len(c.strip()) > LARGO_LOTE]
    if largos:
        form.add_error(campo_lote, f'Cada lote o serie tiene hasta {LARGO_LOTE} caracteres '
                                   f'(«{largos[0].strip()[:12]}…» tiene {len(largos[0].strip())}).')


def operacion_formset(extra=1):
    return inlineformset_factory(Operacion, OperacionItem, form=OperacionItemForm, extra=extra, can_delete=True,
                                 min_num=1, validate_min=True)
