"""Pantallas de importaciones: expediente, gastos vinculados, prorrateo y liquidación al costo."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.forms import BootstrapMixin, remoto
from finanzas.models import Movimiento

from . import importaciones as imp_srv
from .models import Compra, GastoImportacion, Importacion


class ImportacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Importacion
        fields = ['descripcion', 'compra', 'dua', 'fecha_llegada', 'metodo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['compra'].queryset = Compra.objects.filter(estado='REGISTRADO').exclude(
            tipo_comprobante__in=['07', '08'])
        remoto(self.fields['compra'], 'compras')


@login_required
def importaciones(request):
    return render(request, 'compras/importaciones.html', {
        'importaciones': Importacion.objects.select_related('compra__tercero')[:200]})


@login_required
def importacion_nueva(request):
    form = ImportacionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        imp = imp_srv.crear(form.save(commit=False))
        messages.success(request, f'{imp} creada: registre sus gastos vinculados.')
        return redirect('compras:importacion', imp.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva importación'})


def _monto(valor):
    try:
        return Decimal(str(valor).replace(',', '')) if str(valor or '').strip() else None
    except InvalidOperation:
        raise imp_srv.ErrorImportacion('Monto inválido.')


@login_required
def importacion(request, pk):
    imp = get_object_or_404(Importacion.objects.select_related('compra__tercero'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'gasto':
                compra = Compra.objects.filter(pk=request.POST.get('compra')).first() if request.POST.get('compra') \
                    else None
                mov = Movimiento.objects.filter(pk=request.POST.get('movimiento')).first() \
                    if request.POST.get('movimiento') else None
                imp_srv.agregar_gasto(imp, request.POST.get('concepto', 'OTRO'), _monto(request.POST.get('monto')),
                                      request.POST.get('descripcion', ''), compra, mov)
                messages.success(request, 'Gasto agregado.')
            elif accion == 'quitar':
                g = get_object_or_404(GastoImportacion, pk=request.POST.get('gasto'), importacion=imp)
                if imp.ajustes.exists():
                    raise imp_srv.ErrorImportacion('La importación ya se liquidó: no se quitan gastos (agregue uno '
                                                   'negativo con una nota de crédito si corresponde).')
                g.delete()
            elif accion == 'metodo' and not imp.ajustes.exists():
                imp.metodo = request.POST.get('metodo', imp.metodo)
                imp.save(update_fields=['metodo'])
            elif accion == 'liquidar':
                aplicados = imp_srv.liquidar(imp)
                messages.success(request, f'{imp} liquidada: {len(aplicados)} producto(s) con su costo actualizado.')
        except imp_srv.ErrorImportacion as exc:
            messages.error(request, str(exc))
        return redirect('compras:importacion', pk)
    try:
        filas = imp_srv.prorrateo(imp) if imp.gastos.exists() else []
        error = ''
    except imp_srv.ErrorImportacion as exc:
        filas, error = [], str(exc)
    desde = timezone.localdate() - timedelta(days=120)
    pagos = Movimiento.objects.filter(tipo='EGRESO', compra__isnull=True, fecha__gte=desde).exclude(
        concepto='TRANSFERENCIA').select_related('cuenta').order_by('-fecha')[:200]
    return render(request, 'compras/importacion.html', {
        'imp': imp, 'gastos': imp.gastos.select_related('compra', 'movimiento'), 'filas': filas, 'error': error,
        'conceptos': GastoImportacion.CONCEPTOS, 'metodos': Importacion.METODOS, 'pagos': pagos,
        'ajustes': imp.ajustes.select_related('producto')})
