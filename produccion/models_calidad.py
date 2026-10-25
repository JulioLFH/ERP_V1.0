"""Calidad a nivel farmacéutico (v1.84, CAL-01 a CAL-08): rastro de auditoría de los resultados, investigaciones de
resultados fuera de especificación (OOS), especificaciones versionadas y aprobadas, contramuestras, estudios de
estabilidad, registro de lote (batch record), desviaciones, control de cambios, quejas por lote, retiro de mercado y
calibración y calificación de equipos (BPM de DIGEMID, DS 021-2018-SA)."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

D0 = Decimal('0')
USUARIO = settings.AUTH_USER_MODEL


def _numero(tipo, prefijo):
    from core.models import Serie
    _, n = Serie.siguiente(tipo, prefijo)
    return f'{prefijo}-{int(n):05d}'


class CambioResultado(models.Model):
    """CAL-01: cada dato del resultado que se cambia deja el valor anterior, el nuevo, quién, cuándo y por qué (el
    original nunca se borra)."""
    resultado = models.ForeignKey('produccion.ResultadoCalidad', on_delete=models.CASCADE, related_name='cambios')
    campo = models.CharField(max_length=30)
    valor_anterior = models.CharField(max_length=120, blank=True)
    valor_nuevo = models.CharField(max_length=120, blank=True)
    motivo = models.CharField(max_length=250)
    usuario = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']


class EspecificacionCalidad(models.Model):
    """CAL-05: versión aprobada del plan de calidad del producto; la inspección guarda la versión con que se hizo."""
    ESTADOS = [('BORRADOR', 'Por aprobar'), ('APROBADA', 'Aprobada (vigente)'), ('OBSOLETA', 'Obsoleta')]
    producto = models.ForeignKey('core.Producto', on_delete=models.CASCADE, related_name='especificaciones')
    version = models.PositiveSmallIntegerField(default=1)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='BORRADOR')
    referencia = models.CharField('Farmacopea o norma', max_length=150, blank=True,
                                  help_text='Ej. USP 47, Ph. Eur. 11, ficha técnica FT-012')
    muestreo = models.CharField('Plan de muestreo', max_length=250, blank=True,
                                help_text='Ej. √n + 1 envases, 3 muestras de 100 mL por lote')
    tamano_muestra = models.DecimalField('Tamaño de muestra', max_digits=12, decimal_places=2, default=D0)
    envases_muestreo = models.PositiveIntegerField('N° de envases a muestrear', default=0)
    caracteristicas = models.JSONField(default=list, editable=False)
    vigente_desde = models.DateField(null=True, blank=True)
    cambios = models.TextField('Qué cambió en esta versión', blank=True)
    aprobado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                     editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['producto', '-version']
        unique_together = [('producto', 'version')]
        verbose_name = 'especificación de calidad'
        verbose_name_plural = 'especificaciones de calidad'

    def __str__(self):
        return f'Especificación {self.producto.codigo} v{self.version}'


class InvestigacionOOS(models.Model):
    """CAL-02: resultado fuera de especificación: fase I (¿error de laboratorio?) y fase II (proceso), reanálisis,
    conclusión y aprobación de QA. Mientras esté abierta no se aprueba la inspección ni se libera el lote."""
    FASES = [('I', 'Fase I: laboratorio'), ('II', 'Fase II: proceso / producción')]
    CONCLUSIONES = [('', 'Pendiente'), ('ERROR_LAB', 'Error de laboratorio demostrado: el resultado se invalida'),
                    ('CONFIRMADO', 'OOS confirmado: el lote no cumple')]
    ESTADOS = [('ABIERTA', 'Abierta'), ('CERRADA', 'Cerrada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    inspeccion = models.ForeignKey('produccion.InspeccionCalidad', on_delete=models.CASCADE, related_name='oos')
    resultado = models.ForeignKey('produccion.ResultadoCalidad', on_delete=models.CASCADE, related_name='oos')
    valor_original = models.CharField(max_length=60, editable=False)
    fase = models.CharField(max_length=2, choices=FASES, default='I')
    hipotesis = models.TextField('Revisión de laboratorio (fase I)', blank=True,
                                 help_text='Cálculos, equipo, estándares, soluciones, analista')
    investigacion_proceso = models.TextField('Investigación del proceso (fase II)', blank=True)
    reanalisis = models.DecimalField('Valor del reanálisis', max_digits=14, decimal_places=4, null=True, blank=True)
    causa_raiz = models.TextField('Causa raíz', blank=True)
    conclusion = models.CharField(max_length=10, choices=CONCLUSIONES, blank=True)
    capa = models.TextField('Acciones correctivas y preventivas', blank=True)
    estado = models.CharField(max_length=7, choices=ESTADOS, default='ABIERTA')
    abierta_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)
    cerrada_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    editable=False)
    cerrada_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'investigación OOS'
        verbose_name_plural = 'investigaciones OOS'

    def __str__(self):
        return f'OOS {self.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _numero('OOS', 'OOS')
        super().save(*args, **kwargs)


class Contramuestra(models.Model):
    """CAL-05: muestra de retención del lote con su ubicación y hasta cuándo se guarda."""
    ESTADOS = [('RETENIDA', 'Retenida'), ('USADA', 'Usada (análisis o reclamo)'), ('DESTRUIDA', 'Destruida')]
    lote = models.ForeignKey('core.Lote', on_delete=models.PROTECT, related_name='contramuestras')
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    unidad = models.CharField(max_length=20, default='unidades')
    ubicacion = models.CharField('Ubicación (archivo de contramuestras)', max_length=120)
    fecha = models.DateField(default=timezone.localdate)
    conservar_hasta = models.DateField('Conservar hasta', help_text='Normalmente el vencimiento del lote + 1 año')
    estado = models.CharField(max_length=9, choices=ESTADOS, default='RETENIDA')
    observacion = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'contramuestra'


class EstudioEstabilidad(models.Model):
    """CAL-05: programa de estabilidad de un lote: condición de almacenamiento y los tiempos de análisis."""
    CONDICIONES = [('25_60', '25 °C ± 2 / 60 % HR ± 5 (largo plazo)'), ('30_65', '30 °C ± 2 / 65 % HR ± 5 (zona IVa)'),
                   ('30_75', '30 °C ± 2 / 75 % HR ± 5 (zona IVb)'), ('40_75', '40 °C ± 2 / 75 % HR ± 5 (acelerado)'),
                   ('5', '5 °C ± 3 (refrigerado)'), ('OTRA', 'Otra')]
    ESTADOS = [('EN_CURSO', 'En curso'), ('CERRADO', 'Cerrado'), ('CANCELADO', 'Cancelado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    lote = models.ForeignKey('core.Lote', on_delete=models.PROTECT, related_name='estabilidades')
    condicion = models.CharField('Condición', max_length=6, choices=CONDICIONES, default='30_75')
    inicio = models.DateField(default=timezone.localdate)
    meses = models.CharField('Tiempos (meses)', max_length=60, default='0,3,6,9,12,18,24',
                             help_text='Separados por coma')
    estado = models.CharField(max_length=9, choices=ESTADOS, default='EN_CURSO')
    conclusion = models.TextField(blank=True)

    class Meta:
        ordering = ['-inicio', '-id']
        verbose_name = 'estudio de estabilidad'
        verbose_name_plural = 'estudios de estabilidad'

    def __str__(self):
        return f'Estabilidad {self.numero} · lote {self.lote.codigo}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _numero('EST', 'EST')
        super().save(*args, **kwargs)


class PuntoEstabilidad(models.Model):
    estudio = models.ForeignKey(EstudioEstabilidad, on_delete=models.CASCADE, related_name='puntos')
    mes = models.PositiveSmallIntegerField()
    programado = models.DateField()
    realizado = models.DateField(null=True, blank=True)
    resultado = models.CharField(max_length=250, blank=True)
    conforme = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ['mes']


class RegistroLote(models.Model):
    """CAL-06: expediente del lote (batch record) de la orden: pasos con quién los hizo y quién los verificó,
    controles en proceso, despeje de línea, rendimiento y conciliación de materiales."""
    orden = models.OneToOneField('produccion.OrdenProduccion', on_delete=models.CASCADE, related_name='registro_lote')
    despeje_linea = models.BooleanField('Despeje de línea verificado', default=False)
    despeje_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    editable=False)
    despeje_en = models.DateTimeField(null=True, blank=True, editable=False)
    observaciones = models.TextField(blank=True)
    revisado_qa = models.BooleanField('Revisado por QA', default=False, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'registro de lote'
        verbose_name_plural = 'registros de lote'

    def __str__(self):
        return f'Registro de lote de {self.orden.numero}'

    @property
    def completo(self):
        return self.despeje_linea and not self.pasos.filter(models.Q(hecho_por__isnull=True) |
                                                            models.Q(verificado_por__isnull=True)).exists()


class PasoRegistroLote(models.Model):
    registro = models.ForeignKey(RegistroLote, on_delete=models.CASCADE, related_name='pasos')
    secuencia = models.PositiveSmallIntegerField(default=10)
    descripcion = models.CharField('Paso', max_length=200)
    parametro = models.CharField('Parámetro / control', max_length=120, blank=True,
                                 help_text='Ej. Temperatura 60-65 °C, tiempo de mezcla 20 min')
    valor = models.CharField('Valor registrado', max_length=120, blank=True)
    hecho_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    hecho_en = models.DateTimeField(null=True, blank=True)
    verificado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    verificado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['secuencia', 'id']


class Desviacion(models.Model):
    """CAL-07: desviación de un procedimiento o especificación, con su criticidad, vinculada al lote, la inspección o
    la orden; una crítica o mayor abierta impide liberar el lote."""
    CRITICIDADES = [('CRITICA', 'Crítica'), ('MAYOR', 'Mayor'), ('MENOR', 'Menor')]
    ESTADOS = [('ABIERTA', 'Abierta'), ('INVESTIGACION', 'En investigación'), ('CERRADA', 'Cerrada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    criticidad = models.CharField(max_length=7, choices=CRITICIDADES, default='MENOR')
    descripcion = models.TextField('Descripción')
    lote = models.ForeignKey('core.Lote', on_delete=models.SET_NULL, null=True, blank=True, related_name='desviaciones')
    orden = models.ForeignKey('produccion.OrdenProduccion', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='desviaciones')
    inspeccion = models.ForeignKey('produccion.InspeccionCalidad', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='desviaciones')
    impacto = models.TextField('Evaluación del impacto en la calidad', blank=True)
    causa_raiz = models.TextField(blank=True)
    capa = models.TextField('Acciones correctivas y preventivas', blank=True)
    estado = models.CharField(max_length=13, choices=ESTADOS, default='ABIERTA')
    registrado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)
    cerrado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    editable=False)
    cerrado_en = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'desviación'
        verbose_name_plural = 'desviaciones'

    def __str__(self):
        return f'Desviación {self.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _numero('DEV', 'DV')
        super().save(*args, **kwargs)


class ControlCambio(models.Model):
    """CAL-07: control de cambios general (proceso, equipo, proveedor, especificación, documento, sistema) con su
    evaluación de impacto y la aprobación de QA; un cambio abierto sobre el producto impide liberar sus lotes."""
    TIPOS = [('PROCESO', 'Proceso / fórmula'), ('EQUIPO', 'Equipo o instalación'), ('PROVEEDOR', 'Proveedor'),
             ('ESPECIFICACION', 'Especificación'), ('DOCUMENTO', 'Documento'), ('SISTEMA', 'Sistema informático'),
             ('OTRO', 'Otro')]
    ESTADOS = [('PROPUESTO', 'Propuesto'), ('APROBADO', 'Aprobado por QA'), ('IMPLEMENTADO', 'Implementado'),
               ('CERRADO', 'Cerrado (eficacia verificada)'), ('RECHAZADO', 'Rechazado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    titulo = models.CharField('Cambio', max_length=200)
    tipo = models.CharField(max_length=14, choices=TIPOS, default='PROCESO')
    descripcion = models.TextField('Descripción y justificación')
    productos = models.ManyToManyField('core.Producto', blank=True, related_name='controles_cambio')
    impacto = models.TextField('Evaluación del impacto (calidad, validación, registro sanitario)', blank=True)
    estado = models.CharField(max_length=12, choices=ESTADOS, default='PROPUESTO')
    registrado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)
    aprobado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                     editable=False)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'control de cambio'
        verbose_name_plural = 'control de cambios'

    def __str__(self):
        return f'Cambio {self.numero}: {self.titulo[:50]}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _numero('CCB', 'CC')
        super().save(*args, **kwargs)


class QuejaLote(models.Model):
    """CAL-07: queja o reclamo de calidad de un cliente vinculada al lote."""
    CLASES = [('CRITICA', 'Crítica (riesgo para el paciente)'), ('MAYOR', 'Mayor'), ('MENOR', 'Menor')]
    ESTADOS = [('ABIERTA', 'Abierta'), ('INVESTIGACION', 'En investigación'), ('CERRADA', 'Cerrada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+')
    lote = models.ForeignKey('core.Lote', on_delete=models.PROTECT, related_name='quejas')
    descripcion = models.TextField('Queja')
    clasificacion = models.CharField(max_length=7, choices=CLASES, default='MENOR')
    investigacion = models.TextField('Investigación y respuesta', blank=True)
    estado = models.CharField(max_length=13, choices=ESTADOS, default='ABIERTA')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'queja de calidad'
        verbose_name_plural = 'quejas de calidad'

    def __str__(self):
        return f'Queja {self.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _numero('QJA', 'QJ')
        super().save(*args, **kwargs)


class RetiroMercado(models.Model):
    """CAL-07: retiro de mercado (o su simulacro) de un lote, con la lista de clientes que lo recibieron (desde la
    trazabilidad) y la efectividad de la recuperación."""
    TIPOS = [('SIMULACRO', 'Simulacro'), ('REAL', 'Retiro real')]
    ESTADOS = [('ABIERTO', 'En curso'), ('CERRADO', 'Cerrado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=9, choices=TIPOS, default='SIMULACRO')
    lote = models.ForeignKey('core.Lote', on_delete=models.PROTECT, related_name='retiros')
    motivo = models.TextField()
    clientes = models.JSONField(default=list, editable=False)
    recuperado = models.DecimalField('Cantidad recuperada', max_digits=14, decimal_places=2, default=D0)
    estado = models.CharField(max_length=7, choices=ESTADOS, default='ABIERTO')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'retiro de mercado'
        verbose_name_plural = 'retiros de mercado'

    def __str__(self):
        return f'Retiro {self.numero} · lote {self.lote.codigo}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _numero('RET', 'RM')
        super().save(*args, **kwargs)

    @property
    def distribuido(self):
        return sum((Decimal(str(c.get('cantidad') or 0)) for c in self.clientes), D0)

    @property
    def efectividad(self):
        return (self.recuperado * 100 / self.distribuido).quantize(Decimal('0.1')) if self.distribuido else None


class CalibracionEquipo(models.Model):
    """CAL-08 / MNT-08: calibración de un instrumento (balanza, pHmetro, termómetro) con su certificado y la próxima
    fecha; un instrumento vencido o no conforme no se acepta en una inspección."""
    RESULTADOS = [('CONFORME', 'Conforme'), ('AJUSTADO', 'Conforme tras ajuste'), ('NO_CONFORME', 'No conforme')]
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, related_name='calibraciones')
    fecha = models.DateField(default=timezone.localdate)
    entidad = models.CharField('Laboratorio de calibración', max_length=120, blank=True)
    certificado = models.CharField('N° de certificado', max_length=60, blank=True)
    resultado = models.CharField(max_length=11, choices=RESULTADOS, default='CONFORME')
    error_encontrado = models.CharField('Error encontrado', max_length=80, blank=True)
    proxima = models.DateField('Próxima calibración')
    observaciones = models.CharField(max_length=250, blank=True)
    registrado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'calibración'
        verbose_name_plural = 'calibraciones'


class CalificacionEquipo(models.Model):
    """CAL-08: calificación de instalación, operación y desempeño (IQ/OQ/PQ) y validación de limpieza."""
    TIPOS = [('IQ', 'Calificación de instalación (IQ)'), ('OQ', 'Calificación de operación (OQ)'),
             ('PQ', 'Calificación de desempeño (PQ)'), ('LIMPIEZA', 'Validación de limpieza')]
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, related_name='calificaciones')
    tipo = models.CharField(max_length=8, choices=TIPOS)
    fecha = models.DateField(default=timezone.localdate)
    protocolo = models.CharField('Protocolo / informe', max_length=80)
    resultado = models.CharField(max_length=11, choices=CalibracionEquipo.RESULTADOS, default='CONFORME')
    vigente_hasta = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['equipo', 'tipo', '-fecha']
        verbose_name = 'calificación de equipo'
        verbose_name_plural = 'calificaciones de equipos'
