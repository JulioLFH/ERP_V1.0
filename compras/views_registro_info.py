"""Compras › Abastecimiento › Proveedores por producto (registro info)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core.forms import BootstrapMixin, remoto
from core.models import Producto, Tercero

from .models import RegistroInfo


class RegistroInfoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = RegistroInfo
        fields = ['producto', 'proveedor', 'codigo_proveedor', 'moneda', 'precio', 'vigente_hasta', 'plazo_entrega',
                  'cantidad_minima', 'preferido', 'fijo_desde', 'fijo_hasta', 'homologado', 'bloqueado',
                  'motivo_bloqueo', 'calificacion', 'cuestionario', 'auditoria_fecha', 'muestras_aprobadas',
                  'calificado_hasta', 'nivel_inspeccion', 'activo', 'observaciones']
        widgets = {'vigente_hasta': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'auditoria_fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'calificado_hasta': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def clean(self):
        d = super().clean()
        if d.get('fijo_hasta') and not d.get('fijo_desde'):
            self.add_error('fijo_desde', 'Indique desde cuándo es el proveedor fijo.')
        if d.get('fijo_desde') and d.get('fijo_hasta') and d['fijo_hasta'] < d['fijo_desde']:
            self.add_error('fijo_hasta', 'La fecha final es anterior a la inicial.')
        if d.get('bloqueado') and not (d.get('motivo_bloqueo') or '').strip():
            self.add_error('motivo_bloqueo', 'Indique por qué se bloquea al proveedor para este producto.')
        if d.get('bloqueado') and (d.get('preferido') or d.get('fijo_desde')):
            self.add_error('bloqueado', 'Un proveedor bloqueado no puede ser el preferido ni el fijo.')
        return d

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False)
        self.fields['proveedor'].queryset = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS'], activo=True)
        remoto(self.fields['producto'], 'productos')
        remoto(self.fields['proveedor'], 'proveedores')


@login_required
def lista(request):
    if request.method == 'POST' and request.POST.get('accion') == 'cargar':
        from .registro_info import cargar_desde_compras
        creados, actualizados = cargar_desde_compras()
        messages.success(request, f'Registro info cargado desde las compras registradas: {creados} nuevo(s) y '
                                  f'{actualizados} actualizado(s) (último precio, fecha y número de compras).')
        return redirect('compras:registros_info')
    qs = RegistroInfo.objects.select_related('producto', 'proveedor')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q) |
                       Q(proveedor__nombre__icontains=q) | Q(proveedor__numero_doc__startswith=q))
    if not request.GET.get('inactivos'):
        qs = qs.filter(activo=True)
    if request.GET.get('formato') == 'excel':
        from core.utils import excel_response
        return excel_response('Registro_info', 'PROVEEDORES POR PRODUCTO', [
            'Código', 'Producto', 'RUC', 'Proveedor', 'Código del proveedor', 'Moneda', 'Precio', 'Vigente hasta',
            'Último precio', 'Última compra', 'Compras', 'Plazo (días)', 'Lote mínimo', 'Preferido'], [
            [r.producto.codigo, r.producto.nombre, r.proveedor.numero_doc, r.proveedor.nombre, r.codigo_proveedor,
             r.moneda, r.precio, r.vigente_hasta, r.ultimo_precio, r.ultima_compra, r.compras, r.plazo_entrega,
             r.cantidad_minima, 'Sí' if r.preferido else ''] for r in qs])
    return render(request, 'compras/registros_info.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q})


class HistorialForm(BootstrapMixin, forms.Form):
    producto = forms.ModelChoiceField(Producto.objects.filter(es_plantilla=False), required=False)
    proveedor = forms.ModelChoiceField(Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']), required=False,
                                       help_text='Vacío = todos')
    desde = forms.DateField(required=False)
    hasta = forms.DateField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        remoto(self.fields['producto'], 'productos')
        remoto(self.fields['proveedor'], 'proveedores')


@login_required
def historial_precios(request):
    """Evolución del precio de compra de un producto por proveedor (n.º 44)."""
    from core.models import Empresa

    from . import historial_precios as hp
    form = HistorialForm(request.GET or None)
    producto, filas = None, []
    if form.is_valid() and form.cleaned_data['producto']:
        d = form.cleaned_data
        producto = d['producto']
        filas = hp.historial(producto, d['proveedor'], d['desde'], d['hasta'])
        if request.GET.get('formato') == 'excel':
            from core.utils import excel_response
            return excel_response(f'Precios_{producto.codigo}', f'HISTORIAL DE PRECIOS DE COMPRA {producto.codigo} '
                                  f'{producto.nombre}', ['Fecha', 'RUC', 'Proveedor', 'Documento', 'Cantidad', 'Moneda',
                                                         'Precio', 'Precio S/', 'Variación %'],
                                  [[f['fecha'], f['proveedor'].numero_doc, f['proveedor'].nombre, f['documento'],
                                    f['cantidad'], f['moneda'], f['precio'], f['precio_pen'], f['variacion']]
                                   for f in filas])
    return render(request, 'compras/historial_precios.html', {
        'form': form, 'producto': producto, 'filas': filas, 'resumen': hp.resumen(filas),
        'grafico': hp.grafico(filas), 'alerta_pct': Empresa.actual().alerta_precio_pct})


@login_required
def editar(request, pk=None):
    r = get_object_or_404(RegistroInfo, pk=pk) if pk else RegistroInfo(
        producto_id=request.GET.get('producto') or None, preferido=False)
    form = RegistroInfoForm(request.POST or None, instance=r)
    if request.method == 'POST' and form.is_valid():
        r = form.save()
        messages.success(request, f'Registro info de {r.producto.nombre} con {r.proveedor.nombre} guardado.')
        return redirect('compras:registros_info')
    return render(request, 'core/form.html', {
        'form': form, 'titulo': 'Proveedor por producto (registro info)' if not pk else f'Registro info: {r}'})
