from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, UpdateView

from compras.models import Compra, OrdenCompra
from core.models import Empresa, Producto
from core.utils import rango_por_defecto
from core.views import FormGenerico, ListaGenerica
from ventas.models import Venta

from . import servicios
from .forms import OperacionForm, TipoOperacionForm, operacion_formset
from .models import Operacion, TipoOperacion

GRUPOS = {
    'recepciones': ('Recepciones e ingresos', ['INGRESO']),
    'salidas': ('Despachos y salidas', ['SALIDA']),
    'traslados': ('Traslados y tránsito', ['TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION']),
    'manufactura': ('Manufactura', ['MANUFACTURA']),
}


@login_required
def lista(request):
    qs = Operacion.objects.select_related('tipo', 'almacen_origen', 'almacen_destino', 'tercero', 'compra',
                                          'orden_compra', 'venta', 'envio')
    grupo = request.GET.get('grupo', '')
    titulo = 'Operaciones de inventario'
    if grupo in GRUPOS:
        titulo, clases = GRUPOS[grupo]
        qs = qs.filter(tipo__clase__in=clases)
    tipos = TipoOperacion.objects.filter(activo=True)
    if grupo in GRUPOS:
        tipos = tipos.filter(clase__in=GRUPOS[grupo][1])
    if request.GET.get('tipo'):
        qs = qs.filter(tipo_id=request.GET['tipo'])
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(referencia__icontains=q) | Q(glosa__icontains=q) |
                       Q(tercero__nombre__icontains=q) | Q(items__producto__nombre__icontains=q)).distinct()
    desde, hasta = rango_por_defecto(request, qs)
    if not request.GET.get('todo'):
        qs = qs.filter(Q(fecha__range=[desde, hasta]) | Q(estado='BORRADOR'))
    # traslados a tránsito con mercadería aún por recibir
    en_transito = []
    if grupo in ('', 'traslados'):
        for envio in Operacion.objects.filter(estado='CONFIRMADO', tipo__clase='TRANSITO_ENVIO').select_related(
                'almacen_destino'):
            if servicios.pendientes(Operacion(tipo=_tipo_recepcion_transito() or envio.tipo, envio=envio)):
                en_transito.append(envio)
    return render(request, 'inventario/operaciones.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'grupo': grupo, 'titulo': titulo,
        'tipos': tipos, 'q': q, 'desde': desde, 'hasta': hasta, 'en_transito': en_transito,
        'estados': Operacion.ESTADOS, 'recepcion_transito': _tipo_recepcion_transito()})


def _tipo_recepcion_transito():
    return TipoOperacion.objects.filter(clase='TRANSITO_RECEPCION', activo=True).first()


def _contexto_form(op, titulo):
    tipo = op.tipo
    return {'titulo': titulo, 'op': op, 'tipo': tipo,
            'muestra_costo': tipo.requiere_costo or tipo.clase == 'INGRESO',
            'muestra_rol': tipo.clase == 'MANUFACTURA',
            'productos': list(Producto.objects.filter(activo=True, tipo='BIEN').values(
                'id', 'nombre', 'unidad', 'costo_promedio'))}


def _guardar(request, op, titulo, initial=None, items=None):
    formset_cls = operacion_formset(extra=0)  # min_num ya muestra una fila
    if request.method == 'POST':
        form = OperacionForm(request.POST, instance=op, tipo=op.tipo)
        formset = formset_cls(request.POST, instance=form.instance)
        if form.is_valid() and formset.is_valid():
            if op.tipo.clase == 'MANUFACTURA':
                for f in formset.forms:
                    cd = getattr(f, 'cleaned_data', None) or {}
                    if cd and not cd.get('DELETE') and cd.get('producto') and not cd.get('rol'):
                        f.add_error('rol', 'Indique si es insumo o producto terminado.')
            if formset.is_valid():
                with transaction.atomic():
                    nuevo = not op.pk
                    op = form.save(commit=False)
                    if nuevo:
                        op.creado_por = request.user
                    op.save()
                    formset.instance = op
                    formset.save()
                if request.POST.get('accion') == 'confirmar':
                    return _confirmar(request, op)
                messages.success(request, 'Operación guardada en borrador. Revise y confirme para mover el almacén.')
                return redirect('inventario:detalle', op.pk)
    else:
        form = OperacionForm(instance=op, tipo=op.tipo, initial=initial)
        if items:
            fs_cls = operacion_formset(extra=max(len(items) - 1, 0))
            formset = fs_cls(instance=op, initial=items)
        else:
            formset = formset_cls(instance=op)
    ctx = _contexto_form(op, titulo)
    ctx.update(form=form, formset=formset)
    return render(request, 'inventario/operacion_form.html', ctx)


@login_required
def nueva(request):
    codigo = request.GET.get('tipo')
    if not codigo:
        tipos = TipoOperacion.objects.filter(activo=True)
        grupo = request.GET.get('grupo', '')
        bloques = [(clave, nombre, [t for t in tipos if t.clase in clases]) for clave, (nombre, clases)
                   in GRUPOS.items() if not grupo or grupo == clave]
        return render(request, 'inventario/operacion_tipo.html', {'bloques': bloques})
    tipo = get_object_or_404(TipoOperacion, codigo=codigo, activo=True)
    op = Operacion(tipo=tipo)
    initial, items = {}, None
    origen = None
    if request.GET.get('oc'):
        origen = op.orden_compra = get_object_or_404(OrdenCompra, pk=request.GET['oc'])
        initial['orden_compra'] = origen.pk
    elif request.GET.get('compra'):
        origen = op.compra = get_object_or_404(Compra, pk=request.GET['compra'])
        initial['compra'] = origen.pk
        initial['almacen_origen'] = initial['almacen_destino'] = origen.almacen_id
    elif request.GET.get('venta'):
        origen = op.venta = get_object_or_404(Venta, pk=request.GET['venta'])
        initial['venta'] = origen.pk
        initial['almacen_origen'] = initial['almacen_destino'] = origen.almacen_id
    elif request.GET.get('envio'):
        origen = op.envio = get_object_or_404(Operacion, pk=request.GET['envio'], tipo__clase='TRANSITO_ENVIO')
        initial['envio'] = origen.pk
        initial['almacen_destino'] = origen.almacen_destino_id
    if origen is not None:
        items = servicios.items_desde_origen(op)
        if not items:
            messages.warning(request, f'{origen}: no tiene cantidades pendientes para "{tipo.nombre}".')
        initial['referencia'] = str(origen)[:60]
    initial = {k: v for k, v in initial.items() if v}
    return _guardar(request, op, f'Nueva operación: {tipo.nombre}', initial, items)


@login_required
def editar(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if op.estado != 'BORRADOR':
        messages.error(request, 'Solo se editan operaciones en borrador.')
        return redirect('inventario:detalle', pk)
    return _guardar(request, op, f'Editar {op}')


@login_required
def pendientes_origen(request, pk):
    """Recalcula los ítems de un borrador con lo pendiente del documento de origen."""
    op = get_object_or_404(Operacion, pk=pk, estado='BORRADOR')
    if request.method == 'POST':
        with transaction.atomic():
            op.items.all().delete()
            for fila in servicios.items_desde_origen(op):
                op.items.create(producto_id=fila['producto'], cantidad=fila['cantidad'],
                                costo_unitario=fila['costo_unitario'])
        messages.success(request, 'Ítems actualizados con lo pendiente del documento de origen.')
    return redirect('inventario:detalle', pk)


@login_required
def detalle(request, pk):
    op = get_object_or_404(Operacion.objects.select_related(
        'tipo', 'almacen_origen', 'almacen_destino', 'tercero', 'orden_compra', 'compra', 'venta', 'envio',
        'creado_por', 'confirmado_por', 'anulado_por'), pk=pk)
    items = list(op.items.select_related('producto'))
    ctx = {'op': op, 'items': items, 'total': sum((i.valor for i in items), 0),
           'errores': servicios.errores_confirmacion(op) if op.estado == 'BORRADOR' else [],
           'bloqueo_anular': servicios.errores_anulacion(op) if op.estado == 'CONFIRMADO' else [],
           'recepciones': op.recepciones.select_related('tipo') if op.tipo.clase == 'TRANSITO_ENVIO' else [],
           'pendiente_transito': op.tipo.clase == 'TRANSITO_ENVIO' and op.estado == 'CONFIRMADO' and bool(
               servicios.pendientes(Operacion(tipo=_tipo_recepcion_transito() or op.tipo, envio=op))),
           'recepcion_transito': _tipo_recepcion_transito()}
    # nota de crédito sugerida tras una devolución
    if op.estado == 'CONFIRMADO' and op.tipo.origen in ('VENTA', 'COMPRA'):
        doc = op.venta if op.tipo.clase == 'INGRESO' and op.venta_id else (
            op.compra if op.tipo.clase == 'SALIDA' and op.compra_id else None)
        if doc is not None:
            app = 'ventas' if op.venta_id else 'compras'
            ctx['url_nota'] = f"{reverse(f'{app}:nuevo')}?ref={doc.pk}&tipo=07&motivo=07&op={op.pk}"
            ctx['app_nota'] = app
    return render(request, 'inventario/operacion_detalle.html', ctx)


def _confirmar(request, op):
    try:
        servicios.confirmar(op, request.user)
        messages.success(request, f'{op.tipo.nombre} {op.numero} confirmada: almacén actualizado.')
    except servicios.ErrorOperacion as exc:
        messages.error(request, f'No se pudo confirmar: {exc}')
    return redirect('inventario:detalle', op.pk)


@login_required
def confirmar(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if request.method == 'POST':
        return _confirmar(request, op)
    return redirect('inventario:detalle', pk)


@login_required
def anular(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if request.method == 'POST':
        try:
            servicios.anular(op, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{op} anulada: se revirtió el movimiento de almacén.')
        except servicios.ErrorOperacion as exc:
            messages.error(request, f'No se pudo anular: {exc}')
    return redirect('inventario:detalle', pk)


@login_required
def eliminar(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if request.method == 'POST':
        if op.estado != 'BORRADOR':
            messages.error(request, 'Solo se eliminan borradores; las operaciones confirmadas se anulan.')
            return redirect('inventario:detalle', pk)
        op.delete()
        messages.success(request, 'Borrador eliminado.')
    return redirect('inventario:lista')


@login_required
def imprimir(request, pk):
    op = get_object_or_404(Operacion.objects.select_related('tipo'), pk=pk)
    items = list(op.items.select_related('producto'))
    return render(request, 'inventario/operacion_imprimir.html', {
        'op': op, 'items': items, 'total': sum((i.valor for i in items), 0), 'empresa': Empresa.actual()})


# ---------------------------------------------------------------- tipos de operación
class TipoLista(ListaGenerica):
    model = TipoOperacion
    titulo = 'Tipos de operación de inventario'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Clase', 'get_clase_display'),
                ('Documento de origen', 'get_origen_display'), ('Cuenta contable', 'cuenta_contable'),
                ('Tabla 12 SUNAT', 'codigo_sunat'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'inventario:tipo_nuevo', 'inventario:tipo_editar'
    buscar_en = ['codigo', 'nombre']


class TipoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = TipoOperacion, TipoOperacionForm, 'Nuevo tipo de operación'
    success_url = reverse_lazy('inventario:tipos')


class TipoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = TipoOperacion, TipoOperacionForm, 'Editar tipo de operación'
    success_url = reverse_lazy('inventario:tipos')
