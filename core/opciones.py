"""Listas de opciones para los selectores livianos (SelectRemoto): [[valor, texto], ...]."""
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse

from .models import Producto, Tercero


def _productos(**filtro):
    return [[pk, f'{c} - {n}'] for pk, c, n in Producto.objects.filter(activo=True, es_plantilla=False, **filtro)
            .order_by('nombre').values_list('pk', 'codigo', 'nombre')]


def _terceros(*tipos):
    qs = Tercero.objects.filter(activo=True)
    if tipos:
        qs = qs.filter(tipo__in=tipos)
    return [[pk, f'{d} - {n}'] for pk, d, n in qs.order_by('nombre').values_list('pk', 'numero_doc', 'nombre')]


def _cuentas():
    from contabilidad.models import CuentaContable
    return [[pk, f'{c} {n}'] for pk, c, n in CuentaContable.objects.filter(imputable=True, activo=True)
            .order_by('codigo').values_list('pk', 'codigo', 'nombre')]


def _comprobantes(modelo):
    qs = (modelo.objects.filter(estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08'])
          .order_by('-fecha_emision', '-id').values_list('pk', 'serie', 'numero', 'tercero__nombre', 'fecha_emision'))
    return [[pk, f'{s}-{n} · {t[:40]} · {f:%d/%m/%Y}'] for pk, s, n, t, f in qs]


def _ventas():
    from ventas.models import Venta
    return _comprobantes(Venta)


def _compras():
    from compras.models import Compra
    return _comprobantes(Compra)


def _facturas_comerciales():
    """Facturas comerciales para importaciones (01, 00 y 91 de no domiciliado), con su tipo (IMP-01)."""
    from compras.models import Compra
    qs = (Compra.objects.filter(estado='REGISTRADO', tipo_comprobante__in=['01', '00', '91'])
          .order_by('-fecha_emision', '-id')
          .values_list('pk', 'tipo_comprobante', 'serie', 'numero', 'tercero__nombre', 'fecha_emision'))
    nombres = {'01': 'Factura', '00': 'Otros', '91': 'No domiciliado'}
    return [[pk, f'{nombres[t]} {s}-{n} · {nom[:40]} · {f:%d/%m/%Y}'] for pk, t, s, n, nom, f in qs]


def _ordenes_produccion():
    """Órdenes de producción para las desviaciones (pueden ser decenas de miles: la página no las trae)."""
    from produccion.models import OrdenProduccion
    return [[pk, f'{n or "(borrador)"} · {c} {p[:50]} · {f:%d/%m/%Y}'] for pk, n, c, p, f in
            OrdenProduccion.objects.order_by('-id').values_list('pk', 'numero', 'producto__codigo', 'producto__nombre',
                                                                'fecha')]


FUENTES = {
    'ordenes_produccion': _ordenes_produccion,
    'productos': lambda: _productos(),
    'productos_venta': lambda: _productos(puede_venderse=True),
    'productos_bienes': lambda: _productos(tipo='BIEN'),
    'terceros': lambda: _terceros(),
    'clientes': lambda: _terceros('CLIENTE', 'AMBOS'),
    'proveedores': lambda: _terceros('PROVEEDOR', 'AMBOS'),
    'cuentas': _cuentas,
    'ventas': _ventas,
    'compras': _compras,
    'facturas_comerciales': _facturas_comerciales,
}


_PRODUCTOS = {'inventario', 'ventas', 'compras', 'manufactura', 'logistica', 'requerimientos', 'costos',
              'planificacion', 'control_calidad', 'mantenimiento', 'toma_pedido', 'codigo_barras'}
_TERCEROS = {'crm', 'ventas', 'compras', 'contactos', 'tesoreria', 'finanzas', 'contabilidad', 'logistica', 'rendiciones',
             'manufactura', 'requerimientos',  # proveedor sugerido de las solicitudes de pedido
             'toma_pedido', 'proyectos', 'distribucion'}
MODULOS = {  # quién puede pedir cada lista (además del administrador)
    'productos': _PRODUCTOS, 'productos_venta': _PRODUCTOS, 'productos_bienes': _PRODUCTOS,
    'terceros': _TERCEROS, 'clientes': _TERCEROS, 'proveedores': _TERCEROS,
    'cuentas': {'contabilidad', 'compras', 'tesoreria', 'finanzas', 'planillas', 'activos', 'inventario',
                'requerimientos', 'rendiciones'},
    'ventas': {'ventas', 'logistica', 'tesoreria', 'finanzas'}, 'compras': {'compras', 'tesoreria', 'finanzas'},
    'facturas_comerciales': {'compras'},
    'ordenes_produccion': {'manufactura', 'control_calidad', 'calidad', 'planificacion', 'inventario'},
}


CAMPOS_PRODUCTO = ('id', 'nombre', 'unidad', 'precio_venta', 'costo_promedio', 'precio_compra', 'afectacion_igv',
                   'unidad_compra', 'factor_compra')


def datos_productos(user, ids=None):
    """Datos que el detalle de ítems usa al elegir un producto (precio, unidad, IGV). El costo solo viaja a quien
    puede verlo."""
    from .modulos import puede_ver_costos
    qs = Producto.objects.filter(activo=True)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    productos = list(qs.values(*CAMPOS_PRODUCTO))
    costos = puede_ver_costos(user)
    for p in productos:
        # compras: precio de compra referencial o, si no hay (y puede ver costos), el costo promedio; ambos son por
        # unidad de medida (kg) y la orden va en unidad de compra (bolsa de 50 kg): se multiplican por el factor (AUD-01)
        factor = p['factor_compra'] if p['factor_compra'] and p['factor_compra'] > 0 else 1
        p['factor_compra'] = factor
        p['unidad_compra'] = p['unidad_compra'] or p['unidad']
        p['precio_compra'] = (p['precio_compra'] or (p['costo_promedio'] or 0 if costos else 0)) * factor
        if not costos:
            p.pop('costo_promedio')
    return productos


# ---------------------------------------------------------------- PLAT-12: sugerencias de los buscadores de listas
def _q(campos, q):
    from django.db.models import Q
    filtro = Q()
    for c in campos:
        filtro |= Q(**{f'{c}__icontains': q})
    return filtro


def _modelo(label):
    from django.apps import apps
    return apps.get_model(label)


def _s_productos(q):
    # el nombre: lo buscan todas las listas (por código solo algunas)
    return [(n, f'{c} · {n}') for c, n in Producto.objects.filter(_q(['codigo', 'nombre'], q), activo=True)
            .order_by('codigo').values_list('codigo', 'nombre')[:10]]


def _s_terceros(q):
    return [(n, f'{n} · {d}') for d, n in Tercero.objects.filter(_q(['numero_doc', 'nombre'], q))
            .order_by('nombre').values_list('numero_doc', 'nombre')[:10]]


def _s_docs(label, numero='numero', con_serie=False):
    def buscar(q):
        m = _modelo(label)
        campos = ['numero', 'tercero__nombre'] + (['serie'] if con_serie else [])
        filas = m.objects.filter(_q(campos, q)).order_by('-id').values_list(
            *(['serie'] if con_serie else []), numero, 'tercero__nombre')[:10]
        salida = []
        for f in filas:
            *doc, nombre = f
            texto = '-'.join(str(x) for x in doc)
            salida.append((doc[-1], f'{texto} · {nombre or ""}'))
        return salida
    return buscar


def _s_simple(label, campos, valor, *mostrar, orden='-id'):
    def buscar(q):
        m = _modelo(label)
        return [(f[0], ' · '.join(str(x) for x in f if x)) for f in
                m.objects.filter(_q(campos, q)).order_by(orden).values_list(valor, *mostrar)[:10]]
    return buscar


def _s_trabajadores(q):
    T = _modelo('planillas.Trabajador')
    return [(d, f'{a} {n} · {d}') for d, a, n in T.objects.filter(_q(['numero_doc', 'apellido_paterno', 'nombres'], q))
            .order_by('apellido_paterno').values_list('numero_doc', 'apellido_paterno', 'nombres')[:10]]


SUGERIR = {  # módulo de la pantalla -> fuentes (en orden); la pantalla «productos» y «terceros» van por su nombre
    'inventario': [_s_productos, _s_docs('inventario.Operacion')],
    'codigo_barras': [_s_productos], 'toma_pedido': [_s_productos, _s_terceros],
    'compras': [_s_docs('compras.Compra', con_serie=True), _s_docs('compras.OrdenCompra'), _s_terceros, _s_productos],
    'portal_proveedores': [_s_docs('compras.Compra', con_serie=True), _s_terceros],
    'requerimientos': [_s_simple('compras.SolicitudPedido', ['numero', 'motivo'], 'numero', 'motivo'), _s_productos],
    'ventas': [_s_docs('ventas.Venta', con_serie=True), _s_docs('ventas.Cotizacion'), _s_terceros, _s_productos],
    'crm': [_s_terceros, _s_simple('ventas.Oportunidad', ['nombre', 'tercero__nombre'], 'nombre', 'tercero__nombre')],
    'contactos': [_s_terceros], 'tesoreria': [_s_simple('finanzas.Movimiento', ['voucher', 'tercero__nombre', 'glosa'],
                                                         'voucher', 'tercero__nombre'), _s_terceros],
    'finanzas': [_s_terceros], 'rendiciones': [_s_simple('finanzas.Rendicion', ['numero', 'descripcion'], 'numero',
                                                         'descripcion'), _s_trabajadores],
    'contabilidad': [_s_simple('contabilidad.Asiento', ['numero', 'glosa'], 'numero', 'glosa'),
                     _s_simple('contabilidad.CuentaContable', ['codigo', 'nombre'], 'codigo', 'nombre',
                               orden='codigo')],
    'logistica': [_s_simple('logistica.GuiaRemision', ['numero', 'serie', 'destinatario__nombre'], 'numero', 'serie',
                            'destinatario__nombre'), _s_terceros],
    'distribucion': [_s_terceros],
    'obras': [_s_simple('obras.Obra', ['codigo', 'nombre'], 'codigo', 'nombre', orden='codigo'),
              _s_simple('obras.Recurso', ['codigo', 'nombre'], 'codigo', 'nombre', orden='codigo')],
    'activos': [_s_simple('activos.ActivoFijo', ['codigo', 'nombre', 'serie'], 'codigo', 'nombre', orden='codigo')],
    'planillas': [_s_trabajadores], 'empleados': [_s_trabajadores], 'tiempo_personal': [_s_trabajadores],
    'sst': [_s_trabajadores], 'calidad': [_s_simple('calidad.NoConformidad', ['codigo', 'descripcion'], 'codigo',
                                                    'descripcion')],
    'manufactura': [_s_productos], 'planificacion': [_s_productos], 'control_calidad': [_s_productos],
    'mantenimiento': [_s_simple('produccion.Equipo', ['codigo', 'nombre'], 'codigo', 'nombre', orden='codigo')],
    'flota': [_s_simple('produccion.Equipo', ['codigo', 'nombre', 'placa'], 'codigo', 'nombre', 'placa',
                        orden='codigo')],
    'costos': [_s_productos],
}
POR_NOMBRE = {'productos': [_s_productos], 'terceros': [_s_terceros]}


@login_required
def sugerencias(request):
    """Hasta 10 coincidencias para el buscador de la lista en la que está el usuario (pantalla = su dirección)."""
    from django.urls import Resolver404, resolve

    from .modulos import modulos_de_ruta, modulos_del_usuario
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)
    try:
        match = resolve(request.GET.get('ruta') or '/')
    except Resolver404:
        return JsonResponse([], safe=False)
    modulos = modulos_de_ruta(match) or []
    fuentes = POR_NOMBRE.get(match.url_name) if not match.namespace else None
    if fuentes is None:
        propios = [m for m in modulos if request.user.is_superuser or m in modulos_del_usuario(request.user)]
        if modulos and not propios:
            return JsonResponse([], safe=False)
        fuentes = next((SUGERIR[m] for m in (propios or modulos) if m in SUGERIR), [])
    salida, vistos = [], set()
    for fuente in fuentes:
        try:
            filas = fuente(q)
        except Exception:  # fuente de una app no instalada o un campo que no existe en esta versión
            continue
        for valor, texto in filas:
            if valor and texto not in vistos:
                vistos.add(texto)
                salida.append({'valor': str(valor), 'texto': texto})
        if len(salida) >= 10:
            break
    return JsonResponse(salida[:10], safe=False)


@login_required
def tercero_credito(request, pk):
    """Días de crédito del cliente o proveedor: el formulario propone el vencimiento = emisión + días (PLAT-14)."""
    from .modulos import modulos_del_usuario
    if not request.user.is_superuser and not (_TERCEROS & modulos_del_usuario(request.user)):
        raise Http404
    t = Tercero.objects.filter(pk=pk).values('dias_credito').first()
    if t is None:
        raise Http404
    return JsonResponse({'dias': t['dias_credito'] or 0})


@login_required
def producto_datos(request, pk):
    """Un producto para el detalle de ítems (el formulario ya no descarga todo el catálogo)."""
    from .modulos import modulos_del_usuario
    if not request.user.is_superuser and not (_PRODUCTOS & modulos_del_usuario(request.user)):
        raise Http404
    datos = datos_productos(request.user, [pk])
    if not datos:
        raise Http404
    return JsonResponse(datos[0], json_dumps_params={'ensure_ascii': False})


@login_required
def opciones(request, fuente):
    if fuente not in FUENTES:
        raise Http404
    from .modulos import modulos_del_usuario
    if not request.user.is_superuser and not (MODULOS[fuente] & modulos_del_usuario(request.user)):
        raise Http404
    resp = JsonResponse(FUENTES[fuente](), safe=False, json_dumps_params={'ensure_ascii': False,
                                                                         'separators': (',', ':')})
    # las listas de comprobantes cambian al registrar uno: sin caché (una factura recién registrada debe aparecer)
    resp['Cache-Control'] = ('no-store' if fuente in ('compras', 'ventas', 'facturas_comerciales', 'ordenes_produccion')
                             else 'private, max-age=120')
    return resp
