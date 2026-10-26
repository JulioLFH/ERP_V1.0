from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView

from core.modulos import puede_ver_costos
from core.utils import excel_response, rango_por_defecto
from core.views import FormGenerico, ListaGenerica

from . import servicios
from .forms import (CentroTrabajoForm, ComponentesFormSet, HojaRutaForm, ListaMaterialesForm, MotivoParadaForm,
                    OperacionesRutaFormSet, OrdenProduccionForm, PlanDemandaForm, SubproductosFormSet, VersionForm)
from .models import (CambioIngenieria, CentroTrabajo, CorridaMRP, HojaRuta, ListaMateriales, MotivoParada,
                     OrdenProduccion, PlanDemanda, PropuestaMRP, VersionFabricacion)


def _decimal(valor, defecto=None):
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return defecto


# ---------------------------------------------------------------- órdenes de producción
@login_required
def ordenes(request):
    qs = OrdenProduccion.objects.select_related('producto', 'almacen_destino', 'version')
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q))
    desde, hasta = rango_por_defecto(request, qs)
    if not request.GET.get('todo'):
        qs = qs.filter(Q(fecha__range=[desde, hasta]) |
                       Q(estado__in=('BORRADOR', 'CERRADA_TEC') + OrdenProduccion.ABIERTAS))
    from django.db.models import Count
    qs = qs.annotate(n_lotes=Count('declaraciones', filter=Q(declaraciones__anulada=False)))
    pendientes = OrdenProduccion.objects.filter(estado__in=OrdenProduccion.ABIERTAS).count()
    return render(request, 'produccion/ordenes.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q, 'desde': desde, 'hasta': hasta,
        'estados': OrdenProduccion.ESTADOS, 'pendientes': pendientes})


def _guardar_orden(request, orden, titulo):
    form = OrdenProduccionForm(request.POST or None, instance=orden)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            nuevo = not orden.pk
            orden = form.save(commit=False)
            if nuevo:
                orden.creado_por = request.user
            orden.save()
            servicios.explotar(orden)
        messages.success(request, f'Orden guardada en borrador con la versión {orden.version.codigo}: insumos y '
                                  'horas de su receta y hoja de ruta.')
        return redirect('manufactura:orden', orden.pk)
    if not orden.pk and request.GET.get('producto'):
        form.initial['producto'] = request.GET['producto']
        if request.method == 'GET' and request.GET['producto'].isdigit() and not VersionFabricacion.objects.filter(
                producto_id=request.GET['producto'], activa=True).exists():
            from django.urls import reverse
            from django.utils.html import format_html
            lista = ListaMateriales.objects.filter(producto_id=request.GET['producto'], estado='APROBADA').first()
            url = reverse('manufactura:version_nueva') + f'?producto={request.GET["producto"]}' + (
                f'&lista={lista.pk}' if lista else '')
            from core.models import Producto
            producto = Producto.objects.filter(pk=request.GET['producto']).first()
            messages.warning(request, format_html(
                'El producto «{}» no tiene versión de fabricación vigente: por eso no aparece en la lista. '
                '<a href="{}">Crear su versión de fabricación</a> (receta + hoja de ruta).',
                producto.nombre if producto else '', url))
    versiones = {v.pk: v.producto_id for v in VersionFabricacion.objects.filter(activa=True)}
    return render(request, 'produccion/orden_form.html', {'form': form, 'titulo': titulo, 'orden': orden,
                                                         'versiones': versiones})


@login_required
def orden_nueva(request):
    return _guardar_orden(request, OrdenProduccion(), 'Nueva orden de producción')


@login_required
def orden_editar(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if orden.estado != 'BORRADOR':
        messages.error(request, 'Solo se editan órdenes en borrador.')
        return redirect('manufactura:orden', pk)
    return _guardar_orden(request, orden, f'Editar {orden}')


@login_required
def orden_detalle(request, pk):
    orden = get_object_or_404(OrdenProduccion.objects.select_related(
        'producto', 'lista', 'version__hoja', 'almacen_insumos', 'almacen_destino', 'centro_costo', 'creado_por',
        'anulado_por', 'operacion', 'estandar'), pk=pk)
    filas = servicios.disponibilidad(orden)
    from .avanzado import resumen_avance
    avance = resumen_avance(orden)
    abierta = orden.estado in OrdenProduccion.ABIERTAS
    horas = list(orden.horas.select_related('centro').prefetch_related('personal__trabajador'))
    for h in horas:  # MAN-18: horas del saldo por producir (propuesta de la declaración del turno)
        h.horas_saldo = ((h.horas_plan * (orden.cantidad - orden.cantidad_producida) / orden.cantidad)
                         .quantize(Decimal('0.01')) if orden.cantidad else h.horas_plan)
    trabajadores = []
    if orden.estado in ('CONFIRMADA', 'EN_PROCESO') and horas:
        # lo que la planta declaró en la tablet (trabajador, rol y horas) o, si no, la cuadrilla prevista con las
        # horas máquina; más una fila libre para quien se sume
        from .models import AvancePersonal
        declarado = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        for p in AvancePersonal.objects.filter(avance__orden=orden, avance__hora__isnull=False).values(
                'avance__hora_id', 'trabajador_id', 'rol', 'horas'):
            declarado[p['avance__hora_id']][(p['trabajador_id'], p['rol'])] += p['horas']
        for h in horas:
            if declarado.get(h.pk):
                h.filas_personal = [{'rol': rol, 'trabajador': t, 'horas': hh}
                                    for (t, rol), hh in declarado[h.pk].items()]
            else:
                h.filas_personal = ([{'rol': 'MAQUINISTA', 'horas': h.horas_real}] * h.maquinistas +
                                    [{'rol': 'AYUDANTE', 'horas': h.horas_real}] * h.ayudantes)
            h.filas_personal.append({'rol': 'AYUDANTE', 'horas': None})
        from planillas.models import Trabajador
        hoy = timezone.localdate()
        trabajadores = [t for t in Trabajador.objects.select_related('centro_costo').filter(fecha_ingreso__isnull=False).exclude(
            fecha_cese__lt=hoy).order_by('apellido_paterno', 'apellido_materno', 'nombres')]
        planta = [t for t in trabajadores if t.tipo == 'OBRERO' or (t.centro_costo_id and
                                                                     t.centro_costo.tipo == 'PRODUCCION')]
        trabajadores = planta or trabajadores
    if abierta:  # INV-30: lotes libres y vigentes de cada insumo con lote (vacío = por la regla de salida)
        from core.models import StockLote
        from inventario.estados import utilizable
        for f in filas:
            c = f['consumo']
            if c.producto.control:
                f['lotes'] = [sl for sl in StockLote.objects.filter(
                    lote__producto=c.producto, almacen_id=c.almacen_id or orden.almacen_insumos_id, cantidad__gt=0)
                    .select_related('lote').order_by('lote__vencimiento', 'lote__codigo')
                    if utilizable(c.producto, sl.lote, 'PRODUCCION')]
    if abierta and orden.producto.control == 'LOTE':
        from inventario.estados import vencimiento_propuesto
        hoy_ = timezone.localdate()
        orden.vence_propuesto = vencimiento_propuesto(orden.producto, hoy_)
    from core.models import Empresa

    from .consumo import politica
    modo, tol = politica(orden)
    textos = dict(Empresa.POLITICAS_CONSUMO)
    ctx = {'politica': (modo, tol), 'politica_texto': (textos.get(modo, modo).split(':')[0].lower() + (
        f' (± {tol:g} %)' if modo == 'TOLERANCIA' else '')),
           'o': orden, 'filas': filas, 'horas': horas, 'abierta': abierta, 'trabajadores': trabajadores,
           'salidas': list(orden.salidas.select_related('producto')), 'ahora': timezone.localtime(),
           'faltan': any(f['faltante'] > 0 for f in filas) and orden.estado != 'TERMINADA',
           'hoy': timezone.localdate(), 'avance': avance,
           'a_producir': avance['producido'] if avance and avance['producido'] else orden.cantidad}
    # MAN-18 a MAN-21: declaraciones por turno, cierre técnico, liquidación y costos tardíos
    from . import parciales
    from .models import CostoTardio
    ctx['declaraciones'] = list(orden.declaraciones.select_related('ingreso', 'consumo', 'usuario'))
    ctx['puede_declarar'] = abierta and not ctx['salidas'] and not orden.maquilador_id
    # «Terminar» en un paso: solo si no se declaró producción por turnos
    ctx['terminable'] = orden.estado in ('CONFIRMADA', 'EN_PROCESO')
    ctx['saldo'] = max(orden.cantidad - orden.cantidad_producida, Decimal('0'))
    ctx['motivos_faltante'] = OrdenProduccion.MOTIVOS_FALTANTE[1:]
    ctx['costos_tardios'] = list(CostoTardio.objects.filter(Q(orden=orden) | Q(orden_destino=orden))
                                 .select_related('trabajador', 'centro', 'orden', 'orden_destino'))
    ctx['tipos_tardio'] = CostoTardio.TIPOS
    if orden.estado in ('PARCIAL', 'CERRADA_TEC', 'TERMINADA'):
        ctx['puestos'] = list({h.centro for h in horas})
        ctx['trabajadores_tardio'] = trabajadores or _trabajadores_activos()
        ctx['otras_ordenes'] = OrdenProduccion.objects.filter(
            producto=orden.producto, estado__in=OrdenProduccion.ABIERTAS + ('CERRADA_TEC',)).exclude(pk=orden.pk)
    if orden.estado in ('PARCIAL', 'CERRADA_TEC') and puede_ver_costos(request.user):
        materiales, mo, maq, cif = parciales.costo_real(orden)
        estandar = sum((d.valor_estandar for d in ctx['declaraciones'] if not d.anulada), Decimal('0'))
        ctx['acumulado'] = {'materiales': materiales, 'mo': mo, 'maquina': maq, 'cif': cif,
                            'real': materiales + mo + maq + cif, 'estandar': estandar,
                            'variacion': materiales + mo + maq + cif - estandar}
    if orden.estado == 'TERMINADA' and not orden.es_historica and puede_ver_costos(request.user):
        ctx['variaciones'] = servicios.variaciones(orden)
        ctx['por_tipo'] = orden.variaciones.all()
    return render(request, 'produccion/orden_detalle.html', ctx)


def _trabajadores_activos():
    from planillas.models import Trabajador
    return list(Trabajador.objects.filter(fecha_ingreso__isnull=False).exclude(fecha_cese__lt=timezone.localdate())
                .order_by('apellido_paterno', 'apellido_materno', 'nombres'))


def _personal(request, orden):
    """Horas hombre del formulario: filas hh_<operación>_<n>_rol / _trab / _horas (solo las que traen horas)."""
    personal = {}
    for h in orden.horas.all():
        filas, n = [], 0
        while f'hh_{h.pk}_{n}_rol' in request.POST:
            valor = _decimal(request.POST.get(f'hh_{h.pk}_{n}_horas'), None)
            if valor:
                trab = request.POST.get(f'hh_{h.pk}_{n}_trab')
                filas.append({'rol': request.POST.get(f'hh_{h.pk}_{n}_rol'), 'horas': valor,
                              'trabajador': int(trab) if trab and trab.isdigit() else None})
            n += 1
        if n:
            personal[h.pk] = filas
    return personal


def _dia_post(request, nombre):
    from datetime import date as _date
    try:
        return _date.fromisoformat(request.POST.get(nombre) or '')
    except ValueError:
        return None


@login_required
def orden_declarar(request, pk):
    """MAN-18: declarar la producción del turno (la orden queda parcial)."""
    from core.permisos import puede

    from . import parciales
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        horas = {h.pk: _decimal(request.POST.get(f'dhora_{h.pk}'), None) for h in orden.horas.all()
                 if (request.POST.get(f'dhora_{h.pk}') or '').strip()}
        try:
            d = parciales.declarar(
                orden, request.user, _decimal(request.POST.get('cantidad'), None),
                fecha=request.POST.get('fecha') or None, fecha_contable=request.POST.get('fecha_contable') or None,
                lote=request.POST.get('lote', ''), fabricacion=_dia_post(request, 'fabricacion'),
                vencimiento=_dia_post(request, 'vencimiento'), horas=horas, personal=_personal(request, orden),
                nota=request.POST.get('nota', ''),
                lotes_insumos={c.pk: request.POST.get(f'dlote_{c.pk}', '') for c in orden.consumos.all()},
                autorizar=request.POST.get('autorizar') == '1' and puede(request.user, 'manufactura.aprobar_consumo'))
            orden.refresh_from_db()
            messages.success(request, f'Declaración {d.numero}: ingresaron {d.cantidad:,.2f} {orden.producto.unidad}'
                                      f'{f" (lote {d.lote})" if d.lote else ""}. Saldo por producir: '
                                      f'{max(orden.cantidad - orden.cantidad_producida, 0):,.2f}.')
        except servicios.ErrorProduccion as exc:
            messages.error(request, f'No se declaró: {exc}')
    return redirect('manufactura:orden', pk)


@login_required
def declaracion_anular(request, pk):
    from . import parciales
    from .models import DeclaracionOrden
    d = get_object_or_404(DeclaracionOrden.objects.select_related('orden'), pk=pk)
    if request.method == 'POST':
        try:
            parciales.anular_declaracion(d, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'Declaración {d.numero} anulada: salió el lote y volvieron sus materiales.')
        except servicios.ErrorProduccion as exc:
            messages.error(request, str(exc))
    return redirect('manufactura:orden', d.orden_id)


@login_required
def orden_cierre_tecnico(request, pk):
    from . import parciales
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            parciales.cierre_tecnico(orden, request.user, request.POST.get('motivo_faltante', ''),
                                     request.POST.get('detalle', ''), devolver=request.POST.get('devolver') != '0')
            messages.success(request, f'{orden.numero} cerrada técnicamente: ya no admite declaraciones. Liquídela '
                                      'para llevar su variación a los lotes o al costo de ventas.')
        except servicios.ErrorProduccion as exc:
            messages.error(request, f'No se cerró: {exc}')
    return redirect('manufactura:orden', pk)


@login_required
def orden_liquidar(request, pk):
    from . import parciales
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            o = parciales.liquidar_orden(orden, request.user, request.POST.get('fecha') or None)
            messages.success(request, f'{o.numero} liquidada: variación a lotes en stock S/ {o.variacion_inventario:,.2f}, '
                                      f'al costo de ventas S/ {o.variacion_costo_ventas:,.2f}, a gasto '
                                      f'S/ {o.variacion_gasto:,.2f}.')
        except servicios.ErrorProduccion as exc:
            messages.error(request, f'No se liquidó: {exc}')
    return redirect('manufactura:orden', pk)


@login_required
def orden_costo_tardio(request, pk):
    """MAN-21: horas o costos registrados después de producir."""
    from planillas.models import Trabajador

    from . import parciales
    from .models import CentroTrabajo
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            t = parciales.costo_tardio(
                orden, request.user, request.POST.get('tipo', ''), request.POST.get('fecha') or '',
                request.POST.get('motivo', ''), horas=_decimal(request.POST.get('horas'), None),
                importe=_decimal(request.POST.get('importe'), None),
                trabajador=Trabajador.objects.filter(pk=request.POST.get('trabajador') or 0).first(),
                centro=CentroTrabajo.objects.filter(pk=request.POST.get('centro') or 0).first(),
                orden_destino=OrdenProduccion.objects.filter(pk=request.POST.get('orden_destino') or 0).first())
            messages.success(request, f'Costo tardío S/ {t.importe:,.2f} registrado: {t.get_destino_display().lower()}.')
        except servicios.ErrorProduccion as exc:
            messages.error(request, str(exc))
    return redirect('manufactura:orden', pk)


def _accion(request, pk, funcion, exito):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            funcion(orden)
            messages.success(request, exito.format(o=orden))
        except servicios.ErrorProduccion as exc:
            messages.error(request, str(exc))
    return redirect('manufactura:orden', pk)


@login_required
def orden_confirmar(request, pk):
    def confirmar(o):
        if not o.centro_costo_id:  # MAN-17: el costo de la orden (y sus variaciones) se lleva por centro
            raise servicios.ErrorProduccion('Indique el centro de costo de la orden (Editar) antes de confirmarla.')
        servicios.confirmar(o, request.user)
    return _accion(request, pk, confirmar, 'Orden {o.numero} confirmada con su costo estándar fijado.')


@login_required
def orden_maquila(request, pk):
    return _accion(request, pk, lambda o: servicios.enviar_a_maquilador(o, request.user),
                   'Materiales de {o.numero} enviados al maquilador: se consumen de su almacén al terminar la orden.')


@login_required
def orden_iniciar(request, pk):
    return _accion(request, pk, servicios.iniciar, 'Orden {o.numero} en proceso.')


@login_required
def orden_terminar(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        consumos = {c.pk: _decimal(request.POST.get(f'consumo_{c.pk}'), c.cantidad_real) for c in orden.consumos.all()}
        horas = {h.pk: _decimal(request.POST.get(f'hora_{h.pk}'), h.horas_real) for h in orden.horas.all()}
        personal = _personal(request, orden)
        salidas = {s.pk: _decimal(request.POST.get(f'salida_{s.pk}'), s.cantidad_real) for s in orden.salidas.all()}
        cantidad = _decimal(request.POST.get('cantidad_producida'))
        fecha = request.POST.get('fecha') or None
        from datetime import date as _date

        from inventario.cierre import KardexCerrado

        def _dia(nombre):
            try:
                return _date.fromisoformat(request.POST.get(nombre) or '')
            except ValueError:
                return None
        lotes_insumos = {c.pk: request.POST.get(f'lote_{c.pk}', '') for c in orden.consumos.all()}
        try:
            servicios.terminar(orden, request.user, cantidad, consumos, horas, fecha, personal=personal,
                               salidas=salidas, merma_a_gasto=request.POST.get('merma_a_gasto') == '1',
                               fecha_contable=request.POST.get('fecha_contable') or None,
                               lote_producto=request.POST.get('lote_producto', ''), fabricacion=_dia('fabricacion'),
                               vencimiento=_dia('vencimiento'), lotes_insumos=lotes_insumos,
                               motivos={c.pk: request.POST.get(f'motivo_{c.pk}', '') for c in orden.consumos.all()})
            messages.success(request, f'Orden {orden.numero} terminada: ingresaron {orden.cantidad_producida:,.2f} '
                                      f'{orden.producto.unidad} de {orden.producto.nombre} al almacén.')
        except (servicios.ErrorProduccion, KardexCerrado) as exc:
            messages.error(request, f'No se pudo terminar: {exc}')
    return redirect('manufactura:orden', pk)


@login_required
def orden_material(request, pk):
    """MAN-14: material fuera de la receta en una orden abierta (permiso, motivo y aprobación según la política)."""
    from core.models import Producto

    from . import consumo
    from .models import ConsumoOrden
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            c = consumo.agregar_material(
                orden, Producto.objects.filter(pk=request.POST.get('producto') or 0).first(),
                _decimal(request.POST.get('cantidad'), None), request.POST.get('motivo', ''), request.user,
                sustituye=ConsumoOrden.objects.filter(pk=request.POST.get('sustituye') or 0, orden=orden).first())
            messages.success(request, f'{c.producto.nombre} agregado a {orden.numero}' + (
                '.' if c.aprobacion == 'APROBADO' else ': lo aprueba otro usuario antes de terminar.'))
        except consumo.ErrorConsumo as exc:
            messages.error(request, str(exc))
    return redirect('manufactura:orden', pk)


@login_required
def orden_consumo_resolver(request, pk):
    from . import consumo
    from .models import ConsumoOrden
    c = get_object_or_404(ConsumoOrden.objects.select_related('orden', 'producto'), pk=pk)
    if request.method == 'POST':
        try:
            consumo.resolver(c, request.user, request.POST.get('accion') == 'aprobar', request.POST.get('motivo', ''))
            messages.success(request, f'Consumo de {c.producto.nombre}: '
                                      f'{"aprobado" if request.POST.get("accion") == "aprobar" else "rechazado"}.')
        except consumo.ErrorConsumo as exc:
            messages.error(request, str(exc))
    return redirect('manufactura:orden', c.orden_id)


@login_required
def orden_anular(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        try:
            servicios.anular(orden, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{orden} anulada.' + (' Se revirtió el movimiento de almacén.'
                                                            if orden.operacion_id else ''))
        except servicios.ErrorProduccion as exc:
            messages.error(request, f'No se pudo anular: {exc}')
    return redirect('manufactura:orden', pk)


@login_required
def orden_eliminar(request, pk):
    orden = get_object_or_404(OrdenProduccion, pk=pk)
    if request.method == 'POST':
        if orden.estado != 'BORRADOR':
            messages.error(request, 'Solo se eliminan borradores; las órdenes confirmadas se anulan con motivo.')
            return redirect('manufactura:orden', pk)
        orden.delete()
        messages.success(request, 'Borrador eliminado.')
    return redirect('manufactura:ordenes')


@login_required
def requerimiento(request):
    filas = servicios.requerimientos()
    if request.GET.get('formato') == 'excel':
        datos = [[f['p'].codigo, f['p'].nombre, f['p'].unidad, f['requerido'], f['stock'], f['camino'], f['faltante'],
                  ', '.join(f['ordenes'])] for f in filas]
        return excel_response('Requerimiento_materiales', 'Requerimiento de materiales',
                              ['Código', 'Insumo', 'U.M.', 'Requerido', 'Stock', 'En camino (OC)', 'Por comprar',
                               'Órdenes'], datos)
    return render(request, 'produccion/requerimiento.html', {'filas': filas,
                                                             'faltan': sum(1 for f in filas if f['faltante'] > 0)})


# ---------------------------------------------------------------- listas de materiales
@login_required
def listas(request):
    qs = ListaMateriales.objects.select_related('producto').prefetch_related('componentes', 'versiones')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(producto__nombre__icontains=q) | Q(producto__codigo__icontains=q) | Q(codigo__icontains=q))
    if not request.GET.get('todas'):
        qs = qs.exclude(estado='OBSOLETA')
    return render(request, 'produccion/listas.html', {'listas': qs, 'q': q})


def _en_uso(lista):
    return lista.pk and lista.ordenes.exclude(estado__in=['BORRADOR', 'ANULADA']).exists()


def _guardar_lista(request, lista, titulo):
    form = ListaMaterialesForm(request.POST or None, instance=lista)
    componentes = ComponentesFormSet(request.POST or None, instance=lista, prefix='comp')
    subproductos = SubproductosFormSet(request.POST or None, instance=lista, prefix='sub')
    if request.method == 'POST' and form.is_valid() and componentes.is_valid() and subproductos.is_valid():
        producto = form.cleaned_data['producto']
        ciclo = [f for f in componentes.forms
                 if f.cleaned_data and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('producto') == producto]
        repetido = [f for f in subproductos.forms
                    if f.cleaned_data and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('producto') == producto]
        if ciclo:
            form.add_error(None, f'{producto.nombre} no puede ser insumo de su propia receta.')
        elif repetido:
            form.add_error(None, f'{producto.nombre} es el producto principal: no puede ser también su subproducto.')
        else:
            with transaction.atomic():
                lista = form.save()
                componentes.instance = lista
                componentes.save()
                subproductos.instance = lista
                subproductos.save()
                if not lista.versiones.exists():  # primera receta del producto: su versión de fabricación
                    VersionFabricacion.objects.get_or_create(
                        producto=lista.producto, codigo=lista.codigo[:10],
                        defaults={'lista': lista, 'vigente_desde': lista.vigente_desde})
            messages.success(request, f'Lista de materiales {lista} guardada.')
            return redirect('manufactura:lista', lista.pk)
    from core.models import Producto
    return render(request, 'produccion/lista_form.html', {
        'form': form, 'componentes': componentes, 'subproductos': subproductos, 'titulo': titulo, 'lista': lista,
        'unidades': dict(Producto.objects.filter(activo=True, tipo='BIEN').values_list('pk', 'unidad'))})


@login_required
def lista_nueva(request):
    lista = ListaMateriales()
    if request.GET.get('producto'):
        lista.producto_id = request.GET['producto']
    return _guardar_lista(request, lista, 'Nueva lista de materiales')


@login_required
def lista_editar(request, pk):
    lista = get_object_or_404(ListaMateriales, pk=pk)
    if _en_uso(lista):
        messages.error(request, 'La receta ya se usó en órdenes de producción: no se modifica para conservar el '
                                'sustento del costeo. Cree una nueva versión.')
        return redirect('manufactura:lista', pk)
    return _guardar_lista(request, lista, f'Editar {lista}')


@login_required
def lista_version(request, pk):
    """Copia la receta como nuevo borrador (la anterior sigue vigente hasta aprobar la nueva)."""
    lista = get_object_or_404(ListaMateriales, pk=pk)
    if request.method == 'POST':
        from .avanzado import copiar_lista
        with transaction.atomic():
            nueva = copiar_lista(lista, f'Copia de {lista.codigo}')
        messages.success(request, f'Receta {nueva.codigo} creada en borrador: ajústela, apruébela y úsela en una '
                                  'versión de fabricación.')
        return redirect('manufactura:lista_editar', nueva.pk)
    return redirect('manufactura:lista', pk)


@login_required
def lista_detalle(request, pk):
    lista = get_object_or_404(ListaMateriales.objects.select_related('producto'), pk=pk)
    ctx = {'lista': lista, 'componentes': lista.componentes.select_related('producto', 'almacen'),
           'en_uso': _en_uso(lista), 'versiones_fab': lista.versiones.select_related('hoja'),
           'otras': ListaMateriales.objects.filter(producto=lista.producto).exclude(pk=pk),
           'ordenes': lista.ordenes.select_related('producto')[:20],
           'cambios': CambioIngenieria.objects.filter(Q(lista_actual=lista) | Q(lista_nueva=lista),
                                                      estado__in=['BORRADOR', 'POR_APROBAR'])}
    if puede_ver_costos(request.user):
        ctx['hoja'] = servicios.hoja_costos(lista)
    return render(request, 'produccion/lista_detalle.html', ctx)


# ---------------------------------------------------------------- hojas de ruta
@login_required
def hojas(request):
    qs = HojaRuta.objects.prefetch_related('operaciones__centro', 'versiones__producto')
    if not request.GET.get('todas'):
        qs = qs.exclude(estado='OBSOLETA')
    return render(request, 'produccion/hojas.html', {'hojas': qs})


def _guardar_hoja(request, hoja, titulo):
    form = HojaRutaForm(request.POST or None, instance=hoja)
    operaciones = OperacionesRutaFormSet(request.POST or None, instance=hoja, prefix='oper')
    if request.method == 'POST' and form.is_valid() and operaciones.is_valid():
        with transaction.atomic():
            hoja = form.save()
            operaciones.instance = hoja
            operaciones.save()
        messages.success(request, f'Hoja de ruta {hoja.codigo} guardada.')
        return redirect('manufactura:hojas')
    if not hoja.pk and not operaciones.initial_form_count():
        operaciones.forms[0].initial.setdefault('secuencia', 10)
    return render(request, 'produccion/hoja_form.html', {'form': form, 'operaciones': operaciones,
                                                        'titulo': titulo, 'hoja': hoja})


@login_required
def hoja_nueva(request):
    return _guardar_hoja(request, HojaRuta(), 'Nueva hoja de ruta')


def _hoja_en_uso(hoja):
    return OrdenProduccion.objects.filter(version__hoja=hoja).exclude(estado__in=['BORRADOR', 'ANULADA']).exists()


@login_required
def hoja_editar(request, pk):
    hoja = get_object_or_404(HojaRuta, pk=pk)
    if _hoja_en_uso(hoja):  # sustento del costeo: para cambiarla se crea otra hoja y otra versión
        messages.error(request, 'La hoja de ruta ya se usó en órdenes de producción: no se modifica. Cree otra hoja '
                                'y úsela en una nueva versión de fabricación (esta puede declararse obsoleta).')
        return redirect('manufactura:hojas')
    return _guardar_hoja(request, hoja, f'Editar {hoja}')


@login_required
def obsoleta(request, tipo, pk):
    """Declara obsoleta una hoja de ruta o una receta (deja de usarse en versiones nuevas; se conserva)."""
    modelo = {'hoja': HojaRuta, 'lista': ListaMateriales}[tipo]
    obj = get_object_or_404(modelo, pk=pk)
    if request.method == 'POST':
        obj.estado = 'OBSOLETA'
        obj.save()
        VersionFabricacion.objects.filter(**{tipo: obj}).update(activa=False)
        messages.success(request, f'{obj} declarada obsoleta; sus versiones de fabricación quedaron inactivas.')
    return redirect('manufactura:hojas' if tipo == 'hoja' else 'manufactura:listas')


# ---------------------------------------------------------------- versiones de fabricación
class VersionLista(ListaGenerica):
    model = VersionFabricacion
    titulo = 'Versiones de fabricación'
    columnas = [('Producto', 'producto.nombre'), ('Versión', 'codigo'), ('Receta', 'lista.codigo'),
                ('Hoja de ruta', 'hoja'), ('Lote desde', 'lote_min'), ('Lote hasta', 'lote_max'),
                ('Vigente desde', 'vigente_desde'), ('Hasta', 'vigente_hasta'), ('Activa', 'activa')]
    url_nuevo, url_editar = 'manufactura:version_nueva', 'manufactura:version_editar'
    buscar_en = ['producto__nombre', 'codigo']

    def get_queryset(self):
        return super().get_queryset().select_related('producto', 'lista', 'hoja')


class VersionNueva(FormGenerico, CreateView):
    model, form_class, titulo = VersionFabricacion, VersionForm, 'Nueva versión de fabricación'
    success_url = reverse_lazy('manufactura:versiones')

    def get_initial(self):
        """Desde el aviso de la orden de producción: ?producto=…&lista=… ya elegidos."""
        inicial = super().get_initial()
        for campo in ('producto', 'lista'):
            if self.request.GET.get(campo, '').isdigit():
                inicial[campo] = self.request.GET[campo]
        if 'producto' in inicial:
            inicial.setdefault('codigo', '1')
        return inicial


class VersionEditar(FormGenerico, UpdateView):
    model, form_class, titulo = VersionFabricacion, VersionForm, 'Editar versión de fabricación'
    success_url = reverse_lazy('manufactura:versiones')


# ---------------------------------------------------------------- puestos de trabajo
class CentroLista(ListaGenerica):
    model = CentroTrabajo
    titulo = 'Puestos de trabajo'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Tipo', 'get_tipo_display'),
                ('Mano de obra S/ h hombre', 'costo_hora_mo'), ('Máquina S/ h', 'costo_hora_maquina'),
                ('CIF S/ h', 'costo_hora_cif'),
                ('Capacidad h/día', 'capacidad_dia'), ('Eficiencia %', 'eficiencia'),
                ('Centro de costo', 'centro_costo'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'manufactura:centro_nuevo', 'manufactura:centro_editar'
    buscar_en = ['codigo', 'nombre']


@login_required
def motivos_parada(request):
    """Maestro de motivos de parada (Manufactura › Ingeniería) y configuración del OEE de la empresa."""
    from .forms import ConfiguracionPlantaForm
    from .models import ConfiguracionPlanta
    config = ConfiguracionPlanta.actual()
    form = ConfiguracionPlantaForm(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Configuración del OEE guardada.')
        return redirect('manufactura:motivos_parada')
    from django.db.models import Count
    motivos = MotivoParada.objects.prefetch_related('puestos').annotate(n=Count('paradas')).order_by('perdida',
                                                                                                    'codigo')
    return render(request, 'produccion/motivos_parada.html', {'motivos': motivos, 'form': form})


class MotivoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = MotivoParada, MotivoParadaForm, 'Nuevo motivo de parada'
    success_url = reverse_lazy('manufactura:motivos_parada')


class MotivoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = MotivoParada, MotivoParadaForm, 'Editar motivo de parada'
    success_url = reverse_lazy('manufactura:motivos_parada')


class CentroNuevo(FormGenerico, CreateView):
    model, form_class, titulo = CentroTrabajo, CentroTrabajoForm, 'Nuevo puesto de trabajo'
    success_url = reverse_lazy('manufactura:centros')


class CentroEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CentroTrabajo, CentroTrabajoForm, 'Editar puesto de trabajo'
    success_url = reverse_lazy('manufactura:centros')


@login_required
def capacidad(request):
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde')) or hoy
    hasta = _fecha(request.GET.get('hasta')) or desde + timedelta(days=13)
    return render(request, 'produccion/capacidad.html', {'filas': servicios.carga_capacidad(desde, hasta),
                                                         'desde': desde, 'hasta': hasta})


def _fecha(texto):
    from datetime import date
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


# ---------------------------------------------------------------- planificación (MRP)
@login_required
def pronostico(request):
    """Pronóstico de demanda con la historia de ventas; lo marcado pasa al plan de demanda que usa el MRP."""
    from core.models import Producto

    from . import pronostico as pr
    try:
        h = min(max(int(request.GET.get('meses', 3)), 1), 12)
    except ValueError:
        h = 3
    q = request.GET.get('q', '').strip()
    productos = Producto.objects.filter(activo=True, es_plantilla=False, tipo='BIEN',
                                        clase__in=['PRODUCTO_TERMINADO', 'MERCADERIA', 'SEMIELABORADO'])
    if q:
        productos = productos.filter(nombre__icontains=q) | productos.filter(codigo__icontains=q)
    datos = pr.tabla(list(productos.order_by('nombre')[:400]), h)
    if request.method == 'POST':
        elegidos = set(request.POST.getlist('producto'))
        n = 0
        for f in datos['filas']:
            if str(f['producto'].pk) in elegidos:
                pr.pasar_al_plan(f['producto'], datos['meses_futuros'], f['pronostico'], f['metodo'], request.user)
                n += 1
        messages.success(request, f'Pronóstico de {n} producto(s) pasado al plan de demanda: el MRP lo usará en su '
                                  f'próxima corrida.')
        return redirect(f"{request.path}?meses={h}&q={q}")
    if request.GET.get('formato') == 'excel':
        encabezado = (['Código', 'Producto'] + [f'Venta {m:%m/%Y}' for m in datos['meses_historia']] +
                      ['Método', 'Error %'] + [f'Pronóstico {m:%m/%Y}' for m in datos['meses_futuros']])
        filas = [[f['producto'].codigo, f['producto'].nombre] + f['historia'] + [f['metodo_nombre'], f['error_pct']] +
                 f['pronostico'] for f in datos['filas']]
        return excel_response('Pronostico_demanda', 'PRONÓSTICO DE DEMANDA', encabezado, filas)
    return render(request, 'produccion/pronostico.html', {**datos, 'h': h, 'q': q,
                                                          'opciones_meses': [1, 3, 6, 12]})


@login_required
def mrp(request):
    hoy = timezone.localdate()
    form = PlanDemandaForm(request.POST or None, initial={'fecha': hoy + timedelta(days=7)})
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'demanda' and form.is_valid():
            plan = form.save(commit=False)
            plan.creado_por = request.user
            plan.save()
            messages.success(request, 'Demanda agregada al plan.')
            return redirect('manufactura:mrp')
        if accion == 'quitar':
            PlanDemanda.objects.filter(pk=request.POST.get('id')).delete()
            return redirect('manufactura:mrp')
        if accion == 'ejecutar':
            horizonte = _fecha(request.POST.get('horizonte')) or hoy + timedelta(days=60)
            corrida = servicios.ejecutar_mrp(horizonte, request.user)
            messages.success(request, f'MRP ejecutado: {corrida.propuestas.count()} órdenes planificadas.')
            return redirect('manufactura:mrp')
        if accion in ('fabricar', 'comprar'):
            from compras.solped import ErrorSolped
            ids = request.POST.getlist('propuesta')
            propuestas = list(PropuestaMRP.objects.filter(pk__in=ids).select_related('producto', 'version'))
            try:
                if accion == 'fabricar':
                    hechas = [servicios.convertir_en_orden(p, request.user) for p in propuestas
                              if p.tipo == 'PRODUCIR' and not p.convertida]
                    messages.success(request, f'{len(hechas)} orden(es) de producción creadas en borrador.')
                else:  # compras: solicitud de pedido (u OC directa si hay contrato marco y la empresa lo permite)
                    from compras.solped import desde_mrp
                    from contabilidad.models import CentroCosto
                    centro = CentroCosto.objects.filter(pk=request.POST.get('centro_costo') or 0).first()
                    sp, ordenes = desde_mrp(propuestas, request.user, centro)
                    if sp:
                        messages.success(request, f'Solicitud de pedido {sp.numero} '
                                                  f'{"liberada" if sp.estado == "LIBERADA" else "enviada a liberación"}'
                                                  f' con {sp.lineas.count()} ítem(s).')
                    if ordenes:
                        messages.success(request, 'Con contrato marco, órdenes de compra directas (pendientes de '
                                                  'aprobar): ' + ', '.join(o.numero for o in ordenes))
            except (servicios.ErrorProduccion, ErrorSolped) as exc:
                messages.error(request, str(exc))
            return redirect('manufactura:mrp')
    corrida = CorridaMRP.objects.select_related('usuario').first()
    propuestas = (corrida.propuestas.select_related('producto', 'version', 'proveedor', 'orden_produccion',
                                                    'orden_compra', 'solicitud') if corrida else [])
    from contabilidad.models import CentroCosto
    return render(request, 'produccion/mrp.html', {
        'form': form, 'plan': PlanDemanda.objects.select_related('producto').filter(fecha__gte=hoy - timedelta(30)),
        'corrida': corrida, 'propuestas': propuestas, 'horizonte': hoy + timedelta(days=60),
        'centros': CentroCosto.objects.filter(activo=True)})
