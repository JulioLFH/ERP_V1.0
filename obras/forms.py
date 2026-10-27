from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from core.forms import BootstrapMixin, remoto
from core.models import Tercero

from .models import (HitoLicitacion, Licitacion, Obra, Partida, Presupuesto, Recurso, RequisitoPostor,
                     Subpresupuesto, Unidad, VentaInmueble)


class ObraForm(BootstrapMixin, forms.ModelForm):
    motivo_cambio = forms.CharField(label='Motivo para reabrir', max_length=250, required=False,
                                    help_text='Solo al reabrir una obra liquidada o cerrada (queda en la auditoría)')
    terreno_distinto = forms.BooleanField(
        label='Confirmo un % de terreno distinto al 50 % del Reglamento (aprobado por el contador)', required=False,
        help_text='Reglamento de la Ley del IGV, art. 5, num. 9: en la primera venta de inmuebles el terreno es el 50 % '
                  'del valor de transferencia')

    class Meta:
        model = Obra
        fields = ['codigo', 'nombre', 'cliente', 'tipo', 'sistema', 'contrato', 'moneda', 'monto_contratado',
                  'fecha_contrato', 'fecha_inicio', 'plazo_dias', 'direccion', 'ubigeo', 'area_m2', 'residente',
                  'supervisor', 'estado', 'gastos_generales_pct', 'utilidad_pct', 'margen_meta_pct',
                  'adelanto_directo_pct',
                  'adelanto_materiales_pct', 'retencion_garantia_pct', 'terreno_pct', 'prorrateo',
                  'prorrateo_area_pct', 'cuenta_recaudadora', 'banco_financiador', 'horas_presupuestadas',
                  'gastos_presupuestados']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from finanzas.models import Cuenta
        self.fields['cliente'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')
        self.fields['residente'].queryset = get_user_model().objects.filter(is_active=True).order_by('username')
        self.fields['cuenta_recaudadora'].queryset = Cuenta.objects.filter(activo=True, es_detracciones=False)
        if self.instance.pk:
            self.fields['codigo'].disabled = True
        else:  # EMP-02: una oficina de servicios por proyecto crea proyectos de servicios (sin almacén)
            from core.models import Empresa
            giros = set(Empresa.actual().giros or [])
            if 'SERVICIOS_PROYECTO' in giros and not giros & {'CONSTRUCCION', 'INMOBILIARIA'}:
                self.fields['tipo'].initial = 'SERVICIO'
        self.fields['residente'].label = 'Residente / jefe de proyecto'
        if self.instance.estado not in ('LIQUIDADA', 'CERRADA'):
            del self.fields['motivo_cambio']

    def clean(self):
        data = super().clean()
        pct = data.get('terreno_pct')
        inmobiliaria = data.get('tipo') == 'INMOBILIARIA'
        if pct is None:
            data['terreno_pct'] = Decimal('50') if inmobiliaria else 0  # INM-09: el del Reglamento
        elif not 0 <= pct < 100:
            self.add_error('terreno_pct', 'Entre 0 y 99.99 %.')
        elif inmobiliaria and pct != 50 and not data.get('terreno_distinto'):
            self.add_error('terreno_pct', 'El Reglamento de la Ley del IGV (art. 5, num. 9) fija el terreno en el 50 % del '
                                          'valor de transferencia en la primera venta. Para usar otro %, marque la '
                                          'confirmación (con la aprobación del contador).')
        self._validar_rangos(data)
        return data

    PORCENTAJES = ('gastos_generales_pct', 'utilidad_pct', 'margen_meta_pct', 'adelanto_directo_pct',
                   'adelanto_materiales_pct', 'retencion_garantia_pct', 'prorrateo_area_pct')
    MONTOS = ('monto_contratado', 'area_m2', 'horas_presupuestadas', 'gastos_presupuestados', 'plazo_dias')

    def _validar_rangos(self, data):
        """OBR-29: montos y áreas no negativos, % entre 0 y 100, fechas coherentes, ubigeo de 6 dígitos y adelantos
        según la Ley de Contrataciones del Estado (directo hasta 10 % y materiales hasta 20 % en obra pública)."""
        for campo in self.MONTOS:
            if campo in self.fields and (data.get(campo) or 0) < 0:
                self.add_error(campo, 'No puede ser negativo.')
        for campo in self.PORCENTAJES:
            valor = data.get(campo)
            if campo in self.fields and valor is not None and not 0 <= valor <= 100:
                self.add_error(campo, 'Entre 0 y 100 %.')
        contrato, inicio = data.get('fecha_contrato'), data.get('fecha_inicio')
        for campo, fecha in (('fecha_contrato', contrato), ('fecha_inicio', inicio)):
            if fecha and fecha.year < 2000:
                self.add_error(campo, 'Fecha no válida (desde el 2000).')
        if contrato and inicio and inicio < contrato:
            self.add_error('fecha_inicio', 'El inicio de obra no puede ser anterior al contrato.')
        ubigeo = (data.get('ubigeo') or '').strip()
        if ubigeo and not (len(ubigeo) == 6 and ubigeo.isdigit()):
            self.add_error('ubigeo', 'El ubigeo tiene 6 dígitos (INEI).')
        directo, materiales = data.get('adelanto_directo_pct') or 0, data.get('adelanto_materiales_pct') or 0
        if data.get('tipo') == 'PUBLICA':
            if directo > 10:
                self.add_error('adelanto_directo_pct', 'Obra pública: el adelanto directo es hasta el 10 % del contrato.')
            if materiales > 20:
                self.add_error('adelanto_materiales_pct', 'Obra pública: el adelanto de materiales es hasta el 20 %.')
        elif directo + materiales > 100:
            self.add_error('adelanto_materiales_pct', 'Los adelantos suman más del 100 % del contrato.')


# ---------------------------------------------------------------- licitaciones (CRM de constructoras)
class LicitacionNuevaForm(BootstrapMixin, forms.Form):
    nombre = forms.CharField(label='Obra / proyecto', max_length=250)
    cliente = forms.ModelChoiceField(Tercero.objects.none(), label='Entidad o cliente')
    tipo_obra = forms.ChoiceField(label='Tipo de obra', choices=Obra.TIPOS[:2], initial='PUBLICA')
    procedimiento = forms.ChoiceField(choices=Licitacion.PROCEDIMIENTOS, initial='LP')
    numero = forms.CharField(label='N° / nomenclatura (SEACE)', max_length=80, required=False)
    valor_referencial = forms.DecimalField(label='Valor referencial (con IGV)', max_digits=16, decimal_places=2,
                                           min_value=0, required=False)
    consorcio = forms.BooleanField(label='Se postula en consorcio', required=False)
    consorciados = forms.CharField(label='Empresas del consorcio', max_length=250, required=False)
    participacion_pct = forms.DecimalField(label='Nuestra participación %', max_digits=5, decimal_places=2,
                                           initial=100, min_value=0, max_value=100)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cliente'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')

    def clean(self):
        data = super().clean()
        validar_consorcio(self, data)
        return data


def validar_consorcio(form, data):
    """OBR-30: participación mayor a 0; en consorcio, los consorciados y menos del 100 %; menos del 100 % es consorcio."""
    pct, consorcio = data.get('participacion_pct'), data.get('consorcio')
    if pct is not None and pct <= 0:
        form.add_error('participacion_pct', 'Debe ser mayor a 0 %.')
    elif consorcio and pct is not None and pct >= 100:
        form.add_error('participacion_pct', 'En consorcio la participación es menor al 100 %.')
    elif pct is not None and pct < 100 and not consorcio:
        form.add_error('consorcio', f'Con {pct:g} % de participación marque «Se postula en consorcio».')
    if consorcio and not (data.get('consorciados') or '').strip():
        form.add_error('consorciados', 'Indique las empresas del consorcio.')


class LicitacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Licitacion
        fields = ['procedimiento', 'numero', 'valor_referencial', 'consorcio', 'consorciados', 'participacion_pct']

    def clean(self):
        data = super().clean()
        validar_consorcio(self, data)
        if (data.get('valor_referencial') or 0) < 0:
            self.add_error('valor_referencial', 'No puede ser negativo.')
        return data


class HitoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = HitoLicitacion
        fields = ['tipo', 'fecha', 'hora', 'nota']

    def clean_fecha(self):  # CRM-23: un hito de 1900 es un error de digitación
        from datetime import timedelta

        from django.utils import timezone
        fecha = self.cleaned_data['fecha']
        hoy = timezone.localdate()
        if fecha and not hoy - timedelta(days=730) <= fecha <= hoy + timedelta(days=730):
            raise forms.ValidationError('Revise la fecha: el cronograma del proceso va de dos años atrás a dos años '
                                        'adelante.')
        return fecha


class RequisitoForm(BootstrapMixin, forms.ModelForm):
    archivo = forms.FileField(label='Documento (opcional)', required=False,
                              help_text='Constancia de RNP, carta fianza, certificados de experiencia…')

    class Meta:
        model = RequisitoPostor
        fields = ['tipo', 'descripcion', 'monto', 'vence', 'cumple']

    def clean_monto(self):  # CRM-23
        monto = self.cleaned_data.get('monto')
        if monto is not None and monto < 0:
            raise forms.ValidationError('El monto no puede ser negativo.')
        return monto


class PerderForm(BootstrapMixin, forms.Form):
    desierta = forms.BooleanField(label='Proceso desierto o cancelado (nadie ganó)', required=False)
    motivo = forms.ChoiceField(choices=[('', '— elija el motivo —')] + Licitacion.MOTIVOS[1:], required=False)
    ganador = forms.CharField(label='Ganador', max_length=150, required=False)
    precio_ganador = forms.DecimalField(label='Precio del ganador', max_digits=16, decimal_places=2, required=False,
                                        min_value=0)
    detalle = forms.CharField(label='Detalle', max_length=250, required=False)


# ---------------------------------------------------------------- proyecto inmobiliario
class UnidadForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Unidad
        fields = ['codigo', 'tipo', 'piso', 'area_techada', 'area_libre', 'dormitorios', 'vista', 'precio_lista',
                  'notas']

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.obra = obra

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip().upper()
        if Unidad.objects.filter(obra=self.obra, codigo=codigo).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe una unidad con ese código en el proyecto.')
        return codigo


class SepararForm(BootstrapMixin, forms.Form):
    unidades = forms.ModelMultipleChoiceField(Unidad.objects.none(), label='Unidades',
                                              help_text='Departamento con su estacionamiento y depósito, si van juntos')
    cliente = forms.ModelChoiceField(Tercero.objects.none(), label='Comprador')
    precio = forms.DecimalField(label='Precio de venta (con IGV)', max_digits=14, decimal_places=2, min_value=0,
                                required=False, help_text='Vacío = la suma de los precios de lista')
    financiamiento = forms.ChoiceField(choices=VentaInmueble.FINANCIAMIENTOS)
    banco = forms.CharField(label='Banco del crédito', max_length=120, required=False)
    fecha = forms.DateField(label='Fecha de separación')
    vence_separacion = forms.DateField(label='La separación vence el', required=False)
    separacion = forms.DecimalField(label='Monto de la separación', max_digits=14, decimal_places=2, min_value=0,
                                    required=False)
    arras = forms.BooleanField(label='La separación es arras de retractación', required=False,
                               help_text='Sin IGV ni comprobante hasta el % del parámetro; con la minuta se emite su '
                                         'comprobante. Si el comprador desiste, las pierde')
    inicial = forms.DecimalField(label='Cuota inicial', max_digits=14, decimal_places=2, min_value=0, required=False)
    fecha_inicial = forms.DateField(label='Vence la cuota inicial', required=False)
    cuotas = forms.IntegerField(label='N° de cuotas con la empresa', min_value=1, max_value=120, required=False,
                                help_text='Solo financiamiento directo: el saldo en cuotas mensuales')
    primera_cuota = forms.DateField(label='Vence la primera cuota', required=False)
    bono = forms.DecimalField(label='Bono o subsidio (BBP, BFH…)', max_digits=14, decimal_places=2, min_value=0,
                              required=False)
    fecha_banco = forms.DateField(label='Desembolso estimado del banco', required=False,
                                  help_text='Crédito hipotecario o MiVivienda: el saldo lo paga el banco')
    oportunidad = forms.ModelChoiceField(None, label='Oportunidad del CRM', required=False,
                                         help_text='La del comprador en el embudo de Ventas (visitas, cotización): '
                                                   'pasa a negociación y, con la minuta, a ganada')
    notas = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        from ventas.models import Oportunidad
        self.fields['oportunidad'].queryset = Oportunidad.objects.filter(etapa__in=Oportunidad.ABIERTAS,
                                                                         licitacion__isnull=True)
        self.fields['unidades'].queryset = Unidad.objects.filter(obra=obra, estado='DISPONIBLE')
        self.fields['unidades'].label_from_instance = (
            lambda u: f'{u} · {u.area_techada:g} m² · S/ {u.precio_lista:,.2f}')
        self.fields['unidades'].widget.attrs['size'] = 8
        self.fields['cliente'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['cliente'], 'clientes')


class FichaCompradorForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = VentaInmueble
        fields = ['ocupacion', 'origen_fondos', 'origen_fondos_detalle', 'es_pep', 'pep_cargo', 'declaracion_fondos']

    def clean(self):
        d = super().clean()
        if d.get('es_pep') and not (d.get('pep_cargo') or '').strip():
            self.add_error('pep_cargo', 'Indique el cargo público del comprador.')
        return d


class DesistimientoForm(BootstrapMixin, forms.Form):
    motivo = forms.CharField(max_length=180)
    penalidad = forms.DecimalField(label='Penalidad retenida', max_digits=14, decimal_places=2, min_value=0,
                                   help_text='Según el contrato; primero se retienen las arras')
    fecha = forms.DateField()
    cuenta = forms.ModelChoiceField(None, label='Cuenta desde la que se devuelve', required=False)
    medio_pago = forms.ChoiceField(label='Medio de pago')
    numero_operacion = forms.CharField(label='N° de operación', max_length=40, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from finanzas.models import Cuenta, Movimiento
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True, es_detracciones=False)
        self.fields['medio_pago'].choices = Movimiento.MEDIOS


class CobroForm(BootstrapMixin, forms.Form):
    monto = forms.DecimalField(label='Importe cobrado', max_digits=14, decimal_places=2, min_value=0)
    fecha = forms.DateField()
    cuenta = forms.ModelChoiceField(None, label='Cuenta (caja o banco)')
    medio_pago = forms.ChoiceField(label='Medio de pago')
    numero_operacion = forms.CharField(label='N° de operación', max_length=40, required=False)
    sustento = forms.FileField(label='Constancia (opcional)', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from finanzas.models import Cuenta, Movimiento
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True, es_detracciones=False)
        self.fields['medio_pago'].choices = Movimiento.MEDIOS


class _PieMixin:
    """OBR-42: gastos generales y utilidad de 0 a 100 %, IGV el de la empresa (o 0 si la obra está exonerada) y la
    fecha de los precios entre el 2000 y hoy."""

    def clean(self):
        from django.utils import timezone
        data = super().clean()
        for campo, nombre in (('gastos_generales_pct', 'Los gastos generales'), ('utilidad_pct', 'La utilidad')):
            v = data.get(campo)
            if v is not None and not 0 <= v <= 100:
                self.add_error(campo, f'{nombre} van de 0 a 100 %.')
        igv = data.get('igv_pct')
        if igv is not None:
            from core.models import Empresa
            tasa = Empresa.actual().igv_tasa
            if igv not in (0, tasa):
                self.add_error('igv_pct', f'El IGV es {tasa:g} % (o 0 % si la obra está exonerada).')
        fecha = data.get('fecha_precios')
        if fecha and (fecha.year < 2000 or fecha > timezone.localdate()):
            self.add_error('fecha_precios', 'La fecha de los precios va entre el 2000 y hoy.')
        return data


class PresupuestoForm(_PieMixin, BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Presupuesto
        fields = ['tipo', 'nombre', 'fecha_precios', 'gastos_generales_pct', 'utilidad_pct', 'igv_pct']


class PieForm(_PieMixin, BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Presupuesto
        fields = ['nombre', 'fecha_precios', 'gastos_generales_pct', 'utilidad_pct', 'igv_pct']


class PartidaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Partida
        fields = ['subpresupuesto', 'item', 'descripcion', 'unidad', 'metrado', 'es_titulo', 'precio_fijo', 'precio']

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.presupuesto = presupuesto
        self.fields['metrado'].required = False
        self.fields['precio'].required = False
        subs = Subpresupuesto.objects.filter(presupuesto=presupuesto)
        self.fields['subpresupuesto'].queryset = subs
        if subs.exists():
            self.fields['subpresupuesto'].required = True
            self.fields['subpresupuesto'].empty_label = None
        else:
            self.fields['subpresupuesto'].widget = forms.HiddenInput()

    def clean(self):
        import re
        data = super().clean()
        item = (data.get('item') or '').strip().rstrip('.')
        if item and not re.fullmatch(r'\d{1,3}(\.\d{1,4})*', item):
            self.add_error('item', 'Ítem con el formato 01, 01.01, 01.01.01…')
        elif item:
            data['item'] = item
            repetido = Partida.objects.filter(presupuesto=self.presupuesto, item=item,
                                              subpresupuesto=data.get('subpresupuesto')).exclude(pk=self.instance.pk)
            if repetido.exists():
                self.add_error('item', 'Ya existe una partida con ese ítem.')
            elif '.' in item:  # OBR-42: la partida 09.09.09 necesita los títulos 09 y 09.09
                partes = item.split('.')
                padres = ['.'.join(partes[:n]) for n in range(1, len(partes))]
                existentes = set(Partida.objects.filter(presupuesto=self.presupuesto,
                                                        subpresupuesto=data.get('subpresupuesto'),
                                                        item__in=padres).values_list('item', flat=True))
                faltan = [x for x in padres if x not in existentes]
                if faltan:
                    self.add_error('item', f'Falta el título {", ".join(faltan)}: agréguelo antes de la partida '
                                           f'{item}.')
        for campo, nombre in (('metrado', 'El metrado'), ('precio', 'El precio')):
            if (data.get(campo) or 0) < 0:
                self.add_error(campo, f'{nombre} no puede ser negativo.')
        data['metrado'] = data.get('metrado') or 0
        data['precio'] = data.get('precio') or 0
        if not data.get('es_titulo') and not data.get('unidad'):
            self.add_error('unidad', 'Indique la unidad de la partida (o márquela como título).')
        return data


class SubpresupuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Subpresupuesto
        fields = ['codigo', 'nombre', 'es_gastos_generales']

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.presupuesto = presupuesto

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip()
        if Subpresupuesto.objects.filter(presupuesto=self.presupuesto, codigo=codigo).exclude(
                pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe un subpresupuesto con ese código.')
        return codigo


class RecursoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Recurso
        fields = ['codigo', 'nombre', 'unidad', 'tipo', 'categoria_mo', 'precio', 'producto', 'equipo',
                  'indice_unificado', 'es_subanalisis', 'rendimiento', 'jornada', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['producto'], 'productos')
        if self.instance.pk:
            self.fields['codigo'].disabled = True


class ImportarForm(BootstrapMixin, forms.Form):
    archivo = forms.FileField(label='Presupuesto (Excel .xlsx o .xls)',
                              help_text='Exportado de S10 (presupuesto, análisis, reporte del visor) o la plantilla de '
                                        'Ceiba')
    analisis = forms.FileField(label='Análisis de precios unitarios del S10 (opcional)', required=False,
                               help_text='El reporte de APU que el S10 exporta en CSV (.chr o .csv), si el Excel no los '
                                         'trae')
    tipo = forms.ChoiceField(choices=Presupuesto.TIPOS, initial='OFERTA')
    nombre = forms.CharField(max_length=250, required=False)
