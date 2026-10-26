from django import forms
from django.forms import inlineformset_factory

from core.forms import remoto, BootstrapMixin, validar_periodo_abierto
from core.models import Almacen, Producto

from .models import (DIAS, CentroTrabajo, ComponenteLista, ConfiguracionPlanta, HojaRuta, ListaMateriales,
                     MotivoParada, OperacionRuta, OrdenProduccion, PlanDemanda, SubproductoLista, VersionFabricacion)

FABRICABLES = ['PRODUCTO_TERMINADO', 'SEMIELABORADO']
MAXIMO_ORDEN = 99_999_999  # cantidad y consumo por orden que caben con holgura en los importes del kardex


class MotivoParadaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MotivoParada
        fields = ['codigo', 'nombre', 'perdida', 'tipo', 'planificada', 'area', 'es_falla', 'exige_equipo',
                  'genera_mantenimiento', 'puestos', 'activo']
        widgets = {'puestos': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['puestos'].queryset = CentroTrabajo.objects.filter(activo=True)

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip().upper()

    def clean(self):
        d = super().clean()
        if d.get('genera_mantenimiento') and not d.get('exige_equipo'):
            self.add_error('genera_mantenimiento', 'Para proponer una orden de mantenimiento el motivo debe exigir '
                                                   'el equipo.')
        if d.get('tipo') == 'PLANIFICADA':
            d['planificada'] = True
        if d.get('perdida') == 'RENDIMIENTO' and d.get('tipo') not in ('MICROPARADA', 'VELOCIDAD'):
            self.add_error('tipo', 'Las pérdidas de rendimiento son microparadas o velocidad reducida.')
        return d


class ConfiguracionPlantaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ConfiguracionPlanta
        fields = ['planificadas_restan', 'microparada_minutos']


class CentroTrabajoForm(BootstrapMixin, forms.ModelForm):
    dias = forms.MultipleChoiceField(label='Días laborables', choices=DIAS, widget=forms.CheckboxSelectMultiple,
                                     required=False)

    class Meta:
        model = CentroTrabajo
        fields = ['codigo', 'nombre', 'tipo', 'costo_hora_mo', 'costo_hora_maquina', 'costo_hora_cif', 'centro_costo',
                  'horas_turno',
                  'turnos', 'eficiencia', 'horas_normales_mes', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dias'].widget.attrs.pop('class', None)
        self.initial['dias'] = list(self.instance.dias_laborables or '123456')

    def clean_eficiencia(self):
        valor = self.cleaned_data['eficiencia']
        if valor is not None and not 0 < valor <= 200:
            raise forms.ValidationError('Entre 1 y 200 %.')
        return valor

    def clean(self):
        """MAN-17: costos no negativos, al menos un turno, no más de 24 horas al día y algún día laborable."""
        d = super().clean()
        for campo in ('costo_hora_mo', 'costo_hora_maquina', 'costo_hora_cif'):
            if (d.get(campo) or 0) < 0:
                self.add_error(campo, 'No puede ser negativo.')
        horas, turnos = d.get('horas_turno'), d.get('turnos')
        if turnos is not None and turnos < 1:
            self.add_error('turnos', 'Al menos 1 turno.')
        if horas is not None and not 0 < horas <= 24:
            self.add_error('horas_turno', 'Entre 0.5 y 24 horas.')
        elif horas and turnos and horas * turnos > 24:
            self.add_error('turnos', f'{horas:g} h × {turnos} turnos = {horas * turnos:g} h: un día tiene 24 horas.')
        if (d.get('horas_normales_mes') or 0) < 0:
            self.add_error('horas_normales_mes', 'No puede ser negativo.')
        if not d.get('dias'):
            self.add_error('dias', 'Marque al menos un día laborable.')
        return d

    def save(self, commit=True):
        self.instance.dias_laborables = ''.join(sorted(self.cleaned_data.get('dias') or [])) or '123456'
        return super().save(commit)


class ListaMaterialesForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ListaMateriales
        fields = ['producto', 'codigo', 'cantidad_base', 'estado', 'vigente_desde', 'vigente_hasta', 'lote_min',
                  'lote_max', 'politica_consumo', 'tolerancia_consumo_pct', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, clase__in=FABRICABLES)
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
        tolerancia = datos.get('tolerancia_consumo_pct')
        if tolerancia is not None and not 0 <= tolerancia <= 100:  # MAN-17
            self.add_error('tolerancia_consumo_pct', 'Entre 0 y 100 %.')
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
        fields = ['producto', 'cantidad', 'merma', 'operacion', 'almacen', 'modo_consumo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, tipo='BIEN')
        remoto(self.fields['producto'], 'productos_bienes')
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True, uso='')
        self.fields['modo_consumo'].required = False  # vacío = automático por receta

    def clean_modo_consumo(self):
        return self.cleaned_data.get('modo_consumo') or 'AUTO'

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


class _ComponentesBase(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        vistos = set()
        for f in self.forms:
            d = getattr(f, 'cleaned_data', None) or {}
            if d.get('DELETE') or not d.get('producto'):
                continue
            clave = (d['producto'].pk, getattr(d.get('operacion'), 'pk', d.get('operacion')))
            if clave in vistos:  # MAN-17: el mismo insumo dos veces (en la misma operación) duplica el consumo
                raise forms.ValidationError(f'{d["producto"].nombre} se repite en la receta: súmelo en una sola '
                                            f'línea.')
            vistos.add(clave)


ComponentesFormSet = inlineformset_factory(ListaMateriales, ComponenteLista, form=ComponenteForm,
                                           formset=_ComponentesBase, extra=0, can_delete=True, min_num=1,
                                           validate_min=True)


class SubproductoForm(_FilaForm):
    class Meta:
        model = SubproductoLista
        fields = ['producto', 'cantidad', 'tipo', 'valor_unitario', 'participacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, tipo='BIEN')
        remoto(self.fields['producto'], 'productos_bienes')

    def clean(self):
        datos = super().clean()
        if datos.get('cantidad') is not None and datos['cantidad'] <= 0:
            self.add_error('cantidad', 'Debe ser mayor a cero.')
        if datos.get('tipo') == 'COPRODUCTO' and not (0 < (datos.get('participacion') or 0) < 100):
            self.add_error('participacion', 'Indique el % del costo conjunto (entre 0 y 100).')
        if datos.get('tipo') == 'SUBPRODUCTO' and (datos.get('valor_unitario') or 0) < 0:
            self.add_error('valor_unitario', 'No puede ser negativo.')
        return datos


class _SubproductosBase(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        total = sum((f.cleaned_data.get('participacion') or 0 for f in self.forms
                     if f.cleaned_data and not f.cleaned_data.get('DELETE') and
                     f.cleaned_data.get('tipo') == 'COPRODUCTO'), 0)
        if total >= 100:
            raise forms.ValidationError('Los coproductos suman 100 % o más: al producto principal no le quedaría costo.')


SubproductosFormSet = inlineformset_factory(ListaMateriales, SubproductoLista, form=SubproductoForm,
                                            formset=_SubproductosBase, extra=0, can_delete=True)


class HojaRutaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = HojaRuta
        fields = ['codigo', 'nombre', 'estado', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}


class OperacionRutaForm(_FilaForm):
    class Meta:
        model = OperacionRuta
        fields = ['secuencia', 'centro', 'descripcion', 'horas_preparacion', 'horas_unidad', 'horas_espera',
                  'maquinistas', 'ayudantes']

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
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, clase__in=FABRICABLES)
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
                  'maquilador', 'costo_servicio', 'compra_servicio', 'glosa']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'glosa': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(
            activo=True, clase__in=FABRICABLES, versiones_fabricacion__activa=True).distinct()
        # MAN-20: solo versiones con receta (y ruta) aprobadas y no vencidas; la que la orden ya tiene se mantiene
        from django.db.models import Q
        from django.utils import timezone
        hoy = timezone.localdate()
        vigentes = (Q(activa=True, lista__estado='APROBADA') & (Q(hoja__isnull=True) | Q(hoja__estado='APROBADA'))
                    & (Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=hoy))
                    & (Q(lista__vigente_hasta__isnull=True) | Q(lista__vigente_hasta__gte=hoy)))
        self.fields['version'].queryset = VersionFabricacion.objects.filter(
            vigentes | Q(pk=self.instance.version_id or 0)).select_related('producto')
        self.fields['version'].required = False
        self.fields['version'].help_text = 'Vacío = la versión vigente para la cantidad y la fecha'
        # la página filtra las versiones del producto elegido: lista corta, sin buscador
        self.fields['version'].widget.attrs['data-sin-buscador'] = '1'
        normales = Almacen.objects.filter(activo=True, uso='')
        self.fields['almacen_insumos'].queryset = Almacen.objects.filter(activo=True, uso__in=['', 'TERCEROS'])
        self.fields['almacen_destino'].queryset = normales
        from compras.models import Compra
        from core.forms import remoto
        from core.models import Tercero
        self.fields['maquilador'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        remoto(self.fields['maquilador'], 'proveedores')
        self.fields['compra_servicio'].queryset = Compra.objects.filter(estado='REGISTRADO')
        remoto(self.fields['compra_servicio'], 'compras', perezoso=True)
        self.fields['costo_servicio'].required = False
        if not self.instance.pk:
            self.initial.setdefault('almacen_insumos', Almacen.principal().pk)
            self.initial.setdefault('almacen_destino', Almacen.principal().pk)

    def clean_cantidad(self):
        valor = self.cleaned_data['cantidad']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        if valor is not None and valor > MAXIMO_ORDEN:
            raise forms.ValidationError(f'Máximo {MAXIMO_ORDEN:,.0f} por orden: divida la producción en varias órdenes.')
        return valor

    def _cabe_en_la_orden(self, version, cantidad):
        """MAN-16: los consumos y horas de la receta para esa cantidad deben poder grabarse (antes la orden se grababa y
        su ficha daba error 500 para siempre)."""
        lista = version.lista
        factor = cantidad / lista.cantidad_base if lista.cantidad_base else 1
        for c in lista.componentes.select_related('producto'):
            if c.cantidad_con_merma * factor > MAXIMO_ORDEN:
                self.add_error('cantidad', f'La receta pediría {c.cantidad_con_merma * factor:,.0f} de '
                                           f'{c.producto.nombre}: reduzca la cantidad de la orden.')
                return

    def clean(self):
        from .servicios import version_para
        datos = super().clean()
        datos['costo_servicio'] = datos.get('costo_servicio') or 0
        if datos['costo_servicio'] < 0:
            self.add_error('costo_servicio', 'No puede ser negativo.')
        if (datos.get('costo_servicio') or datos.get('compra_servicio')) and not datos.get('maquilador'):
            self.add_error('maquilador', 'Indique el maquilador del servicio.')
        validar_periodo_abierto(self, 'fecha')
        producto, version = datos.get('producto'), datos.get('version')
        cantidad, fecha = datos.get('cantidad'), datos.get('fecha')
        from datetime import timedelta

        from django.utils import timezone
        if fecha and fecha > timezone.localdate() + timedelta(days=365):  # MAN-17
            self.add_error('fecha', 'Máximo un año adelante.')
        elif fecha and fecha.year < 2000:
            self.add_error('fecha', 'Fecha no válida (desde el 2000).')
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
        if version is not None and not self.errors.get('version'):
            self._cabe_en_la_orden(version, cantidad)
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
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, tipo='BIEN')
        remoto(self.fields['producto'], 'productos_bienes')
