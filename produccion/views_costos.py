"""Módulo Costos: costo estándar, real vs estándar por orden y rentabilidad. Exige el permiso de ver costos."""
from decimal import Decimal
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from core.modulos import puede_ver_costos
from core.utils import excel_response, rango_por_defecto

from . import servicios
from .costos import AGRUPACIONES, rentabilidad as calcular_rentabilidad
from .models import ListaMateriales, OrdenProduccion

D0 = Decimal('0')


def con_costos(vista):
    @login_required
    @wraps(vista)
    def envuelta(request, *args, **kwargs):
        if not puede_ver_costos(request.user):
            from core.inventario import _sin_permiso_costos
            return _sin_permiso_costos(request)
        return vista(request, *args, **kwargs)
    return envuelta


@con_costos
def estandar(request):
    """Costo estándar de cada producto con receta vigente frente a su costo promedio y su precio de venta."""
    filas = []
    for lista in ListaMateriales.objects.filter(activa=True).select_related('producto'):
        hoja = servicios.hoja_costos(lista)
        p = lista.producto
        margen = p.precio_venta - hoja['unitario'] if p.precio_venta else None
        filas.append({'lista': lista, 'p': p, 'hoja': hoja, 'margen': margen,
                      'pct': (margen / p.precio_venta * 100).quantize(Decimal('0.1')) if margen is not None else None,
                      'dif_promedio': (p.costo_promedio - hoja['unitario']) if p.costo_promedio else None})
    if request.GET.get('formato') == 'excel':
        datos = [[f['p'].codigo, f['p'].nombre, f['lista'].codigo, f['hoja']['tot_materiales'] / f['hoja']['cantidad'],
                  (f['hoja']['tot_mano_obra'] + f['hoja']['tot_cif']) / f['hoja']['cantidad'], f['hoja']['unitario'],
                  f['p'].costo_promedio, f['p'].precio_venta, f['margen'] or '', f['pct'] or ''] for f in filas]
        return excel_response('Costo_estandar', 'Costo estándar por producto',
                              ['Código', 'Producto', 'Receta', 'Materiales', 'Conversión (MO+CIF)', 'Costo estándar',
                               'Costo promedio', 'Precio venta', 'Margen S/', 'Margen %'], datos)
    return render(request, 'produccion/costo_estandar.html', {'filas': filas})


@con_costos
def hoja(request, pk):
    lista = get_object_or_404(ListaMateriales.objects.select_related('producto'), pk=pk)
    cantidad = None
    if request.GET.get('cantidad'):
        try:
            cantidad = Decimal(request.GET['cantidad'])
        except ArithmeticError:
            cantidad = None
        if cantidad is not None and cantidad <= 0:
            cantidad = None
    return render(request, 'produccion/hoja_costos.html', {'lista': lista,
                                                           'hoja': servicios.hoja_costos(lista, cantidad=cantidad)})


@con_costos
def real_vs_estandar(request):
    qs = OrdenProduccion.objects.filter(estado='TERMINADA').select_related('producto', 'lista')
    desde, hasta = rango_por_defecto(request, qs, campo='fecha_fin')
    filas, totales = [], {'estandar': D0, 'real': D0}
    for o in qs.filter(fecha_fin__range=[desde, hasta]).order_by('fecha_fin', 'id'):
        estandar = o.costo_estandar_unit * o.cantidad_producida
        real = o.costo_total
        filas.append({'o': o, 'estandar': estandar, 'real': real, 'variacion': real - estandar,
                      'pct': ((real - estandar) / estandar * 100).quantize(Decimal('0.1')) if estandar else None,
                      'rendimiento': (o.cantidad_producida / o.cantidad * 100).quantize(Decimal('0.1'))
                      if o.cantidad else None})
        totales['estandar'] += estandar
        totales['real'] += real
    totales['variacion'] = totales['real'] - totales['estandar']
    if request.GET.get('formato') == 'excel':
        datos = [[f['o'].numero, f['o'].fecha_fin.strftime('%d/%m/%Y'), f['o'].producto.nombre, f['o'].cantidad,
                  f['o'].cantidad_producida, f['o'].costo_materiales, f['o'].costo_mano_obra, f['o'].costo_cif,
                  f['real'], f['estandar'], f['variacion'], f['pct'] or ''] for f in filas]
        return excel_response('Costo_real_vs_estandar', f'Costo real vs estándar {desde} a {hasta}',
                              ['Orden', 'Término', 'Producto', 'Planificado', 'Producido', 'Materiales', 'Mano de obra',
                               'Indirectos', 'Costo real', 'Costo estándar', 'Variación', 'Var. %'], datos)
    return render(request, 'produccion/real_vs_estandar.html', {'filas': filas, 'totales': totales,
                                                                'desde': desde, 'hasta': hasta})


@con_costos
def rentabilidad(request):
    from ventas.models import Venta
    agrupar = request.GET.get('agrupar', 'producto')
    if agrupar not in dict(AGRUPACIONES):
        agrupar = 'producto'
    desde, hasta = rango_por_defecto(request, Venta.objects.filter(estado='REGISTRADO'), campo='fecha_emision')
    filas, total = calcular_rentabilidad(desde, hasta, agrupar)
    titulo = dict(AGRUPACIONES)[agrupar]
    if request.GET.get('formato') == 'excel':
        datos = [[f['nombre'], f['cantidad'], f['ventas'], f['costo'], f['margen'], f['pct'] or ''] for f in filas]
        datos.append(['TOTAL', '', total['ventas'], total['costo'], total['margen'], total['pct'] or ''])
        return excel_response(f'Rentabilidad_{agrupar}', f'Rentabilidad por {titulo.lower()} {desde} a {hasta}',
                              [titulo, 'Cantidad', 'Ventas netas S/', 'Costo de ventas S/', 'Margen S/', 'Margen %'],
                              datos)
    return render(request, 'produccion/rentabilidad.html', {
        'filas': filas, 'total': total, 'agrupar': agrupar, 'agrupaciones': AGRUPACIONES, 'titulo_grupo': titulo,
        'desde': desde, 'hasta': hasta})
