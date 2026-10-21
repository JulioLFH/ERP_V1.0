"""Módulo «Mi empresa» del cliente del estudio contable (n.º 54): sube sus comprobantes y estados de cuenta y consulta
sus reportes (ventas, compras, IGV, estado de resultados y saldos por cobrar y pagar). Solo ve su empresa (cada
empresa es su propia base); el contador ve los mismos documentos desde Contabilidad y los marca procesados u
observados."""
from collections import OrderedDict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import DocumentoCliente

D0 = Decimal('0')


def _es_contador(user):
    from .modulos import modulos_del_usuario
    return user.is_superuser or 'contabilidad' in modulos_del_usuario(user)


def _periodo(texto, defecto):
    texto = (texto or '').replace('-', '')[:6]
    return texto if len(texto) == 6 and texto.isdigit() else defecto


@login_required
def documentos(request):
    contador = _es_contador(request.user)
    if request.method == 'POST' and contador:
        d = get_object_or_404(DocumentoCliente, pk=request.POST.get('id'))
        estado = request.POST.get('estado')
        if estado in ('PROCESADO', 'OBSERVADO', 'RECIBIDO'):
            d.estado, d.revisado_por = estado, request.user
            d.comentario = (request.POST.get('comentario') or '').strip()[:300] or d.comentario
            d.save(update_fields=['estado', 'revisado_por', 'comentario'])
            messages.success(request, f'{d}: {d.get_estado_display().lower()}.')
        return redirect(request.get_full_path())
    qs = DocumentoCliente.objects.select_related('archivo', 'subido_por')
    filtros = {k: request.GET.get(k, '') for k in ('estado', 'tipo', 'periodo')}
    if filtros['estado']:
        qs = qs.filter(estado=filtros['estado'])
    if filtros['tipo']:
        qs = qs.filter(tipo=filtros['tipo'])
    if filtros['periodo']:
        qs = qs.filter(periodo=filtros['periodo'].replace('-', ''))
    return render(request, 'core/cliente_documentos.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'contador': contador, 'f': filtros,
        'tipos': DocumentoCliente.TIPOS, 'estados': DocumentoCliente.ESTADOS,
        'por_revisar': DocumentoCliente.objects.filter(estado='RECIBIDO').count()})


@login_required
def archivo(request, pk):
    from django.http import HttpResponse
    d = get_object_or_404(DocumentoCliente.objects.select_related('archivo'), pk=pk)
    resp = HttpResponse(d.archivo.contenido, content_type=d.archivo.tipo or 'application/octet-stream')
    resp['Content-Disposition'] = f'attachment; filename="{d.archivo.nombre}"'
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


@login_required
def subir(request):
    from .sustentos import ErrorSustento, guardar_archivo, validar_archivo
    hoy = timezone.localdate()
    if request.method == 'POST':
        archivos = request.FILES.getlist('archivos')
        tipo = request.POST.get('tipo') if request.POST.get('tipo') in dict(DocumentoCliente.TIPOS) else 'OTRO'
        periodo = _periodo(request.POST.get('periodo'), hoy.strftime('%Y%m'))
        descripcion = (request.POST.get('descripcion') or '').strip()[:200]
        if not archivos:
            messages.error(request, 'Elija al menos un archivo (PDF, XML, Excel o imagen).')
        else:
            subidos, errores = 0, []
            for a in archivos[:30]:
                try:
                    validar_archivo(a)
                except ErrorSustento as exc:
                    errores.append(f'{a.name}: {exc}')
                    continue
                DocumentoCliente.objects.create(tipo=tipo, periodo=periodo, descripcion=descripcion or a.name[:200],
                                                archivo=guardar_archivo(a, request.user), subido_por=request.user)
                subidos += 1
            for e in errores:
                messages.error(request, e)
            if subidos:
                messages.success(request, f'{subidos} documento(s) enviados a su contador.')
                return redirect('cliente_documentos')
    return render(request, 'core/cliente_subir.html', {'tipos': DocumentoCliente.TIPOS,
                                                       'periodo': hoy.strftime('%Y-%m')})


def _por_mes(modelo, desde, hasta):
    """{periodo: (base S/, igv S/, total S/, n)} sin anulados (las notas de crédito restan)."""
    datos = OrderedDict()
    qs = (modelo.objects.filter(periodo__gte=desde, periodo__lte=hasta, es_saldo_inicial=False)
          .exclude(estado='ANULADO'))
    for signo, filtro in ((1, qs.exclude(tipo_comprobante='07')), (-1, qs.filter(tipo_comprobante='07'))):
        for f in filtro.values('periodo').annotate(b=Sum('base_pen'), n=Sum('nograv_pen'), i=Sum('igv_pen'),
                                                   t=Sum('total_pen')):
            b, i, t, c = datos.get(f['periodo'], (D0, D0, D0, 0))
            datos[f['periodo']] = (b + signo * ((f['b'] or D0) + (f['n'] or D0)), i + signo * (f['i'] or D0),
                                   t + signo * (f['t'] or D0), c)
    return datos


@login_required
def reportes(request):
    from compras.models import Compra
    from ventas.models import Venta
    hoy = timezone.localdate()
    hasta = _periodo(request.GET.get('hasta'), hoy.strftime('%Y%m'))
    desde = _periodo(request.GET.get('desde'), f'{hasta[:4]}01')
    ventas, compras = _por_mes(Venta, desde, hasta), _por_mes(Compra, desde, hasta)
    meses = sorted(set(ventas) | set(compras))
    filas = []
    for p in meses:
        v, c = ventas.get(p, (D0, D0, D0, 0)), compras.get(p, (D0, D0, D0, 0))
        filas.append({'periodo': f'{p[4:]}/{p[:4]}', 'ventas': v[0], 'igv_ventas': v[1], 'compras': c[0],
                      'igv_compras': c[1], 'igv_neto': v[1] - c[1]})
    totales = {k: sum((f[k] for f in filas), D0) for k in ('ventas', 'igv_ventas', 'compras', 'igv_compras',
                                                         'igv_neto')}
    resultados, utilidad = [], None
    try:
        from contabilidad import reportes as rep
        resultados, utilidad = rep.estado_resultados(desde, hasta)
    except Exception:  # sin contabilidad generada todavía
        pass
    por_cobrar = sum((d.saldo_pen for d in Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO')
                      .exclude(tipo_comprobante='07') if d.saldo > 0), D0)
    por_pagar = sum((d.saldo_pen for d in Compra.objects.con_saldos().cobrables().filter(estado='REGISTRADO')
                     .exclude(tipo_comprobante='07') if d.saldo > 0), D0)
    return render(request, 'core/cliente_reportes.html', {
        'filas': filas, 'totales': totales, 'resultados': resultados, 'utilidad': utilidad,
        'desde': f'{desde[:4]}-{desde[4:]}', 'hasta': f'{hasta[:4]}-{hasta[4:]}', 'por_cobrar': por_cobrar,
        'por_pagar': por_pagar})
