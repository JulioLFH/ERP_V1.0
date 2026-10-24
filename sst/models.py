"""Seguridad y salud en el trabajo (Ley 29783 y su reglamento; G.050 en construcción): registros obligatorios por
empresa, área u obra con los trabajadores de Planillas, los almacenes y las obras de Ceiba.

- Exámenes médicos ocupacionales (con vencimiento), monitoreo de agentes, inspecciones, inducción, capacitación,
  entrenamiento y simulacros (con sus asistentes), equipos de seguridad y emergencia, IPERC por área, puesto u obra,
  entrega de EPP como salida de almacén al trabajador (con cargo firmado y reposición), SCTR por trabajador y obra y
  permisos de trabajo de alto riesgo.
- Accidentes, incidentes peligrosos, incidentes y enfermedades ocupacionales con su investigación, acciones
  correctivas, días de descanso, costo por centro de costo, plazo de notificación e índices de frecuencia, gravedad y
  accidentabilidad.
Los registros que lo requieren pasan por la validación de un especialista (core.validacion). Formatos y plazos son
parametrizables: confírmelos con un especialista en SST."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.urls import reverse
from django.utils import timezone

from calidad.models import Accion
from core.validacion import Validable

D0 = Decimal('0')


class Lugar(models.Model):
    """Dónde aplica el registro: la obra, el área (unidad organizativa) o un lugar descrito."""
    obra = models.ForeignKey('obras.Obra', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    area = models.ForeignKey('planillas.UnidadOrganizativa', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Área')
    lugar = models.CharField('Lugar / sede', max_length=120, blank=True)

    class Meta:
        abstract = True

    @property
    def donde(self):
        return ' · '.join(str(x) for x in (self.obra and self.obra.nombre, self.area and self.area.nombre,
                                            self.lugar) if x) or 'Empresa'


class RegistroSST(Lugar, Validable):
    MODULO = 'sst'

    class Meta:
        abstract = True

    def get_absolute_url(self):
        return reverse('sst:registro', args=[self.SLUG, self.pk])


class ExamenMedico(RegistroSST):
    SLUG, TIPO_VALIDACION, ESPECIALISTA = 'emo', 'EMO', 'Médico ocupacional'
    TIPOS = [('PREOCUPACIONAL', 'Preocupacional (ingreso)'), ('PERIODICO', 'Periódico'), ('RETIRO', 'De retiro'),
             ('OTRO', 'Otro')]
    RESULTADOS = [('APTO', 'Apto'), ('APTO_RESTRICCIONES', 'Apto con restricciones'), ('NO_APTO', 'No apto'),
                  ('PENDIENTE', 'Pendiente')]
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, related_name='examenes_sst')
    tipo = models.CharField(max_length=14, choices=TIPOS, default='PERIODICO')
    fecha = models.DateField()
    vence = models.DateField('Vence', null=True, blank=True)
    resultado = models.CharField(max_length=18, choices=RESULTADOS, default='APTO')
    restricciones = models.CharField(max_length=250, blank=True)
    clinica = models.CharField('Clínica / centro médico', max_length=120, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'examen médico ocupacional'
        verbose_name_plural = 'exámenes médicos ocupacionales'

    def __str__(self):
        return f'EMO {self.get_tipo_display().lower()} · {self.trabajador.nombre_completo}'


class Monitoreo(RegistroSST):
    SLUG, TIPO_VALIDACION, ESPECIALISTA = 'monitoreos', 'MONITOREO', 'Especialista en higiene ocupacional'
    AGENTES = [('FISICO', 'Físico (ruido, iluminación, vibración, temperatura)'), ('QUIMICO', 'Químico (polvo, gases)'),
               ('BIOLOGICO', 'Biológico'), ('ERGONOMICO', 'Disergonómico'), ('PSICOSOCIAL', 'Psicosocial')]
    fecha = models.DateField()
    tipo_agente = models.CharField('Tipo de agente', max_length=11, choices=AGENTES)
    agente = models.CharField(max_length=120, help_text='Ej. ruido, sílice, iluminación')
    resultado = models.CharField('Resultado', max_length=120)
    limite = models.CharField('Límite permisible', max_length=120, blank=True)
    cumple = models.BooleanField('Dentro del límite', default=True)
    proximo = models.DateField('Próximo monitoreo', null=True, blank=True)
    responsable = models.CharField('Realizado por', max_length=120, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'monitoreo de agentes'
        verbose_name_plural = 'monitoreos de agentes'

    def __str__(self):
        return f'Monitoreo {self.agente} {self.fecha:%d/%m/%Y}'


class Inspeccion(RegistroSST):
    SLUG, TIPO_VALIDACION, VALIDACION_DEFECTO, ESPECIALISTA = ('inspecciones', 'INSPECCION', False,
                                                               'Supervisor o comité de SST')
    TIPOS = [('PLANEADA', 'Planeada'), ('NO_PLANEADA', 'No planeada'), ('OTRA', 'Otra')]
    fecha = models.DateField()
    tipo = models.CharField(max_length=11, choices=TIPOS, default='PLANEADA')
    inspector = models.CharField(max_length=120)
    hallazgos = models.TextField('Hallazgos')
    conforme = models.BooleanField('Sin observaciones', default=False)
    acciones = GenericRelation(Accion, content_type_field='origen_tipo', object_id_field='origen_id')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'inspección de SST'
        verbose_name_plural = 'inspecciones de SST'

    def __str__(self):
        return f'Inspección {self.fecha:%d/%m/%Y} {self.donde}'


class Capacitacion(RegistroSST):
    SLUG, TIPO_VALIDACION, VALIDACION_DEFECTO, ESPECIALISTA = ('capacitaciones', 'CAPACITACION', False,
                                                               'Supervisor de SST')
    TIPOS = [('INDUCCION', 'Inducción'), ('CAPACITACION', 'Capacitación'), ('ENTRENAMIENTO', 'Entrenamiento'),
             ('SIMULACRO', 'Simulacro de emergencia')]
    tipo = models.CharField(max_length=13, choices=TIPOS, default='CAPACITACION')
    tema = models.CharField(max_length=200)
    fecha = models.DateField()
    horas = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1'))
    expositor = models.CharField('Expositor / responsable', max_length=120, blank=True)
    asistentes = models.ManyToManyField('planillas.Trabajador', blank=True, related_name='capacitaciones_sst')
    evaluacion = models.CharField('Resultado o evaluación', max_length=200, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'capacitación / simulacro'
        verbose_name_plural = 'inducción, capacitación y simulacros'

    def __str__(self):
        return f'{self.get_tipo_display()}: {self.tema}'


class EquipoEmergencia(RegistroSST):
    SLUG, TIPO_VALIDACION, VALIDACION_DEFECTO, ESPECIALISTA = ('equipos', 'EQUIPO_EMERGENCIA', False,
                                                               'Supervisor de SST')
    TIPOS = [('EXTINTOR', 'Extintor'), ('BOTIQUIN', 'Botiquín'), ('CAMILLA', 'Camilla'), ('ALARMA', 'Alarma'),
             ('LUCES', 'Luces de emergencia'), ('DETECTOR', 'Detector de humo'), ('SENALIZACION', 'Señalización'),
             ('OTRO', 'Otro')]
    ESTADOS = [('OPERATIVO', 'Operativo'), ('OBSERVADO', 'Observado'), ('BAJA', 'Dado de baja')]
    tipo = models.CharField(max_length=13, choices=TIPOS)
    codigo = models.CharField('Código', max_length=30, blank=True)
    descripcion = models.CharField('Descripción', max_length=150, blank=True)
    fecha_inspeccion = models.DateField('Última inspección')
    vence = models.DateField('Vence (recarga, reposición)', null=True, blank=True)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='OPERATIVO')

    class Meta:
        ordering = ['tipo', 'codigo']
        verbose_name = 'equipo de seguridad y emergencia'
        verbose_name_plural = 'equipos de seguridad y emergencia'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.codigo}'


class Iperc(RegistroSST):
    SLUG, TIPO_VALIDACION, ESPECIALISTA = 'iperc', 'IPERC', 'Supervisor o jefe de SST'
    puesto = models.ForeignKey('planillas.Puesto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    proceso = models.CharField('Proceso / actividad general', max_length=150)
    fecha = models.DateField()
    elaborado_por = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'IPERC'
        verbose_name_plural = 'IPERC (matriz de peligros y riesgos)'

    def __str__(self):
        return f'IPERC {self.proceso} v{self.version}'

    def copiar_relaciones(self, nuevo):
        for l in self.lineas.all():
            l.pk, l.iperc = None, nuevo
            l.save()


class LineaIperc(models.Model):
    NIVELES = [(1, '1 Bajo'), (2, '2 Medio'), (3, '3 Alto'), (4, '4 Muy alto')]
    iperc = models.ForeignKey(Iperc, on_delete=models.CASCADE, related_name='lineas')
    tarea = models.CharField(max_length=150)
    peligro = models.CharField(max_length=150)
    riesgo = models.CharField(max_length=150)
    probabilidad = models.PositiveSmallIntegerField(choices=NIVELES, default=2)
    severidad = models.PositiveSmallIntegerField(choices=NIVELES, default=2)
    controles = models.CharField('Controles (eliminación, sustitución, ingeniería, administrativos, EPP)',
                                 max_length=300, blank=True)
    probabilidad_residual = models.PositiveSmallIntegerField(choices=NIVELES, default=1)
    severidad_residual = models.PositiveSmallIntegerField(choices=NIVELES, default=2)

    class Meta:
        ordering = ['id']

    @property
    def nivel(self):
        return self.probabilidad * self.severidad

    @property
    def nivel_residual(self):
        return self.probabilidad_residual * self.severidad_residual

    @staticmethod
    def clasificar(nivel):
        return 'Intolerable' if nivel >= 12 else 'Importante' if nivel >= 8 else 'Moderado' if nivel >= 4 else \
            'Tolerable'


class EntregaEPP(RegistroSST):
    """Entrega de equipos de protección personal al trabajador: sale del almacén (consumo con su centro de costo),
    con el cargo firmado (sustento) y la fecha de reposición."""
    SLUG, TIPO_VALIDACION, VALIDACION_DEFECTO, ESPECIALISTA = 'epp', 'EPP', False, 'Supervisor de SST'
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, related_name='epp')
    fecha = models.DateField()
    almacen = models.ForeignKey('core.Almacen', on_delete=models.PROTECT, related_name='+')
    operacion = models.ForeignKey('inventario.Operacion', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+', editable=False, verbose_name='Salida de almacén')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'entrega de EPP'
        verbose_name_plural = 'entregas de EPP'

    def __str__(self):
        return f'EPP {self.trabajador.nombre_completo} {self.fecha:%d/%m/%Y}'

    def copiar_relaciones(self, nuevo):
        for l in self.lineas.all():
            l.pk, l.entrega = None, nuevo
            l.save()


class LineaEPP(models.Model):
    entrega = models.ForeignKey(EntregaEPP, on_delete=models.CASCADE, related_name='lineas')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, related_name='+')
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1'))
    reposicion_dias = models.PositiveSmallIntegerField('Reposición (días)', default=0,
                                                       help_text='0 = sin reposición programada')

    class Meta:
        ordering = ['id']

    @property
    def reponer_el(self):
        return self.entrega.fecha + timedelta(days=self.reposicion_dias) if self.reposicion_dias else None


class Sctr(models.Model):
    """Seguro complementario de trabajo de riesgo (salud y pensión) del trabajador, por obra si aplica."""
    MODULO = 'sst'
    TIPOS = [('SALUD', 'Salud'), ('PENSION', 'Pensión'), ('AMBOS', 'Salud y pensión')]
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, related_name='sctr')
    obra = models.ForeignKey('obras.Obra', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    tipo = models.CharField(max_length=7, choices=TIPOS, default='AMBOS')
    aseguradora = models.CharField(max_length=120)
    poliza = models.CharField('N° de póliza / constancia', max_length=60)
    desde = models.DateField()
    hasta = models.DateField()
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-hasta', '-id']
        verbose_name = 'SCTR'
        verbose_name_plural = 'SCTR (seguro complementario de trabajo de riesgo)'

    def __str__(self):
        return f'SCTR {self.trabajador.nombre_completo} {self.poliza}'

    def get_absolute_url(self):
        return reverse('sst:registro', args=['sctr', self.pk])

    @property
    def vigente(self):
        return self.desde <= timezone.localdate() <= self.hasta


class PermisoTrabajo(RegistroSST):
    SLUG, TIPO_VALIDACION, ESPECIALISTA = 'permisos', 'PERMISO_TRABAJO', 'Supervisor de SST'
    TIPOS = [('ALTURA', 'Trabajo en altura'), ('CALIENTE', 'Trabajo en caliente'),
             ('ESPACIO_CONFINADO', 'Espacio confinado'), ('ELECTRICO', 'Trabajo eléctrico'),
             ('EXCAVACION', 'Excavación'), ('IZAJE', 'Izaje'), ('OTRO', 'Otro')]
    tipo = models.CharField(max_length=17, choices=TIPOS)
    fecha = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    descripcion = models.CharField('Trabajo a realizar', max_length=250)
    responsable = models.ForeignKey('planillas.Trabajador', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Responsable del trabajo')
    trabajadores = models.ManyToManyField('planillas.Trabajador', blank=True, related_name='permisos_sst')
    controles = models.TextField('Controles y verificación previa', blank=True)
    cerrado = models.BooleanField('Trabajo terminado (permiso cerrado)', default=False)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'permiso de trabajo de alto riesgo'
        verbose_name_plural = 'permisos de trabajo de alto riesgo'

    def __str__(self):
        return f'Permiso {self.get_tipo_display().lower()} {self.fecha:%d/%m/%Y}'


class Evento(RegistroSST):
    """Accidente de trabajo, incidente peligroso, incidente o enfermedad ocupacional con su investigación."""
    SLUG, TIPO_VALIDACION, ESPECIALISTA = 'eventos', 'EVENTO', 'Jefe de SST o comité de SST'
    TIPOS = [('ACC_LEVE', 'Accidente leve'), ('ACC_INCAPACITANTE', 'Accidente incapacitante'),
             ('ACC_MORTAL', 'Accidente mortal'), ('INC_PELIGROSO', 'Incidente peligroso'), ('INCIDENTE', 'Incidente'),
             ('ENFERMEDAD', 'Enfermedad ocupacional')]
    ACCIDENTES = ('ACC_LEVE', 'ACC_INCAPACITANTE', 'ACC_MORTAL')
    tipo = models.CharField(max_length=17, choices=TIPOS)
    fecha = models.DateField()
    hora = models.TimeField(null=True, blank=True)
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='eventos_sst')
    descripcion = models.TextField('Descripción de lo ocurrido')
    parte_cuerpo = models.CharField('Parte del cuerpo afectada', max_length=120, blank=True)
    dias_descanso = models.PositiveSmallIntegerField('Días de descanso médico', default=0)
    causas_inmediatas = models.TextField('Causas inmediatas (actos y condiciones)', blank=True)
    causas_basicas = models.TextField('Causas básicas (factores personales y del trabajo)', blank=True)
    costo = models.DecimalField('Costo del evento S/', max_digits=14, decimal_places=2, default=D0)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Centro de costo')
    notificado_en = models.DateField('Notificado a la autoridad el', null=True, blank=True)
    acciones = GenericRelation(Accion, content_type_field='origen_tipo', object_id_field='origen_id')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'accidente / incidente'
        verbose_name_plural = 'accidentes, incidentes y enfermedades ocupacionales'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.fecha:%d/%m/%Y}'

    @property
    def plazo_notificacion(self):
        """Fecha límite para notificar a la autoridad (parámetros SST_NOTIFICACION_*; confírmelos)."""
        from .servicios import plazo_notificacion
        return plazo_notificacion(self)


class HorasTrabajadas(models.Model):
    """Horas-hombre trabajadas del mes (base de los índices); vacío = se estiman con Planillas."""
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6)
    obra = models.ForeignKey('obras.Obra', on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    horas = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['-periodo']
        unique_together = [('periodo', 'obra')]
        verbose_name = 'horas-hombre trabajadas'
        verbose_name_plural = 'horas-hombre trabajadas'
