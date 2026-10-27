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
from django.utils import timezone


# ---------------------------------------------------------------- cuentas
@login_required
def cuentas(request):
    from core.politicas import filtrar_cuentas, ver_saldos
    lista = list(filtrar_cuentas(Cuenta.objects.all(), request.user, 'pk'))
    con_saldo = ver_saldos(request.user)
    for c in lista:  # diagnóstico: días en que el saldo quedó en negativo (datos anteriores a la validación)
        c.negativo = None if c.admite_negativo or not con_saldo else c.primer_negativo()
    return render(request, 'finanzas/cuentas.html', {'cuentas': lista,
                                                     'con_negativo': [c for c in lista if c.negativo]})


class CuentaNueva(FormGenerico, CreateView):
    model, form_class, titulo = Cuenta, CuentaForm, 'Nueva cuenta de caja / banco'
    success_url = reverse_lazy('finanzas:cuentas')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.usuario = self.request.user
        return form


@login_required
def saldo_inicial(request, pk):
    """Historial del saldo inicial y regularización (solo administradores, con motivo y sustento)."""
    from .forms import RegularizarSaldoForm
    from .models import SaldoInicialCambio
    cuenta = get_object_or_404(Cuenta, pk=pk)
    form = None
    if request.user.is_superuser:
        form = RegularizarSaldoForm(request.POST or None, request.FILES or None, cuenta=cuenta)
        if request.method == 'POST' and form.is_valid():
            datos, nombre, tipo = form.sustento_leido
            with transaction.atomic():
                SaldoInicialCambio.objects.create(
                    cuenta=cuenta, anterior=cuenta.saldo_inicial, nuevo=form.cleaned_data['nuevo'],
                    motivo=form.cleaned_data['motivo'], sustento=datos, sustento_nombre=nombre, sustento_tipo=tipo,
                    usuario=request.user)
                cuenta.saldo_inicial = form.cleaned_data['nuevo']
                cuenta.save()  # regenera el asiento de apertura
            messages.success(request, f'Saldo inicial de {cuenta} regularizado a {cuenta.simbolo} '
                                      f'{cuenta.saldo_inicial:,.2f}. El asiento de apertura se actualiza solo.')
            return redirect('finanzas:saldo_inicial', pk)
    elif request.method == 'POST':
        messages.error(request, 'Solo un administrador puede regularizar el saldo inicial.')
    return render(request, 'finanzas/saldo_inicial.html', {
        'cuenta': cuenta, 'form': form, 'cambios': cuenta.cambios_saldo_inicial.select_related('usuario')})


@login_required
def saldo_inicial_sustento(request, pk):
    from django.http import Http404, HttpResponse
    from .models import SaldoInicialCambio
    cambio = get_object_or_404(SaldoInicialCambio, pk=pk)
    if not cambio.sustento:
        raise Http404
    resp = HttpResponse(bytes(cambio.sustento), content_type=cambio.sustento_tipo or 'application/octet-stream')
    resp['Content-Disposition'] = f'inline; filename="{cambio.sustento_nombre or "sustento"}"'
    return resp


class CuentaEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Cuenta, CuentaForm, 'Editar cuenta'
    success_url = reverse_lazy('finanzas:cuentas')


# ---------------------------------------------------------------- movimientos
def _filtrar_movs(request):
    f = request.GET
    base = Movimiento.todos if f.get('estado') in ('ANULADO', 'TODOS') else Movimiento.objects
    from core.politicas import filtrar_cuentas
    qs = filtrar_cuentas(base.select_related('cuenta', 'tercero', 'venta', 'compra'), request.user)
    if f.get('estado') == 'ANULADO':
        qs = qs.filter(estado='ANULADO')
    cuentas = [c for c in f.getlist('cuenta') if c.isdigit()]
    if cuentas:
        qs = qs.filter(cuenta_id__in=cuentas)
    if f.get('desde'):
        qs = qs.filter(fecha__gte=f['desde'])
    if f.get('hasta'):
        qs = qs.filter(fecha__lte=f['hasta'])
    for campo in ('tipo', 'concepto'):  # PLAT-13: varios valores
        valores = [v for v in f.getlist(campo) if v]
        if valores:
            qs = qs.filter(**{f'{campo}__in': valores})
    if f.get('q'):
        q = f['q']
        qs = qs.filter(Q(glosa__icontains=q) | Q(voucher__icontains=q) | Q(numero_operacion__icontains=q) |
                       Q(tercero__nombre__icontains=q))
    if f.get('manuales'):  # FIN-13: egresos sin comprobante, letra ni transferencia (para revisarlos)
        qs = qs.filter(tipo='EGRESO', compra__isnull=True, venta__isnull=True, letra__isnull=True).exclude(
            concepto__in=('TRANSFERENCIA', 'PAGO', 'COBRANZA', 'DETRACCION'))
    return qs


@login_required
def movimientos(request):
    from core.politicas import filtrar_cuentas
    qs = _filtrar_movs(request)
    if request.GET.get('formato') == 'excel':
        filas = [[m.voucher, fmt_fecha(m.fecha), str(m.cuenta), m.get_tipo_display(), m.get_concepto_display(),
                  m.get_medio_pago_display(), m.numero_operacion, m.tercero.nombre if m.tercero else '',
                  str(m.documento or ''), m.monto if m.tipo == 'INGRESO' else D0,
                  m.monto if m.tipo == 'EGRESO' else D0, m.glosa, 'Sí' if m.conciliado else 'No'] for m in qs]
        enc = ['Voucher', 'Fecha', 'Cuenta', 'Tipo', 'Concepto', 'Medio', 'N° op.', 'Tercero', 'Documento',
               'Ingreso', 'Egreso', 'Glosa', 'Conciliado']
        return excel_response('Movimientos_caja_bancos', 'Movimientos de caja y bancos', enc, filas)
    agg = qs.filter(estado='VIGENTE').aggregate(i=Sum('monto', filter=Q(tipo='INGRESO')),
                                                e=Sum('monto', filter=Q(tipo='EGRESO')))
    return render(request, 'finanzas/movimientos.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')),
        'cuentas': filtrar_cuentas(Cuenta.objects.all(), request.user, 'pk'), 'conceptos': Movimiento.CONCEPTOS, 'ingresos': agg['i'] or D0, 'egresos': agg['e'] or D0})


@login_required
def movimiento_nuevo(request):
    from core.sustentos import adjuntar
    initial = {'fecha': timezone.localdate(), 'tipo': request.GET.get('tipo', 'EGRESO'), 'cuenta': request.GET.get('cuenta')}
    form = MovimientoForm(request.POST or None, request.FILES or None, initial=initial, usuario=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            mov = form.save(commit=False)
            mov.creado_por = request.user
            mov.save()
            adjuntar(mov, form.cleaned_data['sustento'], request.user, mov.glosa[:200])
            if mov.medio_pago == 'CHEQUE' and mov.numero_operacion:
                from .operaciones import registrar_cheque
                registrar_cheque([mov], mov.numero_operacion, request.user)
        messages.success(request, f'Movimiento {mov.voucher} registrado con su sustento.')
        return redirect('finanzas:movimientos')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Ingreso / egreso de caja y bancos'})


def _error_al_eliminar(mov):
    """Quitar un ingreso (o una transferencia, que es ingreso en la cuenta destino) no puede dejar saldo negativo."""
    par = mov.transferencia_par or Movimiento.objects.filter(transferencia_par=mov).first()
    for m in [mov] + ([par] if par else []):
        if m.tipo == 'INGRESO':
            error = m.cuenta.error_sobregiro(D0, fecha=m.fecha, excluir_ids=[m.pk])
            if error:
                return error
    return ''


@login_required
def movimiento_eliminar(request, pk):
    """Los movimientos no se borran: se anulan con motivo (quedan visibles y fuera de saldos y contabilidad)."""
    from django.utils import timezone
    from .forms import AnularMovimientoForm
    mov = get_object_or_404(Movimiento, pk=pk)
    if request.method == 'POST':
        form = AnularMovimientoForm(request.POST)
        par = mov.transferencia_par or Movimiento.objects.filter(transferencia_par=mov).first()
        conciliado = mov.conciliado or (par is not None and par.conciliado)
        if not form.is_valid():
            messages.error(request, ' '.join(form.errors['motivo']))
        elif conciliado:
            messages.error(request, 'No se puede anular un movimiento conciliado: quite primero la conciliación.')
        elif periodo_cerrado(mov.fecha.strftime('%Y%m')):
            messages.error(request, 'No se puede anular: el periodo contable está cerrado.')
        elif _error_al_eliminar(mov):
            messages.error(request, f'No se puede anular: {_error_al_eliminar(mov)}')
        elif mov.aplicaciones_anticipo.exists():
            messages.error(request, 'No se puede anular: el anticipo ya se aplicó a comprobantes. Anule primero esas '
                                    'aplicaciones (Finanzas > Anticipos).')
        else:
            with transaction.atomic():
                for m in [mov] + ([par] if par else []):
                    m.estado, m.motivo_anulacion = 'ANULADO', form.cleaned_data['motivo']
                    m.anulado_por, m.anulado_en = request.user, timezone.now()
                    m.save()
                if mov.letra_id:
                    from .operaciones import actualizar_letra
                    actualizar_letra(mov.letra)
            messages.success(request, f'Movimiento {mov.voucher} anulado{" junto con su transferencia par" if par else ""}.')
    from django.utils.http import url_has_allowed_host_and_scheme
    siguiente = request.POST.get('next') or ''
    if not (siguiente.startswith('/') and url_has_allowed_host_and_scheme(siguiente, {request.get_host()})):
        siguiente = 'finanzas:movimientos'
    return redirect(siguiente)


@login_required
def voucher(request, pk):
    mov = get_object_or_404(Movimiento.todos.select_related('cuenta', 'tercero'), pk=pk)
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

    from .models import Cheque, Letra
    from .operaciones import ErrorTesoreria, actualizar_letra, anticipos_disponibles, registrar_cheque
    pendientes, letras, anticipos = [], [], []
    if tercero:
        qs = (cfg['modelo'].objects.con_saldos().cobrables().filter(tercero=tercero, estado='REGISTRADO')
              .exclude(tipo_comprobante='07').order_by('fecha_vencimiento'))
        pendientes = [d for d in qs if d.saldo > 0]
        for d in pendientes:  # lo sugerido no incluye la detracción por depositar (va a la cuenta del BN)
            d.detr_pendiente = d.detraccion_por_depositar
            d.sugerido = r2(d.saldo - d.detr_pendiente)
        letras = [l for l in Letra.objects.filter(tercero=tercero, tipo='COBRAR' if modo == 'cobranza' else 'PAGAR',
                                                   estado__in=Letra.ABIERTAS) if l.saldo > 0]
        anticipos = anticipos_disponibles(tercero, cfg['tipo'])
    # cheque recibido que estaba en cartera (diferido) y ahora se deposita
    cheque = Cheque.objects.filter(pk=request.GET.get('cheque'), tipo='RECIBIDO', estado='CARTERA').first() \
        if modo == 'cobranza' and request.GET.get('cheque') else None

    inicial = {'fecha': timezone.localdate(), 'es_detraccion': request.GET.get('detraccion') == '1'}
    if cheque:
        inicial.update(medio_pago='CHEQUE', numero_operacion=cheque.numero, cuenta=cheque.cuenta_id,
                       fecha=max(timezone.localdate(), cheque.fecha_pago or timezone.localdate()))
    form = OperacionForm(request.POST or None, request.FILES or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        creados = []
        errores = []
        with transaction.atomic():
            for l in letras:
                valor = request.POST.get(f'letra_{l.pk}', '').strip()
                if not valor:
                    continue
                try:
                    monto = Decimal(valor)
                except InvalidOperation:
                    errores.append(f'{l}: monto inválido')
                    continue
                if monto <= 0:
                    continue
                if monto > l.saldo:
                    errores.append(f'{l}: el monto supera el saldo ({l.saldo})')
                    continue
                creados.append(Movimiento.objects.create(
                    cuenta=data['cuenta'], fecha=data['fecha'], tipo=cfg['tipo'], concepto=cfg['concepto'],
                    medio_pago=data['medio_pago'], numero_operacion=data['numero_operacion'], tercero=tercero,
                    monto=_monto_en_cuenta(monto, l, data['cuenta'], data['fecha']), monto_doc=monto, letra=l,
                    glosa=data['glosa'] or f'{cfg["concepto"].title()} {l}', creado_por=request.user))
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
                if getattr(d, 'pago_bloqueado', False):
                    errores.append(f'{d}: pago bloqueado por Compras ({d.motivo_bloqueo_pago}). Se paga cuando '
                                   f'Compras lo libere.')
                    continue
                if d.fecha_emision and data['fecha'] < d.fecha_emision:  # FIN-12
                    errores.append(f'{d}: la fecha del {"cobro" if modo == "cobranza" else "pago"} '
                                   f'({data["fecha"]:%d/%m/%Y}) es anterior a su emisión ({d.fecha_emision:%d/%m/%Y}).')
                    continue
                creados.append(Movimiento.objects.create(
                    cuenta=data['cuenta'], fecha=data['fecha'], tipo=cfg['tipo'],
                    concepto='DETRACCION' if data['es_detraccion'] else cfg['concepto'],
                    medio_pago=data['medio_pago'], numero_operacion=data['numero_operacion'], tercero=tercero,
                    monto=_monto_en_cuenta(monto, d, data['cuenta'], data['fecha'], data['es_detraccion']),
                    monto_doc=monto, glosa=data['glosa'] or f'{cfg["concepto"].title()} {d}', creado_por=request.user,
                    **{cfg['fk']: d}))
            if cfg['tipo'] == 'EGRESO' and creados and not errores:
                # con los pagos ya creados, la cuenta no debe quedar negativa en ningún día desde su fecha
                cuenta = data['cuenta']
                negativo = None if cuenta.admite_negativo else cuenta.primer_negativo(desde=data['fecha'])
                if negativo:
                    total = sum(m.monto for m in creados)
                    errores.append(f'Saldo insuficiente en {cuenta}: con los pagos ({cuenta.simbolo} {total:,.2f}) '
                                   f'la cuenta quedaría en {cuenta.simbolo} {negativo[1]:,.2f} el '
                                   f'{negativo[0]:%d/%m/%Y}.')
            if creados and not errores and (data['medio_pago'] == 'CHEQUE' or cheque):
                try:
                    registrar_cheque(creados, data['numero_operacion'], request.user, cheque=cheque)
                except ErrorTesoreria as exc:
                    errores.append(str(exc))
            if errores:
                transaction.set_rollback(True)
            else:
                for l in {m.letra for m in creados if m.letra_id}:
                    actualizar_letra(l)
                if creados and data.get('sustento'):
                    from core.sustentos import guardar_archivo, vincular
                    archivo = guardar_archivo(data['sustento'], request.user)  # un archivo para todos los movimientos
                    for m in creados:
                        vincular(m, archivo, request.user, data['glosa'] or '')
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

    return render(request, 'finanzas/cobrar_pagar.html', {
        'cfg': cfg, 'modo': modo, 'form': form, 'tercero': tercero, 'pendientes': pendientes, 'letras': letras,
        'anticipos': anticipos, 'doc_ini': doc_ini, 'cheque': cheque,
        'fuente': 'clientes' if modo == 'cobranza' else 'proveedores',
        'detraccion': request.GET.get('detraccion') == '1'})


@login_required
def cobrar_pagar_multiple(request, modo):
    """Cobranza / pago de comprobantes de varios clientes o proveedores; cada uno como saldo o como detracción."""
    from . import multiple
    from .forms import OperacionMultipleForm
    from .operaciones import ErrorTesoreria
    cfg = _cobranza_cfg(modo)
    f = request.GET
    tercero = Tercero.objects.filter(pk=f.get('tercero')).first() if f.get('tercero') else None
    hasta = f.get('hasta') or ''
    docs = multiple.pendientes(cfg['modelo'], tercero, a_fecha(hasta) if hasta else None, f.get('moneda', ''),
                               bool(f.get('solo_detraccion')), f.get('q', '').strip())
    inicial = {'fecha': timezone.localdate()}
    if modo == 'cobranza':
        inicial['cuenta_detraccion'] = Cuenta.objects.filter(activo=True, es_detracciones=True).first()
    form = OperacionMultipleForm(request.POST or None, request.FILES or None, initial=inicial, modo=modo)
    if request.method == 'POST' and form.is_valid():
        lineas = []
        for d in docs:
            valor = request.POST.get(f'monto_{d.pk}', '').strip().replace(',', '')
            if not valor:
                continue
            try:
                monto = Decimal(valor)
            except InvalidOperation:
                messages.error(request, f'{d}: monto inválido.')
                break
            lineas.append((d, bool(request.POST.get(f'det_{d.pk}')), monto, request.POST.get(f'constancia_{d.pk}', '')))
        else:
            datos = dict(form.cleaned_data)
            if modo == 'pago' and not datos.get('cuenta_detraccion'):
                datos['cuenta_detraccion'] = datos['cuenta'] if datos['cuenta'].moneda == 'PEN' else None
            try:
                creados = multiple.registrar(modo, datos, lineas, request.user)
            except ErrorTesoreria as exc:
                for e in str(exc).split(' | '):
                    messages.error(request, e)
            else:
                saldos = sum(1 for m in creados if m.concepto != 'DETRACCION')
                messages.success(request, f'{len(creados)} movimiento(s) registrados: {saldos} de saldo y '
                                          f'{len(creados) - saldos} de detracción.')
                return redirect('finanzas:movimientos')
    return render(request, 'finanzas/cobrar_pagar_multiple.html', {
        'cfg': cfg, 'modo': modo, 'form': form, 'docs': docs, 'tercero': tercero, 'hasta': hasta, 'f': f,
        'fuente': 'clientes' if modo == 'cobranza' else 'proveedores'})


# ---------------------------------------------------------------- transferencias
@login_required
def transferencia(request):
    form = TransferenciaForm(request.POST or None, request.FILES or None, initial={'fecha': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        glosa = d['glosa'] or f'Transferencia {d["origen"].nombre} → {d["destino"].nombre}'
        with transaction.atomic():
            salida = Movimiento.objects.create(
                cuenta=d['origen'], fecha=d['fecha'], tipo='EGRESO', concepto='TRANSFERENCIA',
                medio_pago='TRANSFERENCIA', numero_operacion=d['numero_operacion'], monto=d['monto'], glosa=glosa,
                creado_por=request.user)
            entrada = Movimiento.objects.create(
                cuenta=d['destino'], fecha=d['fecha'], tipo='INGRESO', concepto='TRANSFERENCIA',
                medio_pago='TRANSFERENCIA', numero_operacion=d['numero_operacion'], monto=d['monto'], glosa=glosa,
                transferencia_par=salida, creado_por=request.user)
            if d.get('sustento'):
                from core.sustentos import guardar_archivo, vincular
                archivo = guardar_archivo(d['sustento'], request.user)
                vincular(salida, archivo, request.user, glosa)
                vincular(entrada, archivo, request.user, glosa)
        messages.success(request, f'Transferencia registrada ({salida.voucher} / {entrada.voucher}).')
        return redirect('finanzas:movimientos')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Transferencia entre cuentas / depósito de caja'})


# ---------------------------------------------------------------- conciliación bancaria
@login_required
def conciliacion(request):
    cuentas_qs = Cuenta.objects.filter(activo=True)
    cuenta = cuentas_qs.filter(pk=request.GET.get('cuenta') or request.POST.get('cuenta')).first() or cuentas_qs.filter(tipo='BANCO').first()
    hasta = request.GET.get('hasta') or request.POST.get('hasta') or timezone.localdate().isoformat()
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
        if cuenta.cuenta_contable_id:  # CON-21: asientos manuales que movieron esta cuenta sin pasar por Tesorería
            from contabilidad.models import AsientoLinea
            manuales = list(AsientoLinea.objects.filter(
                cuenta_id=cuenta.cuenta_contable_id, asiento__origen='MANUAL', asiento__fecha__lte=fecha,
                asiento__fecha__gte=fecha.replace(day=1)).select_related('asiento')[:20])
            for l in manuales:
                messages.warning(request, f'Asiento manual {l.asiento.numero} del {l.asiento.fecha:%d/%m/%Y} a la '
                                          f'{cuenta.cuenta_contable.codigo} (debe {l.debe:,.2f} / haber {l.haber:,.2f}): '
                                          f'no es un movimiento de Tesorería; revíselo al conciliar. '
                                          f'{l.asiento.glosa[:80]}')
    return render(request, 'finanzas/conciliacion.html', ctx)


# ---------------------------------------------------------------- importar extracto bancario
@login_required
def importar_extracto(request):
    if request.GET.get('plantilla'):
        return excel_response('Plantilla_extracto', 'plantilla', ['fecha', 'descripcion', 'monto', 'operacion'],
                              [[timezone.localdate().strftime('%d/%m/%Y'), 'ABONO CLIENTE X', 1500, '000123'],
                               [timezone.localdate().strftime('%d/%m/%Y'), 'COMISION MANTENIMIENTO', -12.5, '']])
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
            fechas, creados = [], []
            for n, f in enumerate(filas, 2):
                try:
                    monto = Decimal(str(f.get('monto')).replace(',', ''))
                    if monto == 0:
                        continue
                    desc = str(f.get('descripcion') or '')
                    concepto = 'GASTO_BANCARIO' if any(p in desc.upper() for p in ('ITF', 'COMISION', 'MANTENIMIENTO', 'PORTES')) else 'OTRO'
                    fecha = a_fecha(f.get('fecha'))
                    creados.append(Movimiento.objects.create(
                        cuenta=cuenta, fecha=fecha, tipo='INGRESO' if monto > 0 else 'EGRESO',
                        concepto=concepto, medio_pago='TRANSFERENCIA', numero_operacion=str(f.get('operacion') or ''),
                        monto=abs(monto), glosa=desc[:250], conciliado=form.cleaned_data['conciliado'],
                        fecha_conciliacion=fecha if form.cleaned_data['conciliado'] else None,
                        creado_por=request.user))
                    fechas.append(fecha)
                    ok += 1
                except Exception as exc:
                    errores.append(f'Fila {n}: {exc}')
            negativo = cuenta.primer_negativo(desde=min(fechas)) if fechas and not cuenta.admite_negativo else None
            if negativo:
                errores.append(f'Con estos movimientos {cuenta} quedaría en {cuenta.simbolo} {negativo[1]:,.2f} el '
                               f'{negativo[0]:%d/%m/%Y}: revise el extracto o registre antes los ingresos que faltan. '
                               f'No se importó nada.')
                transaction.set_rollback(True)
                ok = 0
            elif creados:
                # el extracto del banco es el sustento de cada movimiento importado
                from core.sustentos import guardar_archivo, vincular
                archivo = guardar_archivo(form.cleaned_data['archivo'], request.user)
                for m in creados:
                    vincular(m, archivo, request.user, 'Extracto bancario importado')
        resultado = {'ok': ok, 'errores': errores}
        if ok:
            messages.success(request, f'{ok} movimientos importados en {cuenta}.')
    return render(request, 'finanzas/importar.html', {'form': form, 'resultado': resultado})


# ---------------------------------------------------------------- flujo de caja
@login_required
def flujo_proyectado(request):
    """Flujo de caja proyectado por semana (12 semanas por defecto)."""
    from core.politicas import cuentas_permitidas
    from core.utils import excel_response

    from .proyeccion import proyectar
    try:
        semanas = min(max(int(request.GET.get('semanas', 12)), 4), 26)
    except ValueError:
        semanas = 12
    moneda = request.GET.get('moneda') if request.GET.get('moneda') in ('PEN', 'USD') else ''
    p = proyectar(semanas, cuentas=cuentas_permitidas(request.user), moneda=moneda or None)
    if request.GET.get('formato') == 'excel':
        encabezado = ['Concepto', 'Tipo'] + [f'{d:%d/%m}-{h:%d/%m}' for d, h in p['semanas']] + ['Total']
        filas = [['Saldo inicial de caja y bancos', ''] + p['saldos_iniciales'] + [p['saldo_inicial']]]
        filas += [[f['concepto'], f['tipo'].capitalize()] + f['valores'] + [f['total']] for f in p['filas']]
        filas += [['Total ingresos', ''] + p['ingresos'] + [sum(p['ingresos'], D0)],
                  ['Total egresos', ''] + p['egresos'] + [sum(p['egresos'], D0)],
                  ['Saldo proyectado al final de la semana', ''] + p['saldos'] + ['']]
        return excel_response('Flujo_proyectado', 'FLUJO DE CAJA PROYECTADO (S/)', encabezado, filas)
    from ventas.crm_reportes import pronostico_semanal  # CRM-16: lo que viene del embudo (referencial)
    crm = pronostico_semanal(p['semanas'])
    return render(request, 'finanzas/flujo_proyectado.html', {'p': p, 'semanas_n': semanas, 'moneda': moneda,
                                                              'opciones_semanas': [4, 8, 12, 16, 26],
                                                              'crm_pronostico': crm if any(crm) else None})


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
