from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core.comprobantes import ComprobanteViews, ple_num
from core.forms import item_formset
from core.models import Empresa, r2
from core.utils import fmt_fecha, guardar_documento

from .forms import CompraForm, OrdenCompraForm
from .models import Compra, CompraItem, OrdenCompra, OrdenCompraItem, SolicitudPedido
from .solped import exige_conformidad


class ComprasViews(ComprobanteViews):
    app = 'compras'
    modelo, item_modelo, form_class = Compra, CompraItem, CompraForm
    titulo = 'Compras'
    tipo_tercero, etiqueta_tercero = 'PROVEEDOR', 'Proveedor'
    libro, codigo_ple = '8.1', '080100'
    agrupaciones = [('tercero', 'Proveedor'), ('producto', 'Producto / concepto'),
                    ('clasificacion', 'Clasificación'), ('periodo', 'Periodo'), ('tipo_comprobante', 'Tipo comprobante')]

    def initial_desde(self, request):
        oc_id = request.GET.get('oc')
        if not oc_id:
            return super().initial_desde(request)
        oc = get_object_or_404(OrdenCompra, pk=oc_id)
        from inventario.servicios import tiene_recepciones
        initial = {'orden_compra': oc.pk, 'tercero': oc.tercero_id, 'moneda': oc.moneda,
                   'tipo_cambio': oc.tipo_cambio, 'tipo_operacion': oc.tipo_operacion,
                   'centro_costo': oc.centro_costo_id, 'forma_pago': 'CREDITO' if oc.dias_credito else 'CONTADO',
                   'clasificacion': Compra.clasificacion_de(oc.items.select_related('producto'))}
        if tiene_recepciones(orden=oc):
            initial['ingresar_almacen'] = False  # ya se recibió en Inventario
        items = [{'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': i.cantidad,
                  'precio_unitario': i.precio_unitario, 'descuento_pct': i.descuento_pct} for i in oc.items.all()]
        return initial, items

    def validar_stock(self, form, formset):
        return super().validar_stock(form, formset) + self.validar_servicios(form, formset)

    def validar_servicios(self, form, formset):
        """Factura de una OC con servicios: solo contra HES liberadas y hasta su valor (PORT-09). Lo ya facturado de
        esos servicios (otras compras de la OC) se descuenta."""
        from decimal import Decimal
        from .tolerancias import permitida
        datos = form.cleaned_data
        oc = datos.get('orden_compra')
        if oc is None or datos.get('tipo_comprobante') in ('07', '08'):
            return []

        def es_servicio(producto):
            return producto is None or not producto.es_inventariable
        if not any(es_servicio(i.producto) for i in oc.items.select_related('producto')):
            return []

        def neto(cantidad, precio, descuento):
            return r2(Decimal(cantidad or 0) * Decimal(precio or 0) * (1 - Decimal(descuento or 0) / 100))
        facturar = sum((neto(c.get('cantidad'), c.get('precio_unitario'), c.get('descuento_pct'))
                        for c in (getattr(f, 'cleaned_data', None) or {} for f in formset.forms)
                        if c and not c.get('DELETE') and (c.get('descripcion') or c.get('producto'))
                        and es_servicio(c.get('producto'))), Decimal('0'))
        if not facturar:
            return []
        liberadas = list(oc.conformidades.filter(estado='LIBERADA'))
        if not liberadas:
            return [f'La orden {oc.numero} es de servicios: registre la conformidad del servicio (HES) y que se '
                    f'libere antes de registrar la factura.']
        liberado = sum((h.valor for h in liberadas), Decimal('0'))
        ya = sum((neto(i.cantidad, i.precio_unitario, i.descuento_pct) for i in CompraItem.objects.filter(
            documento__orden_compra=oc, documento__estado='REGISTRADO').exclude(
            documento__tipo_comprobante__in=['07', '08']).exclude(documento_id=form.instance.pk)
            .select_related('producto') if es_servicio(i.producto)), Decimal('0'))
        if ya + facturar > liberado + permitida(oc.tercero, 'total', liberado):
            return [f'La factura supera lo conformado en las HES liberadas de {oc.numero}: liberado '
                    f'{oc.simbolo} {liberado:,.2f}, ya facturado {ya:,.2f}, esta factura {facturar:,.2f} (sin IGV).']
        return []

    def _ctx(self, **extra):
        doc = extra.get('doc')
        if doc is not None and getattr(doc, 'pk', None) and 'items' in extra:  # detalle: ¿hay bienes de almacén?
            extra['tiene_bienes'] = any(i.producto_id and i.producto.es_inventariable for i in doc.items.all())
        return super()._ctx(**extra)

    def es_salida(self, tipo, mueve_stock):
        return tipo == '07' and mueve_stock  # devolución al proveedor

    def mueve_stock(self, doc):
        return doc.ingresar_almacen

    def al_guardar(self, doc):
        if doc.ingresar_almacen and doc.tipo_comprobante != '08':
            doc.aplicar_stock()
        if doc.orden_compra_id and doc.orden_compra.estado != 'ANULADO':
            OrdenCompra.objects.filter(pk=doc.orden_compra_id).update(estado='ATENDIDO')
            doc.orden_compra.actualizar_vencimientos()  # vence desde el ingreso de la mercadería
        from .precios import liquidar
        liquidar(doc)  # diferencia entre el precio facturado y el costo con que se recibió
        from .registro_info import aprender
        aprender(doc)  # último precio por proveedor y producto (registro info)

    def ingresar_almacen(self, request, pk):
        from inventario.servicios import tiene_recepciones
        doc = get_object_or_404(Compra, pk=pk)
        from inventario.cierre import error_cierre
        if request.method == 'POST' and tiene_recepciones(compra=doc):
            messages.error(request, 'La mercadería ya se recibió con una recepción de Inventario.')
        elif request.method == 'POST' and error_cierre(doc.fecha_emision):
            messages.error(request, error_cierre(doc.fecha_emision))
        elif request.method == 'POST' and not doc.stock_aplicado:
            doc.ingresar_almacen = True
            doc.save(update_fields=['ingresar_almacen'])
            doc.aplicar_stock()
            messages.success(request, 'Mercadería ingresada al almacén.')
        return redirect('compras:detalle', pk)

    def linea_ple(self, periodo, n, d):
        """Registro de Compras 8.1 (estructura PLE SUNAT)."""
        ref = d.doc_referencia
        s = d.signo
        anulado = d.estado == 'ANULADO'
        campos = [
            f'{periodo}00', f'C{n:06d}', f'M{n:06d}', fmt_fecha(d.fecha_emision), fmt_fecha(d.fecha_vencimiento),
            d.tipo_comprobante, d.serie, '', d.numero, '', d.tercero.tipo_doc, d.tercero.numero_doc,
            d.tercero.nombre[:100],
            ple_num(0 if anulado else s * d.base_imponible), ple_num(0 if anulado else s * d.igv),
            '0.00', '0.00', '0.00', '0.00',
            ple_num(0 if anulado else s * d.no_gravado), '0.00', ple_num(d.icbper), '0.00',
            ple_num(0 if anulado else s * d.total), d.moneda, f'{d.tipo_cambio:.3f}',
            fmt_fecha(ref.fecha_emision) if ref else '', ref.tipo_comprobante if ref else '',
            ref.serie if ref else '', '', ref.numero if ref else '',
            '', '', '1' if d.retencion_monto else '', '', '', '', '', '', '', '', '1' if d.total >= 2000 else '',
            '1',
        ]
        return '|'.join(str(c) for c in campos) + '|'

    def urls(self):
        from django.urls import path
        return super().urls() + [
            path('<int:pk>/almacen/', login_required(self.ingresar_almacen), name='ingresar_almacen'),
        ]


compras_views = ComprasViews()


@login_required
def distribuir(request, pk):
    """Reparte el gasto de la factura en varios centros de costo (porcentajes que suman 100)."""
    from decimal import Decimal, InvalidOperation

    from contabilidad.automatico import marcar_pendiente
    from contabilidad.models import CentroCosto
    from core.forms import periodo_cerrado

    from .models import DistribucionCompra
    compra = get_object_or_404(Compra, pk=pk)
    filas = compra.distribucion.select_related('centro_costo')
    if request.method == 'POST':
        if periodo_cerrado(compra.periodo):
            messages.error(request, f'El periodo contable {compra.periodo} está cerrado.')
            return redirect('compras:detalle', pk)
        repartos = {}
        for c, p in zip(request.POST.getlist('centro_costo'), request.POST.getlist('porcentaje')):
            if not c or not p:
                continue
            try:
                pct = Decimal(p.replace(',', '.'))
            except InvalidOperation:
                pct = Decimal('0')
            centro = CentroCosto.objects.filter(pk=c, activo=True).first()
            if centro is None or pct <= 0:
                messages.error(request, 'Revise los centros de costo y porcentajes (mayores a cero).')
                return redirect('compras:distribuir', pk)
            repartos[centro] = repartos.get(centro, Decimal('0')) + pct
        if repartos and sum(repartos.values()) != 100:
            messages.error(request, f'Los porcentajes suman {sum(repartos.values())}%: deben sumar 100%.')
            return redirect('compras:distribuir', pk)
        compra.distribucion.all().delete()
        for centro, pct in repartos.items():
            DistribucionCompra.objects.create(compra=compra, centro_costo=centro, porcentaje=pct)
        marcar_pendiente(compra.periodo)  # el asiento se rehace al centralizar
        messages.success(request, 'Distribución guardada.' if repartos else
                         'Distribución quitada: el gasto va al centro de costo de la factura.')
        return redirect('compras:detalle', pk)
    return render(request, 'compras/distribuir.html', {
        'doc': compra, 'filas': filas, 'centros': CentroCosto.objects.filter(activo=True),
        'vacias': range(max(3, 6 - len(filas)))})


# ---------------------------------------------------------------- órdenes de compra
@login_required
def oc_lista(request):
    qs = OrdenCompra.objects.select_related('tercero')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(tercero__nombre__icontains=q))
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    if request.GET.get('proveedor'):  # respuesta del proveedor (tablero del portal)
        qs = qs.filter(estado_proveedor=request.GET['proveedor']).exclude(estado='ANULADO')
    return render(request, 'core/documento_lista.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q,
        'titulo': 'Órdenes de compra', 'etiqueta_tercero': 'Proveedor', 'app': 'compras',
        'url_nuevo': 'compras:oc_nuevo', 'url_detalle': 'compras:oc_detalle',
        'estados': OrdenCompra._meta.get_field('estado').choices})


def _oc_ctx(titulo, doc=None):
    return {'titulo': titulo, 'doc': doc, 'app': 'compras', 'igv_tasa': Empresa.actual().igv_tasa,
            'precio_campo': 'precio_compra', 'volver': 'compras:oc_lista'}


@login_required
def oc_nuevo(request):
    initial, items = None, None
    if request.GET.get('reponer') is not None:
        # desde Inventario > Sugerencia de compra: productos a reponer del proveedor habitual
        from core.inventario import sugerencias_compra
        prov = int(request.GET['reponer'] or 0) or None
        filas = [f for f in sugerencias_compra() if f['p'].proveedor_id == prov]
        if filas:
            from contabilidad.models import CentroCosto
            centro = CentroCosto.objects.filter(activo=True).first()
            initial = {'tercero': prov, 'centro_costo': centro.pk if centro else None,
                       'glosa': 'Reposición de stock (sugerencia de compra)'}
            items = [{'producto': f['p'].pk, 'descripcion': f['p'].nombre, 'cantidad': f['cantidad'],
                      'precio_unitario': f['precio']} for f in filas]
    elif request.GET.get('contrato'):
        # llamada contra un contrato marco: proveedor, moneda y precios pactados
        from .abastecimiento import consumo
        from .models import ContratoMarco
        contrato = ContratoMarco.objects.filter(pk=request.GET['contrato'], estado='VIGENTE').first()
        if contrato:
            from contabilidad.models import CentroCosto
            centro = CentroCosto.objects.filter(activo=True).first()
            initial = {'tercero': contrato.proveedor_id, 'contrato': contrato.pk, 'moneda': contrato.moneda,
                       'condicion_pago': contrato.condicion_pago, 'centro_costo': centro.pk if centro else None,
                       'glosa': f'Pedido contra el contrato marco {contrato.numero}'}
            filas, _ = consumo(contrato)
            items = [{'producto': f['l'].producto_id, 'descripcion': f['l'].producto.nombre,
                      'cantidad': max(f['saldo'], 0) if f['saldo'] is not None else 1,
                      'precio_unitario': f['l'].precio_unitario} for f in filas
                     if f['saldo'] is None or f['saldo'] > 0]
    return guardar_documento(request, OrdenCompraForm, item_formset(OrdenCompra, OrdenCompraItem), OrdenCompra(),
                             'core/comprobante_form.html', _oc_ctx('Nueva orden de compra'), initial=initial,
                             items_iniciales=items)


@login_required
def oc_editar(request, pk):
    oc = get_object_or_404(OrdenCompra, pk=pk)
    if oc.estado != 'PENDIENTE':
        messages.error(request, 'Solo se pueden editar órdenes pendientes.')
        return redirect('compras:oc_detalle', pk)
    def reiniciar_aprobacion(doc):  # cambió el monto o el detalle: los niveles se vuelven a calcular
        if doc.niveles:
            OrdenCompra.objects.filter(pk=doc.pk).update(niveles=[], nivel_aprobado=0)
    return guardar_documento(request, OrdenCompraForm, item_formset(OrdenCompra, OrdenCompraItem, extra=0), oc,
                             'core/comprobante_form.html', _oc_ctx(f'Editar orden {oc.numero}', oc),
                             al_guardar=reiniciar_aprobacion)


@login_required
def liberar_pago(request, pk):
    """Compras libera el pago de una factura con precio fuera de la tolerancia (con su motivo)."""
    from .tolerancias import liberar_pago as liberar
    compra = get_object_or_404(Compra, pk=pk)
    if request.method == 'POST':
        try:
            liberar(compra, request.user, request.POST.get('comentario', ''))
            messages.success(request, f'Pago de {compra} liberado: Finanzas ya puede pagarla.')
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect('compras:detalle', pk)


@login_required
def oc_detalle(request, pk):
    from inventario.servicios import acciones_para, operaciones_de

    from .abastecimiento import validar_orden
    oc = get_object_or_404(OrdenCompra, pk=pk)
    from django.urls import reverse

    from .historial_precios import alertas_orden
    from .registro_info import problemas_orden
    bloqueos_fuente, avisos_fuente = problemas_orden(oc)
    pendiente = oc.estado == 'PENDIENTE'
    return render(request, 'core/documento_detalle.html', {
        'alertas_precio': alertas_orden(oc) if pendiente else [], 'avisos_fuente': avisos_fuente if pendiente else [],
        'bloqueos_fuente': bloqueos_fuente if pendiente else [],
        'acciones_inv': acciones_para(oc), 'operaciones_inv': operaciones_de(oc), 'es_oc': True,
        'enlace_aceptacion': request.build_absolute_uri(
            reverse('portal:oc_aceptacion', args=[oc.token_aceptacion()])),
        'facturas_portal': oc.facturas_portal.all(), 'fecha_ingreso': oc.fecha_ingreso(),
        'errores_contrato': validar_orden(oc) if oc.contrato_id else [],
        'solicitudes_oc': SolicitudPedido.objects.filter(lineas__items_oc__documento=oc).distinct().select_related(
            'centro_costo'),
        **_aprobacion_ctx(request.user, oc),
        'servicios_oc': exige_conformidad(oc) or oc.items.exclude(producto__tipo='BIEN').exists(),
        'hes_liberada': oc.conformidades.filter(estado='LIBERADA').exists(),
        'doc': oc, 'items': oc.items.all(), 'app': 'compras', 'titulo_doc': 'ORDEN DE COMPRA',
        'etiqueta_tercero': 'Proveedor', 'relacionados': oc.compras.all(),
        'url_editar': 'compras:oc_editar', 'url_estado': 'compras:oc_estado', 'url_lista': 'compras:oc_lista',
        'url_convertir': 'compras:nuevo', 'param_convertir': 'oc', 'texto_convertir': 'Registrar factura de compra',
        'url_rel': 'compras:detalle'})


def _aprobacion_ctx(user, oc):
    """Niveles, historial y si el usuario puede aprobar o rechazar la OC (para la pantalla)."""
    from core import aprobaciones

    from .aprobacion_oc import ErrorAprobacionOC, por_niveles, puede_aprobar
    try:
        niveles = por_niveles(oc)
        error = ''
    except ErrorAprobacionOC as exc:
        niveles, error = False, str(exc)
    return {'oc_niveles': oc.niveles if niveles else [], 'oc_pendiente': aprobaciones.pendiente(oc.niveles,
                                                                                             oc.nivel_aprobado),
            'oc_error_niveles': error, 'oc_historial': aprobaciones.historial('OC', oc),
            'oc_puede_aprobar': oc.estado == 'PENDIENTE' and not error and puede_aprobar(user, oc)}


@login_required
def oc_rechazar(request, pk):
    """Rechazo de una OC por aprobar: vuelve al comprador con el motivo y la aprobación empieza de nuevo."""
    from .aprobacion_oc import ErrorAprobacionOC, rechazar
    oc = get_object_or_404(OrdenCompra, pk=pk)
    if request.method == 'POST':
        try:
            messages.success(request, rechazar(oc, request.user, request.POST.get('motivo', '')))
        except ErrorAprobacionOC as exc:
            messages.error(request, str(exc))
    return redirect('compras:oc_detalle', pk)


@login_required
def oc_estado(request, pk):
    oc = get_object_or_404(OrdenCompra, pk=pk)
    estado = request.POST.get('estado')
    if request.method == 'POST' and estado in dict(OrdenCompra._meta.get_field('estado').choices):
        if estado == 'APROBADO':  # por niveles (Ajustes › Aprobaciones) o, sin reglas, por el límite del usuario
            from .aprobacion_oc import ErrorAprobacionOC, aprobar
            try:
                messages.success(request, aprobar(oc, request.user))
            except ErrorAprobacionOC as exc:
                messages.error(request, str(exc))
            return redirect('compras:oc_detalle', pk)
        oc.estado = estado
        oc.save(update_fields=['estado'])
        if estado == 'ANULADO':  # lo que tenía de solicitudes de pedido vuelve a la bandeja de compras
            from .solped import actualizar_estados
            actualizar_estados(SolicitudPedido.objects.filter(lineas__items_oc__documento=oc).distinct())
        messages.success(request, f'Orden {oc.numero}: {oc.get_estado_display()}.')
    return redirect('compras:oc_detalle', pk)
