from django.db import models

from core.models import ComprobanteBase, DocumentoBase, ItemBase


class OrdenCompra(DocumentoBase):
    fecha_entrega = models.DateField('Fecha de entrega', null=True, blank=True)

    class Meta(DocumentoBase.Meta):
        verbose_name = 'orden de compra'
        verbose_name_plural = 'órdenes de compra'


class OrdenCompraItem(ItemBase):
    documento = models.ForeignKey(OrdenCompra, on_delete=models.CASCADE, related_name='items')


class Compra(ComprobanteBase):
    CLASIFICACION = [
        ('MERCADERIA', 'Mercadería'),
        ('GASTO', 'Gasto'),
        ('ACTIVO_FIJO', 'Activo fijo'),
        ('SERVICIO', 'Servicio'),
        ('HONORARIOS', 'Honorarios (4ta categoría)'),
    ]
    clasificacion = models.CharField('Clasificación', max_length=12, choices=CLASIFICACION, default='MERCADERIA')
    orden_compra = models.ForeignKey(OrdenCompra, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='compras', verbose_name='Orden de compra')
    doc_referencia = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='notas', verbose_name='Doc. que modifica (NC/ND)')
    ingresar_almacen = models.BooleanField('Ingresar a almacén', default=True)

    class Meta(ComprobanteBase.Meta):
        verbose_name = 'compra'
        unique_together = [('tercero', 'tipo_comprobante', 'serie', 'numero')]

    def _signo_stock(self):
        return -1 if self.es_nota_credito else 1

    def _costo_entrada(self, item):
        return item.precio_unitario * self.tipo_cambio

    def calcular_totales(self):
        super().calcular_totales()
        # Recibo por honorarios: retención de 4ta categoría del 8% si supera S/ 1,500
        if self.tipo_comprobante == '02' and not self.retencion_pct and self.total * self.tipo_cambio > 1500:
            from core.models import r2
            self.retencion_monto = r2(self.total * 8 / 100)


class CompraItem(ItemBase):
    documento = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name='items')
