"""Calendario: eventos propios y de la empresa más lo que ya está en el sistema (actividades del CRM, tareas de
proyectos, actividades pendientes, vacaciones aprobadas y vencimientos de SUNAT)."""
import calendar
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.modulos import modulos_del_usuario

from . import servicios as srv
from .forms import EventoForm

MESES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre',
         'Noviembre', 'Diciembre']


def _capas(request):
    elegidas = [c for c in request.GET.getlist('capa') if c in dict((k, n) for k, n, _ in srv.CAPAS)]
    return set(elegidas) if elegidas else {c for c, _, _ in srv.CAPAS}


def _mes(texto, hoy):
    try:
        anio, mes = (int(x) for x in (texto or '').split('-'))
        return date(anio, mes, 1)
    except ValueError:
        return hoy.replace(day=1)


@login_required
def mes(request):
    hoy = timezone.localdate()
    primero = _mes(request.GET.get('mes'), hoy)
    ultimo = primero.replace(day=calendar.monthrange(primero.year, primero.month)[1])
    desde = primero - timedelta(days=primero.weekday())  # semanas de lunes a domingo
    hasta = ultimo + timedelta(days=6 - ultimo.weekday())
    capas = _capas(request)
    datos = srv.items(request.user, modulos_del_usuario(request.user), desde, hasta, capas)
    semanas, d = [], desde
    while d <= hasta:
        semanas.append([{'fecha': d + timedelta(days=i), 'items': datos.get(d + timedelta(days=i), []),
                         'otro_mes': (d + timedelta(days=i)).month != primero.month} for i in range(7)])
        d += timedelta(days=7)
    anterior = (primero - timedelta(days=1)).replace(day=1)
    siguiente = (ultimo + timedelta(days=1))
    filtro = ''.join(f'&capa={c}' for c in sorted(capas)) if len(capas) < len(srv.CAPAS) else ''
    return render(request, 'colaboracion/calendario.html', {
        'semanas': semanas, 'hoy': hoy, 'titulo': f'{MESES[primero.month]} {primero.year}',
        'anterior': f'{anterior:%Y-%m}', 'siguiente': f'{siguiente:%Y-%m}', 'actual': f'{hoy:%Y-%m}',
        'capas': [(c, n, color, c in capas) for c, n, color in srv.CAPAS], 'filtro': filtro,
        'mes_param': f'{primero:%Y-%m}'})


@login_required
def agenda(request):
    hoy = timezone.localdate()
    hasta = hoy + timedelta(days=13)
    datos = srv.items(request.user, modulos_del_usuario(request.user), hoy, hasta, _capas(request))
    dias = [(hoy + timedelta(days=i), datos.get(hoy + timedelta(days=i), [])) for i in range(14)]
    return render(request, 'colaboracion/agenda.html', {'dias': dias, 'hoy': hoy})


def _puede_editar(user, evento):
    return user.is_superuser or evento.organizador_id == user.pk


@login_required
def evento_nuevo(request):
    inicial = {}
    try:
        inicial['fecha'] = date.fromisoformat(request.GET.get('fecha') or '')
    except ValueError:
        inicial['fecha'] = timezone.localdate()
    form = EventoForm(request.POST or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        evento = form.save(commit=False)
        evento.organizador = request.user
        evento.save()
        form.save_m2m()
        messages.success(request, f'Evento «{evento.titulo}» agendado.')
        return redirect(f'{reverse("calendario:mes")}?mes={timezone.localtime(evento.inicio):%Y-%m}')
    return render(request, 'colaboracion/formulario.html', {
        'form': form, 'titulo': 'Nuevo evento', 'cancelar': reverse('calendario:mes'),
        'anchos': ['titulo', 'invitados', 'descripcion'],
        'ayuda': 'Sin hora es de todo el día. Los invitados lo ven en su calendario; «Todos los usuarios» lo publica '
                 'para la empresa (feriados, capacitaciones, cierres).'})


@login_required
def evento(request, pk):
    e = get_object_or_404(srv.eventos_visibles(request.user), pk=pk)
    editar = _puede_editar(request.user, e)
    if request.method == 'POST':
        if not editar:
            raise Http404
        if request.POST.get('accion') == 'eliminar':
            mes_evento = f'{timezone.localtime(e.inicio):%Y-%m}'
            e.delete()
            messages.success(request, 'Evento eliminado.')
            return redirect(f'{reverse("calendario:mes")}?mes={mes_evento}')
    form = EventoForm(request.POST or None, instance=e) if editar else None
    if form is not None and request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Evento actualizado.')
        return redirect('calendario:evento', pk)
    return render(request, 'colaboracion/evento.html', {'e': e, 'form': form, 'invitados': e.invitados.all()})
