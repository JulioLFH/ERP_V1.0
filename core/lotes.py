"""Lotes y números de serie: stock por lote, vencimientos y trazabilidad (de qué proveedor vino, a quién salió)."""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import D0, DIAS_AVISO_VENCIMIENTO, Almacen, Lote, Producto, StockLote
from .utils import excel_response, fmt_fecha


def por_vencer(hoy=None, dias=DIAS_AVISO_VENCIMIENTO):
    """Lotes con stock que vencen dentro del plazo (incluye los ya vencidos)."""
    hoy = hoy or timezone.localdate()
    ids = StockLote.objects.filter(cantidad__gt=0).values('lote')
    return list(Lote.objects.filter(pk__in=ids, vencimiento__isnull=False, vencimiento__lte=hoy + timedelta(days=dias))
                .select_related('producto').order_by('vencimiento'))


@login_required
def lista(request):
    from .permisos import almacenes_permitidos
    hoy = timezone.localdate()
    permitidos = almacenes_permitidos(request.user)  # usuario limitado: existencia solo en sus almacenes
    suma = Sum('stocks__cantidad') if permitidos is None else Sum('stocks__cantidad',
                                                                  filter=Q(stocks__almacen_id__in=permitidos))
    qs = Lote.objects.select_related('producto').annotate(existencia=suma)
    if request.GET.get('producto'):
        qs = qs.filter(producto_id=request.GET['producto'])
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(codigo__icontains=q) | Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q))
    if not request.GET.get('todos'):
        qs = qs.filter(existencia__gt=0)
    dias = int(request.GET.get('dias') or DIAS_AVISO_VENCIMIENTO)
    if request.GET.get('vencimiento'):
        qs = qs.filter(vencimiento__isnull=False, vencimiento__lte=hoy + timedelta(days=dias))
    lotes = list(qs.order_by('vencimiento', 'producto__nombre', 'codigo'))
    almacenes = {}
    stocks = StockLote.objects.filter(lote__in=lotes, cantidad__gt=0).select_related('almacen')
    if permitidos is not None:
        stocks = stocks.filter(almacen_id__in=permitidos)
    for s in stocks:
        almacenes.setdefault(s.lote_id, []).append(f'{s.almacen.nombre}: {s.cantidad:g}')
    for l in lotes:
        l.en_almacenes = almacenes.get(l.pk, [])
    if request.GET.get('formato') == 'excel':
        datos = [[l.producto.codigo, l.producto.nombre, l.codigo, fmt_fecha(l.vencimiento), l.dias_para_vencer,
                  l.existencia or D0, '; '.join(l.en_almacenes)] for l in lotes]
        return excel_response('Lotes', 'Lotes y series', ['Código', 'Producto', 'Lote / serie', 'Vence', 'Días',
                                                          'Stock', 'Almacenes'], datos)
    return render(request, 'inventario/lotes.html', {
        'lotes': lotes, 'q': q, 'dias': dias, 'hoy': hoy, 'aviso': hoy + timedelta(days=dias),
        'productos': Producto.objects.exclude(control='').order_by('nombre')})


@login_required
def detalle(request, pk):
    """Trazabilidad del lote: cada entrada y salida con su documento."""
    lote = get_object_or_404(Lote.objects.select_related('producto'), pk=pk)
    from .modulos import puede_ver_costos
    from .permisos import almacenes_permitidos
    permitidos = almacenes_permitidos(request.user)
    movimientos, stocks = lote.kardex.select_related('almacen'), lote.stocks.filter(cantidad__gt=0)
    if permitidos is not None:
        movimientos, stocks = movimientos.filter(almacen_id__in=permitidos), stocks.filter(almacen_id__in=permitidos)
    return render(request, 'inventario/lote_detalle.html', {
        'lote': lote, 'movimientos': movimientos.order_by('fecha', 'id'),
        'stocks': stocks.select_related('almacen'),
        'ver_costos': puede_ver_costos(request.user)})
