"""Historial del sistema anterior (Odoo) para consulta: kardex, asientos, órdenes de fabricación y sus consumos.

Son copias de lo que registró el sistema anterior: no mueven el stock, ni la contabilidad, ni el costo de Ceiba (que
parten de los saldos iniciales migrados). Sirven para consultar, filtrar y exportar.
"""
from decimal import Decimal

from django.db import models

D0 = Decimal('0')


class MovimientoAnterior(models.Model):
    """Línea del kardex del sistema anterior."""
    id_origen = models.BigIntegerField('Id en el sistema anterior', unique=True)
    fecha = models.DateField(db_index=True)
    almacen = models.CharField('Almacén', max_length=80)
    transaccion = models.CharField('Transacción', max_length=60)
    documento = models.CharField(max_length=60, blank=True, db_index=True)
    orden_fabricacion = models.CharField('Orden de fabricación', max_length=40, blank=True)
    orden_compra = models.CharField('Orden de compra', max_length=40, blank=True)
    guia = models.CharField('Guía', max_length=40, blank=True)
    comprobante = models.CharField(max_length=40, blank=True)
    producto = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    codigo = models.CharField('Código', max_length=30, db_index=True)
    descripcion = models.CharField(max_length=200)
    unidad = models.CharField(max_length=20, blank=True)
    lote = models.CharField(max_length=60, blank=True)
    vencimiento = models.DateField(null=True, blank=True)
    ingreso = models.DecimalField(max_digits=16, decimal_places=4, default=D0)
    salida = models.DecimalField(max_digits=16, decimal_places=4, default=D0)
    costo = models.DecimalField('Costo unitario', max_digits=16, decimal_places=6, default=D0)
    contacto_doc = models.CharField(max_length=20, blank=True)
    contacto = models.CharField(max_length=200, blank=True)
    usuario = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ['-fecha', '-id_origen']
        indexes = [models.Index(fields=['codigo', 'fecha']), models.Index(fields=['almacen', 'fecha'])]
        verbose_name = 'movimiento del sistema anterior'


class AsientoAnterior(models.Model):
    """Línea de asiento contable del sistema anterior."""
    diario = models.CharField(max_length=80)
    voucher = models.CharField(max_length=30, db_index=True)
    fecha = models.DateField(db_index=True)
    periodo = models.CharField(max_length=6, db_index=True)
    contacto_doc = models.CharField(max_length=20, blank=True)
    contacto = models.CharField(max_length=200, blank=True)
    tipo_comprobante = models.CharField(max_length=60, blank=True)
    comprobante = models.CharField(max_length=40, blank=True, db_index=True)
    glosa = models.CharField(max_length=250, blank=True)
    cuenta = models.CharField(max_length=12, db_index=True)
    cuenta_nombre = models.CharField(max_length=200, blank=True)
    debe = models.DecimalField(max_digits=16, decimal_places=2, default=D0)
    haber = models.DecimalField(max_digits=16, decimal_places=2, default=D0)
    centro_costo = models.CharField('Centro de costo', max_length=80, blank=True)
    usuario = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ['fecha', 'voucher', 'id']
        indexes = [models.Index(fields=['cuenta', 'periodo'])]
        verbose_name = 'asiento del sistema anterior'


class OrdenFabricacionAnterior(models.Model):
    referencia = models.CharField(max_length=40, unique=True)
    producto = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    codigo = models.CharField('Código', max_length=30, db_index=True)
    descripcion = models.CharField(max_length=200)
    lista_materiales = models.CharField('Lista de materiales', max_length=200, blank=True)
    lote = models.CharField(max_length=60, blank=True)
    inicio = models.DateTimeField(null=True, blank=True)
    fin = models.DateTimeField(null=True, blank=True)
    fecha_kardex = models.DateTimeField(null=True, blank=True, db_index=True)
    cantidad = models.DecimalField('Cantidad a producir', max_digits=16, decimal_places=4, default=D0)
    producida = models.DecimalField('Cantidad producida', max_digits=16, decimal_places=4, default=D0)
    unidad = models.CharField(max_length=20, blank=True)
    estado = models.CharField(max_length=30, db_index=True)
    responsable = models.CharField(max_length=80, blank=True)
    almacen = models.CharField('Almacén', max_length=80, blank=True)
    costo_materiales = models.DecimalField('Materia prima S/', max_digits=16, decimal_places=2, default=D0)
    costo_mano_obra = models.DecimalField('Mano de obra S/', max_digits=16, decimal_places=2, default=D0)
    costo_indirecto = models.DecimalField('Gasto indirecto S/', max_digits=16, decimal_places=2, default=D0)

    class Meta:
        ordering = ['-inicio', '-referencia']
        verbose_name = 'orden de fabricación del sistema anterior'

    @property
    def costo_total(self):
        return self.costo_materiales + self.costo_mano_obra + self.costo_indirecto


class ConsumoAnterior(models.Model):
    orden = models.ForeignKey(OrdenFabricacionAnterior, on_delete=models.CASCADE, related_name='consumos')
    producto = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    codigo = models.CharField('Código', max_length=30)
    descripcion = models.CharField(max_length=200)
    requerida = models.DecimalField('Cantidad requerida', max_digits=16, decimal_places=4, default=D0)
    reservada = models.DecimalField('Cantidad reservada', max_digits=16, decimal_places=4, default=D0)
    lotes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['id']


class PosicionPresupuestaria(models.Model):
    """Agrupación de cuentas para el presupuesto (ej. Marketing, Planilla operativa)."""
    nombre = models.CharField(max_length=100, db_index=True)
    cuenta = models.CharField(max_length=12)
    cuenta_nombre = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['nombre', 'cuenta']
        unique_together = [('nombre', 'cuenta')]
        verbose_name = 'posición presupuestaria'
        verbose_name_plural = 'posiciones presupuestarias'
