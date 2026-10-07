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
                  f['ce'].mano_obra if f['ce'] else '', f['ce'].maquina if f['ce'] else '', f['ce'].cif if f['ce'] else '',
                  f['ce'].unitario if f['ce'] else '', f['ce'].get_estado_display() if f['ce'] else 'Sin calcular',
                  f['p'].costo_promedio, f['p'].precio_venta, f['margen'] or '', f['pct'] or ''] for f in filas]
        return excel_response(f'Costo_estandar_{periodo}', f'Costo estándar {periodo[4:]}/{periodo[:4]}',
                              ['Código', 'Producto', 'Versión', 'Materiales', 'Mano de obra', 'Máquina', 'CIF',
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


def _mes_anterior():
    from django.utils import timezone
    hoy = timezone.localdate()
    return f'{hoy.year - (hoy.month == 1)}{12 if hoy.month == 1 else hoy.month - 1:02d}'


@con_costos
def liquidacion(request):
    """Cierre de costos NIC 2: liquida el gasto real de planta del periodo a las órdenes, al inventario y al costo de
    ventas; la capacidad ociosa queda en gasto."""
    from django.contrib import messages
    from django.shortcuts import redirect

    from . import liquidacion as liq_srv
    from .models import ComportamientoGasto, LiquidacionCosto
    periodo = (request.GET.get('periodo') or request.POST.get('periodo') or _mes_anterior()).replace('-', '')[:6]
    if request.method == 'POST':  # el middleware exige la acción "Liquidar costo real y registrar el VNR"
        try:
            if request.POST.get('accion') == 'anular':
                liq_srv.anular(periodo)
                messages.success(request, 'Liquidación anulada: el inventario y la contabilidad vuelven al costo con '
                                          'tarifas.')
            else:
                liq = liq_srv.liquidar(periodo, request.user)
                t = liq_srv.resumen(liq)['tot']
                messages.success(request, f'Costo real liquidado: S/ {t["diferencia"]:,.2f} al producto '
                                          f'(S/ {t["a_inventario"]:,.2f} al inventario y S/ {t["a_costo"]:,.2f} al '
                                          f'costo de ventas); S/ {t["gasto"]:,.2f} de capacidad ociosa queda en gasto.')
        except liq_srv.ErrorLiquidacion as exc:
            messages.error(request, str(exc))
        return redirect(f'{request.path}?periodo={periodo[:4]}-{periodo[4:]}')
    liq = LiquidacionCosto.objects.filter(periodo=periodo).first()
    datos = liq_srv.resumen(liq) if liq else None
    if liq and request.GET.get('formato') == 'excel':
        filas = [[lo.orden.numero, lo.orden.producto.nombre, lo.orden.cantidad_producida, lo.orden.costo_total,
                  lo.mano_obra, lo.cif, lo.de_insumos, lo.orden.costo_real_final] for lo in datos['ordenes']]
        return excel_response(f'Liquidacion_costo_real_{periodo}', f'Liquidación de costo real {periodo[4:]}/'
                              f'{periodo[:4]}', ['Orden', 'Producto', 'Producido', 'Costo con tarifas',
                                                 'Mano de obra real', 'CIF real', 'De semielaborados',
                                                 'Costo real final'], filas)
    return render(request, 'produccion/liquidacion.html', {
        'liq': liq, 'datos': datos, 'periodo': periodo, 'periodo_input': f'{periodo[:4]}-{periodo[4:]}',
        'reglas': [(p, dict(ComportamientoGasto.TIPOS)[t]) for p, t in sorted(liq_srv.reglas())]})


@con_costos
def comportamiento(request):
    """Qué cuentas de planta son mano de obra, CIF variable o CIF fijo (lo define el contador)."""
    from django.contrib import messages
    from django.shortcuts import redirect

    from .models import ComportamientoGasto
    if request.method == 'POST':
        if request.POST.get('accion') == 'eliminar':
            ComportamientoGasto.objects.filter(pk=request.POST.get('pk')).delete()
        else:
            prefijo = (request.POST.get('prefijo') or '').strip()
            tipo = request.POST.get('tipo')
            if not prefijo.isdigit() or not prefijo.startswith('6') or tipo not in dict(ComportamientoGasto.TIPOS):
                messages.error(request, 'Indique una cuenta de gasto (empieza con 6, solo números) y su tipo.')
            else:
                ComportamientoGasto.objects.update_or_create(prefijo=prefijo, defaults={'tipo': tipo})
                messages.success(request, f'Cuenta {prefijo}: {dict(ComportamientoGasto.TIPOS)[tipo]}.')
        return redirect('costos:comportamiento')
    return render(request, 'produccion/comportamiento.html', {
        'reglas': ComportamientoGasto.objects.all(), 'tipos': ComportamientoGasto.TIPOS})


@con_costos
def vnr(request):
    """Prueba del valor neto realizable (NIC 2): vista previa, registro (asiento 695/29) y anulación."""
    from datetime import date

    from django.contrib import messages
    from django.shortcuts import redirect

    from contabilidad.centralizar import _fin_mes

    from . import liquidacion as liq_srv
    from .models import PruebaVNR
    datos = request.POST if request.method == 'POST' else request.GET
    try:
        fecha = date.fromisoformat(datos.get('fecha')) if datos.get('fecha') else _fin_mes(_mes_anterior())
        gasto = Decimal(datos.get('gasto_venta') or '0')
        dias = max(1, min(int(datos.get('dias') or 90), 730))
    except (ValueError, ArithmeticError):
        messages.error(request, 'Revise la fecha, el % de gastos de venta y los días.')
        return redirect('costos:vnr')
    norma = datos.get('norma') if datos.get('norma') in dict(PruebaVNR.NORMAS) else 'NIIF'
    usar_lista = datos.get('lista') == '1'
    if request.method == 'POST':  # el middleware exige la acción "Liquidar costo real y registrar el VNR"
        try:
            if request.POST.get('accion') == 'anular':
                liq_srv.anular_vnr(get_object_or_404(PruebaVNR, pk=request.POST.get('pk')))
                messages.success(request, 'Prueba anulada y asiento retirado.')
                return redirect('costos:vnr')
            prueba = liq_srv.registrar_vnr(fecha, gasto, dias, norma, request.user, usar_lista)
            messages.success(request, f'{prueba} registrada: asiento de desvalorización en el periodo '
                                      f'{fecha:%m/%Y}.')
            return redirect('costos:vnr_detalle', pk=prueba.pk)
        except liq_srv.ErrorLiquidacion as exc:
            messages.error(request, str(exc))
        return redirect(f'{request.path}?fecha={fecha}&gasto_venta={gasto}&dias={dias}&norma={norma}'
                        f'&lista={int(usar_lista)}')
    filas = liq_srv.calcular_vnr(fecha, gasto, dias, norma, usar_lista) if datos.get('fecha') else None
    return render(request, 'produccion/vnr.html', {
        'filas': filas, 'fecha': fecha, 'gasto_venta': gasto, 'dias': dias, 'norma': norma, 'normas': PruebaVNR.NORMAS,
        'usar_lista': usar_lista,
        'total_deterioro': sum((f['deterioro'] for f in filas or []), D0),
        'total_ajuste': sum((f['ajuste'] for f in filas or []), D0),
        'pruebas': PruebaVNR.objects.prefetch_related('lineas')[:24]})


@con_costos
def vnr_detalle(request, pk):
    from .models import PruebaVNR
    prueba = get_object_or_404(PruebaVNR, pk=pk)
    lineas = list(prueba.lineas.select_related('producto'))
    return render(request, 'produccion/vnr_detalle.html', {
        'prueba': prueba, 'lineas': lineas, 'total_deterioro': sum((l.deterioro for l in lineas), D0),
        'total_ajuste': sum((l.ajuste for l in lineas), D0)})


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
    qs = OrdenProduccion.objects.filter(estado='TERMINADA', es_historica=False).select_related(
        'producto', 'lista', 'estandar')
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
