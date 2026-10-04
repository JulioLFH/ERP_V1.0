"""Portal de proveedores: accesos, facturas registradas por el proveedor contra sus órdenes de compra."""
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import Producto, Tercero, r2

D0 = Decimal('0')


class AccesoProveedor(models.Model):
    """Usuario del portal: solo ve las órdenes y facturas de su empresa (tercero)."""
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='acceso_proveedor')
    tercero = models.ForeignKey(Tercero, on_delete=models.CASCADE, related_name='accesos_portal',
                                verbose_name='Proveedor')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'acceso al portal de proveedores'
        verbose_name_plural = 'accesos al portal de proveedores'

    def __str__(self):
        return f'{self.usuario.username} ({self.tercero.nombre})'


class FacturaProveedor(models.Model):
    ESTADOS = [('ENVIADA', 'Enviada, por revisar'), ('APROBADA', 'Aprobada y registrada'),
               ('RECHAZADA', 'Rechazada')]
    ESTADOS_SUNAT = [('SIN_VALIDAR', 'Sin validar'), ('VALIDO', 'Válido en SUNAT'),
                     ('OBSERVADO', 'Observado por SUNAT'), ('ERROR', 'No se pudo consultar')]
    TIPOS = [('01', 'Factura')]

    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='facturas_portal',
                                verbose_name='Proveedor')
    orden_compra = models.ForeignKey('compras.OrdenCompra', on_delete=models.PROTECT, related_name='facturas_portal')
    tipo_comprobante = models.CharField('Tipo', max_length=2, choices=TIPOS, default='01')
    serie = models.CharField(max_length=4)
    numero = models.CharField('Número', max_length=8)
    fecha_emision = models.DateField('Fecha de emisión')
    moneda = models.CharField(max_length=3, default='PEN')
    base_imponible = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    no_gravado = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    igv = models.DecimalField('IGV', max_digits=14, decimal_places=2, default=D0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    observaciones = models.CharField('Comentario del proveedor', max_length=300, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ENVIADA')
    estado_sunat = models.CharField('Validación SUNAT', max_length=12, choices=ESTADOS_SUNAT, default='SIN_VALIDAR')
    sunat_detalle = models.CharField('Respuesta SUNAT', max_length=300, blank=True)
    sunat_consultado_en = models.DateTimeField(null=True, blank=True)
    compra = models.OneToOneField('compras.Compra', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='factura_portal')
    enviada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    revisado_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.CharField('Motivo del rechazo', max_length=300, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'factura del portal de proveedores'
        verbose_name_plural = 'facturas del portal de proveedores'

    def __str__(self):
        return f'Factura {self.serie}-{self.numero}'

    @property
    def numero_completo(self):
        return f'{self.serie}-{self.numero}'

    @property
    def simbolo(self):
        return 'US$' if self.moneda == 'USD' else 'S/'

    def calcular_totales(self, tasa_igv, gravada=True):
        subtotal = sum((i.subtotal for i in self.items.all()), D0)
        if gravada:
            self.base_imponible, self.no_gravado = r2(subtotal), D0
            self.igv = r2(subtotal * tasa_igv / 100)
        else:
            self.base_imponible, self.no_gravado, self.igv = D0, r2(subtotal), D0
        self.total = self.base_imponible + self.no_gravado + self.igv


class FacturaProveedorItem(models.Model):
    factura = models.ForeignKey(FacturaProveedor, on_delete=models.CASCADE, related_name='items')
    oc_item = models.ForeignKey('compras.OrdenCompraItem', on_delete=models.SET_NULL, null=True, related_name='+')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, null=True, blank=True)
    descripcion = models.CharField(max_length=250)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    precio_unitario = models.DecimalField('Valor unit. (sin IGV)', max_digits=14, decimal_places=4)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    cantidad_esperada = models.DecimalField('Cantidad recibida / pedida', max_digits=14, decimal_places=2,
                                            default=D0)
    precio_orden = models.DecimalField('Precio de la orden', max_digits=14, decimal_places=4, default=D0)

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        self.subtotal = r2(self.cantidad * self.precio_unitario)
        super().save(*args, **kwargs)
