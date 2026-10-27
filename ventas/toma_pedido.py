"""TomaPedido (v1.83): el vendedor toma el pedido del cliente en el celular con el precio de su lista y el stock, y lo
envía a aprobación. Es una orden de pedido normal de Ventas (la misma ruta de aprobación, emisión y despacho)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, Producto, Tercero
from core.permisos import puede

from .models import Cotizacion, CotizacionItem


def _mis_pedidos(user):
    qs = Cotizacion.objects.filter(tipo='PED')
    return qs if user.is_superuser else qs.filter(tomado_por=user)


def _producto_json(p, lista, hoy):
    precio, descuento = p.precio_para(lista, Decimal('1'), hoy)
    return {'id': p.pk, 'codigo': p.codigo, 'nombre': p.nombre, 'unidad': p.unidad, 'precio': str(precio),
            'descuento': str(descuento), 'stock': str(p.stock) if p.tipo == 'BIEN' else '',
            'barras': p.codigo_barras}


def _productos(q):
    qs = Producto.objects.filter(activo=True, es_plantilla=False, puede_venderse=True)
    if q:
        qs = qs.filter(Q(codigo__iexact=q) | Q(codigo_barras=q) | Q(nombre__icontains=q) | Q(codigo__icontains=q))
    return qs.order_by('nombre')


@login_required
def buscar(request):
    """Productos para el pedido con el precio de la lista del cliente y el stock total."""
    q = (request.GET.get('q') or '').strip()
    cliente = Tercero.objects.filter(pk=request.GET.get('cliente') or 0).select_related('lista_precios').first()
    lista = cliente.lista_precios if cliente else None
    hoy = timezone.localdate()
    return JsonResponse({'productos': [_producto_json(p, lista, hoy) for p in _productos(q)[:20]]},
                        json_dumps_params={'ensure_ascii': False})


@login_required
def lista(request):
    pedidos = _mis_pedidos(request.user).select_related('tercero').order_by('-fecha', '-id')[:100]
    return render(request, 'ventas/toma_pedido_lista.html', {'pedidos': pedidos})


def _lineas(post):
    """[(producto, cantidad, precio, descuento)] de lo que mandó la pantalla."""
    ids, cants = post.getlist('producto'), post.getlist('cantidad')
    precios, descs = post.getlist('precio'), post.getlist('descuento')
    productos = {p.pk: p for p in Producto.objects.filter(pk__in=[int(i) for i in ids if i.isdigit()])}
    lineas = []
    for i, pk in enumerate(ids):
        p = productos.get(int(pk)) if pk.isdigit() else None
        try:
            cantidad = Decimal(cants[i] or '0')
            precio = Decimal(precios[i] or '0')
            descuento = Decimal((descs[i] if i < len(descs) else '') or '0')
        except (InvalidOperation, IndexError):
            continue
        if p is not None and cantidad > 0:
            lineas.append((p, cantidad, precio, descuento))
    return lineas


def crear_pedido(user, cliente, lineas, glosa=''):
    """Crea la orden de pedido con el mismo formulario de Ventas (series, totales, validaciones). Devuelve
    (pedido o None, [errores])."""
    from core.forms import item_formset
    from core.utils import procesar_documento

    from .forms import CotizacionForm
    datos = {'tipo': 'PED', 'tercero': cliente.pk if cliente else '', 'fecha': timezone.localdate().isoformat(),
             'validez_dias': '7', 'vendedor': user.username[:80], 'moneda': 'PEN',  # VEN-22: el usuario
             'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'condicion_pago': '', 'glosa': glosa[:500],
             'items-TOTAL_FORMS': str(len(lineas)), 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
             'items-MAX_NUM_FORMS': '1000'}
    for i, (p, cantidad, precio, descuento) in enumerate(lineas):
        datos.update({f'items-{i}-producto': str(p.pk), f'items-{i}-descripcion': p.nombre[:250],
                      f'items-{i}-cantidad': str(cantidad), f'items-{i}-precio_unitario': str(precio),
                      f'items-{i}-descuento_pct': str(descuento), f'items-{i}-afectacion': ''})
    if not lineas:
        return None, ['Agregue al menos un producto.']
    doc, form, formset = procesar_documento(datos, CotizacionForm, item_formset(Cotizacion, CotizacionItem, extra=0),
                                            Cotizacion())
    if doc is None:
        errores = [f'{form.fields[c].label if c in form.fields else ""}: {e}'.lstrip(': ')
                   for c, lista_e in form.errors.items() for e in lista_e]
        errores += [f'Línea {i + 1}: {e}' for i, f in enumerate(formset.forms) for lista_e in f.errors.values()
                    for e in lista_e]
        errores += list(formset.non_form_errors())
        return None, errores or ['Revise los datos del pedido.']
    Cotizacion.objects.filter(pk=doc.pk).update(tomado_por=user)
    doc.tomado_por = user
    return doc, []


@login_required
def nuevo(request):
    if request.method == 'POST':
        cliente = Tercero.objects.filter(pk=request.POST.get('cliente') or 0, activo=True).first()
        lineas = _lineas(request.POST)
        pedido, errores = crear_pedido(request.user, cliente, lineas, request.POST.get('glosa', '').strip())
        if pedido is not None:
            messages.success(request, f'Pedido {pedido.numero} registrado por S/ {pedido.total:,.2f}.')
            if request.POST.get('enviar') == '1':
                _enviar(request, pedido)
            # ?nuevo=1: la pantalla borra el borrador que guardaba el celular
            return redirect(f'{reverse("toma_pedido:pedido", args=[pedido.pk])}?nuevo=1')
        for e in errores:
            messages.error(request, e)
    return render(request, 'ventas/toma_pedido.html', {'igv': Empresa.actual().igv_tasa})


def _enviar(request, pedido):
    from .aprobacion_pedido import ErrorAprobacionPedido, enviar
    try:
        messages.info(request, enviar(pedido, request.user))
    except ErrorAprobacionPedido as exc:
        messages.error(request, str(exc))


@login_required
def pedido(request, pk):
    from .aprobacion_pedido import enviado_a_aprobacion, motivos
    ped = get_object_or_404(_mis_pedidos(request.user).select_related('tercero'), pk=pk)
    por_enviar = ped.estado == 'PENDIENTE' and not enviado_a_aprobacion(ped)
    if request.method == 'POST':
        if not (por_enviar and puede(request.user, 'toma_pedido.registrar')):
            raise Http404
        _enviar(request, ped)
        return redirect('toma_pedido:pedido', pk)
    return render(request, 'ventas/toma_pedido_detalle.html', {
        'ped': ped, 'items': ped.items.select_related('producto'), 'por_enviar': por_enviar,
        'motivos': motivos(ped)[1] if por_enviar else [],
        'ver_completo': puede(request.user, 'ventas.emitir')})


@login_required
def catalogo(request):
    from core.models import StockAlmacen
    q = (request.GET.get('q') or '').strip()
    cliente = Tercero.objects.filter(pk=request.GET.get('cliente') or 0).select_related('lista_precios').first()
    hoy = timezone.localdate()
    productos = list(_productos(q)[:50]) if q else []
    stocks = {}
    for s in StockAlmacen.objects.filter(producto__in=productos).exclude(cantidad=0).select_related('almacen'):
        stocks.setdefault(s.producto_id, []).append(s)
    filas = [{'p': p, **_producto_json(p, cliente.lista_precios if cliente else None, hoy),
              'almacenes': stocks.get(p.pk, [])} for p in productos]
    return render(request, 'ventas/toma_pedido_catalogo.html', {'q': q, 'filas': filas, 'cliente': cliente})
