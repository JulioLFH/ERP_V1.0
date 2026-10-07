"""Pantallas de picking por olas e inventario cíclico ABC."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Almacen
from core.utils import excel_response

from . import almacen as alm
from .models import ConteoCiclico, OlaPicking


def _dec(valor):
    try:
        return Decimal(str(valor).replace(',', '').strip()) if str(valor or '').strip() else None
    except InvalidOperation:
        return None


def _almacen(request):
    almacenes = Almacen.objects.filter(activo=True, uso='')
    elegido = request.GET.get('almacen') or request.POST.get('almacen')
    return almacenes, (almacenes.filter(pk=elegido).first() if elegido else None) or Almacen.principal()


# ---------------------------------------------------------------- picking por olas
@login_required
def olas(request):
    qs = OlaPicking.objects.select_related('almacen').prefetch_related('pedidos')
    estado = request.GET.get('estado', 'ABIERTA')
    if estado:
        qs = qs.filter(estado=estado)
    return render(request, 'inventario/olas.html', {'olas': qs[:200], 'estado': estado,
                                                    'estados': OlaPicking.ESTADOS,
                                                    'pendientes': alm.pedidos_pendientes().count()})


@login_required
def ola_nueva(request):
    almacenes, almacen = _almacen(request)
    pedidos = alm.pedidos_pendientes().prefetch_related('items')
    if request.method == 'POST':
        elegidos = pedidos.filter(pk__in=request.POST.getlist('pedido'))
        try:
            ola = alm.crear_ola(almacen, elegidos, request.user, request.POST.get('observaciones', ''))
            messages.success(request, f'{ola} creada con {ola.pedidos.count()} pedido(s) y {ola.lineas.count()} '
                                      'producto(s) a recoger.')
            return redirect('inventario:ola', ola.pk)
        except alm.ErrorAlmacen as exc:
            messages.error(request, str(exc))
    return render(request, 'inventario/ola_nueva.html', {'almacenes': almacenes, 'almacen': almacen,
                                                         'pedidos': pedidos})


@login_required
def ola(request, pk):
    o = get_object_or_404(OlaPicking.objects.select_related('almacen', 'creado_por', 'preparado_por'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'preparar':
                preparadas = {li.pk: _dec(request.POST.get(f'prep_{li.pk}')) for li in o.lineas.all()
                              if request.POST.get(f'prep_{li.pk}', '').strip()}
                alm.confirmar_preparacion(o, preparadas, request.user)
                messages.success(request, f'{o} preparada: separe lo recogido por pedido.')
            elif accion == 'anular' and o.estado == 'ABIERTA':
                o.estado = 'ANULADA'
                o.save(update_fields=['estado'])
                messages.success(request, f'{o} anulada: sus pedidos vuelven a estar disponibles.')
        except alm.ErrorAlmacen as exc:
            messages.error(request, str(exc))
        return redirect('inventario:ola', pk)
    filas = alm.lineas_con_stock(o)
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Picking_{o.numero}', f'HOJA DE PICKING {o.numero} - {o.almacen}', [
            'Ubicación', 'Código', 'Producto', 'U.M.', 'Cantidad', 'Pedidos', 'Recogido'],
            [[f['l'].ubicacion or '(sin ubicación)', f['l'].producto.codigo, f['l'].producto.nombre,
              f['l'].producto.unidad, f['l'].cantidad, ', '.join(f'{k}: {v}' for k, v in f['l'].detalle.items()), '']
             for f in filas])
    return render(request, 'inventario/ola.html', {'o': o, 'filas': filas, 'separacion': alm.por_pedido(o),
                                                   'faltan': sum(1 for f in filas if f['falta'] > 0)})


# ---------------------------------------------------------------- inventario cíclico ABC
@login_required
def ciclico(request):
    almacenes, almacen = _almacen(request)
    if request.method == 'POST':
        try:
            maximo = max(1, min(500, int(request.POST.get('maximo') or 40)))
        except ValueError:
            maximo = 40
        clases = [c for c in request.POST.getlist('clase') if c in alm.FRECUENCIA] or list(alm.FRECUENCIA)
        try:
            conteo = alm.generar_conteo(almacen, request.user, clases, maximo)
            messages.success(request, f'{conteo} generado con {conteo.items.count()} producto(s).')
            return redirect('inventario:conteo', conteo.pk)
        except alm.ErrorAlmacen as exc:
            messages.error(request, str(exc))
        return redirect(f'{request.path}?almacen={almacen.pk}')
    filas = alm.programa(almacen)
    resumen = {c: {'total': 0, 'vencidos': 0} for c in alm.FRECUENCIA}
    for f in filas:
        resumen[f['clase']]['total'] += 1
        resumen[f['clase']]['vencidos'] += f['vencido']
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Clasificacion_ABC_{almacen.codigo}', f'Clasificación ABC y programa de conteo - '
                                                                        f'{almacen}', [
            'Código', 'Producto', 'Clase', 'Valor consumido 12 meses', 'Stock', 'Último conteo', 'Toca contar'],
            [[f['p'].codigo, f['p'].nombre, f['clase'], f['valor'], f['stock'], f['ultimo'] or '', f['toca']]
             for f in filas])
    clase = request.GET.get('clase', '')
    return render(request, 'inventario/ciclico.html', {
        'almacenes': almacenes, 'almacen': almacen, 'clase': clase,
        'filas': [f for f in filas if not clase or f['clase'] == clase][:500],
        'resumen': resumen, 'frecuencia': alm.FRECUENCIA,
        'conteos': ConteoCiclico.objects.filter(almacen=almacen).select_related('creado_por')[:20],
        'exactitud': alm.exactitud(almacen, timezone.localdate() - timedelta(days=90))})


@login_required
def conteo(request, pk):
    c = get_object_or_404(ConteoCiclico.objects.select_related('almacen', 'ajuste_ingreso', 'ajuste_salida'), pk=pk)
    items = list(c.items.select_related('producto'))
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'cerrar':
                contados = {i.pk: _dec(request.POST.get(f'contado_{i.pk}')) for i in items
                            if request.POST.get(f'contado_{i.pk}', '').strip()}
                alm.cerrar_conteo(c, contados, request.user)
                messages.success(request, f'{c} cerrado y diferencias ajustadas.')
            elif accion == 'anular' and c.estado == 'ABIERTO':
                c.estado = 'ANULADO'
                c.save(update_fields=['estado'])
        except alm.ErrorAlmacen as exc:
            messages.error(request, str(exc))
        return redirect('inventario:conteo', pk)
    if request.GET.get('formato') == 'excel':  # hoja de conteo a ciegas (sin el stock del sistema)
        return excel_response(f'Conteo_{c.numero}', f'HOJA DE CONTEO {c.numero} - {c.almacen}', [
            'Ubicación', 'Código', 'Producto', 'U.M.', 'Contado'],
            [[i.ubicacion, i.producto.codigo, i.producto.nombre, i.producto.unidad, ''] for i in items])
    return render(request, 'inventario/conteo.html', {'c': c, 'items': items, 'ciego': request.GET.get('ciego')})
