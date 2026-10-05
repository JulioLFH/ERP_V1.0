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
    """Costo estándar por periodo: se calcula (marca) y se libera. Liberado queda fijo para las órdenes del periodo."""
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.utils import timezone

    from .models import CostoEstandar, VersionFabricacion
    periodo = (request.GET.get('periodo') or request.POST.get('periodo') or
               timezone.localdate().strftime('%Y-%m')).replace('-', '')[:6]
    if request.method == 'POST':  # el middleware exige la acción "Calcular y liberar el costo estándar"
        if request.POST.get('accion') == 'calcular':
            hechos = servicios.calcular_estandar(periodo, request.user)
            messages.success(request, f'Estándar calculado para {len(hechos)} producto(s). Revíselo y libérelo.')
        elif request.POST.get('accion') == 'liberar':
            n = servicios.liberar_estandar(periodo, request.user)
            messages.success(request, f'{n} estándar(es) liberado(s): las órdenes de {periodo[4:]}/{periodo[:4]} se '
                                      'comparan contra ellos.')
        return redirect(f"{request.path}?periodo={periodo[:4]}-{periodo[4:]}")
    costos = {c.producto_id: c for c in CostoEstandar.objects.filter(periodo=periodo).select_related('version')}
    filas = []
    for v in (VersionFabricacion.objects.filter(activa=True).select_related('producto', 'lista')
              .order_by('producto__nombre', 'codigo')):
        if any(f['p'].pk == v.producto_id for f in filas):
            continue
        p, ce = v.producto, costos.get(v.producto_id)
        unitario = ce.unitario if ce else None
        margen = p.precio_venta - unitario if (p.precio_venta and unitario is not None) else None
        filas.append({'p': p, 'version': ce.version if ce else v, 'ce': ce, 'margen': margen,
                      'pct': (margen / p.precio_venta * 100).quantize(Decimal('0.1')) if margen is not None else None,
                      'anterior': servicios.estandar_de(p, None) if not ce else None})
    if request.GET.get('formato') == 'excel':
        datos = [[f['p'].codigo, f['p'].nombre, f['version'].codigo, f['ce'].materiales if f['ce'] else '',
                  f['ce'].mano_obra if f['ce'] else '', f['ce'].cif if f['ce'] else '',
                  f['ce'].unitario if f['ce'] else '', f['ce'].get_estado_display() if f['ce'] else 'Sin calcular',
                  f['p'].costo_promedio, f['p'].precio_venta, f['margen'] or '', f['pct'] or ''] for f in filas]
        return excel_response(f'Costo_estandar_{periodo}', f'Costo estándar {periodo[4:]}/{periodo[:4]}',
                              ['Código', 'Producto', 'Versión', 'Materiales', 'Mano de obra', 'Máquina y CIF',
                               'Estándar unitario', 'Estado', 'Costo promedio', 'Precio venta', 'Margen S/',
                               'Margen %'], datos)
    return render(request, 'produccion/costo_estandar.html', {
        'filas': filas, 'periodo': periodo, 'periodo_input': f'{periodo[:4]}-{periodo[4:]}',
        'por_liberar': sum(1 for c in costos.values() if c.estado == 'CALCULADO')})


@con_costos
def absorcion(request):
    """Mano de obra y CIF absorbidos por las órdenes frente al gasto real de cada centro de costo de planta."""
    from django.utils import timezone
    periodo = (request.GET.get('periodo') or timezone.localdate().strftime('%Y-%m')).replace('-', '')[:6]
    filas, sin_centro = servicios.absorcion(periodo)
    totales = {k: sum((f[k] for f in filas), D0) for k in ('real', 'mo', 'cif', 'absorbido', 'diferencia')}
    return render(request, 'produccion/absorcion.html', {
        'filas': filas, 'totales': totales, 'sin_centro': sin_centro, 'periodo': periodo,
        'periodo_input': f'{periodo[:4]}-{periodo[4:]}'})


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
    from .models import VariacionOrden
    qs = OrdenProduccion.objects.filter(estado='TERMINADA').select_related('producto', 'lista', 'estandar')
    desde, hasta = rango_por_defecto(request, qs, campo='fecha_fin')
    tipos = VariacionOrden.TIPOS
    filas, totales = [], {'estandar': D0, 'real': D0, **{t: D0 for t, _ in tipos}}
    for o in qs.filter(fecha_fin__range=[desde, hasta]).order_by('fecha_fin', 'id').prefetch_related('variaciones'):
        estandar = o.costo_estandar_unit * o.cantidad_producida
        real = o.costo_total
        por_tipo = {t: D0 for t, _ in tipos}
        for v in o.variaciones.all():
            por_tipo[v.tipo] += v.monto
            totales[v.tipo] += v.monto
        filas.append({'o': o, 'estandar': estandar, 'real': real, 'variacion': real - estandar,
                      'por_tipo': [por_tipo[t] for t, _ in tipos],
                      'pct': ((real - estandar) / estandar * 100).quantize(Decimal('0.1')) if estandar else None,
                      'rendimiento': (o.cantidad_producida / o.cantidad * 100).quantize(Decimal('0.1'))
                      if o.cantidad else None,
                      'provisional': bool((o.estandar_detalle or {}).get('provisional'))})
        totales['estandar'] += estandar
        totales['real'] += real
    totales['variacion'] = totales['real'] - totales['estandar']
    totales['por_tipo'] = [totales[t] for t, _ in tipos]
    if request.GET.get('formato') == 'excel':
        datos = [[f['o'].numero, f['o'].fecha_fin.strftime('%d/%m/%Y'), f['o'].producto.nombre, f['o'].cantidad,
                  f['o'].cantidad_producida, f['real'], f['estandar'], f['variacion']] + f['por_tipo'] for f in filas]
        return excel_response('Costo_real_vs_estandar', f'Costo real vs estándar {desde} a {hasta}',
                              ['Orden', 'Término', 'Producto', 'Planificado', 'Producido', 'Costo real',
                               'Costo estándar', 'Variación total'] + [n for _, n in tipos], datos)
    return render(request, 'produccion/real_vs_estandar.html', {
        'filas': filas, 'totales': totales, 'tipos': tipos, 'desde': desde, 'hasta': hasta})


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
