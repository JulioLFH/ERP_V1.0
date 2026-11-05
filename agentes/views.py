"""Pantallas de los agentes: el chat de cada módulo (cada usuario ve solo sus conversaciones), la configuración y la
bitácora de conversaciones del administrador (qué se preguntó, qué datos leyó el agente y cuánto costó)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import motor
from .forms import ConfigForm
from .models import ConfigAgentes, Conversacion
from .registro import AGENTES


def _chat(request, clave):
    agente = AGENTES[clave]
    propias = Conversacion.objects.filter(usuario=request.user, agente=clave)
    conversacion = None
    if (request.GET.get('c') or request.POST.get('c') or '').isdigit():
        conversacion = get_object_or_404(propias, pk=request.GET.get('c') or request.POST.get('c'))
    url = reverse(f'agentes:{clave}')
    if request.method == 'POST':
        if conversacion is None:
            conversacion = Conversacion.objects.create(agente=clave, usuario=request.user)
        try:
            motor.responder(conversacion, request.POST.get('pregunta'), request.user)
        except motor.ErrorAgente as exc:
            messages.error(request, str(exc))
            if not conversacion.mensajes.exists():
                conversacion.delete()
                return redirect(url)
        return redirect(f'{url}?c={conversacion.pk}#fin')
    return render(request, 'agentes/chat.html', {
        'agente': agente, 'conversacion': conversacion, 'url': url,
        'mensajes': conversacion.mensajes.all() if conversacion else [],
        'recientes': propias[:15], 'bloqueo': motor.bloqueo(agente, request.user)})


@login_required
def ventas(request):
    return _chat(request, 'ventas')


@login_required
def configuracion(request):
    cfg = ConfigAgentes.actual()
    form = ConfigForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Configuración de los agentes guardada.')
        return redirect('agentes:configuracion')
    return render(request, 'agentes/configuracion.html', {
        'form': form, 'gasto': motor.gasto_del_mes(), 'cfg': cfg, 'configurado': motor.configurado(cfg),
        'agentes': AGENTES.values()})


@login_required
def conversaciones(request):
    qs = Conversacion.objects.select_related('usuario').annotate(
        n=Count('mensajes'), costo=Sum('mensajes__costo_usd')).order_by('-actualizado')
    if request.GET.get('usuario'):
        qs = qs.filter(usuario__username__icontains=request.GET['usuario'].strip())
    if request.GET.get('agente') in AGENTES:
        qs = qs.filter(agente=request.GET['agente'])
    return render(request, 'agentes/conversaciones.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'agentes': AGENTES,
        'gasto': motor.gasto_del_mes()})


@login_required
def conversacion(request, pk):
    c = get_object_or_404(Conversacion.objects.select_related('usuario'), pk=pk)
    return render(request, 'agentes/conversacion.html', {
        'c': c, 'agente': AGENTES.get(c.agente), 'mensajes': c.mensajes.prefetch_related('llamadas'),
        'costo': c.mensajes.aggregate(s=Sum('costo_usd'))['s']})
