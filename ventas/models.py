from django.db import models

from core.models import ComprobanteBase, DocumentoBase, ElectronicoMixin, ItemBase


class Cotizacion(DocumentoBase):
    TIPOS = [('COT', 'Cotización / Proforma'), ('PED', 'Orden de pedido')]
    tipo = models.CharField(max_length=3, choices=TIPOS, default='COT')
    validez_dias = models.PositiveIntegerField('Validez (días)', default=15)
    vendedor = models.CharField(max_length=80, blank=True)

    class Meta(DocumentoBase.Meta):
        verbose_name = 'cotización / pedido'
        verbose_name_plural = 'cotizaciones / pedidos'


class CotizacionItem(ItemBase):
    documento = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name='items')


class Venta(ComprobanteBase, ElectronicoMixin):
    MOTIVOS_NC = [
        ('', '---'),
        ('01', '01 NC: Anulación de la operación / ND: Intereses por mora'),
        ('02', '02 NC: Anulación por error en el RUC / ND: Aumento de valor'),
        ('03', '03 NC: Corrección por error en la descripción / ND: Penalidades'),
        ('04', '04 Descuento global'),
        ('06', '06 Devolución total'),
        ('07', '07 Devolución por ítem'),
        ('09', '09 Disminución en el valor'),
        ('13', '13 Ajuste de cuotas / fechas'),
    ]
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='ventas', verbose_name='Cotización / pedido')
    doc_referencia = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='notas', verbose_name='Doc. que modifica (NC/ND)')
    motivo_nota = models.CharField('Motivo NC/ND', max_length=2, choices=MOTIVOS_NC, blank=True)
    vendedor = models.CharField(max_length=80, blank=True)
    detraccion_codigo = models.CharField('Bien/servicio con detracción', max_length=3, blank=True, default='35',
                                         help_text='Catálogo 54 SUNAT; solo si la venta tiene detracción')
    descontar_stock = models.BooleanField('Mover almacén', default=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo',
                                     help_text='Canal o sucursal de la venta; la línea de negocio sale del producto')

    class Meta(ComprobanteBase.Meta):
        verbose_name = 'venta'
        unique_together = [('tipo_comprobante', 'serie', 'numero')]

    def _signo_stock(self):
        return 1 if self.es_nota_credito else -1


class VentaItem(ItemBase):
    documento = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='items')
