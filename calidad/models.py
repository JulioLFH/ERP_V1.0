"""Excelencia organizacional y calidad (ISO 9001, 14001 y 45001): no conformidades con su análisis de causa (5 porqués
o Ishikawa), acciones correctivas y preventivas con verificación de eficacia (compartidas con SST), auditorías internas
con sus hallazgos, documentos controlados con versión y aprobación, objetivos e indicadores por área (BSC / OKR) y
sugerencias de mejora del personal (Kaizen). Los registros que lo requieren pasan por la validación de un especialista
(core.validacion)."""
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.validacion import Validable

D0 = Decimal('0')
NORMAS = [('', '—'), ('ISO9001', 'ISO 9001 (calidad)'), ('ISO14001', 'ISO 14001 (ambiental)'),
          ('ISO45001', 'ISO 45001 (SST)'), ('LEY29783', 'Ley 29783 (SST)'),
          ('BPM_DIGEMID', 'BPM DIGEMID (DS 021-2018-SA)'), ('OTRA', 'Otra')]


def _anio():
    return timezone.localdate().year


def siguiente(tipo, prefijo):
    from core.models import Serie
    _, numero = Serie.siguiente(tipo, prefijo)
    return f'{prefijo}-{int(numero):04d}'


class Accion(models.Model):
    """Acción correctiva, preventiva o de mejora de una no conformidad, un hallazgo, una inspección o un accidente:
    responsable, plazo, ejecución y verificación de su eficacia."""
    TIPOS = [('CORRECCION', 'Corrección inmediata'), ('CORRECTIVA', 'Acción correctiva'),
             ('PREVENTIVA', 'Acción preventiva'), ('MEJORA', 'Mejora')]
    ESTADOS = [('PENDIENTE', 'Pendiente'), ('EN_CURSO', 'En curso'), ('HECHA', 'Ejecutada'),
               ('VERIFICADA', 'Verificada (cerrada)')]
    origen_tipo = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='+')
    origen_id = models.PositiveBigIntegerField()
    origen = GenericForeignKey('origen_tipo', 'origen_id')
    tipo = models.CharField(max_length=10, choices=TIPOS, default='CORRECTIVA')
    descripcion = models.CharField('Acción', max_length=300)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    plazo = models.DateField()
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    fecha_ejecucion = models.DateField('Ejecutada el', null=True, blank=True)
    verificacion = models.CharField('Verificación de la eficacia', max_length=250, blank=True)
    eficaz = models.BooleanField(null=True, blank=True)
    verificado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', editable=False)
    verificado_en = models.DateField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['estado', 'plazo', 'id']
        verbose_name = 'acción correctiva / preventiva'
        verbose_name_plural = 'acciones correctivas y preventivas'

    def __str__(self):
        return f'{self.get_tipo_display()}: {self.descripcion[:60]}'

    @property
    def vencida(self):
        return self.estado in ('PENDIENTE', 'EN_CURSO') and self.plazo < timezone.localdate()

    @property
    def modulo(self):
        return getattr(type(self.origen), 'MODULO', 'calidad') if self.origen is not None else 'calidad'


class Auditoria(Validable):
    TIPO_VALIDACION, VALIDACION_DEFECTO, ESPECIALISTA, MODULO = 'AUDITORIA', True, 'Auditor líder', 'calidad'
    TIPOS = [('INTERNA', 'Interna'), ('EXTERNA', 'Externa / de certificación')]
    ESTADOS = [('PROGRAMADA', 'Programada'), ('REALIZADA', 'Realizada'), ('CERRADA', 'Cerrada')]
    codigo = models.CharField('Código', max_length=12, blank=True, editable=False)
    anio = models.PositiveSmallIntegerField('Programa (año)', default=_anio)
    norma = models.CharField(max_length=11, choices=NORMAS, default='ISO9001')
    tipo = models.CharField(max_length=8, choices=TIPOS, default='INTERNA')
    fecha = models.DateField('Fecha')
    area = models.ForeignKey('planillas.UnidadOrganizativa', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Área o proceso auditado')
    alcance = models.TextField(blank=True)
    auditor_lider = models.CharField('Auditor líder', max_length=120)
    equipo = models.CharField('Equipo auditor', max_length=250, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PROGRAMADA')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'auditoría'
        verbose_name_plural = 'auditorías'

    def __str__(self):
        return f'{self.codigo} {self.get_norma_display()} {self.fecha:%d/%m/%Y}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            self.codigo = siguiente('AUD', 'AUD')
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('calidad:registro', args=['auditorias', self.pk])

    def copiar_relaciones(self, nuevo):
        for h in self.hallazgos.all():
            Hallazgo.objects.create(auditoria=nuevo, tipo=h.tipo, requisito=h.requisito, descripcion=h.descripcion,
                                    no_conformidad=h.no_conformidad)


class NoConformidad(Validable):
    TIPO_VALIDACION, VALIDACION_DEFECTO, MODULO = 'NO_CONFORMIDAD', False, 'calidad'
    ESPECIALISTA = 'Responsable del sistema de gestión'
    ORIGENES = [('AUDITORIA', 'Auditoría'), ('RECLAMO', 'Reclamo de cliente'), ('INSPECCION_SST', 'Inspección de SST'),
                ('PROVEEDOR', 'Proveedor'), ('PRODUCCION', 'Producción / proceso'), ('OTRO', 'Otro')]
    METODOS = [('5_PORQUES', '5 porqués'), ('ISHIKAWA', 'Ishikawa (6M)')]
    ESTADOS = [('ABIERTA', 'Abierta'), ('TRATAMIENTO', 'En tratamiento'), ('CERRADA', 'Cerrada')]
    SEIS_M = [('mano_obra', 'Mano de obra'), ('metodo', 'Método'), ('maquina', 'Máquina'), ('material', 'Material'),
              ('medicion', 'Medición'), ('medio_ambiente', 'Medio ambiente')]
    codigo = models.CharField('Código', max_length=12, blank=True, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    origen = models.CharField(max_length=15, choices=ORIGENES, default='OTRO')
    norma = models.CharField(max_length=11, choices=NORMAS, blank=True)
    descripcion = models.TextField('Descripción')
    area = models.ForeignKey('planillas.UnidadOrganizativa', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Área')
    proceso = models.CharField(max_length=120, blank=True)
    detectado_por = models.CharField('Detectado por', max_length=120, blank=True)
    metodo = models.CharField('Análisis de causa', max_length=10, choices=METODOS, default='5_PORQUES')
    porques = models.JSONField('5 porqués', default=list, blank=True)
    ishikawa = models.JSONField('Ishikawa (6M)', default=dict, blank=True)
    causa_raiz = models.TextField('Causa raíz', blank=True)
    estado = models.CharField(max_length=11, choices=ESTADOS, default='ABIERTA')
    fecha_cierre = models.DateField(null=True, blank=True, editable=False)
    eficaz = models.BooleanField('Tratamiento eficaz', null=True, blank=True, editable=False)
    acciones = GenericRelation(Accion, content_type_field='origen_tipo', object_id_field='origen_id')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'no conformidad'
        verbose_name_plural = 'no conformidades'

    def __str__(self):
        return f'{self.codigo} {self.descripcion[:60]}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            self.codigo = siguiente('NCF', 'NC')
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('calidad:registro', args=['no-conformidades', self.pk])


class Hallazgo(models.Model):
    TIPOS = [('NC_MAYOR', 'No conformidad mayor'), ('NC_MENOR', 'No conformidad menor'),
             ('OBSERVACION', 'Observación'), ('OPORTUNIDAD', 'Oportunidad de mejora')]
    auditoria = models.ForeignKey(Auditoria, on_delete=models.CASCADE, related_name='hallazgos')
    tipo = models.CharField(max_length=11, choices=TIPOS)
    requisito = models.CharField('Requisito / cláusula', max_length=60, blank=True)
    descripcion = models.CharField('Descripción', max_length=400)
    no_conformidad = models.ForeignKey(NoConformidad, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='hallazgos')

    class Meta:
        ordering = ['id']


class DocumentoControlado(Validable):
    TIPO_VALIDACION, VALIDACION_DEFECTO, MODULO = 'DOCUMENTO', True, 'calidad'
    ESPECIALISTA = 'Responsable que aprueba el documento'
    TIPOS = [('POLITICA', 'Política'), ('MANUAL', 'Manual'), ('PROCEDIMIENTO', 'Procedimiento'),
             ('INSTRUCTIVO', 'Instructivo'), ('FORMATO', 'Formato / registro'), ('PLAN', 'Plan o programa'),
             ('OTRO', 'Otro')]
    codigo = models.CharField('Código', max_length=30, help_text='Ej. PR-SST-01')
    titulo = models.CharField('Título', max_length=200)
    tipo = models.CharField(max_length=13, choices=TIPOS, default='PROCEDIMIENTO')
    norma = models.CharField(max_length=11, choices=NORMAS, blank=True)
    area = models.ForeignKey('planillas.UnidadOrganizativa', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Área')
    elaborado_por = models.CharField('Elaborado por', max_length=120, blank=True)
    proxima_revision = models.DateField('Próxima revisión', null=True, blank=True)
    resumen = models.TextField('Resumen / cambios de esta versión', blank=True)
    # v1.84 (CAL-09): revisión y aprobación con firma electrónica, vigencia, distribución y lectura obligatoria
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    revisado_en = models.DateTimeField(null=True, blank=True, editable=False)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    fecha_vigencia = models.DateField('Vigente desde', null=True, blank=True)
    puestos = models.ManyToManyField('planillas.Puesto', blank=True, related_name='documentos_controlados',
                                     verbose_name='Distribución (puestos que lo deben leer)')
    lectura_obligatoria = models.BooleanField('Lectura obligatoria con constancia', default=False)

    class Meta:
        ordering = ['codigo', '-version']
        unique_together = [('codigo', 'version')]
        verbose_name = 'documento controlado'
        verbose_name_plural = 'documentos controlados'

    def __str__(self):
        return f'{self.codigo} v{self.version} {self.titulo}'

    def get_absolute_url(self):
        return reverse('calidad:registro', args=['documentos', self.pk])

    def flujo_propio(self):
        """CAL-12: un documento BPM solo queda vigente con elaboró → revisó → aprobó y sus firmas electrónicas."""
        if self.norma == 'BPM_DIGEMID':
            return ('Documento BPM: se aprueba con su flujo de firmas (Revisar y Aprobar con su contraseña, personas '
                    'distintas), no con la validación genérica. Use «Flujo de firmas».')
        return ''


class Indicador(models.Model):
    """Objetivo e indicador de un área (balanced scorecard / OKR): meta, fórmula, frecuencia, responsable y semáforo.
    Los de fuente automática se calculan con los datos de Ceiba; los manuales se ingresan con su evidencia."""
    PERSPECTIVAS = [('FINANCIERA', 'Financiera'), ('CLIENTES', 'Clientes'), ('PROCESOS', 'Procesos internos'),
                    ('APRENDIZAJE', 'Aprendizaje y crecimiento')]
    FRECUENCIAS = [('MENSUAL', 'Mensual'), ('TRIMESTRAL', 'Trimestral'), ('ANUAL', 'Anual')]
    SENTIDOS = [('MAYOR', 'Mayor es mejor'), ('MENOR', 'Menor es mejor')]
    FUENTES = [('MANUAL', 'Se ingresa (con su evidencia)'), ('DIAS_COBRO', 'Días promedio de cobro'),
               ('NC_ABIERTAS', 'No conformidades abiertas'), ('ACCIONES_VENCIDAS', 'Acciones correctivas vencidas'),
               ('SST_IF', 'SST: índice de frecuencia'), ('SST_IG', 'SST: índice de gravedad'),
               ('SST_IA', 'SST: índice de accidentabilidad'), ('SST_EMO_VENCIDOS', 'SST: exámenes médicos vencidos'),
               ('OEE', 'Producción: OEE promedio (%)')]
    area = models.ForeignKey('planillas.UnidadOrganizativa', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Área')
    objetivo = models.CharField('Objetivo', max_length=200, help_text='Objetivo estratégico u OKR al que aporta')
    nombre = models.CharField('Indicador', max_length=150)
    perspectiva = models.CharField(max_length=11, choices=PERSPECTIVAS, default='PROCESOS')
    formula = models.CharField('Fórmula', max_length=250, blank=True)
    unidad = models.CharField(max_length=20, blank=True, help_text='%, días, S/, unidades…')
    meta = models.DecimalField(max_digits=14, decimal_places=2)
    sentido = models.CharField(max_length=5, choices=SENTIDOS, default='MAYOR')
    tolerancia_pct = models.DecimalField('Ámbar dentro del (%) de la meta', max_digits=5, decimal_places=2,
                                         default=Decimal('10'))
    frecuencia = models.CharField(max_length=10, choices=FRECUENCIAS, default='MENSUAL')
    fuente = models.CharField(max_length=17, choices=FUENTES, default='MANUAL')
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['perspectiva', 'nombre']
        verbose_name = 'indicador'
        verbose_name_plural = 'objetivos e indicadores'

    def __str__(self):
        return self.nombre

    def semaforo(self, valor):
        """'verde', 'ambar' o 'rojo' frente a la meta (ámbar dentro de la tolerancia)."""
        if valor is None:
            return ''
        meta, tolerancia = self.meta, abs(self.meta) * self.tolerancia_pct / 100
        if self.sentido == 'MAYOR':
            return 'verde' if valor >= meta else 'ambar' if valor >= meta - tolerancia else 'rojo'
        return 'verde' if valor <= meta else 'ambar' if valor <= meta + tolerancia else 'rojo'


class Medicion(models.Model):
    indicador = models.ForeignKey(Indicador, on_delete=models.CASCADE, related_name='mediciones')
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6)
    valor = models.DecimalField(max_digits=16, decimal_places=4)
    nota = models.CharField(max_length=250, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['indicador', 'periodo']
        unique_together = [('indicador', 'periodo')]
        verbose_name = 'medición'
        verbose_name_plural = 'mediciones'

    def __str__(self):
        return f'{self.indicador} {self.periodo[4:]}/{self.periodo[:4]}'


class Sugerencia(models.Model):
    """Sugerencia de mejora del personal (Kaizen) con su evaluación e implementación."""
    ESTADOS = [('RECIBIDA', 'Recibida'), ('EVALUACION', 'En evaluación'), ('APROBADA', 'Aprobada'),
               ('IMPLEMENTADA', 'Implementada'), ('DESCARTADA', 'Descartada')]
    MODULO = 'calidad'
    numero = models.CharField('N°', max_length=12, blank=True, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    autor = models.CharField('Propuesta por', max_length=120)
    area = models.ForeignKey('planillas.UnidadOrganizativa', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Área')
    titulo = models.CharField('Título', max_length=150)
    descripcion = models.TextField('Situación actual y propuesta')
    beneficio = models.CharField('Beneficio esperado', max_length=250, blank=True)
    ahorro_estimado = models.DecimalField('Ahorro estimado S/ al año', max_digits=14, decimal_places=2, null=True,
                                          blank=True)
    estado = models.CharField(max_length=12, choices=ESTADOS, default='RECIBIDA')
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Responsable de evaluarla')
    respuesta = models.CharField('Respuesta / decisión', max_length=250, blank=True)
    fecha_implementacion = models.DateField('Implementada el', null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'sugerencia de mejora'
        verbose_name_plural = 'sugerencias de mejora (Kaizen)'

    def __str__(self):
        return f'{self.numero} {self.titulo}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = siguiente('KZN', 'KZ')
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('calidad:registro', args=['sugerencias', self.pk])


class LecturaDocumento(models.Model):
    """CAL-09: constancia de que el trabajador leyó (o fue capacitado en) la versión vigente del documento."""
    documento = models.ForeignKey(DocumentoControlado, on_delete=models.CASCADE, related_name='lecturas')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    fecha = models.DateTimeField(auto_now_add=True)
    capacitacion = models.CharField('Capacitación / evidencia', max_length=200, blank=True)

    class Meta:
        ordering = ['-fecha']
        unique_together = [('documento', 'usuario')]
        verbose_name = 'lectura de documento'
        verbose_name_plural = 'lecturas de documentos'
