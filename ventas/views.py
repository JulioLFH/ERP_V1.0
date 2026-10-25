from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core import sunat
from core.comprobantes import ComprobanteViews, ple_num
from core.forms import item_formset
from core.models import Empresa, FacturacionConfig
from core.utils import fmt_fecha, guardar_documento

from .forms import CotizacionForm, ListaPreciosForm, PreciosListaFormSet, VentaForm
from .models import Cotizacion, CotizacionItem, ListaPrecios, Venta, VentaItem


def condicion_a_forma_pago(condicion, cliente=None):
    """Condición de pago del pedido (texto libre: «Crédito 30 días», «Contado», «45 días») -> (forma, días).
    Sin condición escrita manda la del cliente (días de crédito)."""
    import re
    texto = (condicion or '').lower()
    numero = re.search(r'(\d+)\s*d', texto)
    if 'contado' in texto:
        return 'CONTADO', 0
    if 'cr' in texto and 'dito' in texto or numero:
        return 'CREDITO', int(numero.group(1)) if numero else (getattr(cliente, 'dias_credito', 0) or 0)
    if not texto and cliente is not None and cliente.dias_credito:
        return 'CREDITO', cliente.dias_credito
    return 'CONTADO', 0


class VentasViews(ComprobanteViews):
    app = 'ventas'
    modelo, item_modelo, form_class = Venta, VentaItem, VentaForm
    titulo = 'Ventas'
    tipo_tercero, etiqueta_tercero = 'CLIENTE', 'Cliente'
    libro, codigo_ple = '14.1', '140100'
    agrupaciones = [('tercero', 'Cliente'), ('producto', 'Producto'), ('vendedor', 'Vendedor'),
                    ('zona', 'Zona'), ('periodo', 'Periodo'), ('tipo_comprobante', 'Tipo comprobante')]

    def initial_desde(self, request):
        cot_id = request.GET.get('cot')
        if not cot_id:
            return super().initial_desde(request)
        cot = get_object_or_404(Cotizacion, pk=cot_id)
        from .aprobacion_pedido import bloqueo
        if bloqueo(cot):
            messages.error(request, bloqueo(cot))
        initial = {'cotizacion': cot.pk, 'tercero': cot.tercero_id, 'moneda': cot.moneda, 'vendedor': cot.vendedor,
                   'tipo_cambio': cot.tipo_cambio, 'tipo_operacion': cot.tipo_operacion,
                   'tipo_comprobante': '01' if cot.tercero.tipo_doc == '6' else '03'}
        forma, dias = condicion_a_forma_pago(cot.condicion_pago, cot.tercero)
        initial['forma_pago'] = forma
        if dias:
            from datetime import timedelta
            from django.utils import timezone
            initial['fecha_vencimiento'] = timezone.localdate() + timedelta(days=dias)
        items = [{'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': i.cantidad,
                  'precio_unitario': i.precio_unitario, 'descuento_pct': i.descuento_pct} for i in cot.items.all()]
        return initial, items

    def _form_ctx(self, titulo, doc=None):
        return {**super()._form_ctx(titulo, doc), 'precio_url': reverse('ventas:precio')}

    def validar_stock(self, form, formset):
        return (super().validar_stock(form, formset) + self.validar_credito(form, formset)
                + self.validar_permisos(form, formset))

    @staticmethod
    def _pedido_aprobado(datos):
        cot = datos.get('cotizacion')
        return cot is not None and cot.estado in ('APROBADO', 'ATENDIDO')

    def validar_permisos(self, form, formset):
        """Acciones del perfil (VEN-11): vender bajo la tarifa (salvo desde un pedido aprobado), notas de crédito
        por devolución y cambiar el vendedor."""
        from decimal import Decimal
        from core.auditoria import usuario_actual
        from core.permisos import puede
        from .aprobacion_pedido import lineas_bajo_tarifa
        usuario, doc, datos = usuario_actual(), form.instance, form.cleaned_data
        if usuario is None or not usuario.is_authenticated:
            return []
        errores = []
        from .aprobacion_pedido import bloqueo
        if bloqueo(datos.get('cotizacion')) and not doc.pk:  # VEN-12: también si se entra con /ventas/nuevo/?cot=N
            errores.append(bloqueo(datos['cotizacion']))
        tipo = datos.get('tipo_comprobante')
        if tipo in ('07',) and datos.get('motivo_nota') in ('06', '07') and not puede(usuario, 'ventas.devoluciones'):
            errores.append('Su usuario no emite notas de crédito por devolución (06 / 07): pida a quien tenga '
                           '«Emitir notas de crédito por devolución» en su perfil.')
        if tipo not in ('07', '08') and not self._pedido_aprobado(datos) and not puede(usuario, 'ventas.bajo_tarifa'):
            items = [(c.get('producto'), Decimal(c.get('cantidad') or 0), Decimal(c.get('precio_unitario') or 0),
                      Decimal(c.get('descuento_pct') or 0))
                     for c in (getattr(f, 'cleaned_data', None) or {} for f in formset.forms) if c and not c.get('DELETE')]
            bajo = lineas_bajo_tarifa(items, datos.get('tercero'), datos.get('lista_precios'), datos.get('fecha_emision'))
            if bajo:
                errores.append('Precio bajo la tarifa o con más descuento que la lista: ' + '; '.join(bajo[:5]) +
                               '. Pida la aprobación del pedido o a quien tenga «Vender bajo el precio de la lista».')
        cot = datos.get('cotizacion')
        anterior = (type(doc).objects.filter(pk=doc.pk).values_list('vendedor', flat=True).first() if doc.pk
                    else (cot.vendedor if cot is not None else None))
        if anterior and (datos.get('vendedor') or '') != anterior and not puede(usuario, 'ventas.vendedor'):
            errores.append(f'Su usuario no cambia el vendedor del documento ({anterior}).')
        return errores

    def validar_credito(self, form, formset):
        """Ventas al crédito: bloqueo por deuda vencida y por límite de crédito del cliente (salvo que venga de un
        pedido aprobado: el exceso de crédito ya se aprobó en el pedido)."""
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
        from core.models import Empresa, r2
        doc, datos = form.instance, form.cleaned_data
        cliente = datos.get('tercero')
        if not cliente or datos.get('forma_pago') != 'CREDITO' or datos.get('tipo_comprobante') in ('07', '08'):
            return []
        if self._pedido_aprobado(datos) and datos['cotizacion'].tercero_id == cliente.pk:
            return []
        empresa = Empresa.actual()
        deuda = [v for v in Venta.objects.con_saldos().cobrables().filter(tercero=cliente, estado='REGISTRADO').exclude(
            tipo_comprobante='07').exclude(pk=doc.pk) if v.saldo > 0]
        errores = []
        if empresa.bloquear_deuda_vencida:
            limite_fecha = timezone.localdate() - timedelta(days=empresa.dias_gracia)
            vencidos = [v for v in deuda if v.fecha_vencimiento and v.fecha_vencimiento < limite_fecha]
            if vencidos:
                lista = ', '.join(f'{v.numero_completo} (venció {v.fecha_vencimiento:%d/%m/%Y})' for v in vencidos[:5])
                errores.append(f'{cliente.nombre} tiene {len(vencidos)} comprobante(s) vencido(s) e impago(s): '
                               f'{lista}. Cobre la deuda o emita la venta al contado.')
        if cliente.limite_credito:
            def neto(f):
                c = f.cleaned_data
                return (Decimal(c.get('cantidad') or 0) * Decimal(c.get('precio_unitario') or 0) *
                        (1 - Decimal(c.get('descuento_pct') or 0) / 100))
            subtotal = sum((neto(f) for f in formset.forms
                            if getattr(f, 'cleaned_data', None) and not f.cleaned_data.get('DELETE')), Decimal('0'))
            gravado = sum((neto(f) for f in formset.forms if getattr(f, 'cleaned_data', None) and not
                           f.cleaned_data.get('DELETE') and (f.cleaned_data.get('afectacion') or
                                                             datos.get('tipo_operacion')) == 'GRAVADA'), Decimal('0'))
            igv = gravado * empresa.igv_tasa / 100
            tc = (datos.get('tipo_cambio') or 1) if datos.get('moneda') == 'USD' else 1
            nuevo = r2((subtotal + igv) * tc)
            from finanzas.models import Letra
            # la deuda incluye las letras aceptadas pendientes (las facturas canjeadas ya no tienen saldo)
            letras = sum((r2(l.saldo * l.tc_efectivo) for l in Letra.objects.filter(
                tercero=cliente, tipo='COBRAR', estado__in=Letra.ABIERTAS)), Decimal('0'))
            actual = sum((v.saldo_pen for v in deuda), Decimal('0')) + letras
            if actual + nuevo > cliente.limite_credito:
                errores.append(f'Se supera el límite de crédito de {cliente.nombre}: deuda S/ {actual:,.2f} + esta venta '
                               f'S/ {nuevo:,.2f} = S/ {actual + nuevo:,.2f} (límite S/ {cliente.limite_credito:,.2f}).')
        return errores

    def es_salida(self, tipo, mueve_stock):
        return tipo in ('01', '03', '12', '00') and mueve_stock

    def mueve_stock(self, doc):
        return doc.descontar_stock

    def al_guardar(self, doc):
        if doc.descontar_stock and doc.tipo_comprobante in ('01', '03', '12', '00', '07'):
            doc.aplicar_stock()
        if doc.cotizacion_id:
            Cotizacion.objects.filter(pk=doc.cotizacion_id).exclude(estado='ANULADO').update(estado='ATENDIDO')
        cfg = FacturacionConfig.actual()
        if cfg.activa and cfg.envio_automatico and doc.tipo_comprobante in sunat.TIPO_NUBEFACT:
            transaction.on_commit(lambda: self._enviar_silencioso(doc.pk))

    @staticmethod
    def _enviar_silencioso(pk):
        try:
            sunat.enviar_comprobante(Venta.objects.get(pk=pk))
        except sunat.ErrorFacturacion:
            pass  # queda en estado ERROR con el detalle; se puede reenviar desde el comprobante

    def trasladar(self, request, pk):
        """La venta va al periodo de su fecha de emisión (no se traslada; eso es solo para el crédito fiscal de
        las compras)."""
        messages.error(request, 'Los comprobantes de venta se registran en el periodo de su fecha de emisión.')
        return redirect('ventas:detalle', pk)

    def puede_editar(self, doc):
        # el comprobante emitido es inmutable: se corrige con nota de crédito o anulación. Solo se edita el que
        # falló al enviarse (no llegó a SUNAT) para corregirlo y reenviarlo.
        return super().puede_editar(doc) and doc.estado_sunat == 'ERROR'

    def anular(self, request, pk):
        doc = get_object_or_404(Venta, pk=pk)
        motivo = request.POST.get('motivo', '').strip()
        if (request.method == 'POST' and doc.estado_sunat == 'ACEPTADO' and len(motivo) >= 5
                and not self.motivo_bloqueo_anulacion(doc)):
            try:
                sunat.anular_comprobante(doc, motivo)
                messages.info(request, 'Comunicación de baja enviada a SUNAT.')
            except sunat.ErrorFacturacion as exc:
                messages.error(request, f'SUNAT: {exc}. El comprobante no se anuló.')
                return redirect('ventas:detalle', pk)
        return super().anular(request, pk)

    def enviar_sunat(self, request, pk):
        doc = get_object_or_404(Venta, pk=pk)
        if request.method == 'POST':
            try:
                if doc.estado_sunat in ('ACEPTADO', 'PENDIENTE') or request.POST.get('accion') == 'consultar':
                    sunat.consultar_comprobante(doc)
                else:
                    sunat.enviar_comprobante(doc)
                nivel = messages.success if doc.estado_sunat == 'ACEPTADO' else messages.warning
                nivel(request, f'SUNAT: {doc.get_estado_sunat_display()}. {doc.sunat_descripcion}')
            except sunat.ErrorFacturacion as exc:
                messages.error(request, f'Facturación electrónica: {exc}')
        return redirect('ventas:detalle', pk)

    def reactivar(self, request, pk):
        """Vuelve a «Registrado» una venta anulada que nunca llegó a SUNAT (se anuló por error); se descuenta otra
        vez del almacén."""
        from core.forms import periodo_cerrado
        from inventario.cierre import error_cierre
        doc = get_object_or_404(Venta, pk=pk)
        if request.method != 'POST':
            return redirect('ventas:detalle', pk)
        bloqueo = ('El comprobante no está anulado.' if doc.estado != 'ANULADO' else
                   'Ya se informó a SUNAT: emita un comprobante nuevo.' if doc.estado_sunat not in ('NO_ENVIADO', 'ERROR')
                   else 'Es historial importado del sistema anterior.' if doc.es_historico else
                   f'El periodo contable {doc.periodo} está cerrado.' if periodo_cerrado(doc.periodo) else
                   error_cierre(doc.fecha_emision) if doc.descontar_stock and error_cierre(doc.fecha_emision) else '')
        if not bloqueo and doc.descontar_stock and doc.tipo_comprobante in ('01', '03', '12', '00'):
            from core.utils import faltantes_stock
            faltan = faltantes_stock([(i.producto, i.cantidad) for i in doc.items.select_related('producto')],
                                     doc.almacen, fecha=doc.fecha_emision)
            bloqueo = ('No hay stock para volver a descontar: ' + ' '.join(faltan)) if faltan else ''
        if bloqueo:
            messages.error(request, bloqueo)
            return redirect('ventas:detalle', pk)
        with transaction.atomic():
            doc.estado = 'REGISTRADO'
            doc.motivo_anulacion = f'Reactivada por {request.user.get_username()}; antes: {doc.motivo_anulacion}'[:250]
            doc.anulado_por, doc.anulado_en = None, None
            doc.save()
            if doc.descontar_stock and doc.tipo_comprobante in ('01', '03', '12', '00', '07'):
                doc.aplicar_stock()
        messages.success(request, f'{doc} reactivado: vuelve a «Registrado».')
        return redirect('ventas:detalle', pk)

    def urls(self):
        from django.urls import path
        return super().urls() + [
            path('<int:pk>/sunat/', login_required(self.enviar_sunat), name='enviar_sunat'),
            path('<int:pk>/reactivar/', login_required(self.reactivar), name='reactivar'),
        ]

    def linea_ple(self, periodo, n, d):
        """Registro de Ventas e Ingresos 14.1 (estructura PLE SUNAT)."""
        ref = d.doc_referencia
        s = 0 if d.estado == 'ANULADO' else d.signo
        exportacion, exonerada, inafecta = d.exportacion, d.exonerado, d.inafecto  # por línea (mixtos)
        campos = [
            f'{periodo}00', f'V{n:06d}', f'M{n:06d}', fmt_fecha(d.fecha_emision), fmt_fecha(d.fecha_vencimiento),
            d.tipo_comprobante, d.serie, d.numero, '', d.tercero.tipo_doc, d.tercero.numero_doc,
            d.tercero.nombre[:100],
            ple_num(s * exportacion), ple_num(s * d.base_imponible), '0.00', ple_num(s * d.igv), '0.00',
            ple_num(s * exonerada), ple_num(s * inafecta), '0.00', '', '', ple_num(s and d.icbper), '0.00',
            ple_num(s * d.total), d.moneda, f'{d.tipo_cambio:.3f}',
            fmt_fecha(ref.fecha_emision) if ref else '', ref.tipo_comprobante if ref else '',
            ref.serie if ref else '', ref.numero if ref else '', '', '', '1' if d.total >= 2000 else '',
            '2' if d.estado == 'ANULADO' else '1',
        ]
        return '|'.join(str(c) for c in campos) + '|'


ventas_views = VentasViews()


# ---------------------------------------------------------------- cotizaciones / pedidos
@login_required
def cot_lista(request):
    from core.politicas import filtrar_vendedor
    qs = filtrar_vendedor(Cotizacion.objects.select_related('tercero'), request.user)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(tercero__nombre__icontains=q))
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    return render(request, 'core/documento_lista.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q,
        'titulo': 'Cotizaciones, proformas y órdenes de pedido', 'etiqueta_tercero': 'Cliente', 'app': 'ventas',
        'url_nuevo': 'ventas:cot_nuevo', 'url_detalle': 'ventas:cot_detalle',
        'estados': Cotizacion._meta.get_field('estado').choices})


def _cot_ctx(titulo, doc=None):
    return {'titulo': titulo, 'doc': doc, 'app': 'ventas', 'igv_tasa': Empresa.actual().igv_tasa,
            'precio_campo': 'precio_venta', 'volver': 'ventas:cot_lista', 'precio_url': reverse('ventas:precio')}


@login_required
def cot_nuevo(request):
    from . import crm_servicios as srv
    from .models import Oportunidad
    inicial = {'tipo': request.GET.get('tipo', 'COT')}
    oportunidad = None
    if (request.GET.get('oportunidad') or '').isdigit():  # CRM-03: desde la oportunidad (cliente, vendedor y nombre)
        oportunidad = Oportunidad.objects.filter(pk=request.GET['oportunidad']).select_related('responsable').first()
        if oportunidad is not None and srv.puede_ver(request.user, oportunidad):
            u = oportunidad.responsable or request.user
            inicial.update(tercero=oportunidad.tercero_id, vendedor=(u.get_full_name() or u.username)[:80],
                           glosa=f'Oportunidad: {oportunidad.nombre}')
        else:
            oportunidad = None

    def al_guardar(doc):
        if oportunidad is not None:
            srv.vincular_cotizacion(doc, oportunidad)
        else:  # CRM-14: desde el monto del parámetro, la cotización sin oportunidad crea la suya
            nueva = srv.asegurar_oportunidad(doc, request.user)
            if nueva is not None:
                messages.info(request, f'Se creó la oportunidad «{nueva.nombre}» en el CRM para esta cotización.')
    return guardar_documento(request, CotizacionForm, item_formset(Cotizacion, CotizacionItem), Cotizacion(),
                             'core/comprobante_form.html', _cot_ctx('Nueva cotización / pedido'),
                             initial=inicial, al_guardar=al_guardar)


@login_required
def cot_editar(request, pk):
    cot = get_object_or_404(Cotizacion, pk=pk)
    if cot.estado not in ('PENDIENTE', 'APROBADO') or cot.ventas.exclude(estado='ANULADO').exists():
        messages.error(request, 'Solo se editan cotizaciones y pedidos pendientes o aprobados sin comprobante emitido.')
        return redirect('ventas:cot_detalle', pk)

    def volver_a_pendiente(doc):
        # un pedido aprobado que se modifica (cantidades, precios, cliente) vuelve a aprobarse
        if doc.estado == 'APROBADO' or doc.niveles:
            Cotizacion.objects.filter(pk=doc.pk).update(estado='PENDIENTE', niveles=[], nivel_aprobado=0)
        if doc.estado == 'APROBADO':
            messages.warning(request, f'{doc.numero} se modificó: volvió a «Pendiente» y debe aprobarse otra vez.')

    vendedor = cot.vendedor  # el form.is_valid() ya vuelca los datos nuevos en la instancia

    def validar_vendedor(form, formset):
        from core.permisos import puede
        if vendedor and (form.cleaned_data.get('vendedor') or '') != vendedor and \
                not puede(request.user, 'ventas.vendedor'):
            return [f'Su usuario no cambia el vendedor del documento ({vendedor}).']
        return []

    if cot.estado == 'APROBADO' and request.method == 'GET':
        messages.info(request, 'Este documento está aprobado: al guardar los cambios volverá a «Pendiente» de '
                               'aprobación.')
    return guardar_documento(request, CotizacionForm, item_formset(Cotizacion, CotizacionItem, extra=0), cot,
                             'core/comprobante_form.html', _cot_ctx(f'Editar {cot.numero}', cot),
                             al_guardar=volver_a_pendiente, validar=validar_vendedor)


@login_required
def cot_detalle(request, pk):
    from core.aprobaciones import historial, pendiente
    from .aprobacion_pedido import bloqueo, estado_aprobacion, motivos
    cot = get_object_or_404(Cotizacion, pk=pk)
    ruta, enviar, espera = estado_aprobacion(cot, request.user) if cot.tipo == 'PED' else ([], False, '')
    from .crm_servicios import credito
    return render(request, 'core/documento_detalle.html', {
        'bloqueo_convertir': bloqueo(cot), 'credito_cliente': credito(cot.tercero) if cot.estado == 'PENDIENTE' else None,
        'pedido_ruta': ruta, 'pedido_enviar': enviar, 'pedido_espera': espera,
        'pedido_historial': historial('PEDIDO', cot) if cot.tipo == 'PED' else [],
        'pedido_motivos': motivos(cot)[1] if cot.estado == 'PENDIENTE' else [],
        'pedido_pendiente': pendiente(cot.niveles, cot.nivel_aprobado),
        'doc': cot, 'items': cot.items.all(), 'app': 'ventas',
        'titulo_doc': 'COTIZACIÓN / PROFORMA' if cot.tipo == 'COT' else 'ORDEN DE PEDIDO',
        'etiqueta_tercero': 'Cliente', 'relacionados': cot.ventas.all(),
        'url_editar': 'ventas:cot_editar', 'url_estado': 'ventas:cot_estado', 'url_lista': 'ventas:cot_lista',
        'url_convertir': 'ventas:nuevo', 'param_convertir': 'cot', 'texto_convertir': 'Emitir comprobante de venta',
        'url_rel': 'ventas:detalle'})


@login_required
def cot_estado(request, pk):
    from .aprobacion_pedido import ErrorAprobacionPedido, aprobar, enviar
    cot = get_object_or_404(Cotizacion, pk=pk)
    estado = request.POST.get('estado')
    if request.method == 'POST' and estado == 'ENVIAR' and cot.estado == 'PENDIENTE':
        try:
            messages.success(request, enviar(cot, request.user))
        except ErrorAprobacionPedido as exc:
            messages.error(request, str(exc))
    elif request.method == 'POST' and estado == 'APROBADO' and cot.estado == 'PENDIENTE':
        try:
            messages.success(request, aprobar(cot, request.user))
        except ErrorAprobacionPedido as exc:
            messages.error(request, str(exc))
    elif request.method == 'POST' and estado in dict(Cotizacion._meta.get_field('estado').choices):
        cot.estado = estado
        if estado == 'PENDIENTE':
            cot.niveles, cot.nivel_aprobado = [], 0
        cot.save(update_fields=['estado', 'niveles', 'nivel_aprobado'])
        messages.success(request, f'{cot.numero}: {cot.get_estado_display()}.')
    return redirect('ventas:cot_detalle', pk)


# ---------------------------------------------------------------- listas de precios
@login_required
def precio(request):
    """Precio y descuento de un producto según la lista (la del documento o la del cliente) y la cantidad."""
    from datetime import date
    from decimal import Decimal, InvalidOperation
    from core.models import Producto, Tercero
    producto = get_object_or_404(Producto, pk=request.GET.get('producto') or 0)
    tercero = Tercero.objects.filter(pk=request.GET.get('tercero') or 0).first()
    lista = ListaPrecios.objects.filter(pk=request.GET.get('lista') or 0).first() or (
        tercero.lista_precios if tercero else None)
    try:
        cantidad = Decimal(request.GET.get('cantidad') or '1')
    except InvalidOperation:
        cantidad = Decimal('1')
    try:
        fecha = date.fromisoformat(request.GET.get('fecha') or '')
    except ValueError:
        fecha = None
    valor, descuento = producto.precio_para(lista, cantidad, fecha)
    return JsonResponse({'precio': str(valor), 'descuento': str(descuento), 'lista': lista.codigo if lista else None})


@login_required
def listas_precios(request):
    listas = list(ListaPrecios.objects.annotate(n=Count('precios')))
    return render(request, 'ventas/listas_precios.html', {
        'listas': listas, 'sin_precios': sum(1 for l in listas if l.activa and not l.n)})


def _guardar_lista(request, lista, titulo):
    form = ListaPreciosForm(request.POST or None, instance=lista)
    precios = PreciosListaFormSet(request.POST or None, instance=lista, prefix='precios')
    if request.method == 'POST' and form.is_valid() and precios.is_valid():
        with transaction.atomic():
            lista = form.save()
            precios.instance = lista
            precios.save()
        messages.success(request, f'Lista de precios {lista.codigo} guardada.')
        return redirect('ventas:listas_precios')
    return render(request, 'ventas/lista_precios_form.html', {'form': form, 'precios': precios, 'titulo': titulo,
                                                               'lista': lista})


@login_required
def lista_precios_nueva(request):
    return _guardar_lista(request, ListaPrecios(), 'Nueva lista de precios')


@login_required
def lista_precios_editar(request, pk):
    lista = get_object_or_404(ListaPrecios, pk=pk)
    return _guardar_lista(request, lista, f'Editar lista {lista}')
