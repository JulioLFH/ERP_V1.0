from django import forms

from core.forms import BootstrapMixin, remoto
from core.models import Almacen, Producto, Tercero

from .models import (ActividadABC, CambioIngenieria, CertificadoAnalisis, Equipo, InspeccionCalidad,
                     ListaMateriales, OrdenMantenimiento, ParametroCalidad, PlanMantenimiento, PlantillaCertificado,
                     RecursoActividad, RepuestoOrden)


class InspeccionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = InspeccionCalidad
        fields = ['tipo', 'fecha', 'producto', 'lote', 'cantidad', 'almacen', 'instrumento', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(tipo='BIEN', es_plantilla=False)
        remoto(self.fields['producto'], 'productos_bienes')
        self.fields['instrumento'].queryset = Equipo.objects.filter(es_instrumento=True, activo=True)
        self.fields['lote'].help_text = 'El código de un lote con stock del producto (con control por lote)'

    def clean(self):
        """CAL-04: el lote existe y tiene stock, la cantidad muestreada no pasa su stock y el tipo de inspección
        corresponde a la clase del producto; CAL-08: el instrumento tiene la calibración vigente."""
        from core.models import Lote, StockAlmacen
        from .calidad_farma import tipo_coherente, validar_instrumento
        from .planta import ErrorPlanta
        d = super().clean()
        p, codigo, cantidad = d.get('producto'), (d.get('lote') or '').strip().upper(), d.get('cantidad') or 0
        if p is None:
            return d
        if d.get('tipo') and not tipo_coherente(d['tipo'], p):
            self.add_error('tipo', f'«{dict(InspeccionCalidad.TIPOS)[d["tipo"]]}» no corresponde a un producto de '
                                   f'tipo {p.get_clase_display().lower()}.')
        if p.control:
            if not codigo:
                self.add_error('lote', 'Indique el lote (el producto se controla por lote).')
            else:
                lote = Lote.objects.filter(producto=p, codigo=codigo).first()
                if lote is None:
                    self.add_error('lote', f'El lote {codigo} no existe para {p.nombre}.')
                elif cantidad > lote.stock:
                    self.add_error('cantidad', f'El lote {codigo} solo tiene {lote.stock.normalize():f} en stock.')
                d['lote'] = codigo
        elif cantidad and d.get('almacen'):
            stock = StockAlmacen.objects.filter(producto=p, almacen=d['almacen']).values_list(
                'cantidad', flat=True).first() or 0
            if cantidad > stock:
                self.add_error('cantidad', f'En {d["almacen"]} solo hay {stock} de {p.nombre}.')
        try:
            validar_instrumento(d.get('instrumento'), d.get('fecha'))
        except ErrorPlanta as exc:
            self.add_error('instrumento', str(exc))
        return d


ParametrosFormSet = forms.inlineformset_factory(
    Producto, ParametroCalidad, fields=['nombre', 'unidad', 'minimo', 'maximo', 'especificacion', 'metodo',
                                        'referencia', 'orden', 'en_certificado'], extra=3, can_delete=True)


class PlantillaCertificadoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PlantillaCertificado
        fields = ['titulo', 'norma', 'texto', 'responsable', 'requiere_liberacion']
        widgets = {'texto': forms.Textarea(attrs={'rows': 3})}


class CertificadoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CertificadoAnalisis
        fields = ['lote', 'inspeccion', 'fecha', 'cliente', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        from django.utils import timezone

        from core.models import Lote
        # CAL-04: solo lotes con stock y vigentes
        self.fields['lote'].queryset = (Lote.objects.filter(producto__plantilla_certificado__isnull=False,
                                                            stocks__cantidad__gt=0)
                                        .filter(Q(vencimiento__isnull=True) | Q(vencimiento__gte=timezone.localdate()))
                                        .distinct().select_related('producto').order_by('-creado'))
        self.fields['lote'].label_from_instance = lambda l: f'{l.producto.codigo} {l.producto.nombre} — {l}'
        self.fields['inspeccion'].queryset = (InspeccionCalidad.objects.exclude(resultado='PENDIENTE')
                                              .filter(producto__plantilla_certificado__isnull=False)
                                              .select_related('producto').order_by('-fecha', '-id'))
        self.fields['inspeccion'].label_from_instance = (
            lambda i: f'{i.numero} · {i.producto.nombre} · lote {i.lote or "-"} · {i.get_resultado_display()}')
        self.fields['inspeccion'].required = True
        self.fields['cliente'].queryset = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')


class PlanForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PlanMantenimiento
        fields = ['tarea', 'componente', 'frecuencia_dias', 'frecuencia_uso', 'tolerancia_pct', 'ultima_fecha',
                  'ultima_lectura', 'anticipacion_dias', 'variable', 'umbral_min', 'umbral_max']

    def __init__(self, *args, equipo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['componente'].queryset = (equipo.componentes.filter(activo=True) if equipo is not None and
                                              equipo.pk else self.fields['componente'].queryset.none())
        if equipo is None or not equipo.medidor:
            for campo in ('frecuencia_uso', 'tolerancia_pct', 'ultima_lectura'):
                del self.fields[campo]

    def clean(self):
        datos = super().clean()
        condicion = datos.get('variable') and (datos.get('umbral_min') is not None or datos.get('umbral_max') is not None)
        if not datos.get('frecuencia_dias') and not datos.get('frecuencia_uso') and not condicion:
            raise forms.ValidationError('Indique cada cuántos días, cada cuántos km u horas, o la variable de condición '
                                        'con su umbral.')
        if datos.get('variable') and not condicion:
            self.add_error('umbral_max', 'Indique el umbral mínimo o máximo de la variable.')
        if (datos.get('umbral_min') is not None and datos.get('umbral_max') is not None
                and datos['umbral_min'] >= datos['umbral_max']):
            self.add_error('umbral_max', 'Debe ser mayor que el mínimo.')
        return datos


class OrdenMantenimientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenMantenimiento
        fields = ['equipo', 'tipo', 'prioridad', 'fecha_programada', 'descripcion', 'tecnico', 'responsable',
                  'horas_estimadas', 'costo_estimado', 'proveedor', 'orden_servicio', 'requiere_permiso',
                  'requiere_loto']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from compras.models import OrdenCompra
        from planillas.models import Trabajador
        self.fields['equipo'].queryset = Equipo.objects.filter(activo=True)
        self.fields['proveedor'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        remoto(self.fields['proveedor'], 'proveedores')
        self.fields['tecnico'].queryset = Trabajador.objects.filter(fecha_cese__isnull=True)
        self.fields['orden_servicio'].queryset = OrdenCompra.objects.filter(estado__in=['APROBADO', 'ATENDIDO'])

    def clean(self):
        d = super().clean()
        for campo in ('horas_estimadas', 'costo_estimado'):
            if (d.get(campo) or 0) < 0:
                self.add_error(campo, 'No puede ser negativo.')
        return d


class SeguridadOrdenForm(BootstrapMixin, forms.ModelForm):
    """MNT-08: permiso de trabajo de SST y LOTO de la orden."""
    class Meta:
        model = OrdenMantenimiento
        fields = ['requiere_permiso', 'permiso_trabajo', 'requiere_loto', 'loto_aplicado_por']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from sst.models import PermisoTrabajo
        self.fields['permiso_trabajo'].queryset = PermisoTrabajo.objects.filter(cerrado=False).order_by('-fecha')


class CierreMantenimientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OrdenMantenimiento
        fields = ['trabajo_realizado', 'responsable', 'inicio_real', 'fin_real', 'horas_parada', 'costo_mano_obra',
                  'costo_servicios', 'lectura_cierre', 'falla_sintoma', 'falla_modo', 'falla_causa', 'falla_accion',
                  'conformidad_por']
        widgets = {'trabajo_realizado': forms.Textarea(attrs={'rows': 2}),
                   'inicio_real': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
                   'fin_real': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')}

    def clean(self):
        """MNT-01: sin negativos y horas de parada coherentes con el inicio y el fin reales; MNT-05: un correctivo se
        cierra con sus códigos de falla (si la empresa tiene el catálogo)."""
        from decimal import Decimal

        from .models import CodigoFalla
        d = super().clean()
        for campo in ('horas_parada', 'costo_mano_obra', 'costo_servicios'):
            if (d.get(campo) or 0) < 0:
                self.add_error(campo, 'No puede ser negativo.')
        ini, fin = d.get('inicio_real'), d.get('fin_real')
        if ini and fin:
            if fin < ini:
                self.add_error('fin_real', 'El fin debe ser posterior al inicio.')
            elif (d.get('horas_parada') or 0) > Decimal((fin - ini).total_seconds() / 3600) + Decimal('0.01'):
                self.add_error('horas_parada', f'Las horas de parada no pueden pasar el tiempo entre el inicio y el fin '
                                               f'({(fin - ini).total_seconds() / 3600:.2f} h).')
        if self.instance.tipo == 'CORRECTIVO' and CodigoFalla.objects.filter(activo=True).exists():
            for campo in ('falla_sintoma', 'falla_modo', 'falla_causa', 'falla_accion'):
                if not d.get(campo):
                    self.add_error(campo, 'Obligatorio al cerrar un correctivo (análisis de fallas).')
        return d


class RepuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = RepuestoOrden
        fields = ['producto', 'cantidad', 'almacen']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(tipo='BIEN', es_plantilla=False)
        remoto(self.fields['producto'], 'productos_bienes')
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True, uso='')

    def clean_cantidad(self):
        c = self.cleaned_data['cantidad']
        if c <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return c


class CambioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CambioIngenieria
        fields = ['lista_actual', 'motivo', 'descripcion', 'fecha_efectiva']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lista_actual'].queryset = ListaMateriales.objects.filter(estado='APROBADA').select_related(
            'producto')

    def clean_descripcion(self):
        d = self.cleaned_data['descripcion'].strip()
        if len(d) < 10:
            raise forms.ValidationError('Describa el cambio y su razón (mínimo 10 caracteres).')
        return d


class ActividadForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ActividadABC
        fields = ['codigo', 'nombre', 'inductor', 'activo']


RecursosFormSet = forms.inlineformset_factory(
    ActividadABC, RecursoActividad, fields=['centro_costo', 'porcentaje'], extra=2, can_delete=True)
