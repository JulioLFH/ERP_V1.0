from decimal import Decimal

from django.db import models
from django.utils import timezone

from core.models import Almacen, ElectronicoMixin, Producto, Tercero

D0 = Decimal('0')

# Catálogo 20 SUNAT — motivos de traslado
MOTIVOS_TRASLADO = [
    ('01', '01 Venta'),
    ('14', '14 Venta sujeta a confirmación del comprador'),
    ('03', '03 Venta con entrega a terceros'),
    ('02', '02 Compra'),
    ('04', '04 Traslado entre establecimientos de la misma empresa'),
    ('18', '18 Traslado emisor itinerante CP'),
    ('05', '05 Consignación'),
    ('06', '06 Devolución'),
    ('07', '07 Recojo de bienes transformados'),
    ('08', '08 Importación'),
    ('09', '09 Exportación'),
    ('17', '17 Traslado de bienes para transformación'),
    ('19', '19 Traslado a zona primaria'),
    ('13', '13 Otros'),
]
MODALIDADES = [('01', 'Transporte público'), ('02', 'Transporte privado')]


class Vehiculo(models.Model):
    placa = models.CharField(max_length=10, unique=True)
    marca = models.CharField(max_length=50, blank=True)
    modelo = models.CharField(max_length=50, blank=True)
    certificado = models.CharField('TUCE / Certificado MTC', max_length=30, blank=True,
                                   help_text='Tarjeta Única de Circulación (si aplica)')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['placa']
        verbose_name = 'vehículo'

    def __str__(self):
        return f'{self.placa} {self.marca}'.strip()


class Conductor(models.Model):
    TIPOS_DOC = [('1', 'DNI'), ('4', 'Carné de extranjería'), ('7', 'Pasaporte')]
    tipo_doc = models.CharField('Tipo doc.', max_length=1, choices=TIPOS_DOC, default='1')
    numero_doc = models.CharField('N° documento', max_length=15, unique=True)
    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    licencia = models.CharField('N° licencia de conducir', max_length=20)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['apellidos', 'nombres']
        verbose_name_plural = 'conductores'

    def __str__(self):
        return f'{self.apellidos}, {self.nombres} ({self.licencia})'


class GuiaRemision(ElectronicoMixin):
    TIPOS = [('09', 'Guía de remisión remitente'), ('31', 'Guía de remisión transportista')]
    EFECTOS_STOCK = [
        ('NINGUNO', 'No mueve almacén'),
        ('SALIDA', 'Salida del almacén de origen'),
        ('ENTRADA', 'Entrada al almacén de destino'),
        ('TRASLADO', 'Traslado entre almacenes (origen → destino)'),
    ]
    ESTADOS = [('EMITIDA', 'Emitida'), ('ANULADA', 'Anulada')]
    UNIDADES_PESO = [('KGM', 'Kilogramos'), ('TNE', 'Toneladas')]

    tipo = models.CharField(max_length=2, choices=TIPOS, default='09')
    serie = models.CharField(max_length=4, blank=True)
    numero = models.CharField('Número', max_length=10, blank=True)
    fecha_emision = models.DateField('Fecha emisión', default=timezone.localdate)
    fecha_traslado = models.DateField('Inicio de traslado', default=timezone.localdate)
    motivo_traslado = models.CharField('Motivo de traslado', max_length=2, choices=MOTIVOS_TRASLADO, default='01')
    descripcion_motivo = models.CharField('Descripción del motivo', max_length=100, blank=True,
                                          help_text='Obligatorio si el motivo es 13 Otros')
    modalidad = models.CharField(max_length=2, choices=MODALIDADES, default='02')

    remitente = models.ForeignKey(Tercero, on_delete=models.PROTECT, null=True, blank=True, related_name='guias_remitente',
                                  help_text='Solo guía transportista: quien envía la mercadería')
    destinatario = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='guias_destinatario')
    transportista = models.ForeignKey(Tercero, on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='guias_transportista',
                                      help_text='Empresa de transporte (modalidad pública)')
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Vehículo')
    conductor = models.ForeignKey(Conductor, on_delete=models.PROTECT, null=True, blank=True)

    partida_ubigeo = models.CharField('Ubigeo partida', max_length=6)
    partida_direccion = models.CharField('Dirección de partida', max_length=250)
    llegada_ubigeo = models.CharField('Ubigeo llegada', max_length=6)
    llegada_direccion = models.CharField('Dirección de llegada', max_length=250)

    peso_bruto = models.DecimalField('Peso bruto total', max_digits=12, decimal_places=3, default=Decimal('1'))
    unidad_peso = models.CharField('Unidad de peso', max_length=3, choices=UNIDADES_PESO, default='KGM')
    numero_bultos = models.PositiveIntegerField('N° de bultos', default=1)

    venta = models.ForeignKey('ventas.Venta', on_delete=models.SET_NULL, null=True, blank=True, related_name='guias',
                              verbose_name='Comprobante de venta relacionado')
    compra = models.ForeignKey('compras.Compra', on_delete=models.SET_NULL, null=True, blank=True, related_name='guias',
                               verbose_name='Comprobante de compra relacionado')
    doc_relacionado = models.CharField('Otro doc. relacionado', max_length=40, blank=True,
                                       help_text='Formato: TIPO SERIE-NÚMERO (ej. 09 T001-00000015)')

    efecto_stock = models.CharField('Efecto en almacén', max_length=8, choices=EFECTOS_STOCK, default='NINGUNO')
    almacen_origen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='guias_salida', verbose_name='Almacén de origen')
    almacen_destino = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='guias_entrada', verbose_name='Almacén de destino')
    stock_aplicado = models.BooleanField(default=False, editable=False)

    observaciones = models.TextField(blank=True)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='EMITIDA')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_emision', '-id']
        unique_together = [('tipo', 'serie', 'numero')]
        verbose_name = 'guía de remisión'
        verbose_name_plural = 'guías de remisión'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero_completo}'

    @property
    def numero_completo(self):
        return f'{self.serie}-{self.numero}'

    def calcular_totales(self):
        """Compatibilidad con guardar_documento (las guías no tienen importes)."""

    # ---- almacén
    def _movimientos_stock(self):
        """[(almacen, signo)] según el efecto configurado."""
        return {
            'SALIDA': [(self.almacen_origen, -1)],
            'ENTRADA': [(self.almacen_destino, 1)],
            'TRASLADO': [(self.almacen_origen, -1), (self.almacen_destino, 1)],
        }.get(self.efecto_stock, [])

    def aplicar_stock(self):
        if self.stock_aplicado or self.estado == 'ANULADA' or self.tipo != '09':
            return
        movs = self._movimientos_stock()
        if not movs:
            return
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                for almacen, signo in movs:
                    item.producto.mover_stock(signo * item.cantidad, str(self), fecha=self.fecha_traslado,
                                              almacen=almacen or Almacen.principal())
        self.stock_aplicado = True
        self.save(update_fields=['stock_aplicado'])

    def revertir_stock(self):
        if not self.stock_aplicado:
            return
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                for almacen, signo in self._movimientos_stock():
                    item.producto.mover_stock(-signo * item.cantidad, f'Reversión {self}',
                                              fecha=timezone.localdate(), almacen=almacen or Almacen.principal())
        self.stock_aplicado = False
        self.save(update_fields=['stock_aplicado'])


class GuiaItem(models.Model):
    documento = models.ForeignKey(GuiaRemision, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, null=True, blank=True)
    descripcion = models.CharField('Descripción', max_length=250)
    unidad = models.CharField('U.M.', max_length=5, choices=Producto.UNIDADES, default='NIU')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('1'))
