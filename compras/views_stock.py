"""Atender con stock de otro almacén antes de comprar (COM-13): disponibilidad, solicitud de traslado, aprobación
del almacén de origen y recepción (o rechazo) en el destino."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Almacen, Producto

from . import stock
from .models import LineaSolicitud


def _decimal(v):
    try:
        return Decimal(str(v or '').replace(',', '').strip() or '0')
    except InvalidOperation:
        return Decimal('0')


@login_required
def traslado_nuevo(request):
    """Disponibilidad del producto por almacén y la solicitud de traslado al almacén que lo necesita."""
    linea = LineaSolicitud.objects.filter(pk=request.GET.get('linea') or request.POST.get('linea') or 0).select_related(
        'solicitud__centro_costo', 'producto').first()
    producto = linea.producto if linea and linea.producto_id else get_object_or_404(
        Producto, pk=request.GET.get('producto') or request.POST.get('producto') or 0)
    destino = (stock.almacen_de_centro(linea.solicitud.centro_costo_id) if linea else
               Almacen.objects.filter(pk=request.GET.get('destino') or request.POST.get('destino') or 0).first()
               or Almacen.principal())
    centro = linea.solicitud.centro_costo if linea else None
    if centro is None:
        from obras.models import Obra
        obra = Obra.objects.filter(almacen=destino).select_related('centro_costo').first()
        centro = obra.centro_costo if obra else None
    filas = stock.disponibilidad({producto.pk}, excluir_almacen=destino.pk).get(producto.pk, [])
    if request.method == 'POST':
        origen = Almacen.objects.filter(pk=request.POST.get('origen') or 0).first()
        try:
            t = stock.solicitar_traslado(producto, _decimal(request.POST.get('cantidad')), origen, destino,
                                         request.user, linea=linea, centro_costo=centro,
                                         motivo=request.POST.get('motivo', ''))
        except stock.ErrorStock as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'{t} solicitado: lo aprueba el responsable de {origen} (o el residente de su '
                                      f'obra) y sale a tránsito; quien recibe lo acepta o lo rechaza.')
            return redirect('compras:traslados')
    sugerida = request.GET.get('cantidad') or (linea.pendiente if linea else '')
    return render(request, 'compras/traslado_nuevo.html', {
        'producto': producto, 'destino': destino, 'linea': linea, 'filas': filas, 'sugerida': sugerida,
        'centro': centro, 'total': stock.total_disponible(filas)})


@login_required
def traslados(request):
    from inventario.models import SolicitudTraslado
    if request.method == 'POST':
        t = get_object_or_404(SolicitudTraslado, pk=request.POST.get('traslado'))
        accion = request.POST.get('accion')
        try:
            if accion == 'aprobar':
                stock.aprobar_traslado(t, request.user)
                messages.success(request, f'{t} aprobado: salió a tránsito ({t.envio.numero}). Emita la guía de '
                                          f'remisión si va por carretera.')
            elif accion == 'rechazar':
                stock.rechazar_traslado(t, request.user, request.POST.get('motivo', ''))
                messages.info(request, f'{t} rechazado.')
            elif accion in ('recibir', 'devolver'):
                stock.recibir_traslado(t, request.user, aceptar=accion == 'recibir', motivo=request.POST.get('motivo', ''))
                messages.success(request, f'{t} {"recibido en " + str(t.almacen_destino) if accion == "recibir" else "devuelto al origen"}.')
            elif accion == 'anular' and t.estado == 'POR_APROBAR' and (t.solicitado_por_id == request.user.pk or
                                                                       request.user.is_superuser):
                t.estado = 'ANULADA'
                t.save(update_fields=['estado'])
                messages.info(request, f'{t} anulado.')
        except stock.ErrorStock as exc:
            messages.error(request, str(exc))
        return redirect(request.get_full_path())
    estado = request.GET.get('estado', 'pendientes')
    qs = SolicitudTraslado.objects.select_related('producto', 'almacen_origen', 'almacen_destino', 'envio',
                                                  'recepcion', 'solicitado_por', 'linea_solped__solicitud')
    if estado == 'pendientes':
        qs = qs.filter(estado__in=('POR_APROBAR', 'EN_TRANSITO'))
    elif estado:
        qs = qs.filter(estado=estado)
    filas = list(qs[:300])
    for t in filas:
        t.puedo_aprobar = t.estado == 'POR_APROBAR' and stock.puede_aprobar(request.user, t)
        t.puedo_recibir = t.estado == 'EN_TRANSITO' and stock.puede_recibir(request.user, t)
    return render(request, 'compras/traslados.html', {'traslados': filas, 'estado': estado,
                                                      'estados': SolicitudTraslado.ESTADOS})
