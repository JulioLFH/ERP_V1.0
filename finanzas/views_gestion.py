"""Pantallas del planeamiento y la gestión financiera (v1.84): presupuesto de caja con escenarios y real vs
proyectado (FIN-02), gestión de cobranza (FIN-03), propuesta de pagos (FIN-04), financiamiento (FIN-05), cierre de
préstamos y arrendamientos NIIF 16 (FIN-06), riesgo cambiario (FIN-07) y tablero financiero (FIN-08)."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Tercero
from ventas.crm_tabla import responder

from . import cobranza as cob
from . import financiamiento as fin
from . import forms_gestion as fg
from . import niif16
from . import propuesta_pagos as pp
from . import proyeccion
from .models import (Arrendamiento, CesionFactura, DepositoPlazo, Forward, GestionCobranza, PartidaCaja,
                     PropuestaPago, ProvisionCobranza, ProyeccionGuardada)
from .operaciones import ErrorTesoreria

D0 = Decimal('0')
ERRORES = (ErrorTesoreria, cob.ErrorCobranza, pp.ErrorPropuesta, ValueError, InvalidOperation)


def _fecha(texto, defecto=None):
    try:
        return date.fromisoformat(texto or '')
    except ValueError:
        return defecto


def _decimal(texto):
    try:
        return Decimal((texto or '').replace(',', '')) if (texto or '').strip() else None
    except InvalidOperation:
        raise ValueError('Revise el monto.')


def _periodo(request):
    p = (request.POST.get('periodo') or request.GET.get('periodo') or '').replace('-', '')
    if len(p) == 6 and p.isdigit():
        return p
    hoy = timezone.localdate()
    anterior = hoy.replace(day=1) - timedelta(days=1)
    return f'{anterior:%Y%m}'


def _post(request, funcion, exito):
    try:
        resultado = funcion()
        messages.success(request, exito(resultado) if callable(exito) else exito)
    except ERRORES as exc:
        messages.error(request, str(exc))


# ---------------------------------------------------------------- FIN-02 presupuesto de caja
@login_required
def presupuesto_caja(request):
    from core.politicas import cuentas_permitidas
    escenario = request.GET.get('escenario') if request.GET.get('escenario') in dict(proyeccion.ESCENARIOS) else 'BASE'
    moneda = request.GET.get('moneda') if request.GET.get('moneda') in ('PEN', 'USD') else ''
    try:
        meses = min(max(int(request.GET.get('meses', 12)), 3), 24)
    except ValueError:
        meses = 12
    form = fg.PartidaCajaForm(request.POST if request.POST.get('accion') == 'partida' else None)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'partida' and form.is_valid():
            p = form.save(commit=False)
            p.creado_por = request.user
            p.save()
            messages.success(request, f'Partida «{p.concepto}» agregada al presupuesto.')
            return redirect(request.get_full_path())
        if accion == 'borrar':
            PartidaCaja.objects.filter(pk=request.POST.get('pk')).delete()
            messages.success(request, 'Partida quitada.')
            return redirect(request.get_full_path())
        if accion == 'guardar':
            datos = proyeccion.presupuesto_caja(meses, escenario=escenario, moneda=moneda or None,
                                                cuentas=cuentas_permitidas(request.user))
            g = proyeccion.guardar(datos, request.user, moneda)
            messages.success(request, f'Proyección guardada ({g}): compárela con lo real desde el próximo mes.')
            return redirect(request.get_full_path())
    p = proyeccion.presupuesto_caja(meses, escenario=escenario, moneda=moneda or None,
                                    cuentas=cuentas_permitidas(request.user))
    if request.GET.get('formato') == 'excel':
        from core.utils import excel_response
        encabezado = ['Concepto', 'Tipo'] + [f'{m:%m/%Y}' for m in p['meses']] + ['Total']
        filas = [['Saldo inicial', ''] + p['saldos_iniciales'] + ['']]
        filas += [[f['concepto'], f['tipo'].capitalize()] + f['valores'] + [f['total']] for f in p['filas']]
        filas += [['Total ingresos', ''] + p['ingresos'] + [sum(p['ingresos'], D0)],
                  ['Total egresos', ''] + p['egresos'] + [sum(p['egresos'], D0)],
                  ['Saldo final', ''] + p['saldos'] + ['']]
        return excel_response('Presupuesto_caja', f'PRESUPUESTO DE CAJA – escenario {escenario.lower()}', encabezado,
                              filas)
    return render(request, 'finanzas/presupuesto_caja.html', {
        'p': p, 'form': form, 'escenarios': proyeccion.ESCENARIOS, 'escenario': escenario, 'moneda': moneda,
        'meses_n': meses, 'opciones_meses': [6, 12, 18, 24], 'partidas': PartidaCaja.objects.all()[:200],
        'guardadas': ProyeccionGuardada.objects.all()[:20]})


@login_required
def real_vs_proyectado(request, pk):
    g = get_object_or_404(ProyeccionGuardada, pk=pk)
    filas = [[f'{f["periodo"][4:]}/{f["periodo"][:4]}', f['p_ingresos'], f['r_ingresos'], f['d_ingresos'],
              f['pct_ingresos'], f['p_egresos'], f['r_egresos'], f['d_egresos'], f['pct_egresos']]
             for f in proyeccion.real_vs_proyectado(g)]
    return responder(request, f'Real vs proyectado · {g}', [{'columnas': [
        'Mes', 'Ingresos proyectados', 'Ingresos reales', 'Desviación', 'Desv. %', 'Egresos proyectados',
        'Egresos reales', 'Desviación', 'Desv. %'], 'filas': filas}],
        botones=[('Presupuesto de caja', reverse('finanzas:presupuesto_caja'), 'bi-calendar3')],
        ayuda='Lo real son los movimientos de caja y bancos del mes (sin transferencias entre cuentas). Los meses '
              'futuros aparecen cuando terminan.')


# ---------------------------------------------------------------- FIN-03 cobranza
def _mora(d):
    return {'texto': f'{d} d', 'clase': 'text-danger fw-semibold' if d > 30 else ('text-warning' if d else '')}


@login_required
def cobranza_gestion(request):
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'bloqueos':
            _post(request, cob.actualizar_bloqueos, lambda r: f'{r[0]} cliente(s) bloqueado(s) por mora y {r[1]} '
                                                              f'desbloqueado(s).')
        elif accion == 'promesas':
            _post(request, cob.revisar_promesas, lambda r: f'{len(r)} promesa(s) incumplida(s).')
        return redirect('finanzas:cobranza_gestion')
    mias = request.GET.get('mias') == '1'
    vendedor = request.GET.get('vendedor', '')
    filas_c = cob.cartera(gestor=request.user if mias else None, vendedor=vendedor or None)
    tramos = [t[0] for t in cob.TRAMOS]
    filas = []
    for c in filas_c:
        t, u = c['tercero'], c['ultima']
        filas.append([{'texto': t.nombre, 'url': reverse('finanzas:cobranza_cliente', args=[t.pk])}, c['docs'],
                      c['saldo'], c['vencido'], *c['tramos'], _mora(c['mora']), c['dso'] if c['dso'] is not None else '',
                      str(t.gestor_cobranza or ''), {'texto': 'Bloqueado', 'clase': 'badge text-bg-danger'}
                      if t.bloqueado_mora else '',
                      f'{u.fecha:%d/%m} {u.get_tipo_display()}: {u.resultado[:40]}' if u else '',
                      {'texto': f'{u.promesa_fecha:%d/%m} {u.get_estado_promesa_display()}',
                       'clase': 'text-danger' if u.estado_promesa == 'INCUMPLIDA' else ''}
                      if u and u.promesa_fecha else ''])
    vendedores = cob.por_vendedor()
    total = cob.dso()
    return responder(request, 'Gestión de cobranza', [
        {'titulo': 'Cartera por cliente', 'columnas': ['Cliente', 'Docs.', 'Saldo S/', 'Vencido S/', *tramos, 'Mora máx.',
                                                       'DSO', 'Gestor', 'Estado', 'Última gestión', 'Promesa'],
         'filas': filas},
        {'titulo': 'Antigüedad por vendedor', 'columnas': ['Vendedor', 'Saldo S/', 'Vencido S/', *tramos],
         'filas': [[n, f['saldo'], f['vencido'], *f['tramos']] for n, f in vendedores]},
        {'titulo': 'Acciones', 'columnas': ['Acción', ''], 'filas': [
            ['Bloquear o desbloquear clientes según los días de mora (parámetro FIN_DIAS_BLOQUEO_MORA)',
             {'texto': 'Actualizar bloqueos', 'post': '', 'campo': 'accion', 'valor': 'bloqueos'}],
            ['Revisar las promesas de pago vencidas', {'texto': 'Revisar promesas', 'post': '', 'campo': 'accion',
                                                       'valor': 'promesas'}]]}],
        por_seccion=True,
        filtros=[('mias', 'Solo mis clientes', mias)],
        selects=[('vendedor', 'Vendedor', [('', 'Todos')] + [(n, n) for n, _ in vendedores], vendedor)],
        botones=[('Recordatorios de hoy', reverse('finanzas:recordatorios'), 'bi-bell'),
                 ('Provisión de cobranza dudosa', reverse('finanzas:provision'), 'bi-shield-exclamation')],
        ayuda=f'DSO de la empresa (90 días): {total if total is not None else "—"} días. La tarea diaria '
              f'«cobranza_diaria» envía los recordatorios, revisa las promesas y bloquea por mora.')


@login_required
def cobranza_cliente(request, pk):
    from django.contrib.auth import get_user_model
    t = get_object_or_404(Tercero, pk=pk)
    docs = cob.pendientes(t)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'gestor':
            u = get_user_model().objects.filter(pk=request.POST.get('gestor'), is_active=True).first()
            Tercero.objects.filter(pk=t.pk).update(gestor_cobranza=u)
            messages.success(request, f'Gestor de cobranza: {u or "sin asignar"}.')
        else:
            venta = next((d for d in docs if str(d.pk) == request.POST.get('venta')), None)
            _post(request, lambda: cob.registrar_gestion(
                t, request.POST.get('tipo', 'LLAMADA'), request.POST.get('resultado', ''), request.user, venta,
                _fecha(request.POST.get('promesa_fecha')), _decimal(request.POST.get('promesa_monto'))),
                'Gestión registrada.')
        return redirect('finanzas:cobranza_cliente', pk)
    hoy = timezone.localdate()
    for d in docs:
        vence = d.fecha_vencimiento or d.fecha_emision
        d.whatsapp = cob.enlace_whatsapp(d, (hoy - vence).days)
    return render(request, 'finanzas/cobranza_cliente.html', {
        't': t, 'docs': docs, 'saldo': sum((d.saldo_pen for d in docs), D0), 'dso': cob.dso(t),
        'gestiones': GestionCobranza.objects.filter(tercero=t).select_related('venta', 'usuario')[:100],
        'tipos': GestionCobranza.TIPOS, 'hoy': hoy,
        'usuarios': get_user_model().objects.filter(is_active=True).order_by('username')})


@login_required
def recordatorios(request):
    if request.method == 'POST':
        _post(request, cob.enviar_recordatorios, lambda r: f'{r[0]} recordatorio(s) enviados, {r[1]} sin enviar.')
        return redirect('finanzas:recordatorios')
    filas = []
    for v, dias in cob.recordatorios_de_hoy():
        enlace = cob.enlace_whatsapp(v, dias)
        filas.append([{'texto': v.tercero.nombre, 'url': reverse('finanzas:cobranza_cliente', args=[v.tercero_id])},
                      str(v), v.fecha_vencimiento or v.fecha_emision, f'{dias:+d} días', v.saldo,
                      v.tercero.email or {'texto': 'sin correo', 'clase': 'text-danger'},
                      {'texto': 'WhatsApp', 'url': enlace} if enlace else ''])
    from .models import RecordatorioCobranza
    historial = [[timezone.localtime(r.creado).strftime('%d/%m/%Y %H:%M'), str(r.venta), f'{r.dias:+d}', r.canal,
                  'Enviado' if r.enviado else {'texto': r.error or 'No enviado', 'clase': 'text-danger'}]
                 for r in RecordatorioCobranza.objects.select_related('venta')[:100]]
    return responder(request, 'Recordatorios de cobranza', [
        {'titulo': 'Para hoy', 'columnas': ['Cliente', 'Comprobante', 'Vence', 'Regla', 'Saldo', 'Correo', ''],
         'filas': filas + [[{'texto': 'Enviar los correos de hoy', 'post': '', 'campo': 'accion',
                             'valor': 'enviar'}, '', '', '', '', '', '']] if filas else []},
        {'titulo': 'Enviados', 'columnas': ['Fecha', 'Comprobante', 'Regla', 'Canal', 'Estado'], 'filas': historial}],
        por_seccion=True, botones=[('Gestión de cobranza', reverse('finanzas:cobranza_gestion'), 'bi-arrow-left')],
        ayuda='Calendario: 3 días antes del vencimiento, el día del vencimiento y a los 7, 15 y 30 días de atraso. El '
              'correo usa Ajustes › Correo saliente; el WhatsApp abre el mensaje listo para enviarlo desde su '
              'teléfono.')


@login_required
def provision(request):
    periodo = _periodo(request)
    if request.method == 'POST':
        _post(request, lambda: cob.registrar_provision(periodo, request.user),
              lambda r: f'Provisión de {periodo[4:]}/{periodo[:4]}: S/ {r.calculada:,.2f}' +
                        (f' (asiento {r.asiento.numero}).' if r.asiento else ' (sin variación).'))
        return redirect(f'{reverse("finanzas:provision")}?periodo={periodo}')
    calc = cob.calcular_provision()
    acumulada = cob.provision_acumulada()
    filas = [[f['tramo'], f['saldo'], f'{f["pct"]:g} %', f['provision']] for f in calc['filas']]
    filas.append([{'texto': 'Total', 'clase': 'fw-semibold'}, sum((f['saldo'] for f in calc['filas']), D0), '',
                  calc['total']])
    filas.append(['Provisión acumulada en la cuenta 19', '', '', acumulada])
    filas.append([{'texto': f'Registrar la provisión de {periodo[4:]}/{periodo[:4]}', 'post': '', 'campo': 'periodo',
                   'valor': periodo, 'confirmar': f'¿Registrar el asiento por la diferencia (S/ '
                                                  f'{calc["total"] - acumulada:,.2f})?'}, '', '', ''])
    historial = [[f'{p.periodo[4:]}/{p.periodo[:4]}', p.calculada, p.anterior,
                  {'texto': p.asiento.numero, 'url': reverse('contabilidad:asiento_detalle', args=[p.asiento_id])}
                  if p.asiento_id else 'Sin variación'] for p in ProvisionCobranza.objects.select_related('asiento')]
    return responder(request, 'Provisión de cobranza dudosa (NIIF 9)', [
        {'titulo': 'Pérdida esperada por tramo de antigüedad', 'columnas': ['Tramo', 'Saldo S/', '% pérdida esperada',
                                                                            'Provisión S/'], 'filas': filas},
        {'titulo': 'Historial', 'columnas': ['Mes', 'Calculada', 'Acumulada antes', 'Asiento'], 'filas': historial}],
        por_seccion=True, textos=[('periodo', 'Mes (AAAAMM)', periodo)],
        botones=[('Gestión de cobranza', reverse('finanzas:cobranza_gestion'), 'bi-arrow-left')],
        ayuda='Enfoque simplificado de la NIIF 9: matriz de provisiones por tramo (Ajustes › Parámetros › Finanzas, '
              'ajústela con la historia de pérdidas). El asiento lleva la diferencia con la cuenta 19: Debe 6841 / '
              'Haber 191. Tributariamente, la provisión solo es deducible si cumple el art. 21 del Reglamento de la '
              'LIR (confirmar con el contador).')


# ---------------------------------------------------------------- FIN-04 propuesta de pagos
@login_required
def propuestas(request):
    filas = [[{'texto': p.numero, 'url': reverse('finanzas:propuesta', args=[p.pk])}, p.fecha, p.hasta, str(p.cuenta),
              p.total, p.get_estado_display(), str(p.aprobado_por or ''),
              {'texto': p.pago_masivo.numero, 'url': reverse('finanzas:pago_masivo', args=[p.pago_masivo_id])}
              if p.pago_masivo_id else ''] for p in PropuestaPago.objects.select_related('cuenta', 'aprobado_por',
                                                                                         'pago_masivo')[:200]]
    return responder(request, 'Propuestas de pago', [{'columnas': [
        'N°', 'Fecha de pago', 'Vencimientos hasta', 'Banco', 'Total', 'Estado', 'Aprobó', 'Pago masivo'],
        'filas': filas}], botones=[('Nueva propuesta', reverse('finanzas:propuesta_nueva'), 'bi-plus-lg'),
                                   ('Descuentos por pronto pago', reverse('finanzas:descuentos_pp'), 'bi-percent')],
        ayuda='Tesorería arma la propuesta (vencimientos, proveedores críticos, descuentos y caja disponible) → la '
              'gerencia la aprueba → se genera el pago masivo con el archivo del banco.')


@login_required
def propuesta_nueva(request):
    hoy = timezone.localdate()
    form = fg.PropuestaForm(request.POST or None, initial={'hasta': hoy + timedelta(days=7 - hoy.weekday())})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            p = pp.armar(d['cuenta'], d['fecha'], d['hasta'], d['reserva'], request.user)
            messages.success(request, f'Propuesta {p.numero} armada: revise los comprobantes y envíela a aprobación.')
            return redirect('finanzas:propuesta', p.pk)
        except ERRORES as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva propuesta de pagos',
                                              'cancelar': reverse('finanzas:propuestas')})


@login_required
def propuesta(request, pk):
    p = get_object_or_404(PropuestaPago.objects.select_related('cuenta', 'creado_por', 'aprobado_por'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'guardar':
            ids = {int(x) for x in request.POST.getlist('incluir') if x.isdigit()}
            montos = {}
            try:
                for l in p.lineas.all():
                    m = _decimal(request.POST.get(f'monto_{l.pk}'))
                    if m is not None and m != l.monto:
                        montos[l.pk] = m
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('finanzas:propuesta', pk)
            _post(request, lambda: pp.cambiar(p, ids, montos), 'Propuesta actualizada.')
        elif accion == 'enviar':
            _post(request, lambda: pp.enviar(p, request.user), 'Propuesta enviada a aprobación de la gerencia.')
        elif accion in ('aprobar', 'rechazar'):
            _post(request, lambda: pp.aprobar(p, request.user, accion == 'aprobar', request.POST.get('motivo', '')),
                  'Propuesta aprobada.' if accion == 'aprobar' else 'Propuesta rechazada: vuelve a tesorería.')
        elif accion == 'generar':
            try:
                pago = pp.generar(p, request.user)
                messages.success(request, f'{pago} generado: descargue el archivo para el banco.')
                return redirect('finanzas:pago_masivo', pago.pk)
            except ERRORES as exc:
                messages.error(request, str(exc))
        return redirect('finanzas:propuesta', pk)
    return render(request, 'finanzas/propuesta_pago.html', {
        'p': p, 'lineas': p.lineas.select_related('compra__tercero'), 'puede_aprobar': pp.puede_aprobar(request.user, p),
        'caja': p.cuenta.saldo - p.reserva})


@login_required
def descuentos_pp(request):
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde'), hoy.replace(day=1) - timedelta(days=90))
    hasta = _fecha(request.GET.get('hasta'), hoy)
    datos = pp.descuentos(desde, hasta)
    filas = [[str(d['compra']), d['compra'].tercero.nombre, d['compra'].total, d['limite'], d['pagado'] or '',
              d['importe'], {'texto': {'GANADO': 'Ganado', 'PERDIDO': 'Perdido', 'DISPONIBLE': 'Disponible'}[d['estado']],
                             'clase': {'GANADO': 'text-success', 'PERDIDO': 'text-danger', 'DISPONIBLE': ''}[d['estado']]}]
             for d in datos]
    ganado = sum((d['importe'] for d in datos if d['estado'] == 'GANADO'), D0)
    perdido = sum((d['importe'] for d in datos if d['estado'] == 'PERDIDO'), D0)
    return responder(request, 'Descuentos por pronto pago', [{'columnas': [
        'Comprobante', 'Proveedor', 'Total', 'Pagar hasta', 'Pagado el', 'Descuento', 'Estado'], 'filas': filas}],
        rango=(desde, hasta), ayuda=f'Ganados S/ {ganado:,.2f} · perdidos S/ {perdido:,.2f}. El % y los días del '
                                    f'descuento se configuran en la ficha del proveedor.')


# ---------------------------------------------------------------- FIN-05 financiamiento
def _formulario(request, form_class, guardar, exito):
    form = form_class(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = guardar(form)
                messages.success(request, exito(obj))
                return form, True
            except ERRORES as exc:
                form.add_error(None, str(exc))
        messages.error(request, 'Revise el formulario.')
    return form, False


@login_required
def lineas_credito(request):
    form, ok = _formulario(request, fg.LineaCreditoForm, lambda f: f.save(), lambda o: f'Línea {o} registrada.')
    if ok:
        return redirect('finanzas:lineas_credito')
    filas = [[str(l.banco.nombre), l.get_tipo_display(), l.moneda, l.limite, l.uso['prestamos'], l.uso['fianzas'],
              l.uso['factoring'], l.uso['utilizado'], {'texto': f'{l.uso["disponible"]:,.2f}',
                                                       'clase': 'text-danger' if l.uso['disponible'] < 0 else ''},
              f'{l.uso["pct"]} %' if l.uso['pct'] is not None else '', l.tasa, l.vencimiento or '',
              'Activa' if l.activa else 'Inactiva'] for l in fin.lineas_con_uso()]
    return responder(request, 'Líneas de crédito', [{'columnas': [
        'Banco', 'Tipo', 'Moneda', 'Límite', 'Préstamos', 'Cartas fianza', 'Factoring', 'Utilizado', 'Disponible',
        'Uso', 'TEA %', 'Vence', 'Estado'], 'filas': filas}], form=form if request.method == 'POST' or request.GET.get(
        'nueva') else None, form_titulo='Nueva línea de crédito',
        botones=[('Nueva línea', '?nueva=1', 'bi-plus-lg'), ('Depósitos a plazo', reverse('finanzas:depositos'),
                                                             'bi-safe'),
                 ('Factoring', reverse('finanzas:cesiones'), 'bi-receipt'),
                 ('Cartas fianza', reverse('finanzas:cartas_fianza'), 'bi-shield-check')],
        ayuda='El uso se calcula solo: préstamos vigentes de la línea (saldo de capital), cartas fianza vigentes y '
              'facturas cedidas en factoring. Un préstamo o una carta que exceda el disponible no se registra.')


@login_required
def depositos(request):
    if request.method == 'POST' and request.POST.get('accion') == 'devengar':
        periodo = _periodo(request)
        _post(request, lambda: fin.devengar_depositos(periodo, request.user),
              lambda a: f'Intereses de {periodo[4:]}/{periodo[:4]} devengados (asiento {a.numero}).')
        return redirect('finanzas:depositos')
    if request.method == 'POST' and request.POST.get('accion') == 'cancelar':
        d = get_object_or_404(DepositoPlazo, pk=request.POST.get('pk'))
        _post(request, lambda: fin.cancelar_deposito(d, timezone.localdate(), request.user),
              f'{d} cancelado: capital e intereses abonados en {d.cuenta}.')
        return redirect('finanzas:depositos')

    def guardar(f):
        return fin.crear_deposito(f.save(commit=False), request.user)
    form, ok = _formulario(request, fg.DepositoForm, guardar, lambda o: f'{o} registrado: sale el capital del banco.')
    if ok:
        return redirect('finanzas:depositos')
    hoy = timezone.localdate()
    filas = [[str(d), d.moneda, d.monto, d.tasa, d.inicio, d.vencimiento, d.devengado, d.devengado_hasta or '',
              d.get_estado_display(), {'texto': 'Cancelar al vencimiento', 'post': '', 'campo': 'pk', 'valor': d.pk,
                                       'extra': {'accion': 'cancelar'},
                                       'confirmar': f'¿Cancelar el {d} y abonar capital e intereses?'}
              if d.estado == 'VIGENTE' and d.vencimiento <= hoy else '']
             for d in DepositoPlazo.objects.select_related('banco')]
    periodo = _periodo(request)
    filas.append([{'texto': f'Devengar los intereses de {periodo[4:]}/{periodo[:4]}', 'post': '', 'campo': 'periodo',
                   'valor': periodo, 'extra': {'accion': 'devengar'}}, '', '', '', '', '', '', '', '', ''])
    return responder(request, 'Depósitos a plazo e inversiones', [{'columnas': [
        'Depósito', 'Moneda', 'Capital', 'TEA %', 'Inicio', 'Vence', 'Intereses devengados', 'Devengado hasta', 'Estado',
        ''], 'filas': filas}], form=form if request.method == 'POST' or request.GET.get('nuevo') else None,
        form_titulo='Nuevo depósito a plazo', botones=[('Nuevo depósito', '?nuevo=1', 'bi-plus-lg'),
                                                       ('Líneas de crédito', reverse('finanzas:lineas_credito'),
                                                        'bi-arrow-left')],
        ayuda='Al abrirlo sale el capital del banco (cuenta 1062). Cada mes se devengan los intereses (Debe 1683 / Haber '
              '7721) y al vencer se abonan capital e intereses.')


@login_required
def cesiones(request):
    def guardar(f):
        c = f.save(commit=False)
        return fin.ceder(c, f.cleaned_data.get('cuenta'), request.user)
    form, ok = _formulario(request, fg.CesionForm, guardar,
                           lambda o: f'{o.get_tipo_display()} de {o.venta} registrado.')
    if ok:
        return redirect('finanzas:cesiones')
    filas = [[str(c.venta), c.venta.tercero.nombre, c.get_tipo_display(), str(c.entidad.nombre if c.entidad else ''),
              c.fecha, c.codigo_cavali, c.monto, c.interes + c.comision, c.neto, c.get_estado_display(),
              {'texto': c.movimiento.voucher, 'url': reverse('finanzas:voucher', args=[c.movimiento_id])}
              if c.movimiento_id else ''] for c in CesionFactura.objects.select_related('venta__tercero', 'entidad',
                                                                                         'movimiento')[:300]]
    return responder(request, 'Facturas negociables y factoring', [{'columnas': [
        'Factura', 'Cliente', 'Tipo', 'Entidad', 'Fecha', 'CAVALI', 'Cedido', 'Interés y comisión', 'Neto', 'Estado',
        'Voucher'], 'filas': filas}], form=form if request.method == 'POST' or request.GET.get('nueva') else None,
        form_titulo='Anotar o ceder una factura', botones=[('Nueva', '?nueva=1', 'bi-plus-lg')],
        ayuda='Factura negociable (Ley 29623): se anota el código de CAVALI. Factoring o confirming: la entidad paga '
              'el monto cedido (la factura queda cobrada) y descuenta interés y comisión (gasto financiero 6739).')


@login_required
def cartas_fianza(request):
    form, ok = _formulario(request, fg.CartaFianzaForm, lambda f: f.save(),
                           lambda o: f'Carta fianza {o.numero or ""} registrada.')
    if ok:
        return redirect('finanzas:cartas_fianza')
    filas = [[f['origen'], f['numero'], f['banco'], f['beneficiario'], f['tipo'], f['moneda'], f['monto'],
              f['vencimiento'], {'texto': f'{f["dias"]} d', 'clase': 'text-danger fw-semibold' if f['alerta'] else ''},
              f['estado'], f['linea']] for f in fin.central_fianzas()]
    return responder(request, 'Cartas fianza (central)', [{'columnas': [
        'Origen', 'N°', 'Banco', 'Beneficiario', 'Tipo', 'Moneda', 'Monto', 'Vence', 'Días', 'Estado', 'Línea'],
        'filas': filas}], form=form if request.method == 'POST' or request.GET.get('nueva') else None,
        form_titulo='Nueva carta fianza de la empresa', botones=[('Nueva carta', '?nueva=1', 'bi-plus-lg')],
        ayuda='Reúne las cartas fianza de la empresa y las de las obras (Obras › Garantías). En rojo, las que vencen '
              'en 15 días o menos: renuévelas o pida su devolución.')


# ---------------------------------------------------------------- FIN-06 préstamos al cierre y NIIF 16
@login_required
def prestamos_cierre(request):
    from . import prestamos as srv
    periodo = _periodo(request)
    if request.method == 'POST':
        _post(request, lambda: srv.devengar(periodo, request.user),
              lambda a: f'Intereses devengados de {periodo[4:]}/{periodo[:4]} registrados (asiento {a.numero}).')
        return redirect(f'{reverse("finanzas:prestamos_cierre")}?periodo={periodo}')
    filas = [[{'texto': str(f['prestamo']), 'url': reverse('finanzas:prestamo', args=[f['prestamo'].pk])},
              f['prestamo'].entidad.nombre, f['prestamo'].moneda, f['corriente'], f['no_corriente'],
              f['corriente'] + f['no_corriente']] for f in srv.deuda_corriente()]
    import calendar
    a, m = int(periodo[:4]), int(periodo[4:])
    fin_mes = date(a, m, calendar.monthrange(a, m)[1])
    from .models import Prestamo
    devengo = [[str(p), p.entidad.nombre, srv.interes_devengado(p, fin_mes)]
               for p in Prestamo.objects.filter(estado='VIGENTE').select_related('entidad')]
    devengo.append([{'texto': f'Registrar el devengo de {periodo[4:]}/{periodo[:4]}', 'post': '', 'campo': 'periodo',
                     'valor': periodo}, '', ''])
    return responder(request, 'Préstamos al cierre del mes', [
        {'titulo': 'Deuda financiera corriente y no corriente (S/)', 'columnas': [
            'Préstamo', 'Entidad', 'Moneda', 'Corriente (12 meses)', 'No corriente', 'Total'], 'filas': filas},
        {'titulo': f'Intereses devengados no vencidos al {fin_mes:%d/%m/%Y}', 'columnas': [
            'Préstamo', 'Entidad', 'Interés devengado'], 'filas': devengo}], por_seccion=True,
        textos=[('periodo', 'Mes (AAAAMM)', periodo)],
        botones=[('Préstamos y leasing', reverse('finanzas:prestamos'), 'bi-arrow-left'),
                 ('Arrendamientos NIIF 16', reverse('finanzas:arrendamientos'), 'bi-building')],
        ayuda='El devengo registra Debe 6731 / Haber 4551 al cierre y su extorno el primer día del mes siguiente (la '
              'cuota sigue registrando su interés al pagarse). En la gracia total el interés del mes se capitaliza. La '
              'porción corriente es el capital que vence en los próximos 12 meses (para el estado de situación y los '
              'indicadores).')


@login_required
def arrendamientos(request):
    if request.method == 'POST' and request.POST.get('accion') == 'mes':
        periodo = _periodo(request)
        _post(request, lambda: niif16.registrar_mes(periodo, request.user),
              lambda a: f'Arrendamientos de {periodo[4:]}/{periodo[:4]} registrados (asiento NIIF {a.numero}).')
        return redirect('finanzas:arrendamientos')

    def guardar(f):
        return niif16.crear(f.save(commit=False), request.user)
    form, ok = _formulario(request, fg.ArrendamientoForm, guardar,
                           lambda o: f'{o} registrado: derecho de uso S/ {o.derecho_uso:,.2f}.')
    if ok:
        return redirect('finanzas:arrendamiento', Arrendamiento.objects.order_by('-id').first().pk)
    filas = [[{'texto': str(r['arr']), 'url': reverse('finanzas:arrendamiento', args=[r['arr'].pk])},
              r['arr'].arrendador.nombre, r['arr'].moneda, r['arr'].cuota, r['arr'].plazo, r['arr'].derecho_uso,
              r['pasivo'], r['activo_neto'], f'{r["cuotas"]}/{r["arr"].plazo}'] for r in niif16.resumen()]
    periodo = _periodo(request)
    filas.append([{'texto': f'Registrar el mes {periodo[4:]}/{periodo[:4]}', 'post': '', 'campo': 'periodo',
                   'valor': periodo, 'extra': {'accion': 'mes'}}, '', '', '', '', '', '', '', ''])
    return responder(request, 'Arrendamientos (NIIF 16)', [{'columnas': [
        'Arrendamiento', 'Arrendador', 'Moneda', 'Renta mensual', 'Plazo', 'Derecho de uso', 'Pasivo', 'Activo neto',
        'Meses registrados'], 'filas': filas}],
        form=form if request.method == 'POST' or request.GET.get('nuevo') else None,
        form_titulo='Nuevo arrendamiento', botones=[('Nuevo arrendamiento', '?nuevo=1', 'bi-plus-lg')],
        ayuda=niif16.__doc__.split('\n\n')[1].replace('\n', ' '))


@login_required
def arrendamiento(request, pk):
    a = get_object_or_404(Arrendamiento, pk=pk)
    filas = [[q.numero, q.fecha, q.cuota, q.interes, q.amortizacion, q.saldo, q.depreciacion,
              {'texto': q.asiento.numero, 'url': reverse('contabilidad:asiento_detalle', args=[q.asiento_id])}
              if q.asiento_id else ''] for q in a.cuotas.select_related('asiento')]
    return responder(request, f'{a} · {a.arrendador.nombre}', [{'columnas': [
        'N°', 'Fecha', 'Renta', 'Interés', 'Amortización', 'Pasivo', 'Depreciación', 'Asiento'], 'filas': filas}],
        botones=[('Arrendamientos', reverse('finanzas:arrendamientos'), 'bi-arrow-left')],
        ayuda=f'Pasivo inicial {a.moneda} {a.pasivo_inicial:,.2f} (valor presente a TEA {a.tasa:g} %); derecho de uso '
              f'{a.derecho_uso:,.2f}.')


# ---------------------------------------------------------------- FIN-07 riesgo cambiario
@login_required
def posicion_moneda(request):
    from . import riesgo_cambiario as rc
    p = rc.posicion()
    signo = {'ACTIVO': 'Activo', 'PASIVO': 'Pasivo', 'FORWARD': 'Cobertura'}
    filas = [[signo[f['lado']], f['rubro'], *f['plazos'], f['total']] for f in p['filas']]
    filas += [[{'texto': 'Exposición neta (activos − pasivos)', 'clase': 'fw-semibold'}, '', *p['neta_por_plazo'],
               p['neta']], ['Exposición con forwards', '', *([''] * len(p['plazos'])), p['cubierta']]]
    sens = [[f'{s["variacion"]:+g} %', s['tc'], s['sin_cobertura'], s['con_cobertura']] for s in p['sensibilidad']]
    return responder(request, 'Posición en moneda extranjera (US$)', [
        {'titulo': f'Activos y pasivos en US$ por plazo (T.C. {p["tc"]})', 'columnas': [
            'Lado', 'Rubro', *p['plazos'], 'Total US$'], 'filas': filas},
        {'titulo': 'Sensibilidad del resultado a una variación del tipo de cambio (S/)', 'columnas': [
            'Variación', 'T.C.', 'Sin cobertura', 'Con forwards'], 'filas': sens}], por_seccion=True,
        botones=[('Forwards', reverse('finanzas:forwards'), 'bi-arrow-left-right')],
        ayuda='Con exposición neta positiva (más activos que pasivos en US$) una subida del tipo de cambio es ganancia; '
              'con exposición negativa, pérdida. Los forwards de compra suman US$ a recibir; los de venta, a entregar.')


@login_required
def forwards(request):
    form, ok = _formulario(request, fg.ForwardForm, lambda f: f.save(), lambda o: 'Forward registrado.')
    if ok:
        return redirect('finanzas:forwards')
    filas = [[f.banco.nombre, f.get_tipo_display(), f.monto_usd, f.tc_pactado, f.fecha, f.vencimiento,
              f.get_estado_display(), f.notas] for f in Forward.objects.select_related('banco')]
    return responder(request, 'Forwards de moneda', [{'columnas': [
        'Banco', 'Tipo', 'Monto US$', 'T.C. pactado', 'Pactado', 'Vence', 'Estado', 'Notas'], 'filas': filas}],
        form=form if request.method == 'POST' or request.GET.get('nuevo') else None, form_titulo='Nuevo forward',
        botones=[('Nuevo forward', '?nuevo=1', 'bi-plus-lg'),
                 ('Posición en US$', reverse('finanzas:posicion_moneda'), 'bi-currency-dollar')])


# ---------------------------------------------------------------- FIN-08 indicadores
@login_required
def tablero_financiero(request):
    from . import indicadores as ind
    periodo = _periodo(request) if request.GET.get('periodo') else f'{timezone.localdate():%Y%m}'
    t = ind.tablero(periodo)
    if request.GET.get('formato') == 'excel':
        from core.utils import excel_response
        return excel_response('Tablero_financiero', f'INDICADORES FINANCIEROS al {periodo[4:]}/{periodo[:4]}',
                              ['Indicador', 'Unidad'] + [f'{m[4:]}/{m[:4]}' for m in t['meses']],
                              [[f['nombre'], f['unidad']] + [v if v is not None else '' for v in f['tendencia']]
                               for f in t['filas']])
    for f in t['filas']:
        valores = [v for v in f['tendencia'] if v is not None]
        f['puntos'] = _sparkline(f['tendencia']) if len(valores) > 1 else ''
    return render(request, 'finanzas/tablero_financiero.html', {'t': t, 'periodo': periodo,
                                                                'mes_input': f'{periodo[:4]}-{periodo[4:]}'})


def _sparkline(valores, ancho=120, alto=28):
    nums = [float(v) for v in valores if v is not None]
    lo, hi = min(nums), max(nums)
    rango = (hi - lo) or 1
    paso = ancho / max(len(valores) - 1, 1)
    puntos = [f'{i * paso:.1f},{alto - 2 - (float(v) - lo) / rango * (alto - 4):.1f}' for i, v in enumerate(valores)
              if v is not None]
    return ' '.join(puntos)


@login_required
def analisis_estados(request):
    from . import indicadores as ind
    hasta = _periodo(request) if request.GET.get('periodo') else f'{timezone.localdate():%Y%m}'
    desde = ind._menos_meses(hasta, 11)
    meses, res = ind.resultados_comparativos(desde, hasta)
    cab = []
    for m in meses:
        cab += [f'{m[4:]}/{m[:4]}', '% ventas', 'Var. %']
    filas = []
    for f in res:
        celdas = [{'texto': f['nombre'], 'clase': 'fw-semibold' if f['estilo'] in ('total', 'subtotal') else ''}]
        for i in range(len(meses)):
            celdas += [f['valores'][i], f'{f["vertical"][i]} %' if f['vertical'][i] is not None else '',
                       f'{f["horizontal"][i]} %' if f['horizontal'][i] is not None else '']
        filas.append(celdas + [f['total']])
    cortes = [ind._menos_meses(hasta, 12), ind._menos_meses(hasta, 1), hasta]
    sit = ind.situacion_comparativa(cortes)
    cab_s = []
    for c in cortes:
        cab_s += [f'{c[4:]}/{c[:4]}', '% activo', 'Var. %']
    filas_s = []
    for f in sit:
        celdas = [{'texto': f['nombre'], 'clase': 'fw-semibold' if f['nombre'].isupper() else ''}]
        for i in range(len(cortes)):
            celdas += [f['valores'][i], f'{f["vertical"][i]} %' if f['vertical'][i] is not None else '',
                       f'{f["horizontal"][i]} %' if f['horizontal'][i] is not None else '']
        filas_s.append(celdas)
    return responder(request, 'Análisis vertical y horizontal', [
        {'titulo': 'Estado de resultados (12 meses)', 'columnas': ['Concepto', *cab, 'Total'], 'filas': filas},
        {'titulo': 'Estado de situación (hace un año, mes anterior y actual)', 'columnas': ['Rubro', *cab_s],
         'filas': filas_s}], por_seccion=True, textos=[('periodo', 'Hasta (AAAAMM)', hasta)],
        botones=[('Tablero financiero', reverse('finanzas:tablero_financiero'), 'bi-speedometer2')],
        ayuda='Vertical: % sobre las ventas del mes (resultados) o sobre el activo total (situación). Horizontal: '
              'variación con la columna anterior. Para el directorio: Excel por tabla o Imprimir › Guardar como PDF.')
