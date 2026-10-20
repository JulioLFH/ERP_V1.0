"""Pantallas de inventario físico (documentos, conteo a ciegas también desde el celular, diferencias, aprobación y
contabilización, indicadores) y de los reportes masivos de inventario."""
import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Almacen
from core.modulos import puede_ver_costos
from core.permisos import puede
from core.utils import excel_response

from . import fisico as srv
from . import reportes_masivos as rm
from .models import ConteoCiclico, ConteoItem


def _dec(valor):
    try:
        return Decimal(str(valor).replace(',', '').strip()) if str(valor or '').strip() != '' else None
    except (InvalidOperation, ValueError):
        return None


def _fecha(valor, defecto):
    try:
        return date.fromisoformat(valor) if valor else defecto
    except ValueError:
        return defecto


# ---------------------------------------------------------------- documentos de inventario físico
@login_required
def lista(request):
    qs = ConteoCiclico.objects.select_related('almacen', 'contador', 'creado_por')
    if request.GET.get('almacen'):
        qs = qs.filter(almacen_id=request.GET['almacen'])
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    if request.GET.get('tipo'):
        qs = qs.filter(tipo=request.GET['tipo'])
    return render(request, 'inventario/fisico_lista.html', {
        'docs': qs[:300], 'almacenes': Almacen.objects.filter(activo=True), 'estados': ConteoCiclico.ESTADOS,
        'tipos': ConteoCiclico.TIPOS})


@login_required
def nuevo(request):
    almacenes = Almacen.objects.filter(activo=True)
    usuarios = get_user_model().objects.filter(is_active=True).order_by('username')
    if request.method == 'POST':
        almacen = get_object_or_404(Almacen, pk=request.POST.get('almacen'))
        contador = usuarios.filter(pk=request.POST.get('contador') or 0).first()
        try:
            n = max(1, min(500, int(request.POST.get('n') or 20)))
            maximo = max(1, min(500, int(request.POST.get('maximo') or 40)))
        except ValueError:
            n, maximo = 20, 40
        try:
            doc = srv.crear(
                almacen, request.POST.get('tipo', 'GENERAL'), request.user, contador=contador,
                bloquear=bool(request.POST.get('bloquear')), n=n, semilla=request.POST.get('semilla', '').strip(),
                ubicacion=request.POST.get('ubicacion', '').strip(), maximo=maximo,
                clases=[c for c in request.POST.getlist('clase') if c in 'ABC'] or ('A', 'B', 'C'),
                tolerancia_pct=_dec(request.POST.get('tolerancia_pct')),
                tolerancia_valor=_dec(request.POST.get('tolerancia_valor')))
            messages.success(request, f'{doc} creado con {doc.items.count()} línea(s). Foto del stock tomada.')
            return redirect('inventario:fisico', doc.pk)
        except srv.ErrorFisico as exc:
            messages.error(request, str(exc))
    return render(request, 'inventario/fisico_nuevo.html', {
        'almacenes': almacenes, 'usuarios': usuarios, 'tipos': ConteoCiclico.TIPOS,
        'almacen_id': request.GET.get('almacen', '')})


def _ciego(request, doc):
    """El contador no ve el stock del sistema mientras cuenta (conteo ciego)."""
    if request.GET.get('ciego'):
        return True
    return doc.estado == 'ABIERTO' and doc.contador_id == request.user.pk


@login_required
def detalle(request, pk):
    doc = get_object_or_404(ConteoCiclico.objects.select_related(
        'almacen', 'contador', 'creado_por', 'aprobado_por', 'cerrado_por', 'ajuste_ingreso', 'ajuste_salida'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion in ('contar', 'recontar'):
                if not puede(request.user, 'inventario.operar'):
                    raise srv.ErrorFisico('Su usuario no registra conteos.')
                items = doc.items.all()
                cantidades = {i.pk: _dec(request.POST.get(f'c_{i.pk}')) for i in items
                              if str(request.POST.get(f'c_{i.pk}', '')).strip()}
                motivos = {i.pk: request.POST.get(f'm_{i.pk}') for i in items if request.POST.get(f'm_{i.pk}')}
                n = srv.registrar(doc, cantidades, request.user, reconteo=accion == 'recontar', motivos=motivos)
                messages.success(request, f'{n} línea(s) registradas.')
            elif accion == 'aprobar':
                srv.aprobar(doc, request.user)
                messages.success(request, f'{doc} aprobado: ya se puede contabilizar.')
            elif accion == 'contabilizar':
                if not puede(request.user, 'inventario.ajustar'):
                    raise srv.ErrorFisico('Su usuario no registra ajustes de inventario.')
                srv.contabilizar(doc, request.user)
                messages.success(request, f'{doc} contabilizado: ajustes registrados con el acta como sustento.')
            elif accion == 'anular':
                srv.anular(doc, request.user)
                messages.success(request, f'{doc} anulado.')
        except srv.ErrorFisico as exc:
            messages.error(request, str(exc))
        destino = request.POST.get('volver', '')
        if destino.startswith('/') and not destino.startswith('//'):
            return redirect(destino)
        return redirect('inventario:fisico', pk)
    if request.GET.get('formato') == 'acta':
        return srv.acta(doc)
    ciego = _ciego(request, doc)
    if request.GET.get('formato') == 'hoja':  # hoja de conteo para imprimir (sin el stock del sistema)
        return excel_response(f'Hoja_{doc.numero}', f'HOJA DE CONTEO {doc.numero} - {doc.almacen} (conteo a ciegas)',
                              ['Ubicación', 'Código', 'Producto', 'Lote', 'U.M.', 'Contado', 'Firma'],
                              [[i.ubicacion, i.producto.codigo, i.producto.nombre, i.lote.codigo if i.lote_id else '',
                                i.producto.unidad, '', ''] for i in doc.items.select_related('producto', 'lote')])
    r = srv.resumen(doc)
    ok, motivo = srv.puede_aprobar(request.user, doc)
    return render(request, 'inventario/fisico.html', {
        'doc': doc, 'r': r, 'ciego': ciego, 'ver_costos': puede_ver_costos(request.user) and not ciego,
        'puede_aprobar': ok, 'motivo_aprobar': motivo, 'motivos': ConteoItem.MOTIVOS[1:],
        'puede_contar': puede(request.user, 'inventario.operar'),
        'editable': puede(request.user, 'inventario.operar') and doc.estado in ('ABIERTO', 'CONTADO'),
        'puede_ajustar': puede(request.user, 'inventario.ajustar')})


@login_required
def contar_movil(request, pk):
    """Conteo desde el celular: una tarjeta por línea en orden de ubicación, a ciegas, con lector de código de
    barras (el campo de búsqueda recibe el código y salta a la línea)."""
    doc = get_object_or_404(ConteoCiclico.objects.select_related('almacen'), pk=pk)
    items = list(doc.items.select_related('producto', 'lote'))
    return render(request, 'inventario/fisico_movil.html', {
        'doc': doc, 'items': items, 'recontar': request.GET.get('recontar') == '1',
        'pendientes': [i for i in items if (i.exige_reconteo if request.GET.get('recontar') == '1'
                                            else i.contado is None)]})


@login_required
def indicadores(request):
    almacenes = Almacen.objects.filter(activo=True)
    almacen = almacenes.filter(pk=request.GET.get('almacen') or 0).first()
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde'), hoy - timedelta(days=90))
    hasta = _fecha(request.GET.get('hasta'), hoy)
    d = srv.indicadores(almacen, desde, hasta)
    if request.GET.get('formato') == 'excel':
        return excel_response('Productos_sin_conteo', 'Productos sin conteo en los últimos 12 meses',
                              ['Código', 'Producto', 'Almacén', 'Stock', 'Último conteo'],
                              [[f['p'].codigo, f['p'].nombre, str(f['almacen']), f['stock'], f['ultimo']]
                               for f in d['nunca']])
    return render(request, 'inventario/fisico_indicadores.html', {'d': d, 'almacenes': almacenes,
                                                                  'almacen': almacen})


# ---------------------------------------------------------------- reportes masivos
REPORTES = {
    'stock_fecha': ('Stock a una fecha', 'MB5B', 'bi-calendar-check',
                    'Saldo por producto y almacén a cualquier fecha, con costo y valor (opcional por lote).'),
    'stock_actual': ('Stock por almacén, lote y ubicación', 'MB52 / MMBE', 'bi-boxes',
                     'Saldo de hoy con lote, vencimiento, ubicación, comprometido (requerimientos y órdenes de '
                     'producción), bloqueado (cuarentena, conteo con bloqueo, vencidos) y disponible.'),
    'kardex': ('Kardex masivo', 'MB51', 'bi-list-columns',
               'Todos los movimientos de un rango de fechas, varios productos y almacenes; un movimiento por fila. '
               'Excel o CSV (el CSV se genera por partes, sin límite de filas).'),
    'valorizacion': ('Valorización a una fecha', 'MB5B / MC.9', 'bi-calculator',
                     'Inventario valorizado a cualquier fecha y cuadre automático con las cuentas 20 a 26.'),
    'rotacion': ('Rotación y stock sin movimiento', 'MC.9 / MC46', 'bi-arrow-repeat',
                 'Rotación, días de inventario y productos sin movimiento en 90, 180 o 365 días.'),
    'negativos': ('Saldos negativos en el kardex', 'Regularización', 'bi-exclamation-triangle',
                  'Productos y almacenes cuyo kardex quedó en negativo en alguna fecha (salidas registradas antes de '
                  'su ingreso): desde cuándo, con qué movimiento, el peor saldo y el de hoy. Para regularizar antes '
                  'del cierre de kardex.'),
}
LIMITE_PANTALLA = 1000
LIMITE_EXCEL = 200000  # más filas: CSV o por correo


@login_required
def reportes(request):
    return render(request, 'inventario/reportes_masivos.html', {'reportes': REPORTES})


@login_required
def reporte(request, clave):
    if clave not in REPORTES:
        return redirect('inventario:reportes_masivos')
    titulo, sap, icono, descripcion = REPORTES[clave]
    costos = puede_ver_costos(request.user)
    if clave in ('valorizacion',) and not costos:
        messages.error(request, 'La valorización requiere el permiso de ver costos.')
        return redirect('inventario:reportes_masivos')
    g = request.GET
    hoy = timezone.localdate()
    almacenes = [int(a) for a in g.getlist('almacen') if a.isdigit()]
    q, familia, ubicacion = g.get('q', '').strip(), g.get('familia', '').strip(), g.get('ubicacion', '').strip()
    por_lote = bool(g.get('lote'))
    fecha = _fecha(g.get('fecha'), hoy)
    desde = _fecha(g.get('desde'), hoy.replace(day=1))
    hasta = _fecha(g.get('hasta'), hoy)
    try:
        dias = int(g.get('dias') or 365)
    except ValueError:
        dias = 365
    sin_mov = int(g['sin_movimiento']) if g.get('sin_movimiento', '').isdigit() else None
    ver = bool(g.get('ver')) or bool(g.get('formato'))
    total, cuadre, encabezados, filas, n_total = None, [], [], [], None
    nombre = f'{clave}_{hoy:%Y%m%d}'
    if ver:
        if clave == 'stock_fecha':
            encabezados, filas, total = rm.stock_a_fecha(fecha, almacenes, q, familia, por_lote, ubicacion)
        elif clave == 'stock_actual':
            encabezados, filas, total = rm.stock_actual(almacenes, q, familia, por_lote, ubicacion)
        elif clave == 'valorizacion':
            encabezados, filas, total, cuadre = rm.valorizacion(fecha, almacenes, q, familia)
        elif clave == 'rotacion':
            encabezados, filas, total = rm.rotacion(almacenes, q, familia, dias, sin_mov)
        elif clave == 'negativos':
            encabezados, filas, total = rm.saldos_negativos(almacenes, q, familia)
        elif clave == 'kardex':
            if g.get('formato') == 'correo':
                if not request.user.email:
                    messages.error(request, 'Su usuario no tiene correo: regístrelo en Ajustes › Usuarios.')
                else:
                    rm.kardex_en_segundo_plano(request.user, desde, hasta, almacenes, q, familia)
                    messages.success(request, f'El kardex se está generando en segundo plano: llegará a '
                                              f'{request.user.email} como CSV comprimido.')
                return redirect(f'{request.path}?{g.urlencode().replace("formato=correo", "").strip("&")}')
            n_mov = n_total = rm.contar_kardex(desde, hasta, almacenes, q, familia)
            if g.get('formato') == 'excel' and n_mov > LIMITE_EXCEL:
                messages.warning(request, f'Son {n_mov:,} movimientos: para más de {LIMITE_EXCEL:,} use el CSV o '
                                          f'«Enviarme por correo».')
                return redirect(f'{request.path}?{g.urlencode().replace("formato=excel", "").strip("&")}')
            if g.get('formato') == 'csv':
                return _csv(f'Kardex_{desde}_{hasta}', rm.ENCABEZADOS_KARDEX,
                            rm.kardex_masivo(desde, hasta, almacenes, q, familia))
            encabezados = rm.ENCABEZADOS_KARDEX
            filas = rm.kardex_masivo(desde, hasta, almacenes, q, familia)
            if g.get('formato') != 'excel':  # en pantalla solo las primeras filas (el total se cuenta aparte)
                import itertools
                filas = list(itertools.islice(filas, LIMITE_PANTALLA))
            else:
                filas = list(filas)
        if not costos:  # sin permiso de costos no se muestran costos ni valores
            quitar = [i for i, e in enumerate(encabezados) if 'Costo' in e or 'S/' in e]
            encabezados = [e for i, e in enumerate(encabezados) if i not in quitar]
            filas = [[v for i, v in enumerate(f) if i not in quitar] for f in filas]
            total = None
        if g.get('formato') == 'excel':
            datos = list(filas)
            if total is not None:
                datos.append(['TOTAL'] + [''] * (len(encabezados) - 2) + [total])
            if cuadre:
                datos += [[], ['CUADRE CON CONTABILIDAD'], ['Cuenta', 'Nombre', 'Kardex S/', 'Contabilidad S/',
                                                           'Diferencia S/']] + cuadre
            return excel_response(nombre, f'{titulo} ({sap})', encabezados, datos)
    return render(request, 'inventario/reporte_masivo.html', {
        'clave': clave, 'titulo': titulo, 'sap': sap, 'icono': icono, 'descripcion': descripcion,
        'encabezados': encabezados, 'filas': filas[:LIMITE_PANTALLA],
        'n_filas': n_total if n_total is not None else len(filas), 'total': total,
        'cuadre': cuadre, 'limite': LIMITE_PANTALLA, 'ver': ver, 'almacenes': Almacen.objects.filter(activo=True),
        'elegidos': almacenes, 'familias': rm.familias(), 'f': {'q': q, 'familia': familia, 'ubicacion': ubicacion,
                                                                'lote': por_lote, 'fecha': fecha, 'desde': desde,
                                                                'hasta': hasta, 'dias': dias,
                                                                'sin_movimiento': sin_mov},
        'opciones_dias': [90, 180, 365]})


class _Eco:
    def write(self, valor):
        return valor


def _csv(nombre, encabezados, filas):
    """CSV por partes (StreamingHttpResponse): no arma todo en memoria, sirve para millones de movimientos."""
    escritor = csv.writer(_Eco(), delimiter=';')

    def generar():
        yield '﻿' + escritor.writerow(encabezados)
        for f in filas:
            yield escritor.writerow(['' if v is None else str(v) for v in f])
    resp = StreamingHttpResponse(generar(), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{nombre}.csv"'
    return resp
