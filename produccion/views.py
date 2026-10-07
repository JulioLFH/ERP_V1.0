from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView

from core.modulos import puede_ver_costos
from core.utils import excel_response, rango_por_defecto
from core.views import FormGenerico, ListaGenerica

from . import servicios
from .forms import (CentroTrabajoForm, ComponentesFormSet, HojaRutaForm, ListaMaterialesForm,
                    OperacionesRutaFormSet, OrdenProduccionForm, PlanDemandaForm, VersionForm)
from .models import (CambioIngenieria, CentroTrabajo, CorridaMRP, HojaRuta, ListaMateriales, OrdenProduccion,
                     PlanDemanda, PropuestaMRP, VersionFabricacion)


def _decimal(valor, defecto=None):
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return defecto


# ---------------------------------------------------------------- órdenes de producción
@login_required
def ordenes(request):
    qs = OrdenProduccion.objects.select_related('producto', 'almacen_destino', 'version')
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
        messages.success(request, f'Orden guardada en borrador con la versión {orden.version.codigo}: insumos y '
                                  'horas de su receta y hoja de ruta.')
        return redirect('manufactura:orden', orden.pk)
    if not orden.pk and request.GET.get('producto'):
        form.initial['producto'] = request.GET['producto']
    versiones = {v.pk: v.producto_id for v in VersionFabricacion.objects.filter(activa=True)}
    return render(request, 'produccion/orden_form.html', {'form': form, 'titulo': titulo, 'orden': orden,
                                                         'versiones': versiones})


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
        'producto', 'lista', 'version__hoja', 'almacen_insumos', 'almacen_destino', 'centro_costo', 'creado_por',
        'anulado_por', 'operacion', 'estandar'), pk=pk)
    filas = servicios.disponibilidad(orden)
    from .avanzado import resumen_avance
    avance = resumen_avance(orden)
    ctx = {'o': orden, 'filas': filas, 'horas': orden.horas.select_related('centro'),
           'faltan': any(f['faltante'] > 0 for f in filas) and orden.estado != 'TERMINADA',
           'hoy': timezone.localdate(), 'avance': avance,
           'a_producir': avance['producido'] if avance and avance['producido'] else orden.cantidad}
    if orden.estado == 'TERMINADA' and not orden.es_historica and puede_ver_costos(request.user):
        ctx['variaciones'] = servicios.variaciones(orden)
        ctx['por_tipo'] = orden.variaciones.all()
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
                   'Orden {o.numero} confirmada con su costo estándar fijado.')


@login_required
def orden_maquila(request, pk):
    return _accion(request, pk, lambda o: servicios.enviar_a_maquilador(o, request.user),
                   'Materiales de {o.numero} enviados al maquilador: se consumen de su almacén al terminar la orden.')


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
        from inventario.cierre import KardexCerrado
        try:
            servicios.terminar(orden, request.user, cantidad, consumos, horas, fecha)
            messages.success(request, f'Orden {orden.numero} terminada: ingresaron {orden.cantidad_producida:,.2f} '
                                      f'{orden.producto.unidad} de {orden.producto.nombre} al almacén.')
        except (servicios.ErrorProduccion, KardexCerrado) as exc:
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
    qs = ListaMateriales.objects.select_related('producto').prefetch_related('componentes', 'versiones')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q) | Q(codigo__icontains=q))
    if not request.GET.get('todas'):
        qs = qs.exclude(estado='OBSOLETA')
    return render(request, 'produccion/listas.html', {'listas': qs, 'q': q})


def _en_uso(lista):
    return lista.pk and lista.ordenes.exclude(estado__in=['BORRADOR', 'ANULADA']).exists()


def _guardar_lista(request, lista, titulo):
    form = ListaMaterialesForm(request.POST or None, instance=lista)
    componentes = ComponentesFormSet(request.POST or None, instance=lista, prefix='comp')
    if request.method == 'POST' and form.is_valid() and componentes.is_valid():
        producto = form.cleaned_data['producto']
        ciclo = [f for f in componentes.forms
                 if f.cleaned_data and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('producto') == producto]
        if ciclo:
            form.add_error(None, f'{producto.nombre} no puede ser insumo de su propia receta.')
        else:
            with transaction.atomic():
                lista = form.save()
                componentes.instance = lista
                componentes.save()
                if not lista.versiones.exists():  # primera receta del producto: su versión de fabricación
                    VersionFabricacion.objects.get_or_create(
                        producto=lista.producto, codigo=lista.codigo[:10],
                        defaults={'lista': lista, 'vigente_desde': lista.vigente_desde})
            messages.success(request, f'Lista de materiales {lista} guardada.')
            return redirect('manufactura:lista', lista.pk)
    from core.models import Producto
    return render(request, 'produccion/lista_form.html', {
        'form': form, 'componentes': componentes, 'titulo': titulo, 'lista': lista,
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
    """Copia la receta como nuevo borrador (la anterior sigue vigente hasta aprobar la nueva)."""
    lista = get_object_or_404(ListaMateriales, pk=pk)
    if request.method == 'POST':
        from .avanzado import copiar_lista
        with transaction.atomic():
            nueva = copiar_lista(lista, f'Copia de {lista.codigo}')
        messages.success(request, f'Receta {nueva.codigo} creada en borrador: ajústela, apruébela y úsela en una '
                                  'versión de fabricación.')
        return redirect('manufactura:lista_editar', nueva.pk)
    return redirect('manufactura:lista', pk)


@login_required
def lista_detalle(request, pk):
    lista = get_object_or_404(ListaMateriales.objects.select_related('producto'), pk=pk)
    ctx = {'lista': lista, 'componentes': lista.componentes.select_related('producto', 'almacen'),
           'en_uso': _en_uso(lista), 'versiones_fab': lista.versiones.select_related('hoja'),
           'otras': ListaMateriales.objects.filter(producto=lista.producto).exclude(pk=pk),
           'ordenes': lista.ordenes.select_related('producto')[:20],
           'cambios': CambioIngenieria.objects.filter(Q(lista_actual=lista) | Q(lista_nueva=lista),
                                                      estado__in=['BORRADOR', 'POR_APROBAR'])}
    if puede_ver_costos(request.user):
        ctx['hoja'] = servicios.hoja_costos(lista)
    return render(request, 'produccion/lista_detalle.html', ctx)


# ---------------------------------------------------------------- hojas de ruta
@login_required
def hojas(request):
    qs = HojaRuta.objects.prefetch_related('operaciones__centro', 'versiones__producto')
    if not request.GET.get('todas'):
        qs = qs.exclude(estado='OBSOLETA')
    return render(request, 'produccion/hojas.html', {'hojas': qs})


def _guardar_hoja(request, hoja, titulo):
    form = HojaRutaForm(request.POST or None, instance=hoja)
    operaciones = OperacionesRutaFormSet(request.POST or None, instance=hoja, prefix='oper')
    if request.method == 'POST' and form.is_valid() and operaciones.is_valid():
        with transaction.atomic():
            hoja = form.save()
            operaciones.instance = hoja
            operaciones.save()
        messages.success(request, f'Hoja de ruta {hoja.codigo} guardada.')
        return redirect('manufactura:hojas')
    if not hoja.pk and not operaciones.initial_form_count():
        operaciones.forms[0].initial.setdefault('secuencia', 10)
    return render(request, 'produccion/hoja_form.html', {'form': form, 'operaciones': operaciones,
                                                        'titulo': titulo, 'hoja': hoja})


@login_required
def hoja_nueva(request):
    return _guardar_hoja(request, HojaRuta(), 'Nueva hoja de ruta')


def _hoja_en_uso(hoja):
    return OrdenProduccion.objects.filter(version__hoja=hoja).exclude(estado__in=['BORRADOR', 'ANULADA']).exists()


@login_required
def hoja_editar(request, pk):
    hoja = get_object_or_404(HojaRuta, pk=pk)
    if _hoja_en_uso(hoja):  # sustento del costeo: para cambiarla se crea otra hoja y otra versión
        messages.error(request, 'La hoja de ruta ya se usó en órdenes de producción: no se modifica. Cree otra hoja '
                                'y úsela en una nueva versión de fabricación (esta puede declararse obsoleta).')
        return redirect('manufactura:hojas')
    return _guardar_hoja(request, hoja, f'Editar {hoja}')


@login_required
def obsoleta(request, tipo, pk):
    """Declara obsoleta una hoja de ruta o una receta (deja de usarse en versiones nuevas; se conserva)."""
    modelo = {'hoja': HojaRuta, 'lista': ListaMateriales}[tipo]
    obj = get_object_or_404(modelo, pk=pk)
    if request.method == 'POST':
        obj.estado = 'OBSOLETA'
        obj.save()
        VersionFabricacion.objects.filter(**{tipo: obj}).update(activa=False)
        messages.success(request, f'{obj} declarada obsoleta; sus versiones de fabricación quedaron inactivas.')
    return redirect('manufactura:hojas' if tipo == 'hoja' else 'manufactura:listas')


# ---------------------------------------------------------------- versiones de fabricación
class VersionLista(ListaGenerica):
    model = VersionFabricacion
    titulo = 'Versiones de fabricación'
    columnas = [('Producto', 'producto.nombre'), ('Versión', 'codigo'), ('Receta', 'lista.codigo'),
                ('Hoja de ruta', 'hoja'), ('Lote desde', 'lote_min'), ('Lote hasta', 'lote_max'),
                ('Vigente desde', 'vigente_desde'), ('Hasta', 'vigente_hasta'), ('Activa', 'activa')]
    url_nuevo, url_editar = 'manufactura:version_nueva', 'manufactura:version_editar'
    buscar_en = ['producto__nombre', 'codigo']

    def get_queryset(self):
        return super().get_queryset().select_related('producto', 'lista', 'hoja')


class VersionNueva(FormGenerico, CreateView):
    model, form_class, titulo = VersionFabricacion, VersionForm, 'Nueva versión de fabricación'
    success_url = reverse_lazy('manufactura:versiones')


class VersionEditar(FormGenerico, UpdateView):
    model, form_class, titulo = VersionFabricacion, VersionForm, 'Editar versión de fabricación'
    success_url = reverse_lazy('manufactura:versiones')


# ---------------------------------------------------------------- puestos de trabajo
class CentroLista(ListaGenerica):
    model = CentroTrabajo
    titulo = 'Puestos de trabajo'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Tipo', 'get_tipo_display'),
                ('Mano de obra S/ h', 'costo_hora_mo'), ('Máquina y CIF S/ h', 'costo_hora_cif'),
                ('Capacidad h/día', 'capacidad_dia'), ('Eficiencia %', 'eficiencia'),
                ('Centro de costo', 'centro_costo'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'manufactura:centro_nuevo', 'manufactura:centro_editar'
    buscar_en = ['codigo', 'nombre']


class CentroNuevo(FormGenerico, CreateView):
    model, form_class, titulo = CentroTrabajo, CentroTrabajoForm, 'Nuevo puesto de trabajo'
    success_url = reverse_lazy('manufactura:centros')


class CentroEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CentroTrabajo, CentroTrabajoForm, 'Editar puesto de trabajo'
    success_url = reverse_lazy('manufactura:centros')


@login_required
def capacidad(request):
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde')) or hoy
    hasta = _fecha(request.GET.get('hasta')) or desde + timedelta(days=13)
    return render(request, 'produccion/capacidad.html', {'filas': servicios.carga_capacidad(desde, hasta),
                                                         'desde': desde, 'hasta': hasta})


def _fecha(texto):
    from datetime import date
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


# ---------------------------------------------------------------- planificación (MRP)
@login_required
def mrp(request):
    hoy = timezone.localdate()
    form = PlanDemandaForm(request.POST or None, initial={'fecha': hoy + timedelta(days=7)})
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'demanda' and form.is_valid():
            plan = form.save(commit=False)
            plan.creado_por = request.user
            plan.save()
            messages.success(request, 'Demanda agregada al plan.')
            return redirect('manufactura:mrp')
        if accion == 'quitar':
            PlanDemanda.objects.filter(pk=request.POST.get('id')).delete()
            return redirect('manufactura:mrp')
        if accion == 'ejecutar':
            horizonte = _fecha(request.POST.get('horizonte')) or hoy + timedelta(days=60)
            corrida = servicios.ejecutar_mrp(horizonte, request.user)
            messages.success(request, f'MRP ejecutado: {corrida.propuestas.count()} órdenes planificadas.')
            return redirect('manufactura:mrp')
        if accion in ('fabricar', 'comprar'):
            ids = request.POST.getlist('propuesta')
            propuestas = list(PropuestaMRP.objects.filter(pk__in=ids).select_related('producto', 'version'))
            try:
                if accion == 'fabricar':
                    hechas = [servicios.convertir_en_orden(p, request.user) for p in propuestas
                              if p.tipo == 'PRODUCIR' and not p.convertida]
                    messages.success(request, f'{len(hechas)} orden(es) de producción creadas en borrador.')
                else:
                    from contabilidad.models import CentroCosto
                    centro = CentroCosto.objects.filter(pk=request.POST.get('centro_costo') or 0).first()
                    hechas = servicios.convertir_en_compras(propuestas, request.user, centro)
                    messages.success(request, f'{len(hechas)} orden(es) de compra creadas (pendientes de aprobar): '
                                              + ', '.join(o.numero for o in hechas))
            except servicios.ErrorProduccion as exc:
                messages.error(request, str(exc))
            return redirect('manufactura:mrp')
    corrida = CorridaMRP.objects.select_related('usuario').first()
    propuestas = (corrida.propuestas.select_related('producto', 'version', 'proveedor', 'orden_produccion',
                                                    'orden_compra') if corrida else [])
    from contabilidad.models import CentroCosto
    return render(request, 'produccion/mrp.html', {
        'form': form, 'plan': PlanDemanda.objects.select_related('producto').filter(fecha__gte=hoy - timedelta(30)),
        'corrida': corrida, 'propuestas': propuestas, 'horizonte': hoy + timedelta(days=60),
        'centros': CentroCosto.objects.filter(activo=True)})
