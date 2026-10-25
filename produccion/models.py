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
    costo_hora_mo = models.DecimalField(
        'Tarifa mano de obra referencial S/ h', max_digits=12, decimal_places=2, default=D0,
        help_text='Por hora hombre. Para el costo estándar y para las horas sin trabajador asignado; con trabajador se '
                  'usa su costo real (sueldo y cargas)')
    costo_hora_maquina = models.DecimalField(
        'Tarifa máquina S/ h', max_digits=12, decimal_places=2, default=D0,
        help_text='Por hora máquina: depreciación, energía y mantenimiento de la máquina')
    costo_hora_cif = models.DecimalField(
        'Tarifa CIF S/ h', max_digits=12, decimal_places=2, default=D0,
        help_text='Costos indirectos de fabricación, distribuidos por hora máquina')
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
        """Por hora máquina con una persona: máquina + CIF + una hora hombre referencial."""
        return self.costo_hora_mo + self.costo_hora_maquina + self.costo_hora_cif

    @property
    def costo_hora_maquina_cif(self):
        return self.costo_hora_maquina + self.costo_hora_cif

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
    maquinistas = models.PositiveSmallIntegerField(default=1, help_text='Personas que operan la máquina')
    ayudantes = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['secuencia']
        unique_together = [('hoja', 'secuencia')]
        verbose_name = 'operación de la hoja de ruta'
        verbose_name_plural = 'operaciones de la hoja de ruta'

    def __str__(self):
        return f'{self.hoja.codigo} op. {self.secuencia} {self.descripcion}'

    @property
    def personas(self):
        return self.maquinistas + self.ayudantes

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
        verbose_name = 'insumo de la receta'
        verbose_name_plural = 'insumos de la receta'

    def __str__(self):
        return f'{self.lista} · {self.producto.codigo} {self.producto.nombre}'

    @property
    def cantidad_con_merma(self):
        return self.cantidad * (1 + self.merma / 100)


class SubproductoLista(models.Model):
    """Otra salida de la receta además del producto principal (NIC 2 párr. 14).
    Subproducto (de menor valor): entra a su valor por unidad, que se resta del costo del principal.
    Coproducto: recibe un % del costo conjunto (lo que queda tras los subproductos); el principal, el resto."""
    TIPOS = [('SUBPRODUCTO', 'Subproducto (valor fijo por unidad)'),
             ('COPRODUCTO', 'Coproducto (% del costo conjunto)')]
    lista = models.ForeignKey(ListaMateriales, on_delete=models.CASCADE, related_name='subproductos')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, limit_choices_to={'tipo': 'BIEN'},
                                 related_name='+')
    cantidad = models.DecimalField('Cantidad por lote', max_digits=14, decimal_places=4,
                                   help_text='Lo que sale por cada lote de la receta (lo que «rinde»)')
    tipo = models.CharField(max_length=12, choices=TIPOS, default='SUBPRODUCTO')
    valor_unitario = models.DecimalField('Valor por unidad S/', max_digits=14, decimal_places=4, default=D0,
                                         help_text='Subproducto: su valor neto realizable (precio − gastos de venta)')
    participacion = models.DecimalField('% del costo conjunto', max_digits=6, decimal_places=2, default=D0,
                                        help_text='Coproducto: según su valor de venta relativo')

    class Meta:
        ordering = ['id']
        verbose_name = 'subproducto'

    def __str__(self):
        return f'{self.lista} · {self.producto.codigo} {self.producto.nombre}'


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
    maquina = models.DecimalField('Máquina por unidad', max_digits=14, decimal_places=4, default=D0)
    cif = models.DecimalField('CIF por unidad', max_digits=14, decimal_places=4, default=D0)
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

    def __str__(self):
        return f'{self.producto.nombre} {self.cantidad} al {self.fecha:%d/%m/%Y}'


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
    solicitud = models.ForeignKey('compras.SolicitudPedido', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='propuestas_mrp', verbose_name='Solicitud de pedido')

    class Meta:
        ordering = ['nivel', 'fecha_inicio', 'producto__nombre']

    @property
    def convertida(self):
        return bool(self.orden_produccion_id or self.orden_compra_id or self.solicitud_id)


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
    fecha_fin = models.DateField('Fecha de término', null=True, blank=True,
                                 help_text='Fecha contable de la producción (kardex, costos y asientos); la fecha y '
                                           'hora reales quedan en «terminado en»')
    terminado_en = models.DateTimeField('Fecha y hora de término', null=True, blank=True)
    cantidad_producida = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    lote_producido = models.CharField('Lote producido', max_length=60, blank=True, editable=False,
                                      help_text='Lote del producto terminado (INV-30); vacío = el número de la orden')
    # costeo
    costo_estandar_unit = models.DecimalField('Costo estándar unitario', max_digits=14, decimal_places=4, default=D0)
    estandar_detalle = models.JSONField(default=dict, blank=True, editable=False,
                                        help_text='Estándar por unidad fijado al confirmar (base de las variaciones)')
    costo_materiales = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo_mano_obra = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo_maquina = models.DecimalField('Costo de máquina', max_digits=14, decimal_places=2, default=D0)
    costo_cif = models.DecimalField('Costos indirectos', max_digits=14, decimal_places=2, default=D0)
    costo_subproductos = models.DecimalField(
        'Asignado a subproductos y coproductos', max_digits=14, decimal_places=2, default=D0, editable=False)
    costo_unitario = models.DecimalField('Costo real unitario', max_digits=14, decimal_places=4, default=D0)
    merma_anormal = models.DecimalField(
        'Merma anormal S/', max_digits=14, decimal_places=2, default=D0, editable=False,
        help_text='NIC 2: consumo por encima de la receta con su merma normal; va a gasto, no al producto')
    ajuste_liquidacion = models.DecimalField(
        'Liquidación de costo real S/', max_digits=14, decimal_places=2, default=D0, editable=False,
        help_text='Diferencia entre el gasto real de planta y lo cargado con las tarifas (cierre del periodo)')
    operacion = models.OneToOneField('inventario.Operacion', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='orden_produccion', editable=False)
    es_historica = models.BooleanField(
        'Del sistema anterior', default=False, editable=False,
        help_text='Terminada o cancelada en el sistema anterior: solo consulta, ya está en el inventario y la '
                  'contabilidad migrados (no movió el almacén del ERP)')
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
    def costo_produccion(self):
        """Costo de toda la orden: materiales + mano de obra + máquina + CIF, sin la merma anormal (va a gasto)."""
        return (self.costo_materiales + self.costo_mano_obra + self.costo_maquina + self.costo_cif -
                self.merma_anormal)

    @property
    def costo_total(self):
        """Lo que corresponde al producto principal (descontado lo asignado a subproductos y coproductos)."""
        return self.costo_produccion - self.costo_subproductos

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
    """Operación de la orden en un puesto: horas máquina (base de la máquina y del CIF) y su cuadrilla."""
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.CASCADE, related_name='horas')
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.PROTECT)
    secuencia = models.PositiveSmallIntegerField(null=True, blank=True)
    descripcion = models.CharField(max_length=100, blank=True)
    horas_plan = models.DecimalField('Horas máquina planificadas', max_digits=10, decimal_places=2)
    horas_real = models.DecimalField('Horas máquina reales', max_digits=10, decimal_places=2)
    maquinistas = models.PositiveSmallIntegerField(default=1)
    ayudantes = models.PositiveSmallIntegerField(default=0)
    costo_mo = models.DecimalField('Mano de obra', max_digits=14, decimal_places=2, default=D0)
    costo_maquina = models.DecimalField('Máquina', max_digits=14, decimal_places=2, default=D0)
    costo_cif = models.DecimalField('CIF', max_digits=14, decimal_places=2, default=D0)

    class Meta:
        ordering = ['id']

    @property
    def horas_hombre(self):
        return sum((p.horas for p in self.personal.all()), D0)


class HoraHombre(models.Model):
    """Horas hombre declaradas en una operación: quién (maquinista o ayudante) y cuánto costó su hora."""
    ROLES = [('MAQUINISTA', 'Maquinista'), ('AYUDANTE', 'Ayudante')]
    hora = models.ForeignKey(HoraOrden, on_delete=models.CASCADE, related_name='personal')
    rol = models.CharField(max_length=10, choices=ROLES, default='MAQUINISTA')
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    horas = models.DecimalField(max_digits=10, decimal_places=2, default=D0)
    costo_hora = models.DecimalField('S/ por hora', max_digits=12, decimal_places=4, default=D0)
    costo = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        ordering = ['id']
        verbose_name = 'horas hombre'
        verbose_name_plural = 'horas hombre'


class SalidaOrden(models.Model):
    """Subproducto o coproducto de la orden (según su receta): lo previsto, lo que salió y su costo asignado."""
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.CASCADE, related_name='salidas')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='+')
    tipo = models.CharField(max_length=12, choices=SubproductoLista.TIPOS, default='SUBPRODUCTO')
    cantidad_plan = models.DecimalField('Prevista', max_digits=14, decimal_places=4, default=D0)
    cantidad_real = models.DecimalField('Producida', max_digits=14, decimal_places=4, default=D0)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=D0)
    participacion = models.DecimalField(max_digits=6, decimal_places=2, default=D0)
    costo_unitario = models.DecimalField('Costo asignado unitario', max_digits=14, decimal_places=4, default=D0)

    class Meta:
        ordering = ['id']

    @property
    def costo(self):
        return r2(self.cantidad_real * self.costo_unitario)


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
    en_certificado = models.BooleanField('En el certificado', default=True,
                                         help_text='Se imprime en el certificado de análisis del lote')
    metodo = models.CharField('Método de análisis', max_length=120, blank=True,
                              help_text='Ej. Valoración por HPLC, MA-QC-012 (CAL-05)')
    referencia = models.CharField('Referencia (farmacopea)', max_length=80, blank=True, help_text='Ej. USP <791>')

    class Meta:
        ordering = ['producto', 'orden', 'id']
        verbose_name = 'parámetro de calidad'
        verbose_name_plural = 'parámetros de calidad'

    def __str__(self):
        return self.nombre


class InspeccionCalidad(models.Model):
    TIPOS = [('RECEPCION', 'Recepción (materia prima / compras)'), ('PROCESO', 'En proceso'),
             ('TERMINADO', 'Producto terminado'), ('DEVOLUCION', 'Devolución de clientes')]
    RESULTADOS = [('PENDIENTE', 'Pendiente'), ('OOS', 'En investigación (fuera de especificación)'),
                  ('APROBADO', 'Aprobado'), ('OBSERVADO', 'Aprobado con observaciones'),
                  ('REPROCESO', 'Reprocesado'), ('RECHAZADO', 'Rechazado')]
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
    # v1.84 (CAL-01, CAL-05, CAL-08): especificación usada, instrumento y bloqueo al decidir
    especificacion = models.ForeignKey('produccion.EspecificacionCalidad', on_delete=models.PROTECT, null=True,
                                       blank=True, related_name='inspecciones', editable=False)
    instrumento = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='inspecciones', limit_choices_to={'es_instrumento': True},
                                    help_text='Balanza, pHmetro u otro instrumento calibrado que se usó')
    decidida_en = models.DateTimeField(null=True, blank=True, editable=False)
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


class PlantillaCertificado(models.Model):
    """Formato del certificado de análisis (COA) del producto: las características salen de su plan de calidad.
    Con «cuarentena», cada lote nuevo del producto entra bloqueado y no se vende hasta que Calidad apruebe su
    certificado (liberación del lote)."""
    producto = models.OneToOneField(Producto, on_delete=models.CASCADE, related_name='plantilla_certificado')
    titulo = models.CharField('Título', max_length=120, default='CERTIFICADO DE ANÁLISIS')
    norma = models.CharField('Norma / especificación de referencia', max_length=150, blank=True,
                             help_text='Ej. NTP 209.038, ficha técnica FT-012 rev. 3')
    texto = models.TextField('Declaración', blank=True,
                             help_text='Texto del certificado. Ej. «El lote cumple con las especificaciones…»')
    responsable = models.CharField('Firma (cargo)', max_length=100, default='Jefe de Aseguramiento de la Calidad')
    requiere_liberacion = models.BooleanField(
        'Lotes nuevos en cuarentena', default=True,
        help_text='El lote no se vende hasta que Calidad apruebe su certificado')

    class Meta:
        verbose_name = 'plantilla de certificado de análisis'
        verbose_name_plural = 'plantillas de certificado de análisis'

    def __str__(self):
        return f'{self.titulo} — {self.producto}'


class CertificadoAnalisis(models.Model):
    """Certificado de análisis (COA) de un lote, con los resultados de su inspección. Al aprobarlo, Calidad libera
    el lote para la venta; al rechazarlo, el lote queda rechazado."""
    ESTADOS = [('BORRADOR', 'Por aprobar'), ('APROBADO', 'Aprobado: lote liberado'),
               ('RECHAZADO', 'Rechazado: lote rechazado'), ('ANULADO', 'Anulado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    lote = models.ForeignKey('core.Lote', on_delete=models.PROTECT, related_name='certificados')
    inspeccion = models.ForeignKey(InspeccionCalidad, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='certificados', verbose_name='Inspección')
    cliente = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                help_text='Opcional: certificado emitido para un cliente')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR')
    resultados = models.JSONField(default=list, editable=False)
    observaciones = models.TextField(blank=True)
    emitido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+',
                                    editable=False)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    firma = models.ForeignKey('core.FirmaElectronica', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='+', editable=False)  # CAL-03: liberación firmada
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'certificado de análisis'
        verbose_name_plural = 'certificados de análisis'

    def __str__(self):
        return f'Certificado {self.numero}'


# ================================================================ mantenimiento
class Equipo(models.Model):
    """Maestro único de equipos y flota (FLO-01): máquinas de planta, vehículos, montacargas y herramientas. Lo usan
    las guías (el vehículo de Logística es un equipo de tipo vehículo), el mantenimiento, las paradas y los activos."""
    TIPOS = [('MAQUINA', 'Máquina / equipo de planta'), ('VEHICULO', 'Vehículo'), ('MONTACARGAS', 'Montacargas'),
             ('MAQ_PESADA', 'Maquinaria pesada'), ('HERRAMIENTA', 'Herramienta'), ('OTRO', 'Otro')]
    ESTADOS = [('OPERATIVO', 'Operativo'), ('MANTENIMIENTO', 'En mantenimiento'), ('INOPERATIVO', 'Inoperativo'),
               ('BAJA', 'De baja')]
    COMBUSTIBLES = [('', '—'), ('DIESEL', 'Diésel'), ('GASOLINA', 'Gasolina'), ('GLP', 'GLP'), ('GNV', 'GNV'),
                    ('ELECTRICO', 'Eléctrico')]
    MEDIDORES = [('', 'Sin medidor'), ('KM', 'Odómetro (km)'), ('HORAS', 'Horómetro (horas)')]
    codigo = models.CharField('Código', max_length=20, unique=True)
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=12, choices=TIPOS, default='MAQUINA')
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.SET_NULL, null=True, blank=True, related_name='equipos',
                               verbose_name='Puesto de trabajo')
    activo_fijo = models.ForeignKey('activos.ActivoFijo', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Activo fijo')
    marca_modelo = models.CharField('Marca / modelo', max_length=120, blank=True)
    placa = models.CharField(max_length=10, blank=True)
    serie = models.CharField('Chasis / N° de serie', max_length=40, blank=True)
    anio = models.PositiveSmallIntegerField('Año', null=True, blank=True)
    combustible = models.CharField(max_length=10, choices=COMBUSTIBLES, blank=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Centro de costo',
                                     help_text='Vacío = el del puesto de trabajo')
    responsable = models.ForeignKey('planillas.Trabajador', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Responsable / conductor')
    ubicacion = models.CharField('Ubicación', max_length=80, blank=True)
    estado = models.CharField(max_length=13, choices=ESTADOS, default='OPERATIVO')
    medidor = models.CharField(max_length=5, choices=MEDIDORES, blank=True)
    lectura_actual = models.DecimalField('Lectura actual', max_digits=12, decimal_places=1, default=D0,
                                         editable=False)
    lectura_fecha = models.DateField(null=True, blank=True, editable=False)
    rendimiento_esperado = models.DecimalField('Rendimiento esperado (km u horas por galón)', max_digits=8,
                                               decimal_places=2, null=True, blank=True)
    aplica_prestamo = models.BooleanField('Se presta a clientes (comodato)', default=False)
    # instrumento de medición (CAL-08): con calibración vigente se acepta en las inspecciones
    es_instrumento = models.BooleanField('Instrumento de medición (se calibra)', default=False)
    rango_tolerancia = models.CharField('Rango y tolerancia', max_length=80, blank=True,
                                        help_text='Ej. 0–220 g, ± 0.1 mg')
    calibracion_dias = models.PositiveIntegerField('Calibrar cada (días)', default=0)
    proxima_calibracion = models.DateField('Próxima calibración', null=True, blank=True, editable=False)
    # historial técnico (MNT-05)
    CRITICIDADES = [('', '—'), ('A', 'A: crítico (producción o seguridad)'), ('B', 'B: importante'),
                    ('C', 'C: no crítico')]
    criticidad = models.CharField(max_length=1, choices=CRITICIDADES, blank=True)
    ubicacion_tecnica = models.ForeignKey('produccion.UbicacionTecnica', on_delete=models.SET_NULL, null=True,
                                          blank=True, related_name='equipos', verbose_name='Ubicación técnica')
    garantia_hasta = models.DateField('Garantía hasta', null=True, blank=True)
    valor_reposicion = models.DecimalField('Valor de reposición S/', max_digits=14, decimal_places=2, default=D0)
    presupuesto_anual = models.DecimalField('Presupuesto de mantenimiento anual S/', max_digits=14, decimal_places=2,
                                            default=D0)
    # HR-10: comprar o alquilar (Obras): el costo hora propio frente a la tarifa de alquiler del mercado
    tarifa_alquiler = models.DecimalField('Tarifa de alquiler del mercado S/ por hora (sin IGV)', max_digits=12,
                                          decimal_places=2, default=D0)
    horas_mes = models.PositiveIntegerField('Horas de uso esperadas al mes', default=0,
                                            help_text='Para repartir la depreciación por hora si aún no hay partes')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'equipo'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    @property
    def centro_costo_efectivo(self):
        if self.centro_costo_id:
            return self.centro_costo
        return self.centro.centro_costo if self.centro_id else None

    @property
    def unidad_medidor(self):
        return {'KM': 'km', 'HORAS': 'h'}.get(self.medidor, '')


class PlanMantenimiento(models.Model):
    """Tarea preventiva del equipo por tiempo (cada N días), por uso (cada N km u horas del odómetro / horómetro) o lo
    que ocurra primero (SAP: estrategia por rendimiento IP42), opcionalmente de un componente con su repuesto."""
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE, related_name='planes')
    componente = models.ForeignKey('flota.ComponenteEquipo', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='planes')
    tarea = models.CharField(max_length=150, help_text='Ej. Cambio de aceite y filtros')
    frecuencia_dias = models.PositiveIntegerField('Cada (días)', null=True, blank=True, default=30,
                                                  help_text='Vacío = solo por uso')
    frecuencia_uso = models.DecimalField('Cada (km u horas)', max_digits=10, decimal_places=1, null=True, blank=True,
                                         help_text='Según el medidor del equipo. Vacío = solo por días')
    tolerancia_pct = models.DecimalField('Tolerancia %', max_digits=5, decimal_places=2, default=Decimal('10'),
                                         help_text='Se programa cuando falta este % de la frecuencia de uso')
    ultima_fecha = models.DateField('Última ejecución', null=True, blank=True)
    ultima_lectura = models.DecimalField('Lectura de la última ejecución', max_digits=12, decimal_places=1,
                                         null=True, blank=True)
    # MNT-02: por condición (lectura de temperatura, vibración o presión fuera del umbral) y horizonte
    variable = models.CharField('Variable de condición', max_length=40, blank=True,
                                help_text='Ej. Temperatura del rodamiento. Con umbral, una lectura fuera genera la orden')
    umbral_min = models.DecimalField('Umbral mínimo', max_digits=12, decimal_places=3, null=True, blank=True)
    umbral_max = models.DecimalField('Umbral máximo', max_digits=12, decimal_places=3, null=True, blank=True)
    anticipacion_dias = models.PositiveSmallIntegerField('Programar con (días de anticipación)', default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['equipo', 'tarea']
        verbose_name = 'plan de mantenimiento'
        verbose_name_plural = 'planes de mantenimiento'

    def __str__(self):
        return f'{self.equipo.codigo}: {self.tarea}'

    @property
    def proxima(self):
        """Fecha en que toca: por días (última ejecución + frecuencia; sin ejecuciones, hoy) o, solo por uso, la fecha
        en que se proyecta llegar a la lectura del próximo mantenimiento. None si no se puede proyectar."""
        from datetime import timedelta
        fechas = []
        if self.frecuencia_dias:
            fechas.append(self.ultima_fecha + timedelta(days=self.frecuencia_dias) if self.ultima_fecha
                          else timezone.localdate())
        if self.frecuencia_uso and self.equipo.medidor:
            from flota.lecturas import fecha_proyectada
            fecha = fecha_proyectada(self.equipo, self.proxima_lectura)
            if fecha:
                fechas.append(fecha)
        return min(fechas) if fechas else None

    @property
    def proxima_lectura(self):
        if not self.frecuencia_uso:
            return None
        return (self.ultima_lectura or Decimal('0')) + self.frecuencia_uso

    @property
    def falta_uso(self):
        """km u horas que faltan para el próximo mantenimiento (negativo = pasado)."""
        if self.proxima_lectura is None or not self.equipo.medidor:
            return None
        return self.proxima_lectura - self.equipo.lectura_actual

    def vence(self, hasta):
        """¿Toca hasta esa fecha? Por días, por la lectura actual (dentro de la tolerancia) o por la proyectada."""
        if self.frecuencia_uso and self.equipo.medidor and self.falta_uso is not None and \
                self.falta_uso <= self.frecuencia_uso * self.tolerancia_pct / 100:
            return True
        return self.proxima is not None and self.proxima <= hasta


class OrdenMantenimiento(models.Model):
    TIPOS = [('PREVENTIVO', 'Preventivo'), ('CORRECTIVO', 'Correctivo (falla)'), ('PREDICTIVO', 'Predictivo'),
             ('MEJORA', 'Mejora'), ('INSPECCION', 'Inspección'), ('CALIBRACION', 'Calibración')]
    ESTADOS = [('PROGRAMADA', 'Programada'), ('EN_EJECUCION', 'En ejecución'), ('CERRADA', 'Cerrada'),
               ('ANULADA', 'Anulada')]
    PRIORIDADES = [('EMERGENCIA', 'Emergencia'), ('ALTA', 'Alta'), ('NORMAL', 'Normal')]
    numero = models.CharField('N°', max_length=20, editable=False)
    equipo = models.ForeignKey(Equipo, on_delete=models.PROTECT, related_name='ordenes')
    tipo = models.CharField(max_length=11, choices=TIPOS, default='CORRECTIVO')
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
    # v1.84 (MNT-01 a MNT-08)
    prioridad = models.CharField(max_length=10, choices=PRIORIDADES, default='NORMAL')
    tecnico = models.ForeignKey('planillas.Trabajador', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', verbose_name='Técnico asignado')
    inicio_real = models.DateTimeField('Inicio real', null=True, blank=True)
    fin_real = models.DateTimeField('Fin real', null=True, blank=True)
    horas_estimadas = models.DecimalField('Horas estimadas', max_digits=8, decimal_places=2, default=D0)
    costo_estimado = models.DecimalField('Costo planificado S/', max_digits=14, decimal_places=2, default=D0)
    orden_servicio = models.ForeignKey('compras.OrdenCompra', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', verbose_name='Orden de servicio del contratista')
    falla_sintoma = models.ForeignKey('produccion.CodigoFalla', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='+', limit_choices_to={'tipo': 'SINTOMA'}, verbose_name='Síntoma')
    falla_modo = models.ForeignKey('produccion.CodigoFalla', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+', limit_choices_to={'tipo': 'MODO'}, verbose_name='Modo de falla')
    falla_causa = models.ForeignKey('produccion.CodigoFalla', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+', limit_choices_to={'tipo': 'CAUSA'}, verbose_name='Causa')
    falla_accion = models.ForeignKey('produccion.CodigoFalla', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', limit_choices_to={'tipo': 'ACCION'}, verbose_name='Acción')
    requiere_permiso = models.BooleanField('Requiere permiso de trabajo (alto riesgo)', default=False)
    permiso_trabajo = models.ForeignKey('sst.PermisoTrabajo', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+', verbose_name='Permiso de trabajo (SST)')
    requiere_loto = models.BooleanField('Requiere bloqueo y etiquetado (LOTO)', default=False)
    loto_aplicado_por = models.CharField('LOTO aplicado por', max_length=120, blank=True)
    lectura_cierre = models.DecimalField('Lectura del medidor al cerrar', max_digits=12, decimal_places=1, null=True,
                                         blank=True)
    conformidad_por = models.CharField('Conformidad de (usuario del equipo)', max_length=120, blank=True)
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

    @property
    def horas_reparacion(self):
        """Horas entre el inicio y el fin reales (MTTR)."""
        if self.inicio_real and self.fin_real:
            return Decimal((self.fin_real - self.inicio_real).total_seconds() / 3600).quantize(Decimal('0.01'))
        return None


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
    turno = models.ForeignKey('planillas.Turno', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    nota = models.CharField(max_length=200, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    registrado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-registrado']
        verbose_name = 'avance de producción'
        verbose_name_plural = 'avances de producción'

    def __str__(self):
        return f'Avance de la orden {self.orden.numero}: {self.cantidad_buena} buenas, {self.horas} h'


class AvancePersonal(models.Model):
    """Horas hombre que la planta declara en un avance: quién trabajó (de la planilla), con qué rol y cuántas horas.
    Al terminar la orden se precargan como su cuadrilla real."""
    avance = models.ForeignKey(AvanceOrden, on_delete=models.CASCADE, related_name='personal')
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, related_name='+')
    rol = models.CharField(max_length=10, choices=HoraHombre.ROLES, default='MAQUINISTA')
    horas = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ['id']


class MotivoParada(models.Model):
    """Maestro de motivos de parada (Manufactura › Ingeniería): cada motivo dice qué pérdida del OEE afecta; la planta
    elige el motivo y el OEE se calcula solo."""
    PERDIDAS = [('DISPONIBILIDAD', 'Disponibilidad'), ('RENDIMIENTO', 'Rendimiento'), ('CALIDAD', 'Calidad'),
                ('NO_PROGRAMADO', 'No programado (no cuenta en el OEE)')]
    TIPOS = [('PLANIFICADA', 'Planificada'), ('AVERIA', 'Avería'), ('PROCESO', 'Proceso / operacional'),
             ('EXTERNA', 'Externa'), ('MICROPARADA', 'Microparada'), ('VELOCIDAD', 'Velocidad reducida'),
             ('CALIDAD', 'Calidad')]
    AREAS = [('PRODUCCION', 'Producción'), ('MANTENIMIENTO', 'Mantenimiento'), ('CALIDAD', 'Calidad'),
             ('LOGISTICA', 'Logística'), ('RRHH', 'RR. HH.'), ('SERVICIOS', 'Servicios'),
             ('PLANEAMIENTO', 'Planeamiento')]
    codigo = models.CharField('Código', max_length=20, unique=True, help_text='Ej. AV-MEC')
    nombre = models.CharField(max_length=80, help_text='Lo que ve el operario en la tablet')
    perdida = models.CharField('Pérdida OEE', max_length=14, choices=PERDIDAS, default='DISPONIBILIDAD')
    tipo = models.CharField(max_length=12, choices=TIPOS, default='PROCESO')
    planificada = models.BooleanField('Parada planificada', default=False,
                                      help_text='Limpieza, cambio de formato, preventivo: resta disponibilidad solo si '
                                                'la empresa lo configura')
    area = models.CharField('Área responsable', max_length=13, choices=AREAS, default='PRODUCCION')
    es_falla = models.BooleanField('Cuenta para MTBF / MTTR', default=False,
                                   help_text='Averías: tiempo medio entre fallas y de reparación')
    exige_equipo = models.BooleanField('Exige equipo', default=False,
                                       help_text='Averías y mantenimiento: la parada se registra con su equipo')
    genera_mantenimiento = models.BooleanField(
        'Propone orden de mantenimiento', default=False,
        help_text='Al cerrar la parada crea una orden correctiva programada para el equipo')
    puestos = models.ManyToManyField(CentroTrabajo, blank=True, related_name='motivos_parada',
                                     verbose_name='Puestos donde aplica', help_text='Vacío = todos')
    activo = models.BooleanField(default=True, help_text='Los motivos con paradas no se borran: se desactivan')

    class Meta:
        ordering = ['codigo']
        verbose_name = 'motivo de parada'
        verbose_name_plural = 'motivos de parada'

    def __str__(self):
        return self.nombre

    def aplica_en(self, centro):
        """Vacío = todos los puestos."""
        ids = [p.pk for p in self.puestos.all()]
        return not ids or (centro is not None and getattr(centro, 'pk', centro) in ids)

    @classmethod
    def para_puesto(cls, centro):
        """Motivos activos que aplican al puesto (los sin puestos asignados aplican a todos)."""
        from django.db.models import Q
        qs = cls.objects.filter(activo=True)
        if centro is None:
            return qs
        return qs.filter(Q(puestos__isnull=True) | Q(puestos=centro)).distinct()


class ConfiguracionPlanta(models.Model):
    """Cómo se calcula el OEE en esta empresa (una fila)."""
    planificadas_restan = models.BooleanField(
        'Las paradas planificadas restan disponibilidad', default=False,
        help_text='No: solo reducen el tiempo planificado (no bajan el OEE). Sí: bajan la disponibilidad')
    microparada_minutos = models.PositiveSmallIntegerField(
        'Duración máxima de microparada (min)', default=5,
        help_text='Las paradas no planificadas más cortas se cuentan como microparada (bajan el rendimiento)')

    class Meta:
        verbose_name = 'configuración de planta'

    @classmethod
    def actual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Parada(models.Model):
    """Tiempo en que el puesto o el equipo no produjo, con su motivo (base de la disponibilidad del OEE)."""
    centro = models.ForeignKey(CentroTrabajo, on_delete=models.PROTECT, related_name='paradas',
                               verbose_name='Puesto / línea')
    equipo = models.ForeignKey(Equipo, on_delete=models.SET_NULL, null=True, blank=True, related_name='paradas')
    orden = models.ForeignKey(OrdenProduccion, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='paradas', verbose_name='Orden de producción')
    motivo = models.ForeignKey(MotivoParada, on_delete=models.PROTECT, related_name='paradas')
    orden_mantenimiento = models.ForeignKey('OrdenMantenimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='paradas', editable=False,
                                            verbose_name='Orden de mantenimiento propuesta')
    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)
    operario = models.CharField(max_length=80, blank=True)
    nota = models.CharField(max_length=200, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-inicio', '-id']
        verbose_name = 'parada'

    def __str__(self):
        return f'Parada {self.centro.codigo} {timezone.localtime(self.inicio):%d/%m/%Y %H:%M} · {self.motivo}'

    @property
    def horas(self):
        if not self.fin:
            return Decimal('0')
        return (Decimal((self.fin - self.inicio).total_seconds()) / 3600).quantize(Decimal('0.01'))


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


from .models_calidad import (  # noqa: E402,F401
    CalibracionEquipo, CalificacionEquipo, CambioResultado, Contramuestra, ControlCambio, Desviacion,
    EspecificacionCalidad, EstudioEstabilidad, InvestigacionOOS, PasoRegistroLote, PuntoEstabilidad, QuejaLote,
    RegistroLote, RetiroMercado)
from .models_mantenimiento import (  # noqa: E402,F401
    AvisoMantenimiento, CodigoFalla, HoraTecnico, LecturaCondicion, MaterialPlan, RepuestoCritico, TareaOrden,
    TareaPlan, UbicacionTecnica, VentanaParada)
