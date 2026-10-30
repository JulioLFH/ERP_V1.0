from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.comprobantes import ComprobanteViews, ple_num
from core.forms import item_formset
from core.listas import Lista
from core.models import Empresa, r2
from core.utils import excel_response, fmt_fecha, guardar_documento

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
        recibida = tiene_recepciones(orden=oc)
        if recibida:
            initial['ingresar_almacen'] = False  # ya se recibió en Inventario
        from obras.models import Obra
        obra = Obra.de_centro(oc.centro_costo_id)
        if obra is not None and obra.almacen_id:
            initial['almacen'] = obra.almacen_id  # compra de una obra: ingresa a su almacén de obra
        else:  # AUD-03: si todos sus bienes tienen el mismo almacén por defecto, la factura ingresa ahí
            defectos = {i.producto.almacen_defecto_id for i in oc.items.select_related('producto')
                        if i.producto_id and i.producto.es_inventariable}
            if len(defectos) == 1 and None not in defectos:
                initial['almacen'] = defectos.pop()
        sugerida = {}
        if recibida:  # ALM-14: se propone lo recibido y aún no facturado (en la unidad de compra)
            from .cruce import cantidad_sugerida
            sugerida = cantidad_sugerida(oc)
        items = []
        for i in oc.items.select_related('producto'):
            cantidad = i.cantidad
            if recibida and i.producto_id and i.producto.es_inventariable:
                en_stock = sugerida.pop(i.producto_id, None)
                if en_stock is None:
                    continue  # nada recibido pendiente de facturar
                from decimal import Decimal
                factor = i.producto.a_stock(Decimal('1')) or Decimal('1')
                cantidad = min(i.cantidad, en_stock / factor)
            items.append({'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': cantidad,
                          'precio_unitario': i.precio_unitario, 'descuento_pct': i.descuento_pct})
        return initial, items

    def anotar_lista(self, qs, request):
        """Compra sin cuenta contable ni centro de costo (tampoco reparto por centros): la fila sale en rojo y se
        puede filtrar. Las anuladas y las históricas migradas (no editables) no cuentan."""
        from django.db.models import BooleanField, Case, Exists, OuterRef, Value, When
        from .models import DistribucionCompra
        qs = qs.annotate(sin_imputacion=Case(
            When(Q(cuenta_contable__isnull=True, centro_costo__isnull=True) & ~Q(estado='ANULADO') & Q(es_historico=False)
                 & ~Exists(DistribucionCompra.objects.filter(compra=OuterRef('pk'))), then=Value(True)),
            default=Value(False), output_field=BooleanField()))
        if request.GET.get('sin_imputacion'):
            qs = qs.filter(sin_imputacion=True)
        return qs

    def validar_stock(self, form, formset):
        return (super().validar_stock(form, formset) + self.validar_servicios(form, formset) +
                self.validar_cruce(form, formset) + self.validar_precio(form, formset))

    def validar_precio(self, form, formset):
        """COM-27: precio de la factura fuera de la tolerancia de la orden (del proveedor o de la empresa): se
        detiene y se pide confirmarlo; al confirmarlo, la factura queda retenida para pago (cruce.retener)."""
        from decimal import Decimal

        from .tolerancias import permitida, texto
        datos = form.cleaned_data
        oc = datos.get('orden_compra')
        if oc is None or datos.get('tipo_comprobante') in ('07', '08') or datos.get('aceptar_precio') \
                or oc.moneda != datos.get('moneda', oc.moneda):
            return []
        precios = {}
        for i in oc.items.all():
            if i.producto_id:
                precios.setdefault(i.producto_id, i.precio_neto)
        fuera = []
        for c in (getattr(f, 'cleaned_data', None) or {} for f in formset.forms):
            p = c.get('producto') if c and not c.get('DELETE') else None
            if p is None or p.pk not in precios or c.get('precio_unitario') is None:
                continue
            # AUD-02: con descuento 0 «or 0» daba un float (0 / 100) y Decimal × float era un error 500
            neto = c['precio_unitario'] * (1 - Decimal(c.get('descuento_pct') or 0) / Decimal('100'))
            ref = precios[p.pk]
            if abs(neto - ref) > permitida(oc.tercero, 'precio', ref):
                fuera.append(f'{p.nombre}: {neto:,.4f} frente a {ref:,.4f} de {oc.numero}')
        if not fuera:
            return []
        return [f'Precio fuera de la tolerancia de la orden ({texto(oc.tercero, "precio")}): {"; ".join(fuera[:5])}. '
                f'Corrija el precio o marque «registrarla igual»: la factura quedará retenida para pago hasta que '
                f'Compras la libere.']

    def validar_cruce(self, form, formset):
        """Cruce de tres vías (ALM-06): no se factura más de lo recibido (o de lo pedido si la factura ingresa la
        mercadería)."""
        from .cruce import errores_cantidad
        datos = form.cleaned_data
        oc = datos.get('orden_compra')
        if oc is None or datos.get('tipo_comprobante') in ('07', '08'):
            return []
        lineas = [(c.get('producto'), c.get('cantidad')) for c in (getattr(f, 'cleaned_data', None) or {}
                                                                  for f in formset.forms)
                  if c and not c.get('DELETE') and c.get('producto')]
        return errores_cantidad(oc, lineas, bool(datos.get('ingresar_almacen')), form.instance)

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
            from .seguimiento import actualizar_estado  # COM-23: «Atendido» solo con todo facturado
            actualizar_estado(doc.orden_compra, f'factura {doc}')
            doc.orden_compra.actualizar_vencimientos()  # vence desde el ingreso de la mercadería
        from .precios import liquidar
        liquidar(doc)  # diferencia entre el precio facturado y el costo con que se recibió
        from .registro_info import aprender
        aprender(doc)  # último precio por proveedor y producto (registro info)
        from .cruce import retener_si_corresponde
        retener_si_corresponde(doc)  # precio fuera de la tolerancia de la orden: retenida para pago (ALM-06)

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

    def _registro_qs(self, periodo):
        # el comprobante de no domiciliado (91) no va en el Registro de Compras 8.1 (es del 8.2)
        return super()._registro_qs(periodo).exclude(tipo_comprobante='91')

    def linea_ple(self, periodo, n, d):
        """Registro de Compras 8.1 (estructura PLE SUNAT)."""
        ref = d.doc_referencia
        s = d.signo
        anulado = d.estado == 'ANULADO'
        # DUA (50): la base del IGV de importación es el valor en aduana más los tributos (ad valorem)
        base = 0 if anulado else s * (d.base_imponible + (d.valor_aduana + d.no_gravado if d.es_dua else 0))
        igv = 0 if anulado else s * d.igv
        # base e IGV según el destino: campos 14-15 gravadas, 16-17 gravadas y no gravadas, 18-19 no gravadas
        columnas = {'GRAVADA': 0, 'COMUN': 1, 'NO_GRAVADA': 2}.get(d.destino_igv, 0)
        destinos = ['0.00'] * 6
        destinos[columnas * 2], destinos[columnas * 2 + 1] = ple_num(base), ple_num(igv)
        campos = [
            f'{periodo}00', f'C{n:06d}', f'M{n:06d}', fmt_fecha(d.fecha_emision), fmt_fecha(d.fecha_vencimiento),
            d.tipo_comprobante, d.serie, '', d.numero, '', d.tercero.tipo_doc, d.tercero.numero_doc,
            d.tercero.nombre[:100], *destinos,
            ple_num(0 if anulado or d.es_dua else s * d.no_gravado), '0.00', ple_num(d.icbper), '0.00',
            ple_num(0 if anulado else s * d.total), d.moneda, f'{d.tipo_cambio:.3f}',
            fmt_fecha(ref.fecha_emision) if ref else '', ref.tipo_comprobante if ref else '',
            ref.serie if ref else '', '', ref.numero if ref else '',
            # constancia de depósito de la detracción (fecha y número)
            fmt_fecha(d.detraccion_fecha) if d.detraccion_constancia and d.detraccion_fecha else '',
            d.detraccion_constancia, '1' if d.retencion_monto else '', '', '', '', '', '', '', '', '1' if d.total >= 2000 else '',
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
            centro = CentroCosto.vigentes().filter(pk=c).first()
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
        'doc': compra, 'filas': filas, 'centros': CentroCosto.vigentes(),
        'vacias': range(max(3, 6 - len(filas)))})


# ---------------------------------------------------------------- órdenes de compra
LISTA_OC = Lista('compras.ordenes', [  # LIS-01: columnas por usuario, filtros guardados y Excel
    ('numero', 'Número', True), ('fecha', 'Fecha', True), ('tercero', 'Proveedor', True),
    ('centro', 'Centro de costo', False), ('comprador', 'Comprador', False), ('entrega', 'Fecha de entrega', False),
    ('registro', 'Registrado', False), ('moneda', 'Moneda', True), ('total', 'Total', True), ('estado', 'Estado', True),
    # COM-23: lo recibido, pendiente y facturado
    ('situacion', 'Situación', True), ('por_recibir', 'Por recibir', False), ('por_facturar', 'Por facturar', False)])


def _con_avance(ordenes):
    """Situación, por recibir y por facturar de cada orden de la página (COM-23)."""
    from . import seguimiento
    for d in ordenes:
        filas = seguimiento.avance(d)
        d.situacion = seguimiento.situacion(d, filas)
        d.por_recibir = sum((f['por_recibir'] or 0 for f in filas), 0)
        d.por_facturar = sum((f['por_facturar'] for f in filas), 0) if d.estado != 'ANULADO' else 0
    return ordenes


@login_required
def oc_lista(request):
    qs = OrdenCompra.objects.select_related('tercero', 'centro_costo', 'comprador')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(tercero__nombre__icontains=q) | Q(tercero__numero_doc__icontains=q) |
                       Q(centro_costo__codigo__icontains=q) | Q(comprador__username__icontains=q))
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    if request.GET.get('proveedor'):  # respuesta del proveedor (tablero del portal)
        qs = qs.filter(estado_proveedor=request.GET['proveedor']).exclude(estado='ANULADO')
    comprador = request.GET.get('comprador', '')
    if comprador == 'yo' or comprador.isdigit():  # órdenes de un comprador (COM-09)
        qs = qs.filter(comprador_id=request.user.pk if comprador == 'yo' else int(comprador))
    if request.GET.get('saldo'):  # COM-23: aprobadas con algo por recibir o facturar
        from . import seguimiento
        qs = qs.filter(pk__in=[x['oc'].pk for x in seguimiento.ordenes_con_saldo(qs)])
    ctx_lista = LISTA_OC.contexto(request)
    if request.GET.get('formato') == 'excel':
        cols = ctx_lista['visibles']
        valor = {'numero': lambda d: d.numero, 'fecha': lambda d: d.fecha,
                 'tercero': lambda d: f'{d.tercero.nombre} ({d.tercero.numero_doc})',
                 'centro': lambda d: d.centro_costo.codigo if d.centro_costo_id else '',
                 'comprador': lambda d: d.comprador.username if d.comprador_id else '',
                 'entrega': lambda d: d.fecha_entrega, 'registro': lambda d: timezone.localtime(d.creado),
                 'moneda': lambda d: d.moneda, 'total': lambda d: d.total, 'estado': lambda d: d.get_estado_display(),
                 'situacion': lambda d: d.situacion, 'por_recibir': lambda d: d.por_recibir,
                 'por_facturar': lambda d: d.por_facturar}
        return excel_response('Ordenes_de_compra', 'Órdenes de compra', LISTA_OC.titulos(cols),
                              [[valor[c](d) for c in cols] for d in _con_avance(list(qs[:5000]))])
    pagina = Paginator(qs, 50).get_page(request.GET.get('page'))
    _con_avance(pagina.object_list)
    return render(request, 'core/documento_lista.html', {
        'page_obj': pagina, 'q': q,
        'titulo': 'Órdenes de compra', 'etiqueta_tercero': 'Proveedor', 'app': 'compras',
        'url_nuevo': 'compras:oc_nuevo', 'url_detalle': 'compras:oc_detalle',
        'estados': OrdenCompra._meta.get_field('estado').choices, **ctx_lista})


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
            centro = CentroCosto.vigentes().first()
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
            centro = CentroCosto.vigentes().first()
            initial = {'tercero': contrato.proveedor_id, 'contrato': contrato.pk, 'moneda': contrato.moneda,
                       'condicion_pago': contrato.condicion_pago, 'centro_costo': centro.pk if centro else None,
                       'glosa': f'Pedido contra el contrato marco {contrato.numero}'}
            filas, _ = consumo(contrato)
            items = [{'producto': f['l'].producto_id, 'descripcion': f['l'].producto.nombre,
                      'cantidad': max(f['saldo'], 0) if f['saldo'] is not None else 1,
                      'precio_unitario': f['l'].precio_unitario} for f in filas
                     if f['saldo'] is None or f['saldo'] > 0]
    initial = {'comprador': request.user.pk, **(initial or {})}  # quien la registra es su comprador (COM-09)
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
        from .cruce import motivos_retencion
        for _, texto in motivos_retencion(compra):  # ALM-14: cada condición por separado
            messages.info(request, texto)
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
    from obras.presupuesto_proyecto import evaluar_oc, mensaje
    presupuesto = evaluar_oc(oc) if pendiente else None  # SRV-11: disponible del rubro del proyecto
    if presupuesto:
        avisos_fuente = [*avisos_fuente, mensaje(presupuesto)]
    from core.permisos import puede

    from . import seguimiento
    avance = seguimiento.avance(oc)
    return render(request, 'core/documento_detalle.html', {
        # COM-23 a COM-26: avance por línea, entregas, cierre del saldo y solo los botones que el usuario puede usar
        'oc_avance': avance, 'oc_situacion': seguimiento.situacion(oc, avance), 'oc_entregas': seguimiento.entregas(oc),
        'oc_tiene_saldo': seguimiento.tiene_saldo(oc, avance), 'motivos_cierre': seguimiento.MOTIVOS_CIERRE,
        'oc_bloqueo_anular': seguimiento.errores_anulacion(oc) if oc.estado != 'ANULADO' else [],
        'sin_editar': oc.estado != 'PENDIENTE' or not puede(request.user, 'compras.oc'),
        'sin_convertir': not puede(request.user, 'compras.registrar') or oc.estado not in ('APROBADO', 'ATENDIDO'),
        'oc_exceso': bool(presupuesto and presupuesto['excede'] and not oc.exceso_autorizado_por_id),
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

    from .aprobacion_oc import ErrorAprobacionOC, calcular_niveles, por_niveles, puede_aprobar
    try:
        niveles = por_niveles(oc)
        error = ''
    except ErrorAprobacionOC as exc:
        niveles, error = False, str(exc)
    avisos = []  # APR-02: niveles sin aprobador que se saltaron (sin jefe, puesto vacante)
    if oc.estado == 'PENDIENTE' and not error and aprobaciones.hay_reglas('OC'):
        try:
            calcular_niveles(oc, avisos)
        except aprobaciones.ErrorAprobacion:
            pass
    return {'oc_niveles': oc.niveles if niveles else [], 'oc_pendiente': aprobaciones.pendiente(oc.niveles,
                                                                                             oc.nivel_aprobado),
            'oc_ruta': aprobaciones.ruta('OC', oc, oc.niveles, oc.nivel_aprobado) if niveles else [],
            'oc_avisos_niveles': avisos,
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
    """COM-22 y COM-24: solo se aprueba (por niveles o por el límite) o se anula (sin recepciones ni facturas, con
    motivo); «Atendido» lo pone el sistema al facturar todo o al cerrar el saldo, nunca un POST."""
    from . import seguimiento
    oc = get_object_or_404(OrdenCompra, pk=pk)
    estado = request.POST.get('estado')
    if request.method != 'POST':
        return redirect('compras:oc_detalle', pk)
    if estado == 'APROBADO':  # por niveles (Ajustes › Aprobaciones) o, sin reglas, por el límite del usuario
        from .aprobacion_oc import ErrorAprobacionOC, aprobar
        try:
            messages.success(request, aprobar(oc, request.user, request.POST.get('comentario', '')))
        except ErrorAprobacionOC as exc:
            messages.error(request, str(exc))
    elif estado == 'ANULADO':
        try:
            seguimiento.anular(oc, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'Orden {oc.numero}: anulada.')
        except seguimiento.ErrorSeguimiento as exc:
            messages.error(request, str(exc))
    elif request.POST.get('accion') == 'cerrar_saldo':
        try:
            seguimiento.cerrar_saldo(oc, request.user, request.POST.get('motivo_cierre', ''),
                                     request.POST.get('detalle', ''))
            messages.success(request, f'Orden {oc.numero}: saldo cerrado.')
        except seguimiento.ErrorSeguimiento as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, 'Cambio de estado no permitido: la orden se aprueba, se anula o se cierra su saldo; '
                                '«Atendido» lo pone el sistema al facturar todo.')
    return redirect('compras:oc_detalle', pk)


@login_required
def oc_seguimiento(request):
    """COM-23 y COM-24: órdenes aprobadas con saldo por recibir o facturar (vencidas, por proveedor y comprador) y el
    cierre masivo de saldos con motivo."""
    from core.utils import id_o_none

    from . import seguimiento
    if request.method == 'POST':
        cerradas, errores = 0, []
        for oc in OrdenCompra.objects.filter(pk__in=[id_o_none(x) for x in request.POST.getlist('oc')
                                                      if id_o_none(x)]):
            try:
                seguimiento.cerrar_saldo(oc, request.user, request.POST.get('motivo_cierre', ''),
                                         request.POST.get('detalle', ''))
                cerradas += 1
            except seguimiento.ErrorSeguimiento as exc:
                errores.append(str(exc))
        if cerradas:
            messages.success(request, f'{cerradas} orden(es) con el saldo cerrado.')
        for e in errores[:5]:
            messages.error(request, e)
        if not cerradas and not errores:
            messages.error(request, 'Marque las órdenes cuyo saldo se cierra.')
        return redirect(request.get_full_path())
    f = request.GET
    filas = seguimiento.ordenes_con_saldo(vencidas=bool(f.get('vencidas')), proveedor=id_o_none(f.get('proveedor')),
                                          comprador=id_o_none(f.get('comprador')))
    if f.get('formato') == 'excel':
        return excel_response('Seguimiento_OC', 'Seguimiento de órdenes de compra con saldo', [
            'Orden', 'Fecha', 'Entrega', 'Proveedor', 'Comprador', 'Situación', 'Pedido', 'Por recibir',
            'Por facturar', 'Vencida'], [
            [x['oc'].numero, x['oc'].fecha, x['oc'].fecha_entrega, x['oc'].tercero.nombre,
             str(x['oc'].comprador or ''), x['situacion'], x['pedido'], x['por_recibir'], x['por_facturar'],
             'Sí' if x['vencida'] else ''] for x in filas])
    from core.models import Tercero
    return render(request, 'compras/oc_seguimiento.html', {
        'filas': filas, 'motivos': seguimiento.MOTIVOS_CIERRE, 'f': f,
        'proveedores': Tercero.objects.filter(pk__in={x['oc'].tercero_id for x in filas}).order_by('nombre'),
        'compradores': sorted({x['oc'].comprador for x in filas if x['oc'].comprador_id}, key=str)})
