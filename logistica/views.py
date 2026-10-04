from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView

from core import sunat
from core.models import Almacen, Empresa, FacturacionConfig
from core.utils import faltantes_stock, guardar_documento, lineas_formset
from core.views import FormGenerico, ListaGenerica
from ventas.models import Venta

from .forms import ConductorForm, GuiaRemitenteForm, GuiaTransportistaForm, VehiculoForm, guia_formset
from .models import Conductor, GuiaRemision, Vehiculo


def _form_class(tipo):
    return GuiaTransportistaForm if tipo == '31' else GuiaRemitenteForm


def _ctx(titulo, tipo, doc=None):
    from core.models import Producto
    productos = Producto.objects.filter(activo=True).only('unidad', 'peso')
    return {'titulo': titulo, 'tipo': tipo, 'doc': doc,
            'productos_unidad': {p.pk: p.unidad for p in productos},
            'productos_peso': {p.pk: str(p.peso) for p in productos if p.peso}}


def _al_guardar(guia):
    guia.aplicar_stock()
    cfg = FacturacionConfig.actual()
    if cfg.activa and cfg.envio_automatico:
        def enviar():
            try:
                sunat.enviar_guia(GuiaRemision.objects.get(pk=guia.pk))
            except sunat.ErrorFacturacion:
                pass  # queda en estado ERROR; se puede reenviar desde la guía
        transaction.on_commit(enviar)


def _initial_desde_venta(venta):
    empresa = Empresa.actual()
    almacen = venta.almacen or Almacen.principal()
    initial = {
        'venta': venta.pk, 'destinatario': venta.tercero_id, 'motivo_traslado': '01',
        'partida_ubigeo': almacen.ubigeo or empresa.ubigeo,
        'partida_direccion': almacen.direccion or empresa.direccion,
        'llegada_ubigeo': venta.tercero.ubigeo, 'llegada_direccion': venta.tercero.direccion,
        'almacen_origen': almacen.pk,
        # si la venta ya descontó stock la guía no vuelve a moverlo
        'efecto_stock': 'NINGUNO' if venta.stock_aplicado else 'SALIDA',
    }
    items = [{'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': i.cantidad,
              'unidad': i.producto.unidad if i.producto else 'NIU'}
             for i in venta.items.select_related('producto') if not i.producto or i.producto.es_inventariable]
    return initial, items


@login_required
def lista(request):
    qs = GuiaRemision.objects.select_related('destinatario', 'remitente', 'venta')
    tipo = request.GET.get('tipo', '')
    if tipo:
        qs = qs.filter(tipo=tipo)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(destinatario__nombre__icontains=q) |
                       Q(remitente__nombre__icontains=q) | Q(llegada_direccion__icontains=q))
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    return render(request, 'logistica/lista.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q, 'tipo': tipo})


def _validar_despacho(form, formset):
    """Una guía de una venta no puede despachar más de lo facturado (sumando las otras guías emitidas)."""
    guia = form.instance
    venta = form.cleaned_data.get('venta')
    if guia.tipo != '09' or not venta:
        return []
    from collections import defaultdict
    from decimal import Decimal
    vendido, nombres = defaultdict(Decimal), {}
    for i in venta.items.select_related('producto'):
        if i.producto_id:
            vendido[i.producto_id] += i.cantidad
            nombres[i.producto_id] = i.producto.nombre
    for nota in venta.notas.filter(estado='REGISTRADO', tipo_comprobante='07'):  # devoluciones
        for i in nota.items.filter(producto__isnull=False):
            vendido[i.producto_id] -= i.cantidad
    despachado = defaultdict(Decimal)
    for otra in venta.guias.filter(estado='EMITIDA').exclude(pk=guia.pk):
        for i in otra.items.filter(producto__isnull=False):
            despachado[i.producto_id] += i.cantidad
    errores = []
    for producto, cantidad in lineas_formset(formset):
        if not producto:
            continue
        if producto.pk not in vendido:
            errores.append(f'"{producto.nombre}" no figura en la {venta}.')
            continue
        despachado[producto.pk] += cantidad
    for pid, cant in despachado.items():
        if pid in vendido and cant > vendido[pid]:
            errores.append(f'"{nombres[pid]}": se despacharían {cant:,.2f} y la {venta} solo factura '
                           f'{vendido[pid]:,.2f} (sumando las guías emitidas).')
    return errores


def _validar_stock(form, formset):
    """Las guías que sacan mercadería (salida o traslado) no pueden dejar el almacén de origen en negativo."""
    errores = _validar_despacho(form, formset)
    guia = form.instance
    if guia.tipo != '09' or guia.efecto_stock not in ('SALIDA', 'TRASLADO'):
        return errores
    devolver = {}
    if guia.pk:
        anterior = GuiaRemision.objects.get(pk=guia.pk)
        if anterior.stock_aplicado and anterior.almacen_origen_id == guia.almacen_origen_id \
                and anterior.efecto_stock in ('SALIDA', 'TRASLADO'):
            for i in anterior.items.all():
                devolver[i.producto_id] = devolver.get(i.producto_id, 0) + i.cantidad
    return errores + faltantes_stock(lineas_formset(formset), guia.almacen_origen, devolver)


def _faltantes_al_anular(guia):
    """Anular una entrada o un traslado saca mercadería del almacén de destino."""
    from inventario.cierre import error_cierre
    if guia.stock_aplicado and error_cierre(guia.fecha_traslado):
        return [error_cierre(guia.fecha_traslado)]
    if not guia.stock_aplicado or guia.efecto_stock not in ('ENTRADA', 'TRASLADO'):
        return []
    return faltantes_stock([(i.producto, i.cantidad) for i in guia.items.select_related('producto')],
                           guia.almacen_destino)


@login_required
def nueva(request):
    tipo = request.GET.get('tipo', '09')
    initial, items = {}, None
    empresa = Empresa.actual()
    if tipo == '09':
        principal = Almacen.principal()
        initial = {'partida_ubigeo': principal.ubigeo or empresa.ubigeo,
                   'partida_direccion': principal.direccion or empresa.direccion,
                   'almacen_origen': principal.pk, 'efecto_stock': 'SALIDA'}
    if request.GET.get('venta'):
        initial, items = _initial_desde_venta(get_object_or_404(Venta, pk=request.GET['venta']))
    titulo = 'Nueva guía de remisión ' + ('transportista' if tipo == '31' else 'remitente')
    return guardar_documento(request, _form_class(tipo), guia_formset(), GuiaRemision(tipo=tipo),
                             'logistica/guia_form.html', _ctx(titulo, tipo), al_guardar=_al_guardar,
                             initial=initial, items_iniciales=items, validar=_validar_stock)


@login_required
def editar(request, pk):
    guia = get_object_or_404(GuiaRemision, pk=pk)
    if guia.estado == 'ANULADA' or guia.estado_sunat in ('ACEPTADO', 'PENDIENTE'):
        messages.error(request, 'No se puede editar una guía anulada o ya enviada a SUNAT.')
        return redirect('logistica:detalle', pk)
    return guardar_documento(request, _form_class(guia.tipo), guia_formset(extra=0), guia, 'logistica/guia_form.html',
                             _ctx(f'Editar {guia}', guia.tipo, guia), al_guardar=_al_guardar, validar=_validar_stock)


@login_required
def detalle(request, pk):
    guia = get_object_or_404(GuiaRemision.objects.select_related(
        'destinatario', 'remitente', 'transportista', 'vehiculo', 'conductor', 'venta'), pk=pk)
    return render(request, 'logistica/detalle.html', {'g': guia, 'items': guia.items.select_related('producto'),
                                                      'cfg': FacturacionConfig.actual()})


@login_required
def imprimir(request, pk):
    guia = get_object_or_404(GuiaRemision, pk=pk)
    return render(request, 'logistica/imprimir.html', {'g': guia, 'items': guia.items.all()})


@login_required
def anular(request, pk):
    guia = get_object_or_404(GuiaRemision, pk=pk)
    if request.method == 'POST' and guia.estado != 'ANULADA':
        motivo = request.POST.get('motivo', '').strip()
        faltan = _faltantes_al_anular(guia)
        if len(motivo) < 5:
            messages.error(request, 'Indique el motivo de la anulación (mínimo 5 caracteres).')
        elif faltan:
            messages.error(request, 'No se puede anular: la mercadería ya salió del almacén de destino. '
                           + ' '.join(faltan))
        else:
            with transaction.atomic():
                guia.revertir_stock()
                guia.estado = 'ANULADA'
                guia.motivo_anulacion = motivo[:250]
                guia.anulado_por = request.user
                guia.anulado_en = timezone.now()
                guia.save(update_fields=['estado', 'motivo_anulacion', 'anulado_por', 'anulado_en'])
            aviso = (' Recuerde darla de baja también en SUNAT con su Clave SOL (las guías no se anulan por el OSE).'
                     if guia.estado_sunat == 'ACEPTADO' else '')
            messages.success(request, f'{guia} anulada.{aviso}')
    return redirect('logistica:detalle', pk)


@login_required
def enviar_sunat(request, pk):
    guia = get_object_or_404(GuiaRemision, pk=pk)
    if request.method == 'POST':
        try:
            if guia.estado_sunat in ('ACEPTADO', 'PENDIENTE') or request.POST.get('accion') == 'consultar':
                sunat.consultar_guia(guia)
            else:
                sunat.enviar_guia(guia)
            nivel = messages.success if guia.estado_sunat == 'ACEPTADO' else messages.warning
            nivel(request, f'SUNAT: {guia.get_estado_sunat_display()}. {guia.sunat_descripcion}')
        except sunat.ErrorFacturacion as exc:
            messages.error(request, f'Facturación electrónica: {exc}')
    return redirect('logistica:detalle', pk)


# ---------------------------------------------------------------- vehículos y conductores
class VehiculoLista(ListaGenerica):
    model = Vehiculo
    titulo = 'Vehículos'
    columnas = [('Placa', 'placa'), ('Marca', 'marca'), ('Modelo', 'modelo'), ('TUCE / Cert.', 'certificado'),
                ('Activo', 'activo')]
    url_nuevo, url_editar = 'logistica:vehiculo_nuevo', 'logistica:vehiculo_editar'
    buscar_en = ['placa', 'marca']


class VehiculoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Vehiculo, VehiculoForm, 'Nuevo vehículo'
    success_url = reverse_lazy('logistica:vehiculos')


class VehiculoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Vehiculo, VehiculoForm, 'Editar vehículo'
    success_url = reverse_lazy('logistica:vehiculos')


class ConductorLista(ListaGenerica):
    model = Conductor
    titulo = 'Conductores'
    columnas = [('Doc.', 'get_tipo_doc_display'), ('Número', 'numero_doc'), ('Apellidos', 'apellidos'),
                ('Nombres', 'nombres'), ('Licencia', 'licencia'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'logistica:conductor_nuevo', 'logistica:conductor_editar'
    buscar_en = ['numero_doc', 'apellidos', 'nombres', 'licencia']


class ConductorNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Conductor, ConductorForm, 'Nuevo conductor'
    success_url = reverse_lazy('logistica:conductores')


class ConductorEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Conductor, ConductorForm, 'Editar conductor'
    success_url = reverse_lazy('logistica:conductores')
