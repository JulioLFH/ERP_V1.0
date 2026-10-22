from django import forms

from contabilidad.models import CentroCosto
from core.forms import remoto, BootstrapMixin, validar_periodo_abierto
from core.models import Tercero

from .models import Compra, ContratoMarco, Licitacion, OrdenCompra


class CompraForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ['tipo_comprobante', 'serie', 'numero', 'tercero', 'fecha_emision', 'fecha_vencimiento', 'periodo',
                  'clasificacion', 'forma_pago', 'moneda', 'tipo_cambio', 'tipo_operacion', 'detraccion_pct',
                  'retencion_pct', 'percepcion_pct', 'icbper', 'orden_compra', 'doc_referencia',
                  'ingresar_almacen', 'almacen', 'centro_costo', 'cuenta_contable', 'valor_aduana', 'igv_importacion',
                  'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2})}
        labels = {'tercero': 'Proveedor'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        remoto(self.fields['tercero'], 'proveedores')
        self.fields['serie'].required = True
        self.fields['numero'].required = True
        self.fields['orden_compra'].queryset = OrdenCompra.objects.exclude(estado='ANULADO')
        self.fields['doc_referencia'].queryset = Compra.objects.filter(
            estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08'])
        remoto(self.fields['doc_referencia'], 'compras', perezoso=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['valor_aduana'].required = False  # solo la DUA (tipo 50)

    def clean_valor_aduana(self):
        return self.cleaned_data.get('valor_aduana') or 0

    def clean(self):
        data = super().clean()
        if data.get('tipo_comprobante') in ('07', '08') and not data.get('doc_referencia'):
            self.add_error('doc_referencia', 'Indique el comprobante que modifica la nota.')
        tercero = data.get('tercero')
        if data.get('tipo_comprobante') == '01' and tercero is not None and tercero.tipo_doc != '6':
            self.add_error('tipo_comprobante', f'Una factura (01) solo la emite un proveedor con RUC; {tercero.nombre} '
                                               f'no lo tiene. Para un proveedor del exterior use «Comprobante de no '
                                               f'domiciliado» (91).')
        if data.get('tipo_comprobante') == '50':  # la DUA no mueve el almacén: lo hace la factura comercial
            data['ingresar_almacen'] = False
            self.instance.ingresar_almacen = False
            if not data.get('valor_aduana'):
                self.add_error('valor_aduana', 'Indique el valor en aduana (CIF) de la DUA: es la base del IGV.')
        elif data.get('valor_aduana') or data.get('igv_importacion') is not None:
            data['valor_aduana'], data['igv_importacion'] = 0, None
            self.instance.valor_aduana, self.instance.igv_importacion = 0, None
        validar_periodo_abierto(self, 'fecha_emision', data.get('periodo'))
        from core.models import Empresa
        if not self.instance.pk and Empresa.actual().exigir_orden_compra and data.get('clasificacion') in Compra.DE_ALMACEN \
                and data.get('tipo_comprobante') not in ('07', '08') and not data.get('orden_compra'):
            self.add_error('orden_compra', 'Las compras de bienes de almacén se registran desde una orden de compra '
                                           '(Ajustes > Empresa > Exigir orden de compra).')
        from inventario.cierre import error_cierre
        if data.get('ingresar_almacen') and data.get('tipo_comprobante') != '08' and error_cierre(data.get('fecha_emision')):
            self.add_error('fecha_emision', error_cierre(data.get('fecha_emision')))
        if self.instance.pk and self.instance.stock_aplicado and error_cierre(self.instance.fecha_emision):
            self.add_error(None, error_cierre(self.instance.fecha_emision))
        if data.get('forma_pago') == 'CREDITO' and data.get('fecha_emision') and not data.get('fecha_vencimiento'):
            from datetime import timedelta  # vacío: emisión + días de crédito de la orden o del proveedor
            oc_ = data.get('orden_compra')
            dias = (oc_.dias_credito if oc_ and oc_.dias_credito else 0) or (
                data['tercero'].dias_credito if data.get('tercero') else 0)
            if dias:
                data['fecha_vencimiento'] = data['fecha_emision'] + timedelta(days=dias)
                self.instance.fecha_vencimiento = data['fecha_vencimiento']
        oc = data.get('orden_compra')
        if oc and oc.estado not in ('APROBADO', 'ATENDIDO') and oc.pk != self.instance.orden_compra_id:
            self.add_error('orden_compra', f'La orden de compra {oc.numero} está «{oc.get_estado_display()}»: solo se '
                                           f'facturan órdenes aprobadas. Apruébela primero.')
        if oc and not self.instance.pk and data.get('tipo_comprobante') not in ('07', '08'):
            from .solped import exige_conformidad
            if exige_conformidad(oc) and not oc.conformidades.filter(estado='LIBERADA').exists():
                self.add_error('orden_compra', f'La orden {oc.numero} es de servicios de una solicitud de pedido: '
                                               f'registre la conformidad del servicio (en la orden de compra) y que '
                                               f'se libere antes de registrar la factura.')
        if data.get('orden_compra') and not data.get('centro_costo'):
            data['centro_costo'] = data['orden_compra'].centro_costo
        # gasto directo (62 a 65 y 68): el centro de costo es obligatorio
        from contabilidad.models import CuentaDefecto
        from contabilidad.pcge import exige_centro
        cuenta = data.get('cuenta_contable') or CuentaDefecto.mapa().get(f'compra_{data.get("clasificacion")}')
        if cuenta and exige_centro(cuenta) and not data.get('centro_costo'):
            self.add_error('centro_costo', f'Obligatorio: la compra va a la cuenta {cuenta.codigo}, que exige centro '
                                           f'de costo. (Los productos de '
                                           'almacén ingresan a existencias y el centro se asigna al consumirlos.)')
        if data.get('ingresar_almacen') and data.get('tipo_comprobante') not in ('07', '08'):
            from inventario.servicios import tiene_recepciones
            recibida = (self.instance.pk and tiene_recepciones(compra=self.instance)) or (
                data.get('orden_compra') and tiene_recepciones(orden=data['orden_compra']))
            if recibida:
                self.add_error('ingresar_almacen', 'La mercadería ya se recibió en Inventario (recepción de compras). '
                                                   'Desmarque esta opción para no ingresarla dos veces.')
        return data


class LicitacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Licitacion
        fields = ['descripcion', 'fecha', 'fecha_limite', 'centro_costo', 'condiciones']
        widgets = {'condiciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)


class ContratoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ContratoMarco
        fields = ['proveedor', 'descripcion', 'fecha_inicio', 'fecha_fin', 'moneda', 'monto_maximo',
                  'condicion_pago', 'observaciones', 'estado']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['proveedor'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        remoto(self.fields['proveedor'], 'proveedores')
        if not self.instance.pk:
            self.fields.pop('estado')

    def clean(self):
        data = super().clean()
        if data.get('fecha_inicio') and data.get('fecha_fin') and data['fecha_fin'] < data['fecha_inicio']:
            self.add_error('fecha_fin', 'Debe ser posterior al inicio.')
        return data


def _lineas_formset(modelo, campos, extra):
    from core.models import Producto

    padre = {'LineaLicitacion': Licitacion, 'LineaContrato': ContratoMarco}[modelo.__name__]

    class LineaForm(forms.ModelForm):
        class Meta:
            model = modelo
            fields = campos

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False)
            remoto(self.fields['producto'], 'productos')
            if 'descripcion' in self.fields:  # vacía = el nombre del producto
                self.fields['descripcion'].required = False

        def clean(self):
            data = super().clean()
            if 'descripcion' in self.fields and not data.get('descripcion'):
                if data.get('producto'):
                    data['descripcion'] = data['producto'].nombre[:250]
                elif data.get('cantidad'):
                    self.add_error('descripcion', 'Elija un producto o describa el ítem.')
            return data

    return forms.inlineformset_factory(padre, modelo, form=LineaForm, extra=extra, can_delete=True)


def licitacion_formset():
    from .models import LineaLicitacion
    return _lineas_formset(LineaLicitacion, ['producto', 'descripcion', 'cantidad'], 5)


def contrato_formset():
    from .models import LineaContrato
    return _lineas_formset(LineaContrato, ['producto', 'precio_unitario', 'cantidad_maxima'], 4)


class OrdenCompraForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenCompra
        fields = ['tercero', 'contrato', 'fecha', 'fecha_entrega', 'centro_costo', 'moneda', 'tipo_cambio',
                  'tipo_operacion', 'condicion_pago', 'dias_credito', 'comprador', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2})}
        labels = {'tercero': 'Proveedor'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        remoto(self.fields['tercero'], 'proveedores')
        from django.contrib.auth import get_user_model
        self.fields['comprador'].queryset = get_user_model().objects.filter(
            is_active=True, acceso_proveedor__isnull=True).order_by('username')
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['centro_costo'].required = True
        if not CentroCosto.objects.filter(activo=True).exists():
            self.fields['centro_costo'].help_text = 'Cree primero los centros de costo en Contabilidad > ' \
                                                    'Configuración > Centros de costo'
        from .models import ContratoMarco
        self.fields['contrato'].queryset = ContratoMarco.objects.filter(estado='VIGENTE').select_related('proveedor')

    def clean(self):
        data = super().clean()
        c = data.get('contrato')
        if c is not None:
            if data.get('tercero') and data['tercero'] != c.proveedor:
                self.add_error('contrato', f'El contrato es con {c.proveedor.nombre}.')
            if data.get('fecha') and not c.vigente_en(data['fecha']):
                self.add_error('contrato', f'El contrato rige del {c.fecha_inicio:%d/%m/%Y} al {c.fecha_fin:%d/%m/%Y}.')
            if data.get('moneda') and data['moneda'] != c.moneda:
                self.add_error('moneda', f'El contrato está en {c.get_moneda_display().lower()}.')
        return data

    def save(self, commit=True):
        if not self.instance.numero:
            from core.models import Serie
            serie, numero = Serie.siguiente('OC', 'OC01')
            self.instance.numero = f'{serie}-{numero}'
        return super().save(commit)
