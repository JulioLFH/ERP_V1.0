"""Consulta del historial del sistema anterior (solo lectura, con filtros y Excel)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, render

from core.utils import excel_response

from .models import AsientoAnterior, MovimientoAnterior, OrdenFabricacionAnterior, PosicionPresupuestaria

D0 = Decimal('0')
MAX_EXCEL = 60000


def _fecha(request, campo):
    from datetime import date
    try:
        return date.fromisoformat(request.GET.get(campo) or '')
    except ValueError:
        return None


def _periodo(request, campo):
    valor = (request.GET.get(campo) or '').replace('-', '')
    return valor if len(valor) == 6 and valor.isdigit() else ''


def _excel_grande(request, qs, nombre, titulo, encabezados, fila):
    if qs.count() > MAX_EXCEL:
        messages.warning(request, f'Son {qs.count():,} filas: filtre para exportar hasta {MAX_EXCEL:,}.')
        return None
    return excel_response(nombre, titulo, encabezados, [fila(o) for o in qs.iterator(chunk_size=5000)])


@login_required
def kardex(request):
    qs = MovimientoAnterior.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        # código o documento exacto (con índice: inmediato); si no, por nombre del producto
        if MovimientoAnterior.objects.filter(codigo=q.upper()).exists():
            qs = qs.filter(codigo=q.upper())
        elif MovimientoAnterior.objects.filter(documento=q).exists():
            qs = qs.filter(documento=q)
        else:
            qs = qs.filter(Q(descripcion__icontains=q) | Q(lote=q) | Q(comprobante=q))
    for campo in ('almacen', 'transaccion'):
        if request.GET.get(campo):
            qs = qs.filter(**{campo: request.GET[campo]})
    desde, hasta = _fecha(request, 'desde'), _fecha(request, 'hasta')
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    if request.GET.get('formato') == 'excel':
        resp = _excel_grande(request, qs.order_by('fecha', 'id_origen'), 'Kardex_sistema_anterior',
                             'KARDEX DEL SISTEMA ANTERIOR',
                             ['Fecha', 'Almacén', 'Transacción', 'Documento', 'O. fabricación', 'O. compra', 'Guía',
                              'Comprobante', 'Código', 'Producto', 'Unidad', 'Lote', 'Vence', 'Ingreso', 'Salida',
                              'Costo unit.', 'Doc. contacto', 'Contacto', 'Usuario'],
                             lambda m: [m.fecha, m.almacen, m.transaccion, m.documento, m.orden_fabricacion,
                                        m.orden_compra, m.guia, m.comprobante, m.codigo, m.descripcion, m.unidad,
                                        m.lote, m.vencimiento, m.ingreso, m.salida, m.costo, m.contacto_doc,
                                        m.contacto, m.usuario])
        if resp:
            return resp
    totales = qs.aggregate(i=Sum('ingreso'), s=Sum('salida')) if q or desde or hasta or request.GET.get('almacen') \
        else {'i': None, 's': None}
    return render(request, 'historial/kardex.html', {
        'page_obj': Paginator(qs.order_by('-fecha', '-id_origen'), 100).get_page(request.GET.get('page')),
        'q': q, 'totales': totales, **_listas_kardex()})


def _listas_kardex():
    """Almacenes y transacciones para los filtros (el historial no cambia: se guardan en caché)."""
    from django.core.cache import cache
    listas = cache.get('historial_listas_kardex')
    if listas is None:
        listas = {campo: list(MovimientoAnterior.objects.order_by(campo).values_list(campo, flat=True).distinct())
                  for campo in ('almacen', 'transaccion')}
        cache.set('historial_listas_kardex', listas, 3600)
    return {'almacenes': listas['almacen'], 'transacciones': listas['transaccion']}


@login_required
def asientos(request):
    qs = AsientoAnterior.objects.all()
    desde, hasta = _periodo(request, 'desde'), _periodo(request, 'hasta')
    if desde:
        qs = qs.filter(periodo__gte=desde)
    if hasta:
        qs = qs.filter(periodo__lte=hasta)
    cuenta = request.GET.get('cuenta', '').strip()
    if cuenta:
        qs = qs.filter(cuenta__startswith=cuenta)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(voucher=q) | Q(comprobante__icontains=q) | Q(contacto__icontains=q) |
                       Q(contacto_doc=q) | Q(glosa__icontains=q))
    if request.GET.get('diario'):
        qs = qs.filter(diario=request.GET['diario'])
    if request.GET.get('formato') == 'excel':
        resp = _excel_grande(request, qs.order_by('fecha', 'voucher', 'id'), 'Asientos_sistema_anterior',
                             'ASIENTOS DEL SISTEMA ANTERIOR',
                             ['Fecha', 'Periodo', 'Diario', 'Voucher', 'Cuenta', 'Denominación', 'Debe', 'Haber',
                              'Doc. contacto', 'Contacto', 'Tipo comprobante', 'Comprobante', 'Glosa',
                              'Centro de costo', 'Usuario'],
                             lambda a: [a.fecha, a.periodo, a.diario, a.voucher, a.cuenta, a.cuenta_nombre, a.debe,
                                        a.haber, a.contacto_doc, a.contacto, a.tipo_comprobante, a.comprobante,
                                        a.glosa, a.centro_costo, a.usuario])
        if resp:
            return resp
    filtrado = bool(desde or hasta or cuenta or q or request.GET.get('diario'))
    totales = qs.aggregate(d=Sum('debe'), h=Sum('haber')) if filtrado else {'d': None, 'h': None}
    return render(request, 'historial/asientos.html', {
        'page_obj': Paginator(qs.order_by('fecha', 'voucher', 'id'), 100).get_page(request.GET.get('page')),
        'q': q, 'cuenta': cuenta, 'desde': desde, 'hasta': hasta, 'totales': totales,
        'diarios': AsientoAnterior.objects.order_by('diario').values_list('diario', flat=True).distinct()})


@login_required
def balance(request):
    """Balance de comprobación del sistema anterior por cuenta y rango de periodos."""
    qs = AsientoAnterior.objects.all()
    desde, hasta = _periodo(request, 'desde'), _periodo(request, 'hasta')
    if desde:
        qs = qs.filter(periodo__gte=desde)
    if hasta:
        qs = qs.filter(periodo__lte=hasta)
    nivel = int(request.GET.get('nivel') or 0)
    filas = {}
    for r in qs.values('cuenta', 'cuenta_nombre').annotate(d=Sum('debe'), h=Sum('haber')).order_by('cuenta'):
        clave = r['cuenta'][:nivel] if nivel else r['cuenta']
        f = filas.setdefault(clave, {'cuenta': clave, 'nombre': r['cuenta_nombre'] if not nivel else '',
                                     'd': D0, 'h': D0})
        f['d'] += r['d'] or D0
        f['h'] += r['h'] or D0
    if nivel:
        from contabilidad.models import CuentaContable
        nombres = dict(CuentaContable.objects.filter(codigo__in=list(filas)).values_list('codigo', 'nombre'))
        for f in filas.values():
            f['nombre'] = nombres.get(f['cuenta'], '')
    lista = list(filas.values())
    for f in lista:
        saldo = f['d'] - f['h']
        f['sd'], f['sa'] = max(saldo, D0), max(-saldo, D0)
    tot = {k: sum((f[k] for f in lista), D0) for k in ('d', 'h', 'sd', 'sa')}
    if request.GET.get('formato') == 'excel':
        return excel_response('Balance_sistema_anterior', f'BALANCE DEL SISTEMA ANTERIOR {desde}-{hasta}',
                              ['Cuenta', 'Denominación', 'Debe', 'Haber', 'Saldo deudor', 'Saldo acreedor'],
                              [[f['cuenta'], f['nombre'], f['d'], f['h'], f['sd'], f['sa']] for f in lista] +
                              [['', 'TOTALES', tot['d'], tot['h'], tot['sd'], tot['sa']]])
    return render(request, 'historial/balance.html', {'filas': lista, 'tot': tot, 'desde': desde, 'hasta': hasta,
                                                      'nivel': nivel})


@login_required
def fabricacion(request):
    qs = OrdenFabricacionAnterior.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(referencia__icontains=q) | Q(codigo__iexact=q) | Q(descripcion__icontains=q) |
                       Q(lote__icontains=q))
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    desde, hasta = _fecha(request, 'desde'), _fecha(request, 'hasta')
    if desde:
        qs = qs.filter(inicio__date__gte=desde)
    if hasta:
        qs = qs.filter(inicio__date__lte=hasta)
    if request.GET.get('formato') == 'excel':
        resp = _excel_grande(request, qs, 'Ordenes_fabricacion_sistema_anterior',
                             'ÓRDENES DE FABRICACIÓN DEL SISTEMA ANTERIOR',
                             ['Referencia', 'Código', 'Producto', 'Lista de materiales', 'Lote', 'Inicio', 'Fin',
                              'A producir', 'Producida', 'Unidad', 'Estado', 'Responsable', 'Almacén',
                              'Materia prima S/', 'Mano de obra S/', 'Gasto indirecto S/'],
                             lambda o: [o.referencia, o.codigo, o.descripcion, o.lista_materiales, o.lote,
                                        o.inicio.replace(tzinfo=None) if o.inicio else None,
                                        o.fin.replace(tzinfo=None) if o.fin else None, o.cantidad, o.producida,
                                        o.unidad, o.estado, o.responsable, o.almacen, o.costo_materiales,
                                        o.costo_mano_obra, o.costo_indirecto])
        if resp:
            return resp
    return render(request, 'historial/fabricacion.html', {
        'page_obj': Paginator(qs, 100).get_page(request.GET.get('page')), 'q': q,
        'estados': OrdenFabricacionAnterior.objects.order_by('estado').values_list('estado', flat=True).distinct()})


@login_required
def orden(request, pk):
    o = get_object_or_404(OrdenFabricacionAnterior, pk=pk)
    movimientos = MovimientoAnterior.objects.filter(Q(orden_fabricacion=o.referencia) | Q(documento=o.referencia)) \
        .order_by('fecha', 'id_origen')[:500]
    return render(request, 'historial/orden.html', {'o': o, 'consumos': o.consumos.all(), 'movimientos': movimientos})


@login_required
def posiciones(request):
    grupos = {}
    for p in PosicionPresupuestaria.objects.all():
        grupos.setdefault(p.nombre, []).append(p)
    if request.GET.get('formato') == 'excel':
        return excel_response('Posiciones_presupuestarias', 'POSICIONES PRESUPUESTARIAS',
                              ['Posición', 'Cuenta', 'Denominación'],
                              [[p.nombre, p.cuenta, p.cuenta_nombre] for ps in grupos.values() for p in ps])
    return render(request, 'historial/posiciones.html', {'grupos': grupos})
