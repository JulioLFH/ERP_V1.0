from decimal import Decimal

from django.db import models
from django.utils import timezone

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
    contrato = models.ForeignKey('ContratoMarco', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='ordenes', verbose_name='Contrato marco',
                                 help_text='Pedido contra un contrato marco: toma sus precios y descuenta su saldo')
    # aprobación por niveles (motor único, core/aprobaciones.py)
    niveles = models.JSONField(default=list, blank=True, editable=False)
    nivel_aprobado = models.PositiveSmallIntegerField(default=0, editable=False)
    observacion_aprobacion = models.CharField('Observación de la aprobación', max_length=300, blank=True,
                                              editable=False)

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
    linea_solped = models.ForeignKey('LineaSolicitud', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='items_oc', editable=False,
                                     verbose_name='Línea de la solicitud de pedido')


class Compra(ComprobanteBase):
    CLASIFICACION = [
        ('MERCADERIA', 'Mercadería'),
        ('MATERIA_PRIMA', 'Materia prima e insumos'),
        ('SUMINISTRO', 'Suministros y repuestos'),
        ('ENVASE', 'Envases y embalajes'),
        ('GASTO', 'Gasto'),
        ('ACTIVO_FIJO', 'Activo fijo'),
        ('SERVICIO', 'Servicio'),
        ('HONORARIOS', 'Honorarios (4ta categoría)'),
    ]
    DE_ALMACEN = ('MERCADERIA', 'MATERIA_PRIMA', 'SUMINISTRO', 'ENVASE')  # clasificaciones de bienes que ingresan al almacén
    clasificacion = models.CharField('Clasificación', max_length=20, choices=CLASIFICACION, default='MERCADERIA')
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
    ajuste_redondeo = models.DecimalField(
        'Diferencia de redondeo del proveedor', max_digits=10, decimal_places=2, default=Decimal('0'),
        editable=False, help_text='Total de la factura del proveedor (XML / SUNAT) menos el calculado con sus líneas, '
                                  'dentro de la tolerancia: se registra el total del proveedor y la diferencia va a '
                                  'su propia cuenta')

    class Meta(ComprobanteBase.Meta):
        verbose_name = 'compra'
        unique_together = [('tercero', 'tipo_comprobante', 'serie', 'numero')]

    def cuadrar_con(self, total_proveedor, tolerancia):
        """Registra el total del proveedor si la diferencia con el calculado está dentro de la tolerancia (redondeo
        por línea, por ejemplo). Devuelve la diferencia; lanza ValueError si la excede."""
        self.ajuste_redondeo = Decimal('0')
        self.calcular_totales()
        diferencia = (total_proveedor - self.total).quantize(Decimal('0.01'))
        if abs(diferencia) > tolerancia:
            raise ValueError(f'El total del proveedor ({total_proveedor:,.2f}) no cuadra con el calculado '
                             f'({self.total:,.2f}); la tolerancia es ±{tolerancia:,.2f}.')
        if diferencia:
            self.ajuste_redondeo = diferencia
            self.calcular_totales()
        return diferencia

    @property
    def ajuste_pen(self):
        from core.models import r2
        return r2(self.ajuste_redondeo * self.tc_efectivo)

    @staticmethod
    def clasificacion_de(items):
        """Clasificación sugerida por el tipo de producto que más pesa en los ítems (OC → factura)."""
        por_clase = {'MERCADERIA': 'MERCADERIA', 'PRODUCTO_TERMINADO': 'MERCADERIA', 'MATERIA_PRIMA': 'MATERIA_PRIMA',
                     'SEMIELABORADO': 'MATERIA_PRIMA', 'SUMINISTRO': 'SUMINISTRO', 'ACTIVO': 'ACTIVO_FIJO',
                     'SERVICIO': 'SERVICIO'}
        pesos = {}
        for i in items:
            clave = por_clase.get(i.producto.clase, 'GASTO') if i.producto_id else 'GASTO'
            pesos[clave] = pesos.get(clave, 0) + (i.cantidad or 0) * (i.precio_unitario or 0)
        return max(pesos, key=pesos.get) if pesos else 'MERCADERIA'

    def _signo_stock(self):
        return -1 if self.es_nota_credito else 1

    def _costo_entrada(self, item):
        # precio por unidad de compra -> costo por unidad de almacén
        return item.precio_neto * self.tipo_cambio / item.producto.factor

    def _cantidad_stock(self, item):
        return item.producto.a_stock(item.cantidad)

    def calcular_totales(self):
        super().calcular_totales()
        if self.ajuste_redondeo:  # redondeo del proveedor: el total es el de su factura
            self.total += self.ajuste_redondeo
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


class ContratoMarco(models.Model):
    """Acuerdo con un proveedor por un periodo: precios pactados y cantidades o monto máximos. Las órdenes de compra
    (llamadas) se emiten contra él y van consumiendo su saldo."""
    ESTADOS = [('VIGENTE', 'Vigente'), ('CERRADO', 'Cerrado'), ('ANULADO', 'Anulado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    proveedor = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+')
    descripcion = models.CharField('Descripción', max_length=150)
    fecha_inicio = models.DateField('Vigente desde')
    fecha_fin = models.DateField('Vigente hasta')
    moneda = models.CharField(max_length=3, choices=[('PEN', 'Soles'), ('USD', 'Dólares')], default='PEN')
    monto_maximo = models.DecimalField('Monto máximo (sin IGV)', max_digits=14, decimal_places=2, null=True,
                                       blank=True, help_text='Vacío = sin tope de monto')
    condicion_pago = models.CharField('Condición de pago', max_length=100, blank=True)
    observaciones = models.TextField(blank=True)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='VIGENTE')
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_inicio', '-id']
        verbose_name = 'contrato marco'
        verbose_name_plural = 'contratos marco'

    def __str__(self):
        return f'Contrato {self.numero} · {self.proveedor.nombre}'

    def vigente_en(self, fecha):
        return self.estado == 'VIGENTE' and self.fecha_inicio <= fecha <= self.fecha_fin


class LineaContrato(models.Model):
    contrato = models.ForeignKey(ContratoMarco, on_delete=models.CASCADE, related_name='lineas')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, related_name='+')
    precio_unitario = models.DecimalField('Precio pactado (sin IGV)', max_digits=14, decimal_places=4)
    cantidad_maxima = models.DecimalField('Cantidad máxima', max_digits=14, decimal_places=2, null=True, blank=True,
                                          help_text='Vacío = sin tope')

    class Meta:
        ordering = ['id']
        unique_together = [('contrato', 'producto')]


class DistribucionCompra(models.Model):
    """Reparto del gasto de una factura de compra en varios centros de costo (en %): el asiento divide la cuenta de
    gasto en una línea por centro."""
    compra = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name='distribucion')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, related_name='+',
                                     verbose_name='Centro de costo')
    porcentaje = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        ordering = ['-porcentaje', 'id']
        unique_together = [('compra', 'centro_costo')]


class RegistroInfo(models.Model):
    """Registro info (fuente de aprovisionamiento): qué proveedores venden cada producto, a qué precio, con qué plazo
    de entrega y lote mínimo, y cuál es el preferido. Se actualiza solo con cada compra registrada (último precio y
    fecha) y lo usan el MRP, la sugerencia de compra y las solicitudes de pedido para elegir proveedor y plazo."""
    producto = models.ForeignKey('core.Producto', on_delete=models.CASCADE, related_name='registros_info')
    proveedor = models.ForeignKey('core.Tercero', on_delete=models.CASCADE, related_name='registros_info',
                                  limit_choices_to={'tipo__in': ['PROVEEDOR', 'AMBOS']})
    codigo_proveedor = models.CharField('Código del proveedor', max_length=40, blank=True)
    moneda = models.CharField(max_length=3, choices=[('PEN', 'Soles'), ('USD', 'Dólares')], default='PEN')
    precio = models.DecimalField('Precio (sin IGV)', max_digits=14, decimal_places=4, null=True, blank=True,
                                 help_text='Precio acordado o cotizado. Vacío = el de la última compra')
    plazo_entrega = models.PositiveSmallIntegerField('Plazo de entrega (días)', default=0)
    cantidad_minima = models.DecimalField('Lote mínimo de compra', max_digits=14, decimal_places=2, default=0)
    vigente_hasta = models.DateField('Precio vigente hasta', null=True, blank=True)
    preferido = models.BooleanField('Proveedor preferido', default=False)
    activo = models.BooleanField(default=True)
    ultimo_precio = models.DecimalField('Último precio comprado', max_digits=14, decimal_places=4, null=True,
                                        blank=True, editable=False)
    ultima_compra = models.DateField('Última compra', null=True, blank=True, editable=False)
    compras = models.PositiveIntegerField('Compras registradas', default=0, editable=False)
    observaciones = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['producto__nombre', '-preferido', 'proveedor__nombre']
        unique_together = [('producto', 'proveedor')]
        verbose_name = 'registro info (proveedor por producto)'
        verbose_name_plural = 'registros info'

    def __str__(self):
        return f'{self.producto} · {self.proveedor}'

    @property
    def precio_vigente(self):
        hoy = timezone.localdate()
        if self.precio is not None and (self.vigente_hasta is None or self.vigente_hasta >= hoy):
            return self.precio
        return self.ultimo_precio

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.preferido:  # un solo preferido por producto
            RegistroInfo.objects.filter(producto=self.producto, preferido=True).exclude(pk=self.pk).update(
                preferido=False)


class Licitacion(models.Model):
    """Solicitud de cotización a varios proveedores (licitación): se registran sus ofertas, se comparan en un cuadro
    y se adjudica cada ítem; las órdenes de compra se generan solas por proveedor ganador."""
    ESTADOS = [('ABIERTA', 'Recibiendo ofertas'), ('ADJUDICADA', 'Adjudicada'), ('ANULADA', 'Anulada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    fecha_limite = models.DateField('Recibir ofertas hasta', null=True, blank=True)
    descripcion = models.CharField('Qué se compra', max_length=150)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, verbose_name='Centro de costo')
    condiciones = models.TextField('Condiciones / especificaciones', blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ABIERTA')
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='+',
                                   editable=False)
    adjudicado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', editable=False)
    adjudicado_en = models.DateTimeField(null=True, blank=True, editable=False)
    justificacion = models.CharField('Justificación de la adjudicación', max_length=300, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'licitación'
        verbose_name_plural = 'licitaciones'

    def __str__(self):
        return f'Licitación {self.numero}'


class LineaLicitacion(models.Model):
    licitacion = models.ForeignKey(Licitacion, on_delete=models.CASCADE, related_name='lineas')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    descripcion = models.CharField('Descripción', max_length=250)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    adjudicada = models.ForeignKey('OfertaLicitacion', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    orden_compra = models.ForeignKey(OrdenCompra, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                     editable=False)
    linea_solped = models.ForeignKey('LineaSolicitud', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='lineas_licitacion', editable=False)

    class Meta:
        ordering = ['id']


class OfertaLicitacion(models.Model):
    licitacion = models.ForeignKey(Licitacion, on_delete=models.CASCADE, related_name='ofertas')
    proveedor = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+')
    moneda = models.CharField(max_length=3, choices=[('PEN', 'Soles'), ('USD', 'Dólares')], default='PEN')
    tipo_cambio = models.DecimalField('T.C.', max_digits=8, decimal_places=3, default=Decimal('1'))
    plazo_entrega = models.PositiveSmallIntegerField('Entrega (días)', default=0)
    condicion_pago = models.CharField('Condición de pago', max_length=100, blank=True)
    observaciones = models.CharField(max_length=250, blank=True)
    recibida = models.DateField('Recibida el', default=timezone.localdate)

    class Meta:
        ordering = ['id']
        unique_together = [('licitacion', 'proveedor')]

    def __str__(self):
        return self.proveedor.nombre


class PrecioOferta(models.Model):
    oferta = models.ForeignKey(OfertaLicitacion, on_delete=models.CASCADE, related_name='precios')
    linea = models.ForeignKey(LineaLicitacion, on_delete=models.CASCADE, related_name='precios')
    precio_unitario = models.DecimalField('Precio (sin IGV)', max_digits=14, decimal_places=4)

    class Meta:
        unique_together = [('oferta', 'linea')]

    @property
    def precio_pen(self):
        return self.precio_unitario * (self.oferta.tipo_cambio if self.oferta.moneda == 'USD' else 1)


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


# ================================================================ solicitud de pedido (SolPed, ME51N de SAP)
# quién la libera: reglas del motor único de aprobación (core.ReglaAprobacion, documento SOLPED)
class SolicitudPedido(models.Model):
    """Necesidad de compra que un área, el MRP, la reposición, un requerimiento o una orden de mantenimiento piden y
    Compras convierte en orden de compra, licitación o pedido contra contrato marco."""
    ORIGENES = [('MANUAL', 'Del área'), ('MRP', 'Planificación (MRP)'), ('REPOSICION', 'Punto de reorden'),
                ('REQUERIMIENTO', 'Requerimiento sin stock'), ('MANTENIMIENTO', 'Orden de mantenimiento')]
    TIPOS = [('MATERIAL', 'Materiales'), ('SERVICIO', 'Servicios'), ('ACTIVO', 'Activo fijo')]
    ESTADOS = [('BORRADOR', 'Borrador'), ('ENVIADA', 'Por liberar'), ('OBSERVADA', 'Observada'),
               ('LIBERADA', 'Liberada'), ('PARCIAL', 'Pedida en parte'), ('PEDIDA', 'Pedida'),
               ('CERRADA', 'Cerrada'), ('RECHAZADA', 'Rechazada'), ('ANULADA', 'Anulada')]
    EDITABLES = ('BORRADOR', 'OBSERVADA')
    ABIERTAS = ('LIBERADA', 'PARCIAL')  # se pueden convertir
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    fecha_requerida = models.DateField('Se necesita el', null=True, blank=True)
    solicitante = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='+', editable=False)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT,
                                     verbose_name='Centro de costo')
    tipo = models.CharField(max_length=8, choices=TIPOS, default='MATERIAL')
    origen = models.CharField(max_length=14, choices=ORIGENES, default='MANUAL', editable=False)
    urgente = models.BooleanField(default=False)
    motivo = models.CharField('Para qué se necesita', max_length=250)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR', editable=False)
    requerimiento = models.ForeignKey('inventario.RequerimientoInterno', on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name='solicitudes', editable=False)
    orden_mantenimiento = models.ForeignKey('produccion.OrdenMantenimiento', on_delete=models.SET_NULL, null=True,
                                            blank=True, related_name='solicitudes', editable=False,
                                            verbose_name='Orden de mantenimiento')
    niveles = models.JSONField(default=list, blank=True, editable=False,
                               help_text='Liberadores calculados al enviar: [{nivel, usuario, nombre}]')
    nivel_aprobado = models.PositiveSmallIntegerField(default=0, editable=False)
    enviada_en = models.DateTimeField(null=True, blank=True, editable=False)
    liberada_en = models.DateTimeField(null=True, blank=True, editable=False)
    comentario = models.CharField('Observación o motivo de rechazo', max_length=300, blank=True, editable=False)
    aviso_presupuesto = models.CharField(max_length=300, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'solicitud de pedido'
        verbose_name_plural = 'solicitudes de pedido'

    def __str__(self):
        return f'SolPed {self.numero or "(borrador)"}'

    @property
    def editable(self):
        return self.estado in self.EDITABLES

    @property
    def monto(self):
        """Monto valorizado sin IGV: cantidad × precio estimado (o el último precio de compra)."""
        return sum((l.valor for l in self.lineas.all()), Decimal('0'))

    @property
    def nivel_pendiente(self):
        for n in self.niveles:
            if n['nivel'] > self.nivel_aprobado:
                return n
        return None


class LineaSolicitud(models.Model):
    UNIDADES = [('NIU', 'Unidad'), ('KGM', 'Kilogramo'), ('LTR', 'Litro'), ('MTR', 'Metro'), ('BX', 'Caja'),
                ('GLL', 'Galón'), ('ZZ', 'Servicio'), ('HUR', 'Hora'), ('DAY', 'Día'), ('MON', 'Mes')]
    solicitud = models.ForeignKey(SolicitudPedido, on_delete=models.CASCADE, related_name='lineas')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                 help_text='Vacío = servicio o material no codificado (texto libre)')
    descripcion = models.CharField('Descripción', max_length=250)
    unidad = models.CharField(max_length=5, choices=UNIDADES, default='NIU')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    precio_estimado = models.DecimalField('Precio estimado S/ (sin IGV)', max_digits=14, decimal_places=4,
                                          default=Decimal('0'),
                                          help_text='Vacío o 0 = el último precio de compra del producto')
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                        blank=True, related_name='+', limit_choices_to={'imputable': True},
                                        verbose_name='Cuenta de gasto o activo',
                                        help_text='Vacío = la cuenta de compra del producto')
    proveedor_sugerido = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True,
                                           related_name='+', verbose_name='Proveedor sugerido')
    requerimiento_item = models.ForeignKey('inventario.RequerimientoItem', on_delete=models.SET_NULL, null=True,
                                           blank=True, related_name='lineas_solped', editable=False)
    cerrada = models.BooleanField('Cerrada', default=False, editable=False,
                                  help_text='No se pedirá lo que falta (cerrada por Compras)')

    class Meta:
        ordering = ['id']

    @property
    def precio(self):
        if self.precio_estimado:
            return self.precio_estimado
        return self.producto.precio_compra / (self.producto.factor or 1) if self.producto_id else Decimal('0')

    @property
    def valor(self):
        return (self.cantidad * self.precio).quantize(Decimal('0.01'))

    @property
    def es_servicio(self):
        return (self.producto.tipo == 'SERVICIO') if self.producto_id else self.unidad in ('ZZ', 'HUR', 'DAY', 'MON')

    @property
    def pedido(self):
        """Cantidad ya pedida en órdenes de compra vigentes (en la unidad de la solicitud)."""
        total = Decimal('0')
        for i in self.items_oc.all():
            if i.documento.estado == 'ANULADO':
                continue
            total += i.producto.a_stock(i.cantidad) if i.producto_id else i.cantidad
        return total

    @property
    def en_licitacion(self):
        return any(l.licitacion.estado == 'ABIERTA' for l in self.lineas_licitacion.all())

    @property
    def pendiente(self):
        if self.cerrada:
            return Decimal('0')
        return max(self.cantidad - self.pedido, Decimal('0'))


class ConformidadServicio(models.Model):
    """Hoja de entrada de servicio: el área conforma lo que el proveedor prestó antes de registrar su factura."""
    numero = models.CharField('N°', max_length=20, editable=False)
    orden_compra = models.ForeignKey(OrdenCompra, on_delete=models.PROTECT, related_name='conformidades')
    fecha = models.DateField(default=timezone.localdate)
    descripcion = models.CharField('Servicio recibido', max_length=250)
    porcentaje = models.DecimalField('Avance conforme %', max_digits=5, decimal_places=2, default=Decimal('100'),
                                     help_text='100 = servicio completo')
    conforme_por = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'conformidad de servicio'
        verbose_name_plural = 'conformidades de servicio'

    def __str__(self):
        return f'Conformidad {self.numero}'
