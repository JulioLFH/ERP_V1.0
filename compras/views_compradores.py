"""Pantallas de grupos de compras / compradores (COM-09) y del desempeño de compradores (COM-10)."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from contabilidad.models import CentroCosto
from core.forms import BootstrapMixin

from . import compradores as srv
from .models import AsignacionCompra, GrupoCompras


class GrupoComprasForm(BootstrapMixin, forms.ModelForm):
    tipos = forms.MultipleChoiceField(label='Tipos que atiende', required=False, choices=GrupoCompras.TIPOS,
                                      widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = GrupoCompras
        fields = ['codigo', 'nombre', 'comprador', 'jefe', 'familias', 'tipos', 'centros', 'plazo_dias',
                  'plazo_urgente', 'prioridad', 'activo']
        widgets = {'centros': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        usuarios = get_user_model().objects.filter(is_active=True, acceso_proveedor__isnull=True).order_by('username')
        self.fields['comprador'].queryset = usuarios
        self.fields['jefe'].queryset = usuarios
        self.fields['centros'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['tipos'].initial = self.instance.tipos if self.instance.pk else []

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip().upper()


@login_required
def grupos(request):
    return render(request, 'compras/grupos_compras.html', {
        'grupos': GrupoCompras.objects.select_related('comprador', 'jefe').prefetch_related('centros')})


@login_required
def grupo(request, pk=None):
    g = get_object_or_404(GrupoCompras, pk=pk) if pk else GrupoCompras()
    form = GrupoComprasForm(request.POST or None, instance=g)
    if request.method == 'POST' and form.is_valid():
        g = form.save(commit=False)
        g.tipos = form.cleaned_data['tipos']
        g.save()
        form.save_m2m()
        messages.success(request, f'Grupo de compras {g} guardado.')
        return redirect('compras:grupos')
    return render(request, 'core/form.html', {'form': form, 'titulo': f'Grupo {g}' if pk else 'Nuevo grupo de compras'})


@login_required
def desempeno(request):
    desde, hasta = srv.mes(request.GET.get('mes'))
    filas = srv.desempeno(desde, hasta)
    if request.GET.get('formato') == 'excel':
        from core.utils import excel_response
        enc = ['Comprador', 'Líneas asignadas', 'Atendidas', 'Pendientes', 'Días hábiles promedio', 'Percentil 90',
               'Cumplimiento del plazo %', 'Vencidas 0-7', 'Vencidas 8-15', 'Vencidas 16-30', 'Vencidas +30',
               'Llegada a tiempo %', 'Ahorro S/', 'Compras con competencia %', 'OC urgentes %', 'OC anuladas',
               'Facturas fuera de tolerancia', 'Monto gestionado S/']
        datos = [[str(f['comprador'].get_full_name() or f['comprador'].username), f['asignadas'], f['atendidas'],
                  f['pendientes'], f['tiempo_promedio'], f['p90'], f['sla'], *f['vencidas'].values(),
                  f['a_tiempo_pct'], f['ahorro'], f['competencia_pct'], f['urgentes_pct'], f['anuladas'],
                  f['observadas'], f['monto']] for f in filas]
        return excel_response(f'Desempeno_compradores_{desde:%Y%m}', f'Desempeño de compradores {desde:%m/%Y}', enc,
                              datos)
    return render(request, 'compras/desempeno_compradores.html', {
        'filas': filas, 'desde': desde, 'mes': f'{desde:%Y-%m}', 'tramos': [t for t, _, _ in srv.TRAMOS]})


@login_required
def historial_linea(request, pk):
    """Historial de asignación de una línea (para la bandeja)."""
    asignaciones = AsignacionCompra.objects.filter(linea_id=pk).select_related('comprador', 'por', 'grupo')
    return render(request, 'compras/asignaciones_linea.html', {'asignaciones': asignaciones})
