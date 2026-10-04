"""Manufactura: centros de trabajo, listas de materiales (recetas) y órdenes de producción."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Almacen, Producto, r2

D0 = Decimal('0')


class CentroTrabajo(models.Model):
    """Área o máquina donde se produce: su costo por hora se aplica a las órdenes de producción."""
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    costo_hora_mo = models.DecimalField('Mano de obra S/ por hora', max_digits=12, decimal_places=2, default=D0,
                                        help_text='Sueldos y cargas sociales del personal / horas productivas')
    costo_hora_cif = models.DecimalField('Costos indirectos S/ por hora', max_digits=12, decimal_places=2, default=D0,
                                         help_text='Energía, depreciación de máquinas, mantenimiento, etc. por hora')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     verbose_name='Centro de costo')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'centro de trabajo'
        verbose_name_plural = 'centros de trabajo'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    @property
    def costo_hora(self):
        return self.costo_hora_mo + self.costo_hora_cif


class ListaMateriales(models.Model):
    """Receta del producto: insumos y horas de trabajo para producir `cantidad_base` unidades."""
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='recetas',
                                 limit_choices_to={'clase__in': ['PRODUCTO_TERMINADO', 'SEMIELABORADO']},
                                 verbose_name='Producto a fabricar')
    codigo = models.CharField('Código / versión', max_length=20)
    cantidad_base = models.DecimalField('Rinde (cantidad por lote)', max_digits=14, decimal_places=2,
                                        default=Decimal('1'))
    activa = models.BooleanField('Vigente', default=True, help_text='Las órdenes nuevas usan la receta vigente')
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['producto__nombre', '-activa', 'codigo']
        unique_together = [('producto', 'codigo')]
        verbose_name = 'lista de materiales'
        verbose_name_plural = 'listas de materiales'

    def __str__(self):
        return f'{self.producto.nombre} ({self.codigo})'


class ComponenteLista(models.Model):
    lista = models.ForeignKey(ListaMateriales, on_delete=models.CASCADE, related_name='componentes')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, limit_choices_to={'tipo': 'BIEN'},
                                 verbose_name='Insumo')
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    merma = models.DecimalField('Merma %', max_digits=6, decimal_places=2, default=D0,
                                help_text='Pérdida normal del insumo en el proceso')

    class Meta:
        ordering = ['id']

    @property
    def cantidad_con_merma(self):
        return self.cantidad * (1 + self.merma / 100)


class OperacionLista(models.Model):
    lista = models.ForeignKey(ListaMateriales, on_delete=models.CASCADE, related_name='operaciones')
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.PROTECT, verbose_name='Centro de trabajo')
    descripcion = models.CharField('Operación', max_length=100, blank=True)
    horas = models.DecimalField('Horas por lote', max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['id']


class OrdenProduccion(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('CONFIRMADA', 'Confirmada'), ('EN_PROCESO', 'En proceso'),
               ('TERMINADA', 'Terminada'), ('ANULADA', 'Anulada')]

    numero = models.CharField('Número', max_length=20, blank=True, editable=False)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='ordenes_produccion',
                                 verbose_name='Producto a fabricar')
    lista = models.ForeignKey(ListaMateriales, on_delete=models.PROTECT, related_name='ordenes',
                              verbose_name='Lista de materiales')
    cantidad = models.DecimalField('Cantidad a producir', max_digits=14, decimal_places=2)
    fecha = models.DateField('Fecha planificada', default=timezone.localdate)
    almacen_insumos = models.ForeignKey(Almacen, on_delete=models.PROTECT, related_name='+',
                                        verbose_name='Almacén de insumos')
    almacen_destino = models.ForeignKey(Almacen, on_delete=models.PROTECT, related_name='+',
                                        verbose_name='Almacén de productos terminados')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     verbose_name='Centro de costo')
    glosa = models.TextField('Observaciones', blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR')
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_fin = models.DateField('Fecha de término', null=True, blank=True)
    cantidad_producida = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    # costeo
    costo_estandar_unit = models.DecimalField('Costo estándar unitario', max_digits=14, decimal_places=4, default=D0)
    costo_materiales = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo_mano_obra = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo_cif = models.DecimalField('Costos indirectos', max_digits=14, decimal_places=2, default=D0)
    costo_unitario = models.DecimalField('Costo real unitario', max_digits=14, decimal_places=4, default=D0)
    operacion = models.OneToOneField('inventario.Operacion', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='orden_produccion', editable=False)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    motivo_anulacion = models.CharField('Motivo de anulación', max_length=250, blank=True)
    anulado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    anulado_en = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'orden de producción'
        verbose_name_plural = 'órdenes de producción'

    def __str__(self):
        return f'Orden de producción {self.numero or "(borrador)"}'

    @property
    def costo_total(self):
        return self.costo_materiales + self.costo_mano_obra + self.costo_cif

    @property
    def editable(self):
        return self.estado in ('BORRADOR', 'CONFIRMADA')


class ConsumoOrden(models.Model):
    """Insumo de la orden: lo planificado según la receta y lo realmente consumido."""
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.CASCADE, related_name='consumos')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad_plan = models.DecimalField('Planificado', max_digits=14, decimal_places=4)
    cantidad_real = models.DecimalField('Consumido', max_digits=14, decimal_places=4)
    costo_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=D0)

    class Meta:
        ordering = ['id']

    @property
    def valor(self):
        return r2(self.cantidad_real * self.costo_unitario)


class HoraOrden(models.Model):
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.CASCADE, related_name='horas')
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.PROTECT)
    descripcion = models.CharField(max_length=100, blank=True)
    horas_plan = models.DecimalField('Horas planificadas', max_digits=10, decimal_places=2)
    horas_real = models.DecimalField('Horas reales', max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['id']
