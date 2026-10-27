"""Estado del stock (INV-26): stock libre, en control de calidad, bloqueado y vencido por producto, almacén y lote,
con el cambio de estado de un lote o el traslado de un producto sin lote a un almacén de calidad o bloqueo."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Almacen, Lote, Producto

from . import estados
from .models import CambioEstadoStock
from .reportes_masivos import por_estado


@login_required
def estado_stock(request):
    if request.method == 'POST':
        try:
            if request.POST.get('que') == 'lote':
                valor = (request.POST.get('lote') or '').strip()
                # ALM-15: el lote escrito como texto («L-E2») era un error 500: se busca por su código
                lotes = Lote.objects.filter(pk=int(valor)) if valor.isdigit() else Lote.objects.filter(
                    codigo__iexact=valor)
                if request.POST.get('producto', '').isdigit():
                    lotes = lotes.filter(producto_id=int(request.POST['producto']))
                lotes = list(lotes[:2])
                if not lotes:
                    raise estados.ErrorLote(f'No existe el lote «{valor}»: elíjalo de la lista.' if valor else
                                            'Elija el lote.')
                if len(lotes) > 1:
                    raise estados.ErrorLote(f'Hay más de un lote «{valor}» (de distintos productos): elíjalo de la '
                                            f'lista.')
                lote = lotes[0]
                c = estados.cambiar_estado_lote(lote, request.POST.get('estado'), request.POST.get('motivo'),
                                                request.user)
                messages.success(request, f'Lote {lote.codigo}: {c.get_estado_anterior_display()} → '
                                          f'{c.get_estado_nuevo_display()}.')
            else:
                producto = get_object_or_404(Producto, pk=request.POST.get('producto') or 0)
                origen = get_object_or_404(Almacen, pk=request.POST.get('origen') or 0)
                destino = get_object_or_404(Almacen, pk=request.POST.get('destino') or 0)
                try:
                    cantidad = Decimal(request.POST.get('cantidad') or '0')
                except InvalidOperation:
                    cantidad = Decimal('0')
                if cantidad <= 0:
                    raise estados.ErrorLote('Indique la cantidad.')
                c = estados.trasladar_estado(producto, cantidad, origen, destino, request.POST.get('motivo'),
                                             request.user, request.POST.get('lote_codigo', '').strip())
                messages.success(request, f'{producto.nombre}: {cantidad:g} pasan a {c.get_estado_nuevo_display()} '
                                          f'({c.operacion.numero}).')
        except estados.ErrorLote as exc:
            messages.error(request, str(exc))
        return redirect(request.get_full_path())
    filas = por_estado()
    estado, q = request.GET.get('estado', ''), (request.GET.get('q') or '').strip().lower()
    if estado:
        filas = [f for f in filas if f['estado'] == estado]
    if q:
        filas = [f for f in filas if q in f['producto'].codigo.lower() or q in f['producto'].nombre.lower()
                 or (f['lote'] and q in f['lote'].codigo.lower())]
    totales = {}
    for f in por_estado():
        totales[f['estado']] = totales.get(f['estado'], Decimal('0')) + f['cantidad']
    return render(request, 'inventario/estado_stock.html', {
        'filas': filas[:500], 'estado': estado, 'q': request.GET.get('q', ''), 'totales': totales,
        'estados': ['Libre', 'En calidad', 'Bloqueado', 'Vencido'], 'destinos': estados.ESTADOS_DESTINO,
        'almacenes': Almacen.objects.filter(activo=True).order_by('uso', 'codigo'),
        'cambios': CambioEstadoStock.objects.select_related('lote', 'producto', 'usuario', 'operacion')[:30]})
