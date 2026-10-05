"""API REST v1 (JSON) para integrar tiendas online, BI y otros sistemas.

Autenticación: cabecera `Authorization: Bearer <clave>` con una clave creada en Mi cuenta › Claves de API. La clave
actúa con los módulos y permisos de su usuario; las claves de solo lectura no pueden registrar. Límite: 120
solicitudes por minuto por clave. Importes como texto decimal para no perder precisión.
"""
import json
from functools import wraps

from django.core.cache import cache
from django.core.paginator import EmptyPage, Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import ApiToken, Producto, StockAlmacen, Tercero

LIMITE_MINUTO = 120


def respuesta(datos, status=200):
    return JsonResponse(datos, status=status, encoder=DjangoJSONEncoder, json_dumps_params={'ensure_ascii': False},
                        safe=not isinstance(datos, list))


def error(mensaje, status=400, **extra):
    return respuesta({'error': mensaje, **extra}, status)


def api(modulo=None, metodos=('GET',), escribir=None):
    """Autentica la clave, exige el módulo y, para POST, una clave de escritura y el permiso `escribir`."""
    def deco(vista):
        @csrf_exempt
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            from . import auditoria
            from .modulos import modulos_del_usuario
            from .permisos import puede
            if request.method not in metodos:
                return error(f'Método no permitido. Use: {", ".join(metodos)}.', 405)
            cabecera = request.META.get('HTTP_AUTHORIZATION', '')
            token = ApiToken.autenticar(cabecera[7:].strip()) if cabecera.startswith('Bearer ') else None
            if token is None:
                return error('Clave de API ausente, inválida, revocada o vencida. Envíe Authorization: Bearer '
                             '<clave>.', 401)
            clave_limite = f'api-limite-{token.pk}-{timezone.now():%Y%m%d%H%M}'
            usos = cache.get_or_set(clave_limite, 0, 70)
            if usos >= LIMITE_MINUTO:
                return error(f'Límite de {LIMITE_MINUTO} solicitudes por minuto superado.', 429)
            cache.set(clave_limite, usos + 1, 70)
            usuario = token.usuario
            request.user = usuario
            auditoria._local.usuario = usuario
            if modulo and not usuario.is_superuser and modulo not in modulos_del_usuario(usuario):
                return error(f'El usuario de la clave no tiene acceso al módulo {modulo}.', 403)
            if request.method != 'GET':
                if token.solo_lectura:
                    return error('La clave es de solo lectura.', 403)
                if escribir and not puede(usuario, escribir):
                    return error(f'El usuario de la clave no tiene el permiso {escribir}.', 403)
            if not token.ultimo_uso or (timezone.now() - token.ultimo_uso).total_seconds() > 60:
                ApiToken.objects.filter(pk=token.pk).update(ultimo_uso=timezone.now())
            return vista(request, *args, **kwargs)
        return envoltura
    return deco


def _cuerpo(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return None


def _pagina(request, qs, serializar):
    try:
        tam = min(max(int(request.GET.get('page_size') or 50), 1), 200)
        num = max(int(request.GET.get('page') or 1), 1)
    except ValueError:
        tam, num = 50, 1
    paginador = Paginator(qs, tam)
    try:
        pagina = paginador.page(num)
    except EmptyPage:
        return {'count': paginador.count, 'page': num, 'pages': paginador.num_pages, 'results': []}
    return {'count': paginador.count, 'page': num, 'pages': paginador.num_pages,
            'results': [serializar(o) for o in pagina]}


def _errores(*formularios):
    errores = {}
    for f in formularios:
        if hasattr(f, 'forms'):  # formset de ítems
            for i, form in enumerate(f.forms):
                for campo, lista in form.errors.items():
                    errores[f'items[{i}].{campo}'] = lista
            if f.non_form_errors():
                errores['items'] = list(f.non_form_errors())
        else:
            for campo, lista in f.errors.items():
                errores[campo if campo != '__all__' else 'general'] = lista
    return errores


# ---------------------------------------------------------------- serializadores
def s_producto(p):
    return {'id': p.pk, 'codigo': p.codigo, 'nombre': p.nombre, 'clase': p.clase, 'unidad': p.unidad,
            'marca': p.marca, 'codigo_barras': p.codigo_barras, 'precio_venta': p.precio_venta,
            'afectacion_igv': p.afectacion_igv, 'stock': p.stock, 'activo': p.activo,
            'es_plantilla': p.es_plantilla, 'plantilla_id': p.plantilla_id, 'atributos': p.atributos,
            'imagen': f'/api/v1/productos/{p.pk}/imagen/' if p.imagen_id else None}


def s_tercero(t):
    return {'id': t.pk, 'tipo': t.tipo, 'tipo_doc': t.tipo_doc, 'numero_doc': t.numero_doc, 'nombre': t.nombre,
            'direccion': t.direccion, 'email': t.email, 'telefono': t.telefono, 'dias_credito': t.dias_credito,
            'activo': t.activo}


def _s_items(doc):
    return [{'producto_id': i.producto_id, 'codigo': i.producto.codigo if i.producto else None,
             'descripcion': i.descripcion, 'cantidad': i.cantidad, 'precio_unitario': i.precio_unitario,
             'descuento_pct': i.descuento_pct, 'subtotal': i.subtotal, 'afectacion': i.afectacion_en(doc)}
            for i in doc.items.select_related('producto')]


def s_comprobante(d, detalle=False):
    datos = {'id': d.pk, 'tipo_comprobante': d.tipo_comprobante, 'numero': d.numero_completo,
             'fecha_emision': d.fecha_emision, 'fecha_vencimiento': d.fecha_vencimiento,
             'tercero': {'id': d.tercero_id, 'numero_doc': d.tercero.numero_doc, 'nombre': d.tercero.nombre},
             'moneda': d.moneda, 'tipo_cambio': d.tipo_cambio, 'base_imponible': d.base_imponible, 'igv': d.igv,
             'total': d.total, 'estado': d.estado, 'forma_pago': d.forma_pago}
    if hasattr(d, 'estado_sunat'):
        datos['estado_sunat'] = d.estado_sunat
    if hasattr(d, 'saldo'):
        datos['saldo'] = d.saldo
    if detalle:
        datos['items'] = _s_items(d)
    return datos


# ---------------------------------------------------------------- endpoints
ENDPOINTS = [
    ('GET', '/api/v1/', 'Índice y usuario de la clave'),
    ('GET', '/api/v1/productos/', 'Productos (q, clase, activo, page, page_size)'),
    ('GET', '/api/v1/productos/{id}/', 'Producto con su stock por almacén y sus variantes'),
    ('GET', '/api/v1/productos/{id}/imagen/', 'Imagen del producto (PNG, JPG, GIF o WEBP)'),
    ('GET', '/api/v1/stock/', 'Stock por producto y almacén (producto, almacen)'),
    ('GET', '/api/v1/terceros/', 'Clientes y proveedores (q, tipo)'),
    ('POST', '/api/v1/terceros/', 'Crear cliente o proveedor'),
    ('GET', '/api/v1/ventas/', 'Comprobantes de venta (desde, hasta, tipo, estado, tercero)'),
    ('GET', '/api/v1/ventas/{id}/', 'Comprobante de venta con ítems'),
    ('POST', '/api/v1/ventas/', 'Emitir comprobante de venta'),
    ('GET', '/api/v1/cuentas-por-cobrar/', 'Comprobantes de venta con saldo pendiente'),
    ('GET', '/api/v1/compras/', 'Comprobantes de compra (desde, hasta, tipo, estado, tercero)'),
    ('GET', '/api/v1/compras/{id}/', 'Comprobante de compra con ítems'),
]


@api()
def indice(request):
    from .modulos import modulos_del_usuario
    return respuesta({'version': 'v1', 'usuario': request.user.username,
                      'modulos': sorted(modulos_del_usuario(request.user)),
                      'endpoints': [{'metodo': m, 'ruta': r, 'descripcion': d} for m, r, d in ENDPOINTS],
                      'documentacion': request.build_absolute_uri('/api/v1/docs/')})


@api('inventario')
def productos(request):
    qs = Producto.objects.all().order_by('codigo')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(codigo__icontains=q) | Q(codigo_barras=q))
    if request.GET.get('clase'):
        qs = qs.filter(clase=request.GET['clase'])
    if request.GET.get('activo') in ('true', 'false'):
        qs = qs.filter(activo=request.GET['activo'] == 'true')
    return respuesta(_pagina(request, qs, s_producto))


@api('inventario')
def producto(request, pk):
    p = Producto.objects.filter(pk=pk).first()
    if p is None:
        return error('Producto no encontrado.', 404)
    datos = s_producto(p)
    datos['stock_por_almacen'] = [{'almacen_id': s.almacen_id, 'almacen': s.almacen.codigo, 'stock': s.cantidad}
                                  for s in StockAlmacen.objects.filter(producto=p).select_related('almacen')]
    if p.es_plantilla:
        datos['variantes'] = [s_producto(v) for v in p.variantes.order_by('codigo')]
    return respuesta(datos)


@api('inventario')
def producto_imagen(request, pk):
    from django.http import HttpResponse
    p = Producto.objects.select_related('imagen').filter(pk=pk).first()
    if p is None or p.imagen is None:
        return error('El producto no tiene imagen.', 404)
    resp = HttpResponse(bytes(p.imagen.datos), content_type=p.imagen.tipo or 'application/octet-stream')
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


@api('inventario')
def stock(request):
    qs = StockAlmacen.objects.select_related('producto', 'almacen').order_by('producto__codigo', 'almacen_id')
    if request.GET.get('producto'):
        qs = qs.filter(producto_id=request.GET['producto'])
    if request.GET.get('almacen'):
        qs = qs.filter(almacen_id=request.GET['almacen'])
    return respuesta(_pagina(request, qs, lambda s: {
        'producto_id': s.producto_id, 'codigo': s.producto.codigo, 'producto': s.producto.nombre,
        'almacen_id': s.almacen_id, 'almacen': s.almacen.codigo, 'stock': s.cantidad}))


@api('contactos', metodos=('GET', 'POST'))
def terceros(request):
    if request.method == 'POST':
        from .forms import TerceroForm
        datos = _cuerpo(request)
        if not isinstance(datos, dict):
            return error('El cuerpo debe ser un objeto JSON.')
        base = {'tipo': 'CLIENTE', 'tipo_doc': '6', 'activo': True, 'dias_credito': 0, 'limite_credito': 0}
        form = TerceroForm({**base, **datos})
        if not form.is_valid():
            return error('Datos inválidos.', 422, campos=_errores(form))
        return respuesta(s_tercero(form.save()), 201)
    qs = Tercero.objects.all().order_by('nombre')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(numero_doc__startswith=q))
    if request.GET.get('tipo') in ('CLIENTE', 'PROVEEDOR'):
        qs = qs.filter(tipo__in=[request.GET['tipo'], 'AMBOS'])
    return respuesta(_pagina(request, qs, s_tercero))


def _filtrar_comprobantes(request, qs):
    for campo, filtro in (('desde', 'fecha_emision__gte'), ('hasta', 'fecha_emision__lte'),
                          ('tipo', 'tipo_comprobante'), ('estado', 'estado'), ('tercero', 'tercero_id')):
        if request.GET.get(campo):
            qs = qs.filter(**{filtro: request.GET[campo]})
    return qs.select_related('tercero').order_by('-fecha_emision', '-id')


def _venta_desde_json(datos):
    """Convierte el JSON de la API en los datos del formulario de venta (mismas validaciones que la pantalla)."""
    from datetime import date
    items = datos.get('items') or []
    form = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'fecha_emision': date.today().isoformat(),
            'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
            'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0',
            'detraccion_codigo': '', 'descontar_stock': 'on', 'glosa': ''}
    for k, v in datos.items():
        if k == 'items':
            continue
        if k == 'descontar_stock':
            if v:
                form[k] = 'on'
            else:
                form.pop(k, None)
        else:
            form[k] = '' if v is None else str(v)
    form.update({'items-TOTAL_FORMS': str(len(items)), 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
                 'items-MAX_NUM_FORMS': '1000'})
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        p = None
        if it.get('producto_id'):
            p = Producto.objects.filter(pk=it['producto_id']).first()
        elif it.get('codigo'):
            p = Producto.objects.filter(codigo=it['codigo']).first()
        precio = it.get('precio_unitario')
        if precio is None and p is not None:
            precio = p.precio_venta
        form.update({f'items-{i}-producto': p.pk if p else '',
                     f'items-{i}-descripcion': it.get('descripcion') or (p.nombre if p else ''),
                     f'items-{i}-cantidad': str(it.get('cantidad', '')),
                     f'items-{i}-precio_unitario': '' if precio is None else str(precio),
                     f'items-{i}-descuento_pct': str(it.get('descuento_pct') or 0),
                     f'items-{i}-afectacion': it.get('afectacion') or ''})
    return form


@api('ventas', metodos=('GET', 'POST'), escribir='ventas.emitir')
def ventas(request):
    from ventas.models import Venta, VentaItem
    if request.method == 'POST':
        from ventas.forms import VentaForm
        from ventas.views import ventas_views
        from .forms import item_formset
        from .utils import procesar_documento
        datos = _cuerpo(request)
        if not isinstance(datos, dict) or not datos.get('items'):
            return error('Envíe un objeto JSON con la cabecera y la lista "items".')
        if datos.get('tipo_comprobante') in ('07', '08'):
            return error('Las notas de crédito y débito se emiten desde el ERP.', 422)
        if 'tercero' not in datos and datos.get('tercero_numero_doc'):
            t = Tercero.objects.filter(numero_doc=datos.pop('tercero_numero_doc')).first()
            if t is None:
                return error('Cliente no encontrado: créelo antes con POST /api/v1/terceros/.', 422)
            datos['tercero'] = t.pk
        doc, form, formset = procesar_documento(_venta_desde_json(datos), VentaForm, item_formset(Venta, VentaItem),
                                                Venta(), ventas_views.al_guardar, ventas_views.validar_stock)
        if doc is None:
            return error('Datos inválidos.', 422, campos=_errores(form, formset))
        return respuesta(s_comprobante(doc, detalle=True), 201)
    return respuesta(_pagina(request, _filtrar_comprobantes(request, Venta.objects.all()), s_comprobante))


@api('ventas')
def venta(request, pk):
    from ventas.models import Venta
    v = Venta.objects.select_related('tercero').filter(pk=pk).first()
    return respuesta(s_comprobante(v, detalle=True)) if v else error('Comprobante no encontrado.', 404)


@api('ventas')
def cuentas_por_cobrar(request):
    from ventas.models import Venta
    qs = _filtrar_comprobantes(request, Venta.objects.con_saldos().filter(estado='REGISTRADO').exclude(
        tipo_comprobante__in=['07', '08']))
    pendientes = [v for v in qs if v.saldo > 0]
    return respuesta(_pagina(request, pendientes, s_comprobante))


@api('compras')
def compras(request):
    from compras.models import Compra
    return respuesta(_pagina(request, _filtrar_comprobantes(request, Compra.objects.all()), s_comprobante))


@api('compras')
def compra(request, pk):
    from compras.models import Compra
    c = Compra.objects.select_related('tercero').filter(pk=pk).first()
    return respuesta(s_comprobante(c, detalle=True)) if c else error('Comprobante no encontrado.', 404)


def openapi(request):
    """Descripción OpenAPI 3 (para Postman, Swagger UI o generadores de clientes)."""
    rutas = {}
    for metodo, ruta, desc in ENDPOINTS:
        params = [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}] if '{id}' in ruta \
            else []
        rutas.setdefault(ruta, {})[metodo.lower()] = {
            'summary': desc, 'parameters': params,
            'responses': {'200': {'description': 'OK'}, '401': {'description': 'Clave inválida'},
                          '403': {'description': 'Sin permiso'}, '422': {'description': 'Datos inválidos'}}}
    return respuesta({'openapi': '3.0.3', 'info': {'title': 'Ceiba ERP API', 'version': '1'},
                      'components': {'securitySchemes': {'bearer': {'type': 'http', 'scheme': 'bearer'}}},
                      'security': [{'bearer': []}], 'paths': rutas})
