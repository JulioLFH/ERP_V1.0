"""Obras, fase 1: obras, recursos, presupuestos por versiones con partidas y análisis de precios unitarios,
precios, explosión de insumos, reportes e importación desde Excel (S10 o plantilla)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView

from core.utils import excel_response
from core.views import FormGenerico, ListaGenerica

from . import calculos
from . import importar as imp
from .forms import ImportarForm, ObraForm, PartidaForm, PieForm, PresupuestoForm, RecursoForm
from .models import Obra, Partida, PartidaBase, PrecioPresupuesto, Presupuesto, Recurso, RecursoAPU, RecursoBase

SESION_IMPORTACION = 'obras_importacion'


def _decimal(valor, defecto=None):
    texto = (valor or '').strip().replace(',', '')
    if not texto:
        return defecto
    try:
        return Decimal(texto)
    except InvalidOperation:
        return defecto


# ---------------------------------------------------------------- obras
@login_required
def obras_lista(request):
    qs = Obra.objects.select_related('cliente').prefetch_related('presupuestos')
    q, estado = request.GET.get('q', '').strip(), request.GET.get('estado', '')
    if q:
        qs = qs.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q) | Q(cliente__nombre__icontains=q))
    if estado:
        qs = qs.filter(estado=estado)
    return render(request, 'obras/lista.html', {'obras': qs, 'q': q, 'estado': estado, 'estados': Obra.ESTADOS})


class ObraNueva(FormGenerico, CreateView):
    model, form_class, titulo = Obra, ObraForm, 'Nueva obra'

    def get_success_url(self):
        return reverse_lazy('obras:obra', args=[self.object.pk])


class ObraEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Obra, ObraForm, 'Editar obra'

    def get_success_url(self):
        return reverse_lazy('obras:obra', args=[self.object.pk])


@login_required
def obra(request, pk):
    o = get_object_or_404(Obra.objects.select_related('cliente', 'centro_costo', 'almacen', 'residente'), pk=pk)
    return render(request, 'obras/obra.html', {'o': o, 'presupuestos': o.presupuestos.all(),
                                               'tipos': Presupuesto.TIPOS})


@login_required
def presupuesto_nuevo(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    form = PresupuestoForm(request.POST or None, initial={
        'gastos_generales_pct': o.gastos_generales_pct, 'utilidad_pct': o.utilidad_pct, 'nombre': o.nombre,
        'tipo': request.GET.get('tipo', 'OFERTA')})
    if request.method == 'POST' and form.is_valid():
        p = form.save(commit=False)
        p.obra = o
        p.save()
        messages.success(request, f'Presupuesto {p} creado. Agregue títulos y partidas, o impórtelo desde Excel.')
        return redirect('obras:presupuesto', p.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': f'Nuevo presupuesto · {o}'})


# ---------------------------------------------------------------- presupuesto
@login_required
def presupuesto(request, pk):
    p = get_object_or_404(Presupuesto.objects.select_related('obra'), pk=pk)
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if request.method == 'POST' and request.POST.get('eliminar'):  # botón de la fila (manda sobre «metrados»)
        accion = 'eliminar'
    if accion:
        respuesta = _accion_presupuesto(request, p, accion)
        if respuesta:
            return respuesta
        return redirect('obras:presupuesto', p.pk)
    partidas = list(p.partidas.all())
    if request.GET.get('formato') == 'excel':
        filas = [[x.item, x.descripcion, x.unidad, None if x.es_titulo else x.metrado,
                  None if x.es_titulo else x.precio, x.parcial] for x in partidas]
        filas += [['', 'COSTO DIRECTO', '', '', '', p.costo_directo],
                  ['', f'GASTOS GENERALES ({p.gastos_generales_pct}%)', '', '', '', p.gastos_generales],
                  ['', f'UTILIDAD ({p.utilidad_pct}%)', '', '', '', p.utilidad], ['', 'SUBTOTAL', '', '', '', p.subtotal],
                  ['', f'IGV ({p.igv_pct}%)', '', '', '', p.igv], ['', 'TOTAL PRESUPUESTO', '', '', '', p.total]]
        return excel_response(f'Presupuesto_{p.obra.codigo}_{p.tipo}_v{p.version}', f'PRESUPUESTO {p} · {p.obra.nombre}',
                              ['Ítem', 'Descripción', 'Und.', 'Metrado', 'Precio', 'Parcial'], filas)
    sin_apu = sum(1 for x in partidas if not x.es_titulo and not x.precio_fijo and not x.recursos.exists())
    return render(request, 'obras/presupuesto.html', {
        'p': p, 'partidas': partidas, 'form': PartidaForm(presupuesto=p, initial={'item': _siguiente_item(partidas)}),
        'pie': PieForm(instance=p), 'tipos': Presupuesto.TIPOS, 'sin_apu': sin_apu,
        'imprimir': request.GET.get('imprimir') == '1'})


def _siguiente_item(partidas):
    if not partidas:
        return '01'
    ultimo = partidas[-1].item.split('.')
    ultimo[-1] = str(int(ultimo[-1]) + 1).zfill(len(ultimo[-1]))
    return '.'.join(ultimo)


def _accion_presupuesto(request, p, accion):
    if accion == 'copiar':
        tipo = request.POST.get('tipo') if request.POST.get('tipo') in dict(Presupuesto.TIPOS) else p.tipo
        nuevo = calculos.copiar(p, tipo)
        messages.success(request, f'Se creó {nuevo} a partir de {p}.')
        return redirect('obras:presupuesto', nuevo.pk)
    if not p.editable:
        messages.error(request, 'El presupuesto está aprobado: cópielo a una nueva versión para cambiarlo.')
        return None
    if accion == 'agregar':
        form = PartidaForm(request.POST, presupuesto=p)
        if form.is_valid():
            partida = form.save(commit=False)
            partida.presupuesto = p
            partida.save()
            calculos.recalcular(p)
            messages.success(request, f'Partida {partida.item} agregada.')
            if not partida.es_titulo and not partida.precio_fijo:
                return redirect('obras:apu', partida.pk)
        else:
            for campo, errores in form.errors.items():
                messages.error(request, f'{form.fields[campo].label if campo in form.fields else ""}: {" ".join(errores)}')
    elif accion == 'eliminar':
        partida = get_object_or_404(Partida, pk=request.POST.get('eliminar'), presupuesto=p)
        hijas = p.partidas.filter(orden__startswith=partida.orden + '.')
        n = hijas.count()
        hijas.delete()
        partida.delete()
        calculos.recalcular(p)
        messages.success(request, f'Partida eliminada{f" con sus {n} partidas" if n else ""}.')
    elif accion == 'metrados':  # edición rápida de metrados en la tabla
        cambios = []
        for partida in p.partidas.filter(es_titulo=False):
            nuevo = _decimal(request.POST.get(f'metrado_{partida.pk}'))
            if nuevo is not None and nuevo != partida.metrado:
                partida.metrado = nuevo
                cambios.append(partida)
        Partida.objects.bulk_update(cambios, ['metrado'])
        calculos.recalcular(p)
        messages.success(request, f'{len(cambios)} metrado(s) actualizados.')
    elif accion == 'pie':
        form = PieForm(request.POST, instance=p)
        if form.is_valid():
            form.save()
            calculos.recalcular(p)
            messages.success(request, 'Pie del presupuesto actualizado.')
    elif accion == 'recalcular':
        calculos.recalcular(p)
        messages.success(request, 'Presupuesto recalculado.')
    elif accion == 'aprobar':
        calculos.recalcular(p)
        p.estado, p.aprobado_por, p.aprobado_en = 'APROBADO', request.user, timezone.now()
        p.save(update_fields=['estado', 'aprobado_por', 'aprobado_en'])
        messages.success(request, f'{p} aprobado por S/ {p.total:,.2f}. Ya no se modifica: cópielo para una nueva versión.')
    return None


# ---------------------------------------------------------------- análisis de precios unitarios
@login_required
def apu(request, pk):
    partida = get_object_or_404(Partida.objects.select_related('presupuesto__obra'), pk=pk, es_titulo=False)
    p = partida.presupuesto
    if request.method == 'POST':
        if not p.editable:
            messages.error(request, 'El presupuesto está aprobado: cópielo a una nueva versión para cambiarlo.')
            return redirect('obras:apu', pk)
        _guardar_apu(request, partida)
        siguiente = request.POST.get('siguiente')
        if siguiente:
            return redirect('obras:apu', siguiente)
        return redirect('obras:apu', pk)
    precios = calculos.precios_de(p)
    a = calculos.analisis(partida, precios)
    for f in a['lineas']:  # 9.730000 → 9.73 (sin ceros de más, como S10)
        f['aporte_txt'] = format(f['aporte'].normalize(), 'f') if f['aporte'] else '0'
    hojas = list(p.partidas.filter(es_titulo=False).values_list('pk', 'item'))
    pos = next((i for i, (x, _) in enumerate(hojas) if x == partida.pk), 0)
    return render(request, 'obras/apu.html', {
        'partida': partida, 'p': p, 'a': a, 'tipos': Recurso.TIPOS,
        'recursos': Recurso.objects.filter(activo=True).only('codigo', 'nombre', 'unidad', 'tipo'),
        'bases': PartidaBase.objects.filter(unidad__iexact=partida.unidad)[:100],
        'anterior': hojas[pos - 1][0] if pos > 0 else None,
        'siguiente': hojas[pos + 1][0] if pos + 1 < len(hojas) else None})


@transaction.atomic
def _guardar_apu(request, partida):
    p = partida.presupuesto
    accion = request.POST.get('accion', 'guardar')
    if accion == 'usar_base':
        base = get_object_or_404(PartidaBase, pk=request.POST.get('base'))
        partida.recursos.all().delete()
        partida.rendimiento, partida.jornada, partida.partida_base = base.rendimiento, base.jornada, base
        partida.precio_fijo = False
        partida.save()
        for r in base.recursos.select_related('recurso'):
            calculos.asegurar_precio(p, r.recurso)
            RecursoAPU.objects.create(partida=partida, recurso=r.recurso, cuadrilla=r.cuadrilla, cantidad=r.cantidad,
                                      orden=r.orden)
        calculos.recalcular(p)
        messages.success(request, f'Análisis copiado de la partida tipo {base}.')
        return
    if accion == 'guardar_base':
        base = PartidaBase.objects.create(descripcion=partida.descripcion, unidad=partida.unidad,
                                          rendimiento=partida.rendimiento, jornada=partida.jornada)
        for l in partida.recursos.all():
            RecursoBase.objects.create(partida=base, recurso_id=l.recurso_id, cuadrilla=l.cuadrilla,
                                       cantidad=l.cantidad, orden=l.orden)
        messages.success(request, f'Guardada en la biblioteca como {base.codigo}.')
        return
    # cabecera
    for campo in ('rendimiento', 'jornada', 'metrado'):
        valor = _decimal(request.POST.get(campo))
        if valor is not None:
            setattr(partida, campo, valor)
    partida.descripcion = (request.POST.get('descripcion') or partida.descripcion)[:400]
    partida.unidad = (request.POST.get('unidad') or partida.unidad)[:12]
    partida.precio_fijo = bool(request.POST.get('precio_fijo'))
    if partida.precio_fijo:
        partida.precio = _decimal(request.POST.get('precio'), partida.precio)
    partida.save()
    # líneas existentes: aporte, cuadrilla, precio (de la lista del presupuesto) o quitar
    for l in partida.recursos.select_related('recurso'):
        if request.POST.get(f'quitar_{l.pk}'):
            l.delete()
            continue
        cuadrilla = _decimal(request.POST.get(f'cuadrilla_{l.pk}'))
        cantidad = _decimal(request.POST.get(f'cantidad_{l.pk}'))
        l.cuadrilla = cuadrilla if l.recurso.tipo in ('MO', 'EQ') else None
        if cantidad is not None:
            l.cantidad = cantidad
        l.save()
        precio = _decimal(request.POST.get(f'precio_{l.pk}'))
        if precio is not None and not l.recurso.es_porcentaje_mo:
            calculos.asegurar_precio(p, l.recurso, precio)
    # recurso nuevo (existente por código o creado aquí)
    texto = (request.POST.get('nuevo_recurso') or '').strip()
    if texto:
        codigo = texto.split(' · ')[0].strip()
        recurso = Recurso.objects.filter(codigo=codigo).first()
        if recurso is None and request.POST.get('nuevo_nombre'):
            recurso = Recurso.objects.create(nombre=request.POST['nuevo_nombre'][:250],
                                             unidad=(request.POST.get('nuevo_unidad') or 'und')[:12],
                                             tipo=request.POST.get('nuevo_tipo') or 'MAT',
                                             precio=_decimal(request.POST.get('nuevo_precio'), Decimal('0')))
        if recurso is None:
            messages.error(request, f'No existe el recurso «{codigo}»: elíjalo de la lista o créelo con su nombre.')
        else:
            calculos.asegurar_precio(p, recurso, _decimal(request.POST.get('nuevo_precio')))
            RecursoAPU.objects.create(partida=partida, recurso=recurso,
                                      cuadrilla=_decimal(request.POST.get('nuevo_cuadrilla'))
                                      if recurso.tipo in ('MO', 'EQ') else None,
                                      cantidad=_decimal(request.POST.get('nuevo_cantidad'), Decimal('0')),
                                      orden=partida.recursos.count())
    elif request.POST.get('nuevo_nombre'):
        recurso = Recurso.objects.create(nombre=request.POST['nuevo_nombre'][:250],
                                         unidad=(request.POST.get('nuevo_unidad') or 'und')[:12],
                                         tipo=request.POST.get('nuevo_tipo') or 'MAT',
                                         precio=_decimal(request.POST.get('nuevo_precio'), Decimal('0')))
        calculos.asegurar_precio(p, recurso)
        RecursoAPU.objects.create(partida=partida, recurso=recurso,
                                  cuadrilla=_decimal(request.POST.get('nuevo_cuadrilla'))
                                  if recurso.tipo in ('MO', 'EQ') else None,
                                  cantidad=_decimal(request.POST.get('nuevo_cantidad'), Decimal('0')),
                                  orden=partida.recursos.count())
    calculos.recalcular(p)
    partida.refresh_from_db()
    messages.success(request, f'Análisis guardado: precio unitario S/ {partida.precio:,.2f}.')


@login_required
def apu_reporte(request, pk):
    """Todos los análisis del presupuesto (para imprimir o Excel)."""
    p = get_object_or_404(Presupuesto.objects.select_related('obra'), pk=pk)
    precios = calculos.precios_de(p)
    bloques = [(x, calculos.analisis(x, precios)) for x in p.partidas.filter(es_titulo=False, precio_fijo=False)
               .prefetch_related('recursos__recurso')]
    bloques = [(x, a) for x, a in bloques if a['lineas']]
    if request.GET.get('formato') == 'excel':
        filas = []
        for x, a in bloques:
            filas.append([x.item, x.descripcion, x.unidad, f'Rend. {x.rendimiento} {x.unidad}/día · jornada {x.jornada} h',
                          '', '', x.precio])
            for f in a['lineas']:
                r = f['linea'].recurso
                filas.append(['', f'   {r.codigo} {r.nombre}', r.unidad, f['linea'].cuadrilla, f['aporte'],
                              f['precio'], f['parcial']])
        return excel_response(f'APU_{p.obra.codigo}_{p.tipo}_v{p.version}', f'ANÁLISIS DE PRECIOS UNITARIOS {p}',
                              ['Ítem', 'Descripción', 'Und.', 'Cuadrilla', 'Cantidad', 'Precio', 'Parcial'], filas)
    return render(request, 'obras/apu_reporte.html', {'p': p, 'bloques': bloques, 'tipos': dict(Recurso.TIPOS)})


# ---------------------------------------------------------------- precios e insumos
@login_required
def precios(request, pk):
    p = get_object_or_404(Presupuesto.objects.select_related('obra'), pk=pk)
    usados = set(RecursoAPU.objects.filter(partida__presupuesto=p).values_list('recurso_id', flat=True))
    for rid in usados - set(p.precios.values_list('recurso_id', flat=True)):
        calculos.asegurar_precio(p, Recurso.objects.get(pk=rid))
    filas = list(p.precios.filter(recurso_id__in=usados).select_related('recurso').order_by('recurso__tipo',
                                                                                             'recurso__nombre'))
    if request.method == 'POST':
        if not p.editable:
            messages.error(request, 'El presupuesto está aprobado.')
        else:
            cambios = []
            for f in filas:
                nuevo = _decimal(request.POST.get(f'precio_{f.pk}'))
                if nuevo is not None and nuevo != f.precio:
                    f.precio = nuevo
                    cambios.append(f)
            PrecioPresupuesto.objects.bulk_update(cambios, ['precio'])
            calculos.recalcular(p)
            messages.success(request, f'{len(cambios)} precio(s) actualizados; presupuesto recalculado (S/ {p.total:,.2f}).')
        return redirect('obras:precios', pk)
    filas = [f for f in filas if not f.recurso.es_porcentaje_mo]
    return render(request, 'obras/precios.html', {'p': p, 'filas': filas, 'tipos': dict(Recurso.TIPOS)})


@login_required
def insumos(request, pk):
    p = get_object_or_404(Presupuesto.objects.select_related('obra'), pk=pk)
    e = calculos.explosion(p)
    if request.GET.get('formato') == 'excel':
        filas = [[dict(Recurso.TIPOS)[t['recurso'].tipo], t['recurso'].codigo, t['recurso'].nombre,
                  t['recurso'].unidad, t['cantidad'], t['precio'] if not t['recurso'].es_porcentaje_mo else '',
                  t['monto'], t['recurso'].indice_unificado] for t in e['filas']]
        if e['sin_apu']:
            filas.append(['', '', 'Partidas sin análisis (precio directo)', '', '', '', e['sin_apu'], ''])
        filas.append(['', '', 'COSTO DIRECTO', '', '', '', e['total'], ''])
        return excel_response(f'Insumos_{p.obra.codigo}_{p.tipo}_v{p.version}', f'RELACIÓN DE INSUMOS {p}',
                              ['Tipo', 'Código', 'Recurso', 'Und.', 'Cantidad', 'Precio', 'Parcial', 'IU'], filas)
    return render(request, 'obras/insumos.html', {'p': p, 'e': e, 'tipos': dict(Recurso.TIPOS)})


# ---------------------------------------------------------------- importación
@login_required
def importar(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    form = ImportarForm(request.POST or None, request.FILES or None, initial={'nombre': o.nombre})
    vista = None
    if request.method == 'POST' and request.POST.get('accion') == 'grabar':
        datos = request.session.get(SESION_IMPORTACION)
        if not datos or datos.get('obra') != o.pk:
            messages.error(request, 'Vuelva a subir el archivo.')
            return redirect('obras:importar', pk)
        pres, diferencias = imp.grabar(datos, o, datos['tipo'], datos['nombre'], request.user,
                                       origen=datos.get('archivo', 'Excel'))
        request.session.pop(SESION_IMPORTACION, None)
        messages.success(request, f'{pres} importado: S/ {pres.total:,.2f} ({pres.partidas.count()} partidas y títulos).')
        if diferencias:
            messages.warning(request, f'{len(diferencias)} partida(s) con precio calculado distinto al del archivo '
                                      f'(primeras: ' + '; '.join(f'{i}: {a:,.2f} → {b:,.2f}' for i, a, b in diferencias[:5])
                             + '). Revise sus análisis.')
        return redirect('obras:presupuesto', pres.pk)
    if request.method == 'POST' and form.is_valid():
        try:
            datos = imp.leer(form.cleaned_data['archivo'])
        except imp.ErrorImportacion as exc:
            messages.error(request, str(exc))
        else:
            datos.update(obra=o.pk, tipo=form.cleaned_data['tipo'], nombre=form.cleaned_data['nombre'] or o.nombre,
                         archivo=form.cleaned_data['archivo'].name[:30])
            request.session[SESION_IMPORTACION] = datos
            vista = {'resumen': imp.resumen(datos), 'avisos': datos['avisos'], 'partidas': datos['partidas'][:40],
                     'apu': datos['apu'], 'datos': datos}
    return render(request, 'obras/importar.html', {'o': o, 'form': form, 'vista': vista})


@login_required
def plantilla(request):
    resp = HttpResponse(imp.plantilla(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="Plantilla_presupuesto_obra.xlsx"'
    return resp


# ---------------------------------------------------------------- recursos y biblioteca
class RecursoLista(ListaGenerica):
    model = Recurso
    titulo = 'Recursos (insumos) de obra'
    columnas = [('Código', 'codigo'), ('Recurso', 'nombre'), ('Und.', 'unidad'), ('Tipo', 'get_tipo_display'),
                ('Precio referencial', 'precio'), ('IU', 'indice_unificado'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'obras:recurso_nuevo', 'obras:recurso_editar'
    buscar_en = ['codigo', 'nombre']


class RecursoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Recurso, RecursoForm, 'Nuevo recurso'
    success_url = reverse_lazy('obras:recursos')


class RecursoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Recurso, RecursoForm, 'Editar recurso'
    success_url = reverse_lazy('obras:recursos')


@login_required
def biblioteca(request):
    if request.method == 'POST' and request.POST.get('eliminar'):
        PartidaBase.objects.filter(pk=request.POST['eliminar']).delete()
        messages.success(request, 'Partida tipo eliminada de la biblioteca.')
        return redirect('obras:biblioteca')
    q = request.GET.get('q', '').strip()
    qs = PartidaBase.objects.prefetch_related('recursos__recurso')
    if q:
        qs = qs.filter(Q(descripcion__icontains=q) | Q(codigo__icontains=q))
    return render(request, 'obras/biblioteca.html', {'bases': qs[:300], 'q': q})
