"""Pantallas de la solicitud de pedido: el área la registra y la envía, los liberadores la liberan (también desde el
celular) y Compras la convierte desde su bandeja en orden de compra, licitación o pedido contra contrato marco."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.forms import BootstrapMixin, remoto
from core.models import Producto, Tercero
from core.permisos import puede

from . import solped as srv
from .models import ContratoMarco, LineaSolicitud, SolicitudPedido


class SolicitudForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = SolicitudPedido
        fields = ['fecha', 'fecha_requerida', 'centro_costo', 'tipo', 'urgente', 'motivo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CentroCosto
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)

    def clean(self):
        d = super().clean()
        if d.get('fecha_requerida') and d.get('fecha') and d['fecha_requerida'] < d['fecha']:
            self.add_error('fecha_requerida', 'No puede ser anterior a la fecha de la solicitud.')
        return d


class LineaForm(forms.ModelForm):
    class Meta:
        model = LineaSolicitud
        fields = ['producto', 'descripcion', 'unidad', 'cantidad', 'precio_estimado', 'cuenta_contable',
                  'proveedor_sugerido']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CuentaContable
        f = self.fields
        f['producto'].queryset = Producto.objects.filter(activo=True, es_plantilla=False, puede_comprarse=True)
        f['producto'].required = False
        f['descripcion'].required = False
        f['precio_estimado'].required = False
        f['proveedor_sugerido'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        # gasto (62-68), activo (33-39) o compras (60): las cuentas que una solicitud puede afectar
        f['cuenta_contable'].queryset = CuentaContable.objects.filter(imputable=True, activo=True,
                                                                      codigo__regex=r'^(3[2-9]|6[0-8])')
        for nombre, campo in f.items():
            css = 'form-select form-select-sm' if isinstance(campo.widget, forms.Select) else \
                'form-control form-control-sm'
            campo.widget.attrs['class'] = css + (' text-end' if nombre in ('cantidad', 'precio_estimado') else '')
        remoto(f['producto'], 'productos', perezoso=True)
        remoto(f['proveedor_sugerido'], 'proveedores', perezoso=True)

    def clean(self):
        d = super().clean()
        if d.get('DELETE'):
            return d
        p = d.get('producto')
        if not p and not (d.get('descripcion') or '').strip():
            self.add_error('descripcion', 'Elija un producto o describa el servicio o material.')
        if d.get('cantidad') is not None and d['cantidad'] <= 0:
            self.add_error('cantidad', 'Mayor a cero.')
        if p:
            d['descripcion'] = (d.get('descripcion') or '').strip() or p.nombre
            if p.tipo != 'SERVICIO':
                d['unidad'] = p.unidad if p.unidad in dict(LineaSolicitud.UNIDADES) else d.get('unidad') or 'NIU'
        d['precio_estimado'] = d.get('precio_estimado') or 0
        return d


LineasFormSet = inlineformset_factory(SolicitudPedido, LineaSolicitud, form=LineaForm, extra=0, can_delete=True,
                                      min_num=1, validate_min=True)


def _ve_todas(user):
    return (user.is_superuser or puede(user, 'requerimientos.liberar_solped') or
            puede(user, 'compras.convertir_solped'))


def _puede_ver(user, sp):
    if sp.solicitante_id == user.pk or _ve_todas(user):
        return True
    return any(n.get('usuario') == user.pk for n in sp.niveles or [])


# ---------------------------------------------------------------- lista y formulario
@login_required
def lista(request):
    qs = SolicitudPedido.objects.select_related('centro_costo', 'solicitante').prefetch_related('lineas__producto')
    vista = request.GET.get('vista', 'mias')
    if vista == 'liberar':
        ids = [sp.pk for sp in qs.filter(estado='ENVIADA') if srv.puede_liberar(request.user, sp)]
        qs = qs.filter(pk__in=ids)
    elif vista == 'todas' and _ve_todas(request.user):
        pass
    else:
        vista = 'mias'
        qs = qs.filter(solicitante=request.user)
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(motivo__icontains=q) | Q(lineas__descripcion__icontains=q)).distinct()
    por_liberar = sum(1 for sp in SolicitudPedido.objects.filter(estado='ENVIADA')
                      if srv.puede_liberar(request.user, sp))
    return render(request, 'compras/solped_lista.html', {
        'solicitudes': qs[:300], 'vista': vista, 'estados': SolicitudPedido.ESTADOS, 'q': q,
        'por_liberar': por_liberar, 've_todas': _ve_todas(request.user)})


def _guardar(request, sp, titulo, iniciales=None):
    form = SolicitudForm(request.POST or None, instance=sp, initial=(iniciales or {}).get('cabecera'))
    if request.method == 'GET' and iniciales and iniciales.get('lineas'):
        formset_cls = inlineformset_factory(SolicitudPedido, LineaSolicitud, form=LineaForm,
                                            extra=len(iniciales['lineas']), can_delete=True)
        lineas = formset_cls(instance=sp, prefix='ln', initial=iniciales['lineas'])
    else:
        lineas = LineasFormSet(request.POST or None, instance=sp, prefix='ln')
    if request.method == 'POST' and form.is_valid() and lineas.is_valid():
        try:
            with transaction.atomic():
                nuevo = not sp.pk
                sp = form.save(commit=False)
                if nuevo:
                    sp.solicitante = request.user
                    origen = request.POST.get('origen_ot')
                    if origen:  # desde una orden de mantenimiento
                        from produccion.models import OrdenMantenimiento
                        sp.orden_mantenimiento = OrdenMantenimiento.objects.filter(pk=origen).first()
                        sp.origen = 'MANTENIMIENTO' if sp.orden_mantenimiento else 'MANUAL'
                sp.save()
                lineas.instance = sp
                lineas.save()
                if request.POST.get('accion') == 'enviar':
                    srv.enviar(sp, request.user)
                    if sp.aviso_presupuesto:
                        messages.warning(request, 'Presupuesto: ' + sp.aviso_presupuesto)
                    messages.success(request, f'{sp} enviada para su liberación.')
                else:
                    messages.success(request, 'Solicitud guardada en borrador.')
            return redirect('solicitudes:detalle', sp.pk)
        except srv.ErrorSolped as exc:
            messages.error(request, str(exc))
    return render(request, 'compras/solped_form.html', {
        'form': form, 'lineas': lineas, 'titulo': titulo, 'sp': sp,
        'origen_ot': request.GET.get('ot') or request.POST.get('origen_ot') or ''})


@login_required
def nueva(request):
    iniciales = None
    if request.GET.get('ot'):
        from produccion.models import OrdenMantenimiento
        ot = get_object_or_404(OrdenMantenimiento, pk=request.GET['ot'])
        cabecera, lineas = srv.iniciales_mantenimiento(ot)
        cabecera['centro_costo'] = cabecera['centro_costo'].pk if cabecera['centro_costo'] else None
        iniciales = {'cabecera': cabecera,
                     'lineas': [{**l, 'producto': l['producto'].pk if l['producto'] else None,
                                 'proveedor_sugerido': l['proveedor_sugerido'].pk if l['proveedor_sugerido'] else None}
                                for l in lineas]}
    return _guardar(request, SolicitudPedido(), 'Nueva solicitud de pedido', iniciales)


@login_required
def editar(request, pk):
    sp = get_object_or_404(SolicitudPedido, pk=pk)
    if not sp.editable or (sp.solicitante_id != request.user.pk and not request.user.is_superuser):
        messages.error(request, 'Solo el solicitante edita la solicitud, y mientras esté en borrador u observada.')
        return redirect('solicitudes:detalle', pk)
    return _guardar(request, sp, f'Editar {sp}')


@login_required
def detalle(request, pk):
    sp = get_object_or_404(SolicitudPedido.objects.select_related(
        'centro_costo', 'solicitante', 'requerimiento', 'orden_mantenimiento'), pk=pk)
    if not _puede_ver(request.user, sp):
        messages.error(request, 'Solo puede ver sus solicitudes o las que le toca liberar.')
        return redirect('solicitudes:lista')
    filas = srv.trazabilidad(sp)
    from core.models import StockAlmacen

    from . import stock as srv_stock
    propio = srv_stock.almacen_de_centro(sp.centro_costo_id)  # COM-13: stock antes de comprar
    productos = {f['l'].producto_id for f in filas if f['l'].producto_id}
    disp = srv_stock.disponibilidad(productos, excluir_almacen=propio.pk if propio else None)
    stock_propio = dict(StockAlmacen.objects.filter(almacen=propio, producto_id__in=productos).values_list(
        'producto_id', 'cantidad')) if propio else {}
    for f in filas:
        pid = f['l'].producto_id
        f['stock_propio'] = stock_propio.get(pid, Decimal('0'))
        f['disp_otros'] = srv_stock.total_disponible(disp.get(pid, []))
    from core import aprobaciones
    ruta = aprobaciones.ruta('SOLPED', sp, sp.niveles, sp.nivel_aprobado,
                             rechazado=sp.estado in ('RECHAZADA', 'OBSERVADA', 'ANULADA'))
    return render(request, 'compras/solped_detalle.html', {
        'sp': sp, 'filas': filas, 'ruta': ruta, 'puede_liberar': srv.puede_liberar(request.user, sp),
        'puede_convertir': puede(request.user, 'compras.convertir_solped') and sp.estado in SolicitudPedido.ABIERTAS,
        'propuestas': sp.propuestas_mrp.select_related('producto') if sp.origen == 'MRP' else []})


@login_required
def accion(request, pk, que):
    sp = get_object_or_404(SolicitudPedido, pk=pk)
    if request.method != 'POST':
        return redirect('solicitudes:detalle', pk)
    comentario = request.POST.get('comentario', '')
    try:
        if que == 'enviar':
            if sp.solicitante_id != request.user.pk and not request.user.is_superuser:
                raise srv.ErrorSolped('Solo el solicitante envía la solicitud.')
            srv.enviar(sp, request.user, comentario=comentario)
            if sp.aviso_presupuesto:
                messages.warning(request, 'Presupuesto: ' + sp.aviso_presupuesto)
            messages.success(request, f'{sp} enviada para su liberación.')
        elif que == 'liberar':
            srv.liberar(sp, request.user, comentario)
            messages.success(request, f'{sp}: ' + ('liberada, ya puede pedirse.' if sp.estado == 'LIBERADA' else
                                                   f'nivel {sp.nivel_aprobado} liberado; pasa a '
                                                   f'{sp.nivel_pendiente["nombre"]}.'))
        elif que == 'observar':
            srv.observar(sp, request.user, comentario)
            messages.success(request, f'{sp} devuelta al solicitante con la observación.')
        elif que == 'rechazar':
            srv.rechazar(sp, request.user, comentario)
            messages.success(request, f'{sp} rechazada.')
        elif que == 'anular':
            srv.anular(sp, request.user, comentario)
            messages.success(request, f'{sp} anulada.')
        elif que == 'cerrar':
            if not puede(request.user, 'compras.convertir_solped'):
                raise srv.ErrorSolped('Solo Compras cierra líneas de una solicitud.')
            n = srv.cerrar_lineas(sp, [int(x) for x in request.POST.getlist('linea') if x.isdigit()], request.user)
            messages.success(request, f'{n} línea(s) cerradas: ya no se pedirá lo que falta.')
    except srv.ErrorSolped as exc:
        messages.error(request, str(exc))
    destino = request.POST.get('volver', '')
    if destino.startswith('/') and not destino.startswith('//'):
        return redirect(destino)
    return redirect('solicitudes:detalle', pk)


@login_required
def movil(request):
    """Liberación desde el celular: las solicitudes que le toca liberar, con sus ítems y botones grandes."""
    pendientes = [sp for sp in SolicitudPedido.objects.filter(estado='ENVIADA').select_related(
        'centro_costo', 'solicitante').prefetch_related('lineas__producto') if srv.puede_liberar(request.user, sp)]
    return render(request, 'compras/solped_movil.html', {'pendientes': pendientes})


# ---------------------------------------------------------------- bandeja de Compras y conversión
@login_required
def bandeja(request):
    from django.contrib.auth import get_user_model

    from . import compradores
    lineas = srv.lineas_abiertas()
    soy_comprador = compradores.es_comprador(request.user)
    comprador = request.GET.get('comprador', 'mias' if soy_comprador else '')
    if comprador == 'mias':
        lineas = [l for l in lineas if l.comprador_id == request.user.pk]
    elif comprador == 'sin':
        lineas = [l for l in lineas if not l.comprador_id]
    elif comprador.isdigit():
        lineas = [l for l in lineas if l.comprador_id == int(comprador)]
    hoy_ = timezone.localdate()
    for l in lineas:  # plazo objetivo de la línea (días hábiles desde que se liberó o asignó)
        l.dias = compradores.dias_habiles(compradores._fecha_liberada(l), hoy_)
        l.plazo = compradores.plazo(l)
    proveedor = request.GET.get('proveedor', '')
    producto = request.GET.get('producto', '').strip().lower()
    hasta = request.GET.get('hasta', '')
    if proveedor:
        lineas = [l for l in lineas if str((l.proveedor_sugerido_id or (l.producto.proveedor_id if l.producto_id
                                                                         else '')) or '') == proveedor]
    if producto:
        lineas = [l for l in lineas if producto in l.descripcion.lower() or
                  (l.producto_id and producto in (l.producto.codigo or '').lower())]
    if hasta:
        try:
            limite = date.fromisoformat(hasta)
            lineas = [l for l in lineas if l.solicitud.fecha_requerida and l.solicitud.fecha_requerida <= limite]
        except ValueError:
            pass
    agrupar = request.GET.get('agrupar', 'proveedor')
    grupos = {}
    # COM-13: lo disponible en otros almacenes (se puede atender por traslado) y lo que ya está en traslado
    from inventario.models import SolicitudTraslado

    from . import stock as srv_stock
    disp = srv_stock.disponibilidad({l.producto_id for l in lineas if l.producto_id})
    propios, en_traslado = {}, defaultdict(lambda: Decimal('0'))
    for lid, c in SolicitudTraslado.objects.filter(linea_solped__in=[l.pk for l in lineas], estado__in=(
            'POR_APROBAR', 'EN_TRANSITO')).values_list('linea_solped_id', 'cantidad'):
        en_traslado[lid] += c
    for l in lineas:
        cc = l.solicitud.centro_costo_id
        if cc not in propios:
            propios[cc] = srv_stock.almacen_de_centro(cc)
        l.disp_otros = srv_stock.total_disponible([f for f in disp.get(l.producto_id, [])
                                                   if f['almacen'].pk != propios[cc].pk])
        l.en_traslado = en_traslado.get(l.pk, Decimal('0'))
    for l in lineas:
        l.importe = (l.pendiente * l.precio).quantize(Decimal('0.01'))
        if agrupar == 'producto':
            clave = l.producto.nombre if l.producto_id else 'Sin producto (texto libre)'
        elif agrupar == 'fecha':
            clave = f'{l.solicitud.fecha_requerida:%d/%m/%Y}' if l.solicitud.fecha_requerida else 'Sin fecha'
        elif agrupar == 'comprador':
            clave = (l.comprador.get_full_name() or l.comprador.username) if l.comprador_id else 'Sin comprador'
        else:
            prov = l.proveedor_sugerido or (l.producto.proveedor if l.producto_id else None)
            clave = prov.nombre if prov else 'Sin proveedor sugerido'
        grupos.setdefault(clave, []).append(l)
    if request.method == 'POST':
        ids = [int(x) for x in request.POST.getlist('linea') if x.isdigit()]
        que = request.POST.get('convertir')
        try:
            if que == 'reasignar':
                nuevo = get_user_model().objects.filter(pk=request.POST.get('nuevo_comprador') or 0).first()
                n = compradores.reasignar(ids, nuevo, request.user, request.POST.get('motivo_reasignacion', ''))
                messages.success(request, f'{n} línea(s) reasignadas a {nuevo}.')
                return redirect(request.get_full_path())
            if que == 'asignar':
                n = compradores.asignar_lineas(srv.lineas_abiertas(), request.user)
                messages.success(request, f'{n} línea(s) asignadas a su comprador según los grupos de compras.')
                return redirect(request.get_full_path())
            justificaciones = {i: (request.POST.get(f'motivo_stock_{i}', ''), request.POST.get(f'justif_stock_{i}', ''))
                               for i in ids}
            if que == 'oc':
                prov = Tercero.objects.filter(pk=request.POST.get('proveedor') or 0).first()
                ordenes = srv.a_orden_compra(ids, request.user, proveedor=prov, justificaciones=justificaciones)
                messages.success(request, f'{len(ordenes)} orden(es) de compra creadas (pendientes de aprobar): '
                                 + ', '.join(o.numero for o in ordenes))
                return redirect('compras:oc_detalle', ordenes[0].pk) if len(ordenes) == 1 else \
                    redirect('compras:solped_bandeja')
            if que == 'contrato':
                contrato = ContratoMarco.objects.filter(pk=request.POST.get('contrato') or 0, estado='VIGENTE').first()
                if contrato is None:
                    raise srv.ErrorSolped('Elija el contrato marco vigente.')
                ordenes = srv.a_orden_compra(ids, request.user, contrato=contrato, justificaciones=justificaciones)
                messages.success(request, 'Pedido contra el contrato: ' + ', '.join(o.numero for o in ordenes))
                return redirect('compras:oc_detalle', ordenes[0].pk)
            if que == 'licitacion':
                lic = srv.a_licitacion(ids, request.user, request.POST.get('descripcion', ''),
                                       request.POST.get('fecha_limite') or None)
                messages.success(request, f'{lic} creada: registre las ofertas de los proveedores.')
                return redirect('compras:licitacion', lic.pk)
            raise srv.ErrorSolped('Elija cómo convertir las líneas.')
        except (srv.ErrorSolped, compradores.ErrorComprador) as exc:
            messages.error(request, str(exc))
            return redirect(request.get_full_path())
    from .abastecimiento import contratos_vigentes
    from .models import GrupoCompras
    ids_compradores = set(GrupoCompras.objects.filter(activo=True).values_list('comprador_id', flat=True))
    return render(request, 'compras/solped_bandeja.html', {
        'grupos': grupos, 'total': len(lineas), 'agrupar': agrupar, 'proveedor': proveedor,
        'comprador': comprador, 'soy_comprador': soy_comprador,
        'compradores': get_user_model().objects.filter(pk__in=ids_compradores).order_by('username'),
        'usuarios': get_user_model().objects.filter(is_active=True, acceso_proveedor__isnull=True).order_by('username'),
        'agrupaciones': [('proveedor', 'Proveedor sugerido'), ('producto', 'Producto'), ('fecha', 'Fecha requerida'),
                         ('comprador', 'Comprador')],
        'producto': request.GET.get('producto', ''), 'hasta': hasta,
        'proveedores': Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS']).order_by('nombre'),
        'contratos': contratos_vigentes().select_related('proveedor'),
        'hoy': timezone.localdate(), 'control_stock': srv_stock.control_compra(),
        'motivos_stock': LineaSolicitud.MOTIVOS_STOCK})


# ---------------------------------------------------------------- reglas de liberación (motor único)
@login_required
def reglas(request):
    from django.urls import reverse
    return redirect(reverse('aprobaciones_reglas') + '?documento=SOLPED')


# ---------------------------------------------------------------- reportes
@login_required
def reportes(request):
    hoy = timezone.localdate()
    try:
        desde = date.fromisoformat(request.GET.get('desde', ''))
    except ValueError:
        desde = hoy.replace(day=1) - timedelta(days=90)
    try:
        hasta = date.fromisoformat(request.GET.get('hasta', ''))
    except ValueError:
        hasta = hoy
    datos = srv.reporte(desde, hasta)
    if request.GET.get('formato') == 'excel':
        from core.utils import excel_response
        filas = [['Por estado', e, n, m] for e, n, m in datos['por_estado']]
        filas += [['Por centro de costo', str(c), v['n'], v['monto']] for c, v in datos['por_centro']]
        filas += [['Antigüedad (abiertas)', t, v['n'], v['monto']] for t, v in datos['antiguedad'].items()]
        filas += [['Tiempo promedio de liberación (horas)', '', datos['horas_liberacion'], ''],
                  ['Días promedio de solicitud liberada a OC', '', datos['dias_a_oc'], '']]
        return excel_response(f'Solicitudes_de_pedido_{desde}_{hasta}', 'Solicitudes de pedido',
                              ['Reporte', 'Concepto', 'Cantidad', 'Monto S/'], filas)
    return render(request, 'compras/solped_reportes.html', {'d': datos, 'desde': desde, 'hasta': hasta})
