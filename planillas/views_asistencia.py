"""Asistencia y turnos."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.forms import BootstrapMixin
from core.utils import excel_response, leer_excel

from . import asistencia
from .models import Marcacion, Trabajador, Turno


class TurnoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Turno
        fields = ['nombre', 'hora_entrada', 'hora_salida', 'refrigerio_min', 'tolerancia_min', 'dias']
        widgets = {'hora_entrada': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
                   'hora_salida': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')}


class MarcacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Marcacion
        fields = ['trabajador', 'fecha', 'entrada', 'salida', 'observacion', 'justificada']
        widgets = {'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'entrada': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
                   'salida': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['trabajador'].queryset = Trabajador.objects.filter(fecha_cese__isnull=True)


@login_required
def turnos(request, pk=None):
    turno = get_object_or_404(Turno, pk=pk) if pk else Turno()
    form = TurnoForm(request.POST or None, instance=turno)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Turno guardado. Asígnelo a cada trabajador en su ficha.')
        return redirect('planillas:turnos')
    from django.db.models import Count
    return render(request, 'planillas/turnos.html', {
        'form': form, 'turno': turno, 'turnos': Turno.objects.annotate(n=Count('trabajadores'))})


def _periodo(request):
    return (request.GET.get('periodo') or request.POST.get('periodo') or
            timezone.localdate().strftime('%Y-%m')).replace('-', '')[:6]


@login_required
def asistencia_mes(request):
    periodo = _periodo(request)
    mes = f'{periodo[:4]}-{periodo[4:]}'
    form = MarcacionForm(request.POST if request.POST.get('accion') == 'marcar' else None,
                         initial={'fecha': timezone.localdate()})
    if request.GET.get('plantilla'):
        return excel_response('Plantilla_marcaciones', 'plantilla', ['dni', 'fecha', 'entrada', 'salida', 'observacion'],
                              [['40000001', timezone.localdate().strftime('%d/%m/%Y'), '08:02', '17:30', '']])
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'marcar':
            if form.is_valid():
                d = form.cleaned_data
                Marcacion.objects.update_or_create(trabajador=d['trabajador'], fecha=d['fecha'], defaults={
                    'entrada': d['entrada'], 'salida': d['salida'], 'observacion': d['observacion'],
                    'justificada': d['justificada'], 'origen': 'MANUAL'})
                messages.success(request, 'Marcación registrada.')
                return redirect(f'{request.path}?periodo={mes}')
        elif accion == 'importar' and request.FILES.get('archivo'):
            try:
                ok, errores = asistencia.importar(leer_excel(request.FILES['archivo']))
                messages.success(request, f'{ok} marcación(es) importada(s).')
                for e in errores[:10]:
                    messages.warning(request, e)
            except Exception as exc:  # archivo ilegible
                messages.error(request, f'No se pudo leer el archivo: {exc}')
            return redirect(f'{request.path}?periodo={mes}')
        elif accion == 'planilla':
            try:
                planilla, n = asistencia.pasar_a_planilla(periodo)
                messages.success(request, f'Faltas y horas extra de {n} trabajador(es) pasadas a la planilla: '
                                          'vuelva a calcularla.')
                return redirect('planillas:detalle', planilla.pk)
            except asistencia.ErrorAsistencia as exc:
                messages.error(request, str(exc))
            return redirect(f'{request.path}?periodo={mes}')
    filas = asistencia.resumen(periodo)
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Asistencia_{periodo}', f'ASISTENCIA {mes}', [
            'DNI', 'Trabajador', 'Turno', 'Días laborables', 'Asistidos', 'Faltas', 'Tardanzas', 'Minutos de tardanza',
            'Vacaciones', 'Horas extra 25%', 'Horas extra 35%'],
            [[f['t'].numero_doc, f['t'].nombre_completo, str(f['turno'] or 'Sin turno'), f['laborables'],
              f['asistidos'], f['faltas'], f['tardanzas'], f['tardanza_min'], f['vacaciones'], f['he25'], f['he35']]
             for f in filas])
    recientes = Marcacion.objects.select_related('trabajador').filter(
        fecha__year=int(periodo[:4]), fecha__month=int(periodo[4:]))[:50]
    return render(request, 'planillas/asistencia.html', {
        'filas': filas, 'periodo': periodo, 'mes': mes, 'form': form, 'recientes': recientes,
        'sin_turno': sum(1 for f in filas if f['sin_turno'])})
