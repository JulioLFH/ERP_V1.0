"""Licitaciones (cuadro comparativo de ofertas) y contratos marco."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Tercero
from core.utils import excel_response

from . import abastecimiento as ab
from .forms import ContratoForm, LicitacionForm, contrato_formset, licitacion_formset
from .models import ContratoMarco, Licitacion


def _dec(valor):
    try:
        return Decimal(str(valor).replace(',', '').strip()) if str(valor or '').strip() else None
    except InvalidOperation:
        return None


# ---------------------------------------------------------------- licitaciones
@login_required
def licitaciones(request):
    qs = Licitacion.objects.select_related('centro_costo').prefetch_related('ofertas')
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado=estado)
    return render(request, 'compras/licitaciones.html', {'licitaciones': qs[:300], 'estado': estado,
                                                         'estados': Licitacion.ESTADOS})


@login_required
def licitacion_nueva(request):
    form = LicitacionForm(request.POST or None)
    lineas = licitacion_formset()(request.POST or None, prefix='lin')
    if request.method == 'POST' and form.is_valid() and lineas.is_valid():
        datos = [(f.cleaned_data.get('producto'), f.cleaned_data.get('descripcion', ''), f.cleaned_data.get('cantidad'))
                 for f in lineas.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')]
        try:
            lic = ab.crear_licitacion(form.save(commit=False), datos, request.user)
            messages.success(request, f'{lic} creada: envíe la solicitud a los proveedores y registre sus ofertas.')
            return redirect('compras:licitacion', lic.pk)
        except ab.ErrorAbastecimiento as exc:
            form.add_error(None, str(exc))
    return render(request, 'compras/licitacion_form.html', {'form': form, 'lineas': lineas})


@login_required
def licitacion(request, pk):
    lic = get_object_or_404(Licitacion.objects.select_related('centro_costo', 'creado_por', 'adjudicado_por'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'oferta':
                proveedor = get_object_or_404(Tercero, pk=request.POST.get('proveedor'))
                precios = {li.pk: _dec(request.POST.get(f'precio_{li.pk}')) for li in lic.lineas.all()}
                moneda = request.POST.get('moneda') if request.POST.get('moneda') in ('PEN', 'USD') else 'PEN'
                ab.registrar_oferta(lic, proveedor, precios, {
                    'moneda': moneda, 'tipo_cambio': _dec(request.POST.get('tipo_cambio')) or Decimal('1'),
                    'plazo_entrega': int(request.POST.get('plazo_entrega') or 0),
                    'condicion_pago': request.POST.get('condicion_pago', '')[:100],
                    'observaciones': request.POST.get('observaciones', '')[:250]})
                messages.success(request, f'Oferta de {proveedor.nombre} registrada.')
            elif accion == 'quitar_oferta' and lic.estado == 'ABIERTA':
                lic.ofertas.filter(pk=request.POST.get('oferta')).delete()
            elif accion == 'adjudicar':
                eleccion = {li.pk: int(request.POST[f'gana_{li.pk}']) for li in lic.lineas.all()
                            if (request.POST.get(f'gana_{li.pk}') or '').isdigit()}
                ordenes = ab.adjudicar(lic, eleccion, request.user, request.POST.get('justificacion', ''))
                messages.success(request, 'Adjudicada. Órdenes de compra generadas (pendientes de aprobar): ' +
                                 ', '.join(o.numero for o in ordenes))
            elif accion == 'anular' and lic.estado == 'ABIERTA':
                lic.estado = 'ANULADA'
                lic.save(update_fields=['estado'])
        except (ab.ErrorAbastecimiento, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect('compras:licitacion', pk)
    ofertas, filas, totales = ab.cuadro(lic)
    if request.GET.get('formato') == 'excel':
        if request.GET.get('solicitud'):  # solicitud de cotización para enviar a los proveedores
            return excel_response(f'Solicitud_{lic.numero}', f'SOLICITUD DE COTIZACIÓN {lic.numero}: {lic.descripcion}'
                                  f'{" - ofertas hasta el " + lic.fecha_limite.strftime("%d/%m/%Y") if lic.fecha_limite else ""}',
                                  ['Ítem', 'Código', 'Descripción', 'Cantidad', 'U.M.', 'Precio unitario sin IGV',
                                   'Plazo de entrega (días)'],
                                  [[n, f['l'].producto.codigo if f['l'].producto else '', f['l'].descripcion,
                                    f['l'].cantidad, f['l'].producto.unidad if f['l'].producto else '', '', '']
                                   for n, f in enumerate(filas, 1)])
        return excel_response(f'Cuadro_comparativo_{lic.numero}', f'CUADRO COMPARATIVO {lic.numero}: {lic.descripcion}',
                              ['Ítem', 'Cantidad'] + [f'{o.proveedor.nombre} (S/ unit.)' for o in ofertas],
                              [[f['l'].descripcion, f['l'].cantidad] + [c['pen'] or '' for c in f['celdas']]
                               for f in filas] + [['TOTAL', ''] + [t['total'] for t in totales]])
    return render(request, 'compras/licitacion.html', {
        'lic': lic, 'ofertas': ofertas, 'filas': filas, 'totales': totales, 'abierta': lic.estado == 'ABIERTA'})


# ---------------------------------------------------------------- contratos marco
@login_required
def contratos(request):
    qs = ContratoMarco.objects.select_related('proveedor')
    estado = request.GET.get('estado', 'VIGENTE')
    if estado:
        qs = qs.filter(estado=estado)
    return render(request, 'compras/contratos.html', {'contratos': qs[:300], 'estado': estado,
                                                      'estados': ContratoMarco.ESTADOS})


def _guardar_contrato(request, contrato):
    form = ContratoForm(request.POST or None, instance=contrato)
    lineas = contrato_formset()(request.POST or None, instance=contrato, prefix='lin')
    if request.method == 'POST' and form.is_valid() and lineas.is_valid():
        with transaction.atomic():
            nuevo = not contrato.pk
            c = form.save(commit=False)
            if nuevo:
                ab.crear_contrato(c, request.user)
            else:
                c.save()
            lineas.instance = c
            lineas.save()
        messages.success(request, f'{c} guardado.')
        return redirect('compras:contrato', c.pk)
    return render(request, 'compras/contrato_form.html', {'form': form, 'lineas': lineas, 'contrato': contrato})


@login_required
def contrato_nuevo(request):
    return _guardar_contrato(request, ContratoMarco())


@login_required
def contrato_editar(request, pk):
    return _guardar_contrato(request, get_object_or_404(ContratoMarco, pk=pk))


@login_required
def contrato(request, pk):
    c = get_object_or_404(ContratoMarco.objects.select_related('proveedor', 'creado_por'), pk=pk)
    filas, usado = ab.consumo(c)
    return render(request, 'compras/contrato.html', {
        'c': c, 'filas': filas, 'usado': usado,
        'saldo_monto': c.monto_maximo - usado if c.monto_maximo is not None else None,
        'ordenes': c.ordenes.exclude(estado='ANULADO').order_by('-fecha')})
