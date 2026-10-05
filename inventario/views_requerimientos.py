"""Pantallas de requerimientos internos: el área pide y aprueba (módulo Requerimientos); el almacén atiende
(módulo Inventario)."""
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render

from core.forms import BootstrapMixin
from core.models import Almacen, Producto
from core.permisos import puede

from . import requerimientos as srv
from .models import RequerimientoInterno, RequerimientoItem


class RequerimientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = RequerimientoInterno
        fields = ['fecha', 'fecha_requerida', 'centro_costo', 'almacen', 'cuenta_gasto', 'motivo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CentroCosto
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True, uso='')
        if not self.instance.pk:
            self.initial.setdefault('almacen', Almacen.principal().pk)


class ItemForm(forms.ModelForm):
    class Meta:
        model = RequerimientoItem
        fields = ['producto', 'cantidad', 'observacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, tipo='BIEN')
        self.fields['producto'].widget.attrs['class'] = 'form-select form-select-sm'
        self.fields['cantidad'].widget.attrs['class'] = 'form-control form-control-sm text-end'
        self.fields['observacion'].widget.attrs['class'] = 'form-control form-control-sm'

    def clean_cantidad(self):
        valor = self.cleaned_data['cantidad']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        return valor


ItemsFormSet = inlineformset_factory(RequerimientoInterno, RequerimientoItem, form=ItemForm, extra=0,
                                     can_delete=True, min_num=1, validate_min=True)


def _puede_ver_todos(user):
    return user.is_superuser or puede(user, 'requerimientos.aprobar') or puede(user, 'inventario.operar')


@login_required
def lista(request):
    qs = RequerimientoInterno.objects.select_related('centro_costo', 'solicitante', 'almacen')
    if not _puede_ver_todos(request.user):
        qs = qs.filter(solicitante=request.user)
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    return render(request, 'inventario/requerimientos.html', {
        'reqs': qs[:200], 'estados': RequerimientoInterno.ESTADOS, 'por_aprobar': qs.filter(estado='ENVIADO').count()})


def _guardar(request, req, titulo):
    form = RequerimientoForm(request.POST or None, instance=req)
    items = ItemsFormSet(request.POST or None, instance=req, prefix='it')
    if request.method == 'POST' and form.is_valid() and items.is_valid():
        with transaction.atomic():
            nuevo = not req.pk
            req = form.save(commit=False)
            if nuevo:
                req.solicitante = request.user
            req.save()
            items.instance = req
            items.save()
            if request.POST.get('accion') == 'enviar':
                srv.enviar(req, request.user)
                messages.success(request, f'{req} enviado para su aprobación.')
            else:
                messages.success(request, 'Requerimiento guardado en borrador.')
        return redirect('requerimientos:detalle', req.pk)
    return render(request, 'inventario/requerimiento_form.html', {'form': form, 'items': items, 'titulo': titulo,
                                                                  'req': req})


@login_required
def nuevo(request):
    return _guardar(request, RequerimientoInterno(), 'Nuevo requerimiento de materiales')


@login_required
def editar(request, pk):
    req = get_object_or_404(RequerimientoInterno, pk=pk)
    if req.estado != 'BORRADOR' or (req.solicitante_id != request.user.pk and not request.user.is_superuser):
        messages.error(request, 'Solo quien lo pidió edita el requerimiento, y mientras esté en borrador.')
        return redirect('requerimientos:detalle', pk)
    return _guardar(request, req, f'Editar {req}')


@login_required
def detalle(request, pk):
    req = get_object_or_404(RequerimientoInterno.objects.select_related(
        'centro_costo', 'cuenta_gasto', 'almacen', 'solicitante', 'aprobado_por'), pk=pk)
    if req.solicitante_id != request.user.pk and not _puede_ver_todos(request.user):
        messages.error(request, 'Solo puede ver sus requerimientos.')
        return redirect('requerimientos:lista')
    items = list(req.items.select_related('producto', 'orden_compra'))
    for i in items:
        i.stock = i.producto.stock_en(req.almacen)
    return render(request, 'inventario/requerimiento_detalle.html', {
        'req': req, 'items': items, 'atenciones': req.atenciones.all(),
        'puede_aprobar': puede(request.user, 'requerimientos.aprobar') and (
            req.solicitante_id != request.user.pk or request.user.is_superuser),
        'puede_atender': puede(request.user, 'inventario.operar'),
        'puede_comprar': puede(request.user, 'compras.oc'), 'faltantes': srv.faltantes(req)})


def _decimal(valor):
    try:
        return Decimal(str(valor).replace(',', '').strip()) if valor not in (None, '') else None
    except (InvalidOperation, ValueError):
        return None


@login_required
def accion(request, pk, que):
    req = get_object_or_404(RequerimientoInterno, pk=pk)
    if request.method != 'POST':
        return redirect('requerimientos:detalle', pk)
    try:
        if que == 'enviar':
            if req.solicitante_id != request.user.pk and not request.user.is_superuser:
                raise srv.ErrorRequerimiento('Solo quien lo pidió lo envía.')
            srv.enviar(req, request.user)
            messages.success(request, f'{req} enviado para su aprobación.')
        elif que in ('aprobar', 'rechazar'):
            if not puede(request.user, 'requerimientos.aprobar'):
                raise srv.ErrorRequerimiento('Su usuario no aprueba requerimientos.')
            if que == 'aprobar':
                srv.aprobar(req, request.user)
                messages.success(request, f'{req} aprobado: el almacén ya puede atenderlo.')
            else:
                srv.rechazar(req, request.user, request.POST.get('motivo', ''))
                messages.success(request, f'{req} rechazado.')
        elif que == 'anular':
            if req.solicitante_id != request.user.pk and not puede(request.user, 'requerimientos.aprobar'):
                raise srv.ErrorRequerimiento('Solo quien lo pidió o un aprobador lo anula.')
            srv.rechazar(req, request.user, request.POST.get('motivo', ''), anular=True)
            messages.success(request, f'{req} anulado.')
        elif que == 'atender':
            if not puede(request.user, 'inventario.operar'):
                raise srv.ErrorRequerimiento('Solo el almacén atiende requerimientos.')
            cantidades = {i.pk: _decimal(request.POST.get(f'entregar_{i.pk}')) for i in req.items.all()}
            op = srv.atender(req, request.user, cantidades)
            messages.success(request, f'Entrega registrada ({op.numero}). Estado: {req.get_estado_display()}.')
        elif que == 'comprar':
            if not puede(request.user, 'compras.oc'):
                raise srv.ErrorRequerimiento('Su usuario no crea órdenes de compra.')
            ordenes = srv.pasar_a_compras(req, request.user)
            messages.success(request, 'Faltantes pedidos en: ' + (', '.join(o.numero for o in ordenes) or 'ninguna'))
    except srv.ErrorRequerimiento as exc:
        messages.error(request, str(exc))
    return redirect('requerimientos:detalle', pk)
