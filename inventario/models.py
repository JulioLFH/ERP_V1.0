"""Operaciones de inventario (notas de ingreso, salida, traslado y manufactura) al estilo Odoo.

Cada operación pertenece a un TipoOperacion configurable (Recepción de compras, Devolución de clientes,
Consumo interno, Traslado a tránsito, ...) que define su clase, el documento de origen que exige, si
requiere costo, la cuenta contable de contrapartida y el código de la tabla 12 de SUNAT.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Almacen, Producto, Tercero

D0 = Decimal('0')

CLASES = [
    ('INGRESO', 'Ingreso al almacén'),
    ('SALIDA', 'Salida del almacén'),
    ('TRASLADO', 'Traslado directo entre almacenes'),
    ('TRANSITO_ENVIO', 'Traslado a tránsito (envío)'),
    ('TRANSITO_RECEPCION', 'Recepción de tránsito'),
    ('MANUFACTURA', 'Manufactura (consume insumos y produce)'),
]
ORIGENES = [
    ('', 'Ninguno'),
    ('ORDEN_COMPRA', 'Orden de compra'),
    ('COMPRA', 'Factura de compra'),
    ('VENTA', 'Factura / boleta de venta'),
    ('TRANSITO', 'Traslado a tránsito pendiente'),
]
# Tabla 12 SUNAT - tipo de operación (registro de inventario permanente)
CODIGOS_SUNAT = [
    ('01', '01 Venta'), ('02', '02 Compra'), ('05', '05 Devolución recibida'), ('06', '06 Devolución entregada'),
    ('09', '09 Donación'), ('10', '10 Salida a producción'), ('11', '11 Transferencia entre almacenes'),
    ('12', '12 Retiro'), ('13', '13 Mermas'), ('14', '14 Desmedros'), ('15', '15 Destrucción'),
    ('16', '16 Saldo inicial'), ('19', '19 Entrada de producción'), ('21', '21 Entrada por transferencia'),
    ('28', '28 Ajuste por diferencia de inventario'), ('99', '99 Otros'),
]
PREFIJOS = {'INGRESO': 'NI', 'SALIDA': 'NS', 'TRASLADO': 'NT', 'TRANSITO_ENVIO': 'NT', 'TRANSITO_RECEPCION': 'NT',
            'MANUFACTURA': 'MF'}


class TipoOperacion(models.Model):
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=80)
    clase = models.CharField(max_length=20, choices=CLASES)
    origen = models.CharField('Documento de origen', max_length=15, choices=ORIGENES, blank=True)
    requiere_costo = models.BooleanField('Pide costo unitario', default=False,
                                         help_text='Solo ingresos sin documento de origen (saldo inicial, ajustes)')
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                        blank=True, related_name='+', limit_choices_to={'imputable': True},
                                        verbose_name='Cuenta contable (contrapartida)',
                                        help_text='Vacío en traslados y manufactura (no afectan resultados)')
    codigo_sunat = models.CharField('Tabla 12 SUNAT', max_length=2, choices=CODIGOS_SUNAT, default='99')
    codigo_sunat_ingreso = models.CharField('Tabla 12 SUNAT (ingreso)', max_length=2, choices=CODIGOS_SUNAT,
                                            blank=True, help_text='Traslados y manufactura: código del ingreso')
    almacen_destino = models.ForeignKey(Almacen, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                        verbose_name='Almacén de destino por defecto')
    almacen_origen = models.ForeignKey(Almacen, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                       verbose_name='Almacén de origen por defecto')
    icono = models.CharField(max_length=30, default='bi-box-seam')
    orden = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['orden', 'nombre']
        verbose_name = 'tipo de operación de inventario'
        verbose_name_plural = 'tipos de operación de inventario'

    def __str__(self):
        return self.nombre

    @property
    def es_ingreso(self):
        return self.clase in ('INGRESO', 'TRANSITO_RECEPCION')

    @property
    def es_salida(self):
        return self.clase == 'SALIDA'

    @property
    def usa_origen_almacen(self):
        return self.clase in ('SALIDA', 'TRASLADO', 'TRANSITO_ENVIO', 'MANUFACTURA')

    @property
    def usa_destino_almacen(self):
        return self.clase in ('INGRESO', 'TRASLADO', 'TRANSITO_ENVIO', 'TRANSITO_RECEPCION', 'MANUFACTURA')


class Operacion(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('CONFIRMADO', 'Confirmado'), ('ANULADO', 'Anulado')]

    numero = models.CharField('Número', max_length=20, blank=True, editable=False)
    tipo = models.ForeignKey(TipoOperacion, on_delete=models.PROTECT, related_name='operaciones')
    fecha = models.DateField(default=timezone.localdate)
    almacen_origen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='operaciones_salida', verbose_name='Almacén de origen')
    almacen_destino = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='operaciones_ingreso', verbose_name='Almacén de destino')
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT, null=True, blank=True,
                                verbose_name='Proveedor / cliente')
    orden_compra = models.ForeignKey('compras.OrdenCompra', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='recepciones')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='operaciones_inventario')
    venta = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='operaciones_inventario')
    envio = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='recepciones',
                              verbose_name='Traslado a tránsito que se recibe')
    referencia = models.CharField('Documento de referencia', max_length=60, blank=True,
                                  help_text='Guía del proveedor, orden de trabajo, acta de destrucción, etc.')
    glosa = models.TextField('Observaciones', blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR')
    stock_aplicado = models.BooleanField(default=False, editable=False)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    confirmado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    confirmado_en = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.CharField(max_length=250, blank=True)
    anulado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    anulado_en = models.DateTimeField(null=True, blank=True)
    conformidad_enviada_en = models.DateTimeField('Conformidad enviada al proveedor', null=True, blank=True)
    conformidad_enviada_a = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'operación de inventario'
        verbose_name_plural = 'operaciones de inventario'

    def __str__(self):
        return f'{self.tipo.nombre} {self.numero or "(borrador)"}'

    @property
    def documento_origen(self):
        return self.orden_compra or self.compra or self.venta or self.envio

    @property
    def valor_total(self):
        return sum((i.valor for i in self.items.all()), D0)


class CierreKardex(models.Model):
    """Cierre mensual del kardex: ningún movimiento de almacén con fecha hasta fecha_corte.

    Guarda la foto del inventario valorizado al cierre (SaldoCierre). Solo se reabre el último cierre vigente.
    """
    ESTADOS = [('CERRADO', 'Cerrado'), ('REABIERTO', 'Reabierto')]

    periodo = models.CharField('Periodo (AAAAMM)', max_length=6)
    fecha_corte = models.DateField()
    estado = models.CharField(max_length=10, choices=ESTADOS, default='CERRADO')
    unidades = models.DecimalField(max_digits=16, decimal_places=2, default=D0)
    valor_total = models.DecimalField('Inventario valorizado S/', max_digits=16, decimal_places=2, default=D0)
    observaciones = models.CharField(max_length=250, blank=True)
    cerrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    cerrado_en = models.DateTimeField(auto_now_add=True)
    reabierto_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    reabierto_en = models.DateTimeField(null=True, blank=True)
    motivo_reapertura = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['-fecha_corte', '-id']
        verbose_name = 'cierre de kardex'
        verbose_name_plural = 'cierres de kardex'

    def __str__(self):
        return f'Cierre de kardex {self.periodo_texto}'

    @property
    def periodo_texto(self):
        return f'{self.periodo[4:]}/{self.periodo[:4]}'


class SaldoCierre(models.Model):
    cierre = models.ForeignKey(CierreKardex, on_delete=models.CASCADE, related_name='saldos')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    costo = models.DecimalField(max_digits=14, decimal_places=4)
    valor = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        ordering = ['producto__codigo', 'almacen__nombre']


class OperacionItem(models.Model):
    ROLES = [('', '—'), ('INSUMO', 'Insumo (se consume)'), ('PRODUCTO', 'Producto terminado (se produce)')]

    operacion = models.ForeignKey(Operacion, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, limit_choices_to={'tipo': 'BIEN'})
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    costo_unitario = models.DecimalField('Costo unit. S/', max_digits=14, decimal_places=4, null=True, blank=True)
    rol = models.CharField(max_length=8, choices=ROLES, blank=True)
    observacion = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['id']

    @property
    def valor(self):
        return (self.cantidad * (self.costo_unitario or self.producto.costo_promedio)).quantize(Decimal('0.01'))
