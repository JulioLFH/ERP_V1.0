"""Herramientas de trabajo en equipo (v1.83): Conversaciones, Calendario, Actividades pendientes y Proyectos."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

USUARIO = settings.AUTH_USER_MODEL


# ---------------------------------------------------------------- conversaciones
class Canal(models.Model):
    """Canal de un área o tema (abierto o solo para sus miembros) o chat directo entre dos usuarios."""
    TIPOS = [('CANAL', 'Canal'), ('DIRECTO', 'Mensaje directo')]
    nombre = models.CharField(max_length=80)
    descripcion = models.CharField('Descripción', max_length=200, blank=True)
    tipo = models.CharField(max_length=8, choices=TIPOS, default='CANAL')
    publico = models.BooleanField('Abierto a todos', default=True,
                                  help_text='Cualquier usuario lo ve y puede escribir; si no, solo sus miembros')
    clave = models.CharField(max_length=40, blank=True, db_index=True, editable=False)  # chat directo: d:<id>:<id>
    miembros = models.ManyToManyField(USUARIO, through='MiembroCanal', related_name='canales')
    archivado = models.BooleanField(default=False)
    creado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return self.nombre


class MiembroCanal(models.Model):
    canal = models.ForeignKey(Canal, on_delete=models.CASCADE, related_name='membresias')
    usuario = models.ForeignKey(USUARIO, on_delete=models.CASCADE, related_name='+')
    ultimo_leido = models.PositiveBigIntegerField(default=0)  # id del último mensaje que vio
    unido = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('canal', 'usuario')]


class Mensaje(models.Model):
    canal = models.ForeignKey(Canal, on_delete=models.CASCADE, related_name='mensajes')
    autor = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    texto = models.TextField(max_length=4000)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.autor}: {self.texto[:40]}'


# ---------------------------------------------------------------- calendario
class Evento(models.Model):
    VISIBILIDAD = [('PRIVADO', 'Solo el organizador y los invitados'), ('EMPRESA', 'Todos los usuarios')]
    titulo = models.CharField('Título', max_length=120)
    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)
    todo_el_dia = models.BooleanField('Todo el día', default=False)
    lugar = models.CharField(max_length=150, blank=True, help_text='Sala, dirección o enlace de la videollamada')
    descripcion = models.TextField('Descripción', blank=True)
    organizador = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    invitados = models.ManyToManyField(USUARIO, blank=True, related_name='eventos')
    visibilidad = models.CharField(max_length=8, choices=VISIBILIDAD, default='PRIVADO')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['inicio']

    def __str__(self):
        return self.titulo


# ---------------------------------------------------------------- actividades pendientes
class Pendiente(models.Model):
    """Actividad por hacer encargada a un usuario (por él mismo o por otro), con fecha y enlace opcional al
    documento del sistema que la origina."""
    PRIORIDADES = [('BAJA', 'Baja'), ('NORMAL', 'Normal'), ('ALTA', 'Alta')]
    titulo = models.CharField('Actividad', max_length=150)
    detalle = models.TextField(blank=True)
    fecha = models.DateField('Para el', default=timezone.localdate)
    prioridad = models.CharField(max_length=6, choices=PRIORIDADES, default='NORMAL')
    asignado_a = models.ForeignKey(USUARIO, on_delete=models.CASCADE, related_name='pendientes',
                                   verbose_name='Encargada a')
    enlace = models.CharField('Enlace al documento', max_length=300, blank=True,
                              help_text='Dirección de la pantalla del sistema (ej. /compras/ordenes/15/)')
    hecho = models.BooleanField('Hecha', default=False)
    hecho_en = models.DateTimeField(null=True, blank=True)
    resultado = models.CharField(max_length=250, blank=True)
    creado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['hecho', 'fecha', 'id']

    def __str__(self):
        return self.titulo


# ---------------------------------------------------------------- proyectos
class Proyecto(models.Model):
    ESTADOS = [('ACTIVO', 'Activo'), ('PAUSADO', 'En pausa'), ('CERRADO', 'Cerrado')]
    nombre = models.CharField(max_length=120)
    cliente = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    obra = models.ForeignKey('obras.Obra', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                             verbose_name='Obra o servicio', help_text='Para ver el costo y las horas en Obras')
    responsable = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    verbose_name='Jefe de proyecto')
    miembros = models.ManyToManyField(USUARIO, blank=True, related_name='proyectos_colaboracion',
                                      help_text='Quienes ven el proyecto y trabajan sus tareas')
    fecha_inicio = models.DateField('Inicio', null=True, blank=True)
    fecha_fin = models.DateField('Fin previsto', null=True, blank=True)
    descripcion = models.TextField('Descripción', blank=True)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='ACTIVO')
    creado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['estado', 'nombre']

    def __str__(self):
        return self.nombre


class Tarea(models.Model):
    ETAPAS = [('POR_HACER', 'Por hacer'), ('EN_CURSO', 'En curso'), ('REVISION', 'En revisión'), ('HECHA', 'Hecha')]
    PRIORIDADES = [('0', 'Normal'), ('1', 'Alta'), ('2', 'Urgente')]
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='tareas')
    titulo = models.CharField('Tarea', max_length=150)
    descripcion = models.TextField('Descripción', blank=True)
    responsable = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='tareas_colaboracion')
    etapa = models.CharField(max_length=9, choices=ETAPAS, default='POR_HACER')
    prioridad = models.CharField(max_length=1, choices=PRIORIDADES, default='0')
    fecha_limite = models.DateField('Fecha límite', null=True, blank=True)
    horas_estimadas = models.DecimalField('Horas estimadas', max_digits=8, decimal_places=2, default=Decimal('0'))
    orden = models.PositiveIntegerField(default=0)
    terminada_en = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['orden', 'id']

    def __str__(self):
        return self.titulo

    @property
    def vencida(self):
        return bool(self.fecha_limite and self.etapa != 'HECHA' and self.fecha_limite < timezone.localdate())


class ComentarioTarea(models.Model):
    tarea = models.ForeignKey(Tarea, on_delete=models.CASCADE, related_name='comentarios')
    autor = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    texto = models.TextField(max_length=2000)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
