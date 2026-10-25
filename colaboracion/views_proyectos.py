"""Proyectos: tablero de tareas por etapas (Por hacer, En curso, En revisión, Hecha) con responsables y plazos."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import servicios as srv
from .forms import ProyectoForm, TareaForm
from .models import ComentarioTarea, Proyecto, Tarea


@login_required
def lista(request):
    estado = request.GET.get('estado', 'ACTIVO')
    qs = srv.proyectos_visibles(request.user).select_related('cliente', 'responsable').annotate(
        n_tareas=Count('tareas', distinct=True), n_hechas=Count('tareas', filter=Q(tareas__etapa='HECHA'), distinct=True),
        n_vencidas=Count('tareas', filter=Q(tareas__fecha_limite__lt=timezone.localdate()) & ~Q(tareas__etapa='HECHA'),
                         distinct=True))
    if estado in dict(Proyecto.ESTADOS):
        qs = qs.filter(estado=estado)
    for p in qs:
        p.avance = round(p.n_hechas * 100 / p.n_tareas) if p.n_tareas else 0
    return render(request, 'colaboracion/proyectos.html', {'proyectos': qs, 'estado': estado,
                                                           'estados': Proyecto.ESTADOS})


def _guardar(request, proyecto, titulo):
    form = ProyectoForm(request.POST or None, instance=proyecto)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            p = form.save(commit=False)
            if p.creado_por_id is None:
                p.creado_por = request.user
            if p.responsable_id is None and not p.pk:
                p.responsable = request.user
            p.save()
            form.save_m2m()
        messages.success(request, f'Proyecto «{p.nombre}» guardado.')
        return redirect('proyectos:proyecto', p.pk)
    return render(request, 'colaboracion/formulario.html', {
        'form': form, 'titulo': titulo, 'anchos': ['nombre', 'miembros', 'descripcion'],
        'cancelar': reverse('proyectos:proyecto', args=[proyecto.pk]) if proyecto.pk else reverse('proyectos:lista'),
        'ayuda': 'Los miembros ven el proyecto y trabajan sus tareas; el jefe de proyecto además lo edita. Vincúlelo '
                 'a una obra o servicio para ver su costo y horas en Obras.'})


@login_required
def nuevo(request):
    return _guardar(request, Proyecto(), 'Nuevo proyecto')


@login_required
def editar(request, pk):
    p = get_object_or_404(Proyecto, pk=pk)
    if not srv.puede_editar_proyecto(request.user, p):
        raise Http404
    return _guardar(request, p, f'Editar {p.nombre}')


def _visible(request, pk):
    return get_object_or_404(srv.proyectos_visibles(request.user), pk=pk)


@login_required
def proyecto(request, pk):
    p = _visible(request, pk)
    trabajar = srv.puede_trabajar(request.user, p) and p.estado != 'CERRADO'
    form = TareaForm(request.POST or None, prefix='t') if trabajar else None
    if request.method == 'POST':
        if form is None:
            raise Http404
        if form.is_valid():
            t = form.save(commit=False)
            t.proyecto, t.creado_por = p, request.user
            t.orden = (p.tareas.filter(etapa=t.etapa).order_by('-orden').values_list('orden', flat=True).first() or 0) + 1
            t.save()
            messages.success(request, f'Tarea «{t.titulo}» agregada.')
            return redirect('proyectos:proyecto', pk)
    tareas = list(p.tareas.select_related('responsable').annotate(n_comentarios=Count('comentarios')))
    columnas = [(clave, nombre, [t for t in tareas if t.etapa == clave]) for clave, nombre in Tarea.ETAPAS]
    porcentaje, total = srv.avance(p)
    return render(request, 'colaboracion/proyecto.html', {
        'p': p, 'columnas': columnas, 'form': form, 'trabajar': trabajar, 'avance': porcentaje, 'total': total,
        'editar': srv.puede_editar_proyecto(request.user, p), 'etapas': Tarea.ETAPAS,
        'horas': sum(t.horas_estimadas for t in tareas), 'miembros': p.miembros.all()})


@login_required
@require_POST
def tarea_mover(request, pk):
    t = get_object_or_404(Tarea.objects.select_related('proyecto'), pk=pk)
    if not srv.proyectos_visibles(request.user).filter(pk=t.proyecto_id).exists():
        raise Http404
    try:
        srv.mover_tarea(t, request.POST.get('etapa'), request.user)
    except srv.ErrorColaboracion as exc:
        if request.headers.get('x-requested-with') == 'fetch':
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect('proyectos:proyecto', t.proyecto_id)
    if request.headers.get('x-requested-with') == 'fetch':
        return JsonResponse({'ok': True})
    volver = srv.volver_seguro(request, request.POST.get('volver'))
    return redirect(volver) if volver else redirect('proyectos:proyecto', t.proyecto_id)


@login_required
def tarea(request, pk):
    t = get_object_or_404(Tarea.objects.select_related('proyecto', 'responsable'), pk=pk)
    if not srv.proyectos_visibles(request.user).filter(pk=t.proyecto_id).exists():
        raise Http404
    trabajar = (srv.puede_trabajar(request.user, t.proyecto) or t.responsable_id == request.user.pk) and \
        t.proyecto.estado != 'CERRADO'
    form = TareaForm(request.POST if request.POST.get('que') == 'tarea' else None, instance=t) if trabajar else None
    if request.method == 'POST':
        if not trabajar:
            raise Http404
        if request.POST.get('que') == 'comentario':
            texto = (request.POST.get('texto') or '').strip()[:2000]
            if texto:
                ComentarioTarea.objects.create(tarea=t, autor=request.user, texto=texto)
            return redirect('proyectos:tarea', pk)
        if request.POST.get('que') == 'eliminar' and srv.puede_editar_proyecto(request.user, t.proyecto):
            proyecto_id = t.proyecto_id
            t.delete()
            messages.success(request, 'Tarea eliminada.')
            return redirect('proyectos:proyecto', proyecto_id)
        if form is not None and form.is_valid():
            antes = Tarea.objects.values_list('etapa', flat=True).get(pk=t.pk)
            t = form.save(commit=False)
            if t.etapa != antes:
                t.terminada_en = timezone.now() if t.etapa == 'HECHA' else None
            t.save()
            messages.success(request, 'Tarea actualizada.')
            return redirect('proyectos:tarea', pk)
    return render(request, 'colaboracion/tarea.html', {
        't': t, 'form': form, 'trabajar': trabajar, 'comentarios': t.comentarios.select_related('autor'),
        'eliminar': srv.puede_editar_proyecto(request.user, t.proyecto)})


@login_required
def mis_tareas(request):
    tareas = (Tarea.objects.filter(responsable=request.user).exclude(etapa='HECHA')
              .exclude(proyecto__estado='CERRADO').select_related('proyecto')
              .order_by('fecha_limite', 'proyecto__nombre', 'orden'))
    return render(request, 'colaboracion/mis_tareas.html', {'tareas': tareas, 'etapas': Tarea.ETAPAS})
