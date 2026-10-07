"""Préstamos bancarios y leasing."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from core.utils import excel_response

from . import prestamos as srv
from .forms import PrestamoForm
from .models import CuotaPrestamo, Prestamo
from .operaciones import ErrorTesoreria


@login_required
def prestamos(request):
    qs = Prestamo.objects.select_related('entidad', 'cuenta')
    estado = request.GET.get('estado', 'VIGENTE')
    if estado:
        qs = qs.filter(estado=estado)
    saldos, proximas = srv.resumen()
    return render(request, 'finanzas/prestamos.html', {
        'prestamos': [srv.sincronizar(p) for p in qs[:200]], 'estado': estado, 'estados': Prestamo.ESTADOS,
        'saldos': saldos, 'proximas': proximas})


@login_required
def prestamo_nuevo(request):
    form = PrestamoForm(request.POST or None, initial={'tipo': request.GET.get('tipo', 'PRESTAMO')})
    simulacion = None
    if request.method == 'POST' and form.is_valid():
        p = form.save(commit=False)
        if request.POST.get('accion') == 'simular':
            simulacion = srv.cronograma(p.monto, p.tasa_anual, p.plazo, p.meses_entre_cuotas, p.primera_cuota,
                                        p.opcion_compra, p.tipo == 'LEASING')
            for f in simulacion:
                f['cuota'] = f['capital'] + f['interes'] + f['igv']
        else:
            try:
                srv.crear(p, request.user, form.cleaned_data.get('comision_cuota') or 0)
                messages.success(request, f'{p} registrado con {p.plazo} cuotas' +
                                 (' y su desembolso en caja y bancos.' if p.tipo == 'PRESTAMO' else '.'))
                return redirect('finanzas:prestamo', p.pk)
            except ErrorTesoreria as exc:
                form.add_error(None, str(exc))
    return render(request, 'finanzas/prestamo_form.html', {'form': form, 'simulacion': simulacion})


@login_required
def prestamo(request, pk):
    p = srv.sincronizar(get_object_or_404(Prestamo.objects.select_related('entidad', 'cuenta', 'cuenta_activo'),
                                          pk=pk))
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'pagar':
                cuota = get_object_or_404(CuotaPrestamo, pk=request.POST.get('cuota'), prestamo=p)
                fecha = date.fromisoformat(request.POST.get('fecha') or date.today().isoformat())
                mov = srv.pagar_cuota(cuota, fecha, request.user, request.POST.get('numero_operacion', ''))
                messages.success(request, f'Cuota {cuota.numero} pagada ({mov.voucher}).')
            elif accion == 'anular':
                srv.anular(p, request.POST.get('motivo', ''), request.user)
                messages.success(request, f'{p} anulado.')
        except (ErrorTesoreria, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect('finanzas:prestamo', pk)
    cuotas = list(p.cuotas.prefetch_related('pagos'))
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Cronograma_{p.numero}', f'CRONOGRAMA {p} - {p.entidad.nombre}', [
            'N°', 'Vence', 'Amortización', 'Interés', 'Comisiones', 'IGV', 'Cuota', 'Saldo', 'Pagada'],
            [[c.numero, c.fecha, c.capital, c.interes, c.comision, c.igv, c.cuota, c.saldo,
              c.pago.fecha if c.pagada else ''] for c in cuotas])
    siguiente = next((c for c in cuotas if not c.pagada), None)
    return render(request, 'finanzas/prestamo.html', {
        'p': p, 'cuotas': cuotas, 'siguiente': siguiente, 'hoy': date.today(),
        'totales': {k: sum(getattr(c, k) for c in cuotas) for k in ('capital', 'interes', 'comision', 'igv', 'cuota')},
        'desembolso': p.desembolso})
