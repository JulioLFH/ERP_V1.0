"""Componente común de listas (LIS-01): columnas visibles por defecto y opcionales que cada usuario prende o apaga
(se guardan por usuario), filtros guardados («Mis recepciones», «Pendientes de factura»), agrupar por con subtotales
y exportar a Excel lo que se ve (con los mismos filtros y permisos de la pantalla)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from .models import PreferenciaLista


class Lista:
    """columnas: [(clave, título, visible por defecto)]; agrupaciones: [(clave, título)]."""

    def __init__(self, clave, columnas, agrupaciones=()):
        self.clave, self.columnas, self.agrupaciones = clave, columnas, list(agrupaciones)

    def preferencia(self, user):
        return PreferenciaLista.objects.filter(usuario=user, lista=self.clave).first()

    def visibles(self, user, ocultas=()):
        pref = self.preferencia(user)
        elegidas = set(pref.columnas) if pref and pref.columnas else {c for c, _, v in self.columnas if v}
        return [c for c, _, _ in self.columnas if c in elegidas and c not in ocultas]

    def contexto(self, request, ocultas=()):
        """Para la plantilla core/_lista_opciones.html."""
        pref = self.preferencia(request.user)
        visibles = self.visibles(request.user, ocultas)
        query = request.GET.copy()
        for k in ('page', 'formato'):
            query.pop(k, None)
        return {'lista_clave': self.clave, 'visibles': visibles,
                'lista_columnas': [{'clave': c, 'titulo': t, 'visible': c in visibles} for c, t, _ in self.columnas
                                   if c not in ocultas],
                'lista_filtros': pref.filtros if pref else [], 'lista_agrupaciones': self.agrupaciones,
                'agrupar': request.GET.get('agrupar', ''), 'lista_query': query.urlencode(),
                'lista_vuelta': request.get_full_path()}

    def titulos(self, visibles):
        nombres = {c: t for c, t, _ in self.columnas}
        return [nombres[c] for c in visibles]


@login_required
def preferencias(request):
    """Guarda las columnas o un filtro de una lista y vuelve a ella."""
    vuelta = request.POST.get('vuelta') or '/'
    if not url_has_allowed_host_and_scheme(vuelta, allowed_hosts={request.get_host()}):
        vuelta = '/'
    if request.method != 'POST':
        return redirect(vuelta)
    lista = (request.POST.get('lista') or '').strip()[:60]
    if not lista:
        return redirect(vuelta)
    pref, _ = PreferenciaLista.objects.get_or_create(usuario=request.user, lista=lista)
    accion = request.POST.get('accion')
    if accion == 'columnas':
        pref.columnas = [c[:40] for c in request.POST.getlist('col')][:40]
        pref.save(update_fields=['columnas'])
        messages.success(request, 'Columnas guardadas para su usuario.')
    elif accion == 'restablecer':
        pref.columnas = []
        pref.save(update_fields=['columnas'])
    elif accion == 'guardar_filtro':
        nombre = (request.POST.get('nombre') or '').strip()[:40]
        if nombre:
            filtros = [f for f in pref.filtros if f.get('nombre') != nombre]
            filtros.append({'nombre': nombre, 'query': (request.POST.get('query') or '')[:500]})
            pref.filtros = filtros[-15:]
            pref.save(update_fields=['filtros'])
            messages.success(request, f'Filtro «{nombre}» guardado.')
    elif accion == 'borrar_filtro':
        pref.filtros = [f for f in pref.filtros if f.get('nombre') != request.POST.get('nombre')]
        pref.save(update_fields=['filtros'])
    return redirect(vuelta)


@login_required
def lista_auto(request):
    """LIS-01 en cualquier lista con table[data-lista] (static/js/app.js): columnas ocultas y filtros guardados del
    usuario, en JSON. GET los devuelve; POST (accion: ocultas | guardar_filtro | borrar_filtro) los guarda."""
    from django.http import JsonResponse
    lista = ('auto:' + (request.GET.get('lista') or request.POST.get('lista') or '').strip())[:60]
    if lista == 'auto:':
        return JsonResponse({'error': 'Falta la lista.'}, status=400)
    pref = PreferenciaLista.objects.filter(usuario=request.user, lista=lista).first()
    if request.method == 'POST':
        pref = pref or PreferenciaLista.objects.create(usuario=request.user, lista=lista)
        accion = request.POST.get('accion')
        if accion == 'ocultas':
            pref.columnas = [c[:60] for c in request.POST.getlist('col')][:60]
            pref.save(update_fields=['columnas'])
        elif accion == 'guardar_filtro':
            nombre = (request.POST.get('nombre') or '').strip()[:40]
            if nombre:
                pref.filtros = [f for f in pref.filtros if f.get('nombre') != nombre][-14:] + [
                    {'nombre': nombre, 'query': (request.POST.get('query') or '').lstrip('?')[:500]}]
                pref.save(update_fields=['filtros'])
        elif accion == 'borrar_filtro':
            pref.filtros = [f for f in pref.filtros if f.get('nombre') != request.POST.get('nombre')]
            pref.save(update_fields=['filtros'])
    return JsonResponse({'ocultas': pref.columnas if pref else [], 'filtros': pref.filtros if pref else []})


def agrupar(filas, clave, valor=None):
    """[(grupo, filas, cantidad, subtotal)] de las filas por la función clave (y subtotal de la función valor)."""
    grupos = {}
    for f in filas:
        grupos.setdefault(clave(f) or '—', []).append(f)
    return [(g, fs, len(fs), sum((valor(f) or 0 for f in fs), 0) if valor else None)
            for g, fs in sorted(grupos.items(), key=lambda x: str(x[0]))]
