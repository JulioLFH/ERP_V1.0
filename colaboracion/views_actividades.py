"""Actividades pendientes: lo que cada usuario tiene por hacer (sus actividades, las que le encargan, sus tareas de
proyectos y sus actividades del CRM) y lo que espera su aprobación."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import servicios as srv
from .forms import PendienteForm
from .models import Pendiente


@login_required
def lista(request):
    hoy = timezone.localdate()
    if request.GET.get('vista') == 'encargadas':
        encargadas = (Pendiente.objects.filter(creado_por=request.user).exclude(asignado_a=request.user)
                      .select_related('asignado_a'))
        return render(request, 'colaboracion/actividades.html', {
            'vista': 'encargadas', 'encargadas': encargadas, 'hoy': hoy})
    from core.aprobaciones import pendientes_de
    aprobaciones = pendientes_de(request.user)
    return render(request, 'colaboracion/actividades.html', {
        'vista': 'mias', 'grupos': srv.agrupar(srv.por_hacer(request.user), hoy), 'hoy': hoy,
        'aprobaciones': sum(aprobaciones.values()), 'hechas': Pendiente.objects.filter(
            asignado_a=request.user, hecho=True).order_by('-hecho_en')[:10]})


@login_required
def nueva(request):
    inicial = {'asignado_a': request.user.pk, 'titulo': request.GET.get('titulo', '')[:150],
               'enlace': request.GET.get('enlace', '')[:300]}
    form = PendienteForm(request.POST or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        p = form.save(commit=False)
        p.creado_por = request.user
        p.save()
        para = '' if p.asignado_a_id == request.user.pk else f' y encargada a {srv.nombre(p.asignado_a)}'
        messages.success(request, f'Actividad «{p.titulo}» registrada{para}.')
        return redirect(srv.volver_seguro(request, request.POST.get('volver')) or 'actividades:lista')
    return render(request, 'colaboracion/formulario.html', {
        'form': form, 'titulo': 'Nueva actividad', 'cancelar': reverse('actividades:lista'), 'anchos': ['titulo', 'detalle'],
        'volver_a': srv.volver_seguro(request, request.GET.get('volver')) or '',
        'ayuda': 'Encárguela a otro usuario para que le aparezca en sus pendientes; usted la sigue en «Encargadas por mí».'})


def _propia(request, pk):
    p = get_object_or_404(Pendiente, pk=pk)
    if request.user.pk not in (p.asignado_a_id, p.creado_por_id) and not request.user.is_superuser:
        raise Http404
    return p


@login_required
def editar(request, pk):
    p = _propia(request, pk)
    form = PendienteForm(request.POST or None, instance=p)
    if request.method == 'POST':
        if request.POST.get('accion') == 'eliminar':
            if p.creado_por_id != request.user.pk and not request.user.is_superuser:
                messages.error(request, 'Solo quien registró la actividad la elimina.')
                return redirect('actividades:editar', pk)
            p.delete()
            messages.success(request, 'Actividad eliminada.')
            return redirect('actividades:lista')
        if form.is_valid():
            form.save()
            messages.success(request, 'Actividad actualizada.')
            return redirect('actividades:lista')
    return render(request, 'colaboracion/formulario.html', {
        'form': form, 'titulo': 'Editar actividad', 'cancelar': reverse('actividades:lista'), 'anchos': ['titulo', 'detalle'],
        'eliminar': '¿Eliminar la actividad?' if p.creado_por_id == request.user.pk or request.user.is_superuser else ''})


@login_required
@require_POST
def hecha(request, pk):
    p = _propia(request, pk)
    p.hecho = request.POST.get('deshacer') != '1'
    p.hecho_en = timezone.now() if p.hecho else None
    p.resultado = (request.POST.get('resultado') or p.resultado)[:250]
    p.save(update_fields=['hecho', 'hecho_en', 'resultado'])
    messages.success(request, f'«{p.titulo}»: {"hecha" if p.hecho else "pendiente otra vez"}.')
    return redirect(srv.volver_seguro(request, request.POST.get('volver')) or 'actividades:lista')
