from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView

from compras.models import Compra, OrdenCompra
from core.listas import Lista
from core.models import Empresa, Producto
from core.permisos import puede
from core.utils import elegidos, rango_por_defecto
from core.views import FormGenerico, ListaGenerica
from ventas.models import Venta

from . import servicios
from .forms import OperacionForm, TipoOperacionForm, operacion_formset
from .models import Operacion, TipoOperacion

GRUPOS = {
    'recepciones': ('Recepciones e ingresos', ['INGRESO']),
    'salidas': ('Despachos y salidas', ['SALIDA']),
    'traslados': ('Traslados y tránsito', ['TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION']),
    'manufactura': ('Manufactura', ['MANUFACTURA']),
}


# columnas de la lista de operaciones (ALM-13 / LIS-01): las básicas y las opcionales que cada usuario prende
LISTA_OPERACIONES = Lista('inventario.operaciones', [
    ('fecha', 'Fecha contable', True), ('numero', 'Número', True), ('tipo', 'Tipo', True),
    ('origen', 'Almacén de origen', True), ('destino', 'Almacén de destino', True),
    ('documento', 'Documento de origen', True), ('tercero', 'Proveedor / cliente', True),
    ('guia', 'Guía / referencia', False), ('centro', 'Centro de costo / obra', False),
    ('registro', 'Registrado (fecha y usuario)', False), ('confirmo', 'Confirmado por', False),
    ('valor', 'Valor S/', True), ('estado', 'Estado', True)],
    agrupaciones=[('tercero', 'Proveedor / cliente'), ('almacen', 'Almacén'), ('usuario', 'Usuario'),
                  ('tipo', 'Tipo'), ('centro', 'Centro de costo')])


def _tercero_de(o):
    doc = o.orden_compra or o.compra or o.venta
    return o.tercero or (doc.tercero if doc is not None else None)


def _celda(o, col):
    """Valor de la columna para el Excel."""
    tercero = _tercero_de(o)
    return {'fecha': o.fecha, 'numero': o.numero or '(borrador)', 'tipo': o.tipo.nombre,
            'origen': str(o.almacen_origen or ''), 'destino': str(o.almacen_destino or ''),
            'documento': str(o.documento_origen or ''),
            'tercero': f'{tercero.nombre} ({tercero.numero_doc})' if tercero else '', 'guia': o.referencia,
            'centro': str(o.centro_costo or (o.orden_compra.centro_costo if o.orden_compra_id else '') or ''),
            'registro': f'{timezone.localtime(o.creado):%d/%m/%Y %H:%M} {o.creado_por.username if o.creado_por_id else ""}',
            'confirmo': o.confirmado_por.username if o.confirmado_por_id else '', 'valor': o.valor_total,
            'estado': o.get_estado_display()}[col]


@login_required
def lista(request):
    from core.listas import agrupar
    from core.models import Almacen
    from core.modulos import puede_ver_costos
    from django.contrib.auth import get_user_model
    from contabilidad.models import CentroCosto
    qs = Operacion.objects.select_related('tipo', 'almacen_origen', 'almacen_destino', 'tercero', 'compra__tercero',
                                          'orden_compra__tercero', 'orden_compra__centro_costo', 'venta__tercero',
                                          'envio', 'centro_costo', 'creado_por', 'confirmado_por')
    g = request.GET
    grupo = g.get('grupo', '')
    titulo = 'Operaciones de inventario'
    if grupo in GRUPOS:
        titulo, clases = GRUPOS[grupo]
        qs = qs.filter(tipo__clase__in=clases)
    tipos = TipoOperacion.objects.filter(activo=True)
    if grupo in GRUPOS:
        tipos = tipos.filter(clase__in=GRUPOS[grupo][1])
    # PLAT-13: cada filtro admite varios valores (OR dentro del filtro, AND entre filtros)
    sel = {k: [v for v in elegidos(request, k) if k == 'estado' or v.isdigit()]
           for k in ('tipo', 'estado', 'almacen', 'usuario', 'centro')}
    if sel['tipo']:
        qs = qs.filter(tipo_id__in=sel['tipo'])
    if sel['estado']:
        qs = qs.filter(estado__in=sel['estado'])
    if sel['almacen']:
        qs = qs.filter(Q(almacen_origen_id__in=sel['almacen']) | Q(almacen_destino_id__in=sel['almacen']))
    if sel['usuario']:
        qs = qs.filter(Q(creado_por_id__in=sel['usuario']) | Q(confirmado_por_id__in=sel['usuario']))
    if sel['centro']:
        qs = qs.filter(Q(centro_costo_id__in=sel['centro']) | Q(orden_compra__centro_costo_id__in=sel['centro']))
    q = g.get('q', '').strip()
    if q:  # número, guía, OC, factura, proveedor (nombre o RUC), producto y usuario
        qs = qs.filter(Q(numero__icontains=q) | Q(referencia__icontains=q) | Q(glosa__icontains=q) |
                       Q(tercero__nombre__icontains=q) | Q(tercero__numero_doc__icontains=q) |
                       Q(orden_compra__numero__icontains=q) | Q(orden_compra__tercero__nombre__icontains=q) |
                       Q(orden_compra__tercero__numero_doc__icontains=q) | Q(compra__numero__icontains=q) |
                       Q(compra__tercero__nombre__icontains=q) | Q(compra__tercero__numero_doc__icontains=q) |
                       Q(venta__numero__icontains=q) | Q(venta__tercero__nombre__icontains=q) |
                       Q(creado_por__username__icontains=q) | Q(confirmado_por__username__icontains=q) |
                       Q(items__producto__nombre__icontains=q)).distinct()
    desde, hasta = rango_por_defecto(request, qs)
    if not g.get('todo'):
        qs = qs.filter(Q(fecha__range=[desde, hasta]) | Q(estado='BORRADOR'))
    costos = puede_ver_costos(request.user)
    lst = LISTA_OPERACIONES
    ctx_lista = lst.contexto(request, ocultas=() if costos else ('valor',))
    if g.get('formato') == 'excel':
        from core.utils import excel_response
        cols = ctx_lista['visibles']
        filas = [[_celda(o, c) for c in cols] for o in qs.prefetch_related('items__producto')[:20000]]
        return excel_response('Operaciones_inventario', titulo, lst.titulos(cols), filas)
    # traslados a tránsito con mercadería aún por recibir
    en_transito = []
    if grupo in ('', 'traslados'):
        for envio in Operacion.objects.filter(estado='CONFIRMADO', tipo__clase='TRANSITO_ENVIO').select_related(
                'almacen_destino'):
            if servicios.pendientes(Operacion(tipo=_tipo_recepcion_transito() or envio.tipo, envio=envio)):
                en_transito.append(envio)
    grupos_filas = None
    clave_grupo = {'tercero': lambda o: (_tercero_de(o).nombre if _tercero_de(o) else ''),
                   'almacen': lambda o: str(o.almacen_destino or o.almacen_origen or ''),
                   'usuario': lambda o: o.creado_por.username if o.creado_por_id else '',
                   'tipo': lambda o: o.tipo.nombre,
                   'centro': lambda o: str(o.centro_costo or (o.orden_compra.centro_costo if o.orden_compra_id
                                                              else '') or '')}.get(g.get('agrupar'))
    if clave_grupo:
        grupos_filas = agrupar(list(qs.prefetch_related('items__producto')[:1000]), clave_grupo,
                               (lambda o: o.valor_total) if costos else None)
    usuarios = get_user_model().objects.filter(is_active=True, acceso_proveedor__isnull=True).order_by('username')
    return render(request, 'inventario/operaciones.html', {
        'page_obj': Paginator(qs, 50).get_page(g.get('page')), 'grupo': grupo, 'titulo': titulo,
        'tipos': tipos, 'q': q, 'desde': desde, 'hasta': hasta, 'en_transito': en_transito,
        'estados': Operacion.ESTADOS, 'recepcion_transito': _tipo_recepcion_transito(), 'grupos_filas': grupos_filas,
        'almacenes': Almacen.objects.filter(activo=True), 'usuarios': usuarios,
        'centros': CentroCosto.objects.filter(activo=True).order_by('codigo'), 'sel': sel, **ctx_lista})

def _tipo_recepcion_transito():
    return TipoOperacion.objects.filter(clase='TRANSITO_RECEPCION', activo=True).first()


def _contexto_form(op, titulo, costos=True, user=None, formset=None):
    tipo = op.tipo
    # solo los productos del documento; los demás se piden al elegirlos (no viaja todo el catálogo)
    from core.opciones import datos_productos
    ids = {int(f['producto'].value()) for f in (formset.forms if formset else [])
           if 'producto' in f.fields and str(f['producto'].value() or '').isdigit()}
    return {'titulo': titulo, 'op': op, 'tipo': tipo,
            # sin permiso de costos solo se pide el costo cuando el tipo lo exige (saldo inicial, ajuste ingreso)
            # la recepción de compras toma el costo de la orden o la factura: no se escribe (ALM-07)
            'muestra_costo': tipo.requiere_costo or (tipo.clase == 'INGRESO' and costos and tipo.origen not in (
                'ORDEN_COMPRA', 'COMPRA')),
            'muestra_rol': tipo.clase == 'MANUFACTURA',
            'usa_lotes': Producto.objects.filter(activo=True).exclude(control='').exists(),
            'productos': datos_productos(user, ids) if user is not None else []}


def _guardar(request, op, titulo, initial=None, items=None):
    formset_cls = operacion_formset(extra=0)  # min_num ya muestra una fila
    if request.method == 'POST':
        form = OperacionForm(request.POST, request.FILES, instance=op, tipo=op.tipo)
        formset = formset_cls(request.POST, instance=form.instance)
        if form.is_valid() and formset.is_valid():
            hoy = timezone.localdate()
            if form.cleaned_data['fecha'] < hoy.replace(day=1) and not puede(request.user, 'inventario.fecha_anterior'):
                form.add_error('fecha', 'Registrar con fecha de un mes anterior exige el permiso «Fecha contable de un '
                                        'mes anterior» (Ajustes › Usuarios).')
            if op.tipo.clase == 'MANUFACTURA':
                for f in formset.forms:
                    cd = getattr(f, 'cleaned_data', None) or {}
                    if cd and not cd.get('DELETE') and cd.get('producto') and not cd.get('rol'):
                        f.add_error('rol', 'Indique si es insumo o producto terminado.')
            if formset.is_valid() and form.is_valid():
                nuevo = not op.pk
                confirmar = request.POST.get('accion') == 'confirmar'
                try:
                    with transaction.atomic():
                        op = form.save(commit=False)
                        if nuevo:
                            op.creado_por = request.user
                        motivo_fecha = (form.cleaned_data.get('motivo_fecha') or '').strip()
                        if motivo_fecha and motivo_fecha not in op.glosa:
                            op.glosa = f'{op.glosa}\nFecha de un mes anterior: {motivo_fecha}'.strip()
                        op.save()
                        formset.instance = op
                        formset.save()
                        if form.cleaned_data.get('sustento'):
                            from core.sustentos import adjuntar
                            adjuntar(op, form.cleaned_data['sustento'], request.user, op.referencia or op.tipo.nombre)
                        if confirmar and nuevo:  # si no se puede confirmar, no queda un borrador suelto (ALM-11)
                            servicios.confirmar(op, request.user)
                except servicios.ErrorOperacion as exc:
                    messages.error(request, f'No se pudo confirmar y no se guardó: {exc}')
                    op = Operacion(tipo=op.tipo)
                    form = OperacionForm(request.POST, request.FILES, instance=op, tipo=op.tipo)
                    formset = formset_cls(request.POST, instance=op)
                    form.is_valid()
                    formset.is_valid()
                else:
                    _avisar_guia_repetida(request, op)
                    if confirmar and nuevo:
                        messages.success(request, f'{op.tipo.nombre} {op.numero} confirmada: almacén actualizado.')
                        return _despues_de_confirmar(request, op)
                    if confirmar:
                        return _confirmar(request, op)
                    messages.success(request, 'Operación guardada en borrador. Revise y confirme para mover el almacén.')
                    return redirect('inventario:detalle', op.pk)
    else:
        form = OperacionForm(instance=op, tipo=op.tipo, initial=initial)
        if items:
            fs_cls = operacion_formset(extra=max(len(items) - 1, 0))
            formset = fs_cls(instance=op, initial=items)
        else:
            formset = formset_cls(instance=op)
    from core.modulos import puede_ver_costos
    ctx = _contexto_form(op, titulo, puede_ver_costos(request.user), request.user, formset)
    ctx.update(form=form, formset=formset)
    return render(request, 'inventario/operacion_form.html', ctx)


@login_required
def nueva(request):
    codigo = request.GET.get('tipo')
    if not codigo:
        tipos = TipoOperacion.objects.filter(activo=True)
        grupo = request.GET.get('grupo', '')
        bloques = [(clave, nombre, [t for t in tipos if t.clase in clases]) for clave, (nombre, clases)
                   in GRUPOS.items() if not grupo or grupo == clave]
        return render(request, 'inventario/operacion_tipo.html', {'bloques': bloques})
    tipo = get_object_or_404(TipoOperacion, codigo=codigo, activo=True)
    op = Operacion(tipo=tipo)
    initial, items = {}, None
    origen = None
    if request.GET.get('oc'):
        origen = op.orden_compra = get_object_or_404(OrdenCompra, pk=request.GET['oc'])
        initial['orden_compra'] = origen.pk
        initial['centro_costo'] = origen.centro_costo_id  # AUD-04: el centro de costo de la orden viaja
        from obras.models import Obra
        obra = Obra.de_centro(origen.centro_costo_id)
        if obra is not None and obra.almacen_id and tipo.usa_destino_almacen:
            initial['almacen_destino'] = obra.almacen_id  # la orden es de una obra: entra a su almacén
        elif tipo.usa_destino_almacen:
            # AUD-03: los bienes de la orden entran al almacén por defecto de sus productos (si todos tienen el mismo)
            defectos = {i.producto.almacen_defecto_id for i in origen.items.select_related('producto')
                        if i.producto_id and i.producto.es_inventariable}
            if len(defectos) == 1 and None not in defectos:
                initial['almacen_destino'] = defectos.pop()
            elif len(defectos - {None}) > 1:
                messages.info(request, 'Los productos de la orden tienen distintos almacenes por defecto: registre una '
                                       'recepción por almacén (quite las líneas que van a otro).')
    elif request.GET.get('compra'):
        origen = op.compra = get_object_or_404(Compra, pk=request.GET['compra'])
        initial['compra'] = origen.pk
        initial['almacen_origen'] = initial['almacen_destino'] = origen.almacen_id
    elif request.GET.get('venta'):
        origen = op.venta = get_object_or_404(Venta, pk=request.GET['venta'])
        initial['venta'] = origen.pk
        initial['almacen_origen'] = initial['almacen_destino'] = origen.almacen_id
    elif request.GET.get('envio'):
        origen = op.envio = get_object_or_404(Operacion, pk=request.GET['envio'], tipo__clase='TRANSITO_ENVIO')
        initial['envio'] = origen.pk
        initial['almacen_destino'] = origen.almacen_destino_id
    if origen is not None:
        items = servicios.items_desde_origen(op)
        if not items:
            messages.warning(request, f'{origen}: no tiene cantidades pendientes para "{tipo.nombre}".')
        initial['referencia'] = str(origen)[:60]
    initial = {k: v for k, v in initial.items() if v}
    return _guardar(request, op, f'Nueva operación: {tipo.nombre}', initial, items)


@login_required
def editar(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if op.estado != 'BORRADOR':
        messages.error(request, 'Solo se editan operaciones en borrador.')
        return redirect('inventario:detalle', pk)
    return _guardar(request, op, f'Editar {op}')


@login_required
def pendientes_origen(request, pk):
    """Recalcula los ítems de un borrador con lo pendiente del documento de origen."""
    op = get_object_or_404(Operacion, pk=pk, estado='BORRADOR')
    if request.method == 'POST':
        with transaction.atomic():
            op.items.all().delete()
            for fila in servicios.items_desde_origen(op):
                op.items.create(producto_id=fila['producto'], cantidad=fila['cantidad'],
                                costo_unitario=fila['costo_unitario'])
        messages.success(request, 'Ítems actualizados con lo pendiente del documento de origen.')
    return redirect('inventario:detalle', pk)


@login_required
def detalle(request, pk):
    op = get_object_or_404(Operacion.objects.select_related(
        'tipo', 'almacen_origen', 'almacen_destino', 'tercero', 'orden_compra', 'compra', 'venta', 'envio',
        'creado_por', 'confirmado_por', 'anulado_por'), pk=pk)
    items = list(op.items.select_related('producto'))
    ctx = {'op': op, 'items': items, 'total': sum((i.valor for i in items), 0),
           'errores': servicios.errores_confirmacion(op) if op.estado == 'BORRADOR' else [],
           'bloqueo_anular': servicios.errores_anulacion(op) if op.estado == 'CONFIRMADO' else [],
           'recepciones': op.recepciones.select_related('tipo') if op.tipo.clase == 'TRANSITO_ENVIO' else [],
           'pendiente_transito': op.tipo.clase == 'TRANSITO_ENVIO' and op.estado == 'CONFIRMADO' and bool(
               servicios.pendientes(Operacion(tipo=_tipo_recepcion_transito() or op.tipo, envio=op))),
           'recepcion_transito': _tipo_recepcion_transito()}
    # nota de crédito sugerida tras una devolución
    if op.estado == 'CONFIRMADO' and op.tipo.origen in ('VENTA', 'COMPRA'):
        doc = op.venta if op.tipo.clase == 'INGRESO' and op.venta_id else (
            op.compra if op.tipo.clase == 'SALIDA' and op.compra_id else None)
        if doc is not None:
            app = 'ventas' if op.venta_id else 'compras'
            ctx['url_nota'] = f"{reverse(f'{app}:nuevo')}?ref={doc.pk}&tipo=07&motivo=07&op={op.pk}"
            ctx['app_nota'] = app
    ctx['contrapartidas'] = servicios.contrapartidas(op)
    ctx['facturas_oc'] = servicios.facturas_de_recepcion(op) if op.estado == 'CONFIRMADO' and op.tipo.clase == 'INGRESO' \
        else []
    return render(request, 'inventario/operacion_detalle.html', ctx)


def _avisar_guia_repetida(request, op):
    """ALM-10: la misma guía del proveedor ya se usó en otra recepción (aviso, no bloqueo)."""
    ref = (op.referencia or '').strip()
    proveedor = op.tercero_id or (op.orden_compra.tercero_id if op.orden_compra_id else None) or (
        op.compra.tercero_id if op.compra_id else None)
    if op.tipo.clase != 'INGRESO' or not ref or not proveedor or (op.orden_compra_id and ref == op.orden_compra.numero):
        return
    otra = (Operacion.objects.filter(tipo__clase='INGRESO', referencia__iexact=ref).exclude(pk=op.pk)
            .exclude(estado='ANULADO').filter(Q(tercero_id=proveedor) | Q(orden_compra__tercero_id=proveedor) |
                                              Q(compra__tercero_id=proveedor)).first())
    if otra is not None:
        messages.warning(request, f'La guía {ref} ya se usó en {otra.numero or "un borrador"} del mismo proveedor: '
                                  f'revise que no sea la misma mercadería registrada dos veces.')


def _confirmar(request, op):
    try:
        servicios.confirmar(op, request.user)
        messages.success(request, f'{op.tipo.nombre} {op.numero} confirmada: almacén actualizado.')
    except servicios.ErrorOperacion as exc:
        messages.error(request, f'No se pudo confirmar: {exc}')
        return redirect('inventario:detalle', op.pk)
    return _despues_de_confirmar(request, op)


def _despues_de_confirmar(request, op):
    if op.orden_compra_id and op.almacen_destino_id:  # compra de una obra recibida en otro almacén: se avisa
        from obras.models import Obra
        obra = Obra.de_centro(op.orden_compra.centro_costo_id)
        if obra is not None and obra.almacen_id and obra.almacen_id != op.almacen_destino_id:
            messages.warning(request, f'La orden es de la obra {obra.nombre} pero se recibió en {op.almacen_destino}, '
                                      f'no en su almacén de obra ({obra.almacen}).')
    # recepción de una compra: conformidad (aceptación de la mercadería) al proveedor por correo
    from core.models import CorreoConfig
    if _es_recepcion_compra(op) and CorreoConfig.actual().activa:
        _enviar_conformidad(request, op)
    return redirect('inventario:detalle', op.pk)


def _es_recepcion_compra(op):
    return op.tipo.clase == 'INGRESO' and bool(op.orden_compra_id or op.compra_id)


def _enviar_conformidad(request, op):
    from core.correo import ErrorCorreo
    from proveedores.servicios import enviar_conformidad
    try:
        destinos = enviar_conformidad(op, request)
        messages.success(request, f'Conformidad de recepción enviada a {", ".join(destinos)}.')
    except ErrorCorreo as exc:
        messages.warning(request, f'No se envió la conformidad al proveedor: {exc}')


@login_required
def conformidad(request, pk):
    op = get_object_or_404(Operacion, pk=pk, estado='CONFIRMADO')
    if request.method == 'POST' and _es_recepcion_compra(op):
        _enviar_conformidad(request, op)
    return redirect('inventario:detalle', pk)


@login_required
def confirmar(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if request.method == 'POST':
        return _confirmar(request, op)
    return redirect('inventario:detalle', pk)


@login_required
def anular(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if request.method == 'POST':
        try:
            servicios.anular(op, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{op} anulada: se revirtió el movimiento de almacén.')
        except servicios.ErrorOperacion as exc:
            messages.error(request, f'No se pudo anular: {exc}')
    return redirect('inventario:detalle', pk)


@login_required
def eliminar(request, pk):
    op = get_object_or_404(Operacion, pk=pk)
    if request.method == 'POST':
        if op.estado != 'BORRADOR':
            messages.error(request, 'Solo se eliminan borradores; las operaciones confirmadas se anulan.')
            return redirect('inventario:detalle', pk)
        op.delete()
        messages.success(request, 'Borrador eliminado.')
    return redirect('inventario:lista')


@login_required
def imprimir(request, pk):
    op = get_object_or_404(Operacion.objects.select_related('tipo'), pk=pk)
    items = list(op.items.select_related('producto'))
    # ubicación habitual en el almacén donde se toma (salidas, traslados) o se guarda (ingresos) la mercadería
    from core.models import StockAlmacen
    almacen_id = op.almacen_origen_id or op.almacen_destino_id
    lugares = StockAlmacen.ubicaciones(almacen_id, [i.producto_id for i in items]) if almacen_id else {}
    for i in items:
        i.ubicacion = lugares.get(i.producto_id, '')
    return render(request, 'inventario/operacion_imprimir.html', {
        'con_ubicacion': bool(lugares),
        'op': op, 'items': items, 'total': sum((i.valor for i in items), 0), 'empresa': Empresa.actual()})


# ---------------------------------------------------------------- cierre de kardex
def _requiere_costos(request):
    from core.inventario import _sin_permiso_costos
    from core.modulos import puede_ver_costos
    return None if puede_ver_costos(request.user) else _sin_permiso_costos(request)


@login_required
def cierres(request):
    from . import cierre as srv
    from .models import CierreKardex
    bloqueo = _requiere_costos(request)
    if bloqueo:
        return bloqueo
    siguiente = srv.periodo_siguiente()
    errores = srv.errores_cierre(siguiente)[0] if siguiente else []
    if request.method == 'POST':
        try:
            c = srv.cerrar(request.POST.get('periodo', ''), request.user, request.POST.get('observaciones', ''))
            messages.success(request, f'Kardex cerrado hasta el {c.fecha_corte:%d/%m/%Y}. Inventario valorizado: '
                                      f'S/ {c.valor_total:,.2f}.')
            return redirect('inventario:cierre', c.pk)
        except srv.KardexCerrado as exc:
            messages.error(request, str(exc))
        return redirect('inventario:cierres')
    return render(request, 'inventario/cierres.html', {
        'cierres': CierreKardex.objects.select_related('cerrado_por', 'reabierto_por')[:60],
        'vigente': srv.ultimo_cierre(), 'siguiente': siguiente, 'errores_siguiente': errores,
        'siguiente_texto': f'{siguiente[4:]}/{siguiente[:4]}' if siguiente else ''})


@login_required
def cierre_detalle(request, pk):
    from core.utils import excel_response
    from .cierre import ultimo_cierre
    from .models import CierreKardex
    bloqueo = _requiere_costos(request)
    if bloqueo:
        return bloqueo
    c = get_object_or_404(CierreKardex.objects.select_related('cerrado_por', 'reabierto_por'), pk=pk)
    saldos = c.saldos.select_related('producto', 'almacen')
    if request.GET.get('formato') == 'excel':
        datos = [[s.producto.codigo, s.producto.nombre, s.producto.unidad, str(s.almacen or ''), s.cantidad, s.costo,
                  s.valor] for s in saldos]
        datos.append(['', 'TOTAL', '', '', c.unidades, '', c.valor_total])
        return excel_response(f'Cierre_kardex_{c.periodo}', f'Cierre de kardex al {c.fecha_corte:%d/%m/%Y}',
                              ['Código', 'Producto', 'U.M.', 'Almacén', 'Cantidad', 'Costo promedio', 'Valor S/'],
                              datos)
    return render(request, 'inventario/cierre_detalle.html', {
        'c': c, 'saldos': saldos, 'puede_reabrir': request.user.is_superuser and c == ultimo_cierre()})


@login_required
def cierre_reabrir(request, pk):
    from . import cierre as srv
    from .models import CierreKardex
    c = get_object_or_404(CierreKardex, pk=pk)
    if request.method == 'POST':
        if not request.user.is_superuser:
            messages.error(request, 'Solo un administrador puede reabrir el kardex.')
        else:
            try:
                srv.reabrir(c, request.user, request.POST.get('motivo', ''))
                messages.success(request, f'{c} reabierto: se pueden volver a registrar movimientos de ese periodo.')
            except srv.KardexCerrado as exc:
                messages.error(request, str(exc))
    return redirect('inventario:cierre', pk)


# ---------------------------------------------------------------- tipos de operación
class TipoLista(ListaGenerica):
    model = TipoOperacion
    titulo = 'Tipos de operación de inventario'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Clase', 'get_clase_display'),
                ('Documento de origen', 'get_origen_display'), ('Cuenta contable', 'cuenta_contable'),
                ('Tabla 12 SUNAT', 'codigo_sunat'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'inventario:tipo_nuevo', 'inventario:tipo_editar'
    buscar_en = ['codigo', 'nombre']


class TipoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = TipoOperacion, TipoOperacionForm, 'Nuevo tipo de operación'
    success_url = reverse_lazy('inventario:tipos')


class TipoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = TipoOperacion, TipoOperacionForm, 'Editar tipo de operación'
    success_url = reverse_lazy('inventario:tipos')
