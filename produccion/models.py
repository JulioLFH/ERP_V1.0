"""Manufactura: centros de trabajo, listas de materiales (recetas) y órdenes de producción."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Almacen, Producto, r2

D0 = Decimal('0')


ESTADOS_MAESTRO = [('BORRADOR', 'Borrador'), ('APROBADA', 'Aprobada'), ('OBSOLETA', 'Obsoleta')]
DIAS = [('1', 'Lun'), ('2', 'Mar'), ('3', 'Mié'), ('4', 'Jue'), ('5', 'Vie'), ('6', 'Sáb'), ('7', 'Dom')]


class CentroTrabajo(models.Model):
    """Puesto de trabajo (máquina, línea o puesto manual): capacidad, calendario y tarifas por actividad."""
    TIPOS = [('MAQUINA', 'Máquina'), ('LINEA', 'Línea de producción'), ('MANUAL', 'Puesto manual')]

    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=8, choices=TIPOS, default='MAQUINA')
    costo_hora_mo = models.DecimalField('Tarifa mano de obra S/ h', max_digits=12, decimal_places=2, default=D0,
                                        help_text='Sueldos y cargas sociales del personal / horas productivas')
    costo_hora_cif = models.DecimalField('Tarifa máquina y CIF S/ h', max_digits=12, decimal_places=2, default=D0,
                                         help_text='Energía, depreciación de máquinas, mantenimiento, etc. por hora')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     verbose_name='Centro de costo',
                                     help_text='Su gasto real se compara con lo absorbido por las órdenes')
    horas_turno = models.DecimalField('Horas por turno', max_digits=5, decimal_places=2, default=Decimal('8'))
    turnos = models.PositiveSmallIntegerField('Turnos por día', default=1)
    dias_laborables = models.CharField('Días laborables', max_length=7, default='123456',
                                       help_text='1 = lunes … 7 = domingo')
    eficiencia = models.DecimalField('Eficiencia %', max_digits=5, decimal_places=2, default=Decimal('100'),
                                     help_text='Las horas planificadas se dividen entre la eficiencia')
    horas_normales_mes = models.DecimalField(
        'Capacidad normal (horas al mes)', max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='NIC 2: horas que el puesto trabaja en un mes normal. Si se trabaja menos, el CIF fijo de la '
                  'capacidad ociosa va a gasto y no al producto. Vacío = todo el CIF fijo va al producto')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'puesto de trabajo'
        verbose_name_plural = 'puestos de trabajo'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    @property
    def costo_hora(self):
        return self.costo_hora_mo + self.costo_hora_cif

    @property
    def capacidad_dia(self):
        """Horas productivas disponibles en un día laborable."""
        return self.horas_turno * self.turnos * self.eficiencia / 100

    def laborable(self, fecha):
        return str(fecha.isoweekday()) in self.dias_laborables

    def capacidad_entre(self, desde, hasta):
        from datetime import timedelta
        dias, d = 0, desde
        while d <= hasta:
            dias += self.laborable(d)
            d += timedelta(days=1)
        return self.capacidad_dia * dias


class HojaRuta(models.Model):
    """Secuencia de operaciones para fabricar (reutilizable entre productos y recetas)."""
    codigo = models.CharField('Código', max_length=20, unique=True)
    nombre = models.CharField(max_length=120)
    estado = models.CharField(max_length=8, choices=ESTADOS_MAESTRO, default='BORRADOR')
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'hoja de ruta'
        verbose_name_plural = 'hojas de ruta'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    def horas(self, cantidad):
        """[(operación, horas totales para la cantidad)]: preparación + ejecución por unidad, según eficiencia."""
        return [(o, o.horas_para(cantidad)) for o in self.operaciones.select_related('centro')]


class OperacionRuta(models.Model):
    hoja = models.ForeignKey(HojaRuta, on_delete=models.CASCADE, related_name='operaciones')
    secuencia = models.PositiveSmallIntegerField('Op.', help_text='10, 20, 30…')
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.PROTECT, verbose_name='Puesto de trabajo')
    descripcion = models.CharField('Operación', max_length=100)
    horas_preparacion = models.DecimalField('Preparación (h por orden)', max_digits=10, decimal_places=3,
                                            default=D0)
    horas_unidad = models.DecimalField('Ejecución (h por unidad)', max_digits=10, decimal_places=4, default=D0)
    horas_espera = models.DecimalField('Espera (h)', max_digits=10, decimal_places=2, default=D0,
                                       help_text='Enfriado, secado…: no se costea, sí cuenta en el plazo')

    class Meta:
        ordering = ['secuencia']
        unique_together = [('hoja', 'secuencia')]

    def horas_para(self, cantidad):
        eficiencia = (self.centro.eficiencia or Decimal('100')) / 100
        return ((self.horas_preparacion + self.horas_unidad * cantidad) / eficiencia).quantize(Decimal('0.01'))


class ListaMateriales(models.Model):
    """Receta del producto: insumos y horas de trabajo para producir `cantidad_base` unidades."""
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='recetas',
                                 limit_choices_to={'clase__in': ['PRODUCTO_TERMINADO', 'SEMIELABORADO']},
                                 verbose_name='Producto a fabricar')
    codigo = models.CharField('Código / versión', max_length=20)
    cantidad_base = models.DecimalField('Rinde (cantidad por lote)', max_digits=14, decimal_places=2,
                                        default=Decimal('1'))
    estado = models.CharField(max_length=8, choices=ESTADOS_MAESTRO, default='APROBADA',
                              help_text='Solo las aprobadas se usan en versiones de fabricación')
    vigente_desde = models.DateField('Vigente desde', default=timezone.localdate)
    vigente_hasta = models.DateField('Vigente hasta', null=True, blank=True)
    lote_min = models.DecimalField('Lote desde', max_digits=14, decimal_places=2, null=True, blank=True)
    lote_max = models.DecimalField('Lote hasta', max_digits=14, decimal_places=2, null=True, blank=True)
    activa = models.BooleanField('Vigente', default=True, editable=False)  # = aprobada (compatibilidad)
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['producto__nombre', '-activa', 'codigo']
        unique_together = [('producto', 'codigo')]
        verbose_name = 'lista de materiales'
        verbose_name_plural = 'listas de materiales'

    def __str__(self):
        return f'{self.producto.nombre} ({self.codigo})'

    def save(self, *args, **kwargs):
        self.activa = self.estado == 'APROBADA'
        super().save(*args, **kwargs)

    def vigente_en(self, fecha):
        return self.estado == 'APROBADA' and self.vigente_desde <= fecha and (
            self.vigente_hasta is None or fecha <= self.vigente_hasta)


class ComponenteLista(models.Model):
    lista = models.ForeignKey(ListaMateriales, on_delete=models.CASCADE, related_name='componentes')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, limit_choices_to={'tipo': 'BIEN'},
                                 verbose_name='Insumo')
    cantidad = models.DecimalField(max_digits=14, decimal_places=4)
    merma = models.DecimalField('Merma %', max_digits=6, decimal_places=2, default=D0,
                                help_text='Pérdida normal del insumo en el proceso')
    operacion = models.PositiveSmallIntegerField('Se consume en la op.', null=True, blank=True,
                                                 help_text='Secuencia de la hoja de ruta (10, 20…)')
    almacen = models.ForeignKey(Almacen, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                verbose_name='Almacén de consumo', help_text='Vacío = el de insumos de la orden')

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


class VersionFabricacion(models.Model):
    """Cómo se fabrica un producto: receta + hoja de ruta, para un rango de lote y un periodo de vigencia.
    La orden de producción y el MRP eligen la versión según la cantidad y la fecha."""
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='versiones_fabricacion',
                                 limit_choices_to={'clase__in': ['PRODUCTO_TERMINADO', 'SEMIELABORADO']})
    codigo = models.CharField('Versión', max_length=10)
    descripcion = models.CharField(max_length=120, blank=True)
    lista = models.ForeignKey(ListaMateriales, on_delete=models.PROTECT, related_name='versiones',
                              verbose_name='Lista de materiales')
    hoja = models.ForeignKey(HojaRuta, on_delete=models.PROTECT, null=True, blank=True, related_name='versiones',
                             verbose_name='Hoja de ruta')
    lote_min = models.DecimalField('Lote desde', max_digits=14, decimal_places=2, default=D0)
    lote_max = models.DecimalField('Lote hasta', max_digits=14, decimal_places=2, null=True, blank=True)
    lote_costeo = models.DecimalField('Lote de costeo', max_digits=14, decimal_places=2, null=True, blank=True,
                                      help_text='Cantidad con que se calcula el estándar (reparte la preparación). '
                                                'Vacío = lo que rinde la receta')
    vigente_desde = models.DateField('Vigente desde', default=timezone.localdate)
    vigente_hasta = models.DateField('Vigente hasta', null=True, blank=True)
    dias_fabricacion = models.PositiveSmallIntegerField('Plazo de fabricación (días)', default=1,
                                                        help_text='El MRP inicia la orden con esta anticipación')
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['producto__nombre', 'codigo']
        unique_together = [('producto', 'codigo')]
        verbose_name = 'versión de fabricación'
        verbose_name_plural = 'versiones de fabricación'

    def __str__(self):
        return f'{self.producto.nombre} · versión {self.codigo}'

    @property
    def lote_estandar(self):
        return self.lote_costeo or self.lista.cantidad_base or Decimal('1')

    def aplica(self, cantidad, fecha):
        return (self.activa and self.lista.vigente_en(fecha) and self.vigente_desde <= fecha and
                (self.vigente_hasta is None or fecha <= self.vigente_hasta) and cantidad >= self.lote_min and
                (self.lote_max is None or cantidad <= self.lote_max) and
                (self.hoja_id is None or self.hoja.estado == 'APROBADA'))


class CostoEstandar(models.Model):
    """Costo estándar unitario del producto para un periodo. Se calcula (marca) y se libera: liberado queda fijo y
    las órdenes del periodo se comparan contra él (no se recalcula al confirmar cada orden)."""
    ESTADOS = [('CALCULADO', 'Calculado (sin liberar)'), ('LIBERADO', 'Liberado')]
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='costos_estandar')
    periodo = models.CharField(max_length=6)
    version = models.ForeignKey(VersionFabricacion, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    lote_costeo = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('1'))
    materiales = models.DecimalField('Materiales por unidad', max_digits=14, decimal_places=4, default=D0)
    mano_obra = models.DecimalField('Mano de obra por unidad', max_digits=14, decimal_places=4, default=D0)
    cif = models.DecimalField('Máquina y CIF por unidad', max_digits=14, decimal_places=4, default=D0)
    unitario = models.DecimalField('Costo estándar unitario', max_digits=14, decimal_places=4, default=D0)
    detalle = models.JSONField(default=dict, blank=True,
                               help_text='Cantidades y precios estándar por unidad (materiales y actividades)')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='CALCULADO')
    calculado_en = models.DateTimeField(auto_now=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        ordering = ['-periodo', 'producto__nombre']
        unique_together = [('producto', 'periodo')]
        verbose_name = 'costo estándar'
        verbose_name_plural = 'costos estándar'

    def __str__(self):
        return f'Estándar {self.producto.nombre} {self.periodo[4:]}/{self.periodo[:4]}'


class PlanDemanda(models.Model):
    """Demanda prevista (pronóstico o plan de ventas) que el MRP suma a los pedidos de venta."""
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    fecha = models.DateField('Fecha requerida')
    nota = models.CharField(max_length=120, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        ordering = ['fecha', 'producto__nombre']
        verbose_name = 'plan de demanda'


class CorridaMRP(models.Model):
    fecha = models.DateTimeField(auto_now_add=True)
    horizonte = models.DateField('Planificar hasta')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        ordering = ['-fecha']


class PropuestaMRP(models.Model):
    """Orden planificada del MRP: fabricar (con su versión) o comprar (con su proveedor)."""
    TIPOS = [('PRODUCIR', 'Fabricar'), ('COMPRAR', 'Comprar')]
    corrida = models.ForeignKey(CorridaMRP, on_delete=models.CASCADE, related_name='propuestas')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    tipo = models.CharField(max_length=8, choices=TIPOS)
    nivel = models.PositiveSmallIntegerField(default=0)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    fecha_necesidad = models.DateField()
    fecha_inicio = models.DateField('Iniciar / pedir el')
    version = models.ForeignKey(VersionFabricacion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    proveedor = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    origen = models.CharField(max_length=250, blank=True)
    orden_produccion = models.ForeignKey('OrdenProduccion', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='+')
    orden_compra = models.ForeignKey('compras.OrdenCompra', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')

    class Meta:
        ordering = ['nivel', 'fecha_inicio', 'producto__nombre']

    @property
    def convertida(self):
        return bool(self.orden_produccion_id or self.orden_compra_id)


class OrdenProduccion(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('CONFIRMADA', 'Confirmada'), ('EN_PROCESO', 'En proceso'),
               ('TERMINADA', 'Terminada'), ('ANULADA', 'Anulada')]

    numero = models.CharField('Número', max_length=20, blank=True, editable=False)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='ordenes_produccion',
                                 verbose_name='Producto a fabricar')
    version = models.ForeignKey(VersionFabricacion, on_delete=models.PROTECT, null=True, blank=True,
                                related_name='ordenes', verbose_name='Versión de fabricación')
    estandar = models.ForeignKey(CostoEstandar, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                 editable=False)
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
    maquilador = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                   help_text='Tercerización: el proveedor que fabrica con los materiales que se le '
                                             'envían')
    costo_servicio = models.DecimalField('Servicio de maquila S/', max_digits=14, decimal_places=2, default=D0,
                                         help_text='Se suma al costo del producto. 0 = la base de la factura del '
                                                   'maquilador')
    compra_servicio = models.ForeignKey('compras.Compra', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+', verbose_name='Factura del maquilador')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR')
    prioridad = models.PositiveSmallIntegerField(default=5, help_text='1 = la más urgente; ordena la programación '
                                                                      'de planta')
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_fin = models.DateField('Fecha de término', null=True, blank=True)
    cantidad_producida = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    # costeo
    costo_estandar_unit = models.DecimalField('Costo estándar unitario', max_digits=14, decimal_places=4, default=D0)
    estandar_detalle = models.JSONField(default=dict, blank=True, editable=False,
                                        help_text='Estándar por unidad fijado al confirmar (base de las variaciones)')
    costo_materiales = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo_mano_obra = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo_cif = models.DecimalField('Costos indirectos', max_digits=14, decimal_places=2, default=D0)
    costo_unitario = models.DecimalField('Costo real unitario', max_digits=14, decimal_places=4, default=D0)
    merma_anormal = models.DecimalField(
        'Merma anormal S/', max_digits=14, decimal_places=2, default=D0, editable=False,
        help_text='NIC 2: consumo por encima de la receta con su merma normal; va a gasto, no al producto')
    ajuste_liquidacion = models.DecimalField(
        'Liquidación de costo real S/', max_digits=14, decimal_places=2, default=D0, editable=False,
        help_text='Diferencia entre el gasto real de planta y lo cargado con las tarifas (cierre del periodo)')
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
        """Lo que entró al almacén: materiales + mano de obra + CIF, sin la merma anormal (va a gasto)."""
        return self.costo_materiales + self.costo_mano_obra + self.costo_cif - self.merma_anormal

    @property
    def costo_real_final(self):
        """Costo de la orden después de la liquidación del periodo (gasto real de planta)."""
        return self.costo_total + self.ajuste_liquidacion

    @property
    def costo_estandar_total(self):
        return r2(self.costo_estandar_unit * self.cantidad_producida)

    @property
    def costo_real_unitario(self):
        if not self.cantidad_producida:
            return D0
        return (self.costo_real_final / self.cantidad_producida).quantize(Decimal('0.0001'))

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
    almacen = models.ForeignKey(Almacen, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                verbose_name='Almacén de consumo')
    operacion = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    @property
    def valor(self):
        return r2(self.cantidad_real * self.costo_unitario)


class VariacionOrden(models.Model):
    """Variación de la orden terminada frente al estándar liberado, separada por tipo."""
    TIPOS = [('PRECIO_MAT', 'Precio de materiales'), ('CANTIDAD_MAT', 'Cantidad de materiales (consumo)'),
             ('EFICIENCIA_MO', 'Eficiencia de mano de obra (horas)'),
             ('EFICIENCIA_CIF', 'Eficiencia de máquina y CIF (horas)'), ('TARIFA', 'Tarifa de actividades'),
             ('MERMA_ANORMAL', 'Merma anormal llevada a gasto (NIC 2)'), ('OTRAS', 'Otras (redondeo)')]
    orden = models.ForeignKey('OrdenProduccion', on_delete=models.CASCADE, related_name='variaciones')
    tipo = models.CharField(max_length=15, choices=TIPOS)
    monto = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['id']


class HoraOrden(models.Model):
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.CASCADE, related_name='horas')
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.PROTECT)
    secuencia = models.PositiveSmallIntegerField(null=True, blank=True)
    descripcion = models.CharField(max_length=100, blank=True)
    horas_plan = models.DecimalField('Horas planificadas', max_digits=10, decimal_places=2)
    horas_real = models.DecimalField('Horas reales', max_digits=10, decimal_places=2)
    costo_mo = models.DecimalField('Mano de obra absorbida', max_digits=14, decimal_places=2, default=D0)
    costo_cif = models.DecimalField('Máquina y CIF absorbidos', max_digits=14, decimal_places=2, default=D0)

    class Meta:
        ordering = ['id']


# ================================================================ calidad
class ParametroCalidad(models.Model):
    """Plan de calidad del producto: lo que se mide en cada inspección y su especificación."""
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='parametros_calidad')
    nombre = models.CharField('Característica', max_length=80, help_text='Ej. Humedad, pH, peso neto, color')
    unidad = models.CharField(max_length=20, blank=True)
    minimo = models.DecimalField('Mínimo', max_digits=14, decimal_places=4, null=True, blank=True)
    maximo = models.DecimalField('Máximo', max_digits=14, decimal_places=4, null=True, blank=True)
    especificacion = models.CharField('Especificación (texto)', max_length=120, blank=True,
                                      help_text='Para características que no se miden con un número')
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['producto', 'orden', 'id']
        verbose_name = 'parámetro de calidad'
        verbose_name_plural = 'parámetros de calidad'

    def __str__(self):
        return self.nombre


class InspeccionCalidad(models.Model):
    TIPOS = [('RECEPCION', 'Recepción (materia prima / compras)'), ('PROCESO', 'En proceso'),
             ('TERMINADO', 'Producto terminado'), ('DEVOLUCION', 'Devolución de clientes')]
    RESULTADOS = [('PENDIENTE', 'Pendiente'), ('APROBADO', 'Aprobado'), ('OBSERVADO', 'Aprobado con observaciones'),
                  ('RECHAZADO', 'Rechazado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    tipo = models.CharField(max_length=10, choices=TIPOS, default='RECEPCION')
    fecha = models.DateField(default=timezone.localdate)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='inspecciones')
    lote = models.CharField(max_length=40, blank=True)
    cantidad = models.DecimalField('Cantidad inspeccionada', max_digits=14, decimal_places=2, default=D0)
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                help_text='Dónde está la mercadería (para enviarla a cuarentena si se rechaza)')
    operacion = models.ForeignKey('inventario.Operacion', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='inspecciones', verbose_name='Operación de almacén')
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='inspecciones', verbose_name='Orden de producción')
    resultado = models.CharField(max_length=10, choices=RESULTADOS, default='PENDIENTE')
    observaciones = models.TextField(blank=True)
    cuarentena = models.ForeignKey('inventario.Operacion', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+',
                                  editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'inspección de calidad'
        verbose_name_plural = 'inspecciones de calidad'

    def __str__(self):
        return f'Inspección {self.numero}'


class ResultadoCalidad(models.Model):
    inspeccion = models.ForeignKey(InspeccionCalidad, on_delete=models.CASCADE, related_name='resultados')
    caracteristica = models.CharField(max_length=80)
    unidad = models.CharField(max_length=20, blank=True)
    minimo = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    maximo = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    especificacion = models.CharField(max_length=120, blank=True)
    valor = models.DecimalField('Valor medido', max_digits=14, decimal_places=4, null=True, blank=True)
    texto = models.CharField('Resultado (texto)', max_length=120, blank=True)
    conforme = models.BooleanField(null=True)

    class Meta:
        ordering = ['id']

    def evaluar(self):
        """Conforme si el valor está dentro de la especificación (si no hay rango, lo decide el inspector)."""
        if self.valor is not None and (self.minimo is not None or self.maximo is not None):
            self.conforme = ((self.minimo is None or self.valor >= self.minimo) and
                             (self.maximo is None or self.valor <= self.maximo))
        return self.conforme


# ================================================================ mantenimiento
class Equipo(models.Model):
    codigo = models.CharField('Código', max_length=20, unique=True)
    nombre = models.CharField(max_length=120)
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.SET_NULL, null=True, blank=True, related_name='equipos',
                               verbose_name='Puesto de trabajo')
    activo_fijo = models.ForeignKey('activos.ActivoFijo', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Activo fijo')
    marca_modelo = models.CharField('Marca / modelo / serie', max_length=120, blank=True)
    ubicacion = models.CharField('Ubicación', max_length=80, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'equipo'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class PlanMantenimiento(models.Model):
    """Tarea preventiva periódica del equipo."""
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE, related_name='planes')
    tarea = models.CharField(max_length=150, help_text='Ej. Cambio de aceite y filtros')
    frecuencia_dias = models.PositiveIntegerField('Cada (días)', default=30)
    ultima_fecha = models.DateField('Última ejecución', null=True, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['equipo', 'tarea']
        verbose_name = 'plan de mantenimiento'
        verbose_name_plural = 'planes de mantenimiento'

    def __str__(self):
        return f'{self.equipo.codigo}: {self.tarea}'

    @property
    def proxima(self):
        """Fecha en que toca: última ejecución + frecuencia (sin ejecuciones registradas, toca hoy)."""
        from datetime import timedelta
        if not self.ultima_fecha:
            return timezone.localdate()
        return self.ultima_fecha + timedelta(days=self.frecuencia_dias)


class OrdenMantenimiento(models.Model):
    TIPOS = [('PREVENTIVO', 'Preventivo'), ('CORRECTIVO', 'Correctivo (falla)')]
    ESTADOS = [('PROGRAMADA', 'Programada'), ('EN_EJECUCION', 'En ejecución'), ('CERRADA', 'Cerrada'),
               ('ANULADA', 'Anulada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    equipo = models.ForeignKey(Equipo, on_delete=models.PROTECT, related_name='ordenes')
    tipo = models.CharField(max_length=10, choices=TIPOS, default='CORRECTIVO')
    plan = models.ForeignKey(PlanMantenimiento, on_delete=models.SET_NULL, null=True, blank=True, related_name='ordenes')
    fecha_programada = models.DateField(default=timezone.localdate)
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_fin = models.DateField('Fecha de cierre', null=True, blank=True)
    estado = models.CharField(max_length=12, choices=ESTADOS, default='PROGRAMADA')
    descripcion = models.TextField('Falla / trabajo a realizar')
    trabajo_realizado = models.TextField(blank=True)
    responsable = models.CharField(max_length=80, blank=True)
    proveedor = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                  verbose_name='Servicio externo')
    horas_parada = models.DecimalField('Horas de parada', max_digits=8, decimal_places=2, default=D0)
    costo_mano_obra = models.DecimalField('Mano de obra S/', max_digits=14, decimal_places=2, default=D0)
    costo_servicios = models.DecimalField('Servicios externos S/', max_digits=14, decimal_places=2, default=D0)
    consumo = models.OneToOneField('inventario.Operacion', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='orden_mantenimiento', editable=False)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_programada', '-id']
        verbose_name = 'orden de mantenimiento'
        verbose_name_plural = 'órdenes de mantenimiento'

    def __str__(self):
        return f'OT {self.numero}'

    @property
    def costo_repuestos(self):
        return sum((i.valor for i in self.consumo.items.all()), D0) if self.consumo_id else D0

    @property
    def costo_total(self):
        return self.costo_mano_obra + self.costo_servicios + self.costo_repuestos


class RepuestoOrden(models.Model):
    orden = models.ForeignKey(OrdenMantenimiento, on_delete=models.CASCADE, related_name='repuestos')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['id']


# ================================================================ reporte de planta (tablets)
class AvanceOrden(models.Model):
    """Lo que el operario reporta desde la planta: producción buena, merma y horas de una operación."""
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.CASCADE, related_name='avances')
    hora = models.ForeignKey(HoraOrden, on_delete=models.SET_NULL, null=True, blank=True, related_name='avances',
                             verbose_name='Operación')
    cantidad_buena = models.DecimalField('Cantidad buena', max_digits=14, decimal_places=2, default=D0)
    cantidad_merma = models.DecimalField('Merma / rechazo', max_digits=14, decimal_places=2, default=D0)
    horas = models.DecimalField(max_digits=8, decimal_places=2, default=D0)
    operario = models.CharField(max_length=80, blank=True)
    nota = models.CharField(max_length=200, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    registrado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-registrado']
        verbose_name = 'avance de producción'
        verbose_name_plural = 'avances de producción'


# ================================================================ cambios de ingeniería
class CambioIngenieria(models.Model):
    """Orden de cambio de ingeniería (ECO): propuesta de nueva receta con motivo, aprobación por otra persona y
    fecha efectiva desde la que la nueva receta reemplaza a la actual."""
    MOTIVOS = [('MEJORA', 'Mejora del producto o proceso'), ('COSTO', 'Reducción de costo'),
               ('CALIDAD', 'Problema de calidad'), ('PROVEEDOR', 'Cambio de insumo o proveedor'),
               ('NORMATIVA', 'Normativa / registro sanitario'), ('OTRO', 'Otro')]
    ESTADOS = [('BORRADOR', 'En preparación'), ('POR_APROBAR', 'Por aprobar'), ('APROBADO', 'Aprobado y aplicado'),
               ('RECHAZADO', 'Rechazado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    lista_actual = models.ForeignKey(ListaMateriales, on_delete=models.PROTECT, related_name='cambios',
                                     verbose_name='Receta actual')
    lista_nueva = models.ForeignKey(ListaMateriales, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+', editable=False, verbose_name='Receta propuesta')
    motivo = models.CharField(max_length=10, choices=MOTIVOS, default='MEJORA')
    descripcion = models.TextField('Descripción del cambio')
    fecha_efectiva = models.DateField('Fecha efectiva', default=timezone.localdate,
                                      help_text='Desde esta fecha las órdenes usan la nueva receta')
    estado = models.CharField(max_length=12, choices=ESTADOS, default='BORRADOR')
    solicitado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    comentario = models.CharField('Comentario de la aprobación', max_length=250, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'cambio de ingeniería'
        verbose_name_plural = 'cambios de ingeniería'

    def __str__(self):
        return f'Cambio de ingeniería {self.numero}'


# ================================================================ costeo por actividades (ABC)
class ActividadABC(models.Model):
    """Actividad de apoyo (recepción, preparación de máquinas, control de calidad, despacho…): recibe gasto de
    centros de costo y lo reparte a los productos según su inductor."""
    INDUCTORES = [('ORDENES', 'Órdenes de producción terminadas'), ('HORAS', 'Horas reales de las órdenes'),
                  ('UNIDADES', 'Unidades producidas'), ('INSPECCIONES', 'Inspecciones de calidad'),
                  ('DESPACHOS', 'Líneas vendidas (despachos)')]
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    inductor = models.CharField(max_length=12, choices=INDUCTORES, default='ORDENES')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'actividad'
        verbose_name_plural = 'actividades'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class RecursoActividad(models.Model):
    """Parte del gasto de un centro de costo que consume la actividad."""
    actividad = models.ForeignKey(ActividadABC, on_delete=models.CASCADE, related_name='recursos')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, related_name='+',
                                     verbose_name='Centro de costo')
    porcentaje = models.DecimalField('% del gasto', max_digits=6, decimal_places=2, default=Decimal('100'))

    class Meta:
        ordering = ['id']


# ================================================================ cierre de costos NIC 2
class ComportamientoGasto(models.Model):
    """Cómo se comporta un gasto de planta (por prefijo de cuenta, el más largo manda): mano de obra y CIF variables
    se liquidan completos al producto; el CIF fijo según la capacidad normal (NIC 2 párr. 13)."""
    TIPOS = [('MO', 'Mano de obra directa'), ('VARIABLE', 'CIF variable'), ('FIJO', 'CIF fijo')]
    prefijo = models.CharField('Cuenta (prefijo)', max_length=12, unique=True, help_text='Ej. 62, 6361, 681')
    tipo = models.CharField(max_length=8, choices=TIPOS)

    class Meta:
        ordering = ['prefijo']
        verbose_name = 'comportamiento de gasto de planta'

    def __str__(self):
        return f'{self.prefijo}: {self.get_tipo_display()}'


class LiquidacionCosto(models.Model):
    """Cierre de costos del periodo: lleva el gasto real de planta (62-68 por centro de costo) a las órdenes
    terminadas y, por producto, a inventario (21/23) o costo de ventas (69). La capacidad ociosa queda en gasto."""
    periodo = models.CharField(max_length=6, unique=True)
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        ordering = ['-periodo']
        verbose_name = 'liquidación de costo real'
        verbose_name_plural = 'liquidaciones de costo real'

    def __str__(self):
        return f'Liquidación de costo real {self.periodo[4:]}/{self.periodo[:4]}'


class LiquidacionCentro(models.Model):
    liquidacion = models.ForeignKey(LiquidacionCosto, on_delete=models.CASCADE, related_name='centros')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, related_name='+')
    horas = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    horas_normales = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    mo_real = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    variable_real = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    fijo_real = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    fijo_inventariable = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    absorbido_mo = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    absorbido_cif = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        ordering = ['centro_costo__codigo']

    @property
    def real(self):
        return self.mo_real + self.variable_real + self.fijo_real

    @property
    def inventariable(self):
        return self.mo_real + self.variable_real + self.fijo_inventariable if self.horas else D0

    @property
    def gasto_periodo(self):
        """No va al producto: capacidad ociosa (o todo el gasto si no hubo producción)."""
        return self.real - self.inventariable

    @property
    def absorbido(self):
        return self.absorbido_mo + self.absorbido_cif

    @property
    def diferencia(self):
        return self.inventariable - self.absorbido

    @property
    def uso_capacidad(self):
        if not self.horas_normales:
            return None
        return (self.horas / self.horas_normales * 100).quantize(Decimal('0.1'))


class LiquidacionOrden(models.Model):
    liquidacion = models.ForeignKey(LiquidacionCosto, on_delete=models.CASCADE, related_name='ordenes')
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.CASCADE, related_name='liquidaciones')
    mano_obra = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    cif = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    de_insumos = models.DecimalField('De sus semielaborados', max_digits=14, decimal_places=2, default=D0)

    class Meta:
        ordering = ['orden__fecha_fin', 'orden_id']

    @property
    def total(self):
        return self.mano_obra + self.cif + self.de_insumos


class LiquidacionProducto(models.Model):
    """Reparto de la diferencia de un producto: lo que sigue en stock revaloriza su costo promedio; lo consumido
    por otras órdenes pasa a ellas (multinivel); lo vendido va al costo de ventas."""
    liquidacion = models.ForeignKey(LiquidacionCosto, on_delete=models.CASCADE, related_name='productos')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    fecha = models.DateField(help_text='Fecha del ajuste en el kardex y en la contabilidad')
    producido = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    en_stock = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    consumido = models.DecimalField('Consumido por otras órdenes', max_digits=14, decimal_places=2, default=D0)
    diferencia = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    a_inventario = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    a_produccion = models.DecimalField('A otras órdenes', max_digits=14, decimal_places=2, default=D0)
    a_costo = models.DecimalField('A costo de ventas', max_digits=14, decimal_places=2, default=D0)
    kardex = models.ForeignKey('core.Kardex', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['producto__nombre']


class PruebaVNR(models.Model):
    """NIC 2 párr. 9 y 28-33: el inventario se mide al menor entre su costo y su valor neto realizable (precio de
    venta estimado − gastos de venta). La diferencia se provisiona (29) contra gasto (695) y se revierte si el
    precio se recupera."""
    NORMAS = [('NIIF', 'Solo libro NIIF (no deducible hasta la venta o destrucción)'), ('AMBOS', 'NIIF y tributario')]
    fecha = models.DateField('Fecha de la prueba')
    norma = models.CharField('Libro', max_length=5, choices=NORMAS, default='NIIF')
    gasto_venta = models.DecimalField('Gastos de venta %', max_digits=5, decimal_places=2, default=D0,
                                      help_text='Comisiones, fletes y demás gastos para vender, en % del precio')
    dias_precio = models.PositiveSmallIntegerField('Precio: promedio de los últimos días', default=90)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'prueba de valor neto realizable'
        verbose_name_plural = 'pruebas de valor neto realizable'

    def __str__(self):
        return f'Valor neto realizable al {self.fecha:%d/%m/%Y}'


class PruebaVNRLinea(models.Model):
    prueba = models.ForeignKey(PruebaVNR, on_delete=models.CASCADE, related_name='lineas')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo = models.DecimalField('Costo unitario', max_digits=14, decimal_places=4, default=D0)
    precio = models.DecimalField('Precio de venta estimado', max_digits=14, decimal_places=4, default=D0)
    fuente = models.CharField(max_length=40, blank=True)
    vnr = models.DecimalField('VNR unitario', max_digits=14, decimal_places=4, default=D0)
    deterioro = models.DecimalField('Desvalorización acumulada', max_digits=14, decimal_places=2, default=D0)
    ajuste = models.DecimalField('Ajuste del periodo', max_digits=14, decimal_places=2, default=D0,
                                 help_text='Positivo: provisión; negativo: reversión')

    class Meta:
        ordering = ['-deterioro', 'producto__nombre']
