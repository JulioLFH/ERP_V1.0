"""Buscador general (como el de Odoo): una palabra busca en todos los módulos a la vez: contactos y productos, y los
documentos que los mencionan (ventas, pedidos, compras, órdenes, almacén, tesorería, contabilidad, CRM, obras,
producción, guías, activos, personal…). Así, el nombre de un cliente trae al cliente y sus comprobantes, pedidos,
cobros y oportunidades; un número de documento trae el documento.

Cada fuente solo aparece si el usuario puede abrir su pantalla (módulo, acción de la ruta y pantalla no oculta en su
perfil) y respeta los filtros por dato de su perfil (vendedor, centros de costo, cuentas de caja y bancos y la
cartera del CRM). No muestra importes de costo ni sueldos."""
from django.db.models import Q
from django.http import HttpRequest, QueryDict
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

LIMITE = 6
MINIMO = 2


def _q(campos, texto):
    filtro = Q()
    for campo in campos:
        filtro |= Q(**{f'{campo}__icontains': texto})
    return filtro


def _numero(texto):
    """«F001-123» busca serie F001 y número 123; «123» solo el número."""
    serie, _, numero = texto.partition('-')
    return (serie.strip(), numero.strip().lstrip('0') or numero.strip()) if numero else ('', texto)


def puede_abrir(user, url):
    """¿El usuario abre esta pantalla? Las mismas reglas que el middleware: módulo, acción de la ruta (GET) y la
    pantalla no oculta ni de sueldos o saldos que no ve."""
    if user.is_superuser:
        return True
    try:
        match = resolve(url.partition('?')[0])
    except Resolver404:
        return False
    from .modulos import modulos_de_ruta, modulos_del_usuario
    from .permisos import accion_de_ruta, puede
    from .politicas import restriccion_de_pantalla
    permitidos = modulos_de_ruta(match)
    if permitidos is not None and not set(permitidos) & modulos_del_usuario(user):
        return False
    request = HttpRequest()
    request.method, request.path, request.user, request.resolver_match = 'GET', url.partition('?')[0], user, match
    request.GET = QueryDict(url.partition('?')[2])
    try:
        accion = accion_de_ruta(match, request)
    except Exception:  # una regla que necesita datos de un POST: la pantalla se decide al abrirla
        accion = None
    if accion and not puede(user, accion):
        return False
    return restriccion_de_pantalla(request, match) is None


# ---------------------------------------------------------------- fuentes
def _contactos(user, t):
    from .models import Tercero
    for x in Tercero.objects.filter(_q(('nombre', 'numero_doc', 'email', 'telefono'), t))[:LIMITE]:
        yield (x.nombre, f'{x.get_tipo_display()} · {x.get_tipo_doc_display()} {x.numero_doc}',
               reverse('tercero_editar', args=[x.pk]))


def _productos(user, t):
    from .models import Producto
    qs = Producto.objects.filter(_q(('codigo', 'nombre', 'codigo_barras', 'marca'), t), es_plantilla=False)
    for x in qs[:LIMITE]:
        yield f'{x.codigo} {x.nombre}', f'{x.get_clase_display()} · {x.unidad}', reverse('producto_editar', args=[x.pk])


def _ventas(user, t):
    from ventas.models import Venta

    from .politicas import filtrar_vendedor
    serie, numero = _numero(t)
    filtro = _q(('tercero__nombre', 'tercero__numero_doc', 'numero', 'glosa'), t)
    if serie:
        filtro |= Q(serie__iexact=serie, numero__iendswith=numero)
    for x in filtrar_vendedor(Venta.objects.filter(filtro).select_related('tercero'), user).order_by(
            '-fecha_emision', '-id')[:LIMITE]:
        yield (f'{x.get_tipo_comprobante_display()} {x.numero_completo}', f'{x.tercero.nombre} · {x.fecha_emision:%d/%m/%Y} · '
               f'{x.get_estado_display()}', reverse('ventas:detalle', args=[x.pk]))


def _pedidos(user, t):
    from ventas.models import Cotizacion

    from .politicas import filtrar_vendedor
    qs = filtrar_vendedor(Cotizacion.objects.filter(
        _q(('numero', 'tercero__nombre', 'tercero__numero_doc', 'glosa'), t)).select_related('tercero'), user)
    for x in qs.order_by('-fecha', '-id')[:LIMITE]:
        yield (f'{x.get_tipo_display()} {x.numero}', f'{x.tercero.nombre} · {x.fecha:%d/%m/%Y} · {x.get_estado_display()}',
               reverse('ventas:cot_detalle', args=[x.pk]))


def _oportunidades(user, t):
    from ventas import crm_servicios as srv
    from ventas.models import Oportunidad
    qs = srv.visibles(user, Oportunidad.objects.filter(
        _q(('nombre', 'prospecto', 'tercero__nombre', 'tercero__numero_doc', 'email', 'telefono'), t)))
    for x in qs.select_related('tercero').order_by('-id')[:LIMITE]:
        yield x.nombre, f'{x.cliente} · {srv.nombre_etapa(x.etapa)}', reverse('crm:oportunidad', args=[x.pk])


def _compras(user, t):
    from compras.models import Compra
    serie, numero = _numero(t)
    filtro = _q(('tercero__nombre', 'tercero__numero_doc', 'numero', 'glosa'), t)
    if serie:
        filtro |= Q(serie__iexact=serie, numero__iendswith=numero)
    for x in Compra.objects.filter(filtro).select_related('tercero').order_by('-fecha_emision', '-id')[:LIMITE]:
        yield (f'{x.get_tipo_comprobante_display()} {x.numero_completo}', f'{x.tercero.nombre} · '
               f'{x.fecha_emision:%d/%m/%Y} · {x.get_estado_display()}', reverse('compras:detalle', args=[x.pk]))


def _ordenes_compra(user, t):
    from compras.models import OrdenCompra
    qs = OrdenCompra.objects.filter(_q(('numero', 'tercero__nombre', 'tercero__numero_doc', 'glosa'), t))
    for x in qs.select_related('tercero').order_by('-fecha', '-id')[:LIMITE]:
        yield (f'Orden {x.numero}', f'{x.tercero.nombre} · {x.fecha:%d/%m/%Y} · {x.get_estado_display()}',
               reverse('compras:oc_detalle', args=[x.pk]))


def _solicitudes(user, t):
    from compras.models import SolicitudPedido
    qs = SolicitudPedido.objects.filter(_q(('numero', 'motivo', 'lineas__descripcion'), t)).distinct()
    for x in qs.select_related('centro_costo').order_by('-id')[:LIMITE]:
        yield (f'Solicitud {x.numero}', f'{x.motivo[:60]} · {x.get_estado_display()}',
               reverse('solicitudes:detalle', args=[x.pk]))


def _operaciones(user, t):
    from inventario.models import Operacion
    qs = Operacion.objects.filter(_q(('numero', 'referencia', 'glosa', 'tercero__nombre', 'items__lote'), t)).distinct()
    for x in qs.select_related('tipo').order_by('-fecha', '-id')[:LIMITE]:
        yield (f'{x.tipo.nombre} {x.numero}', f'{x.fecha:%d/%m/%Y} · {x.referencia or ""} · {x.get_estado_display()}',
               reverse('inventario:detalle', args=[x.pk]))


def _lotes(user, t):
    from .models import Lote
    for x in Lote.objects.filter(codigo__icontains=t).select_related('producto')[:LIMITE]:
        yield (f'Lote {x.codigo}', f'{x.producto.nombre} · {x.get_estado_calidad_display()}',
               f'{reverse("inventario:trazabilidad")}?lote={x.codigo}')


def _guias(user, t):
    from logistica.models import GuiaRemision
    serie, numero = _numero(t)
    filtro = _q(('destinatario__nombre', 'destinatario__numero_doc', 'descripcion_motivo'), t)
    filtro |= Q(serie__iexact=serie, numero__iendswith=numero) if serie else Q(numero__iendswith=t)
    for x in GuiaRemision.objects.filter(filtro).select_related('destinatario').order_by('-id')[:LIMITE]:
        yield (f'Guía {x.serie}-{x.numero}', f'{x.destinatario.nombre if x.destinatario_id else ""} · '
               f'{x.get_estado_display()}', reverse('logistica:detalle', args=[x.pk]))


def _movimientos(user, t):
    from finanzas.models import Movimiento

    from .politicas import filtrar_cuentas
    qs = filtrar_cuentas(Movimiento.objects.filter(
        _q(('voucher', 'numero_operacion', 'glosa', 'tercero__nombre', 'tercero__numero_doc'), t)), user)
    for x in qs.select_related('cuenta', 'tercero').order_by('-fecha', '-id')[:LIMITE]:
        yield (f'{x.get_tipo_display()} {x.voucher}', f'{x.cuenta} · {x.fecha:%d/%m/%Y} · '
               f'{x.tercero.nombre if x.tercero_id else x.glosa[:40]}',
               f'{reverse("finanzas:movimientos")}?q={x.voucher}')


def _asientos(user, t):
    from contabilidad.models import Asiento
    for x in Asiento.objects.filter(_q(('numero', 'glosa'), t)).order_by('-fecha', '-id')[:LIMITE]:
        yield f'Asiento {x.numero}', f'{x.fecha:%d/%m/%Y} · {x.glosa[:70]}', reverse(
            'contabilidad:asiento_detalle', args=[x.pk])


def _cuentas_contables(user, t):
    from contabilidad.models import CuentaContable
    qs = CuentaContable.objects.filter(Q(codigo__startswith=t) | Q(nombre__icontains=t)).order_by('codigo')
    for x in qs[:LIMITE]:
        yield f'{x.codigo} {x.nombre}', 'Plan de cuentas', reverse('contabilidad:cuenta_editar', args=[x.pk])


def _trabajadores(user, t):
    from planillas.models import Trabajador

    from .politicas import filtrar_centros
    qs = filtrar_centros(Trabajador.objects.filter(
        _q(('numero_doc', 'apellido_paterno', 'apellido_materno', 'nombres', 'cargo', 'email'), t)), user)
    for x in qs.order_by('apellido_paterno', 'nombres')[:LIMITE]:  # sin sueldo: solo nombre, cargo y documento
        yield x.nombre_completo, f'{x.cargo or ""} · {x.numero_doc}', reverse('planillas:trabajador_editar', args=[x.pk])


def _obras(user, t):
    from obras.models import Obra
    qs = Obra.objects.filter(_q(('codigo', 'nombre', 'contrato', 'cliente__nombre', 'cliente__numero_doc'), t))
    for x in qs.select_related('cliente').order_by('-creado')[:LIMITE]:
        yield f'{x.codigo} {x.nombre}', f'{x.get_tipo_display()} · {x.get_estado_display()}', reverse(
            'obras:obra', args=[x.pk])


def _ordenes_produccion(user, t):
    from produccion.models import OrdenProduccion
    qs = OrdenProduccion.objects.filter(_q(('numero', 'producto__nombre', 'producto__codigo', 'lote_producido'), t))
    for x in qs.select_related('producto').order_by('-id')[:LIMITE]:
        yield f'Orden {x.numero}', f'{x.producto.nombre} · {x.get_estado_display()}', reverse(
            'manufactura:orden', args=[x.pk])


def _activos(user, t):
    from activos.models import ActivoFijo
    qs = ActivoFijo.objects.filter(_q(('codigo', 'nombre', 'marca', 'modelo', 'serie', 'ubicacion'), t))
    for x in qs.order_by('codigo')[:LIMITE]:
        yield f'{x.codigo} {x.nombre}', f'{x.ubicacion or ""} · {x.get_estado_display()}', reverse(
            'activos:detalle', args=[x.pk])


# (clave, título, icono, función, URL «ver todos» con ?q=)
FUENTES = [
    ('contactos', 'Contactos', 'bi-people', _contactos, 'terceros'),
    ('productos', 'Productos', 'bi-box-seam', _productos, 'productos'),
    ('ventas', 'Comprobantes de venta', 'bi-receipt', _ventas, 'ventas:lista'),
    ('pedidos', 'Cotizaciones y pedidos', 'bi-file-earmark-text', _pedidos, 'ventas:cot_lista'),
    ('crm', 'Oportunidades (CRM)', 'bi-funnel', _oportunidades, None),
    ('compras', 'Comprobantes de compra', 'bi-bag', _compras, 'compras:lista'),
    ('ordenes', 'Órdenes de compra', 'bi-clipboard-check', _ordenes_compra, 'compras:oc_lista'),
    ('solicitudes', 'Solicitudes de pedido', 'bi-cart', _solicitudes, None),
    ('almacen', 'Operaciones de almacén', 'bi-arrow-left-right', _operaciones, None),
    ('lotes', 'Lotes', 'bi-upc-scan', _lotes, None),
    ('guias', 'Guías de remisión', 'bi-truck', _guias, None),
    ('tesoreria', 'Movimientos de caja y bancos', 'bi-bank', _movimientos, 'finanzas:movimientos'),
    ('asientos', 'Asientos contables', 'bi-journal-text', _asientos, None),
    ('plan', 'Plan de cuentas', 'bi-list-ol', _cuentas_contables, None),
    ('personal', 'Personal', 'bi-person-badge', _trabajadores, None),
    ('obras', 'Obras y proyectos', 'bi-building', _obras, None),
    ('produccion', 'Órdenes de producción', 'bi-gear', _ordenes_produccion, None),
    ('activos', 'Activos fijos', 'bi-pc-display', _activos, None),
]


def _plano(texto):
    """Sin tildes ni mayúsculas: «facturacion» encuentra «Facturación»."""
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', texto or '') if unicodedata.category(c) != 'Mn').lower()


def menus(user, texto='', limite=40):
    """Paleta de Ctrl + K (como la de Odoo): sin texto, los módulos del usuario; con texto, los módulos y las
    opciones de su menú cuyo nombre lo contiene (sin tildes ni mayúsculas). Solo lo que el usuario puede abrir.
    [{texto, detalle, url, icono, color}]."""
    from .modulos import MODULOS, modulos_del_usuario, resolver_url
    mods = modulos_del_usuario(user)
    buscado = _plano(texto.strip())
    salida, vistos, permiso = [], set(), {}

    def abre(url):
        clave = url.partition('?')[0]
        if clave not in permiso:
            permiso[clave] = puede_abrir(user, url)
        return permiso[clave]
    for m in MODULOS:
        if m['clave'] not in mods:
            continue
        try:
            inicio = resolver_url(m['inicio'])
        except NoReverseMatch:
            continue
        if not buscado or buscado in _plano(m['nombre']):
            salida.append({'texto': m['nombre'], 'detalle': 'Módulo', 'url': inicio, 'icono': m['icono'],
                           'color': m.get('color', '')})
        if not buscado:
            continue
        for etiqueta, destino in m['menu']:
            hijos = destino if isinstance(destino, list) else [(etiqueta, destino, '')]
            for texto_hijo, d, icono in hijos:
                nombre = texto_hijo if texto_hijo == etiqueta else f'{etiqueta} › {texto_hijo}'
                if buscado not in _plano(nombre):
                    continue
                try:
                    url = resolver_url(d)
                except NoReverseMatch:
                    continue
                if url in vistos or not abre(url):
                    continue
                vistos.add(url)
                salida.append({'texto': nombre, 'detalle': m['nombre'], 'url': url, 'icono': icono or m['icono'],
                               'color': m.get('color', '')})
        if len(salida) >= limite:
            break
    return salida[:limite]


def buscar(user, texto):
    """[{clave, titulo, icono, filas: [(texto, detalle, url)], todos: url o None}] de las fuentes que el usuario
    puede abrir y que tienen resultados."""
    texto = (texto or '').strip()
    if len(texto) < MINIMO:
        return []
    salida, permiso = [], {}
    for clave, titulo, icono, funcion, lista in FUENTES:
        try:
            filas = []
            for fila in funcion(user, texto):
                ruta = resolve(fila[2].partition('?')[0]).view_name
                if ruta not in permiso:
                    permiso[ruta] = puede_abrir(user, fila[2])
                if permiso[ruta]:
                    filas.append(fila)
        except (LookupError, NoReverseMatch, Resolver404):  # un módulo que esta instalación no tiene
            continue
        if not filas:
            continue
        todos = None
        if lista:
            try:
                url = reverse(lista)
                todos = f'{url}?q={texto}' if puede_abrir(user, url) else None
            except NoReverseMatch:
                todos = None
        salida.append({'clave': clave, 'titulo': titulo, 'icono': icono, 'filas': filas, 'todos': todos})
    return salida
