from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.forms import BootstrapMixin
from core.permisos import puede

from . import servicios as srv
from .models import HojaReparto, Parada


class HojaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = HojaReparto
        fields = ['fecha', 'vehiculo', 'conductor', 'almacen', 'zona', 'ayudante', 'observaciones']
        widgets = {'observaciones': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from logistica.models import Conductor, Vehiculo
        self.fields['vehiculo'].queryset = Vehiculo.objects.filter(activo=True)
        self.fields['conductor'].queryset = Conductor.objects.filter(activo=True)


def _fecha(texto, defecto):
    try:
        return date.fromisoformat(texto or '')
    except ValueError:
        return defecto


@login_required
def lista(request):
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde'), hoy - timedelta(days=7))
    hasta = _fecha(request.GET.get('hasta'), hoy + timedelta(days=7))
    hojas = HojaReparto.objects.filter(fecha__range=(desde, hasta)).select_related('vehiculo', 'conductor')
    if request.GET.get('estado') in dict(HojaReparto.ESTADOS):
        hojas = hojas.filter(estado=request.GET['estado'])
    filas = [(h, srv.resumen(h)) for h in hojas]
    return render(request, 'distribucion/lista.html', {
        'filas': filas, 'desde': desde, 'hasta': hasta, 'estados': HojaReparto.ESTADOS,
        'estado': request.GET.get('estado', '')})


def _documentos(request):
    from logistica.models import GuiaRemision
    from ventas.models import Cotizacion
    ids_g = [int(x) for x in request.POST.getlist('guia') if x.isdigit()]
    ids_p = [int(x) for x in request.POST.getlist('pedido') if x.isdigit()]
    # en el orden en que se marcaron (es el orden de visita inicial)
    guias = sorted(GuiaRemision.objects.filter(pk__in=ids_g).select_related('destinatario'),
                   key=lambda g: ids_g.index(g.pk))
    pedidos = sorted(Cotizacion.objects.filter(pk__in=ids_p, tipo='PED').select_related('tercero'),
                     key=lambda p: ids_p.index(p.pk))
    return guias, pedidos


@login_required
def nueva(request):
    hoy = timezone.localdate()
    form = HojaForm(request.POST or None, initial={'fecha': hoy})
    if request.method == 'POST' and form.is_valid():
        guias, pedidos = _documentos(request)
        hoja = form.save(commit=False)
        hoja.creado_por = request.user
        hoja.save()
        n = srv.agregar(hoja, guias, pedidos)
        messages.success(request, f'Hoja {hoja.numero} programada con {n} parada(s).')
        return redirect('distribucion:hoja', hoja.pk)
    return render(request, 'distribucion/nueva.html', {
        'form': form, 'guias': srv.guias_disponibles(hoy), 'pedidos': srv.pedidos_disponibles(hoy)})


@login_required
def hoja(request, pk):
    h = get_object_or_404(HojaReparto.objects.select_related('vehiculo', 'conductor', 'almacen'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'agregar':
                guias, pedidos = _documentos(request)
                n = srv.agregar(h, guias, pedidos)
                messages.success(request, f'{n} parada(s) agregadas.')
            elif accion == 'orden':
                srv.reordenar(h, [int(x) for x in request.POST.get('orden', '').split(',') if x.isdigit()])
                messages.success(request, 'Orden de visita actualizado.')
            elif accion == 'quitar':
                srv.quitar(get_object_or_404(Parada, pk=request.POST.get('parada') or 0, hoja=h))
                messages.success(request, 'Parada quitada.')
            elif accion == 'entrega':
                p = get_object_or_404(Parada, pk=request.POST.get('parada') or 0, hoja=h)
                srv.registrar_entrega(p, request.POST.get('estado'), request.POST.get('motivo', ''),
                                      request.POST.get('recibido_por', ''), request.POST.get('cobrado') or '0',
                                      request.POST.get('observacion', ''))
                messages.success(request, f'Parada {p.orden}: {p.get_estado_display().lower()}.')
            elif accion == 'iniciar':
                srv.iniciar(h)
                messages.success(request, f'{h.numero} salió a ruta.')
            elif accion == 'cerrar':
                srv.cerrar(h)
                messages.success(request, f'{h.numero} cerrada. Lo no entregado queda disponible para otra hoja.')
            elif accion == 'anular':
                srv.anular(h)
                messages.success(request, f'{h.numero} anulada.')
        except srv.ErrorReparto as exc:
            messages.error(request, str(exc))
        return redirect('distribucion:hoja', pk)
    activa = h.estado in srv.ACTIVAS
    return render(request, 'distribucion/hoja.html', {
        'h': h, 'paradas': h.paradas.select_related('cliente', 'guia', 'pedido'), 'resumen': srv.resumen(h),
        'activa': activa, 'programar': puede(request.user, 'distribucion.programar'), 'motivos': Parada.MOTIVOS[1:],
        'guias': srv.guias_disponibles(h.fecha) if activa else [],
        'pedidos': srv.pedidos_disponibles(h.fecha) if activa else []})


@login_required
def imprimir(request, pk):
    h = get_object_or_404(HojaReparto.objects.select_related('vehiculo', 'conductor', 'almacen'), pk=pk)
    return render(request, 'distribucion/imprimir.html', {
        'h': h, 'paradas': h.paradas.select_related('cliente', 'guia', 'pedido'), 'resumen': srv.resumen(h)})


@login_required
def movil(request):
    """El conductor registra cada entrega desde el celular (con la ubicación, si la permite)."""
    hojas = list(srv.hojas_del_conductor(request.user))
    if puede(request.user, 'distribucion.programar'):  # el programador puede abrir cualquier hoja activa
        hojas += [h for h in HojaReparto.objects.filter(estado__in=srv.ACTIVAS).select_related('vehiculo', 'conductor')
                  if h not in hojas]
    actual = next((h for h in hojas if str(h.pk) == request.GET.get('hoja')), hojas[0] if hojas else None)
    if request.method == 'POST' and actual is not None:
        p = get_object_or_404(Parada, pk=request.POST.get('parada') or 0, hoja=actual)
        try:
            srv.registrar_entrega(p, request.POST.get('estado'), request.POST.get('motivo', ''),
                                  request.POST.get('recibido_por', ''), request.POST.get('cobrado') or '0',
                                  request.POST.get('observacion', ''), request.POST.get('latitud'),
                                  request.POST.get('longitud'))
            messages.success(request, f'{p.cliente.nombre}: {p.get_estado_display().lower()}.')
        except srv.ErrorReparto as exc:
            messages.error(request, str(exc))
        return redirect(f'{reverse("distribucion:movil")}?hoja={actual.pk}')
    return render(request, 'distribucion/movil.html', {
        'hojas': hojas, 'actual': actual, 'motivos': Parada.MOTIVOS[1:],
        'paradas': actual.paradas.select_related('cliente', 'guia', 'pedido') if actual else [],
        'resumen': srv.resumen(actual) if actual else None})


@login_required
def reporte(request):
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde'), hoy.replace(day=1))
    hasta = _fecha(request.GET.get('hasta'), hoy)
    return render(request, 'distribucion/reporte.html', {'desde': desde, 'hasta': hasta,
                                                         **srv.reporte(desde, hasta)})
