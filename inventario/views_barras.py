"""Código de barras (v1.83): consultar un producto escaneándolo, imprimir etiquetas y asignar códigos."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe

from core.models import Lote, Producto, StockAlmacen

from . import barras


def buscar_codigo(codigo):
    """Producto (y lote, si se escaneó un lote) por código de barras, código del producto o código del lote."""
    codigo = (codigo or '').strip()
    if not codigo:
        return None, None
    p = Producto.objects.filter(Q(codigo_barras=codigo) | Q(codigo__iexact=codigo), es_plantilla=False).first()
    if p is not None:
        return p, None
    lote = Lote.objects.filter(codigo=codigo).select_related('producto').first()
    return (lote.producto, lote) if lote else (None, None)


@login_required
def escanear(request):
    from core.modulos import puede_ver_costos
    codigo = (request.GET.get('codigo') or '').strip()
    producto, lote = buscar_codigo(codigo)
    contexto = {'codigo': codigo, 'producto': producto, 'lote': lote}
    if producto is not None:
        contexto.update(
            stocks=StockAlmacen.objects.filter(producto=producto).exclude(cantidad=0).select_related('almacen'),
            lotes=producto.lotes.filter(stocks__cantidad__gt=0).distinct()[:20],
            ver_costos=puede_ver_costos(request.user),
            barras_svg=mark_safe(barras.svg(producto.codigo_barras or producto.codigo)))
    elif codigo:
        messages.warning(request, f'No hay un producto ni un lote con el código «{codigo}».')
    return render(request, 'inventario/barras_escanear.html', contexto)


@login_required
def etiquetas(request):
    """Hoja de etiquetas para imprimir (impresora común en hojas de etiquetas o impresora de etiquetas)."""
    ids = [int(x) for x in request.GET.getlist('producto') if x.isdigit()]
    cantidades = request.GET.getlist('cantidad')
    productos = {p.pk: p for p in Producto.objects.filter(pk__in=ids)}
    con_precio = request.GET.get('precio') == '1'
    etiquetas_ = []
    for i, pk in enumerate(ids):
        p = productos.get(pk)
        if p is None:
            continue
        n = int(cantidades[i]) if i < len(cantidades) and cantidades[i].isdigit() else 1
        try:
            dibujo = mark_safe(barras.svg(p.codigo_barras or p.codigo, alto=40, modulo=1))
        except barras.ErrorBarras as exc:
            messages.error(request, f'{p.codigo}: {exc}')
            continue
        etiquetas_ += [{'p': p, 'svg': dibujo}] * min(max(n, 1), 500)
    return render(request, 'inventario/barras_etiquetas.html', {
        'etiquetas': etiquetas_, 'con_precio': con_precio, 'elegidos': [productos[i] for i in ids if i in productos]})


@login_required
def asignar(request):
    """Productos sin código de barras: escribir o escanear el del fabricante, o generar un EAN-13 interno."""
    if request.method == 'POST':
        n, errores = 0, []
        with transaction.atomic():
            generar = set(int(x) for x in request.POST.getlist('generar') if x.isdigit())
            for clave, valor in request.POST.items():
                if not clave.startswith('codigo_') or not clave[7:].isdigit():
                    continue
                pk, valor = int(clave[7:]), valor.strip()
                if pk in generar and not valor:
                    valor = barras.ean13_interno(pk)
                if not valor:
                    continue
                if len(valor) > 40:
                    errores.append(f'{valor}: máximo 40 caracteres.')
                    continue
                otro = Producto.objects.filter(codigo_barras=valor).exclude(pk=pk).first()
                if otro is not None:
                    errores.append(f'{valor} ya es el código de {otro.codigo} {otro.nombre}.')
                    continue
                n += Producto.objects.filter(pk=pk, codigo_barras='').update(codigo_barras=valor)
        for e in errores:
            messages.error(request, e)
        if n:
            messages.success(request, f'{n} producto(s) con código de barras.')
        return redirect(request.get_full_path())
    q = (request.GET.get('q') or '').strip()
    qs = Producto.objects.filter(activo=True, es_plantilla=False, codigo_barras='')
    if request.GET.get('tipo') != 'todos':
        qs = qs.filter(tipo='BIEN')
    if q:
        qs = qs.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q))
    pagina = Paginator(qs.order_by('codigo'), 50).get_page(request.GET.get('page'))
    return render(request, 'inventario/barras_asignar.html', {'pagina': pagina, 'q': q,
                                                              'total': qs.count()})
