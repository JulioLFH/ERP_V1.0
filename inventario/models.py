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
    requiere_sustento = models.BooleanField(
        'Exige sustento', default=False,
        help_text='No se confirma sin un documento adjunto (acta de inventario, de destrucción, informe, etc.)')
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
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', verbose_name='Centro de costo',
                                     help_text='Consumos y salidas: el gasto se carga a este centro')
    cuenta_gasto = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', verbose_name='Cuenta de gasto',
                                     limit_choices_to={'imputable': True},
                                     help_text='Vacío = la cuenta del tipo de operación')
    requerimiento = models.ForeignKey('RequerimientoInterno', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='atenciones', editable=False)
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
    costo_adicional = models.DecimalField(
        'Costo de conversión S/', max_digits=14, decimal_places=2, default=D0,
        help_text='Manufactura: mano de obra y costos indirectos que se suman al costo de los insumos')
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
    ROLES = [('', '—'), ('INSUMO', 'Insumo (se consume)'), ('PRODUCTO', 'Producto terminado (se produce)'),
             ('SUBPROD', 'Subproducto o coproducto (entra a su costo asignado)')]

    operacion = models.ForeignKey(Operacion, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, limit_choices_to={'tipo': 'BIEN'})
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    costo_unitario = models.DecimalField('Costo unit. S/', max_digits=14, decimal_places=4, null=True, blank=True)
    rol = models.CharField(max_length=8, choices=ROLES, blank=True)
    lote = models.CharField('Lote / series', max_length=400, blank=True,
                            help_text='Productos con lote: el código del lote. Con serie: los números separados por '
                                      'coma. Vacío en salidas = sale lo que vence primero')
    vencimiento = models.DateField('Vence', null=True, blank=True)
    observacion = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['id']

    @property
    def codigos_lote(self):
        import re
        return [c.strip().upper() for c in re.split(r'[,;\n]+', self.lote or '') if c.strip()]

    def lotes_para(self, cantidad):
        """[(código, cantidad, vencimiento)] para mover_stock, o None si la línea no indica lotes."""
        if not self.producto.control or not self.codigos_lote:
            return None
        if self.producto.control == 'SERIE':
            return [(c, Decimal('1'), self.vencimiento) for c in self.codigos_lote]
        return [(self.codigos_lote[0], abs(cantidad), self.vencimiento)]

    @property
    def valor(self):
        return (self.cantidad * (self.costo_unitario or self.producto.costo_promedio)).quantize(Decimal('0.01'))


class RequerimientoInterno(models.Model):
    """Pedido de materiales de un área al almacén, contra su centro de costo: se aprueba, se atiende (total o
    parcial), los faltantes pasan a compras y el consumo va al gasto y centro de costo correctos."""
    ESTADOS = [('BORRADOR', 'Borrador'), ('ENVIADO', 'Por aprobar'), ('APROBADO', 'Aprobado (por atender)'),
               ('PARCIAL', 'Atendido parcialmente'), ('ATENDIDO', 'Atendido'), ('RECHAZADO', 'Rechazado'),
               ('ANULADO', 'Anulado')]

    numero = models.CharField('Número', max_length=20, blank=True, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    fecha_requerida = models.DateField('Se necesita el', null=True, blank=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, related_name='+',
                                     verbose_name='Centro de costo')
    cuenta_gasto = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', verbose_name='Cuenta de gasto',
                                     limit_choices_to={'imputable': True, 'codigo__startswith': '6'},
                                     help_text='Ej. 6561 suministros, 6343 mantenimiento. Vacío = consumo interno')
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, related_name='+', verbose_name='Almacén')
    motivo = models.CharField('Para qué se necesita', max_length=250)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR')
    solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    related_name='+')
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    aprobado_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.CharField('Motivo de rechazo / anulación', max_length=250, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'requerimiento interno'
        verbose_name_plural = 'requerimientos internos'

    def __str__(self):
        return f'Requerimiento {self.numero or "(borrador)"}'

    @property
    def pendiente(self):
        return sum((i.pendiente for i in self.items.all()), D0)


class OlaPicking(models.Model):
    """Ola de preparación: junta varios pedidos de venta para recorrer el almacén una sola vez (por ubicación) y
    luego separar lo preparado por pedido."""
    ESTADOS = [('ABIERTA', 'Por preparar'), ('PREPARADA', 'Preparada'), ('ANULADA', 'Anulada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, related_name='+')
    pedidos = models.ManyToManyField('ventas.Cotizacion', related_name='olas')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ABIERTA')
    observaciones = models.CharField(max_length=200, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    preparado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    preparado_en = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'ola de picking'
        verbose_name_plural = 'olas de picking'

    def __str__(self):
        return f'Ola {self.numero}'


class LineaOla(models.Model):
    ola = models.ForeignKey(OlaPicking, on_delete=models.CASCADE, related_name='lineas')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    ubicacion = models.CharField('Ubicación', max_length=20, blank=True)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    preparada = models.DecimalField('Cantidad preparada', max_digits=14, decimal_places=2, null=True, blank=True)
    detalle = models.JSONField(default=dict, help_text='{número de pedido: cantidad}')

    class Meta:
        ordering = ['ubicacion', 'producto__nombre']


class ConteoCiclico(models.Model):
    """Conteo cíclico: se cuentan pocos productos cada vez según su clase ABC (los A más seguido). Al cerrarlo, las
    diferencias se ajustan con su acta de conteo como sustento."""
    ESTADOS = [('ABIERTO', 'En conteo'), ('CERRADO', 'Cerrado'), ('ANULADO', 'Anulado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, related_name='+')
    estado = models.CharField(max_length=8, choices=ESTADOS, default='ABIERTO')
    ajuste_ingreso = models.ForeignKey(Operacion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    ajuste_salida = models.ForeignKey(Operacion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    cerrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    cerrado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'conteo cíclico'
        verbose_name_plural = 'conteos cíclicos'

    def __str__(self):
        return f'Conteo {self.numero}'


class ConteoItem(models.Model):
    conteo = models.ForeignKey(ConteoCiclico, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    clase = models.CharField('Clase ABC', max_length=1)
    ubicacion = models.CharField(max_length=20, blank=True)
    sistema = models.DecimalField('Stock del sistema', max_digits=14, decimal_places=2)
    contado = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ['ubicacion', 'producto__nombre']

    @property
    def diferencia(self):
        return None if self.contado is None else self.contado - self.sistema


class RequerimientoItem(models.Model):
    requerimiento = models.ForeignKey(RequerimientoInterno, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, limit_choices_to={'tipo': 'BIEN'})
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    cantidad_atendida = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    orden_compra = models.ForeignKey('compras.OrdenCompra', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    observacion = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['id']

    @property
    def pendiente(self):
        return max(self.cantidad - self.cantidad_atendida, D0)
