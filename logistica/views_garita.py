"""Control de garita (LOG-04): ingreso y salida de vehículos con mercadería contra la orden de compra o la guía,
enlazado a la recepción o al despacho del almacén. Los montos solo para quien tenga «Ver montos en garita»."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Serie

from .forms import GaritaForm
from .models import RegistroGarita


@login_required
def garita_lista(request):
    qs = RegistroGarita.objects.select_related('tercero', 'orden_compra', 'guia', 'operacion')
    if request.GET.get('tipo'):
        qs = qs.filter(tipo=request.GET['tipo'])
    if request.GET.get('en_planta'):
        qs = qs.filter(salida__isnull=True)
    if request.GET.get('fecha'):
        qs = qs.filter(ingreso__date=request.GET['fecha'])
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(numero__icontains=q) | Q(placa__icontains=q) | Q(conductor__icontains=q) |
                       Q(documento__icontains=q) | Q(tercero__nombre__icontains=q))
    return render(request, 'logistica/garita_lista.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'tipos': RegistroGarita.TIPOS,
        'en_planta': RegistroGarita.objects.filter(salida__isnull=True).count()})


@login_required
def garita_nuevo(request):
    inicial = {'ingreso': timezone.localtime().strftime('%Y-%m-%dT%H:%M')}
    inicial.update({k: request.GET[k] for k in ('tipo', 'orden_compra', 'guia') if request.GET.get(k)})
    form = GaritaForm(request.POST or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        reg = form.save(commit=False)
        s, n = Serie.siguiente('GAR', 'GA')
        reg.numero, reg.registrado_por = f'{s}-{n}', request.user
        if reg.guia_id and not reg.tercero_id:
            reg.tercero = reg.guia.destinatario
        reg.save()
        messages.success(request, f'{reg} registrado: {reg.placa} en planta.')
        return redirect('logistica:garita', reg.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Registro de garita: ingreso de vehículo'})


def _operaciones_candidatas(reg):
    """Recepciones de la OC o despachos de la guía (o de su venta) para enlazar el registro."""
    from inventario.models import Operacion
    qs = Operacion.objects.exclude(estado='ANULADO').select_related('tipo')
    if reg.orden_compra_id:
        return qs.filter(orden_compra=reg.orden_compra)
    if reg.guia_id:
        filtro = Q(referencia__icontains=f'{reg.guia.serie}-{reg.guia.numero}')
        if reg.guia.venta_id:
            filtro |= Q(venta=reg.guia.venta)
        return qs.filter(filtro)
    if reg.tercero_id:
        return qs.filter(tercero=reg.tercero, fecha__gte=timezone.localtime(reg.ingreso).date() - timedelta(days=3))
    return qs.none()


@login_required
def garita(request, pk):
    reg = get_object_or_404(RegistroGarita.objects.select_related('tercero', 'orden_compra', 'guia', 'operacion'),
                            pk=pk)
    candidatas = _operaciones_candidatas(reg)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'salida':
            if reg.salida:
                messages.error(request, 'La salida ya está registrada.')
            else:
                reg.salida, reg.salida_por = timezone.now(), request.user
                reg.save(update_fields=['salida', 'salida_por'])
                messages.success(request, f'Salida de {reg.placa} registrada.')
        elif accion == 'enlazar':
            op = candidatas.filter(pk=request.POST.get('operacion') or 0).first()
            if op is None:
                messages.error(request, 'Elija una recepción o despacho de la lista.')
            else:
                reg.operacion = op
                reg.save(update_fields=['operacion'])
                messages.success(request, f'Enlazado a {op}.')
        return redirect('logistica:garita', pk)
    return render(request, 'logistica/garita.html', {'r': reg, 'candidatas': candidatas})
