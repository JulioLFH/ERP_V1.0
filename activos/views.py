from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView

from core.utils import excel_response
from core.views import FormGenerico, ListaGenerica

from . import servicios
from .forms import ActivoForm, CategoriaForm
from .models import ActivoFijo, CategoriaActivo, ProcesoDepreciacion

D0 = Decimal('0')


# ---------------------------------------------------------------- activos
@login_required
def lista(request):
    qs = ActivoFijo.objects.select_related('categoria', 'centro_costo').prefetch_related('depreciaciones')
    estado = request.GET.get('estado', 'ACTIVO')
    if estado:
        qs = qs.filter(estado=estado)
    if request.GET.get('categoria'):
        qs = qs.filter(categoria_id=request.GET['categoria'])
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q) | Q(serie__icontains=q) |
                       Q(ubicacion__icontains=q) | Q(responsable__icontains=q) | Q(marca__icontains=q))
    activos = list(qs)
    totales = {'valor': sum((a.valor for a in activos), D0),
               'depreciacion': sum((a.depreciacion_registrada for a in activos), D0)}
    totales['neto'] = totales['valor'] - totales['depreciacion']
    if request.GET.get('formato') == 'excel':
        datos = [[a.codigo, a.nombre, a.categoria.nombre, a.marca, a.modelo, a.serie, a.ubicacion, a.responsable,
                  str(a.centro_costo or ''), a.fecha_adquisicion.strftime('%d/%m/%Y'), a.fecha_uso.strftime('%d/%m/%Y'),
                  a.valor, a.depreciacion_registrada, a.valor_neto, a.get_estado_display()] for a in activos]
        return excel_response('Activos_fijos', 'Activos fijos',
                              ['Código', 'Descripción', 'Categoría', 'Marca', 'Modelo', 'Serie / placa', 'Ubicación',
                               'Responsable', 'Centro de costo', 'Adquisición', 'Inicio de uso', 'Valor S/',
                               'Depreciación acumulada', 'Valor neto', 'Estado'], datos)
    return render(request, 'activos/lista.html', {
        'activos': activos, 'totales': totales, 'q': q, 'estado': estado, 'estados': ActivoFijo.ESTADOS,
        'categorias': CategoriaActivo.objects.all()})


def _guardar(request, activo, titulo, compra=None, item=None, maximo=1, valor_unitario=None):
    initial = {}
    if compra and not activo.pk:
        initial = {'nombre': (item.producto.nombre if item and item.producto else item.descripcion if item else ''),
                   'fecha_adquisicion': compra.fecha_emision, 'fecha_uso': compra.fecha_emision,
                   'valor': valor_unitario, 'origen': 'COMPRA', 'centro_costo': compra.centro_costo_id,
                   'unidades': maximo}
    form = ActivoForm(request.POST or None, request.FILES or None, instance=activo, compra=compra, maximo=maximo,
                      initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            nuevo = not activo.pk
            activo = form.save(commit=False)
            if nuevo:
                activo.creado_por = request.user
                if compra:
                    activo.compra, activo.proveedor = compra, compra.tercero
                    activo.documento = f'{compra.get_tipo_comprobante_display()} {compra.numero_completo}'[:60]
                    activo.producto = item.producto if item else None
                    activo.cuenta_origen = servicios.cuenta_de_compra(compra, item)
            activo.save()
            creados = [activo]
            for _ in range((form.cleaned_data.get('unidades') or 1) - 1):  # copias de la misma factura
                copia = ActivoFijo.objects.get(pk=activo.pk)
                copia.pk = copia.id = None
                copia.codigo, copia.serie = '', ''
                copia.save()
                creados.append(copia)
            if form.cleaned_data.get('sustento'):
                from core.sustentos import guardar_archivo, vincular
                archivo = guardar_archivo(form.cleaned_data['sustento'], request.user)
                for c in creados:
                    vincular(c, archivo, request.user, 'Adquisición' if nuevo else '')
        if len(creados) > 1:
            messages.success(request, f'Se registraron {len(creados)} activos ({creados[0].codigo} a '
                                      f'{creados[-1].codigo}). Complete la serie de cada uno.')
        else:
            messages.success(request, f'Activo {activo.codigo} guardado.')
        return redirect('activos:detalle', activo.pk)
    vidas = {c.pk: c.vida_util_meses for c in CategoriaActivo.objects.filter(activo=True)}
    return render(request, 'activos/form.html', {'form': form, 'titulo': titulo, 'activo': activo, 'compra': compra,
                                                 'item': item, 'vidas': vidas})


@login_required
def nuevo(request):
    compra = item = None
    maximo, valor = 1, None
    if request.GET.get('compra'):
        from compras.models import Compra
        compra = get_object_or_404(Compra, pk=request.GET['compra'])
        pendientes = {i.pk: (i, n, v) for i, n, v in servicios.items_compra(compra)}
        fila = pendientes.get(int(request.GET.get('item') or 0)) or next(iter(pendientes.values()), None)
        if not fila:
            messages.warning(request, f'{compra}: no tiene activos fijos pendientes de registrar.')
            return redirect('compras:detalle', compra.pk)
        item, maximo, valor = fila
    return _guardar(request, ActivoFijo(), 'Nuevo activo fijo', compra, item, maximo, valor)


@login_required
def editar(request, pk):
    activo = get_object_or_404(ActivoFijo, pk=pk)
    if activo.estado != 'ACTIVO':
        messages.error(request, 'Solo se editan activos en uso.')
        return redirect('activos:detalle', pk)
    return _guardar(request, activo, f'Editar {activo}')


@login_required
def detalle(request, pk):
    activo = get_object_or_404(ActivoFijo.objects.select_related(
        'categoria', 'centro_costo', 'compra', 'proveedor', 'creado_por', 'baja_por', 'cuenta_origen'), pk=pk)
    depreciaciones = list(activo.depreciaciones.select_related('proceso'))
    for d in depreciaciones:
        d.neto = activo.valor - d.acumulada
    ctx = {'a': activo, 'depreciaciones': depreciaciones, 'proyeccion': servicios.cronograma(activo)[:60],
           'motivos': ActivoFijo.MOTIVOS_BAJA, 'hoy': timezone.localdate(),
           'excede_tasa': activo.deprecia and activo.categoria.tasa_anual and activo.tasa > activo.categoria.tasa_anual}
    return render(request, 'activos/detalle.html', ctx)


@login_required
def baja(request, pk):
    activo = get_object_or_404(ActivoFijo, pk=pk)
    if request.method == 'POST':
        try:
            fecha = date.fromisoformat(request.POST.get('fecha', ''))
        except ValueError:
            fecha = None
        try:
            servicios.dar_de_baja(activo, request.user, fecha, request.POST.get('motivo', ''),
                                  request.POST.get('detalle', ''), request.FILES.get('archivo'))
            messages.success(request, f'{activo.codigo} dado de baja. Valor neto S/ {activo.valor_neto:,.2f} al gasto '
                                      '(se registra al centralizar el periodo).')
        except servicios.ErrorActivo as exc:
            messages.error(request, f'No se pudo dar de baja: {exc}')
    return redirect('activos:detalle', pk)


@login_required
def anular(request, pk):
    activo = get_object_or_404(ActivoFijo, pk=pk)
    if request.method == 'POST':
        try:
            servicios.anular(activo, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{activo.codigo} anulado.')
        except servicios.ErrorActivo as exc:
            messages.error(request, f'No se pudo anular: {exc}')
    return redirect('activos:detalle', pk)


# ---------------------------------------------------------------- depreciación
@login_required
def depreciacion(request):
    if request.method == 'POST':
        try:
            p = servicios.depreciar(request.POST.get('periodo', '').replace('-', ''), request.user)
            messages.success(request, f'{p}: {p.cantidad} activos, S/ {p.total:,.2f}. Se contabiliza al centralizar '
                                      'el periodo.')
            return redirect('activos:proceso', p.pk)
        except servicios.ErrorActivo as exc:
            messages.error(request, str(exc))
        return redirect('activos:depreciacion')
    siguiente = servicios.periodo_siguiente()
    return render(request, 'activos/depreciacion.html', {
        'procesos': ProcesoDepreciacion.objects.select_related('usuario')[:36], 'siguiente': siguiente,
        'siguiente_input': f'{siguiente[:4]}-{siguiente[4:]}' if siguiente else '',
        'libre': servicios.ultimo_proceso() is None,
        'siguiente_texto': servicios.texto_periodo(siguiente) if siguiente else '',
        'futuro': bool(siguiente) and siguiente > servicios.periodo_de(timezone.localdate())})


@login_required
def proceso(request, pk):
    p = get_object_or_404(ProcesoDepreciacion.objects.select_related('usuario'), pk=pk)
    lineas = p.lineas.select_related('activo__categoria', 'activo__centro_costo')
    if request.GET.get('formato') == 'excel':
        datos = [[l.activo.codigo, l.activo.nombre, l.activo.categoria.nombre, str(l.activo.centro_costo or ''),
                  l.activo.valor, l.meses, l.cuota, l.acumulada, l.activo.valor - l.acumulada] for l in lineas]
        return excel_response(f'Depreciacion_{p.periodo}', str(p),
                              ['Código', 'Activo', 'Categoría', 'Centro de costo', 'Valor S/', 'Meses', 'Depreciación',
                               'Acumulada', 'Valor neto'], datos)
    return render(request, 'activos/proceso.html', {'p': p, 'lineas': lineas,
                                                    'es_ultimo': p == servicios.ultimo_proceso()})


@login_required
def proceso_revertir(request, pk):
    p = get_object_or_404(ProcesoDepreciacion, pk=pk)
    if request.method == 'POST':
        try:
            texto = str(p)
            servicios.revertir(p, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{texto} revertida.')
            return redirect('activos:depreciacion')
        except servicios.ErrorActivo as exc:
            messages.error(request, str(exc))
    return redirect('activos:proceso', pk)


# ---------------------------------------------------------------- reportes
@login_required
def registro(request):
    anio = int(request.GET.get('anio') or timezone.localdate().year)
    filas, totales = servicios.registro(anio)
    if request.GET.get('formato') == 'excel':
        datos = [[f['a'].codigo, f['a'].categoria.cuenta_activo.codigo, f['a'].nombre, f['a'].marca, f['a'].modelo,
                  f['a'].serie, f['saldo_inicial'], f['adquisiciones'], 0, f['retiros'], 0, f['historico'], 0,
                  f['historico'], f['a'].fecha_adquisicion.strftime('%d/%m/%Y'), f['a'].fecha_uso.strftime('%d/%m/%Y'),
                  'Lineal', '', f['a'].tasa, f['dep_anterior'], f['dep_ejercicio'], f['dep_retiros'], 0,
                  f['dep_acumulada'], 0, f['dep_acumulada']] for f in filas]
        t = totales
        datos.append(['', '', 'TOTALES', '', '', '', t['saldo_inicial'], t['adquisiciones'], 0, t['retiros'], 0,
                      t['historico'], 0, t['historico'], '', '', '', '', '', t['dep_anterior'], t['dep_ejercicio'],
                      t['dep_retiros'], 0, t['dep_acumulada'], 0, t['dep_acumulada']])
        return excel_response(f'Formato_7_1_{anio}', f'Formato 7.1 Registro de activos fijos {anio}',
                              ['Código', 'Cuenta', 'Descripción', 'Marca', 'Modelo', 'N° serie / placa',
                               'Saldo inicial', 'Adquisiciones', 'Mejoras', 'Retiros y/o bajas', 'Otros ajustes',
                               'Valor histórico al 31.12', 'Ajuste por inflación', 'Valor ajustado al 31.12',
                               'Fecha de adquisición', 'Inicio de uso', 'Método', 'N° documento de autorización',
                               '% depreciación', 'Dep. acumulada al cierre anterior', 'Dep. del ejercicio',
                               'Dep. de retiros y/o bajas', 'Dep. otros ajustes', 'Dep. acumulada histórica',
                               'Ajuste por inflación de la dep.', 'Dep. acumulada ajustada'], datos)
    return render(request, 'activos/registro.html', {'filas': filas, 'totales': totales, 'anio': anio,
                                                     'anios': range(timezone.localdate().year, 2015, -1)})


@login_required
def cuadre(request):
    try:
        fecha = date.fromisoformat(request.GET.get('fecha', ''))
    except ValueError:
        fecha = timezone.localdate()
    return render(request, 'activos/cuadre.html', {'filas': servicios.cuadre(fecha), 'fecha': fecha})


# ---------------------------------------------------------------- categorías
class CategoriaLista(ListaGenerica):
    model = CategoriaActivo
    titulo = 'Categorías de activos fijos'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Cuenta activo', 'cuenta_activo'),
                ('Dep. acumulada', 'cuenta_depreciacion'), ('Gasto', 'cuenta_gasto'), ('Tasa SUNAT %', 'tasa_anual'),
                ('Vida útil (meses)', 'vida_util_meses'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'activos:categoria_nueva', 'activos:categoria_editar'
    buscar_en = ['codigo', 'nombre']


class CategoriaNueva(FormGenerico, CreateView):
    model, form_class, titulo = CategoriaActivo, CategoriaForm, 'Nueva categoría de activo'
    success_url = reverse_lazy('activos:categorias')


class CategoriaEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CategoriaActivo, CategoriaForm, 'Editar categoría de activo'
    success_url = reverse_lazy('activos:categorias')
