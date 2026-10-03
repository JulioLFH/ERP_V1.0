from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core.comprobantes import ComprobanteViews, ple_num
from core.forms import item_formset
from core.models import Empresa
from core.utils import fmt_fecha, guardar_documento

from .forms import CompraForm, OrdenCompraForm
from .models import Compra, CompraItem, OrdenCompra, OrdenCompraItem


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
        initial = {'orden_compra': oc.pk, 'tercero': oc.tercero_id, 'moneda': oc.moneda,
                   'tipo_cambio': oc.tipo_cambio, 'tipo_operacion': oc.tipo_operacion}
        items = [{'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': i.cantidad,
                  'precio_unitario': i.precio_unitario} for i in oc.items.all()]
        return initial, items

    def es_salida(self, tipo, mueve_stock):
        return tipo == '07' and mueve_stock  # devolución al proveedor

    def mueve_stock(self, doc):
        return doc.ingresar_almacen

    def al_guardar(self, doc):
        if doc.ingresar_almacen and doc.tipo_comprobante != '08':
            doc.aplicar_stock()
        if doc.orden_compra_id and doc.orden_compra.estado != 'ANULADO':
            OrdenCompra.objects.filter(pk=doc.orden_compra_id).update(estado='ATENDIDO')

    def ingresar_almacen(self, request, pk):
        doc = get_object_or_404(Compra, pk=pk)
        if request.method == 'POST' and not doc.stock_aplicado:
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


# ---------------------------------------------------------------- órdenes de compra
@login_required
def oc_lista(request):
    qs = OrdenCompra.objects.select_related('tercero')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(tercero__nombre__icontains=q))
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    return render(request, 'core/documento_lista.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q,
        'titulo': 'Órdenes de compra', 'etiqueta_tercero': 'Proveedor', 'app': 'compras',
        'url_nuevo': 'compras:oc_nuevo', 'url_detalle': 'compras:oc_detalle',
        'estados': OrdenCompra._meta.get_field('estado').choices})


def _oc_ctx(titulo, doc=None):
    return {'titulo': titulo, 'doc': doc, 'app': 'compras', 'igv_tasa': Empresa.actual().igv_tasa,
            'precio_campo': 'costo_promedio', 'volver': 'compras:oc_lista'}


@login_required
def oc_nuevo(request):
    return guardar_documento(request, OrdenCompraForm, item_formset(OrdenCompra, OrdenCompraItem), OrdenCompra(),
                             'core/comprobante_form.html', _oc_ctx('Nueva orden de compra'))


@login_required
def oc_editar(request, pk):
    oc = get_object_or_404(OrdenCompra, pk=pk)
    if oc.estado != 'PENDIENTE':
        messages.error(request, 'Solo se pueden editar órdenes pendientes.')
        return redirect('compras:oc_detalle', pk)
    return guardar_documento(request, OrdenCompraForm, item_formset(OrdenCompra, OrdenCompraItem, extra=0), oc,
                             'core/comprobante_form.html', _oc_ctx(f'Editar orden {oc.numero}', oc))


@login_required
def oc_detalle(request, pk):
    oc = get_object_or_404(OrdenCompra, pk=pk)
    return render(request, 'core/documento_detalle.html', {
        'doc': oc, 'items': oc.items.all(), 'app': 'compras', 'titulo_doc': 'ORDEN DE COMPRA',
        'etiqueta_tercero': 'Proveedor', 'relacionados': oc.compras.all(),
        'url_editar': 'compras:oc_editar', 'url_estado': 'compras:oc_estado', 'url_lista': 'compras:oc_lista',
        'url_convertir': 'compras:nuevo', 'param_convertir': 'oc', 'texto_convertir': 'Registrar factura de compra',
        'url_rel': 'compras:detalle'})


@login_required
def oc_estado(request, pk):
    oc = get_object_or_404(OrdenCompra, pk=pk)
    estado = request.POST.get('estado')
    if request.method == 'POST' and estado in dict(OrdenCompra._meta.get_field('estado').choices):
        oc.estado = estado
        oc.save(update_fields=['estado'])
        messages.success(request, f'Orden {oc.numero}: {oc.get_estado_display()}.')
    return redirect('compras:oc_detalle', pk)
