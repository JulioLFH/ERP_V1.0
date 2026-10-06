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
}


_PRODUCTOS = {'inventario', 'ventas', 'compras', 'manufactura', 'logistica', 'requerimientos', 'costos'}
_TERCEROS = {'ventas', 'compras', 'contactos', 'finanzas', 'contabilidad', 'logistica'}
MODULOS = {  # quién puede pedir cada lista (además del administrador)
    'productos': _PRODUCTOS, 'productos_venta': _PRODUCTOS, 'productos_bienes': _PRODUCTOS,
    'terceros': _TERCEROS, 'clientes': _TERCEROS, 'proveedores': _TERCEROS,
    'cuentas': {'contabilidad', 'compras', 'finanzas', 'planillas', 'activos', 'inventario', 'requerimientos'},
    'ventas': {'ventas', 'logistica', 'finanzas'}, 'compras': {'compras', 'finanzas'},
}


@login_required
def opciones(request, fuente):
    if fuente not in FUENTES:
        raise Http404
    from .modulos import modulos_del_usuario
    if not request.user.is_superuser and not (MODULOS[fuente] & modulos_del_usuario(request.user)):
        raise Http404
    resp = JsonResponse(FUENTES[fuente](), safe=False, json_dumps_params={'ensure_ascii': False,
                                                                         'separators': (',', ':')})
    resp['Cache-Control'] = 'private, max-age=120'
    return resp
