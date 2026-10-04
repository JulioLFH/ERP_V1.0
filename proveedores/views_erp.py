"""Compras > Portal de proveedores (lado del ERP): revisión de facturas, accesos y envío de órdenes."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from compras.models import OrdenCompra
from core import correo, sunat_consulta

from . import servicios
from .forms import AccesoForm
from .models import AccesoProveedor, FacturaProveedor


@login_required
def facturas(request):
    qs = FacturaProveedor.objects.select_related('tercero', 'orden_compra', 'compra')
    estado = request.GET.get('estado', 'ENVIADA')
    if estado:
        qs = qs.filter(estado=estado)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(serie__icontains=q) | Q(numero__icontains=q) | Q(tercero__nombre__icontains=q) |
                       Q(orden_compra__numero__icontains=q))
    return render(request, 'proveedores/erp_facturas.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'estado': estado, 'q': q,
        'estados': FacturaProveedor.ESTADOS})


@login_required
def factura_detalle(request, pk):
    f = get_object_or_404(FacturaProveedor.objects.select_related('tercero', 'orden_compra', 'compra'), pk=pk)
    items = list(f.items.all())
    for i in items:
        i.dif_cant = i.cantidad - i.cantidad_esperada
        i.dif_precio = i.precio_unitario - i.precio_orden
    return render(request, 'proveedores/erp_factura.html', {
        'f': f, 'items': items, 'sunat_configurada': sunat_consulta.configurada()})


@login_required
def factura_archivo(request, pk, tipo):
    from .views import _descargar
    return _descargar(get_object_or_404(FacturaProveedor, pk=pk), tipo)


@login_required
def factura_sunat(request, pk):
    f = get_object_or_404(FacturaProveedor, pk=pk)
    if request.method == 'POST':
        r = servicios.validar_sunat(f)
        if r is None:
            messages.error(request, 'Registre las credenciales de la API SUNAT en Ajustes > Empresa.')
        else:
            (messages.success if f.estado_sunat == 'VALIDO' else messages.warning)(request, f'SUNAT: {f.sunat_detalle}')
    return redirect('compras:portal_factura', pk)


@login_required
def factura_aprobar(request, pk):
    f = get_object_or_404(FacturaProveedor, pk=pk)
    if request.method == 'POST':
        if f.estado_sunat == 'OBSERVADO' and not request.POST.get('forzar'):
            messages.error(request, 'SUNAT observó el comprobante: no se puede registrar.')
            return redirect('compras:portal_factura', pk)
        try:
            c = servicios.registrar_compra(f, request.user)
            messages.success(request, f'Compra {c} registrada. Vence el {c.fecha_vencimiento:%d/%m/%Y}.')
            return redirect('compras:detalle', c.pk)
        except servicios.ErrorPortal as exc:
            messages.error(request, ' '.join(exc.args[0]))
    return redirect('compras:portal_factura', pk)


@login_required
def factura_rechazar(request, pk):
    f = get_object_or_404(FacturaProveedor, pk=pk)
    if request.method == 'POST':
        try:
            servicios.rechazar(f, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{f} rechazada; el proveedor verá el motivo en el portal.')
        except servicios.ErrorPortal as exc:
            messages.error(request, ' '.join(exc.args[0]))
    return redirect('compras:portal_factura', pk)


# ---------------------------------------------------------------- accesos
@login_required
def accesos(request):
    return render(request, 'proveedores/erp_accesos.html', {
        'accesos': AccesoProveedor.objects.select_related('usuario', 'tercero').order_by('tercero__nombre')})


def _acceso_form(request, acceso, titulo):
    form = AccesoForm(request.POST or None, acceso=acceso,
                      initial={'tercero': request.GET.get('tercero')} if not acceso else None)
    if request.method == 'POST' and form.is_valid():
        a = form.save()
        messages.success(request, f'Acceso al portal de {a.tercero.nombre} guardado (usuario {a.usuario.username}).')
        return redirect('compras:portal_accesos')
    return render(request, 'core/form.html', {'form': form, 'titulo': titulo})


@login_required
def acceso_nuevo(request):
    return _acceso_form(request, None, 'Nuevo acceso al portal de proveedores')


@login_required
def acceso_editar(request, pk):
    return _acceso_form(request, get_object_or_404(AccesoProveedor, pk=pk), 'Editar acceso al portal')


# ---------------------------------------------------------------- órdenes de compra
@login_required
def oc_enviar(request, pk):
    oc = get_object_or_404(OrdenCompra, pk=pk)
    if request.method == 'POST':
        if oc.estado == 'ANULADO':
            messages.error(request, 'La orden está anulada.')
        else:
            try:
                destinos = servicios.enviar_orden(oc, request)
                messages.success(request, f'Orden enviada a {", ".join(destinos)} con el enlace de aceptación.')
            except correo.ErrorCorreo as exc:
                messages.error(request, f'{exc} Puede copiar el enlace de aceptación y enviarlo por otro medio.')
    return redirect('compras:oc_detalle', pk)
