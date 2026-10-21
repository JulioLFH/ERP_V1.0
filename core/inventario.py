"""Inventario: stock por almacén, kardex valorizado, ajustes y valorización al cierre."""
from collections import defaultdict
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
from django.utils import timezone


def _signo(k):
    return k.cantidad if k.tipo == 'ENTRADA' else -k.cantidad


def _sin_permiso_costos(request):
    return render(request, 'core/sin_acceso.html', {'motivo': 'Para ver los costos e inventario valorizado necesita el '
                                                              'permiso "Ver costos de inventario" (Ajustes > Usuarios '
                                                              'y permisos).'}, status=403)


@login_required
def stock(request):
    from .modulos import puede_ver_costos
    costos = puede_ver_costos(request.user)
    from core.models import StockAlmacen
    todos = request.GET.get('todos') == '1'
    productos = Producto.objects.filter(activo=True, tipo='BIEN', es_plantilla=False).prefetch_related('stocks')
    if not todos:  # por defecto solo lo que tiene stock (la lista completa puede tener miles de productos)
        productos = productos.exclude(stock=0)
    # columnas: los almacenes con mercadería; con «Incluir almacenes sin stock» (o todos los productos), todos los
    # activos aunque estén en cero
    from .permisos import almacenes_permitidos
    permitidos = almacenes_permitidos(request.user)  # usuario limitado a ciertos almacenes: solo ve esos
    con_stock = set(StockAlmacen.objects.exclude(cantidad=0).values_list('almacen_id', flat=True))
    vacios = todos or request.GET.get('vacios') == '1'
    activos = [a for a in Almacen.objects.filter(activo=True) if permitidos is None or a.pk in permitidos]
    almacenes = [a for a in activos if vacios or a.pk in con_stock]
    q = request.GET.get('q', '').strip()
    if q:
        productos = productos.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q))
    solo_bajo = request.GET.get('bajo') == '1'
    filas = []
    for p in productos:
        por_alm = {s.almacen_id: s.cantidad for s in p.stocks.all()}
        if solo_bajo and not p.bajo_minimo:
            continue
        if permitidos is None:
            total = p.stock
        else:
            total = sum((c for a, c in por_alm.items() if a in permitidos), D0)
            if not total and not todos:
                continue
        filas.append({'p': p, 'cantidades': [por_alm.get(a.pk, D0) for a in almacenes], 'total': total,
                      'valor': p.valorizado if permitidos is None else r2(total * (p.costo_promedio or D0))})
    total_valor = sum((f['valor'] for f in filas), D0)
    if request.GET.get('formato') == 'excel':
        enc = ['Código', 'Producto', 'U.M.'] + [a.nombre for a in almacenes] + ['Stock total', 'Stock mínimo'] + (
            ['Costo promedio', 'Valorizado'] if costos else [])
        datos = [[f['p'].codigo, f['p'].nombre, f['p'].unidad] + f['cantidades'] + [f['total'], f['p'].stock_minimo]
                 + ([f['p'].costo_promedio, f['valor']] if costos else []) for f in filas]
        return excel_response('Stock_por_almacen', 'Stock por almacén', enc, datos)
    from django.core.paginator import Paginator
    return render(request, 'inventario/stock.html', {
        'almacenes': almacenes, 'page_obj': Paginator(filas, 100).get_page(request.GET.get('page')),
        'n_filas': len(filas), 'q': q, 'solo_bajo': solo_bajo, 'todos': todos, 'vacios': vacios,
        'n_activos': len(activos), 'sin_stock': [a for a in activos if a.pk not in con_stock],
        'total_valor': total_valor if costos else None,
        'n_bajo': sum(1 for p in Producto.objects.filter(activo=True, tipo='BIEN') if p.bajo_minimo)})


@login_required
def kardex(request):
    from .permisos import almacenes_permitidos
    permitidos = almacenes_permitidos(request.user)  # usuario limitado a ciertos almacenes: solo sus movimientos
    productos = Producto.objects.filter(tipo='BIEN')
    almacenes = Almacen.objects.all() if permitidos is None else Almacen.objects.filter(pk__in=permitidos)
    producto = productos.filter(pk=request.GET.get('producto')).first() if request.GET.get('producto') else None
    almacen = almacenes.filter(pk=request.GET.get('almacen')).first() if request.GET.get('almacen') else None
    from inventario.cierre import ultimo_cierre
    ctx = {'productos': productos, 'almacenes': almacenes, 'producto': producto, 'almacen': almacen,
           'cierre': ultimo_cierre()}
    if producto:
        qs = producto.kardex.all()
        if almacen:
            qs = qs.filter(almacen=almacen)
        elif permitidos is not None:
            qs = qs.filter(almacen_id__in=permitidos)
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
            from .modulos import puede_ver_costos
            costos = puede_ver_costos(request.user)
            enc = ['Fecha', 'Almacén', 'Documento / referencia', 'Tipo', 'Tabla 12 SUNAT', 'Entrada cant.',
                   'Salida cant.'] + (['Costo unit.', 'Valor'] if costos else []) + ['Saldo cant.'] + (
                ['Costo promedio', 'Saldo valorizado'] if costos else [])
            datos = [[fmt_fecha(f['k'].fecha), str(f['k'].almacen or ''), f['k'].referencia, f['k'].tipo,
                      f['k'].codigo_sunat,
                      f['k'].cantidad if f['k'].tipo == 'ENTRADA' else D0,
                      f['k'].cantidad if f['k'].tipo == 'SALIDA' else D0]
                     + ([f['k'].costo_unitario, f['valor']] if costos else []) + [f['saldo']]
                     + ([f['costo_prom'], f['saldo_valor']] if costos else []) for f in filas]
            titulo = (f'Kardex {"valorizado " if costos else ""}{producto.codigo} {producto.nombre} '
                      f'del {desde} al {hasta}')
            return excel_response(f'Kardex_{producto.codigo}', titulo, enc, datos)
        ctx.update(filas=filas, desde=desde, hasta=hasta, inicial=inicial, entradas=ent, salidas=sal, final=saldo)
    return render(request, 'inventario/kardex.html', ctx)


@login_required
def kardex_producto(request, pk):
    """Compatibilidad: enlace del listado de productos."""
    return redirect(f"{reverse('inv_kardex')}?producto={pk}")


TIPO_AJUSTE = {'INICIAL': 'SALDO_INI', 'SOBRANTE': 'AJ_ING', 'MERMA': 'AJ_SAL', 'CONSUMO': 'CONS_INT'}


@login_required
def ajuste(request):
    """Ajuste rápido: registra y confirma una operación de inventario (saldo inicial, ajuste o consumo) con su
    documento de sustento, para que quede numerada, auditada y anulable."""
    from inventario import servicios
    from inventario.models import Operacion, TipoOperacion
    from .sustentos import adjuntar
    form = AjusteInventarioForm(request.POST or None, request.FILES or None,
                                initial={'fecha': timezone.localdate(), 'tipo': 'ENTRADA', 'producto': request.GET.get('producto')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        tipo = TipoOperacion.objects.get(codigo=TIPO_AJUSTE[d['concepto']])
        try:
            with transaction.atomic():
                op = Operacion.objects.create(
                    tipo=tipo, fecha=d['fecha'], referencia=d['motivo'][:60], glosa=d['motivo'], creado_por=request.user,
                    **({'almacen_destino': d['almacen']} if d['tipo'] == 'ENTRADA' else {'almacen_origen': d['almacen']}))
                op.items.create(producto=d['producto'], cantidad=d['cantidad'],
                                costo_unitario=d['costo_unitario'] if d['tipo'] == 'ENTRADA' else None)
                adjuntar(op, d['sustento'], request.user, d['motivo'])
                servicios.confirmar(op, request.user)
        except servicios.ErrorOperacion as exc:
            messages.error(request, f'No se registró el ajuste: {exc}')
        else:
            messages.success(request, f'{tipo.nombre} {op.numero} registrado con su sustento.')
            return redirect('inventario:detalle', op.pk)
    ultimos = Operacion.objects.filter(tipo__codigo__in=TIPO_AJUSTE.values()).select_related(
        'tipo', 'almacen_origen', 'almacen_destino').prefetch_related('items__producto')[:20]
    return render(request, 'inventario/ajuste.html', {'form': form, 'ultimos': ultimos})


def en_camino():
    """{producto_id: cantidad} pedida en órdenes de compra vigentes y aún no recibida en el almacén."""
    from collections import defaultdict
    from compras.models import OrdenCompra
    from proveedores.servicios import recibido_por_ordenes
    pendiente = defaultdict(lambda: D0)
    factores = {}
    # del sistema anterior solo cuentan las órdenes abiertas (las cerradas ya se recibieron allá)
    ocs = list(OrdenCompra.objects.exclude(estado='ANULADO').exclude(estado_proveedor='RECHAZADA')
               .exclude(Q(glosa__startswith='[Sistema anterior]') & ~Q(estado='APROBADO'))
               .prefetch_related('items__producto'))
    recibidos = recibido_por_ordenes(oc.pk for oc in ocs)  # en unidad de compra, como el pedido
    for oc in ocs:
        pedido = defaultdict(lambda: D0)
        for i in oc.items.all():
            if i.producto_id:
                pedido[i.producto_id] += i.cantidad
                factores[i.producto_id] = i.producto.factor
        if not pedido:
            continue
        recibido = recibidos.get(oc.pk, {})
        for pid, cant in pedido.items():
            falta = cant - recibido.get(pid, D0)
            if falta > 0:
                pendiente[pid] += falta * factores.get(pid, 1)  # en unidad de almacén
    return pendiente


def solicitado():
    """{producto_id: cantidad} de solicitudes de pedido por liberar o liberadas que aún no se pidió en una OC."""
    from collections import defaultdict
    from compras.models import LineaSolicitud
    pendiente = defaultdict(lambda: D0)
    for l in (LineaSolicitud.objects.filter(solicitud__estado__in=['ENVIADA', 'OBSERVADA', 'LIBERADA', 'PARCIAL'],
                                            producto__isnull=False, cerrada=False)
              .prefetch_related('items_oc__documento', 'items_oc__producto')):
        pendiente[l.producto_id] += l.pendiente
    return pendiente


def sugerencias_compra():
    """Productos a reponer: stock + en camino (órdenes de compra y solicitudes de pedido) ≤ punto de reorden (o
    stock mínimo). Cantidad sugerida hasta el stock máximo (o el doble del punto de reorden), redondeada al lote
    mínimo de compra."""
    import math
    camino = en_camino()
    for pid, cantidad in solicitado().items():
        camino[pid] += cantidad
    filas = []
    # lo que se fabrica no se compra: lo repone el MRP con una orden de producción
    from produccion.models import VersionFabricacion
    fabricados = set(VersionFabricacion.objects.values_list('producto_id', flat=True))
    for p in Producto.objects.filter(activo=True, tipo='BIEN', puede_comprarse=True, es_plantilla=False) \
            .exclude(clase__in=['PRODUCTO_TERMINADO', 'SEMIELABORADO']).exclude(pk__in=fabricados) \
            .select_related('proveedor'):
        limite = p.punto_reorden or p.stock_minimo
        if not limite:
            continue
        disponible = p.stock + camino.get(p.pk, D0)
        if disponible > limite:
            continue
        objetivo = p.stock_maximo if p.stock_maximo > limite else limite * 2
        # se pide en unidad de compra (cajas) y en múltiplos del lote mínimo de compra
        cantidad = p.a_compra(objetivo - disponible)
        if p.lote_compra and p.lote_compra > 0:
            cantidad = Decimal(math.ceil(cantidad / p.lote_compra)) * p.lote_compra
        if cantidad <= 0:
            continue
        filas.append({'p': p, 'limite': limite, 'camino': camino.get(p.pk, D0), 'disponible': disponible,
                      'objetivo': objetivo, 'cantidad': cantidad, 'unidad_compra': p.unidad_de_compra,
                      'unidades': p.a_stock(cantidad), 'precio': p.precio_compra,
                      'importe': r2(cantidad * p.precio_compra)})
    return filas


@login_required
def reposicion(request):
    from collections import OrderedDict
    filas = sugerencias_compra()
    if request.method == 'POST':  # solicitudes de pedido por proveedor habitual (o OC directa con contrato marco)
        from compras.solped import ErrorSolped, desde_reposicion
        from contabilidad.models import CentroCosto
        from .permisos import puede
        elegidos = set(request.POST.getlist('producto'))
        centro = CentroCosto.objects.filter(pk=request.POST.get('centro_costo') or 0).first()
        try:
            if not puede(request.user, 'requerimientos.solped'):
                raise ErrorSolped('Su usuario no registra solicitudes de pedido.')
            solicitudes, ordenes = desde_reposicion([f for f in filas if str(f['p'].pk) in elegidos], request.user,
                                                    centro)
            if not (solicitudes or ordenes):
                raise ErrorSolped('Marque los productos a reponer.')
            messages.success(request, 'Solicitudes de pedido enviadas a liberación: ' +
                             (', '.join(s.numero for s in solicitudes) or 'ninguna') +
                             ('. Con contrato marco, órdenes de compra: ' + ', '.join(o.numero for o in ordenes)
                              if ordenes else ''))
        except ErrorSolped as exc:
            messages.error(request, str(exc))
        return redirect('inv_reposicion')
    grupos = OrderedDict()
    for f in sorted(filas, key=lambda f: (f['p'].proveedor.nombre if f['p'].proveedor else 'ZZZ', f['p'].nombre)):
        clave = f['p'].proveedor_id or 0
        g = grupos.setdefault(clave, {'proveedor': f['p'].proveedor, 'filas': [], 'total': D0})
        g['filas'].append(f)
        g['total'] += f['importe']
    if request.GET.get('formato') == 'excel':
        datos = [[f['p'].codigo, f['p'].nombre, f['p'].unidad, f['p'].stock, f['camino'], f['limite'], f['objetivo'],
                  f['cantidad'], f['p'].proveedor.nombre if f['p'].proveedor else '', f['p'].tiempo_entrega]
                 for f in filas]
        return excel_response('Sugerencia_de_compra', 'Sugerencia de compra (reposición)',
                              ['Código', 'Producto', 'U.M.', 'Stock', 'En camino (OC y solicitudes)', 'Punto de reorden',
                               'Stock objetivo', 'Cantidad sugerida', 'Proveedor habitual', 'Entrega (días)'], datos)
    from contabilidad.models import CentroCosto
    return render(request, 'inventario/reposicion.html', {'grupos': grupos, 'total_filas': len(filas),
                                                          'centros': CentroCosto.objects.filter(activo=True)})


def valor_inventario(corte, almacen=None, permitidos=None):
    """([filas], total) del inventario valorizado al corte. La contabilidad ajusta la 20111 a este total.
    permitidos: ids de almacenes del usuario (None = todos)."""
    filas, total = [], D0
    movimientos = defaultdict(list)
    qs = Kardex.objects.filter(fecha__lte=corte, producto__tipo='BIEN').order_by('fecha', 'id')
    for k in qs.only('producto_id', 'almacen_id', 'tipo', 'cantidad', 'costo_promedio', 'fecha'):
        movimientos[k.producto_id].append(k)
    for p in Producto.objects.filter(pk__in=movimientos).order_by('codigo'):
        movs = movimientos[p.pk]
        # se suma por fecha (no se usa Kardex.saldo: registros con fecha anterior lo desordenan)
        cantidad = sum((_signo(k) for k in movs if (not almacen or k.almacen_id == almacen.pk)
                        and (permitidos is None or k.almacen_id in permitidos)), D0)
        if cantidad == 0:
            continue
        costo = movs[-1].costo_promedio or p.costo_promedio
        valor = r2(cantidad * costo)
        total += valor
        filas.append({'p': p, 'cantidad': cantidad, 'costo': costo, 'valor': valor})
    return filas, total


@login_required
def valorizacion(request):
    from .modulos import puede_ver_costos
    if not puede_ver_costos(request.user):
        return _sin_permiso_costos(request)
    mes = request.GET.get('mes') or timezone.localdate().strftime('%Y-%m')
    try:
        corte = _fin_de_mes(date.fromisoformat(f'{mes}-01'))
    except ValueError:
        corte, mes = _fin_de_mes(timezone.localdate()), timezone.localdate().strftime('%Y-%m')
    from .permisos import almacenes_permitidos
    permitidos = almacenes_permitidos(request.user)  # usuario limitado: solo sus almacenes
    almacenes = Almacen.objects.all() if permitidos is None else Almacen.objects.filter(pk__in=permitidos)
    almacen = almacenes.filter(pk=request.GET.get('almacen')).first() if request.GET.get('almacen') else None
    filas, total = valor_inventario(corte, almacen, permitidos)
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
                ('Cód. SUNAT', 'codigo_sunat'), ('Uso', 'get_uso_display'), ('Principal', 'es_principal'),
                ('Activo', 'activo')]
    url_nuevo, url_editar = 'almacen_nuevo', 'almacen_editar'
    buscar_en = ['codigo', 'nombre']


class AlmacenNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Almacen, AlmacenForm, 'Nuevo almacén'
    success_url = reverse_lazy('almacenes')

    def get_initial(self):
        # suele estar en el mismo local: se propone la dirección del principal (punto de partida de las guías)
        principal = Almacen.objects.filter(es_principal=True).first()
        return {'direccion': principal.direccion, 'ubigeo': principal.ubigeo} if principal else {}


class AlmacenEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Almacen, AlmacenForm, 'Editar almacén'
    success_url = reverse_lazy('almacenes')


# ---------------------------------------------------------------- ubicaciones dentro del almacén
def _asignar_ubicacion(almacen, producto, ubicacion):
    from .models import StockAlmacen
    s, _ = StockAlmacen.objects.get_or_create(producto=producto, almacen=almacen)
    s.ubicacion = ubicacion
    s.save(update_fields=['ubicacion'])


@login_required
def ubicaciones(request):
    """Ubicaciones (zona-rack-nivel) de cada almacén, ubicación habitual de cada producto y hoja de conteo."""
    from .models import StockAlmacen, Ubicacion
    almacenes = Almacen.objects.filter(activo=True)
    almacen = almacenes.filter(pk=request.GET.get('almacen') or request.POST.get('almacen')).first() or \
        Almacen.principal()
    if request.GET.get('plantilla'):
        return excel_response('Plantilla_ubicaciones', 'plantilla', ['codigo_producto', 'ubicacion'],
                              [['MP000001', 'A-01-01']])
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'nueva':
            codigo = request.POST.get('codigo', '').strip().upper()[:20]
            if not codigo:
                messages.error(request, 'Indique el código de la ubicación.')
            else:
                _, creada = Ubicacion.objects.get_or_create(almacen=almacen, codigo=codigo, defaults={
                    'descripcion': request.POST.get('descripcion', '')[:100]})
                messages.success(request, f'Ubicación {codigo} {"creada" if creada else "ya existía"}.')
        elif accion == 'asignar':
            producto = Producto.objects.filter(pk=request.POST.get('producto')).first()
            ubic = Ubicacion.objects.filter(pk=request.POST.get('ubicacion'), almacen=almacen).first()
            if producto is None:
                messages.error(request, 'Elija el producto.')
            else:
                _asignar_ubicacion(almacen, producto, ubic)
                messages.success(request, f'{producto.codigo} → {ubic or "sin ubicación"} en {almacen}.')
        elif accion == 'importar' and request.FILES.get('archivo'):
            from .utils import leer_excel
            ok, errores = 0, []
            try:
                filas = leer_excel(request.FILES['archivo'])
            except Exception as exc:
                filas, errores = [], [f'No se pudo leer el archivo: {exc}']
            productos = {p.codigo.upper(): p for p in Producto.objects.filter(
                codigo__in=[str(f.get('codigo_producto') or '').strip().upper() for f in filas])}
            with transaction.atomic():
                for n, f in enumerate(filas, 2):
                    codigo = str(f.get('codigo_producto') or '').strip().upper()
                    lugar = str(f.get('ubicacion') or '').strip().upper()[:20]
                    if not codigo:
                        continue
                    p = productos.get(codigo)
                    if p is None:
                        errores.append(f'Fila {n}: no existe el producto {codigo}')
                        continue
                    ubic = Ubicacion.objects.get_or_create(almacen=almacen, codigo=lugar)[0] if lugar else None
                    _asignar_ubicacion(almacen, p, ubic)
                    ok += 1
            messages.success(request, f'{ok} productos ubicados en {almacen}.')
            for e in errores[:10]:
                messages.warning(request, e)
        elif accion == 'eliminar':
            ubic = Ubicacion.objects.filter(pk=request.POST.get('ubicacion'), almacen=almacen).first()
            if ubic and ubic.stocks.exists():
                ubic.activo = False
                ubic.save(update_fields=['activo'])
                messages.info(request, f'{ubic} tiene productos asignados: quedó inactiva.')
            elif ubic:
                ubic.delete()
                messages.success(request, 'Ubicación eliminada.')
        return redirect(f'{reverse("ubicaciones")}?almacen={almacen.pk}')
    from django.db.models import Count
    lista = Ubicacion.objects.filter(almacen=almacen).annotate(n=Count('stocks'))
    stocks = (StockAlmacen.objects.filter(almacen=almacen).filter(Q(ubicacion__isnull=False) | ~Q(cantidad=0))
              .select_related('producto', 'ubicacion').order_by('ubicacion__codigo', 'producto__nombre'))
    filtro = request.GET.get('ubic', '')
    if filtro == 'sin':
        stocks = stocks.filter(ubicacion=None)
    elif filtro:
        stocks = stocks.filter(ubicacion_id=filtro)
    if request.GET.get('formato') == 'excel':  # hoja de conteo físico por ubicación
        return excel_response(f'Conteo_{almacen.codigo}', f'HOJA DE CONTEO - {almacen}', [
            'Ubicación', 'Código', 'Producto', 'U.M.', 'Stock sistema', 'Conteo físico', 'Diferencia'],
            [[s.ubicacion.codigo if s.ubicacion else '(sin ubicación)', s.producto.codigo, s.producto.nombre,
              s.producto.unidad, s.cantidad, '', ''] for s in stocks])
    from django.core.paginator import Paginator
    return render(request, 'inventario/ubicaciones.html', {
        'almacenes': almacenes, 'almacen': almacen, 'ubicaciones': lista, 'filtro': filtro,
        'page_obj': Paginator(stocks, 100).get_page(request.GET.get('page')),
        'sin_ubicacion': StockAlmacen.objects.filter(almacen=almacen, ubicacion=None).exclude(cantidad=0).count()})
