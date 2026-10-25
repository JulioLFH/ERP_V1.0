"""Pantallas de importaciones: expediente (OC, facturas comerciales, DUA), gastos vinculados reales y estimados,
prorrateo por concepto y liquidación al costo."""
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
from .models import Compra, GastoImportacion, Importacion, OrdenCompra
from core.utils import id_o_none

TIPOS_FACTURA_COMERCIAL = ('01', '00', '91')  # factura, otros y comprobante de no domiciliado (IMP-01)


def facturas_comerciales():
    return Compra.objects.filter(estado='REGISTRADO', tipo_comprobante__in=TIPOS_FACTURA_COMERCIAL)


class ImportacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Importacion
        fields = ['descripcion', 'orden_compra', 'compra', 'dua', 'fecha_llegada', 'metodo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['compra'].queryset = facturas_comerciales()
        remoto(self.fields['compra'], 'facturas_comerciales')
        self.fields['orden_compra'].queryset = OrdenCompra.objects.filter(estado__in=('APROBADO', 'ATENDIDO'))
        self.fields['metodo'].choices = Importacion.METODOS[:3]

    def clean(self):
        datos = super().clean()
        if not datos.get('compra') and not datos.get('orden_compra'):
            raise forms.ValidationError('Indique la orden de compra (para cargar los gastos estimados desde ya) o la '
                                        'factura comercial.')
        return datos


@login_required
def importaciones(request):
    return render(request, 'compras/importaciones.html', {
        'importaciones': Importacion.objects.select_related('compra__tercero', 'orden_compra__tercero')[:200]})


@login_required
def importacion_nueva(request):
    form = ImportacionForm(request.POST or None, initial={'orden_compra': request.GET.get('oc')})
    if request.method == 'POST' and form.is_valid():
        try:
            imp = imp_srv.crear(form.save(commit=False))
            messages.success(request, f'{imp} creada: registre sus gastos (reales o estimados).')
            return redirect('compras:importacion', imp.pk)
        except imp_srv.ErrorImportacion as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva importación'})


def _monto(valor):
    try:
        return Decimal(str(valor).replace(',', '')) if str(valor or '').strip() else None
    except InvalidOperation:
        raise imp_srv.ErrorImportacion('Monto inválido.')


@login_required
def importacion(request, pk):
    imp = get_object_or_404(Importacion.objects.select_related('compra__tercero', 'orden_compra__tercero'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'gasto':
                compra = Compra.objects.filter(pk=id_o_none(request.POST.get('compra'))).first() if request.POST.get('compra') \
                    else None
                mov = Movimiento.objects.filter(pk=id_o_none(request.POST.get('movimiento'))).first() \
                    if request.POST.get('movimiento') else None
                imp_srv.agregar_gasto(imp, request.POST.get('concepto', 'OTRO'), _monto(request.POST.get('monto')),
                                      request.POST.get('descripcion', ''), compra, mov, request.POST.get('metodo', ''))
                messages.success(request, 'Gasto agregado.')
            elif accion == 'estimado':
                imp_srv.estimar(imp, request.POST.get('concepto', 'OTRO'), _monto(request.POST.get('monto')) or 0,
                                request.POST.get('metodo', ''))
                messages.success(request, 'Gasto estimado guardado.')
            elif accion == 'quitar':
                g = get_object_or_404(GastoImportacion, pk=request.POST.get('gasto'), importacion=imp)
                if imp.ajustes.exists():
                    raise imp_srv.ErrorImportacion('La importación ya se liquidó: no se quitan gastos (agregue uno '
                                                   'negativo con una nota de crédito si corresponde).')
                g.delete()
            elif accion == 'metodo' and not imp.ajustes.exists():
                imp.metodo = request.POST.get('metodo', imp.metodo)
                imp.save(update_fields=['metodo'])
            elif accion == 'manual' and not imp.ajustes.exists():
                reparto = {}
                for clave, valor in request.POST.items():
                    if clave.startswith('pct_') and str(valor).strip():
                        reparto[clave[4:]] = str(_monto(valor))
                imp.reparto_manual, imp.metodo = reparto, 'MANUAL'
                imp.save(update_fields=['reparto_manual', 'metodo'])
                messages.success(request, 'Reparto manual guardado.')
            elif accion == 'factura':
                compra = facturas_comerciales().filter(pk=request.POST.get('compra') or 0).first()
                if compra is None:
                    raise imp_srv.ErrorImportacion('Elija la factura comercial.')
                if imp.compra_id is None:
                    imp.compra = compra
                    imp.save(update_fields=['compra'])
                elif compra.pk != imp.compra_id:
                    imp.facturas.add(compra)
                messages.success(request, f'{compra} agregada al expediente.')
            elif accion == 'liquidar':
                aplicados = imp_srv.liquidar(imp)
                messages.success(request, f'{imp} liquidada: {len(aplicados)} producto(s) con su costo actualizado.')
        except imp_srv.ErrorImportacion as exc:
            messages.error(request, str(exc))
        return redirect('compras:importacion', pk)
    try:
        filas = imp_srv.prorrateo(imp) if (imp.gastos.exists() or imp.estimados.exists()) else []
        error = ''
    except imp_srv.ErrorImportacion as exc:
        filas, error = [], str(exc)
    lineas = imp_srv._lineas(imp)
    desde = timezone.localdate() - timedelta(days=365)
    # pagos de Finanzas sin factura (tributos de la DUA pagados al banco, etc.) que aún no están en una importación
    usados = GastoImportacion.objects.filter(movimiento__isnull=False).values_list('movimiento_id', flat=True)
    pagos = (Movimiento.objects.filter(tipo='EGRESO', compra__isnull=True, fecha__gte=desde)
             .exclude(concepto='TRANSFERENCIA').exclude(pk__in=usados).select_related('cuenta').order_by('-fecha')[:300])
    fob, moneda, fob_pen = imp.fob
    return render(request, 'compras/importacion.html', {
        'imp': imp, 'gastos': imp.gastos.select_related('compra', 'movimiento'), 'filas': filas, 'error': error,
        'avisos': imp_srv.avisos(imp, lineas), 'lineas': [{**f, 'pct': (imp.reparto_manual or {}).get(str(pid), '')}
                                                          for pid, f in sorted(lineas.items())],
        'conceptos': GastoImportacion.CONCEPTOS, 'metodos': Importacion.METODOS, 'metodos_gasto': Importacion.METODOS[:3],
        'pagos': pagos, 'estimados': imp.estimados.all(), 'reales': {g.concepto for g in imp.gastos.all()},
        'ajustes': imp.ajustes.select_related('producto'), 'fob': fob, 'moneda': moneda, 'fob_pen': fob_pen,
        'tc': (fob_pen / fob).quantize(Decimal('0.001')) if fob and moneda == 'USD' else None,
        'facturas': imp.facturas_comerciales})
