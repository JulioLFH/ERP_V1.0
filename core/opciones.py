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


FUENTES = {
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
}


CAMPOS_PRODUCTO = ('id', 'nombre', 'unidad', 'precio_venta', 'costo_promedio', 'precio_compra', 'afectacion_igv')


def datos_productos(user, ids=None):
    """Datos que el detalle de ítems usa al elegir un producto (precio, unidad, IGV). El costo solo viaja a quien
    puede verlo."""
    from .modulos import puede_ver_costos
    qs = Producto.objects.filter(activo=True)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    productos = list(qs.values(*CAMPOS_PRODUCTO))
    costos = puede_ver_costos(user)
    for p in productos:  # compras: precio de compra referencial o, si no hay (y puede ver costos), el costo promedio
        p['precio_compra'] = p['precio_compra'] or (p['costo_promedio'] if costos else 0)
        if not costos:
            p.pop('costo_promedio')
    return productos


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
    resp['Cache-Control'] = ('no-store' if fuente in ('compras', 'ventas', 'facturas_comerciales')
                             else 'private, max-age=120')
    return resp
