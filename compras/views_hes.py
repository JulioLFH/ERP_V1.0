"""Pantallas de la conformidad de servicio (HES): registro con adjuntos, liberación por niveles, vistas individuales
(mis HES, por liberar, de mi área, todas) y el acta imprimible."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core import aprobaciones
from core.forms import BootstrapMixin
from core.permisos import puede

from . import hes as srv
from .models import AdjuntoHES, ConformidadServicio, OrdenCompra


class MultiplesArchivos(forms.ClearableFileInput):
    allow_multiple_selected = True


class ArchivosField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultiplesArchivos(attrs={'accept': '.pdf,image/*'}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(ArchivosField, self).clean(d, initial) for d in data]
        return [super().clean(data, initial)] if data else []


class HESForm(BootstrapMixin, forms.ModelForm):
    informe = forms.FileField(label='Informe del proveedor firmado (PDF)', required=False,
                              widget=forms.ClearableFileInput(attrs={'accept': '.pdf,application/pdf'}))
    trabajo = ArchivosField(label='Documento de trabajo con imágenes', required=False,
                            help_text='Fotos del antes y después, parte de trabajo, acta (PDF o imágenes; varios)')

    class Meta:
        model = ConformidadServicio
        fields = ['oc_item', 'fecha', 'descripcion', 'porcentaje', 'cantidad', 'periodo_desde', 'periodo_hasta',
                  'lugar', 'responsable_proveedor', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, oc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['oc_item'].queryset = oc.items.all()
        self.fields['oc_item'].label_from_instance = (
            lambda i: f'{i.descripcion} · {i.cantidad:,.2f} · pendiente {srv.pendiente_linea(i):,.2f}')
        self.fields['oc_item'].required = oc.items.count() > 1
        self.fields['cantidad'].required = False
        self.fields['cantidad'].help_text = 'Vacío = sale del avance %'

    def clean_porcentaje(self):
        v = self.cleaned_data['porcentaje']
        if not 0 < v <= 100:
            raise forms.ValidationError('Entre 0 y 100 %.')
        return v


@login_required
def conformidad(request, oc_pk):
    oc = get_object_or_404(OrdenCompra, pk=oc_pk)
    form = HESForm(request.POST or None, request.FILES or None, oc=oc, initial={'descripcion': '; '.join(
        i.descripcion for i in oc.items.all())[:250]})
    if request.method == 'POST' and form.is_valid():
        hes = form.save(commit=False)
        hes.orden_compra = oc
        hes.cantidad = hes.cantidad or 0
        archivos = ([('INFORME', form.cleaned_data['informe'])] if form.cleaned_data.get('informe') else []) + \
            [('TRABAJO', a) for a in form.cleaned_data.get('trabajo') or []]
        try:
            srv.registrar(hes, request.user, archivos)
            if hes.tiene_adjuntos and request.POST.get('enviar'):
                srv.enviar(hes, request.user)
            messages.success(request, f'{hes} registrada ({hes.get_estado_display().lower()}).')
            return redirect('compras:hes', hes.pk)
        except srv.ErrorHES as exc:
            form.add_error(None, str(exc))
    return render(request, 'compras/conformidad_form.html', {'form': form, 'oc': oc})


VISTAS = [('mias', 'Mis conformidades'), ('liberar', 'Por liberar'), ('area', 'De mi área'),
          ('todas', 'Todas')]


@login_required
def hes_lista(request):
    vista = request.GET.get('vista') or 'mias'
    if vista == 'todas' and not puede(request.user, 'compras.hes_todas'):
        vista = 'mias'
    qs = srv.visibles(request.user, vista)
    if vista != 'liberar':
        for campo in ('estado',):
            if request.GET.get(campo):
                qs = qs.filter(**{campo: request.GET[campo]})
        if request.GET.get('sin_facturar'):
            qs = [h for h in qs.filter(estado='LIBERADA') if srv.facturado(h) < h.cantidad]
    if request.GET.get('formato') == 'excel':
        from core.utils import excel_response
        filas = [[h.numero, h.fecha.strftime('%d/%m/%Y'), h.orden_compra.numero, h.orden_compra.tercero.nombre,
                  h.orden_compra.centro_costo.codigo if h.orden_compra.centro_costo_id else '', h.descripcion,
                  h.cantidad, h.orden_compra.moneda, h.valor, h.get_estado_display(), srv.facturado(h)] for h in qs]
        return excel_response('Conformidades_de_servicio', 'Conformidades de servicio (HES)',
                              ['N°', 'Fecha', 'OC', 'Proveedor', 'Centro de costo', 'Servicio', 'Cantidad', 'Moneda',
                               'Valor', 'Estado', 'Facturado'], filas)
    return render(request, 'compras/hes_lista.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'vista': vista,
        'vistas': [v for v in VISTAS if v[0] != 'todas' or puede(request.user, 'compras.hes_todas')],
        'estados': ConformidadServicio.ESTADOS, 'por_liberar': len(srv.visibles(request.user, 'liberar'))})


@login_required
def hes(request, pk):
    h = get_object_or_404(ConformidadServicio.objects.select_related('orden_compra__tercero', 'oc_item',
                                                                     'conforme_por', 'liberado_por'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'adjuntar':
                for a in request.FILES.getlist('archivos'):
                    srv.adjuntar(h, request.POST.get('tipo'), a, request.user)
                messages.success(request, 'Adjunto(s) agregado(s).')
            elif accion == 'enviar':
                srv.enviar(h, request.user)
                messages.success(request, f'{h} enviada a liberación.')
            elif accion == 'anular':
                srv.anular(h, request.user, request.POST.get('motivo', ''))
                messages.success(request, f'{h} anulada.')
            else:
                srv.resolver(h, request.user, accion, request.POST.get('motivo', ''))
                messages.success(request, f'{h}: {h.get_estado_display().lower()}.')
        except srv.ErrorHES as exc:
            messages.error(request, str(exc))
        return redirect('compras:hes', pk)
    return render(request, 'compras/hes.html', {
        'h': h, 'adjuntos': h.adjuntos.select_related('subido_por'), 'tipos_adjunto': AdjuntoHES.TIPOS,
        'puede_liberar': srv.puede_liberar(request.user, h),
        'pendiente': aprobaciones.pendiente(h.niveles, h.nivel_aprobado),
        'historial': aprobaciones.historial('HES', h), 'facturado': srv.facturado(h),
        'facturas': h.lineas_factura.select_related('factura')})


@login_required
def hes_adjunto(request, pk):
    a = get_object_or_404(AdjuntoHES, pk=pk)
    if not a.contenido:
        raise Http404
    resp = HttpResponse(bytes(a.contenido), content_type=a.tipo_contenido)
    resp['Content-Disposition'] = f'inline; filename="{a.nombre}"'
    return resp
