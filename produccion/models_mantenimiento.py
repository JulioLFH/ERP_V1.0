"""Mantenimiento ampliado (v1.84, MNT-01 a MNT-08): ubicaciones técnicas, códigos de falla, avisos del operador,
hojas de tareas y materiales del plan, planes por condición con sus lecturas, horas por técnico, repuestos
críticos, ventanas de parada y checklist de la orden."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

D0 = Decimal('0')
USUARIO = settings.AUTH_USER_MODEL


class UbicacionTecnica(models.Model):
    """MNT-05: árbol de ubicaciones técnicas (planta › área › línea) donde se instalan los equipos."""
    codigo = models.CharField('Código', max_length=30, unique=True, help_text='Ej. PL1-ENV-L2')
    nombre = models.CharField(max_length=120)
    padre = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='hijas')

    class Meta:
        ordering = ['codigo']
        verbose_name = 'ubicación técnica'
        verbose_name_plural = 'ubicaciones técnicas'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class CodigoFalla(models.Model):
    """MNT-05: catálogo de síntomas, modos, causas y acciones (análisis de fallas y Pareto)."""
    TIPOS = [('SINTOMA', 'Síntoma'), ('MODO', 'Modo de falla'), ('CAUSA', 'Causa'), ('ACCION', 'Acción')]
    tipo = models.CharField(max_length=7, choices=TIPOS)
    codigo = models.CharField('Código', max_length=20)
    nombre = models.CharField(max_length=120)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['tipo', 'codigo']
        unique_together = [('tipo', 'codigo')]
        verbose_name = 'código de falla'
        verbose_name_plural = 'códigos de falla'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class AvisoMantenimiento(models.Model):
    """MNT-03: el operador avisa una falla (desde el celular, con foto); se aprueba y se convierte en orden."""
    PRIORIDADES = [('EMERGENCIA', 'Emergencia'), ('ALTA', 'Alta'), ('NORMAL', 'Normal')]
    ESTADOS = [('PENDIENTE', 'Por revisar'), ('CONVERTIDO', 'Convertido en orden'), ('RECHAZADO', 'Rechazado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, related_name='avisos')
    sintoma = models.TextField('Qué pasa (síntoma)')
    prioridad = models.CharField(max_length=10, choices=PRIORIDADES, default='NORMAL')
    equipo_parado = models.BooleanField('El equipo está parado', default=False)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    motivo_rechazo = models.CharField(max_length=200, blank=True)
    orden = models.ForeignKey('produccion.OrdenMantenimiento', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='avisos', editable=False)
    reportado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['estado', '-creado']
        verbose_name = 'aviso de mantenimiento'
        verbose_name_plural = 'avisos de mantenimiento'

    def __str__(self):
        return f'Aviso {self.numero}'


class TareaPlan(models.Model):
    """MNT-02: hoja de tareas del plan preventivo (se copia como checklist a la orden)."""
    plan = models.ForeignKey('produccion.PlanMantenimiento', on_delete=models.CASCADE, related_name='tareas')
    secuencia = models.PositiveSmallIntegerField(default=10)
    descripcion = models.CharField('Tarea', max_length=200)
    minutos = models.PositiveIntegerField('Tiempo estándar (min)', default=0)
    herramientas = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['secuencia', 'id']


class MaterialPlan(models.Model):
    """MNT-02: materiales y repuestos del plan (se reservan en la orden programada)."""
    plan = models.ForeignKey('produccion.PlanMantenimiento', on_delete=models.CASCADE, related_name='materiales')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, related_name='+')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['id']


class LecturaCondicion(models.Model):
    """MNT-02: lectura de condición (temperatura, vibración, presión): fuera del umbral del plan genera la orden."""
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, related_name='lecturas_condicion')
    variable = models.CharField(max_length=40, help_text='Ej. Temperatura del rodamiento, vibración')
    valor = models.DecimalField(max_digits=12, decimal_places=3)
    unidad = models.CharField(max_length=15, blank=True)
    fecha = models.DateTimeField(default=timezone.now)
    fuera_de_rango = models.BooleanField(default=False, editable=False)
    orden = models.ForeignKey('produccion.OrdenMantenimiento', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+', editable=False)
    usuario = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'lectura de condición'
        verbose_name_plural = 'lecturas de condición'


class TareaOrden(models.Model):
    """MNT-07: checklist de la orden (desde la hoja de tareas del plan)."""
    orden = models.ForeignKey('produccion.OrdenMantenimiento', on_delete=models.CASCADE, related_name='tareas')
    secuencia = models.PositiveSmallIntegerField(default=10)
    descripcion = models.CharField(max_length=200)
    hecha = models.BooleanField(default=False)
    observacion = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['secuencia', 'id']


class HoraTecnico(models.Model):
    """MNT-04: horas reales de cada técnico en la orden, a su costo hora (sueldo con cargas de la planilla)."""
    orden = models.ForeignKey('produccion.OrdenMantenimiento', on_delete=models.CASCADE, related_name='horas_tecnicos')
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, related_name='+')
    fecha = models.DateField(default=timezone.localdate)
    horas = models.DecimalField(max_digits=6, decimal_places=2)
    costo_hora = models.DecimalField(max_digits=10, decimal_places=2, default=D0)

    class Meta:
        ordering = ['fecha', 'id']

    @property
    def costo(self):
        return (self.horas * self.costo_hora).quantize(Decimal('0.01'))


class RepuestoCritico(models.Model):
    """MNT-04: repuesto crítico del equipo con el stock mínimo que debe haber (alerta si baja)."""
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, related_name='repuestos_criticos')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, related_name='+')
    stock_minimo = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        ordering = ['equipo', 'producto__nombre']
        unique_together = [('equipo', 'producto')]
        verbose_name = 'repuesto crítico'
        verbose_name_plural = 'repuestos críticos'


class VentanaParada(models.Model):
    """MNT-07: ventana de parada acordada con producción para hacer mantenimiento."""
    centro = models.ForeignKey('produccion.CentroTrabajo', on_delete=models.CASCADE, null=True, blank=True,
                               related_name='+', verbose_name='Línea / puesto')
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    motivo = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['inicio']
        verbose_name = 'ventana de parada'
        verbose_name_plural = 'ventanas de parada'
