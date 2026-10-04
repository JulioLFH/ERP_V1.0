from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from core.modulos import puede_ver_costos
from core.utils import excel_response, rango_por_defecto
from core.views import FormGenerico, ListaGenerica

from . import servicios
from .forms import (CentroTrabajoForm, ComponentesFormSet, ListaMaterialesForm, OperacionesFormSet,
                    OrdenProduccionForm)
from .models import CentroTrabajo, ListaMateriales, OrdenProduccion


def _decimal(valor, defecto=None):
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return defecto


# ---------------------------------------------------------------- órdenes de producción
@login_required
def ordenes(request):
    qs = OrdenProduccion.objects.select_related('producto', 'almacen_destino')
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q))
    desde, hasta = rango_por_defecto(request, qs)
    if not request.GET.get('todo'):
        qs = qs.filter(Q(fecha__range=[desde, hasta]) | Q(estado__in=['BORRADOR', 'CONFIRMADA', 'EN_PROCESO']))
    pendientes = OrdenProduccion.objects.filter(estado__in=['CONFIRMADA', 'EN_PROCESO']).count()
    return render(request, 'produccion/ordenes.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q, 'desde': desde, 'hasta': hasta,
        'estados': OrdenProduccion.ESTADOS, 'pendientes': pendientes})


def _guardar_orden(request, orden, titulo):
    form = OrdenProduccionForm(request.POST or None, instance=orden)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            nuevo = not orden.pk
            orden = form.save(commit=False)
            if nuevo:
                orden.creado_por = request.user
            orden.save()
            servicios.explotar(orden)
        messages.success(request, 'Orden guardada en borrador con los insumos y horas de la receta.')
        return redirect('manufactura:orden', orden.pk)
    if not orden.pk and request.GET.get('producto'):
        receta = ListaMateriales.objects.filter(producto_id=request.GET['producto'], activa=True).first()
        if receta:
            form.initial.update(producto=receta.producto_id, lista=receta.pk, cantidad=receta.cantidad_base)
    recetas = {lm.pk: lm.producto_id for lm in ListaMateriales.objects.filter(activa=True)}
    return render(request, 'produccion/orden_form.html', {'form': form, 'titulo': titulo, 'orden': orden,
                                                         'recetas': recetas})


@login_required
def orden_nueva(request):
    return _guardar_orden(request, OrdenProduccion(), 'Nueva orden de producción')


@login_required
def orden_editar(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if orden.estado != 'BORRADOR':
        messages.error(request, 'Solo se editan órdenes en borrador.')
        return redirect('manufactura:orden', pk)
    return _guardar_orden(request, orden, f'Editar {orden}')


@login_required
def orden_detalle(request, pk):
    orden = get_object_or_404(OrdenProduccion.objects.select_related(
        'producto', 'lista', 'almacen_insumos', 'almacen_destino', 'centro_costo', 'creado_por', 'anulado_por',
        'operacion'), pk=pk)
    filas = servicios.disponibilidad(orden)
    ctx = {'o': orden, 'filas': filas, 'horas': orden.horas.select_related('centro'),
           'faltan': any(f['faltante'] > 0 for f in filas) and orden.estado != 'TERMINADA'}
    if orden.estado == 'TERMINADA' and puede_ver_costos(request.user):
        ctx['variaciones'] = servicios.variaciones(orden)
    return render(request, 'produccion/orden_detalle.html', ctx)


def _accion(request, pk, funcion, exito):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            funcion(orden)
            messages.success(request, exito.format(o=orden))
        except servicios.ErrorProduccion as exc:
            messages.error(request, str(exc))
    return redirect('manufactura:orden', pk)


@login_required
def orden_confirmar(request, pk):
    return _accion(request, pk, lambda o: servicios.confirmar(o, request.user),
                   'Orden {o.numero} confirmada: los insumos quedan reservados en el requerimiento de materiales.')


@login_required
def orden_iniciar(request, pk):
    return _accion(request, pk, servicios.iniciar, 'Orden {o.numero} en proceso.')


@login_required
def orden_terminar(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        consumos = {c.pk: _decimal(request.POST.get(f'consumo_{c.pk}'), c.cantidad_real) for c in orden.consumos.all()}
        horas = {h.pk: _decimal(request.POST.get(f'hora_{h.pk}'), h.horas_real) for h in orden.horas.all()}
        cantidad = _decimal(request.POST.get('cantidad_producida'))
        fecha = request.POST.get('fecha') or None
        try:
            servicios.terminar(orden, request.user, cantidad, consumos, horas, fecha)
            messages.success(request, f'Orden {orden.numero} terminada: ingresaron {orden.cantidad_producida:,.2f} '
                                      f'{orden.producto.unidad} de {orden.producto.nombre} al almacén.')
        except servicios.ErrorProduccion as exc:
            messages.error(request, f'No se pudo terminar: {exc}')
        except Exception as exc:  # kardex cerrado u otra validación del almacén
            from inventario.cierre import KardexCerrado
            if not isinstance(exc, KardexCerrado):
                raise
            messages.error(request, f'No se pudo terminar: {exc}')
    return redirect('manufactura:orden', pk)


@login_required
def orden_anular(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            servicios.anular(orden, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{orden} anulada.' + (' Se revirtió el movimiento de almacén.'
                                                            if orden.operacion_id else ''))
        except servicios.ErrorProduccion as exc:
            messages.error(request, f'No se pudo anular: {exc}')
    return redirect('manufactura:orden', pk)


@login_required
def orden_eliminar(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        if orden.estado != 'BORRADOR':
            messages.error(request, 'Solo se eliminan borradores; las órdenes confirmadas se anulan con motivo.')
            return redirect('manufactura:orden', pk)
        orden.delete()
        messages.success(request, 'Borrador eliminado.')
    return redirect('manufactura:ordenes')


@login_required
def requerimiento(request):
    filas = servicios.requerimientos()
    if request.GET.get('formato') == 'excel':
        datos = [[f['p'].codigo, f['p'].nombre, f['p'].unidad, f['requerido'], f['stock'], f['camino'], f['faltante'],
                  ', '.join(f['ordenes'])] for f in filas]
        return excel_response('Requerimiento_materiales', 'Requerimiento de materiales',
                              ['Código', 'Insumo', 'U.M.', 'Requerido', 'Stock', 'En camino (OC)', 'Por comprar',
                               'Órdenes'], datos)
    return render(request, 'produccion/requerimiento.html', {'filas': filas,
                                                             'faltan': sum(1 for f in filas if f['faltante'] > 0)})


# ---------------------------------------------------------------- listas de materiales
@login_required
def listas(request):
    qs = ListaMateriales.objects.select_related('producto').prefetch_related('componentes', 'operaciones')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q) | Q(codigo__icontains=q))
    if not request.GET.get('todas'):
        qs = qs.filter(activa=True)
    return render(request, 'produccion/listas.html', {'listas': qs, 'q': q})


def _en_uso(lista):
    return lista.pk and lista.ordenes.exclude(estado__in=['BORRADOR', 'ANULADA']).exists()


def _guardar_lista(request, lista, titulo):
    form = ListaMaterialesForm(request.POST or None, instance=lista)
    componentes = ComponentesFormSet(request.POST or None, instance=lista, prefix='comp')
    operaciones = OperacionesFormSet(request.POST or None, instance=lista, prefix='oper')
    if request.method == 'POST' and form.is_valid() and componentes.is_valid() and operaciones.is_valid():
        producto = form.cleaned_data['producto']
        ciclo = [f.cleaned_data['producto'] for f in componentes.forms
                 if f.cleaned_data and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('producto') == producto]
        if ciclo:
            form.add_error(None, f'{producto.nombre} no puede ser insumo de su propia receta.')
        else:
            with transaction.atomic():
                lista = form.save()
                componentes.instance = operaciones.instance = lista
                componentes.save()
                operaciones.save()
                if lista.activa:  # una sola receta vigente por producto
                    ListaMateriales.objects.filter(producto=lista.producto, activa=True).exclude(pk=lista.pk).update(
                        activa=False)
            messages.success(request, f'Lista de materiales {lista} guardada.')
            return redirect('manufactura:lista', lista.pk)
    from core.models import Producto
    return render(request, 'produccion/lista_form.html', {
        'form': form, 'componentes': componentes, 'operaciones': operaciones, 'titulo': titulo, 'lista': lista,
        'unidades': dict(Producto.objects.filter(activo=True, tipo='BIEN').values_list('pk', 'unidad'))})


@login_required
def lista_nueva(request):
    lista = ListaMateriales()
    if request.GET.get('producto'):
        lista.producto_id = request.GET['producto']
    return _guardar_lista(request, lista, 'Nueva lista de materiales')


@login_required
def lista_editar(request, pk):
    lista = get_object_or_404(ListaMateriales, pk=pk)
    if _en_uso(lista):
        messages.error(request, 'La receta ya se usó en órdenes de producción: no se modifica para conservar el '
                                'sustento del costeo. Cree una nueva versión.')
        return redirect('manufactura:lista', pk)
    return _guardar_lista(request, lista, f'Editar {lista}')


@login_required
def lista_version(request, pk):
    """Copia la receta como nueva versión vigente (la anterior queda en el historial)."""
    lista = get_object_or_404(ListaMateriales, pk=pk)
    if request.method == 'POST':
        with transaction.atomic():
            n = ListaMateriales.objects.filter(producto=lista.producto).count() + 1
            codigo = f'V{n}'
            while ListaMateriales.objects.filter(producto=lista.producto, codigo=codigo).exists():
                n += 1
                codigo = f'V{n}'
            nueva = ListaMateriales.objects.create(producto=lista.producto, codigo=codigo,
                                                   cantidad_base=lista.cantidad_base, activa=False,
                                                   observaciones=f'Copia de {lista.codigo}')
            for c in lista.componentes.all():
                nueva.componentes.create(producto_id=c.producto_id, cantidad=c.cantidad, merma=c.merma)
            for o in lista.operaciones.all():
                nueva.operaciones.create(centro_id=o.centro_id, descripcion=o.descripcion, horas=o.horas)
        messages.success(request, f'Versión {codigo} creada (aún no vigente): ajústela y márquela como vigente.')
        return redirect('manufactura:lista_editar', nueva.pk)
    return redirect('manufactura:lista', pk)


@login_required
def lista_detalle(request, pk):
    lista = get_object_or_404(ListaMateriales.objects.select_related('producto'), pk=pk)
    ctx = {'lista': lista, 'componentes': lista.componentes.select_related('producto'),
           'operaciones': lista.operaciones.select_related('centro'), 'en_uso': _en_uso(lista),
           'versiones': ListaMateriales.objects.filter(producto=lista.producto).exclude(pk=pk),
           'ordenes': lista.ordenes.select_related('producto')[:20]}
    if puede_ver_costos(request.user):
        ctx['hoja'] = servicios.hoja_costos(lista)
    return render(request, 'produccion/lista_detalle.html', ctx)


# ---------------------------------------------------------------- centros de trabajo
class CentroLista(ListaGenerica):
    model = CentroTrabajo
    titulo = 'Centros de trabajo'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Mano de obra S/ h', 'costo_hora_mo'),
                ('Indirectos S/ h', 'costo_hora_cif'), ('Total S/ h', 'costo_hora'), ('Centro de costo', 'centro_costo'),
                ('Activo', 'activo')]
    url_nuevo, url_editar = 'manufactura:centro_nuevo', 'manufactura:centro_editar'
    buscar_en = ['codigo', 'nombre']


class CentroNuevo(FormGenerico, CreateView):
    model, form_class, titulo = CentroTrabajo, CentroTrabajoForm, 'Nuevo centro de trabajo'
    success_url = reverse_lazy('manufactura:centros')


class CentroEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CentroTrabajo, CentroTrabajoForm, 'Editar centro de trabajo'
    success_url = reverse_lazy('manufactura:centros')
