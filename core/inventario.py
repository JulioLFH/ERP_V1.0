"""Inventario: stock por almacén, kardex valorizado, ajustes y valorización al cierre."""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, UpdateView

from .forms import AjusteInventarioForm, AlmacenForm
from .models import D0, Almacen, Kardex, Producto, r2
from .utils import _fin_de_mes, excel_response, fmt_fecha, rango_por_defecto
from .views import FormGenerico, ListaGenerica


def _signo(k):
    return k.cantidad if k.tipo == 'ENTRADA' else -k.cantidad


@login_required
def stock(request):
    almacenes = list(Almacen.objects.filter(activo=True))
    productos = Producto.objects.filter(activo=True, tipo='BIEN').prefetch_related('stocks')
    q = request.GET.get('q', '').strip()
    if q:
        productos = productos.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q))
    solo_bajo = request.GET.get('bajo') == '1'
    filas = []
    for p in productos:
        por_alm = {s.almacen_id: s.cantidad for s in p.stocks.all()}
        if solo_bajo and not p.bajo_minimo:
            continue
        filas.append({'p': p, 'cantidades': [por_alm.get(a.pk, D0) for a in almacenes]})
    total_valor = sum((f['p'].valorizado for f in filas), D0)
    if request.GET.get('formato') == 'excel':
        enc = ['Código', 'Producto', 'U.M.'] + [a.nombre for a in almacenes] + ['Stock total', 'Stock mínimo',
                                                                                'Costo promedio', 'Valorizado']
        datos = [[f['p'].codigo, f['p'].nombre, f['p'].unidad] + f['cantidades'] +
                 [f['p'].stock, f['p'].stock_minimo, f['p'].costo_promedio, f['p'].valorizado] for f in filas]
        return excel_response('Stock_por_almacen', 'Stock por almacén', enc, datos)
    return render(request, 'inventario/stock.html', {
        'almacenes': almacenes, 'filas': filas, 'q': q, 'solo_bajo': solo_bajo, 'total_valor': total_valor,
        'n_bajo': sum(1 for p in Producto.objects.filter(activo=True, tipo='BIEN') if p.bajo_minimo)})


@login_required
def kardex(request):
    productos = Producto.objects.filter(tipo='BIEN')
    almacenes = Almacen.objects.all()
    producto = productos.filter(pk=request.GET.get('producto')).first() if request.GET.get('producto') else None
    almacen = almacenes.filter(pk=request.GET.get('almacen')).first() if request.GET.get('almacen') else None
    ctx = {'productos': productos, 'almacenes': almacenes, 'producto': producto, 'almacen': almacen}
    if producto:
        qs = producto.kardex.all()
        if almacen:
            qs = qs.filter(almacen=almacen)
        desde, hasta = rango_por_defecto(request, qs)
        movs = list(qs.order_by('fecha', 'id'))
        saldo = sum((_signo(k) for k in movs if k.fecha.isoformat() < desde), D0)
        inicial = saldo
        filas, ent, sal = [], D0, D0
        for k in movs:
            f = k.fecha.isoformat()
            if f < desde:
                continue
            if f > hasta:
                break
            saldo += _signo(k)
            costo_prom = k.costo_promedio or producto.costo_promedio
            valor = r2(k.cantidad * k.costo_unitario)
            ent += k.cantidad if k.tipo == 'ENTRADA' else D0
            sal += k.cantidad if k.tipo == 'SALIDA' else D0
            filas.append({'k': k, 'saldo': saldo, 'costo_prom': costo_prom, 'valor': valor,
                          'saldo_valor': r2(saldo * costo_prom)})
        if request.GET.get('formato') == 'excel':
            enc = ['Fecha', 'Almacén', 'Documento / referencia', 'Tipo', 'Entrada cant.', 'Salida cant.',
                   'Costo unit.', 'Valor', 'Saldo cant.', 'Costo promedio', 'Saldo valorizado']
            datos = [[fmt_fecha(f['k'].fecha), str(f['k'].almacen or ''), f['k'].referencia, f['k'].tipo,
                      f['k'].cantidad if f['k'].tipo == 'ENTRADA' else D0,
                      f['k'].cantidad if f['k'].tipo == 'SALIDA' else D0, f['k'].costo_unitario, f['valor'],
                      f['saldo'], f['costo_prom'], f['saldo_valor']] for f in filas]
            titulo = f'Kardex valorizado {producto.codigo} {producto.nombre} del {desde} al {hasta}'
            return excel_response(f'Kardex_{producto.codigo}', titulo, enc, datos)
        ctx.update(filas=filas, desde=desde, hasta=hasta, inicial=inicial, entradas=ent, salidas=sal, final=saldo)
    return render(request, 'inventario/kardex.html', ctx)


@login_required
def kardex_producto(request, pk):
    """Compatibilidad: enlace del listado de productos."""
    return redirect(f"{reverse('inv_kardex')}?producto={pk}")


@login_required
def ajuste(request):
    form = AjusteInventarioForm(request.POST or None, initial={'fecha': date.today(), 'tipo': 'ENTRADA',
                                                                 'producto': request.GET.get('producto')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        p = d['producto']
        cantidad = d['cantidad'] if d['tipo'] == 'ENTRADA' else -d['cantidad']
        costo = d['costo_unitario'] if d['tipo'] == 'ENTRADA' and d['costo_unitario'] is not None else None
        with transaction.atomic():
            p.mover_stock(cantidad, f'Ajuste: {d["motivo"]}', costo=costo, fecha=d['fecha'], almacen=d['almacen'],
                          origen='AJUSTE')
        messages.success(request, f'Ajuste registrado. Stock de {p.nombre}: {p.stock}')
        return redirect(f"{reverse('inv_kardex')}?producto={p.pk}")
    ultimos = Kardex.objects.filter(referencia__startswith='Ajuste').select_related('producto', 'almacen')[:20]
    return render(request, 'inventario/ajuste.html', {'form': form, 'ultimos': ultimos})


@login_required
def valorizacion(request):
    mes = request.GET.get('mes') or date.today().strftime('%Y-%m')
    try:
        corte = _fin_de_mes(date.fromisoformat(f'{mes}-01'))
    except ValueError:
        corte, mes = _fin_de_mes(date.today()), date.today().strftime('%Y-%m')
    almacenes = Almacen.objects.all()
    almacen = almacenes.filter(pk=request.GET.get('almacen')).first() if request.GET.get('almacen') else None
    filas, total = [], D0
    for p in Producto.objects.filter(tipo='BIEN').order_by('codigo'):
        movs = p.kardex.filter(fecha__lte=corte)
        ultimo = movs.order_by('-fecha', '-id').first()
        # se suma por fecha (no se usa Kardex.saldo: registros con fecha anterior lo desordenan)
        cantidad = sum((_signo(k) for k in (movs.filter(almacen=almacen) if almacen else movs)), D0)
        if not ultimo or cantidad == 0:
            continue
        costo = ultimo.costo_promedio or p.costo_promedio
        valor = r2(cantidad * costo)
        total += valor
        filas.append({'p': p, 'cantidad': cantidad, 'costo': costo, 'valor': valor})
    if request.GET.get('formato') == 'excel':
        enc = ['Código', 'Producto', 'U.M.', 'Cantidad', 'Costo promedio', 'Valor S/']
        datos = [[f['p'].codigo, f['p'].nombre, f['p'].unidad, f['cantidad'], f['costo'], f['valor']] for f in filas]
        datos.append(['', 'TOTAL', '', '', '', total])
        titulo = f'Inventario valorizado al {fmt_fecha(corte)}' + (f' - {almacen}' if almacen else '')
        return excel_response(f'Valorizacion_{mes}', titulo, enc, datos)
    return render(request, 'inventario/valorizacion.html', {
        'filas': filas, 'total': total, 'mes': mes, 'corte': corte, 'almacenes': almacenes, 'almacen': almacen})


class AlmacenLista(ListaGenerica):
    model = Almacen
    titulo = 'Almacenes / establecimientos'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Dirección', 'direccion'), ('Ubigeo', 'ubigeo'),
                ('Cód. SUNAT', 'codigo_sunat'), ('Principal', 'es_principal'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'almacen_nuevo', 'almacen_editar'
    buscar_en = ['codigo', 'nombre']


class AlmacenNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Almacen, AlmacenForm, 'Nuevo almacén'
    success_url = reverse_lazy('almacenes')


class AlmacenEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Almacen, AlmacenForm, 'Editar almacén'
    success_url = reverse_lazy('almacenes')
