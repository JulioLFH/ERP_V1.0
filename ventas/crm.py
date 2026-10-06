"""CRM: embudo comercial (oportunidades por etapa, tablero arrastrable) y actividades de seguimiento."""
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.forms import BootstrapMixin, remoto
from core.models import Tercero
from core.utils import excel_response, fmt_fecha

from .models import ActividadCRM, Oportunidad


class OportunidadForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Oportunidad
        fields = ['nombre', 'tercero', 'prospecto', 'contacto', 'telefono', 'email', 'etapa', 'monto',
                  'probabilidad', 'fecha_cierre', 'origen', 'responsable', 'cotizacion', 'motivo_perdida', 'notas']
        widgets = {'notas': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['tercero'], 'clientes')

    def clean(self):
        d = super().clean()
        if not d.get('tercero') and not (d.get('prospecto') or '').strip():
            self.add_error('prospecto', 'Indique el cliente o el nombre del prospecto.')
        if d.get('etapa') == 'PERDIDA' and len((d.get('motivo_perdida') or '').strip()) < 5:
            self.add_error('motivo_perdida', 'Indique por qué se perdió.')
        if d.get('probabilidad') is not None and d['probabilidad'] > 100:
            self.add_error('probabilidad', 'Máximo 100.')
        return d


class ActividadForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ActividadCRM
        fields = ['tipo', 'fecha', 'descripcion', 'hecha']


def _filtrar(request):
    qs = Oportunidad.objects.select_related('tercero', 'responsable')
    if request.GET.get('mias'):
        qs = qs.filter(responsable=request.user)
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(nombre__icontains=q) | Q(prospecto__icontains=q) | Q(tercero__nombre__icontains=q))
    return qs


@login_required
def embudo(request):
    """Tablero por etapas (se arrastran las tarjetas para cambiar de etapa)."""
    qs = list(_filtrar(request).filter(Q(etapa__in=Oportunidad.ABIERTAS) | Q(
        actualizado__date__gte=date.today().replace(day=1))))
    columnas = []
    for etapa, nombre in Oportunidad.ETAPAS:
        tarjetas = [o for o in qs if o.etapa == etapa]
        columnas.append({'etapa': etapa, 'nombre': nombre, 'tarjetas': tarjetas,
                         'monto': sum((o.monto for o in tarjetas), Decimal('0')),
                         'ponderado': sum((o.ponderado for o in tarjetas), Decimal('0'))})
    abiertas = [o for o in qs if o.etapa in Oportunidad.ABIERTAS]
    pendientes = ActividadCRM.objects.filter(hecha=False, fecha__lte=date.today(),
                                             oportunidad__etapa__in=Oportunidad.ABIERTAS).select_related('oportunidad')
    if request.GET.get('mias'):
        pendientes = pendientes.filter(oportunidad__responsable=request.user)
    return render(request, 'ventas/crm_embudo.html', {
        'columnas': columnas, 'pendientes': pendientes[:20],
        'total_abierto': sum((o.monto for o in abiertas), Decimal('0')),
        'total_ponderado': sum((o.ponderado for o in abiertas), Decimal('0'))})


@login_required
def oportunidades(request):
    qs = _filtrar(request)
    if request.GET.get('etapa'):
        qs = qs.filter(etapa=request.GET['etapa'])
    if request.GET.get('formato') == 'excel':
        return excel_response('Oportunidades', 'Oportunidades comerciales', [
            'Oportunidad', 'Cliente / prospecto', 'Contacto', 'Etapa', 'Monto', 'Probabilidad %', 'Ponderado',
            'Cierre estimado', 'Origen', 'Responsable', 'Motivo de pérdida'],
            [[o.nombre, o.cliente, o.contacto, o.get_etapa_display(), o.monto, o.prob, o.ponderado,
              fmt_fecha(o.fecha_cierre), o.get_origen_display(), str(o.responsable or ''), o.motivo_perdida]
             for o in qs])
    return render(request, 'ventas/crm_lista.html', {'oportunidades': qs[:300], 'etapas': Oportunidad.ETAPAS})


@login_required
def oportunidad_nueva(request):
    form = OportunidadForm(request.POST or None, initial={'responsable': request.user.pk,
                                                         'tercero': request.GET.get('tercero')})
    if request.method == 'POST' and form.is_valid():
        o = form.save()
        messages.success(request, f'Oportunidad «{o}» registrada.')
        return redirect('ventas:crm_oportunidad', o.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva oportunidad'})


@login_required
def oportunidad(request, pk):
    o = get_object_or_404(Oportunidad.objects.select_related('tercero', 'responsable', 'cotizacion'), pk=pk)
    form = OportunidadForm(request.POST if request.POST.get('accion') == 'datos' else None, instance=o)
    actividad = ActividadForm(request.POST if request.POST.get('accion') == 'actividad' else None,
                              initial={'fecha': date.today()})
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'datos' and form.is_valid():
            form.save()
            messages.success(request, 'Oportunidad actualizada.')
            return redirect('ventas:crm_oportunidad', pk)
        if accion == 'actividad' and actividad.is_valid():
            a = actividad.save(commit=False)
            a.oportunidad, a.usuario = o, request.user
            a.save()
            o.save(update_fields=['actualizado'])
            return redirect('ventas:crm_oportunidad', pk)
        if accion == 'hecha':
            o.actividades.filter(pk=request.POST.get('actividad')).update(hecha=True)
            return redirect('ventas:crm_oportunidad', pk)
    return render(request, 'ventas/crm_oportunidad.html', {
        'o': o, 'form': form, 'actividad': actividad, 'actividades': o.actividades.select_related('usuario')})


@login_required
def cambiar_etapa(request, pk):
    """Desde el tablero (arrastrar y soltar). Perdida exige motivo: se pide en la ficha."""
    o = get_object_or_404(Oportunidad, pk=pk)
    etapa = request.POST.get('etapa')
    if request.method != 'POST' or etapa not in dict(Oportunidad.ETAPAS):
        return JsonResponse({'ok': False, 'error': 'Etapa no válida.'}, status=400)
    if etapa == 'PERDIDA' and not o.motivo_perdida:
        return JsonResponse({'ok': False, 'error': 'Indique el motivo de pérdida en la ficha de la oportunidad.',
                             'url': reverse('ventas:crm_oportunidad', args=[o.pk])}, status=422)
    o.etapa = etapa
    o.save(update_fields=['etapa', 'actualizado'])
    return JsonResponse({'ok': True, 'probabilidad': o.prob})
