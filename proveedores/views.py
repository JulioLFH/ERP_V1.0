"""Portal de proveedores: el proveedor ve sus órdenes de compra, las acepta y registra sus facturas."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from compras.models import OrdenCompra

from . import servicios
from .forms import FacturaCabeceraForm, leer_lineas
from .models import FacturaProveedor


def portal_requerido(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        acceso = getattr(request.user, 'acceso_proveedor', None)
        if acceso is None:
            return redirect('home')  # usuario del ERP
        request.tercero = acceso.tercero
        return vista(request, *args, **kwargs)
    return envoltura


def _ordenes(tercero):
    """Órdenes visibles para el proveedor: enviadas o con mercadería recibida."""
    return (OrdenCompra.objects.filter(tercero=tercero).exclude(estado='ANULADO')
            .filter(Q(estado_proveedor__in=['ENVIADA', 'ACEPTADA', 'RECHAZADA']) | Q(estado__in=['APROBADO', 'ATENDIDO']) |
                    Q(recepciones__estado='CONFIRMADO')).distinct().select_related('centro_costo'))


@portal_requerido
def inicio(request):
    ordenes = list(_ordenes(request.tercero)[:100])
    for oc in ordenes:
        oc.ingresada = servicios.tiene_ingreso(oc)
        oc.por_facturar = sum((l['esperado'] for l in servicios.lineas_por_facturar(oc)), 0) if oc.ingresada else 0
    facturas = FacturaProveedor.objects.filter(tercero=request.tercero).select_related('orden_compra')[:100]
    return render(request, 'proveedores/portal_inicio.html', {
        'ordenes': ordenes, 'facturas': facturas,
        'por_aceptar': [o for o in ordenes if o.estado_proveedor == 'ENVIADA']})


@portal_requerido
def oc_detalle(request, pk):
    oc = get_object_or_404(_ordenes(request.tercero), pk=pk)
    lineas = servicios.lineas_por_facturar(oc)
    recibido = servicios.recibido_por_producto(oc)
    ingresada = servicios.tiene_ingreso(oc)
    return render(request, 'proveedores/portal_oc.html', {
        'oc': oc, 'lineas': lineas, 'recibido': recibido, 'ingresada': ingresada,
        'puede_facturar': ingresada and any(l['esperado'] > 0 for l in lineas) and oc.estado_proveedor != 'RECHAZADA',
        'facturas': oc.facturas_portal.all(),
        'recepciones': [r for r in _recepciones(oc)]})


def _recepciones(oc):
    from inventario.models import Operacion
    return Operacion.objects.filter(Q(orden_compra=oc) | Q(compra__orden_compra=oc), estado='CONFIRMADO',
                                    tipo__clase='INGRESO').prefetch_related('items__producto').distinct()


def _responder(request, oc, nombre):
    accion = request.POST.get('accion')
    if oc.estado_proveedor not in ('ENVIADA', 'SIN_ENVIAR'):
        messages.info(request, f'La orden ya fue {oc.get_estado_proveedor_display().lower()}.')
        return
    if accion not in ('aceptar', 'rechazar'):
        return
    comentario = request.POST.get('comentario', '').strip()
    if accion == 'rechazar' and len(comentario) < 5:
        messages.error(request, 'Indique el motivo del rechazo.')
        return
    oc.estado_proveedor = 'ACEPTADA' if accion == 'aceptar' else 'RECHAZADA'
    oc.respondida_en, oc.respuesta_comentario = timezone.now(), comentario[:300]
    oc.respondida_por = nombre[:120]
    oc.save(update_fields=['estado_proveedor', 'respondida_en', 'respuesta_comentario', 'respondida_por'])
    messages.success(request, f'Orden {oc.numero} {oc.get_estado_proveedor_display().lower()}. ¡Gracias!')


@portal_requerido
def oc_responder(request, pk):
    oc = get_object_or_404(_ordenes(request.tercero), pk=pk)
    if request.method == 'POST':
        _responder(request, oc, request.user.get_full_name() or request.user.username)
    return redirect('portal:oc_detalle', pk)


def oc_aceptacion(request, token):
    """Enlace del correo: el proveedor acepta o rechaza la orden sin iniciar sesión."""
    oc = OrdenCompra.desde_token(token)
    if oc is None or oc.estado == 'ANULADO':
        return render(request, 'proveedores/aceptacion.html', {'invalido': True}, status=404)
    if request.method == 'POST':
        _responder(request, oc, request.POST.get('nombre', '').strip() or f'{oc.tercero.nombre} (enlace del correo)')
        return redirect('portal:oc_aceptacion', token)
    return render(request, 'proveedores/aceptacion.html', {'oc': oc, 'items': oc.items.all()})


@portal_requerido
def factura_nueva(request, pk):
    oc = get_object_or_404(_ordenes(request.tercero), pk=pk)
    if oc.estado_proveedor == 'RECHAZADA':
        messages.error(request, 'La orden fue rechazada: no se puede facturar.')
        return redirect('portal:oc_detalle', pk)
    if not servicios.tiene_ingreso(oc):
        messages.warning(request, f'La mercadería de la orden {oc.numero} aún no ingresa a nuestro almacén. '
                                  'Podrá cargar la factura cuando se registre la recepción (recibirá un correo de '
                                  'conformidad).')
        return redirect('portal:oc_detalle', pk)
    lineas = [l for l in servicios.lineas_por_facturar(oc) if l['esperado'] > 0]
    if not lineas:
        messages.info(request, 'La orden ya no tiene cantidades pendientes de facturar.')
        return redirect('portal:oc_detalle', pk)
    form = FacturaCabeceraForm(request.POST or None, request.FILES or None)
    errores = []
    if request.method == 'POST':
        enviadas, errores = leer_lineas(request.POST, lineas)
        if form.is_valid() and not errores:
            pdf = form.cleaned_data['archivo_pdf']
            xml = form.cleaned_data.get('archivo_xml')
            archivos = {'pdf': pdf.read(), 'pdf_nombre': pdf.name, 'xml_nombre': xml.name if xml else ''}
            try:
                f = servicios.registrar_factura(oc, request.user, form.cleaned_data, enviadas, archivos,
                                                form.datos_xml)
                aviso = {'VALIDO': ' SUNAT: comprobante válido.',
                         'ERROR': ' No se pudo consultar a SUNAT; el cliente lo validará.'}.get(f.estado_sunat, '')
                messages.success(request, f'Factura {f.numero_completo} enviada para revisión.{aviso}')
                return redirect('portal:factura', f.pk)
            except servicios.ErrorPortal as exc:
                errores = exc.args[0]
    for l in lineas:  # valores escritos por el proveedor al volver a mostrar el formulario
        l['cant_txt'] = request.POST.get(f'cant_{l["item"].pk}', l['esperado'])
        l['precio_txt'] = request.POST.get(f'precio_{l["item"].pk}', l['precio'])
    from core.models import Empresa
    e = Empresa.actual()
    return render(request, 'proveedores/portal_factura_form.html', {
        'oc': oc, 'lineas': lineas, 'form': form, 'errores': errores, 'tol_c': e.tolerancia_cantidad,
        'tol_p': e.tolerancia_precio, 'tol_t': e.tolerancia_total, 'igv_tasa': e.igv_tasa,
        'xml_lineas': [{'descripcion': l['descripcion'], 'cantidad': str(l['cantidad']), 'precio': str(l['precio'])}
                       for l in (form.datos_xml or {}).get('lineas', [])]})


def _descargar(f, tipo):
    from django.http import Http404, HttpResponse
    if tipo == 'pdf' and f.pdf:
        resp = HttpResponse(bytes(f.pdf), content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{f.pdf_nombre or f.numero_completo + ".pdf"}"'
        return resp
    if tipo == 'xml' and f.xml:
        resp = HttpResponse(f.xml, content_type='application/xml; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{f.tercero.numero_doc}-01-{f.numero_completo}.xml"'
        return resp
    raise Http404


@portal_requerido
def factura_archivo(request, pk, tipo):
    return _descargar(get_object_or_404(FacturaProveedor.objects.filter(tercero=request.tercero), pk=pk), tipo)


@portal_requerido
def factura(request, pk):
    f = get_object_or_404(FacturaProveedor.objects.filter(tercero=request.tercero), pk=pk)
    return render(request, 'proveedores/portal_factura.html', {'f': f, 'items': f.items.all()})
