from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from compras.models import Compra
from core.forms import periodo_cerrado
from core.models import D0, Tercero, r2
from core.utils import a_fecha, excel_response, fmt_fecha, leer_excel, rango_por_defecto
from core.views import FormGenerico
from ventas.models import Venta

from .forms import CuentaForm, ImportarExtractoForm, MovimientoForm, OperacionForm, TransferenciaForm
from .models import Cuenta, Movimiento


# ---------------------------------------------------------------- cuentas
@login_required
def cuentas(request):
    return render(request, 'finanzas/cuentas.html', {'cuentas': Cuenta.objects.all()})


class CuentaNueva(FormGenerico, CreateView):
    model, form_class, titulo = Cuenta, CuentaForm, 'Nueva cuenta de caja / banco'
    success_url = reverse_lazy('finanzas:cuentas')


class CuentaEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Cuenta, CuentaForm, 'Editar cuenta'
    success_url = reverse_lazy('finanzas:cuentas')


# ---------------------------------------------------------------- movimientos
def _filtrar_movs(request):
    qs = Movimiento.objects.select_related('cuenta', 'tercero', 'venta', 'compra')
    f = request.GET
    if f.get('cuenta'):
        qs = qs.filter(cuenta_id=f['cuenta'])
    if f.get('desde'):
        qs = qs.filter(fecha__gte=f['desde'])
    if f.get('hasta'):
        qs = qs.filter(fecha__lte=f['hasta'])
    for campo in ('tipo', 'concepto'):
        if f.get(campo):
            qs = qs.filter(**{campo: f[campo]})
    if f.get('q'):
        q = f['q']
        qs = qs.filter(Q(glosa__icontains=q) | Q(voucher__icontains=q) | Q(numero_operacion__icontains=q) |
                       Q(tercero__nombre__icontains=q))
    return qs


@login_required
def movimientos(request):
    qs = _filtrar_movs(request)
    if request.GET.get('formato') == 'excel':
        filas = [[m.voucher, fmt_fecha(m.fecha), str(m.cuenta), m.get_tipo_display(), m.get_concepto_display(),
                  m.get_medio_pago_display(), m.numero_operacion, m.tercero.nombre if m.tercero else '',
                  str(m.documento or ''), m.monto if m.tipo == 'INGRESO' else D0,
                  m.monto if m.tipo == 'EGRESO' else D0, m.glosa, 'Sí' if m.conciliado else 'No'] for m in qs]
        enc = ['Voucher', 'Fecha', 'Cuenta', 'Tipo', 'Concepto', 'Medio', 'N° op.', 'Tercero', 'Documento',
               'Ingreso', 'Egreso', 'Glosa', 'Conciliado']
        return excel_response('Movimientos_caja_bancos', 'Movimientos de caja y bancos', enc, filas)
    agg = qs.aggregate(i=Sum('monto', filter=Q(tipo='INGRESO')), e=Sum('monto', filter=Q(tipo='EGRESO')))
    return render(request, 'finanzas/movimientos.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'cuentas': Cuenta.objects.all(),
        'conceptos': Movimiento.CONCEPTOS, 'ingresos': agg['i'] or D0, 'egresos': agg['e'] or D0})


@login_required
def movimiento_nuevo(request):
    initial = {'fecha': date.today(), 'tipo': request.GET.get('tipo', 'EGRESO'), 'cuenta': request.GET.get('cuenta')}
    form = MovimientoForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        mov = form.save()
        messages.success(request, f'Movimiento {mov.voucher} registrado.')
        return redirect('finanzas:movimientos')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Ingreso / egreso de caja y bancos'})


@login_required
def movimiento_eliminar(request, pk):
    mov = get_object_or_404(Movimiento, pk=pk)
    if request.method == 'POST':
        if mov.conciliado:
            messages.error(request, 'No se puede eliminar un movimiento conciliado.')
        elif periodo_cerrado(mov.fecha.strftime('%Y%m')):
            messages.error(request, 'No se puede eliminar: el periodo contable está cerrado.')
        else:
            with transaction.atomic():
                par = mov.transferencia_par or Movimiento.objects.filter(transferencia_par=mov).first()
                if par:
                    Movimiento.objects.filter(pk__in=[mov.pk, par.pk]).update(transferencia_par=None)
                    par.delete()
                mov.delete()
            messages.success(request, 'Movimiento eliminado.')
    return redirect(request.POST.get('next') or 'finanzas:movimientos')


@login_required
def voucher(request, pk):
    mov = get_object_or_404(Movimiento.objects.select_related('cuenta', 'tercero'), pk=pk)
    return render(request, 'finanzas/voucher.html', {'m': mov})


# ---------------------------------------------------------------- cobranzas y pagos
def _cobranza_cfg(modo):
    if modo == 'cobranza':
        return {'modelo': Venta, 'fk': 'venta', 'tipo': 'INGRESO', 'concepto': 'COBRANZA', 'tercero_tipos':
                ['CLIENTE', 'AMBOS'], 'titulo': 'Cobranza de comprobantes de venta', 'etiqueta': 'Cliente'}
    return {'modelo': Compra, 'fk': 'compra', 'tipo': 'EGRESO', 'concepto': 'PAGO', 'tercero_tipos':
            ['PROVEEDOR', 'AMBOS'], 'titulo': 'Pago de comprobantes de compra', 'etiqueta': 'Proveedor'}


def _monto_en_cuenta(aplicado, doc, cuenta, fecha, es_detraccion=False):
    """Convierte lo aplicado al documento (su moneda) a la moneda de la cuenta de caja/banco.

    La detracción de un documento en dólares se deposita en soles al tipo de cambio del documento.
    """
    if doc.moneda == cuenta.moneda:
        return aplicado
    from core.tipo_cambio import venta_del_dia
    tc = doc.tipo_cambio if es_detraccion else venta_del_dia(fecha)
    if doc.moneda == 'USD':
        return r2(aplicado * tc)
    return r2(aplicado / tc)


@login_required
def cobrar_pagar(request, modo):
    cfg = _cobranza_cfg(modo)
    doc_ini = None
    if request.GET.get('doc'):
        doc_ini = get_object_or_404(cfg['modelo'], pk=request.GET['doc'])
    tercero_id = request.GET.get('tercero') or (doc_ini.tercero_id if doc_ini else None)
    tercero = Tercero.objects.filter(pk=tercero_id).first() if tercero_id else None

    pendientes = []
    if tercero:
        qs = (cfg['modelo'].objects.con_saldos().filter(tercero=tercero, estado='REGISTRADO')
              .exclude(tipo_comprobante__in=['07', '08']).order_by('fecha_vencimiento'))
        pendientes = [d for d in qs if d.saldo > 0]

    form = OperacionForm(request.POST or None, initial={
        'fecha': date.today(), 'es_detraccion': request.GET.get('detraccion') == '1'})
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        creados = []
        errores = []
        with transaction.atomic():
            for d in pendientes:
                valor = request.POST.get(f'monto_{d.pk}', '').strip()
                if not valor:
                    continue
                try:
                    monto = Decimal(valor)
                except InvalidOperation:
                    errores.append(f'{d}: monto inválido')
                    continue
                if monto <= 0:
                    continue
                if monto > d.saldo:
                    errores.append(f'{d}: el monto supera el saldo ({d.saldo})')
                    continue
                creados.append(Movimiento.objects.create(
                    cuenta=data['cuenta'], fecha=data['fecha'], tipo=cfg['tipo'],
                    concepto='DETRACCION' if data['es_detraccion'] else cfg['concepto'],
                    medio_pago=data['medio_pago'], numero_operacion=data['numero_operacion'], tercero=tercero,
                    monto=_monto_en_cuenta(monto, d, data['cuenta'], data['fecha'], data['es_detraccion']),
                    monto_doc=monto, glosa=data['glosa'] or f'{cfg["concepto"].title()} {d}', **{cfg['fk']: d}))
            if cfg['tipo'] == 'EGRESO' and creados and not errores:
                # el saldo ya incluye los pagos recién creados: no debe quedar en negativo
                cuenta = data['cuenta']
                saldo_final = cuenta.saldo
                if not cuenta.permite_sobregiro and saldo_final < 0:
                    total = sum(m.monto for m in creados)
                    errores.append(f'Saldo insuficiente en {cuenta}: disponible {cuenta.simbolo} '
                                   f'{saldo_final + total:,.2f}, pagos {cuenta.simbolo} {total:,.2f}.')
            if errores:
                transaction.set_rollback(True)
        if errores:
            for e in errores:
                messages.error(request, e)
        elif not creados:
            messages.warning(request, 'Ingrese al menos un monto.')
        else:
            total = sum(m.monto for m in creados)
            messages.success(request, f'{len(creados)} movimiento(s) registrados por {total:,.2f}.')
            if doc_ini:
                return redirect('ventas:detalle' if modo == 'cobranza' else 'compras:detalle', doc_ini.pk)
            return redirect('finanzas:movimientos')

    terceros = Tercero.objects.filter(activo=True, tipo__in=cfg['tercero_tipos'])
    return render(request, 'finanzas/cobrar_pagar.html', {
        'cfg': cfg, 'modo': modo, 'form': form, 'tercero': tercero, 'terceros': terceros,
        'pendientes': pendientes, 'doc_ini': doc_ini,
        'detraccion': request.GET.get('detraccion') == '1'})


# ---------------------------------------------------------------- transferencias
@login_required
def transferencia(request):
    form = TransferenciaForm(request.POST or None, initial={'fecha': date.today()})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        glosa = d['glosa'] or f'Transferencia {d["origen"].nombre} → {d["destino"].nombre}'
        with transaction.atomic():
            salida = Movimiento.objects.create(
                cuenta=d['origen'], fecha=d['fecha'], tipo='EGRESO', concepto='TRANSFERENCIA',
                medio_pago='TRANSFERENCIA', numero_operacion=d['numero_operacion'], monto=d['monto'], glosa=glosa)
            entrada = Movimiento.objects.create(
                cuenta=d['destino'], fecha=d['fecha'], tipo='INGRESO', concepto='TRANSFERENCIA',
                medio_pago='TRANSFERENCIA', numero_operacion=d['numero_operacion'], monto=d['monto'], glosa=glosa,
                transferencia_par=salida)
        messages.success(request, f'Transferencia registrada ({salida.voucher} / {entrada.voucher}).')
        return redirect('finanzas:movimientos')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Transferencia entre cuentas / depósito de caja'})


# ---------------------------------------------------------------- conciliación bancaria
@login_required
def conciliacion(request):
    cuentas_qs = Cuenta.objects.filter(activo=True)
    cuenta = cuentas_qs.filter(pk=request.GET.get('cuenta') or request.POST.get('cuenta')).first() or cuentas_qs.filter(tipo='BANCO').first()
    hasta = request.GET.get('hasta') or request.POST.get('hasta') or date.today().isoformat()
    saldo_banco = request.GET.get('saldo_banco') or request.POST.get('saldo_banco') or ''

    if request.method == 'POST' and cuenta:
        marcados = set(map(int, request.POST.getlist('mov')))
        movs = cuenta.movimientos.filter(fecha__lte=hasta)
        fecha_c = a_fecha(hasta)
        movs.filter(pk__in=marcados, conciliado=False).update(conciliado=True, fecha_conciliacion=fecha_c)
        movs.exclude(pk__in=marcados).filter(conciliado=True, fecha_conciliacion__gte=fecha_c.replace(day=1)).update(
            conciliado=False, fecha_conciliacion=None)
        messages.success(request, 'Conciliación guardada.')
        return redirect(f'{request.path}?cuenta={cuenta.pk}&hasta={hasta}&saldo_banco={saldo_banco}')

    ctx = {'cuentas': cuentas_qs, 'cuenta': cuenta, 'hasta': hasta, 'saldo_banco': saldo_banco}
    if cuenta:
        fecha = a_fecha(hasta)
        movs = cuenta.movimientos.filter(fecha__lte=fecha).filter(
            Q(conciliado=False) | Q(fecha_conciliacion__gte=fecha.replace(day=1))).order_by('fecha', 'id')
        saldo_libros = cuenta.saldo_al(fecha)
        pendientes = cuenta.movimientos.filter(fecha__lte=fecha, conciliado=False)
        agg = pendientes.aggregate(i=Sum('monto', filter=Q(tipo='INGRESO')), e=Sum('monto', filter=Q(tipo='EGRESO')))
        ing_trans, egr_trans = agg['i'] or D0, agg['e'] or D0
        saldo_conciliado = saldo_libros - ing_trans + egr_trans
        try:
            sb = Decimal(saldo_banco) if saldo_banco else None
        except InvalidOperation:
            sb = None
        ctx.update(movs=movs, saldo_libros=saldo_libros, ing_trans=ing_trans, egr_trans=egr_trans,
                   saldo_conciliado=saldo_conciliado, diferencia=(sb - saldo_conciliado) if sb is not None else None)
    return render(request, 'finanzas/conciliacion.html', ctx)


# ---------------------------------------------------------------- importar extracto bancario
@login_required
def importar_extracto(request):
    if request.GET.get('plantilla'):
        return excel_response('Plantilla_extracto', 'plantilla', ['fecha', 'descripcion', 'monto', 'operacion'],
                              [[date.today().strftime('%d/%m/%Y'), 'ABONO CLIENTE X', 1500, '000123'],
                               [date.today().strftime('%d/%m/%Y'), 'COMISION MANTENIMIENTO', -12.5, '']])
    form = ImportarExtractoForm(request.POST or None, request.FILES or None)
    resultado = None
    if request.method == 'POST' and form.is_valid():
        cuenta = form.cleaned_data['cuenta']
        ok, errores = 0, []
        try:
            filas = leer_excel(form.cleaned_data['archivo'])
        except Exception as exc:
            filas, errores = [], [f'No se pudo leer el archivo: {exc}']
        with transaction.atomic():
            for n, f in enumerate(filas, 2):
                try:
                    monto = Decimal(str(f.get('monto')).replace(',', ''))
                    if monto == 0:
                        continue
                    desc = str(f.get('descripcion') or '')
                    concepto = 'GASTO_BANCARIO' if any(p in desc.upper() for p in ('ITF', 'COMISION', 'MANTENIMIENTO', 'PORTES')) else 'OTRO'
                    Movimiento.objects.create(
                        cuenta=cuenta, fecha=a_fecha(f.get('fecha')), tipo='INGRESO' if monto > 0 else 'EGRESO',
                        concepto=concepto, medio_pago='TRANSFERENCIA', numero_operacion=str(f.get('operacion') or ''),
                        monto=abs(monto), glosa=desc[:250], conciliado=form.cleaned_data['conciliado'],
                        fecha_conciliacion=a_fecha(f.get('fecha')) if form.cleaned_data['conciliado'] else None)
                    ok += 1
                except Exception as exc:
                    errores.append(f'Fila {n}: {exc}')
        resultado = {'ok': ok, 'errores': errores}
        if ok:
            messages.success(request, f'{ok} movimientos importados en {cuenta}.')
    return render(request, 'finanzas/importar.html', {'form': form, 'resultado': resultado})


# ---------------------------------------------------------------- flujo de caja
@login_required
def flujo_caja(request):
    desde, hasta = rango_por_defecto(request, Movimiento.objects.exclude(concepto='TRANSFERENCIA'))
    qs = Movimiento.objects.filter(fecha__range=[desde, hasta]).exclude(concepto='TRANSFERENCIA')
    nombres = dict(Movimiento.CONCEPTOS)
    filas = {}
    for r in qs.values('concepto', 'tipo', 'cuenta__moneda').annotate(t=Sum('monto')):
        clave = (nombres.get(r['concepto'], r['concepto']), r['cuenta__moneda'])
        fila = filas.setdefault(clave, {'concepto': clave[0], 'moneda': clave[1], 'ingreso': D0, 'egreso': D0})
        fila['ingreso' if r['tipo'] == 'INGRESO' else 'egreso'] += r['t']
    filas = sorted(filas.values(), key=lambda f: (f['moneda'], f['concepto']))
    for f in filas:
        f['neto'] = f['ingreso'] - f['egreso']
    resumen = {}
    for f in filas:
        r = resumen.setdefault(f['moneda'], {'ingreso': D0, 'egreso': D0})
        r['ingreso'] += f['ingreso']
        r['egreso'] += f['egreso']
    for r in resumen.values():
        r['neto'] = r['ingreso'] - r['egreso']
    return render(request, 'finanzas/flujo.html', {
        'filas': filas, 'resumen': resumen, 'desde': desde, 'hasta': hasta,
        'cuentas': Cuenta.objects.filter(activo=True)})
