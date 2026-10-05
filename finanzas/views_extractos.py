"""Conciliación bancaria automática: carga del estado de cuenta del banco y emparejamiento con tesorería."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.forms import BootstrapMixin

from . import extractos as servicio
from .models import Cuenta, Extracto, LineaExtracto, Movimiento


class CargarExtractoForm(BootstrapMixin, forms.Form):
    cuenta = forms.ModelChoiceField(Cuenta.objects.none(), label='Cuenta bancaria')
    archivo = forms.FileField(label='Estado de cuenta (.xlsx o .csv)',
                              help_text='El archivo que descarga de la banca por internet de BCP, BBVA o Interbank '
                                        '(o la plantilla del ERP)')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = Cuenta.objects.filter(activo=True, tipo='BANCO')


@login_required
def extractos(request):
    form = CargarExtractoForm(request.POST or None, request.FILES or None,
                              initial={'cuenta': request.GET.get('cuenta')})
    if request.method == 'POST' and form.is_valid():
        try:
            extracto, repetidas, n = servicio.cargar(form.cleaned_data['cuenta'], form.cleaned_data['archivo'],
                                                     request.user)
        except servicio.ErrorExtracto as exc:
            messages.error(request, str(exc))
        else:
            texto = (f'Estado de cuenta ({extracto.formato}) cargado: {extracto.lineas.count()} líneas, '
                     f'{n} conciliadas automáticamente.')
            if repetidas:
                texto += f' {repetidas} líneas ya estaban cargadas y se omitieron.'
            messages.success(request, texto)
            return redirect('finanzas:extracto', extracto.pk)
    return render(request, 'finanzas/extractos.html', {
        'form': form, 'extractos': Extracto.objects.select_related('cuenta')[:50]})


def _candidatos(linea, libres):
    """Movimientos sin conciliar del mismo tipo, cerca en fecha; primero los de igual importe."""
    return sorted((m for m in libres if m.tipo == linea.tipo and abs((m.fecha - linea.fecha).days) <= 31),
                  key=lambda m: (m.monto != abs(linea.monto), abs(m.monto - abs(linea.monto)),
                                 abs((m.fecha - linea.fecha).days)))[:6]


@login_required
def extracto(request, pk):
    ext = get_object_or_404(Extracto.objects.select_related('cuenta'), pk=pk)
    lineas = list(ext.lineas.select_related('movimiento'))
    pendientes = [l for l in lineas if l.estado == 'PENDIENTE']
    if pendientes:
        libres = list(Movimiento.objects.filter(
            cuenta=ext.cuenta, conciliado=False, lineas_extracto__isnull=True,
            fecha__range=[min(l.fecha for l in pendientes) - timedelta(days=31),
                          max(l.fecha for l in pendientes) + timedelta(days=31)]))
        for l in pendientes:
            l.candidatos = _candidatos(l, libres)
            l.gasto = servicio.es_gasto_bancario(l)
    # movimientos de tesorería del rango que el banco no muestra (cheques no cobrados, depósitos en tránsito)
    en_transito = Movimiento.objects.filter(cuenta=ext.cuenta, conciliado=False, fecha__lte=ext.hasta) \
        .order_by('fecha') if ext.hasta else Movimiento.objects.none()
    saldo_libros = ext.cuenta.saldo_al(ext.hasta) if ext.hasta else None
    return render(request, 'finanzas/extracto.html', {
        'ext': ext, 'lineas': lineas, 'resumen': ext.resumen, 'en_transito': en_transito,
        'saldo_libros': saldo_libros, 'gastos_pendientes': sum(1 for l in pendientes if l.gasto),
        'diferencia': (ext.saldo_final - saldo_libros) if ext.saldo_final is not None and saldo_libros is not None
        else None})


@login_required
def extracto_accion(request, pk):
    ext = get_object_or_404(Extracto, pk=pk)
    if request.method != 'POST':
        return redirect('finanzas:extracto', pk)
    accion = request.POST.get('accion')
    try:
        with transaction.atomic():
            if accion == 'conciliar':
                n = servicio.conciliar(ext)
                messages.success(request, f'{n} líneas conciliadas automáticamente.')
            elif accion == 'gastos':
                lineas = [l for l in ext.lineas.filter(estado='PENDIENTE') if servicio.es_gasto_bancario(l)]
                for l in lineas:
                    servicio.crear_movimiento(l, request.user, 'GASTO_BANCARIO')
                messages.success(request, f'{len(lineas)} comisiones / ITF registrados como gastos bancarios.')
            else:
                linea = get_object_or_404(LineaExtracto, pk=request.POST.get('linea'), extracto=ext)
                if accion == 'crear':
                    mov = servicio.crear_movimiento(linea, request.user, request.POST.get('concepto') or None)
                    messages.success(request, f'Movimiento {mov.voucher} registrado y conciliado.')
                elif accion == 'enlazar':
                    mov = get_object_or_404(Movimiento, pk=request.POST.get('movimiento'))
                    servicio.enlazar_manual(linea, mov)
                    messages.success(request, f'Línea conciliada con {mov.voucher}.')
                elif accion == 'ignorar':
                    linea.estado, linea.regla = 'IGNORADA', f'Ignorada por {request.user.username}'
                    linea.save(update_fields=['estado', 'regla'])
                elif accion == 'quitar':
                    servicio.quitar(linea)
                    messages.info(request, 'Conciliación de la línea deshecha.')
    except servicio.ErrorExtracto as exc:
        messages.error(request, str(exc))
    return redirect('finanzas:extracto', pk)
