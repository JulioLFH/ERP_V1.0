"""Pantallas de requerimientos internos: el área pide y aprueba (módulo Requerimientos); el almacén atiende
(módulo Inventario)."""
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
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
        # el consumo de suministros es el gasto del área que los pide
        self.fields['centro_costo'].required = True
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True, uso='')
        if not self.instance.pk:
            self.initial.setdefault('almacen', Almacen.principal().pk)

    def clean(self):
        """REQ-04: el pedido es de hoy o de días recientes (no futuro, no de un periodo cerrado) y la fecha requerida
        no es anterior al pedido."""
        from datetime import timedelta

        from django.utils import timezone

        from core.forms import validar_periodo_abierto
        datos = super().clean()
        hoy, fecha, requerida = timezone.localdate(), datos.get('fecha'), datos.get('fecha_requerida')
        if fecha and fecha > hoy:
            self.add_error('fecha', 'No puede ser futura.')
        elif fecha and fecha < hoy - timedelta(days=DIAS_ATRAS):
            self.add_error('fecha', f'Máximo {DIAS_ATRAS} días atrás: registre el pedido con la fecha de hoy.')
        else:
            validar_periodo_abierto(self, 'fecha')
        if requerida and fecha and requerida < fecha:
            self.add_error('fecha_requerida', 'No puede ser anterior a la fecha del pedido.')
        elif requerida and requerida > hoy + timedelta(days=365):
            self.add_error('fecha_requerida', 'Máximo un año adelante.')
        return datos


DIAS_ATRAS = 31
MAXIMO_ITEM = Decimal('99999999')


class _ItemsBase(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        vistos = set()
        for f in self.forms:
            d = getattr(f, 'cleaned_data', None) or {}
            if d.get('DELETE') or not d.get('producto'):
                continue
            if d['producto'].pk in vistos:  # REQ-04
                raise forms.ValidationError(f'{d["producto"].nombre} se repite: súmelo en una sola línea.')
            vistos.add(d['producto'].pk)


class ItemForm(forms.ModelForm):
    class Meta:
        model = RequerimientoItem
        fields = ['producto', 'cantidad', 'observacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True, tipo='BIEN', es_plantilla=False)
        self.fields['producto'].widget.attrs['class'] = 'form-select form-select-sm'
        from core.forms import remoto
        remoto(self.fields['producto'], 'productos_bienes')
        self.fields['cantidad'].widget.attrs['class'] = 'form-control form-control-sm text-end'
        self.fields['observacion'].widget.attrs['class'] = 'form-control form-control-sm'

    def clean_cantidad(self):
        valor = self.cleaned_data['cantidad']
        if valor is not None and valor <= 0:
            raise forms.ValidationError('Debe ser mayor a cero.')
        if valor is not None and valor > MAXIMO_ITEM:
            raise forms.ValidationError(f'Máximo {MAXIMO_ITEM:,.0f} por línea.')
        return valor


ItemsFormSet = inlineformset_factory(RequerimientoInterno, RequerimientoItem, form=ItemForm, formset=_ItemsBase,
                                     extra=0, can_delete=True, min_num=1, validate_min=True)


def _puede_ver_todos(user):
    return user.is_superuser or puede(user, 'requerimientos.aprobar') or puede(user, 'inventario.operar')


@login_required
def lista(request):
    qs = RequerimientoInterno.objects.select_related('centro_costo', 'solicitante', 'almacen')
    if not _puede_ver_todos(request.user):  # los suyos y los que le toca aprobar (jefe, responsable del centro…)
        mios = [r.pk for r in qs.filter(estado='ENVIADO').exclude(niveles=[]) if srv.puede_aprobar(request.user, r)]
        qs = qs.filter(Q(solicitante=request.user) | Q(pk__in=mios))
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
        sin_stock = [f'{i.producto.nombre} (pide {i.cantidad:,.2f}, hay {i.producto.stock_en(req.almacen):,.2f})'
                     for i in req.items.select_related('producto') if i.cantidad > i.producto.stock_en(req.almacen)]
        if sin_stock:  # REQ-04: aviso frente al stock del almacén
            messages.warning(request, 'Más de lo que hay en stock: ' + '; '.join(sin_stock[:5]) +
                             '. Lo que falte se pide a compras al aprobarlo.')
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
    if req.solicitante_id != request.user.pk and not _puede_ver_todos(request.user) and \
            not srv.puede_aprobar(request.user, req):
        messages.error(request, 'Solo puede ver sus requerimientos.')
        return redirect('requerimientos:lista')
    items = list(req.items.select_related('producto', 'orden_compra').prefetch_related(
        'lineas_solped__solicitud', 'lineas_solped__items_oc__documento', 'lineas_solped__items_oc__producto'))
    for i in items:
        i.stock = i.producto.stock_en(req.almacen)
        i.solped = [l for l in i.lineas_solped.all() if l.solicitud.estado not in ('RECHAZADA', 'ANULADA')]
    return render(request, 'inventario/requerimiento_detalle.html', {
        'req': req, 'items': items, 'atenciones': req.atenciones.all(),
        'puede_aprobar': srv.puede_aprobar(request.user, req),
        'pendiente': srv.pendiente(req), 'historial': srv.historial(req),
        'puede_atender': srv.puede_atender(request.user, req),
        'almacen_puede': puede(request.user, 'inventario.operar'),
        'en_lugar_de': srv.en_lugar_de(request.user, req) if req.estado == 'ENVIADO' else None,
        'puede_comprar': puede(request.user, 'requerimientos.aprobar') or puede(request.user, 'inventario.operar'),
        'faltantes': srv.faltantes(req)})


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
            if req.estado != 'ENVIADO':  # REQ-04: antes decía «No le corresponde aprobar» a un RQ ya atendido
                raise srv.ErrorRequerimiento(f'{req} está «{req.get_estado_display()}»: solo se aprueba o rechaza '
                                             f'un requerimiento enviado y pendiente.')
            if not srv.puede_aprobar(request.user, req):
                raise srv.ErrorRequerimiento('No le corresponde aprobar este requerimiento (su nivel, su permiso o '
                                             'es suyo).')
            if que == 'aprobar':
                srv.aprobar(req, request.user, request.POST.get('motivo', ''))
                if req.estado != 'APROBADO':
                    n = srv.pendiente(req)
                    messages.success(request, f'{req}: nivel {req.nivel_aprobado} aprobado; pasa a {n["nombre"]}.')
                    return redirect('requerimientos:detalle', pk)
                sp = req.solicitudes.exclude(estado__in=['RECHAZADA', 'ANULADA']).first()
                messages.success(request, f'{req} aprobado: el almacén ya puede atenderlo.' + (
                    f' Lo que no hay en stock pasó a la solicitud de pedido {sp.numero}.' if sp else ''))
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
            if not (request.POST.get('recibe') or '').strip():
                raise srv.ErrorRequerimiento('Indique quién recibe los materiales.')
            cantidades = {i.pk: _decimal(request.POST.get(f'entregar_{i.pk}')) for i in req.items.all()}
            op = srv.atender(req, request.user, cantidades, recibe=request.POST.get('recibe', ''))
            messages.success(request, f'Entrega registrada ({op.numero}). Estado: {req.get_estado_display()}.')
        elif que == 'comprar':
            if not (puede(request.user, 'requerimientos.aprobar') or puede(request.user, 'inventario.operar')):
                raise srv.ErrorRequerimiento('Su usuario no pide faltantes a compras.')
            sp = srv.pasar_a_compras(req, request.user)
            messages.success(request, f'Faltantes pedidos a compras en la solicitud de pedido {sp.numero}.')
    except srv.ErrorRequerimiento as exc:
        messages.error(request, str(exc))
    return redirect('requerimientos:detalle', pk)
