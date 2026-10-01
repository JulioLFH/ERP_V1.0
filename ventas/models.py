from django.db import models

from core.models import ComprobanteBase, DocumentoBase, ItemBase


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


class Venta(ComprobanteBase):
    MOTIVOS_NC = [
        ('', '---'),
        ('01', '01 Anulación de la operación'),
        ('02', '02 Anulación por error en el RUC'),
        ('03', '03 Corrección por error en la descripción'),
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
    descontar_stock = models.BooleanField('Mover almacén', default=True)

    class Meta(ComprobanteBase.Meta):
        verbose_name = 'venta'
        unique_together = [('tipo_comprobante', 'serie', 'numero')]

    def _signo_stock(self):
        return 1 if self.es_nota_credito else -1


class VentaItem(ItemBase):
    documento = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='items')
