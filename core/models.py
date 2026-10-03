from decimal import ROUND_HALF_UP, Decimal

from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

D0 = Decimal('0')
D2 = Decimal('0.01')


def r2(valor):
    return Decimal(valor or 0).quantize(D2, ROUND_HALF_UP)


# ---------------------------------------------------------------- catálogos SUNAT
TIPO_COMPROBANTE = [
    ('01', 'Factura'),
    ('03', 'Boleta de Venta'),
    ('07', 'Nota de Crédito'),
    ('08', 'Nota de Débito'),
    ('02', 'Recibo por Honorarios'),
    ('12', 'Ticket'),
    ('14', 'Recibo Servicios Públicos'),
    ('00', 'Otros'),
]
MONEDAS = [('PEN', 'Soles (S/)'), ('USD', 'Dólares (US$)')]
TIPO_OPERACION = [
    ('GRAVADA', 'Gravada (IGV)'),
    ('EXONERADA', 'Exonerada'),
    ('INAFECTA', 'Inafecta'),
    ('EXPORTACION', 'Exportación'),
    ('GRATUITA', 'Gratuita'),
]
FORMA_PAGO = [('CONTADO', 'Contado'), ('CREDITO', 'Crédito')]
ESTADO_COMPROBANTE = [('REGISTRADO', 'Registrado'), ('ANULADO', 'Anulado')]
ESTADO_DOCUMENTO = [
    ('PENDIENTE', 'Pendiente'),
    ('APROBADO', 'Aprobado'),
    ('ATENDIDO', 'Atendido / Facturado'),
    ('ANULADO', 'Anulado'),
]


class Empresa(models.Model):
    ruc = models.CharField('RUC', max_length=11)
    razon_social = models.CharField('Razón social', max_length=200)
    nombre_comercial = models.CharField(max_length=200, blank=True)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    telefono = models.CharField('Teléfono', max_length=50, blank=True)
    email = models.EmailField(blank=True)
    igv_tasa = models.DecimalField('Tasa IGV %', max_digits=5, decimal_places=2, default=Decimal('18.00'))
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Ubigeo del domicilio fiscal (6 dígitos)')
    registro_mtc = models.CharField('Registro MTC', max_length=20, blank=True,
                                    help_text='Solo si emite guías como transportista')

    class Meta:
        verbose_name = 'empresa'

    def __str__(self):
        return self.razon_social

    @classmethod
    def actual(cls):
        empresa = cls.objects.first()
        if empresa is None:
            empresa = cls.objects.create(ruc='20000000001', razon_social='MI EMPRESA S.A.C.')
        return empresa


class Tercero(models.Model):
    TIPOS = [('CLIENTE', 'Cliente'), ('PROVEEDOR', 'Proveedor'), ('AMBOS', 'Cliente y proveedor')]
    TIPOS_DOC = [('6', 'RUC'), ('1', 'DNI'), ('4', 'Carné de extranjería'), ('7', 'Pasaporte'), ('0', 'Otros')]

    tipo = models.CharField(max_length=10, choices=TIPOS, default='CLIENTE')
    tipo_doc = models.CharField('Tipo doc.', max_length=1, choices=TIPOS_DOC, default='6')
    numero_doc = models.CharField('N° documento', max_length=15)
    nombre = models.CharField('Nombre / Razón social', max_length=200)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    zona = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField('Teléfono', max_length=50, blank=True)
    dias_credito = models.PositiveIntegerField('Días de crédito', default=0)
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Ubigeo de la dirección (para guías)')
    registro_mtc = models.CharField('Registro MTC', max_length=20, blank=True,
                                    help_text='Solo empresas de transporte (guía transportista)')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'cliente / proveedor'
        verbose_name_plural = 'clientes / proveedores'

    def __str__(self):
        return f'{self.numero_doc} - {self.nombre}'


class Producto(models.Model):
    TIPOS = [('BIEN', 'Bien (inventariable)'), ('SERVICIO', 'Servicio')]
    UNIDADES = [('NIU', 'Unidad'), ('KGM', 'Kilogramo'), ('LTR', 'Litro'), ('MTR', 'Metro'),
                ('BX', 'Caja'), ('PK', 'Paquete'), ('GLL', 'Galón'), ('ZZ', 'Servicio')]

    codigo = models.CharField('Código', max_length=30, unique=True)
    nombre = models.CharField(max_length=200)
    tipo = models.CharField(max_length=10, choices=TIPOS, default='BIEN')
    unidad = models.CharField(max_length=5, choices=UNIDADES, default='NIU')
    precio_venta = models.DecimalField('Precio venta (sin IGV)', max_digits=12, decimal_places=2, default=D0)
    costo_promedio = models.DecimalField('Costo promedio', max_digits=12, decimal_places=4, default=D0)
    stock = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    stock_minimo = models.DecimalField('Stock mínimo', max_digits=14, decimal_places=2, default=D0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'

    @property
    def es_inventariable(self):
        return self.tipo == 'BIEN'

    @property
    def valorizado(self):
        return r2(self.stock * self.costo_promedio)

    @property
    def bajo_minimo(self):
        return self.es_inventariable and self.stock <= self.stock_minimo

    def mover_stock(self, cantidad, referencia, costo=None, fecha=None, almacen=None, origen=''):
        """cantidad > 0 entrada, < 0 salida. Actualiza costo promedio en entradas y el stock del almacén."""
        cantidad = Decimal(cantidad)
        if not self.es_inventariable or cantidad == 0:
            return
        almacen = almacen or Almacen.principal()
        if cantidad > 0 and costo is not None:
            nuevo_stock = self.stock + cantidad
            if nuevo_stock > 0:
                total = self.stock * self.costo_promedio + cantidad * Decimal(costo)
                self.costo_promedio = (total / nuevo_stock).quantize(Decimal('0.0001'))
        self.stock += cantidad
        self.save(update_fields=['stock', 'costo_promedio'])
        sa, _ = StockAlmacen.objects.get_or_create(producto=self, almacen=almacen)
        sa.cantidad += cantidad
        sa.save(update_fields=['cantidad'])
        Kardex.objects.create(
            producto=self, almacen=almacen, fecha=fecha or timezone.localdate(),
            tipo='ENTRADA' if cantidad > 0 else 'SALIDA', cantidad=abs(cantidad),
            costo_unitario=costo if costo is not None else self.costo_promedio,
            costo_promedio=self.costo_promedio, saldo=self.stock, referencia=referencia, origen=origen,
        )


class Almacen(models.Model):
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Código de 6 dígitos (INEI)')
    codigo_sunat = models.CharField('Cód. establecimiento SUNAT', max_length=4, default='0000')
    es_principal = models.BooleanField('Principal', default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['-es_principal', 'nombre']
        verbose_name = 'almacén'
        verbose_name_plural = 'almacenes'

    def __str__(self):
        return self.nombre

    @classmethod
    def principal(cls):
        alm = cls.objects.filter(es_principal=True, activo=True).first() or cls.objects.filter(activo=True).first()
        if alm is None:
            alm = cls.objects.create(codigo='ALM01', nombre='Almacén principal', es_principal=True)
        return alm


class StockAlmacen(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='stocks')
    almacen = models.ForeignKey(Almacen, on_delete=models.CASCADE, related_name='stocks')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        unique_together = [('producto', 'almacen')]


class TipoCambio(models.Model):
    fecha = models.DateField(unique=True)
    compra = models.DecimalField(max_digits=8, decimal_places=3)
    venta = models.DecimalField(max_digits=8, decimal_places=3)
    fuente = models.CharField(max_length=20, default='SUNAT')

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'tipo de cambio'
        verbose_name_plural = 'tipos de cambio'

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y} C {self.compra} V {self.venta}'


class FacturacionConfig(models.Model):
    """Conexión con el OSE/PSE para comprobantes y guías electrónicas."""
    PROVEEDORES = [('NINGUNO', 'Sin facturación electrónica'), ('NUBEFACT', 'Nubefact (OSE/PSE)')]
    proveedor = models.CharField(max_length=10, choices=PROVEEDORES, default='NINGUNO')
    ruta = models.URLField('Ruta / URL de la API', max_length=300, blank=True,
                           help_text='La entrega el proveedor (ej. https://api.nubefact.com/api/v1/xxxx)')
    token = models.CharField(max_length=200, blank=True)
    envio_automatico = models.BooleanField('Enviar al emitir', default=False,
                                           help_text='Envía facturas, boletas, notas y guías al guardarlas')

    class Meta:
        verbose_name = 'configuración de facturación electrónica'

    @classmethod
    def actual(cls):
        return cls.objects.first() or cls.objects.create()

    @property
    def activa(self):
        return self.proveedor != 'NINGUNO' and bool(self.ruta and self.token)


class Kardex(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='kardex')
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, related_name='kardex')
    fecha = models.DateField()
    tipo = models.CharField(max_length=10)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    costo_promedio = models.DecimalField(max_digits=12, decimal_places=4, default=D0)
    origen = models.CharField(max_length=10, blank=True, help_text='VENTA, COMPRA, GUIA, AJUSTE')
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=4, default=D0)
    saldo = models.DecimalField(max_digits=14, decimal_places=2)
    referencia = models.CharField(max_length=120)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']


class Serie(models.Model):
    """Correlativos de comprobantes, cotizaciones, órdenes y vouchers."""
    TIPOS = TIPO_COMPROBANTE + [('OC', 'Orden de compra'), ('COT', 'Cotización / Proforma'),
                                ('PED', 'Orden de pedido'), ('VOU', 'Voucher caja/bancos'),
                                ('09', 'Guía de remisión remitente'), ('31', 'Guía de remisión transportista')]
    tipo = models.CharField(max_length=3, choices=TIPOS)
    serie = models.CharField(max_length=4)
    correlativo = models.PositiveIntegerField('Último correlativo', default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = [('tipo', 'serie')]
        ordering = ['tipo', 'serie']

    def __str__(self):
        return f'{self.serie} ({self.get_tipo_display()})'

    @classmethod
    def siguiente(cls, tipo, serie=None):
        """Devuelve (serie, numero) incrementando el correlativo de forma atómica."""
        with transaction.atomic():
            qs = cls.objects.select_for_update().filter(tipo=tipo, activo=True)
            if serie:
                qs = qs.filter(serie=serie)
            obj = qs.first()
            if obj is None:
                obj = cls.objects.create(tipo=tipo, serie=serie or tipo[:4].upper())
                obj = cls.objects.select_for_update().get(pk=obj.pk)
            obj.correlativo += 1
            obj.save(update_fields=['correlativo'])
            return obj.serie, str(obj.correlativo).zfill(8)


# ---------------------------------------------------------------- bases abstractas
class ItemBase(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, null=True, blank=True)
    descripcion = models.CharField('Descripción', max_length=250)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('1'))
    precio_unitario = models.DecimalField('Valor unit. (sin IGV)', max_digits=14, decimal_places=4, default=D0)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.subtotal = r2(self.cantidad * self.precio_unitario)
        super().save(*args, **kwargs)


class TotalesMixin(models.Model):
    tipo_operacion = models.CharField('Operación', max_length=12, choices=TIPO_OPERACION, default='GRAVADA')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    tipo_cambio = models.DecimalField('T.C.', max_digits=8, decimal_places=3, default=Decimal('1.000'))
    base_imponible = models.DecimalField('Base imponible', max_digits=14, decimal_places=2, default=D0)
    no_gravado = models.DecimalField('Exon./Inaf./Export.', max_digits=14, decimal_places=2, default=D0)
    igv = models.DecimalField('IGV', max_digits=14, decimal_places=2, default=D0)
    icbper = models.DecimalField('ICBPER', max_digits=10, decimal_places=2, default=D0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        abstract = True

    @property
    def simbolo(self):
        return 'US$' if self.moneda == 'USD' else 'S/'

    @property
    def lleva_igv(self):
        return self.tipo_operacion == 'GRAVADA' and getattr(self, 'tipo_comprobante', '01') not in ('02', '00')

    def calcular_totales(self):
        subtotal = sum((i.subtotal for i in self.items.all()), D0)
        if self.tipo_operacion == 'GRATUITA':
            self.base_imponible = self.no_gravado = self.igv = D0
        elif self.lleva_igv:
            self.base_imponible = r2(subtotal)
            self.no_gravado = D0
            self.igv = r2(subtotal * Empresa.actual().igv_tasa / 100)
        else:
            self.base_imponible = D0
            self.no_gravado = r2(subtotal)
            self.igv = D0
        self.total = self.base_imponible + self.no_gravado + self.igv + self.icbper


class DocumentoBase(TotalesMixin):
    """Cotizaciones / órdenes de compra (documentos no tributarios)."""
    numero = models.CharField(max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT)
    condicion_pago = models.CharField('Condición de pago', max_length=100, blank=True)
    glosa = models.TextField('Observaciones', blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_DOCUMENTO, default='PENDIENTE')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ['-fecha', '-id']

    def __str__(self):
        return self.numero


class ComprobanteBase(TotalesMixin):
    """Comprobante de pago SUNAT (compras y ventas)."""
    tipo_comprobante = models.CharField('Tipo', max_length=2, choices=TIPO_COMPROBANTE, default='01')
    serie = models.CharField(max_length=4, blank=True)
    numero = models.CharField('Número', max_length=10, blank=True)
    fecha_emision = models.DateField('Fecha emisión', default=timezone.localdate)
    fecha_vencimiento = models.DateField('Fecha vencimiento', null=True, blank=True)
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6, blank=True)
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT)
    forma_pago = models.CharField('Forma de pago', max_length=8, choices=FORMA_PAGO, default='CONTADO')
    detraccion_pct = models.DecimalField('% Detracción', max_digits=5, decimal_places=2, default=D0)
    detraccion_monto = models.DecimalField('Detracción', max_digits=14, decimal_places=2, default=D0)
    retencion_pct = models.DecimalField('% Retención', max_digits=5, decimal_places=2, default=D0)
    retencion_monto = models.DecimalField('Retención', max_digits=14, decimal_places=2, default=D0)
    percepcion_pct = models.DecimalField('% Percepción', max_digits=5, decimal_places=2, default=D0)
    percepcion_monto = models.DecimalField('Percepción', max_digits=14, decimal_places=2, default=D0)
    glosa = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_COMPROBANTE, default='REGISTRADO')
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Almacén')
    stock_aplicado = models.BooleanField('Movió almacén', default=False, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ['-fecha_emision', '-id']

    def __str__(self):
        return f'{self.get_tipo_comprobante_display()} {self.numero_completo}'

    @property
    def numero_completo(self):
        return f'{self.serie}-{self.numero}' if self.serie else self.numero

    @property
    def es_nota_credito(self):
        return self.tipo_comprobante == '07'

    @property
    def signo(self):
        return -1 if self.es_nota_credito else 1

    def calcular_totales(self):
        super().calcular_totales()
        self.detraccion_monto = r2(self.total * self.detraccion_pct / 100)
        self.retencion_monto = r2(self.total * self.retencion_pct / 100)
        self.percepcion_monto = r2(self.total * self.percepcion_pct / 100)

    def save(self, *args, **kwargs):
        if not self.periodo and self.fecha_emision:
            self.periodo = self.fecha_emision.strftime('%Y%m')
        if not self.fecha_vencimiento:
            self.fecha_vencimiento = self.fecha_emision
        super().save(*args, **kwargs)

    # ---- saldos
    @property
    def total_documento(self):
        """Total ajustado por notas de crédito/débito que lo referencian."""
        notas = self.notas.filter(estado='REGISTRADO')
        nc = notas.filter(tipo_comprobante='07').aggregate(s=Sum('total'))['s'] or D0
        nd = notas.filter(tipo_comprobante='08').aggregate(s=Sum('total'))['s'] or D0
        return self.total - nc + nd

    @property
    def neto(self):
        return self.total_documento - self.retencion_monto + self.percepcion_monto

    @property
    def pagado(self):
        return self.movimientos.aggregate(s=Sum('monto'))['s'] or D0

    @property
    def saldo(self):
        if self.estado == 'ANULADO' or self.tipo_comprobante in ('07', '08') and self.doc_referencia_id:
            return D0
        return self.neto - self.pagado

    @property
    def dias_vencido(self):
        if not self.fecha_vencimiento:
            return 0
        return (timezone.localdate() - self.fecha_vencimiento).days

    # ---- almacén
    def _signo_stock(self):
        raise NotImplementedError

    def _costo_entrada(self, item):
        return None

    def aplicar_stock(self):
        if self.stock_aplicado or self.estado == 'ANULADO':
            return
        if not self.almacen_id:
            self.almacen = Almacen.principal()
        signo = self._signo_stock()
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                costo = self._costo_entrada(item) if signo > 0 else None
                item.producto.mover_stock(signo * item.cantidad, str(self), costo=costo, fecha=self.fecha_emision,
                                          almacen=self.almacen, origen=self._meta.model_name.upper())
        self.stock_aplicado = True
        self.save(update_fields=['stock_aplicado', 'almacen'])

    def revertir_stock(self):
        if not self.stock_aplicado:
            return
        signo = -self._signo_stock()
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                item.producto.mover_stock(signo * item.cantidad, f'Reversión {self}', fecha=timezone.localdate(),
                                          almacen=self.almacen, origen=self._meta.model_name.upper())
        self.stock_aplicado = False
        self.save(update_fields=['stock_aplicado'])


class ElectronicoMixin(models.Model):
    """Estado del envío al OSE/SUNAT (comprobantes de venta y guías)."""
    ESTADOS_SUNAT = [('NO_ENVIADO', 'No enviado'), ('PENDIENTE', 'Pendiente SUNAT'), ('ACEPTADO', 'Aceptado'),
                     ('RECHAZADO', 'Rechazado'), ('ERROR', 'Error de envío'), ('BAJA', 'Comunicado de baja')]
    estado_sunat = models.CharField('Estado SUNAT', max_length=10, choices=ESTADOS_SUNAT, default='NO_ENVIADO')
    sunat_descripcion = models.TextField('Respuesta SUNAT', blank=True)
    enlace_pdf = models.URLField(max_length=500, blank=True)
    enlace_xml = models.URLField(max_length=500, blank=True)
    enlace_cdr = models.URLField(max_length=500, blank=True)
    codigo_hash = models.CharField(max_length=100, blank=True)
    cadena_qr = models.TextField(blank=True)
    fecha_envio = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
