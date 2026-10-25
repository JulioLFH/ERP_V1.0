"""Pantallas de anticipos, letras, cheques, entregas a rendir / caja chica y pagos masivos."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from compras.models import Compra
from core.models import D0, Tercero, r2
from core.utils import a_fecha, excel_response, fmt_fecha
from ventas.models import Venta

from . import operaciones as op
from .forms import ChequeForm, EntregaForm, GastoRendicionForm
from .models import (Aplicacion, CanjeLetras, Cheque, Cuenta, EntregaRendir, Letra, Movimiento, PagoMasivo)
from django.utils import timezone
from core.utils import id_o_none


def _monto(valor):
    valor = (valor or '').strip().replace(',', '')
    if not valor:
        return None
    try:
        monto = Decimal(valor)
    except InvalidOperation:
        raise op.ErrorTesoreria(f'Monto inválido: {valor}')
    return monto if monto > 0 else None


def _fecha(valor, defecto=None):
    try:
        return a_fecha(valor) if valor else (defecto or timezone.localdate())
    except Exception:
        raise op.ErrorTesoreria(f'Fecha inválida: {valor}')


def _pendientes(modelo, **filtro):
    qs = (modelo.objects.con_saldos().cobrables().filter(estado='REGISTRADO', **filtro)
          .exclude(tipo_comprobante='07').select_related('tercero').order_by('fecha_vencimiento', 'id'))
    return [d for d in qs if d.saldo > 0]


def _montos_documentos(request, docs, prefijo='monto_'):
    """[(doc, monto)] con lo ingresado en cada fila."""
    salida = []
    for d in docs:
        monto = _monto(request.POST.get(f'{prefijo}{d.pk}'))
        if monto:
            salida.append((d, monto))
    return salida


# ---------------------------------------------------------------- anticipos
@login_required
def anticipos(request):
    tipo = request.GET.get('tipo', '')
    qs = Movimiento.objects.filter(concepto='ANTICIPO').select_related('cuenta', 'tercero')
    if tipo:
        qs = qs.filter(tipo=tipo)
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(tercero__nombre__icontains=q) | Q(tercero__numero_doc=q) | Q(voucher__icontains=q))
    lista = list(qs.order_by('-fecha', '-id')[:500])
    if request.GET.get('ver') != 'todos':
        lista = [m for m in lista if m.anticipo_disponible > 0]
    aplicaciones = (Aplicacion.todos.filter(origen='ANTICIPO').select_related('venta', 'compra', 'anticipo__tercero')
                    .order_by('-fecha', '-id')[:30])
    return render(request, 'finanzas/anticipos.html', {'anticipos': lista, 'aplicaciones': aplicaciones, 'tipo': tipo})


@login_required
def anticipo_aplicar(request, pk):
    anticipo = get_object_or_404(Movimiento.objects.select_related('cuenta', 'tercero'), pk=pk, concepto='ANTICIPO')
    es_cliente = anticipo.tipo == 'INGRESO'
    docs = _pendientes(Venta if es_cliente else Compra, tercero_id=anticipo.tercero_id,
                       moneda=anticipo.cuenta.moneda) if anticipo.tercero_id else []
    if request.method == 'POST':
        try:
            creadas = op.aplicar_anticipo(anticipo, _montos_documentos(request, docs),
                                          _fecha(request.POST.get('fecha')), request.user)
            if not creadas:
                raise op.ErrorTesoreria('Ingrese el monto a aplicar en al menos un comprobante.')
            messages.success(request, f'Anticipo {anticipo.voucher} aplicado a {len(creadas)} comprobante(s).')
            return redirect('finanzas:anticipos')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
    return render(request, 'finanzas/anticipo_aplicar.html', {
        'anticipo': anticipo, 'docs': docs, 'es_cliente': es_cliente, 'hoy': timezone.localdate()})


@login_required
def aplicacion_anular(request, pk):
    ap = get_object_or_404(Aplicacion, pk=pk)
    if request.method == 'POST':
        try:
            op.anular_aplicacion(ap, request.POST.get('motivo'), request.user)
            messages.success(request, 'Aplicación anulada: el comprobante vuelve a tener saldo.')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
    return redirect(request.POST.get('next') or 'finanzas:anticipos')


# ---------------------------------------------------------------- letras
def _filtrar_letras(request):
    f = request.GET
    qs = Letra.objects.select_related('tercero', 'banco', 'canje')
    if f.get('tipo'):
        qs = qs.filter(tipo=f['tipo'])
    estado = f.get('estado', 'ABIERTAS')
    if estado == 'ABIERTAS':
        qs = qs.filter(estado__in=Letra.ABIERTAS)
    elif estado:
        qs = qs.filter(estado=estado)
    if f.get('vencidas'):
        qs = qs.filter(fecha_vencimiento__lt=timezone.localdate())
    if f.get('q'):
        qs = qs.filter(Q(numero__icontains=f['q']) | Q(tercero__nombre__icontains=f['q']) |
                       Q(tercero__numero_doc=f['q']) | Q(codigo_banco__icontains=f['q']))
    return qs, estado


@login_required
def letras(request):
    qs, estado = _filtrar_letras(request)
    if request.GET.get('formato') == 'excel':
        filas = [[l.numero, l.get_tipo_display(), l.tercero.numero_doc, l.tercero.nombre, fmt_fecha(l.fecha_giro),
                  fmt_fecha(l.fecha_vencimiento), l.moneda, l.monto, l.pagado, l.saldo, l.get_estado_display(),
                  l.banco.nombre if l.banco else '', l.codigo_banco, l.canje.numero if l.canje else ''] for l in qs]
        return excel_response('Letras', 'Letras por cobrar y por pagar',
                              ['Letra', 'Tipo', 'RUC/DNI', 'Aceptante / girador', 'Giro', 'Vencimiento', 'Moneda',
                               'Monto', 'Cobrado/pagado', 'Saldo', 'Estado', 'Banco', 'N° único', 'Canje'], filas)
    resumen = {}
    for l in qs:
        r = resumen.setdefault((l.get_tipo_display(), l.moneda), D0)
        resumen[(l.get_tipo_display(), l.moneda)] = r + l.saldo
    return render(request, 'finanzas/letras.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'estado': estado,
        'estados': Letra.ESTADOS, 'resumen': sorted(resumen.items()), 'hoy': timezone.localdate()})


@login_required
def canje_nuevo(request):
    tipo = request.GET.get('tipo') or request.POST.get('tipo') or 'COBRAR'
    moneda = request.GET.get('moneda') or 'PEN'
    tercero = Tercero.objects.filter(pk=id_o_none(request.GET.get('tercero'))).first() if request.GET.get('tercero') else None
    docs = _pendientes(Venta if tipo == 'COBRAR' else Compra, tercero=tercero, moneda=moneda) if tercero else []
    if request.method == 'POST' and tercero:
        try:
            fecha = _fecha(request.POST.get('fecha'))
            letras_ = []
            for venc, monto, numero in zip(request.POST.getlist('l_vencimiento'), request.POST.getlist('l_monto'),
                                           request.POST.getlist('l_numero')):
                m = _monto(monto)
                if m:
                    letras_.append((_fecha(venc), m, numero.strip()[:20]))
            from core.tipo_cambio import venta_del_dia
            tc = _monto(request.POST.get('tipo_cambio')) or (venta_del_dia(fecha) if moneda == 'USD' else Decimal('1'))
            canje = op.canjear(tipo, tercero, fecha, moneda, tc, _montos_documentos(request, docs), letras_,
                               request.user, request.POST.get('glosa', '')[:250])
            messages.success(request, f'Canje {canje.numero} registrado: {canje.letras.count()} letra(s) por '
                                      f'{canje.total:,.2f}.')
            return redirect('finanzas:canje', canje.pk)
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
    return render(request, 'finanzas/canje_nuevo.html', {
        'tipo': tipo, 'moneda': moneda, 'tercero': tercero, 'docs': docs, 'hoy': timezone.localdate(),
        'fuente': 'clientes' if tipo == 'COBRAR' else 'proveedores'})


@login_required
def canje(request, pk):
    c = get_object_or_404(CanjeLetras.objects.select_related('tercero'), pk=pk)
    if request.method == 'POST':
        try:
            op.anular_canje(c, request.POST.get('motivo'), request.user)
            messages.success(request, f'Canje {c.numero} anulado: los comprobantes vuelven a tener saldo.')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
        return redirect('finanzas:canje', pk)
    return render(request, 'finanzas/canje.html', {
        'c': c, 'letras': c.letras.all(), 'aplicaciones': Aplicacion.todos.filter(canje=c).select_related('venta', 'compra')})


@login_required
def letra(request, pk):
    l = get_object_or_404(Letra.objects.select_related('tercero', 'banco', 'canje', 'renovada_de'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            fecha = _fecha(request.POST.get('fecha'))
            if accion == 'estado':
                banco = Cuenta.objects.filter(pk=id_o_none(request.POST.get('banco'))).first() if request.POST.get('banco') else None
                op.cambiar_estado_letra(l, request.POST.get('estado'), fecha, banco, request.POST.get('codigo', ''))
                messages.success(request, f'{l}: {l.get_estado_display().lower()}.')
            elif accion == 'renovar':
                nuevas = []
                for venc, monto in zip(request.POST.getlist('l_vencimiento'), request.POST.getlist('l_monto')):
                    m = _monto(monto)
                    if m:
                        nuevas.append((_fecha(venc), m))
                creadas = op.renovar(l, nuevas, fecha, request.user)
                messages.success(request, f'{l} renovada por {", ".join(c.numero for c in creadas)}.')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
        return redirect('finanzas:letra', pk)
    return render(request, 'finanzas/letra.html', {
        'l': l, 'movimientos': l.movimientos.select_related('cuenta'), 'renovaciones': l.renovaciones.all(),
        'bancos': Cuenta.objects.filter(activo=True, tipo='BANCO'), 'hoy': timezone.localdate(),
        'estados_manuales': [(e, n) for e, n in Letra.ESTADOS if e in op.ESTADOS_MANUALES]})


# ---------------------------------------------------------------- cheques
@login_required
def cheques(request):
    f = request.GET
    qs = Cheque.objects.select_related('tercero', 'cuenta')
    if f.get('tipo'):
        qs = qs.filter(tipo=f['tipo'])
    estado = f.get('estado', 'CARTERA')
    if estado:
        qs = qs.filter(estado=estado)
    if f.get('q'):
        qs = qs.filter(Q(numero__icontains=f['q']) | Q(tercero__nombre__icontains=f['q']))
    if f.get('formato') == 'excel':
        filas = [[c.get_tipo_display(), c.numero, c.banco_emisor or (c.cuenta.nombre if c.cuenta else ''),
                  c.tercero.nombre if c.tercero else '', fmt_fecha(c.fecha_emision), fmt_fecha(c.fecha_pago),
                  c.moneda, c.monto, c.get_estado_display(), fmt_fecha(c.fecha_estado)] for c in qs]
        return excel_response('Cheques', 'Cheques emitidos y recibidos',
                              ['Tipo', 'N°', 'Banco', 'Beneficiario / girador', 'Emisión', 'Pago diferido', 'Moneda',
                               'Monto', 'Estado', 'Fecha estado'], filas)
    form = ChequeForm(request.POST or None, initial={'fecha_emision': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        ch = form.save(commit=False)
        ch.tipo, ch.creado_por = 'RECIBIDO', request.user
        ch.save()
        messages.success(request, f'Cheque {ch.numero} registrado en cartera. Al depositarlo use "Depositar".')
        return redirect('finanzas:cheques')
    totales = qs.values('tipo', 'moneda').annotate(t=Sum('monto')).order_by('tipo', 'moneda')
    return render(request, 'finanzas/cheques.html', {
        'page_obj': Paginator(qs, 50).get_page(f.get('page')), 'form': form, 'estado': estado,
        'estados': Cheque.ESTADOS, 'totales': totales, 'hoy': timezone.localdate()})


@login_required
def cheque_estado(request, pk):
    ch = get_object_or_404(Cheque, pk=pk)
    if request.method == 'POST':
        try:
            op.cambiar_estado_cheque(ch, request.POST.get('estado'), _fecha(request.POST.get('fecha')),
                                     request.POST.get('motivo', ''), request.user)
            messages.success(request, f'Cheque {ch.numero}: {ch.get_estado_display().lower()}.')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
    return redirect(request.POST.get('next') or 'finanzas:cheques')


# ---------------------------------------------------------------- entregas a rendir y caja chica
@login_required
def entregas(request):
    qs = EntregaRendir.objects.select_related('responsable')
    estado = request.GET.get('estado', 'ABIERTA')
    if estado:
        qs = qs.filter(estado=estado)
    if request.GET.get('tipo'):
        qs = qs.filter(tipo=request.GET['tipo'])
    if request.GET.get('q'):
        qs = qs.filter(Q(responsable__nombre__icontains=request.GET['q']) | Q(numero__icontains=request.GET['q']))
    return render(request, 'finanzas/entregas.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'estado': estado})


@login_required
def entrega_nueva(request):
    form = EntregaForm(request.POST or None, initial={'fecha': timezone.localdate(), 'tipo': request.GET.get('tipo', 'ENTREGA')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                e = op.crear_entrega(form.save(commit=False), d['cuenta'], d['monto'], d['numero_operacion'],
                                     d['medio_pago'], request.user)
            messages.success(request, f'{e.get_tipo_display()} {e.numero} registrada.')
            return redirect('finanzas:entrega', e.pk)
        except op.ErrorTesoreria as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva entrega a rendir / caja chica'})


@login_required
def entrega(request, pk):
    e = get_object_or_404(EntregaRendir.objects.select_related('responsable', 'centro_costo'), pk=pk)
    gasto_form = GastoRendicionForm(request.POST if request.POST.get('accion') == 'gasto' else None,
                                    request.FILES or None, initial={'fecha': timezone.localdate(),
                                                                    'centro_costo': e.centro_costo_id},
                                    prefix='g')
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'gasto':
                if not gasto_form.is_valid():
                    raise op.ErrorTesoreria('Revise los datos del gasto: ' + '; '.join(
                        f'{k}: {" ".join(v)}' for k, v in gasto_form.errors.items()))
                with transaction.atomic():
                    gasto = op.rendir_gasto(e, gasto_form.save(commit=False), request.user)
                    if gasto_form.cleaned_data.get('sustento'):
                        from core.sustentos import adjuntar
                        adjuntar(gasto, gasto_form.cleaned_data['sustento'], request.user, gasto.descripcion)
                messages.success(request, 'Gasto rendido.')
            elif accion == 'compra':
                compra = get_object_or_404(Compra.objects.con_saldos(), pk=request.POST.get('compra'))
                monto = _monto(request.POST.get('monto')) or compra.saldo
                op.rendir_compra(e, compra, monto, _fecha(request.POST.get('fecha')), request.user)
                messages.success(request, f'{compra} rendida y cancelada con el fondo.')
            elif accion in ('devolver', 'reembolsar', 'reponer', 'ampliar'):
                cuenta = get_object_or_404(Cuenta, pk=request.POST.get('cuenta'))
                monto = _monto(request.POST.get('monto'))
                if not monto:
                    raise op.ErrorTesoreria('Indique el monto.')
                m = op.movimiento_fondo(e, accion, cuenta, monto, _fecha(request.POST.get('fecha')), request.user,
                                        request.POST.get('numero_operacion', ''))
                messages.success(request, f'Movimiento {m.voucher} registrado.')
            elif accion == 'liquidar':
                op.liquidar(e, _fecha(request.POST.get('fecha')))
                messages.success(request, f'{e.numero} liquidada.')
            elif accion == 'eliminar_gasto':
                gasto = get_object_or_404(e.gastos, pk=request.POST.get('gasto'))
                if e.estado != 'ABIERTA':
                    raise op.ErrorTesoreria('La entrega está liquidada.')
                op._periodo_abierto(gasto.fecha)
                gasto.delete()
                messages.success(request, 'Gasto quitado de la rendición.')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
        return redirect('finanzas:entrega', pk)
    return render(request, 'finanzas/entrega.html', {
        'e': e, 'gasto_form': gasto_form, 'gastos': e.gastos.select_related('cuenta_contable', 'centro_costo'),
        'aplicaciones': e.aplicaciones.select_related('compra__tercero'),
        'movimientos': e.movimientos.select_related('cuenta'),
        'cuentas': Cuenta.objects.filter(activo=True, moneda=e.moneda), 'hoy': timezone.localdate()})


# ---------------------------------------------------------------- pagos masivos
@login_required
def pagos_masivos(request):
    qs = PagoMasivo.objects.select_related('cuenta')
    return render(request, 'finanzas/pagos_masivos.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page'))})


@login_required
def pago_masivo_nuevo(request):
    bancos = Cuenta.objects.filter(activo=True, tipo='BANCO', es_detracciones=False)
    cuenta = bancos.filter(pk=id_o_none(request.GET.get('cuenta'))).first() or bancos.first()
    hasta = request.GET.get('hasta') or timezone.localdate().isoformat()
    docs = []
    if cuenta:
        filtro = {'moneda': cuenta.moneda, 'fecha_vencimiento__lte': hasta}
        if request.GET.get('proveedor'):
            filtro['tercero_id'] = request.GET['proveedor']
        docs = _pendientes(Compra, pago_bloqueado=False, **filtro)[:400]  # las bloqueadas por Compras no se pagan
    if request.method == 'POST' and cuenta:
        try:
            pago = op.crear_pago_masivo(cuenta, _fecha(request.POST.get('fecha')),
                                        _montos_documentos(request, docs), request.user,
                                        request.POST.get('glosa', '')[:250])
            messages.success(request, f'{pago} generado por {cuenta.simbolo} {pago.total:,.2f}. Descargue el archivo '
                                      f'para el banco y, cuando el banco confirme, registre los pagos.')
            return redirect('finanzas:pago_masivo', pago.pk)
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
    for d in docs:
        d.abono = op.datos_banco(d.tercero, cuenta)
        d.detr_pendiente = d.detraccion_por_depositar  # va a la cuenta del BN del proveedor, no a su banco
        d.a_pagar = r2(d.saldo - d.detr_pendiente)
    return render(request, 'finanzas/pago_masivo_nuevo.html', {
        'bancos': bancos, 'cuenta': cuenta, 'hasta': hasta, 'docs': docs, 'hoy': timezone.localdate(),
        'proveedor': Tercero.objects.filter(pk=id_o_none(request.GET.get('proveedor'))).first()
        if request.GET.get('proveedor') else None})


@login_required
def pago_masivo(request, pk):
    pago = get_object_or_404(PagoMasivo.objects.select_related('cuenta'), pk=pk)
    lineas = list(pago.lineas.select_related('compra__tercero', 'movimiento'))
    for l in lineas:
        l.abono = op.datos_banco(l.compra.tercero, pago.cuenta)
    if request.GET.get('formato') == 'excel':
        filas = [[n, l.compra.tercero.get_tipo_doc_display(), l.compra.tercero.numero_doc, l.compra.tercero.nombre,
                  l.compra.tercero.get_banco_display() or '', l.abono[0], l.abono[1], pago.cuenta.moneda, l.monto,
                  f'{l.compra.tipo_comprobante} {l.compra.numero_completo}', l.compra.tercero.email]
                 for n, l in enumerate(lineas, 1)]
        return excel_response(f'Pago_masivo_{pago.numero}', f'Pago masivo {pago.numero} - {pago.cuenta}',
                              ['N°', 'Tipo doc.', 'N° documento', 'Beneficiario', 'Banco destino', 'Tipo de abono',
                               'Cuenta / CCI', 'Moneda', 'Monto', 'Referencia (comprobante)', 'Correo'], filas)
    if request.method == 'POST':
        try:
            if request.POST.get('accion') == 'registrar':
                op.registrar_pago_masivo(pago, request.user, request.POST.get('numero_operacion', '').strip())
                messages.success(request, f'Pagos de {pago} registrados en {pago.cuenta}.')
            elif request.POST.get('accion') == 'anular' and pago.estado == 'BORRADOR':
                pago.estado = 'ANULADO'
                pago.save(update_fields=['estado'])
                messages.success(request, f'{pago} anulado (no se registró ningún pago).')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
        return redirect('finanzas:pago_masivo', pk)
    sin_cuenta = [l for l in lineas if not l.abono[1]]
    return render(request, 'finanzas/pago_masivo.html', {'pago': pago, 'lineas': lineas, 'sin_cuenta': sin_cuenta})


# ---------------------------------------------------------------- constancias de detracción de clientes
SESION_CONSTANCIAS = 'constancias_clientes'


@login_required
def constancias_clientes(request):
    """Reporte de constancias de SUNAT (lo que los clientes depositaron en la cuenta del BN de la empresa): vista
    previa factura por factura y registro de las cobranzas de detracción."""
    from . import constancias as cst
    cuentas_bn = Cuenta.objects.filter(activo=True, es_detracciones=True)
    items = None
    if request.method == 'POST' and request.POST.get('accion') == 'leer':
        try:
            items = cst.emparejar_clientes(cst.leer(request.FILES['archivo']) if request.FILES.get('archivo') else [])
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
        else:
            if not items:
                messages.warning(request, 'Elija el archivo con las constancias.')
            request.session[SESION_CONSTANCIAS] = [
                {'constancia': i['constancia'], 'fecha': i['fecha'].isoformat() if i['fecha'] else None,
                 'importe': str(i['importe']) if i['importe'] is not None else None, 'tipo': i['tipo'],
                 'serie': i['serie'], 'numero': i['numero'], 'ruc_adquirente': i['ruc_adquirente'],
                 'ruc_proveedor': i['ruc_proveedor']} for i in items]
    elif request.method == 'POST' and request.POST.get('accion') == 'registrar':
        guardados = request.session.get(SESION_CONSTANCIAS) or []
        registros = [{**g, 'fecha': date.fromisoformat(g['fecha']) if g['fecha'] else None,
                      'importe': Decimal(g['importe']) if g['importe'] else None} for g in guardados]
        elegidas = set(request.POST.getlist('constancia'))
        items = [i for i in cst.emparejar_clientes(registros) if i['constancia'] in elegidas]
        cuenta = cuentas_bn.filter(pk=id_o_none(request.POST.get('cuenta'))).first()
        try:
            if cuenta is None:
                raise op.ErrorTesoreria('Elija su cuenta de detracciones del Banco de la Nación.')
            creados = cst.registrar_clientes(items, cuenta, request.user)
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
            items = cst.emparejar_clientes(registros)
        else:
            request.session.pop(SESION_CONSTANCIAS, None)
            messages.success(request, f'{len(creados)} cobranza(s) de detracción registradas en {cuenta}.')
            return redirect('ventas:detracciones')
    nombres = {'REGISTRAR': 'Se registrará', 'YA': 'Ya registrada', 'NO_ENCONTRADA': 'Factura no encontrada',
               'SIN_DETRACCION': 'Sin detracción por cobrar'}
    for i in items or []:
        i['estado_nombre'] = nombres[i['estado']]
    return render(request, 'finanzas/constancias_clientes.html', {
        'items': items, 'cuentas_bn': cuentas_bn,
        'por_registrar': sum(1 for i in items or [] if i['estado'] == 'REGISTRAR')})


# ---------------------------------------------------------------- depósito masivo de detracciones
@login_required
def detracciones_masivas(request):
    from .models import DepositoDetraccion
    qs = DepositoDetraccion.objects.select_related('cuenta')
    return render(request, 'finanzas/detracciones_masivas.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page'))})


@login_required
def detracciones_masivas_nuevo(request):
    from . import detracciones_masivo as dm
    bancos = Cuenta.objects.filter(activo=True, moneda='PEN', es_detracciones=False)
    cuenta = bancos.filter(pk=request.GET.get('cuenta') or request.POST.get('cuenta')).first() or bancos.first()
    hasta = request.GET.get('hasta') or timezone.localdate().isoformat()
    compras = dm.compras_por_depositar(hasta, request.GET.get('proveedor') or None)
    if request.method == 'POST' and cuenta:
        try:
            lineas = []
            for c in compras:
                monto = _monto(request.POST.get(f'monto_{c.pk}'))
                if monto:
                    lineas.append((c, request.POST.get(f'codigo_{c.pk}', ''), monto))
            lote = dm.crear_lote(cuenta, _fecha(request.POST.get('fecha')), lineas, request.user,
                                 request.POST.get('glosa', '')[:250])
            messages.success(request, f'{lote} generado por S/ {lote.total:,.2f}. Descargue el archivo para SUNAT, '
                                      f'páguelo y registre el pago con las constancias.')
            return redirect('finanzas:detraccion_masiva', lote.pk)
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
    for c in compras:
        c.importe = dm.importe_deposito(c)
        c.codigo_sunat = dm.codigo_de(c)
        c.cuenta_bn = dm.cuenta_bn(c.tercero)
    return render(request, 'finanzas/detraccion_masiva_nuevo.html', {
        'bancos': bancos, 'cuenta': cuenta, 'hasta': hasta, 'compras': compras, 'codigos': dm.codigos(),
        'hoy': timezone.localdate(), 'proveedor': Tercero.objects.filter(pk=id_o_none(request.GET.get('proveedor'))).first()
        if request.GET.get('proveedor') else None})


@login_required
def detraccion_masiva(request, pk):
    from . import detracciones_masivo as dm
    from .models import DepositoDetraccion
    lote = get_object_or_404(DepositoDetraccion.objects.select_related('cuenta'), pk=pk)
    errores = dm.errores_archivo(lote) if lote.estado == 'BORRADOR' else []
    if request.GET.get('formato') == 'txt':
        if errores:
            messages.error(request, 'Corrija antes de generar el archivo: ' + ' '.join(errores))
            return redirect('finanzas:detraccion_masiva', pk)
        from django.http import HttpResponse
        nombre, contenido = dm.archivo_txt(lote)
        resp = HttpResponse(contenido.encode('ascii', 'replace'), content_type='text/plain; charset=ascii')
        resp['Content-Disposition'] = f'attachment; filename="{nombre}"'
        return resp
    lineas = list(lote.lineas.select_related('compra__tercero', 'movimiento'))
    if request.GET.get('formato') == 'excel':
        nombres = dict(dm.codigos())
        filas = [[n, l.compra.tercero.numero_doc, l.compra.tercero.nombre, dm.cuenta_bn(l.compra.tercero),
                  f'{l.compra.tipo_comprobante} {l.compra.numero_completo}', l.compra.fecha_emision.strftime('%Y%m'),
                  nombres.get(l.codigo, l.codigo), l.monto, l.constancia] for n, l in enumerate(lineas, 1)]
        return excel_response(f'Detracciones_lote_{lote.lote}', f'{lote} - {lote.cuenta}',
                              ['N°', 'RUC proveedor', 'Proveedor', 'Cuenta BN', 'Comprobante', 'Periodo',
                               'Bien o servicio', 'Importe S/', 'N° constancia'], filas)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        constancias = {l.pk: request.POST.get(f'constancia_{l.pk}', '') for l in lineas}
        try:
            if accion == 'registrar':
                dm.registrar_pago(lote, _fecha(request.POST.get('fecha')), request.POST.get('numero_operacion', ''),
                                  constancias, request.user)
                messages.success(request, f'Depósitos de {lote} registrados en {lote.cuenta}.')
            elif accion == 'constancias':
                if request.FILES.get('archivo'):
                    leidas, faltan = dm.leer_constancias(request.FILES['archivo'], lote)
                    constancias = {**constancias, **leidas}
                    if faltan:
                        messages.warning(request, 'No están en el lote: ' + ', '.join(faltan[:20]))
                n = dm.guardar_constancias(lote, constancias)
                messages.success(request, f'{n} constancia(s) registradas.')
            elif accion == 'anular':
                dm.anular_lote(lote)
                messages.success(request, f'{lote} anulado (no se registró ningún depósito).')
        except op.ErrorTesoreria as exc:
            messages.error(request, str(exc))
        return redirect('finanzas:detraccion_masiva', pk)
    nombres = dict(dm.codigos())
    for l in lineas:
        l.bien = nombres.get(l.codigo, l.codigo)
    return render(request, 'finanzas/detraccion_masiva.html', {'lote': lote, 'lineas': lineas, 'errores': errores})
