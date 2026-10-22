from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from .models import Almacen, CorreoConfig, Empresa, FacturacionConfig, Producto, Serie, TipoCambio, Tercero


def periodo_cerrado(periodo):
    """True si el periodo contable (AAAAMM) está cerrado."""
    from contabilidad.models import PeriodoContable
    return bool(periodo) and PeriodoContable.esta_cerrado(periodo)


def validar_periodo_abierto(form, campo_fecha, periodo=None):
    fecha = form.cleaned_data.get(campo_fecha)
    periodo = periodo or (fecha.strftime('%Y%m') if fecha else '')
    if periodo_cerrado(periodo):
        form.add_error(campo_fecha, f'El periodo contable {periodo[4:]}/{periodo[:4]} está cerrado.')


class SelectRemoto(forms.Select):
    """Selector de listas grandes (productos, clientes, cuentas…): la página solo trae la opción elegida y el navegador
    llena el resto desde /opciones/<fuente>/ (una vez por página, compartido por todas las filas). La validación
    sigue siendo la del campo en el servidor."""

    def __init__(self, fuente, perezoso=False, attrs=None):
        super().__init__(attrs)
        self.fuente, self.perezoso = fuente, perezoso

    def optgroups(self, name, value, attrs=None):
        elegidos = [v for v in value if v not in ('', None)]
        opciones = [('', '---------')]
        completas = self.choices
        if elegidos and hasattr(completas, 'queryset'):
            opciones += [(str(o.pk), completas.field.label_from_instance(o))
                         for o in completas.queryset.filter(pk__in=elegidos)]
        self.choices = opciones
        try:
            return super().optgroups(name, value, attrs)
        finally:
            self.choices = completas

    def get_context(self, name, value, attrs):
        from django.urls import reverse
        contexto = super().get_context(name, value, attrs)
        contexto['widget']['attrs']['data-opciones'] = reverse('opciones', args=[self.fuente])
        if self.perezoso:
            contexto['widget']['attrs']['data-perezoso'] = '1'
        return contexto


def remoto(campo, fuente, perezoso=False):
    """Cambia el selector del campo por uno liviano (conserva clases y la validación del queryset)."""
    widget = SelectRemoto(fuente, perezoso, attrs=dict(campo.widget.attrs))
    widget.choices = campo.choices
    campo.widget = widget


class BootstrapMixin:
    """Aplica clases Bootstrap a todos los widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                w.attrs.setdefault('class', 'form-check-input')  # casillas: nunca form-control (ALM-05)
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault('class', 'form-select form-select-sm')
            else:
                w.attrs.setdefault('class', 'form-control form-control-sm')
            if isinstance(field, forms.DateField):
                w.input_type = 'date'
                w.format = '%Y-%m-%d'
        for nombre in ('almacen', 'almacen_origen', 'almacen_destino'):
            if nombre in self.fields:
                self.fields[nombre].queryset = Almacen.objects.filter(activo=True)
        if 'almacen' in self.fields and not self.initial.get('almacen') and not getattr(getattr(self, 'instance', None), 'almacen_id', 1):
            self.initial['almacen'] = Almacen.principal().pk

    def __getitem__(self, nombre):
        # al mostrar el formulario (las subclases ya fijaron sus listas) se dejan solo los almacenes del usuario;
        # el middleware además rechaza cualquier almacén no permitido que llegue en el POST
        if not getattr(self, '_almacenes_filtrados', False):
            self._almacenes_filtrados = True
            self._filtrar_almacenes()
        return super().__getitem__(nombre)

    def _filtrar_almacenes(self):
        from django.contrib.auth import get_user_model

        from .auditoria import usuario_actual
        from .permisos import almacenes_permitidos
        # los usuarios del portal de proveedores nunca aparecen en listas internas (aprobadores, responsables, jefes)
        for field in self.fields.values():
            qs = getattr(field, 'queryset', None)
            if qs is not None and qs.model is get_user_model():
                field.queryset = qs.filter(acceso_proveedor__isnull=True)
        permitidos = almacenes_permitidos(usuario_actual())
        if not permitidos:
            return
        for nombre, field in self.fields.items():
            qs = getattr(field, 'queryset', None)
            if qs is not None and qs.model is Almacen:
                field.queryset = qs.filter(pk__in=permitidos)
                actual = getattr(self.initial.get(nombre), 'pk', self.initial.get(nombre))
                if actual and actual not in permitidos and not self.is_bound:
                    primero = field.queryset.first()
                    self.initial[nombre] = primero.pk if primero else None


class FechaInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        kwargs.setdefault('format', '%Y-%m-%d')
        super().__init__(**kwargs)


class UbigeoMixin:
    """Departamento → provincia → distrito en cascada; el ubigeo (6 dígitos) se completa solo.

    Los selectores de departamento y provincia solo ayudan a elegir: el dato que se guarda es el ubigeo
    del distrito, validado contra la tabla del INEI (static/js/app.js los llena desde /ubigeos.json).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'ubigeo' not in self.fields:
            return
        from . import ubigeo
        actual = self.initial.get('ubigeo') or getattr(self.instance, 'ubigeo', '') or ''
        dep = forms.CharField(label='Departamento', required=False,
                              widget=forms.Select(attrs={'class': 'form-select form-select-sm',
                                                         'data-ubigeo-nivel': 'departamento'}))
        prov = forms.CharField(label='Provincia', required=False,
                               widget=forms.Select(attrs={'class': 'form-select form-select-sm',
                                                          'data-ubigeo-nivel': 'provincia'}))
        campo = self.fields['ubigeo']
        campo.label = 'Distrito'
        campo.help_text = (f'Ubigeo {actual}: {ubigeo.descripcion(actual)}' if ubigeo.existe(actual)
                           else 'Elija departamento, provincia y distrito: el ubigeo se completa solo')
        opciones = [('', '---------')] + ([(actual, ubigeo.tabla()[actual][2])] if ubigeo.existe(actual) else [])
        campo.widget = forms.Select(choices=opciones, attrs={
            'class': 'form-select form-select-sm', 'data-ubigeo-nivel': 'distrito', 'data-valor': actual})
        orden = []
        for nombre in self.fields:
            if nombre == 'ubigeo':
                orden += ['ubigeo_departamento', 'ubigeo_provincia']
            orden.append(nombre)
        self.fields['ubigeo_departamento'], self.fields['ubigeo_provincia'] = dep, prov
        self.order_fields(orden)

    def clean_ubigeo(self):
        from . import ubigeo
        valor = (self.cleaned_data.get('ubigeo') or '').strip()
        if valor and not ubigeo.existe(valor):
            raise forms.ValidationError('Ubigeo inexistente: elija el distrito de la lista.')
        return valor


class EmpresaForm(UbigeoMixin, BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Empresa
        fields = '__all__'
        widgets = {'token_tipo_cambio': forms.PasswordInput(render_value=True),
                   'sunat_client_secret': forms.PasswordInput(render_value=True),
                   'sire_client_secret': forms.PasswordInput(render_value=True)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # van en los comprobantes electrónicos y en los correos a clientes y proveedores
        self.fields['direccion'].required = True
        self.fields['email'].required = True
        self.fields['ruc'].help_text = 'Al escribirlo se traen de SUNAT la razón social, la dirección y el ubigeo'

    def clean_ruc(self):
        ruc = (self.cleaned_data.get('ruc') or '').strip()
        if error_ruc(ruc):
            raise forms.ValidationError(error_ruc(ruc))
        return ruc

    def clean(self):
        datos = super().clean()
        if datos.get('fuente_tipo_cambio') == 'SBS' and not datos.get('token_tipo_cambio'):
            self.add_error('token_tipo_cambio', 'Para usar el tipo de cambio SBS indique el token de Decolecta.')
        return datos


def digito_ruc(primeros10):
    """Dígito verificador del RUC (módulo 11, pesos de SUNAT)."""
    suma = sum(int(d) * p for d, p in zip(primeros10, (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)))
    resto = 11 - suma % 11
    return {10: 0, 11: 1}.get(resto, resto)


def error_ruc(ruc):
    """Mensaje de error si el RUC no es válido para SUNAT; '' si es correcto."""
    if len(ruc) != 11 or not ruc.isdigit():
        return 'El RUC debe tener 11 dígitos.'
    if ruc[:2] not in ('10', '15', '16', '17', '20'):
        return 'El RUC debe empezar con 10, 15, 16, 17 o 20.'
    if int(ruc[10]) != digito_ruc(ruc[:10]):
        return f'RUC inválido: el dígito verificador debería ser {digito_ruc(ruc[:10])}. Revise el número.'
    return ''


class TerceroForm(UbigeoMixin, BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Tercero
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        t = self.instance
        if t.pk and t.tipo_doc == '6' and error_ruc(t.numero_doc or ''):
            # registro antiguo (ej. datos de demostración): se puede editar; se avisa que conviene corregirlo
            self.fields['numero_doc'].help_text = (f'Atención: {error_ruc(t.numero_doc)} Puede guardar otros cambios, '
                                                   'pero SUNAT rechazará facturas a este RUC.')

    def clean(self):
        data = super().clean()
        doc, num = data.get('tipo_doc'), (data.get('numero_doc') or '').strip()
        sin_cambio = (self.instance.pk and doc == self.instance.tipo_doc and num == self.instance.numero_doc)
        if doc == '6' and error_ruc(num) and not sin_cambio:  # se valida al crear o al cambiar el número
            self.add_error('numero_doc', error_ruc(num))
        if doc == '1' and (len(num) != 8 or not num.isdigit()):
            self.add_error('numero_doc', 'El DNI debe tener 8 dígitos.')
        return data


class ProductoForm(BootstrapMixin, forms.ModelForm):
    # pestañas del formulario (estilo Odoo): (clave, título, icono, campos)
    PESTANAS = [
        ('general', 'General', 'bi-info-circle',
         ['clase', 'codigo', 'nombre', 'unidad', 'marca', 'familia', 'codigo_barras', 'peso', 'peso_neto',
          'unidad_reporte', 'control', 'descripcion',
          'imagen_archivo', 'quitar_imagen', 'activo']),
        ('compras', 'Compras', 'bi-bag', ['puede_comprarse', 'precio_compra', 'proveedor', 'unidad_compra',
                                          'factor_compra']),
        ('ventas', 'Ventas', 'bi-receipt', ['puede_venderse', 'precio_venta', 'afectacion_igv']),
        ('detraccion', 'Detracción', 'bi-bank', ['detraccion_codigo', 'detraccion_pct']),
        ('contabilidad', 'Contabilidad', 'bi-journal-bookmark',
         ['centro_beneficio', 'cuenta_existencias', 'cuenta_compra', 'cuenta_venta', 'cuenta_costo']),
        ('planificacion', 'Planificación', 'bi-calendar-check',
         ['tipo_planificacion', 'estrategia', 'stock_minimo', 'punto_reorden', 'stock_maximo', 'lote_compra',
          'tiempo_entrega', 'almacen_defecto']),
    ]

    imagen_archivo = forms.FileField(label='Imagen', required=False,
                                     help_text='PNG, JPG, GIF o WEBP de hasta 2 MB (catálogo, API, tienda online)')
    quitar_imagen = forms.BooleanField(label='Quitar la imagen actual', required=False)

    class Meta:
        model = Producto
        exclude = ['stock', 'costo_promedio']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 2})}

    def clean_imagen_archivo(self):
        archivo = self.cleaned_data.get('imagen_archivo')
        if archivo:
            from .variantes import MAX_IMAGEN, tipo_imagen
            if archivo.size > MAX_IMAGEN:
                raise forms.ValidationError('La imagen supera 2 MB.')
            inicio = archivo.read(16)
            archivo.seek(0)
            if tipo_imagen(inicio) is None:
                raise forms.ValidationError('La imagen debe ser PNG, JPG, GIF o WEBP.')
        return archivo

    def save(self, commit=True):
        producto = super().save(commit)
        if commit:
            from .variantes import guardar_imagen
            if self.cleaned_data.get('imagen_archivo'):
                guardar_imagen(producto, self.cleaned_data['imagen_archivo'])
            elif self.cleaned_data.get('quitar_imagen') and producto.imagen_id:
                producto.imagen = None
                producto.save(update_fields=['imagen'])
        return producto

    def clean_peso(self):
        peso = self.cleaned_data.get('peso') or Decimal('0')
        if peso < 0:
            raise forms.ValidationError('No puede ser negativo.')
        return peso

    def clean_tipo_planificacion(self):
        return self.cleaned_data.get('tipo_planificacion') or self.instance.tipo_planificacion or 'MRP'

    def clean_estrategia(self):
        return self.cleaned_data.get('estrategia') or self.instance.estrategia or 'MTS'

    def clean_peso_neto(self):
        peso = self.cleaned_data.get('peso_neto') or Decimal('0')
        if peso < 0:
            raise forms.ValidationError('No puede ser negativo.')
        return peso

    def clean_unidad_reporte(self):
        return self.cleaned_data.get('unidad_reporte') or self.instance.unidad_reporte or 'KG'

    def clean_factor_compra(self):
        factor = self.cleaned_data.get('factor_compra') or Decimal('1')
        if factor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return factor

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['peso'].required = False
        self.fields['factor_compra'].required = False
        self.fields['tipo_planificacion'].required = False
        self.fields['estrategia'].required = False
        self.fields['peso_neto'].required = False
        self.fields['unidad_reporte'].required = False
        from django.db.models import Q

        from contabilidad.models import CuentaContable

        from .models import PREFIJOS_CAMPO, cuentas_de_clase
        del_tipo = cuentas_de_clase(self.instance.clase or self.initial.get('clase') or 'MERCADERIA')
        for campo in ('cuenta_existencias', 'cuenta_compra', 'cuenta_venta', 'cuenta_costo'):
            # solo la clase de cuenta que corresponde (INV-12): existencias 20-28, compra 60, venta 70, costo 69
            filtro = Q()
            for prefijo in PREFIJOS_CAMPO[campo]:
                filtro |= Q(codigo__startswith=prefijo)
            actual = getattr(self.instance, f'{campo}_id', None)
            self.fields[campo].queryset = CuentaContable.objects.filter(
                filtro | Q(pk=actual or 0), imputable=True).order_by('codigo')
            tipo = del_tipo.get(campo)
            texto = f'Del tipo: {tipo.codigo} {tipo.nombre}' if tipo else 'El tipo no la usa'
            if self.instance.pk and actual and tipo and actual != tipo.pk:
                texto = f'⚠ Excepción: el tipo usa {tipo.codigo} {tipo.nombre}'
            self.fields[campo].help_text = f'{texto}. Vacío = la del tipo (Contabilidad › Cuentas por tipo de producto)'
        self.fields['proveedor'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        self.fields['almacen_defecto'].queryset = Almacen.objects.filter(activo=True, uso='')
        if self.instance.pk:  # el tipo define el código y el inventario: no se cambia luego de crearlo
            self.fields['clase'].disabled = True
            self.fields['clase'].help_text = 'No se cambia después de creado (define el código y las cuentas)'
            self.fields['codigo'].required = True
        if not self.instance.imagen_id:
            del self.fields['quitar_imagen']
        from .sunat import DETRACCION_TIPOS
        self.fields['detraccion_codigo'].widget = forms.Select(
            choices=[('', 'No sujeto a detracción')] + DETRACCION_TIPOS, attrs={'class': 'form-select form-select-sm'})
        self.fields['detraccion_pct'].required = False

    def pestanas(self):
        return [(clave, titulo, icono, [self[c] for c in campos if c in self.fields])
                for clave, titulo, icono, campos in self.PESTANAS]

    def clean(self):
        datos = super().clean()
        clase = datos.get('clase') or self.instance.clase
        if clase != 'ACTIVO' and datos.get('puede_venderse') and datos.get('precio_venta') is None:
            self.add_error('precio_venta', 'Indique el precio de venta.')
        if datos.get('stock_maximo') and datos.get('stock_minimo') and datos['stock_maximo'] < datos['stock_minimo']:
            self.add_error('stock_maximo', 'Debe ser mayor o igual al stock mínimo.')
        pct = datos.get('detraccion_pct') or Decimal('0')
        datos['detraccion_pct'] = pct
        if not 0 <= pct <= 100:
            self.add_error('detraccion_pct', 'Debe estar entre 0 y 100.')
        elif pct and not datos.get('detraccion_codigo'):
            self.add_error('detraccion_codigo', 'Indique el bien o servicio sujeto a detracción (catálogo 54).')
        elif datos.get('detraccion_codigo') and not pct:
            self.add_error('detraccion_pct', 'Indique el % de detracción del bien o servicio.')
        return datos


class SerieForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Serie
        fields = '__all__'


class AlmacenForm(UbigeoMixin, BootstrapMixin, forms.ModelForm):
    operaciones = forms.MultipleChoiceField(
        label='Operaciones permitidas', required=False, widget=forms.CheckboxSelectMultiple,
        help_text='Ninguna marcada = todas. Se valida en cada movimiento (recepciones, ventas, traslados, consumos…)')

    class Meta:
        model = Almacen
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        from .models import OPERACIONES_ALMACEN
        self.fields['operaciones'].choices = OPERACIONES_ALMACEN
        self.fields['operaciones'].initial = list(self.instance.operaciones or [])
        self.fields['responsables'].queryset = get_user_model().objects.filter(
            is_active=True, acceso_proveedor__isnull=True).order_by('username')
        self.fields['responsables'].widget.attrs.pop('class', None)
        self.fields['responsables'].widget = forms.CheckboxSelectMultiple(
            attrs={'class': 'form-check-input'}, choices=self.fields['responsables'].choices)
        # se muestran como obligatorios (asterisco); el uso de tránsito, cuarentena o terceros los libera en clean()
        self.fields['direccion'].help_text = ('Dirección y distrito obligatorios en un almacén físico: son el punto de '
                                              'partida de las guías (no en tránsito, cuarentena ni terceros)')
        for campo in ('direccion', 'ubigeo'):
            self.fields[campo].widget.attrs['data-obligatorio-fisico'] = '1'
            self.fields[campo].label = f'{self.fields[campo].label} *'

    def clean(self):
        datos = super().clean()
        # un almacén físico es punto de partida de las guías de remisión: dirección y ubigeo obligatorios
        if not datos.get('uso') and datos.get('activo'):
            for campo in ('direccion', 'ubigeo'):
                if not (datos.get(campo) or '').strip():
                    self.add_error(campo, 'Obligatorio: es el punto de partida de las guías de remisión.')
        datos['operaciones'] = list(datos.get('operaciones') or [])
        return datos


class TipoCambioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TipoCambio
        fields = ['fecha', 'compra', 'venta']


class FacturacionConfigForm(BootstrapMixin, forms.ModelForm):
    sol_clave_nueva = forms.CharField(label='Clave SOL del usuario secundario', required=False,
                                      widget=forms.PasswordInput, help_text='Se guarda cifrada. Vacía = no cambia')
    certificado_archivo = forms.FileField(label='Certificado digital (.pfx / .p12)', required=False,
                                          help_text='El certificado de la empresa para firmar los comprobantes')
    certificado_clave_nueva = forms.CharField(label='Clave del certificado', required=False,
                                              widget=forms.PasswordInput)
    gre_client_secret_nuevo = forms.CharField(label='Guías: client_secret', required=False,
                                              widget=forms.PasswordInput, help_text='Se guarda cifrado. Vacío = no '
                                                                                    'cambia')

    class Meta:
        model = FacturacionConfig
        fields = ['proveedor', 'ruta', 'token', 'entorno', 'sol_usuario', 'gre_client_id', 'envio_automatico']
        widgets = {'token': forms.PasswordInput(render_value=True)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entorno'].required = False

    def clean(self):
        datos = super().clean()
        datos['entorno'] = datos.get('entorno') or 'BETA'
        archivo = datos.get('certificado_archivo')
        if archivo:
            if archivo.size > 200 * 1024:
                self.add_error('certificado_archivo', 'El certificado no debería pesar más de 200 KB.')
            else:
                from .sunat_directo import ErrorSunatDirecto, _leer_pfx
                self._pfx = archivo.read()
                try:
                    _leer_pfx(self._pfx, datos.get('certificado_clave_nueva') or '')
                except ErrorSunatDirecto as exc:
                    self.add_error('certificado_clave_nueva', str(exc))
        if datos.get('proveedor') == 'SUNAT' and datos.get('entorno') == 'PRODUCCION':
            if not datos.get('sol_usuario'):
                self.add_error('sol_usuario', 'En producción indique el usuario SOL secundario.')
            if not (datos.get('sol_clave_nueva') or self.instance.sol_clave):
                self.add_error('sol_clave_nueva', 'En producción indique la clave SOL del usuario secundario.')
            if not (archivo or self.instance.certificado) or (self.instance.certificado_prueba and not archivo):
                self.add_error('certificado_archivo', 'En producción cargue el certificado digital de la empresa '
                                                      '(el de prueba no sirve).')
        return datos

    def save(self, commit=True):
        cfg = super().save(commit=False)
        if self.cleaned_data.get('sol_clave_nueva'):
            from .cifrado import cifrar
            cfg.sol_clave = cifrar(self.cleaned_data['sol_clave_nueva'])
        if self.cleaned_data.get('gre_client_secret_nuevo'):
            from .cifrado import cifrar
            cfg.gre_client_secret = cifrar(self.cleaned_data['gre_client_secret_nuevo'].strip())
        cfg.gre_client_id = (cfg.gre_client_id or '').strip()
        cfg.save()
        if getattr(self, '_pfx', None):
            from .sunat_directo import guardar_certificado
            guardar_certificado(cfg, self._pfx, self.cleaned_data.get('certificado_clave_nueva') or '',
                                self.cleaned_data['certificado_archivo'].name)
        return cfg


class CorreoConfigForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CorreoConfig
        fields = '__all__'
        widgets = {'clave': forms.PasswordInput(render_value=True)}


class AjusteInventarioForm(BootstrapMixin, forms.Form):
    TIPOS = [('ENTRADA', 'Entrada (inventario inicial / sobrante)'), ('SALIDA', 'Salida (merma / faltante / consumo)')]
    producto = forms.ModelChoiceField(Producto.objects.none())
    almacen = forms.ModelChoiceField(Almacen.objects.none(), label='Almacén')
    CONCEPTOS = [('', 'Según el tipo'), ('INICIAL', 'Inventario inicial (apertura)'), ('SOBRANTE', 'Sobrante'),
                 ('MERMA', 'Merma o faltante'), ('CONSUMO', 'Consumo interno')]
    tipo = forms.ChoiceField(choices=TIPOS)
    concepto = forms.ChoiceField(choices=CONCEPTOS, required=False,
                                 help_text='Define la cuenta contable: inicial contra patrimonio (59), sobrante a '
                                           'otros ingresos (75), merma y consumo a gastos (65)')
    cantidad = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    costo_unitario = forms.DecimalField(label='Costo unitario (solo entradas)', max_digits=12, decimal_places=4,
                                        required=False, min_value=0)
    fecha = forms.DateField()
    motivo = forms.CharField(max_length=80)
    sustento = forms.FileField(label='Documento de sustento',
                               help_text='Obligatorio: acta de inventario, informe de merma, vale de consumo… '
                                         '(PDF, imagen, Excel; máx. 5 MB)')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, tipo='BIEN')
        self.initial.setdefault('almacen', Almacen.principal().pk)

    def clean_sustento(self):
        from .sustentos import ErrorSustento, validar_archivo
        archivo = self.cleaned_data['sustento']
        try:
            validar_archivo(archivo)
        except ErrorSustento as exc:
            raise forms.ValidationError(str(exc))
        return archivo

    def clean(self):
        data = super().clean()
        tipo, concepto = data.get('tipo'), data.get('concepto')
        if not concepto:
            data['concepto'] = 'SOBRANTE' if tipo == 'ENTRADA' else 'MERMA'
        elif tipo == 'ENTRADA' and concepto in ('MERMA', 'CONSUMO') or tipo == 'SALIDA' and concepto in (
                'INICIAL', 'SOBRANTE'):
            self.add_error('concepto', 'El concepto no corresponde al tipo de ajuste.')
        if tipo == 'ENTRADA' and data.get('costo_unitario') is None and data.get('producto') \
                and not data['producto'].costo_promedio:
            self.add_error('costo_unitario', 'Indique el costo unitario: el producto aún no tiene costo.')
        from inventario.cierre import error_cierre
        if error_cierre(data.get('fecha')):
            self.add_error('fecha', error_cierre(data.get('fecha')))
        p, alm = data.get('producto'), data.get('almacen')
        if data.get('tipo') == 'SALIDA' and p and alm:
            disponible = p.stocks.filter(almacen=alm).values_list('cantidad', flat=True).first() or 0
            if data.get('cantidad') and data['cantidad'] > disponible:
                self.add_error('cantidad', f'Stock disponible en {alm}: {disponible}')
        return data


class ItemForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        fields = ['producto', 'descripcion', 'cantidad', 'precio_unitario', 'descuento_pct', 'afectacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['descuento_pct'].required = False
        self.fields['descuento_pct'].widget.attrs['class'] = 'form-control form-control-sm text-end js-descuento'
        self.fields['afectacion'].widget.attrs['class'] = 'form-select form-select-sm js-afectacion'
        self.fields['afectacion'].choices = [('', 'Según doc.'), ('GRAVADA', 'Gravada'),
                                             ('EXONERADA', 'Exonerada'), ('INAFECTA', 'Inafecta')]
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False)
        self.fields['producto'].widget.attrs['class'] = 'form-select form-select-sm js-producto'
        remoto(self.fields['producto'], 'productos')
        self.fields['cantidad'].widget.attrs['class'] = 'form-control form-control-sm text-end js-cantidad'
        self.fields['precio_unitario'].widget.attrs['class'] = 'form-control form-control-sm text-end js-precio'
        self.fields['descripcion'].widget.attrs['class'] = 'form-control form-control-sm js-descripcion'

    def clean_descuento_pct(self):
        valor = self.cleaned_data.get('descuento_pct') or Decimal('0')
        if not 0 <= valor < 100:
            raise forms.ValidationError('Entre 0 y 99.99 %.')
        return valor


def item_formset(parent_model, item_model, extra=1):
    return inlineformset_factory(
        parent_model, item_model, form=ItemForm, fk_name='documento',
        extra=extra, can_delete=True, min_num=1, validate_min=True,
    )
