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
                  'cantidad_minima', 'preferido', 'activo', 'observaciones']
        widgets = {'vigente_hasta': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

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
