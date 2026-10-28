"""Conversaciones: canales por área o tema y chats directos entre usuarios (se actualizan solos cada pocos segundos)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import servicios as srv
from .forms import CanalForm
from .models import Canal


def _canal_visible(request, pk):
    canal = get_object_or_404(Canal, pk=pk)
    if not srv.puede_ver(request.user, canal):
        raise Http404
    return canal


@login_required
def inicio(request):
    user = request.user
    canales = list(srv.canales_visibles(user).prefetch_related('membresias__usuario'))
    nuevos = srv.no_leidos(user)
    actual = None
    if (request.GET.get('canal') or '').isdigit():
        actual = next((c for c in canales if c.pk == int(request.GET['canal'])), None)
    if actual is None:
        actual = next((c for c in canales if c.tipo == 'CANAL'), None)
    if request.method == 'POST' and actual is not None:
        try:
            srv.publicar(actual, user, request.POST.get('texto'))
        except srv.ErrorColaboracion as exc:
            messages.error(request, str(exc))
        return redirect(f'{reverse("conversaciones:inicio")}?canal={actual.pk}')
    mensajes = []
    if actual is not None:
        mensajes = list(actual.mensajes.select_related('autor').order_by('-id')[:100])[::-1]
        if mensajes and actual.membresias.filter(usuario=user).exists():
            srv.marcar_leido(actual, user, mensajes[-1].pk)
            nuevos.pop(actual.pk, None)
    filas = [{'canal': c, 'titulo': srv.titulo_canal(c, user), 'nuevos': nuevos.get(c.pk, 0)} for c in canales]
    return render(request, 'colaboracion/conversaciones.html', {
        'canales': [f for f in filas if f['canal'].tipo == 'CANAL'],
        'directos': [f for f in filas if f['canal'].tipo == 'DIRECTO'],
        'actual': actual, 'titulo_actual': srv.titulo_canal(actual, user) if actual else '',
        'mensajes': mensajes, 'ultimo': mensajes[-1].pk if mensajes else 0,
        'es_miembro': actual is not None and actual.membresias.filter(usuario=user).exists(),
        'usuarios': srv.usuarios_internos().exclude(pk=user.pk)})


@login_required
def nuevos(request, pk):
    """Mensajes posteriores al último que tiene la pantalla (la pantalla pregunta cada 5 segundos)."""
    canal = _canal_visible(request, pk)
    desde = int(request.GET.get('desde') or 0) if (request.GET.get('desde') or '0').isdigit() else 0
    lista = list(canal.mensajes.filter(id__gt=desde).select_related('autor')[:50])
    if lista:
        srv.marcar_leido(canal, request.user, lista[-1].pk)
    return JsonResponse({'mensajes': [srv.mensaje_json(m, request.user) for m in lista],
                         'no_leidos': sum(srv.no_leidos(request.user).values())})


@login_required
def resumen(request):
    """Chat de la barra superior: total sin leer y las últimas conversaciones (JSON)."""
    return JsonResponse(srv.resumen(request.user))


@login_required
def canal_nuevo(request):
    form = CanalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            canal = form.save(commit=False)
            canal.creado_por = request.user
            canal.save()
            for u in {request.user, *form.cleaned_data['integrantes']}:
                srv.unirse(canal, u)
        messages.success(request, f'Canal «{canal.nombre}» creado.')
        return redirect(f'{reverse("conversaciones:inicio")}?canal={canal.pk}')
    return render(request, 'colaboracion/formulario.html', {
        'form': form, 'titulo': 'Nuevo canal', 'cancelar': reverse('conversaciones:inicio'),
        'anchos': ['descripcion', 'integrantes'],
        'ayuda': 'Un canal abierto lo ve cualquier usuario (por ejemplo «General» o «Avisos»); uno cerrado, solo sus '
                 'miembros (por ejemplo «Gerencia» o un proyecto). Usted queda como miembro.'})


@login_required
@require_POST
def unirse(request, pk):
    canal = _canal_visible(request, pk)
    srv.unirse(canal, request.user)
    return redirect(f'{reverse("conversaciones:inicio")}?canal={canal.pk}')


@login_required
@require_POST
def directo(request):
    otro = get_object_or_404(srv.usuarios_internos(), pk=request.POST.get('usuario') or 0)
    try:
        canal = srv.directo(request.user, otro)
    except srv.ErrorColaboracion as exc:
        messages.error(request, str(exc))
        return redirect('conversaciones:inicio')
    return redirect(f'{reverse("conversaciones:inicio")}?canal={canal.pk}')


@login_required
@require_POST
def archivar(request, pk):
    """El creador del canal (o un administrador) lo archiva: deja de verse, sus mensajes se conservan."""
    canal = _canal_visible(request, pk)
    if canal.tipo != 'CANAL' or not (request.user.is_superuser or canal.creado_por_id == request.user.pk):
        messages.error(request, 'Solo quien creó el canal lo archiva.')
    else:
        canal.archivado = True
        canal.save(update_fields=['archivado'])
        messages.success(request, f'Canal «{canal.nombre}» archivado.')
    return redirect('conversaciones:inicio')
