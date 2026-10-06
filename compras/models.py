from django.db import models

from core.models import ComprobanteBase, DocumentoBase, ItemBase


class OrdenCompra(DocumentoBase):
    ESTADOS_PROVEEDOR = [('SIN_ENVIAR', 'Sin enviar'), ('ENVIADA', 'Enviada al proveedor'),
                         ('ACEPTADA', 'Aceptada por el proveedor'), ('RECHAZADA', 'Rechazada por el proveedor')]

    fecha_entrega = models.DateField('Fecha de entrega', null=True, blank=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True,
                                     verbose_name='Centro de costo')
    dias_credito = models.PositiveIntegerField(
        'Días de crédito', null=True, blank=True,
        help_text='La factura vence estos días después del ingreso de la mercadería al almacén. '
                  'Vacío = los del proveedor')
    estado_proveedor = models.CharField('Respuesta del proveedor', max_length=10, choices=ESTADOS_PROVEEDOR,
                                        default='SIN_ENVIAR', editable=False)
    enviada_en = models.DateTimeField(null=True, blank=True, editable=False)
    enviada_a = models.CharField(max_length=200, blank=True, editable=False)
    respondida_en = models.DateTimeField(null=True, blank=True, editable=False)
    respuesta_comentario = models.CharField('Comentario del proveedor', max_length=300, blank=True, editable=False)
    respondida_por = models.CharField(max_length=120, blank=True, editable=False)

    class Meta(DocumentoBase.Meta):
        verbose_name = 'orden de compra'
        verbose_name_plural = 'órdenes de compra'

    def save(self, *args, **kwargs):
        if self.dias_credito is None and self.tercero_id:
            self.dias_credito = self.tercero.dias_credito
        super().save(*args, **kwargs)

    def token_aceptacion(self):
        from django.core import signing

        from erp.empresas import actual
        datos = {'oc': self.pk}
        if actual() != 'default':  # multiempresa: el enlace del proveedor abre la base de su empresa
            datos['e'] = actual()
        return signing.dumps(datos, salt='oc-aceptacion')

    @classmethod
    def desde_token(cls, token, dias=120):
        from django.core import signing

        from erp.empresas import activar
        try:
            datos = signing.loads(token, salt='oc-aceptacion', max_age=dias * 86400)
        except signing.BadSignature:
            return None
        activar(datos.get('e', 'default'))  # el middleware restaura la empresa al terminar la petición
        return cls.objects.filter(pk=datos.get('oc')).first()

    def fecha_ingreso(self):
        """Fecha del último ingreso de mercadería al almacén de esta orden (recepciones o facturas que
        ingresaron directamente). None si aún no se recibe nada."""
        from inventario.models import Operacion
        fechas = list(Operacion.objects.filter(
            models.Q(orden_compra=self) | models.Q(compra__orden_compra=self), estado='CONFIRMADO',
            tipo__clase='INGRESO').values_list('fecha', flat=True))
        fechas += list(self.compras.filter(estado='REGISTRADO', stock_aplicado=True).exclude(
            tipo_comprobante__in=['07', '08']).values_list('fecha_emision', flat=True))
        return max(fechas) if fechas else None

    def actualizar_vencimientos(self):
        """Vencimiento de las facturas de la orden = fecha de ingreso de la mercadería + días de crédito."""
        from datetime import timedelta
        ingreso = self.fecha_ingreso()
        for c in self.compras.filter(estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08']):
            base = ingreso or c.fecha_emision
            vence = base + timedelta(days=self.dias_credito or 0)
            if c.fecha_vencimiento != vence or c.fecha_ingreso != ingreso:
                Compra.objects.filter(pk=c.pk).update(fecha_vencimiento=vence, fecha_ingreso=ingreso)


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
    fecha_ingreso = models.DateField('Ingreso de la mercadería', null=True, blank=True, editable=False,
                                     help_text='Base del vencimiento cuando viene de una orden de compra')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo')
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                        blank=True, related_name='+', limit_choices_to={'imputable': True},
                                        verbose_name='Cuenta de gasto / compra',
                                        help_text='Vacío = según la clasificación')

    class Meta(ComprobanteBase.Meta):
        verbose_name = 'compra'
        unique_together = [('tercero', 'tipo_comprobante', 'serie', 'numero')]

    def _signo_stock(self):
        return -1 if self.es_nota_credito else 1

    def _costo_entrada(self, item):
        # precio por unidad de compra -> costo por unidad de almacén
        return item.precio_neto * self.tipo_cambio / item.producto.factor

    def _cantidad_stock(self, item):
        return item.producto.a_stock(item.cantidad)

    def calcular_totales(self):
        super().calcular_totales()
        # Recibo por honorarios: retención de 4ta categoría del 8% si supera S/ 1,500
        if self.tipo_comprobante == '02' and not self.retencion_pct and self.total * self.tipo_cambio > 1500:
            from core.models import r2
            self.retencion_monto = r2(self.total * 8 / 100)


class CompraItem(ItemBase):
    documento = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name='items')


class AjustePrecioCompra(models.Model):
    """Liquidación de la diferencia entre el precio de la factura y el costo con que se recibió la mercadería
    (cuenta 28 por recibir): lo que sigue en stock revaloriza el inventario; lo ya vendido va al costo de ventas."""
    compra = models.ForeignKey(Compra, on_delete=models.PROTECT, related_name='ajustes_precio')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, related_name='+')
    fecha = models.DateField()
    cantidad = models.DecimalField('Cantidad conciliada', max_digits=14, decimal_places=2)
    diferencia = models.DecimalField('Diferencia S/', max_digits=14, decimal_places=2)
    a_inventario = models.DecimalField('Al inventario S/', max_digits=14, decimal_places=2)
    a_costo = models.DecimalField('Al costo de ventas S/', max_digits=14, decimal_places=2)
    importacion = models.ForeignKey('Importacion', on_delete=models.CASCADE, null=True, blank=True,
                                    related_name='ajustes', help_text='Gastos de importación prorrateados al costo')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']
        verbose_name = 'diferencia de precio de compra'
        verbose_name_plural = 'diferencias de precio de compra'


class Importacion(models.Model):
    """Expediente de importación: la factura comercial (FOB) más los gastos vinculados (flete, seguro, ad valorem,
    agente de aduanas, almacenaje…) que se prorratean al costo de cada producto al liquidar."""
    METODOS = [('VALOR', 'Por valor FOB'), ('CANTIDAD', 'Por cantidad'), ('PESO', 'Por peso')]
    ESTADOS = [('ABIERTA', 'Abierta'), ('LIQUIDADA', 'Liquidada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    descripcion = models.CharField('Descripción', max_length=150)
    dua = models.CharField('N° DUA / DAM', max_length=40, blank=True)
    fecha_llegada = models.DateField('Llegada / levante', null=True, blank=True)
    compra = models.ForeignKey(Compra, on_delete=models.PROTECT, related_name='importaciones',
                               verbose_name='Factura comercial (proveedor del exterior)')
    metodo = models.CharField('Prorrateo', max_length=8, choices=METODOS, default='VALOR')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ABIERTA')
    fecha_liquidacion = models.DateField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'importación'
        verbose_name_plural = 'importaciones'

    def __str__(self):
        return f'Importación {self.numero}'

    @property
    def total_gastos(self):
        return self.gastos.aggregate(s=models.Sum('monto'))['s'] or 0


class GastoImportacion(models.Model):
    CONCEPTOS = [('FLETE', 'Flete internacional'), ('SEGURO', 'Seguro'), ('AD_VALOREM', 'Ad valorem y derechos'),
                 ('AGENTE', 'Agente de aduanas'), ('ALMACEN', 'Almacenaje / terminal'),
                 ('TRANSPORTE', 'Transporte local'), ('OTRO', 'Otros gastos vinculados')]
    importacion = models.ForeignKey(Importacion, on_delete=models.CASCADE, related_name='gastos')
    concepto = models.CharField(max_length=12, choices=CONCEPTOS)
    compra = models.ForeignKey(Compra, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                               verbose_name='Factura del gasto')
    movimiento = models.ForeignKey('finanzas.Movimiento', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+', verbose_name='Pago de caja / bancos',
                                   help_text='Gastos sin factura (ej. tributos de la DUA pagados al banco)')
    descripcion = models.CharField(max_length=150, blank=True)
    monto = models.DecimalField('Monto S/ (sin IGV)', max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['id']
