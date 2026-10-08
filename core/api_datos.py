"""API v1: datos para exportar (contabilidad, kardex, tesorería, cuentas por pagar y producción), con filtros de fecha.

Todas las listas aceptan page y page_size (hasta 1000) y formato=csv para descargar todo el resultado de una vez.
Los reportes contables usan periodos AAAA-MM y el parámetro libro=NIIF o TRIBUTARIO (por defecto, el oficial NIIF).
"""
from decimal import Decimal

from django.db.models import Q, Sum

from .api import ErrorParametro, _filtrar_comprobantes, _pagina, api, error, fecha_param, respuesta, s_comprobante

D0 = Decimal('0')


def _periodo_param(request, nombre, defecto=None):
    """Periodo AAAAMM desde AAAA-MM, AAAAMM o una fecha AAAA-MM-DD."""
    valor = (request.GET.get(nombre) or '').strip().replace('-', '')[:6]
    if not valor:
        return defecto
    if len(valor) != 6 or not valor.isdigit() or not 1 <= int(valor[4:]) <= 12:
        raise ErrorParametro(f'Parámetro {nombre} inválido: use AAAA-MM.')
    return valor


def _rango_periodos(request):
    from django.utils import timezone
    hasta = _periodo_param(request, 'hasta', timezone.localdate().strftime('%Y%m'))
    return _periodo_param(request, 'desde', f'{hasta[:4]}01'), hasta


def _libro(request):
    from contabilidad.models import usar_norma
    libro = (request.GET.get('libro') or 'NIIF').upper()
    if libro not in ('NIIF', 'TRIBUTARIO'):
        raise ErrorParametro('Parámetro libro inválido: NIIF o TRIBUTARIO.')
    return usar_norma(libro)


def _ver_costos(request):
    from .modulos import puede_ver_costos
    return puede_ver_costos(request.user)


# ---------------------------------------------------------------- contabilidad
@api('contabilidad')
def cuentas_contables(request):
    from contabilidad.models import CuentaContable
    qs = CuentaContable.objects.order_by('codigo')
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(codigo__startswith=q) | Q(nombre__icontains=q))
    if request.GET.get('imputable') in ('true', 'false'):
        qs = qs.filter(imputable=request.GET['imputable'] == 'true')
    return respuesta(_pagina(request, qs, lambda c: {
        'id': c.pk, 'codigo': c.codigo, 'nombre': c.nombre, 'naturaleza': c.naturaleza, 'imputable': c.imputable}))


def _s_linea(l):
    return {'id': l.pk, 'asiento_id': l.asiento_id, 'numero': l.asiento.numero, 'fecha': l.asiento.fecha,
            'periodo': l.asiento.periodo, 'libro': l.asiento.libro, 'origen': l.asiento.origen,
            'norma': l.asiento.norma, 'glosa_asiento': l.asiento.glosa, 'cuenta': l.cuenta.codigo,
            'cuenta_nombre': l.cuenta.nombre, 'debe': l.debe, 'haber': l.haber, 'debe_me': l.debe_me,
            'haber_me': l.haber_me, 'moneda': l.asiento.moneda, 'tipo_cambio': l.asiento.tipo_cambio,
            'tercero': l.tercero.numero_doc if l.tercero_id else None,
            'tercero_nombre': l.tercero.nombre if l.tercero_id else None, 'documento': l.documento,
            'glosa': l.glosa, 'centro_costo': l.centro_costo.codigo if l.centro_costo_id else None,
            'centro_beneficio': l.centro_beneficio.codigo if l.centro_beneficio_id else None,
            'es_destino': l.es_destino}


def _filtrar_asientos(request, qs, campo=''):
    desde, hasta = fecha_param(request, 'desde'), fecha_param(request, 'hasta')
    if desde:
        qs = qs.filter(**{f'{campo}fecha__gte': desde})
    if hasta:
        qs = qs.filter(**{f'{campo}fecha__lte': hasta})
    p_desde, p_hasta = _periodo_param(request, 'periodo_desde'), _periodo_param(request, 'periodo_hasta')
    if p_desde:
        qs = qs.filter(**{f'{campo}periodo__gte': p_desde})
    if p_hasta:
        qs = qs.filter(**{f'{campo}periodo__lte': p_hasta})
    for param in ('libro', 'origen'):
        if request.GET.get(param):
            qs = qs.filter(**{f'{campo}{param}': request.GET[param].upper()})
    return qs


@api('contabilidad')
def asientos(request):
    from contabilidad.models import Asiento
    with _libro(request) as norma:
        qs = _filtrar_asientos(request, Asiento.objects.exclude(norma={'NIIF': 'TRIBUTARIO',
                                                                       'TRIBUTARIO': 'NIIF'}[norma.norma]))
        qs = qs.order_by('fecha', 'numero')
        detalle = request.GET.get('detalle') == 'true'

        def serializar(a):
            datos = {'id': a.pk, 'numero': a.numero, 'fecha': a.fecha, 'periodo': a.periodo, 'libro': a.libro,
                     'origen': a.origen, 'norma': a.norma, 'glosa': a.glosa, 'moneda': a.moneda,
                     'tipo_cambio': a.tipo_cambio}
            if detalle:
                datos['lineas'] = [_s_linea(l) for l in a.lineas.select_related(
                    'asiento', 'cuenta', 'tercero', 'centro_costo', 'centro_beneficio')]
            return datos
        return respuesta(_pagina(request, qs, serializar))


@api('contabilidad')
def asiento(request, pk):
    from contabilidad.models import Asiento
    a = Asiento.objects.filter(pk=pk).first()
    if a is None:
        return error('Asiento no encontrado.', 404)
    lineas = [_s_linea(l) for l in a.lineas.select_related('asiento', 'cuenta', 'tercero', 'centro_costo',
                                                            'centro_beneficio')]
    return respuesta({'id': a.pk, 'numero': a.numero, 'fecha': a.fecha, 'periodo': a.periodo, 'libro': a.libro,
                      'origen': a.origen, 'norma': a.norma, 'glosa': a.glosa, 'moneda': a.moneda,
                      'tipo_cambio': a.tipo_cambio, 'lineas': lineas,
                      'total_debe': sum((l['debe'] for l in lineas), D0),
                      'total_haber': sum((l['haber'] for l in lineas), D0)})


@api('contabilidad')
def libro_diario(request):
    """Líneas de los asientos (libro diario plano): lo más práctico para cargar a un BI o a Excel."""
    from contabilidad.models import AsientoLinea
    with _libro(request):
        qs = _filtrar_asientos(request, AsientoLinea.objects.all(), 'asiento__')
        if request.GET.get('cuenta'):
            qs = qs.filter(cuenta__codigo__startswith=request.GET['cuenta'])
        if request.GET.get('centro_costo'):
            qs = qs.filter(centro_costo__codigo=request.GET['centro_costo'])
        if request.GET.get('tercero'):
            qs = qs.filter(tercero__numero_doc=request.GET['tercero'])
        if request.GET.get('destinos') != 'true':
            qs = qs.filter(es_destino=False)
        qs = qs.select_related('asiento', 'cuenta', 'tercero', 'centro_costo', 'centro_beneficio').order_by(
            'asiento__fecha', 'asiento__numero', 'id')
        return respuesta(_pagina(request, qs, _s_linea))


@api('contabilidad')
def balance_comprobacion(request):
    from contabilidad import reportes
    desde, hasta = _rango_periodos(request)
    nivel = request.GET.get('nivel')
    if nivel and nivel not in ('2', '3', '4'):
        raise ErrorParametro('Parámetro nivel inválido: 2, 3 o 4 dígitos.')
    with _libro(request) as norma:
        filas, tot = reportes.balance_comprobacion(desde, hasta, int(nivel) if nivel else None)
        return respuesta({'desde': desde, 'hasta': hasta, 'libro': norma.norma, 'filas': [
            {'cuenta': f['codigo'], 'nombre': f['nombre'], 'debe': f['d'], 'haber': f['h'], 'saldo_deudor': f['sd'],
             'saldo_acreedor': f['sa']} for f in filas],
            'totales': {'debe': tot['d'], 'haber': tot['h'], 'resultado': tot['res_nat']}})


@api('contabilidad')
def estado_resultados(request):
    from contabilidad import reportes
    desde, hasta = _rango_periodos(request)
    vista = request.GET.get('vista') or 'funcion'
    if vista not in ('funcion', 'naturaleza'):
        raise ErrorParametro('Parámetro vista inválido: funcion o naturaleza.')
    with _libro(request) as norma:
        if request.GET.get('mensual') == 'true':
            meses, filas = reportes.resultados_mensual(desde, hasta, vista)
            return respuesta({'desde': desde, 'hasta': hasta, 'libro': norma.norma, 'vista': vista, 'meses': meses,
                              'filas': [{'concepto': f['nombre'], 'tipo': f['estilo'],
                                         'meses': dict(zip(meses, f['valores'])), 'total': f['total']}
                                        for f in filas]})
        calcular = reportes.estado_resultados_naturaleza if vista == 'naturaleza' else reportes.estado_resultados
        lineas, neta = calcular(desde, hasta)
        return respuesta({'desde': desde, 'hasta': hasta, 'libro': norma.norma, 'vista': vista,
                          'filas': [{'concepto': n, 'importe': v, 'tipo': e} for n, v, e in lineas],
                          'resultado': neta})


@api('contabilidad')
def situacion_financiera(request):
    from contabilidad import reportes
    _, hasta = _rango_periodos(request)
    with _libro(request) as norma:
        d = reportes.situacion_financiera(hasta, f'{hasta[:4]}01')

        def rubros(lista):
            return [{'rubro': n, 'importe': v} for n, v in lista]
        return respuesta({'hasta': hasta, 'libro': norma.norma,
                          'activo_corriente': rubros(d['activo']['corriente']),
                          'activo_no_corriente': rubros(d['activo']['no_corriente']),
                          'pasivo_corriente': rubros(d['pasivo']['corriente']),
                          'pasivo_no_corriente': rubros(d['pasivo']['no_corriente']),
                          'patrimonio': rubros(d['patrimonio']),
                          'totales': {'activo': d['t']['activo'], 'pasivo': d['t']['pasivo'],
                                      'patrimonio': d['t']['pat'], 'diferencia': d['t']['diferencia']}})


# ---------------------------------------------------------------- inventario
@api('inventario')
def kardex(request):
    from .models import Kardex
    qs = Kardex.objects.select_related('producto', 'almacen', 'lote')
    desde, hasta = fecha_param(request, 'desde'), fecha_param(request, 'hasta')
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    if request.GET.get('producto'):
        qs = qs.filter(Q(producto_id=request.GET['producto']) if request.GET['producto'].isdigit()
                       else Q(producto__codigo=request.GET['producto']))
    if request.GET.get('almacen'):
        qs = qs.filter(almacen_id=request.GET['almacen'])
    if request.GET.get('origen'):
        qs = qs.filter(origen=request.GET['origen'].upper())
    costos = _ver_costos(request)

    def serializar(k):
        datos = {'id': k.pk, 'fecha': k.fecha, 'producto_id': k.producto_id, 'codigo': k.producto.codigo,
                 'producto': k.producto.nombre, 'almacen': k.almacen.codigo if k.almacen_id else None,
                 'tipo': k.tipo, 'cantidad': k.cantidad, 'saldo': k.saldo, 'origen': k.origen, 'concepto': k.concepto,
                 'codigo_sunat': k.codigo_sunat, 'referencia': k.referencia,
                 'lote': k.lote.codigo if k.lote_id else None}
        if costos:
            datos.update(costo_unitario=k.costo_unitario, costo_promedio=k.costo_promedio,
                         valor=(k.cantidad * (k.costo_unitario or D0)).quantize(Decimal('0.01')))
        return datos
    return respuesta(_pagina(request, qs.order_by('fecha', 'id'), serializar))


def stock_al_corte(request, corte):
    """Stock y valor por producto a una fecha, según el kardex."""
    from .inventario import valor_inventario
    from .models import Almacen
    almacen = Almacen.objects.filter(pk=request.GET.get('almacen')).first() if request.GET.get('almacen') else None
    filas, total = valor_inventario(corte, almacen)
    costos = _ver_costos(request)

    def serializar(f):
        datos = {'producto_id': f['p'].pk, 'codigo': f['p'].codigo, 'producto': f['p'].nombre,
                 'unidad': f['p'].unidad, 'stock': f['cantidad']}
        if costos:
            datos.update(costo_promedio=f['costo'], valor=f['valor'])
        return datos
    if request.GET.get('producto'):
        filas = [f for f in filas if str(f['p'].pk) == request.GET['producto'] or f['p'].codigo == request.GET['producto']]
    datos = _pagina(request, filas, serializar)
    if isinstance(datos, dict):
        datos.update(fecha=corte, almacen=almacen.codigo if almacen else None)
        if costos:
            datos['valor_total'] = total
    return respuesta(datos)


# ---------------------------------------------------------------- compras y tesorería
@api('compras')
def cuentas_por_pagar(request):
    from compras.models import Compra
    qs = _filtrar_comprobantes(request, Compra.objects.con_saldos().filter(estado='REGISTRADO').exclude(
        tipo_comprobante='07'))
    return respuesta(_pagina(request, [c for c in qs if c.saldo > 0], s_comprobante))


@api('finanzas')
def movimientos_tesoreria(request):
    from finanzas.models import Movimiento
    qs = Movimiento.objects.select_related('cuenta', 'tercero', 'venta', 'compra')
    desde, hasta = fecha_param(request, 'desde'), fecha_param(request, 'hasta')
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    for campo, filtro in (('cuenta', 'cuenta_id'), ('tipo', 'tipo'), ('concepto', 'concepto'), ('estado', 'estado')):
        if request.GET.get(campo):
            qs = qs.filter(**{filtro: request.GET[campo].upper() if campo != 'cuenta' else request.GET[campo]})
    return respuesta(_pagina(request, qs.order_by('fecha', 'id'), lambda m: {
        'id': m.pk, 'voucher': m.voucher, 'fecha': m.fecha, 'cuenta_id': m.cuenta_id, 'cuenta': str(m.cuenta),
        'moneda': m.cuenta.moneda, 'tipo': m.tipo, 'concepto': m.concepto, 'medio_pago': m.medio_pago,
        'numero_operacion': m.numero_operacion, 'monto': m.monto,
        'tercero': m.tercero.numero_doc if m.tercero_id else None,
        'tercero_nombre': m.tercero.nombre if m.tercero_id else None,
        'documento': (m.venta.numero_completo if m.venta_id else m.compra.numero_completo if m.compra_id else None),
        'glosa': m.glosa, 'conciliado': m.conciliado, 'estado': m.estado}))


# ---------------------------------------------------------------- producción
@api('manufactura')
def ordenes_produccion(request):
    from produccion.models import OrdenProduccion
    qs = OrdenProduccion.objects.select_related('producto', 'almacen_insumos', 'almacen_destino')
    campo = 'fecha_fin' if request.GET.get('por') == 'termino' else 'fecha'
    desde, hasta = fecha_param(request, 'desde'), fecha_param(request, 'hasta')
    if desde:
        qs = qs.filter(**{f'{campo}__gte': desde})
    if hasta:
        qs = qs.filter(**{f'{campo}__lte': hasta})
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'].upper())
    if request.GET.get('producto'):
        qs = qs.filter(producto__codigo=request.GET['producto'])
    if request.GET.get('historicas') in ('true', 'false'):
        qs = qs.filter(es_historica=request.GET['historicas'] == 'true')
    costos = _ver_costos(request)

    def serializar(o):
        datos = {'id': o.pk, 'numero': o.numero, 'producto_id': o.producto_id, 'codigo': o.producto.codigo,
                 'producto': o.producto.nombre, 'cantidad': o.cantidad, 'cantidad_producida': o.cantidad_producida,
                 'estado': o.estado, 'fecha': o.fecha, 'fecha_inicio': o.fecha_inicio, 'fecha_fin': o.fecha_fin,
                 'terminado_en': o.terminado_en, 'almacen_insumos': o.almacen_insumos.codigo,
                 'almacen_destino': o.almacen_destino.codigo, 'del_sistema_anterior': o.es_historica}
        if costos:
            datos.update(costo_materiales=o.costo_materiales, costo_mano_obra=o.costo_mano_obra,
                         costo_maquina=o.costo_maquina, costo_cif=o.costo_cif,
                         costo_subproductos=o.costo_subproductos, costo_total=o.costo_total,
                         costo_unitario=o.costo_unitario, costo_real_final=o.costo_real_final)
        return datos
    return respuesta(_pagina(request, qs.order_by(campo, 'id'), serializar))
