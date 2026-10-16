"""Rendición de gastos: tarjetas corporativas, proyectos y sitios, integraciones y la planilla de movilidad."""
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.permisos import puede
from core.utils import a_fecha, excel_response, fmt_fecha

from . import rendiciones as rd
from .forms_rendicion import ConfigRendicionForm, ProyectoForm, SitioForm, TarjetaForm
from .models import ConfigRendicion, Cuenta, Proyecto, Sitio, TarjetaCorporativa
from .views_rendicion import ERRORES, _exigir


# ---------------------------------------------------------------- planilla de movilidad
@login_required
def movilidad(request, pk):
    """Planilla de gastos de movilidad (sustento sin comprobante): por día, con el tope de 4% de la RMV y la firma
    del trabajador."""
    r = get_object_or_404(rd.visibles(request.user).select_related('entrega__responsable'), pk=pk)
    dias = OrderedDict()
    for l in r.lineas.filter(tipo_comprobante='PM').exclude(estado='RECHAZADA').order_by('fecha', 'id'):
        dias.setdefault(l.fecha, []).append(l)
    filas = []
    for fecha, lineas in dias.items():
        total = sum((l.importe for l in lineas), Decimal('0'))
        tope = rd.tope_movilidad(fecha)
        filas.append({'fecha': fecha, 'lineas': lineas, 'total': total, 'tope': tope,
                      'excede': bool(tope and total > tope)})
    from planillas.models import Trabajador
    t = Trabajador.objects.filter(numero_doc=r.entrega.responsable.numero_doc).first()
    return render(request, 'finanzas/rendicion_movilidad.html', {
        'r': r, 'filas': filas, 'total': sum((f['total'] for f in filas), Decimal('0')), 'trabajador': t})


# ---------------------------------------------------------------- tarjetas corporativas
@login_required
def tarjetas(request):
    return render(request, 'finanzas/rendicion_tarjetas.html', {
        'tarjetas': TarjetaCorporativa.objects.select_related('titular', 'entrega', 'cuenta_contable'),
        'pendientes': rd.sin_comprobante()})


@login_required
def tarjeta_editar(request, pk=None):
    t = get_object_or_404(TarjetaCorporativa, pk=pk) if pk else None
    form = TarjetaForm(request.POST or None, instance=t)
    if request.method == 'POST' and form.is_valid():
        t = rd.crear_tarjeta(form.save(commit=False), request.user)
        messages.success(request, f'Tarjeta {t} guardada.')
        return redirect('rendiciones:tarjeta', t.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Editar tarjeta' if t else 'Nueva tarjeta corporativa'})


@login_required
def tarjeta(request, pk):
    t = get_object_or_404(TarjetaCorporativa.objects.select_related('titular', 'entrega'), pk=pk)
    if request.method == 'POST':
        try:
            _exigir(request, 'tesoreria')
            if request.POST.get('accion') == 'importar':
                if not request.FILES.get('archivo'):
                    raise rd.ErrorRendicion('Adjunte el estado de cuenta (Excel o CSV).')
                r, n = rd.importar_estado_cuenta(t, request.FILES['archivo'], request.user)
                if r is None:
                    messages.info(request, 'Todos los cargos del archivo ya estaban importados.')
                else:
                    messages.success(request, f'{n} cargo(s) importados en {r.numero}: el titular debe sustentarlos '
                                              f'con su comprobante.')
            elif request.POST.get('accion') == 'pagar':
                cuenta = get_object_or_404(Cuenta, pk=request.POST.get('cuenta'))
                monto = Decimal((request.POST.get('monto') or '0').replace(',', ''))
                fecha = a_fecha(request.POST['fecha']) if request.POST.get('fecha') else date.today()
                m = rd.pagar_tarjeta(t, cuenta, monto, fecha, request.user, request.POST.get('numero_operacion', ''))
                messages.success(request, f'Pago de la tarjeta registrado ({m.voucher}).')
        except (*ERRORES, ValueError, ArithmeticError) as exc:
            messages.error(request, str(exc))
        return redirect('rendiciones:tarjeta', pk)
    e = t.entrega
    return render(request, 'finanzas/rendicion_tarjeta.html', {
        't': t, 'e': e, 'deuda': -e.saldo if e else Decimal('0'),
        'movimientos': t.movimientos.select_related('linea__rendicion').order_by('-fecha', '-id')[:300],
        'rendiciones': e.rendiciones.order_by('-fecha', '-id') if e else [],
        'cuentas': Cuenta.objects.filter(activo=True, moneda=t.moneda), 'hoy': date.today(),
        'puede_pagar': puede(request.user, 'rendiciones.tesoreria')})


@login_required
def sin_comprobante(request):
    filas = rd.sin_comprobante()
    if request.GET.get('formato') == 'excel':
        return excel_response('cargos_sin_comprobante', 'Cargos de tarjeta sin comprobante', [
            'Tarjeta', 'Titular', 'Fecha', 'Descripción', 'Referencia', 'Monto', 'Días', 'Tramo', 'Rendición'],
            [[str(f['m'].tarjeta), f['m'].tarjeta.titular.nombre, fmt_fecha(f['m'].fecha), f['m'].descripcion,
              f['m'].referencia, f['m'].monto, f['dias'], f['tramo'],
              f['m'].linea.rendicion.numero if f['m'].linea_id else ''] for f in filas])
    return render(request, 'finanzas/rendicion_sin_comprobante.html', {'filas': filas})


# ---------------------------------------------------------------- proyectos y sitios
@login_required
def proyectos(request):
    return render(request, 'finanzas/rendicion_proyectos.html', {
        'proyectos': Proyecto.objects.select_related('cliente', 'centro_costo').prefetch_related('sitios')})


@login_required
def proyecto_editar(request, pk=None):
    p = get_object_or_404(Proyecto, pk=pk) if pk else None
    form = ProyectoForm(request.POST or None, instance=p)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Proyecto guardado.')
        return redirect('rendiciones:proyectos')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Editar proyecto' if p else 'Nuevo proyecto'})


@login_required
def sitio_editar(request, pk=None):
    s = get_object_or_404(Sitio, pk=pk) if pk else None
    form = SitioForm(request.POST or None, instance=s, initial={'proyecto': request.GET.get('proyecto')})
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Sitio guardado.')
        return redirect('rendiciones:proyectos')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Editar sitio' if s else 'Nuevo sitio'})


# ---------------------------------------------------------------- trazabilidad y errores
def _rango(request):
    from .views_rendicion import _rango as rango
    return rango(request)


@login_required
def trazabilidad(request):
    desde, hasta = _rango(request)
    filas = rd.trazabilidad(desde, hasta)
    if request.GET.get('formato') == 'excel':
        datos = []
        for f in filas:
            for p in f['pasos']:
                h = p['h']
                datos.append([f['r'].numero, f['r'].entrega.responsable.nombre, f['r'].total, h.nivel or '',
                              h.a_display, (h.usuario.get_full_name() or h.usuario.username) if h.usuario else '',
                              timezone.localtime(h.fecha).strftime('%d/%m/%Y %H:%M'), p['horas'],
                              (f['ultimo'].get_full_name() or f['ultimo'].username) if f['ultimo'] else '',
                              f['r'].get_estado_display(), h.comentario])
        return excel_response('trazabilidad_aprobacion', f'Trazabilidad de aprobación {fmt_fecha(desde)} - '
                                                         f'{fmt_fecha(hasta)}',
                              ['Rendición', 'Responsable', 'Total', 'Nivel', 'Paso', 'Aprobador', 'Fecha y hora',
                               'Horas del paso', 'Último aprobador', 'Estado actual', 'Comentario'], datos)
    return render(request, 'finanzas/rendicion_trazabilidad.html', {'filas': filas, 'desde': desde, 'hasta': hasta})


@login_required
def errores(request):
    desde, hasta = _rango(request)
    d = rd.errores_por(desde, hasta)
    if request.GET.get('formato') == 'excel':
        return excel_response('errores_rendiciones', f'Observaciones y rechazos {fmt_fecha(desde)} - {fmt_fecha(hasta)}',
                              ['Fecha', 'Rendición', 'Documento', 'Resultado', 'Motivo', 'Comentario', 'Rendidor',
                               'Área (centro de costo)', 'Revisó'],
                              [[timezone.localtime(o.fecha).strftime('%d/%m/%Y %H:%M'), o.rendicion.numero, o.documento,
                                o.estado.capitalize(), o.motivo.nombre, o.comentario,
                                o.rendidor.username if o.rendidor else '',
                                o.centro_costo.codigo if o.centro_costo else '', o.usuario.username if o.usuario else '']
                               for o in d['filas']])
    return render(request, 'finanzas/rendicion_errores.html', {'d': d, 'desde': desde, 'hasta': hasta})


# ---------------------------------------------------------------- integración con un ERP externo
@login_required
def integracion(request):
    from . import integracion_erp as erp
    from .models import EnvioERP
    if request.method == 'POST':
        try:
            _exigir(request, 'contabilizar')
            accion = request.POST.get('accion')
            if accion in ('generar', 'reintentar'):
                envio = erp.generar(request.user, solo_errores=accion == 'reintentar')
                messages.success(request, f'Lote {envio.numero} generado con {envio.detalles.count()} gasto(s): '
                                          f'descárguelo y cárguelo en el ERP externo.')
            elif accion == 'respuesta':
                envio = get_object_or_404(EnvioERP, pk=request.POST.get('envio'))
                if not request.FILES.get('archivo'):
                    raise rd.ErrorRendicion('Adjunte el archivo de respuesta del ERP externo.')
                ok, mal, otros = erp.registrar_respuesta(envio, request.FILES['archivo'])
                messages.success(request, f'{envio.numero}: {ok} aceptado(s), {mal} con error'
                                          f'{f", {otros} fila(s) no reconocidas" if otros else ""}.')
        except (*ERRORES, erp.ErrorIntegracion) as exc:
            messages.error(request, str(exc))
        return redirect('rendiciones:integracion')
    if request.GET.get('descargar'):
        envio = get_object_or_404(EnvioERP, pk=request.GET['descargar'])
        url = lambda s: request.build_absolute_uri(reverse('sustento_ver', args=[s.pk]))
        resp = HttpResponse(erp.csv_de(envio, url), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{envio.numero}.csv"'
        return resp
    return render(request, 'finanzas/rendicion_integracion.html', {
        'envios': EnvioERP.objects.prefetch_related('detalles')[:30], 'pendientes': erp.por_exportar().count(),
        'conc': erp.conciliacion(), 'columnas': erp.COLUMNAS})


# ---------------------------------------------------------------- integraciones
@login_required
def integraciones(request):
    cfg = ConfigRendicion.actual()
    form = ConfigRendicionForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Integraciones guardadas.')
        return redirect('rendiciones:integraciones')
    webhook = request.build_absolute_uri('/rendiciones/whatsapp/')
    from erp.empresas import actual, es_multiempresa
    if es_multiempresa() and actual():
        webhook += f'?empresa={actual()}'
    from core import almacen_objetos, sunat_consulta
    grupos = {g: [form[c] for c in form.fields if c.startswith(g)] for g in ('ia', 'whatsapp', 'correo', 'dia')}
    return render(request, 'finanzas/rendicion_integraciones.html', {
        'form': form, 'grupos': grupos, 'webhook': webhook, 'objetos': almacen_objetos.configurado(),
        'sunat': sunat_consulta.configurada()})
