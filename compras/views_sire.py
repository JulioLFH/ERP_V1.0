from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from core.forms import BootstrapMixin
from core.utils import excel_response

from . import sire
from django.utils import timezone

SESION = 'sire_propuesta'


class PropuestaForm(BootstrapMixin, forms.Form):
    periodo = forms.CharField(label='Periodo', widget=forms.TextInput(attrs={'type': 'month'}))
    archivo = forms.FileField(label='Propuesta RCE de SIRE (.txt, .zip, .csv o .xlsx)',
                              help_text='SUNAT Operaciones en Línea › SIRE › Registro de Compras › Descargar propuesta')

    def clean_periodo(self):
        valor = self.cleaned_data['periodo'].replace('-', '')
        if len(valor) != 6 or not valor.isdigit():
            raise forms.ValidationError('Periodo no válido.')
        return valor


def _guardar(request, periodo, encabezados, registros):
    """La última propuesta leída queda en la sesión (con todas sus columnas) para exportar el cruce sin volver a subir
    el archivo."""
    request.session[SESION] = {'periodo': periodo, 'encabezados': encabezados, 'registros': sire.a_json(registros)}


def _de_sesion(request):
    datos = request.session.get(SESION)
    if not datos:
        return None, None, None
    return datos['periodo'], datos.get('encabezados') or [], sire.de_json(datos['registros'])


def _rend(x):
    return x['rendicion'].rendicion.numero if x and x.get('rendicion') else ''


def _excel(periodo, encabezados, registros, r):
    encabezados, filas = sire.filas_cruce_completo(encabezados, registros, r, rendiciones=True,
                                                   extra=(['Rendición de gastos'], lambda x: [_rend(x)]))
    return excel_response(f'Cruce_SIRE_compras_{periodo}', f'CRUCE PROPUESTA SIRE (RCE) {periodo}', encabezados,
                          filas)


@login_required
def comparar(request):
    hoy = timezone.localdate()
    anterior = f'{hoy.year - (hoy.month == 1)}-{(hoy.month - 2) % 12 + 1:02d}'
    if request.GET.get('formato') == 'excel':
        periodo, encabezados, registros = _de_sesion(request)
        if periodo is None:
            return redirect('compras:sire')
        return _excel(periodo, encabezados, registros, sire.comparar(registros, periodo))
    form = PropuestaForm(request.POST or None, request.FILES or None, initial={'periodo': anterior})
    periodo, registros = None, None
    if request.method == 'POST' and form.is_valid():
        try:
            encabezados, registros = sire.leer_completo(form.cleaned_data['archivo'])
        except sire.ErrorSIRE as exc:
            messages.error(request, str(exc))
        else:
            periodo = form.cleaned_data['periodo']
            _guardar(request, periodo, encabezados, registros)
    elif request.method == 'GET' and request.GET.get('ultimo'):
        periodo, _, registros = _de_sesion(request)
    resultado = None
    if periodo:
        resultado = {**sire.comparar(registros, periodo), 'periodo': periodo, 'total': len(registros)}
    return render(request, 'compras/sire.html', {'form': form, 'r': resultado})
