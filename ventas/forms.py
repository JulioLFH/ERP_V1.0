from django import forms

from core.forms import BootstrapMixin, validar_periodo_abierto
from core.sunat import DETRACCION_TIPOS
from core.models import Serie, Tercero

from .models import Cotizacion, Venta

SERIES_DEFECTO = {'01': 'F001', '03': 'B001', '07': 'FC01', '08': 'FD01', '12': 'T001', '00': 'NV01'}


class VentaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Venta
        fields = ['tipo_comprobante', 'serie', 'numero', 'tercero', 'fecha_emision', 'fecha_vencimiento',
                  'forma_pago', 'moneda', 'tipo_cambio', 'tipo_operacion', 'detraccion_pct', 'retencion_pct',
                  'percepcion_pct', 'icbper', 'detraccion_codigo', 'vendedor', 'cotizacion', 'doc_referencia',
                  'motivo_nota', 'descontar_stock', 'almacen', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2}),
                   'detraccion_codigo': forms.Select(choices=[('', '---')] + DETRACCION_TIPOS)}
        labels = {'tercero': 'Cliente', 'numero': 'Número (vacío = automático)'}
        help_texts = {'serie': 'Vacío = serie por defecto del tipo'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tipo_comprobante'].choices = [c for c in self.fields['tipo_comprobante'].choices
                                                   if c[0] in ('01', '03', '07', '08', '12', '00')]
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        self.fields['serie'].widget.attrs['list'] = 'series-venta'
        self.fields['cotizacion'].queryset = Cotizacion.objects.exclude(estado='ANULADO')
        self.fields['doc_referencia'].queryset = Venta.objects.filter(
            estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08'])
        self.series = Serie.objects.filter(activo=True, tipo__in=['01', '03', '07', '08', '12', '00'])
        if self.instance.pk:
            # no se renumeran comprobantes emitidos
            self.fields['serie'].disabled = True
            self.fields['numero'].disabled = True
            self.fields['tipo_comprobante'].disabled = True

    def clean(self):
        data = super().clean()
        tipo = data.get('tipo_comprobante')
        if tipo in ('07', '08') and not data.get('doc_referencia'):
            self.add_error('doc_referencia', 'Indique el comprobante que modifica la nota.')
        if tipo in ('07', '08') and not data.get('motivo_nota'):
            self.add_error('motivo_nota', 'Indique el motivo de la nota.')
        if not self.instance.pk and tipo:
            from core.permisos import validar_serie
            error = validar_serie(data, tipo, _serie_defecto(tipo, data.get('doc_referencia')))
            if error:
                self.add_error('serie', error)
        tercero = data.get('tercero')
        if tipo == '01' and tercero and tercero.tipo_doc != '6':
            self.add_error('tercero', 'Las facturas requieren un cliente con RUC.')
        validar_periodo_abierto(self, 'fecha_emision', self.instance.periodo if self.instance.pk else None)
        from inventario.cierre import error_cierre
        if data.get('descontar_stock') and tipo in ('01', '03', '12', '00', '07') and \
                error_cierre(data.get('fecha_emision')):
            self.add_error('fecha_emision', error_cierre(data.get('fecha_emision')))
        if self.instance.pk and self.instance.stock_aplicado and error_cierre(self.instance.fecha_emision):
            self.add_error(None, error_cierre(self.instance.fecha_emision))
        if self.instance.pk and data.get('descontar_stock') and \
                self.instance.operaciones_inventario.filter(estado='CONFIRMADO', tipo__clase='SALIDA').exists():
            self.add_error('descontar_stock', 'Este comprobante ya tiene despachos (salida por ventas) en Inventario. '
                                              'Desmarque esta opción para no mover el almacén dos veces.')
        return data

    def clean_serie(self):
        serie = (self.cleaned_data.get('serie') or '').upper().strip()
        tipo = self.data.get('tipo_comprobante') or self.instance.tipo_comprobante
        letra = _letra_serie(tipo, self._referencia())
        if serie and letra and not self.instance.pk and (not serie.startswith(letra) or len(serie) != 4):
            raise forms.ValidationError(f'Para este comprobante la serie debe tener 4 caracteres y empezar con '
                                        f'"{letra}" (ej. {letra}001).')
        return serie

    def _referencia(self):
        ref_id = self.data.get('doc_referencia')
        return Venta.objects.filter(pk=ref_id).first() if ref_id else self.instance.doc_referencia

    def validate_unique(self):
        # el número se asigna en save() cuando está vacío
        if self.instance.numero:
            super().validate_unique()

    def save(self, commit=True):
        venta = self.instance
        if not venta.pk:
            tipo = venta.tipo_comprobante
            venta.serie = (venta.serie or '').upper() or _serie_defecto(tipo, venta.doc_referencia)
            if not venta.numero:
                venta.serie, venta.numero = Serie.siguiente(tipo, venta.serie)
        return super().save(commit)


def _letra_serie(tipo, referencia=None):
    """SUNAT: facturas y sus notas empiezan con F; boletas y sus notas con B."""
    if tipo == '01':
        return 'F'
    if tipo == '03':
        return 'B'
    if tipo in ('07', '08'):
        return 'B' if referencia and referencia.tipo_comprobante == '03' else 'F'
    return ''


def _serie_defecto(tipo, referencia=None):
    letra = _letra_serie(tipo, referencia)
    qs = Serie.objects.filter(tipo=tipo, activo=True)
    if letra:
        qs = qs.filter(serie__startswith=letra)
    propia = qs.values_list('serie', flat=True).first()
    if propia:
        return propia
    if tipo in ('07', '08'):
        return f'{letra}{"C" if tipo == "07" else "D"}01'
    return SERIES_DEFECTO.get(tipo, 'S001')


class CotizacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Cotizacion
        fields = ['tipo', 'tercero', 'fecha', 'validez_dias', 'vendedor', 'moneda', 'tipo_cambio',
                  'tipo_operacion', 'condicion_pago', 'glosa']
        widgets = {'glosa': forms.Textarea(attrs={'rows': 2})}
        labels = {'tercero': 'Cliente'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])

    def save(self, commit=True):
        if not self.instance.numero:
            tipo = self.instance.tipo
            serie, numero = Serie.siguiente(tipo, 'CT01' if tipo == 'COT' else 'PD01')
            self.instance.numero = f'{serie}-{numero}'
        return super().save(commit)
