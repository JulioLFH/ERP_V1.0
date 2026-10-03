from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core import sunat
from core.comprobantes import ComprobanteViews, ple_num
from core.forms import item_formset
from core.models import Empresa, FacturacionConfig
from core.utils import fmt_fecha, guardar_documento

from .forms import CotizacionForm, VentaForm
from .models import Cotizacion, CotizacionItem, Venta, VentaItem


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
        initial = {'cotizacion': cot.pk, 'tercero': cot.tercero_id, 'moneda': cot.moneda, 'vendedor': cot.vendedor,
                   'tipo_cambio': cot.tipo_cambio, 'tipo_operacion': cot.tipo_operacion,
                   'tipo_comprobante': '01' if cot.tercero.tipo_doc == '6' else '03'}
        items = [{'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': i.cantidad,
                  'precio_unitario': i.precio_unitario} for i in cot.items.all()]
        return initial, items

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

    def puede_editar(self, doc):
        return super().puede_editar(doc) and doc.estado_sunat in ('NO_ENVIADO', 'ERROR', 'RECHAZADO')

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

    def urls(self):
        from django.urls import path
        return super().urls() + [
            path('<int:pk>/sunat/', login_required(self.enviar_sunat), name='enviar_sunat'),
        ]

    def linea_ple(self, periodo, n, d):
        """Registro de Ventas e Ingresos 14.1 (estructura PLE SUNAT)."""
        ref = d.doc_referencia
        s = 0 if d.estado == 'ANULADO' else d.signo
        exportacion = d.no_gravado if d.tipo_operacion == 'EXPORTACION' else 0
        exonerada = d.no_gravado if d.tipo_operacion == 'EXONERADA' else 0
        inafecta = d.no_gravado if d.tipo_operacion in ('INAFECTA', 'GRATUITA') else 0
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
    qs = Cotizacion.objects.select_related('tercero')
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
            'precio_campo': 'precio_venta', 'volver': 'ventas:cot_lista'}


@login_required
def cot_nuevo(request):
    return guardar_documento(request, CotizacionForm, item_formset(Cotizacion, CotizacionItem), Cotizacion(),
                             'core/comprobante_form.html', _cot_ctx('Nueva cotización / pedido'),
                             initial={'tipo': request.GET.get('tipo', 'COT')})


@login_required
def cot_editar(request, pk):
    cot = get_object_or_404(Cotizacion, pk=pk)
    if cot.estado not in ('PENDIENTE', 'APROBADO'):
        messages.error(request, 'Solo se pueden editar cotizaciones pendientes o aprobadas.')
        return redirect('ventas:cot_detalle', pk)
    return guardar_documento(request, CotizacionForm, item_formset(Cotizacion, CotizacionItem, extra=0), cot,
                             'core/comprobante_form.html', _cot_ctx(f'Editar {cot.numero}', cot))


@login_required
def cot_detalle(request, pk):
    cot = get_object_or_404(Cotizacion, pk=pk)
    return render(request, 'core/documento_detalle.html', {
        'doc': cot, 'items': cot.items.all(), 'app': 'ventas',
        'titulo_doc': 'COTIZACIÓN / PROFORMA' if cot.tipo == 'COT' else 'ORDEN DE PEDIDO',
        'etiqueta_tercero': 'Cliente', 'relacionados': cot.ventas.all(),
        'url_editar': 'ventas:cot_editar', 'url_estado': 'ventas:cot_estado', 'url_lista': 'ventas:cot_lista',
        'url_convertir': 'ventas:nuevo', 'param_convertir': 'cot', 'texto_convertir': 'Emitir comprobante de venta',
        'url_rel': 'ventas:detalle'})


@login_required
def cot_estado(request, pk):
    cot = get_object_or_404(Cotizacion, pk=pk)
    estado = request.POST.get('estado')
    if request.method == 'POST' and estado in dict(Cotizacion._meta.get_field('estado').choices):
        cot.estado = estado
        cot.save(update_fields=['estado'])
        messages.success(request, f'{cot.numero}: {cot.get_estado_display()}.')
    return redirect('ventas:cot_detalle', pk)
