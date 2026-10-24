"""Pantallas genéricas de registros (lista, nuevo y ficha) para los módulos de SST y calidad: cada tipo declara su
modelo, columnas y campos; la ficha muestra el formulario (bloqueado si el registro ya se validó), lo propio del tipo
(líneas, asistentes, acciones) y la validación por especialista."""
from django import forms
from django.contrib import messages
from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import BootstrapMixin, remoto
from .utils import excel_response, fmt_fecha


class _Base(BootstrapMixin, forms.ModelForm):
    remotos = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from planillas.models import Trabajador
        for campo, lista in self.remotos.items():
            if campo in self.fields:
                remoto(self.fields[campo], lista)
        for campo in self.fields.values():  # trabajadores con vínculo laboral registrado
            qs = getattr(campo, 'queryset', None)
            if qs is not None and qs.model is Trabajador:
                campo.queryset = Trabajador.objects.filter(fecha_ingreso__isnull=False)


class Tipo:
    def __init__(self, slug, modelo, titulo, columnas, campos, extra='', remotos=None, filtros=None, orden=None):
        self.slug, self.modelo, self.titulo = slug, modelo, titulo
        self.columnas = columnas  # [(título, atributo)]
        self.campos, self.extra = campos, extra
        self.remotos = remotos or {}  # campo -> lista remota (selector con búsqueda)
        self.filtros = filtros or {}
        self.orden = orden

    def formulario(self):
        widgets = {c: forms.Textarea(attrs={'rows': 3}) for c in self.campos
                   if isinstance(self.modelo._meta.get_field(c), models.TextField)}
        base = type('Form', (_Base,), {'remotos': self.remotos})
        return forms.modelform_factory(self.modelo, form=base, fields=self.campos, widgets=widgets)


def _valor(obj, atributo):
    v = obj
    for parte in atributo.split('.'):
        v = getattr(v, parte, '') if v is not None else ''
        if callable(v) and not isinstance(v, models.Manager):  # métodos sí; los managers (lineas.count) no
            v = v()
    return v


def lista(request, app, tipos, slug, plantilla='core/registro_lista.html'):
    tipo = tipos.get(slug)
    if tipo is None:
        raise Http404
    qs = tipo.modelo.objects.all()
    if any(f.name == 'vigente' for f in tipo.modelo._meta.fields) and not request.GET.get('historia'):
        qs = qs.filter(vigente=True)  # sin las versiones anteriores de los registros validados
    if tipo.orden:
        qs = qs.order_by(*tipo.orden)
    q = (request.GET.get('q') or '').strip()
    filas = list(qs[:500])
    if q:
        filas = [o for o in filas if q.lower() in ' '.join(str(_valor(o, a)) for _, a in tipo.columnas).lower()]
    if request.GET.get('formato') == 'excel':
        return excel_response(tipo.titulo[:30], tipo.titulo, [t for t, _ in tipo.columnas],
                              [[fmt_fecha(v) if hasattr(v, 'strftime') else (v if v is not None else '')
                                for v in (_valor(o, a) for _, a in tipo.columnas)] for o in filas])
    return render(request, plantilla, {
        'tipo': tipo, 'app': app, 'tipos': tipos, 'q': q, 'historia': request.GET.get('historia'),
        'filas': [(o, [_valor(o, a) for _, a in tipo.columnas]) for o in filas]})


def nuevo(request, app, tipos, slug, inicial=None):
    tipo = tipos.get(slug)
    if tipo is None:
        raise Http404
    form = tipo.formulario()(request.POST or None, request.FILES or None, initial=inicial or {})
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if hasattr(obj, 'creado_por_id'):
            obj.creado_por = request.user
        obj.save()
        form.save_m2m()
        messages.success(request, f'{obj._meta.verbose_name.capitalize()} registrado.')
        return redirect(reverse(f'{app}:registro', args=[slug, obj.pk]))
    return render(request, 'core/form.html', {'form': form, 'titulo': f'Nuevo: {tipo.modelo._meta.verbose_name}'})


def ficha(request, app, tipos, slug, pk, contexto=None, puede_registrar=False, puede_validar=False):
    """Ficha con el formulario (POST accion=datos guarda si el registro es editable)."""
    tipo = tipos.get(slug)
    if tipo is None:
        raise Http404
    obj = get_object_or_404(tipo.modelo, pk=pk)
    editable = getattr(obj, 'editable', True) and puede_registrar
    form = tipo.formulario()(request.POST if request.POST.get('accion') == 'datos' else None,
                             instance=obj)
    if not editable:
        for f in form.fields.values():
            f.disabled = True
    if request.POST.get('accion') == 'datos':
        if not editable:
            messages.error(request, 'El registro está validado o no tiene permiso: cree una versión nueva para '
                                    'cambiarlo.')
            return redirect(request.path)
        if form.is_valid():
            form.save()
            messages.success(request, 'Registro guardado.')
            return redirect(request.path)
    return render(request, 'core/registro_ficha.html', {
        'tipo': tipo, 'app': app, 'objeto': obj, 'form': form, 'editable': editable,
        'puede_registrar': puede_registrar, 'puede_validar': puede_validar,
        'validable': hasattr(obj, 'estado_validacion'), **(contexto or {})})
