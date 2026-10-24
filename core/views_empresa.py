"""Ajustes › Giro y régimen tributario (asistente de configuración de la empresa) y Parámetros tributarios."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from . import giro, parametros
from .forms import BootstrapMixin
from .models import Empresa, ParametroTributario


class GiroForm(BootstrapMixin, forms.Form):
    giros = forms.MultipleChoiceField(label='Giro del negocio (puede marcar más de uno)', choices=Empresa.GIROS,
                                      widget=forms.CheckboxSelectMultiple)
    ciiu = forms.CharField(label='CIIU', max_length=10, required=False)
    actividad = forms.CharField(label='Actividad económica (SUNAT)', max_length=200, required=False)
    regimen = forms.ChoiceField(label='Régimen tributario', choices=Empresa.REGIMENES)
    ingresos_uit = forms.DecimalField(label='Ingresos anuales estimados (UIT)', required=False, min_value=0,
                                      max_digits=12, decimal_places=2,
                                      help_text='Para indicar los libros obligatorios')
    sistema_costeo = forms.ChoiceField(label='Costeo', choices=Empresa.COSTEOS)
    ocultos = forms.MultipleChoiceField(label='Módulos que la empresa no usa (no se muestran a nadie)',
                                        required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .modulos import MODULOS
        self.fields['ocultos'].choices = [(m['clave'], m['nombre']) for m in MODULOS
                                          if m['clave'] not in giro.SIEMPRE]


@login_required
def empresa_giro(request):
    empresa = Empresa.actual()
    hoy = timezone.localdate()
    inicial = {'giros': empresa.giros, 'ciiu': empresa.ciiu, 'actividad': empresa.actividad_economica,
               'regimen': empresa.regimen_tributario, 'sistema_costeo': empresa.sistema_costeo,
               'ocultos': empresa.modulos_ocultos}
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if accion == 'sunat':
        from .padron_ruc import ErrorPadron, consultar
        try:
            datos = consultar(empresa.ruc)
            actividad = datos.get('actividad_economica') or ''
            codigo = re.search(r'\b(\d{4,5})\b', actividad)
            inicial.update(actividad=actividad[:200], ciiu=codigo.group(1) if codigo else empresa.ciiu)
            messages.info(request, 'Actividad económica traída de SUNAT: revise y guarde.')
        except ErrorPadron as exc:
            messages.error(request, str(exc))
        form = GiroForm(initial=inicial)
    elif accion == 'sugerir':
        form = GiroForm(request.POST)
        if form.is_valid():
            datos = dict(form.cleaned_data)
            sugerido = giro.GIROS
            costeos = {sugerido[g][3] for g in datos['giros']}
            inicial = {**datos, 'ocultos': giro.sugerir_ocultos(datos['giros']),
                       'sistema_costeo': 'PROCESOS' if 'PROCESOS' in costeos else
                       'ORDENES' if 'ORDENES' in costeos else 'NATURALEZA'}
            form = GiroForm(initial=inicial)
            messages.info(request, 'Módulos y costeo sugeridos según el giro: revise y guarde.')
    elif accion == 'guardar':
        form = GiroForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            mensajes = giro.aplicar(empresa, d['giros'], d['regimen'], d['sistema_costeo'], d.get('ciiu') or '',
                                    d.get('actividad') or '', d.get('ocultos'))
            messages.success(request, 'Giro y régimen guardados. ' + ' '.join(mensajes))
            return redirect('empresa_giro')
    else:
        form = GiroForm(initial=inicial)
    regimen = form['regimen'].value() or empresa.regimen_tributario
    ingresos = form['ingresos_uit'].value()
    try:
        ingresos = Decimal(str(ingresos)) if ingresos not in (None, '') else None
    except InvalidOperation:
        ingresos = None
    return render(request, 'core/empresa_giro.html', {
        'form': form, 'empresa': empresa, 'giros': giro.GIROS,
        'obligaciones': giro.obligaciones(regimen, ingresos, hoy), 'uit': parametros.uit(hoy),
        'regimen_nombre': dict(Empresa.REGIMENES).get(regimen, regimen)})


@login_required
def parametros_tributarios(request):
    hoy = timezone.localdate()
    if request.method == 'POST':
        clave = request.POST.get('clave')
        if clave not in parametros.DEFECTOS:
            messages.error(request, 'Parámetro no válido.')
            return redirect('parametros')
        try:
            valor = Decimal(request.POST.get('valor', '').replace(',', ''))
            desde = date.fromisoformat(request.POST.get('desde', ''))
        except (InvalidOperation, ValueError):
            messages.error(request, 'Indique el valor y la fecha desde la que rige.')
            return redirect('parametros')
        ParametroTributario.objects.update_or_create(
            clave=clave, desde=desde, defaults={'valor': valor, 'nota': request.POST.get('nota', '')[:200],
                                                'usuario': request.user})
        messages.success(request, f'{parametros.DEFECTOS[clave][1]}: {valor:g} desde el {desde:%d/%m/%Y}.')
        return redirect('parametros')
    return render(request, 'core/parametros.html', {'grupos': parametros.tabla(hoy), 'hoy': hoy})
