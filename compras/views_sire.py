from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from core.forms import BootstrapMixin
from core.utils import excel_response

from . import sire

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


def _guardar(request, periodo, registros):
    """La última propuesta leída queda en la sesión para exportar el cruce sin volver a subir el archivo."""
    request.session[SESION] = {'periodo': periodo, 'registros': [
        {**r, 'fecha': r['fecha'].isoformat() if r['fecha'] else None,
         'base': str(r['base']), 'igv': str(r['igv']), 'total': str(r['total'])} for r in registros]}


def _de_sesion(request):
    datos = request.session.get(SESION)
    if not datos:
        return None, None
    registros = [{**r, 'fecha': date.fromisoformat(r['fecha']) if r['fecha'] else None,
                  'base': Decimal(r['base']), 'igv': Decimal(r['igv']), 'total': Decimal(r['total'])}
                 for r in datos['registros']]
    return datos['periodo'], registros


def _excel(periodo, r):
    filas = [['Coincide', x['ruc'], x['nombre'], x['tipo'], x['serie'], x['numero'], x['total'], x['compra'].total, 0]
             for x in r['coinciden']]
    filas += [['Diferencia de importe', x['ruc'], x['nombre'], x['tipo'], x['serie'], x['numero'], x['total'],
               x['compra'].total, x['dif']] for x in r['diferencias']]
    filas += [['Solo en SUNAT' + (' (registrada en otro periodo)' if x['otro_periodo'] else ''), x['ruc'],
               x['nombre'], x['tipo'], x['serie'], x['numero'], x['total'], None, None] for x in r['solo_sunat']]
    filas += [['Solo en el ERP', c.tercero.numero_doc, c.tercero.nombre, c.tipo_comprobante, c.serie, c.numero,
               None, c.total, None] for c in r['solo_erp']]
    return excel_response(f'Cruce_SIRE_compras_{periodo}', f'CRUCE PROPUESTA SIRE (RCE) {periodo}',
                          ['Resultado', 'RUC', 'Proveedor', 'Tipo', 'Serie', 'Número', 'Total SUNAT', 'Total ERP',
                           'Diferencia'], filas)


@login_required
def comparar(request):
    hoy = date.today()
    anterior = f'{hoy.year - (hoy.month == 1)}-{(hoy.month - 2) % 12 + 1:02d}'
    if request.GET.get('formato') == 'excel':
        periodo, registros = _de_sesion(request)
        if periodo is None:
            return redirect('compras:sire')
        return _excel(periodo, sire.comparar(registros, periodo))
    form = PropuestaForm(request.POST or None, request.FILES or None, initial={'periodo': anterior})
    periodo, registros = None, None
    if request.method == 'POST' and form.is_valid():
        try:
            registros = sire.leer(form.cleaned_data['archivo'])
        except sire.ErrorSIRE as exc:
            messages.error(request, str(exc))
        else:
            periodo = form.cleaned_data['periodo']
            _guardar(request, periodo, registros)
    elif request.method == 'GET' and request.GET.get('ultimo'):
        periodo, registros = _de_sesion(request)
    resultado = None
    if periodo:
        resultado = {**sire.comparar(registros, periodo), 'periodo': periodo, 'total': len(registros)}
    return render(request, 'compras/sire.html', {'form': form, 'r': resultado})
