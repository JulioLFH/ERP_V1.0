"""Estado de Flujos de Efectivo (NIC 7): reporte por el método directo e indirecto y su configuración (rubros, reglas
por cuenta y políticas)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from core.forms import BootstrapMixin
from core.utils import excel_response, fmt_fecha

from . import flujo_efectivo
from .models import PoliticaFlujo, ReglaFlujoCuenta, RubroFlujo
from .views import _mes_input, _periodo, _periodo_txt, al_dia, con_norma


@login_required
@al_dia
@con_norma
def estado_flujo(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    if desde > hasta:
        desde, hasta = hasta, desde
    codigo = request.GET.get('detalle', '')
    if codigo:
        rubro = RubroFlujo.objects.filter(codigo=codigo).first()
        filas = flujo_efectivo.detalle_rubro(desde, hasta, codigo)
        if request.GET.get('formato') == 'excel':
            return excel_response(f'Flujo_{codigo}_{desde}_{hasta}', f'{rubro or codigo} {desde}-{hasta}',
                                  ['Fecha', 'Asiento', 'Glosa', 'Importe S/'],
                                  [[fmt_fecha(a.fecha), a.numero, a.glosa, v] for a, v in filas])
        return render(request, 'contabilidad/flujo_detalle.html', {
            'rubro': rubro, 'codigo': codigo, 'filas': filas, 'desde': desde, 'hasta': hasta,
            'total': sum((v for _, v in filas), flujo_efectivo.D0)})
    e = flujo_efectivo.estado(desde, hasta)
    metodo = request.GET.get('metodo') if request.GET.get('metodo') in ('directo', 'indirecto') else 'ambos'
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Estado_flujos_efectivo_{desde}_{hasta}',
                              f'ESTADO DE FLUJOS DE EFECTIVO {_periodo_txt(desde)} - {_periodo_txt(hasta)} (S/)',
                              ['Concepto', 'Importe S/'], _filas_excel(e, metodo))
    return render(request, 'contabilidad/flujo_efectivo.html', {
        'e': e, 'desde': desde, 'hasta': hasta, 'metodo': metodo,
        'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta)})


def _filas_excel(e, metodo):
    filas = []
    if metodo in ('directo', 'ambos'):
        filas.append(['MÉTODO DIRECTO', ''])
        for s in e['secciones']:
            filas.append([s['titulo'], ''])
            filas += [[f'   {f["rubro"].nombre}', f['importe']] for f in s['filas']]
            filas.append([f'Flujo neto de {s["titulo"].lower()}', s['total']])
        filas += [['AUMENTO (DISMINUCIÓN) NETO DEL EFECTIVO', e['neto']],
                  ['Efecto de las variaciones en el tipo de cambio sobre el efectivo', e['efecto_tc']],
                  ['Efectivo y equivalentes al inicio del periodo', e['inicial']],
                  ['EFECTIVO Y EQUIVALENTES AL FINAL DEL PERIODO', e['final']], ['', '']]
    if metodo in ('indirecto', 'ambos'):
        i = e['indirecto']
        filas += [['MÉTODO INDIRECTO: CONCILIACIÓN DE LA UTILIDAD NETA', ''], ['Utilidad (pérdida) neta', i['utilidad']]]
        filas += [[f'   {n}', v] for n, v in i['ajustes']]
        filas += [[f'   {n}', v] for n, v in i['variaciones']]
        filas.append(['FLUJO NETO DE ACTIVIDADES DE OPERACIÓN', i['operacion']])
    return filas


# ---------------------------------------------------------------- configuración
class PoliticaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PoliticaFlujo
        fields = ['intereses_pagados', 'intereses_cobrados', 'dividendos_pagados', 'detracciones_en_efectivo']


class ReglaForm(forms.ModelForm):
    class Meta:
        model = ReglaFlujoCuenta
        fields = ['prefijo', 'rubro_entrada', 'rubro_salida']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in ('rubro_entrada', 'rubro_salida'):
            self.fields[campo].queryset = RubroFlujo.objects.filter(activo=True)
            self.fields[campo].widget.attrs['class'] = 'form-select form-select-sm'
        self.fields['prefijo'].widget.attrs.update({'class': 'form-control form-control-sm', 'style': 'width:110px'})

    def clean_prefijo(self):
        prefijo = (self.cleaned_data.get('prefijo') or '').strip()
        if not prefijo.isdigit():
            raise forms.ValidationError('Solo dígitos del PCGE.')
        return prefijo


class RubroForm(forms.ModelForm):
    class Meta:
        model = RubroFlujo
        fields = ['codigo', 'nombre', 'actividad', 'sentido', 'rubro_smv', 'orden', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, campo in self.fields.items():
            campo.widget.attrs['class'] = ('form-check-input' if nombre == 'activo' else
                                           'form-select form-select-sm' if nombre in ('actividad', 'sentido') else
                                           'form-control form-control-sm')
        if self.instance.pk:  # el código y la actividad de un rubro con uso no cambian
            self.fields['codigo'].disabled = True


ReglasFormSet = forms.modelformset_factory(ReglaFlujoCuenta, form=ReglaForm, extra=3, can_delete=True)
RubrosFormSet = forms.modelformset_factory(RubroFlujo, form=RubroForm, extra=1)


@login_required
def configuracion_flujo(request):
    from . import pcge
    politica = PoliticaFlujo.actual()
    form = PoliticaForm(request.POST or None, instance=politica, prefix='pol')
    reglas = ReglasFormSet(request.POST or None, queryset=ReglaFlujoCuenta.objects.all(), prefix='reglas')
    rubros = RubrosFormSet(request.POST or None, queryset=RubroFlujo.objects.all(), prefix='rubros')
    if request.method == 'POST':
        if form.is_valid() and reglas.is_valid() and rubros.is_valid():
            form.save()
            rubros.save()
            reglas.save()
            messages.success(request, 'Configuración del estado de flujos de efectivo guardada.')
            return redirect('contabilidad:flujo_configuracion')
        messages.error(request, 'Revise los datos marcados.')
    return render(request, 'contabilidad/flujo_configuracion.html', {
        'form': form, 'reglas': reglas, 'rubros': rubros, 'plan_2026': pcge.plan() == pcge.PCGE2026})
