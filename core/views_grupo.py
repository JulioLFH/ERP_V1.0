"""Ajustes › Empresas del grupo: empresas, creación de empresas y usuarios corporativos."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from erp import empresas as reg

from . import grupo
from .models import EmpresaGrupo, UsuarioCorporativo


def _exigir_corporativo(request):
    if grupo.corporativo(request) is None:
        raise Http404


@login_required
def empresas(request):
    _exigir_corporativo(request)
    principal = grupo.corporativo(request)
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if accion == 'crear':
        if not grupo.puede_crear(request):
            messages.error(request, 'Su usuario corporativo no crea empresas.')
        else:
            try:
                r = grupo.crear_empresa(request.POST.get('razon_social'), request.POST.get('ruc'), principal)
                messages.success(request, f'Creando {r.razon_social}: tarda un par de minutos. Esta página se '
                                          f'actualiza sola.')
            except grupo.ErrorGrupo as exc:
                messages.error(request, str(exc))
        return redirect('grupo_empresas')
    if accion == 'reintentar' and grupo.puede_crear(request):
        r = get_object_or_404(EmpresaGrupo.objects.using('default'), pk=request.POST.get('id'), estado='ERROR')
        grupo.reintentar(r, principal)
        messages.info(request, f'Reintentando la creación de {r.razon_social}.')
        return redirect('grupo_empresas')
    if accion == 'corporativo' and principal.is_superuser:
        User = get_user_model()
        u = get_object_or_404(User.objects.using('default'), pk=request.POST.get('usuario'))
        if request.POST.get('quitar'):
            UsuarioCorporativo.objects.filter(usuario_id=u.pk).delete()
            messages.info(request, f'{u.username} ya no es usuario corporativo.')
        else:
            elegidas = [a for a in request.POST.getlist('empresas') if a in reg.empresas()]
            UsuarioCorporativo.objects.update_or_create(usuario_id=u.pk, defaults={
                'todas': bool(request.POST.get('todas')), 'empresas': elegidas,
                'puede_crear': bool(request.POST.get('puede_crear'))})
            messages.success(request, f'{u.username} es usuario corporativo.')
        return redirect('grupo_empresas')
    registradas = {e.alias: e for e in EmpresaGrupo.objects.using('default')}
    filas = []
    for alias in reg.empresas():
        e = registradas.get(alias)
        filas.append({'alias': alias, 'nombre': reg.razon_social(alias), 'registro': e,
                      'puede_entrar': alias in dict(grupo.empresas_de(request)) and (e is None or e.estado == 'LISTA'),
                      'actual': alias == reg.actual()})
    filas += [{'alias': e.alias, 'nombre': e.razon_social, 'registro': e, 'puede_entrar': False, 'actual': False}
              for e in registradas.values() if e.alias not in reg.empresas()]
    corporativos = []
    if principal.is_superuser:
        User = get_user_model()
        actuales = {c.usuario_id: c for c in UsuarioCorporativo.objects.all()}
        corporativos = [{'u': u, 'c': actuales.get(u.pk)} for u in User.objects.using('default').filter(
            is_active=True).order_by('-is_superuser', 'username')]
    return render(request, 'core/grupo_empresas.html', {
        'filas': filas, 'puede_crear': grupo.puede_crear(request), 'principal': principal,
        'corporativos': corporativos, 'aliases': list(reg.empresas()),
        'creando': any(f['registro'] and f['registro'].estado == 'CREANDO' for f in filas)})


@login_required
def entrar(request):
    if request.method != 'POST':
        return redirect('grupo_empresas')
    try:
        grupo.entrar(request, request.POST.get('empresa', ''))
        messages.success(request, f'Ahora trabaja en {reg.razon_social(request.session["empresa"])}.')
        return redirect('home')
    except grupo.ErrorGrupo as exc:
        messages.error(request, str(exc))
        return redirect(request.META.get('HTTP_REFERER') or 'home')


@login_required
def estado(request):
    _exigir_corporativo(request)
    return JsonResponse({e.alias: {'estado': e.estado, 'mensaje': e.mensaje}
                         for e in EmpresaGrupo.objects.using('default')})
