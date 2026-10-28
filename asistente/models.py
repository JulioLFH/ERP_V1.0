"""Asistente de planta «Lucho»: el conocimiento para arrancar un proceso y resolver sus defectos queda en la empresa y
llega al operador al pie de la máquina.

- Proceso: el eje escalable (inyección, extrusión, mezclado, empaque…): define sus parámetros y su catálogo de
  defectos, y en qué puestos se usa. Un rubro nuevo = un proceso nuevo, sin tocar el motor.
- FichaParametros + Setpoint: el estándar validado de producto × equipo (y molde) × proceso, con revisión y
  aprobación de otra persona; una sola vigente por combinación.
- CasoPlanta: defecto → causa → acción → resultado, ligado a la orden y al lote, con sus piezas perdidas y su costo.
- Manual: base de conocimiento por equipo, puesto, material o proceso, con su fabricante o proveedor.
- Consulta: cada pregunta a Lucho con su respuesta y sus fuentes (auditoría y uso inadecuado).

Lucho responde solo con fuentes validadas del sistema (asistente/motor.py); sin fuente, lo dice y no inventa valores."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse

USUARIO = settings.AUTH_USER_MODEL
D0 = Decimal('0')


def _numero(tipo, prefijo):
    from core.models import Serie
    _, n = Serie.siguiente(tipo, prefijo)
    return f'{prefijo}-{int(n):05d}'


class Proceso(models.Model):
    codigo = models.CharField('Código', max_length=20, unique=True, help_text='Ej. INY, EXT, MEZ')
    nombre = models.CharField(max_length=80, help_text='Ej. Moldeo por inyección')
    descripcion = models.CharField('Descripción', max_length=250, blank=True)
    puestos = models.ManyToManyField('produccion.CentroTrabajo', blank=True, related_name='procesos_asistente',
                                     verbose_name='Puestos donde se usa',
                                     help_text='Con una orden abierta en estos puestos, Lucho sabe el proceso')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'proceso'

    def __str__(self):
        return self.nombre


class ParametroProceso(models.Model):
    """Plantilla de parámetros del proceso (los que lleva cada ficha)."""
    proceso = models.ForeignKey(Proceso, on_delete=models.CASCADE, related_name='parametros')
    nombre = models.CharField(max_length=80, help_text='Ej. Temperatura de barril, presión de mantenimiento')
    unidad = models.CharField(max_length=15, help_text='Ej. °C, bar, %, mm/s')
    orden = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ['proceso', 'orden', 'id']
        unique_together = [('proceso', 'nombre')]

    def __str__(self):
        return f'{self.nombre} ({self.unidad})'


class Defecto(models.Model):
    """Catálogo de defectos por proceso (rebaba, burbuja, quemado, grumos, bajo peso…), editable por la empresa."""
    proceso = models.ForeignKey(Proceso, on_delete=models.CASCADE, related_name='defectos')
    codigo = models.CharField('Código', max_length=20)
    nombre = models.CharField(max_length=80)
    descripcion = models.CharField('Cómo se reconoce', max_length=250, blank=True)
    sinonimos = models.CharField(max_length=200, blank=True,
                                 help_text='Otras palabras con que la planta lo nombra, separadas por coma')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['proceso', 'nombre']
        unique_together = [('proceso', 'codigo')]

    def __str__(self):
        return self.nombre

    def palabras(self):
        return [self.nombre.lower()] + [s.strip().lower() for s in self.sinonimos.split(',') if s.strip()]


class AjusteDefecto(models.Model):
    """Qué parámetro de la ficha se revisa ante el defecto y en qué sentido (el conocimiento de ingeniería)."""
    SENTIDOS = [('SUBIR', 'Subir'), ('BAJAR', 'Bajar'), ('REVISAR', 'Revisar')]
    defecto = models.ForeignKey(Defecto, on_delete=models.CASCADE, related_name='ajustes')
    parametro = models.ForeignKey(ParametroProceso, on_delete=models.CASCADE, related_name='ajustes')
    sentido = models.CharField(max_length=7, choices=SENTIDOS, default='REVISAR')
    prioridad = models.PositiveSmallIntegerField(default=1, help_text='1 = lo primero que se revisa')
    nota = models.CharField(max_length=200, blank=True, help_text='Ej. la masa llega más fría y compacta peor')

    class Meta:
        ordering = ['defecto', 'prioridad', 'id']
        unique_together = [('defecto', 'parametro')]


class FichaParametros(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('POR_APROBAR', 'Por aprobar'), ('VIGENTE', 'Vigente'),
               ('RETIRADA', 'Retirada'), ('RECHAZADA', 'Rechazada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, related_name='fichas_parametros')
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, related_name='fichas_parametros',
                               verbose_name='Equipo / máquina')
    molde = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='fichas_molde', verbose_name='Molde / herramienta',
                              help_text='Si el proceso usa molde o herramienta')
    proceso = models.ForeignKey(Proceso, on_delete=models.PROTECT, related_name='fichas')
    revision = models.PositiveSmallIntegerField('Revisión', default=1)
    estado = models.CharField(max_length=11, choices=ESTADOS, default='BORRADOR')
    nota = models.TextField('Notas del estándar', blank=True)
    origen_caso = models.ForeignKey('CasoPlanta', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='fichas_propuestas', editable=False)
    propuesta_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+',
                                      editable=False)
    propuesta_en = models.DateTimeField(null=True, blank=True, editable=False)
    aprobada_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)
    aprobada_en = models.DateTimeField(null=True, blank=True, editable=False)
    vigente_desde = models.DateField(null=True, blank=True, editable=False)
    motivo_rechazo = models.CharField(max_length=250, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['producto', 'equipo', '-revision']
        verbose_name = 'ficha de parámetros'
        verbose_name_plural = 'fichas de parámetros'

    def __str__(self):
        return f'{self.numero} Rev. {self.revision}'

    def save(self, *args, **kwargs):
        if not self.numero:
            anterior = FichaParametros.objects.filter(
                producto_id=self.producto_id, equipo_id=self.equipo_id, molde_id=self.molde_id,
                proceso_id=self.proceso_id).exclude(numero='').order_by('-revision').first()
            self.numero = anterior.numero if anterior else _numero('FPA', 'FP')
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('asistente:ficha', args=[self.pk])

    @property
    def combinacion(self):
        return f'{self.producto.nombre} · {self.equipo.nombre}' + (f' · {self.molde.nombre}' if self.molde_id else '')


class Setpoint(models.Model):
    ficha = models.ForeignKey(FichaParametros, on_delete=models.CASCADE, related_name='setpoints')
    parametro = models.ForeignKey(ParametroProceso, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='setpoints')
    nombre = models.CharField('Parámetro', max_length=80)
    unidad = models.CharField(max_length=15)
    minimo = models.DecimalField('Mínimo', max_digits=12, decimal_places=3)
    objetivo = models.DecimalField(max_digits=12, decimal_places=3)
    maximo = models.DecimalField('Máximo', max_digits=12, decimal_places=3)
    orden = models.PositiveSmallIntegerField('Orden de operación', default=10)
    nota = models.CharField(max_length=200, blank=True, help_text='Fuente o detalle (ej. manual del fabricante)')

    class Meta:
        ordering = ['orden', 'id']

    def __str__(self):
        return f'{self.nombre}: {self.objetivo:g} {self.unidad}'

    @property
    def tolerancia(self):
        return max(self.maximo - self.objetivo, self.objetivo - self.minimo)

    def dentro(self, valor):
        return self.minimo <= valor <= self.maximo


class Manual(models.Model):
    """Base de conocimiento: manuales del fabricante, procedimientos y fichas técnicas del material, amarrados al
    equipo, al puesto, al material o al proceso (el archivo va como sustento)."""
    TIPOS = [('MANUAL_EQUIPO', 'Manual del equipo (fabricante)'), ('PROCEDIMIENTO', 'Procedimiento / instructivo'),
             ('FICHA_MATERIAL', 'Ficha técnica del material (proveedor)'), ('LECCION', 'Lección aprendida'),
             ('OTRO', 'Otro')]
    titulo = models.CharField('Título', max_length=150)
    tipo = models.CharField(max_length=14, choices=TIPOS, default='MANUAL_EQUIPO')
    proceso = models.ForeignKey(Proceso, on_delete=models.SET_NULL, null=True, blank=True, related_name='manuales')
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='manuales_asistente')
    puesto = models.ForeignKey('produccion.CentroTrabajo', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='manuales_asistente', verbose_name='Puesto de trabajo')
    material = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='manuales_asistente', verbose_name='Material / producto')
    fabricante = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='manuales_asistente', verbose_name='Fabricante / proveedor')
    documento = models.ForeignKey('calidad.DocumentoControlado', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+', verbose_name='Documento controlado',
                                  help_text='Si el procedimiento ya está en Calidad › Documentos')
    contenido = models.TextField('Puntos clave', blank=True,
                                 help_text='Lo que Lucho cita: arranque, ajustes, advertencias del fabricante')
    validado = models.BooleanField('Validado por ingeniería o calidad', default=False,
                                   help_text='Lucho solo cita conocimiento validado')
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['titulo']
        verbose_name = 'manual o conocimiento'
        verbose_name_plural = 'manuales y conocimiento'

    def __str__(self):
        return self.titulo

    def get_absolute_url(self):
        return reverse('asistente:manual', args=[self.pk])


class CasoPlanta(models.Model):
    ESTADOS = [('PENDIENTE', 'Pendiente de confirmar'), ('RESUELTO', 'Resuelto'), ('NO_RESUELTO', 'No resuelto')]
    numero = models.CharField('N°', max_length=20, editable=False)
    estado = models.CharField(max_length=11, choices=ESTADOS, default='PENDIENTE')
    orden = models.ForeignKey('produccion.OrdenProduccion', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='casos_planta', verbose_name='Orden de producción')
    producto = models.ForeignKey('core.Producto', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='casos_planta')
    molde = models.ForeignKey('produccion.Equipo', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    proceso = models.ForeignKey(Proceso, on_delete=models.SET_NULL, null=True, blank=True, related_name='casos')
    material = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    lote = models.CharField(max_length=60, blank=True)
    ficha = models.ForeignKey(FichaParametros, on_delete=models.SET_NULL, null=True, blank=True, related_name='casos',
                              verbose_name='Ficha usada')
    defecto = models.ForeignKey(Defecto, on_delete=models.PROTECT, null=True, blank=True, related_name='casos')
    problema = models.TextField(blank=True)
    causa = models.TextField(blank=True)
    accion = models.TextField('Acción (ajuste realizado)', blank=True)
    resultado = models.TextField(blank=True)
    piezas_perdidas = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    costo = models.DecimalField('Costo del defecto S/', max_digits=14, decimal_places=2, default=D0, editable=False)
    minutos = models.PositiveIntegerField('Duración (min)', default=0)
    parametros_reales = models.JSONField('Parámetros que resolvieron', default=list, blank=True)
    merma_registrada = models.BooleanField(default=False, editable=False)
    parada = models.ForeignKey('produccion.Parada', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', editable=False)
    no_conformidad = models.ForeignKey('calidad.NoConformidad', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', editable=False)
    aviso = models.ForeignKey('produccion.AvisoMantenimiento', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+', editable=False)
    autor = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)
    resuelto_en = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-creado', '-id']
        verbose_name = 'caso de planta'
        verbose_name_plural = 'casos de planta'

    def __str__(self):
        return self.numero

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _numero('CPL', 'CP')
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('asistente:caso', args=[self.pk])


class Consulta(models.Model):
    """Cada pregunta a Lucho: contexto, respuesta, fuentes y la decisión del operador (auditoría)."""
    COBERTURAS = [('PRECEDENTE', 'Con ficha o caso'), ('PARECIDO', 'Caso parecido'), ('SIN', 'Sin precedente'),
                  ('FUERA', 'Fuera de dominio')]
    usuario = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    caso = models.ForeignKey(CasoPlanta, on_delete=models.SET_NULL, null=True, blank=True, related_name='consultas')
    orden = models.ForeignKey('produccion.OrdenProduccion', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+')
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    defecto = models.ForeignKey(Defecto, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    pregunta = models.TextField(blank=True)
    con_foto = models.BooleanField(default=False)
    respuesta = models.TextField()
    fuentes = models.JSONField(default=list, blank=True)
    recomendados = models.JSONField(default=list, blank=True)  # parámetros sugeridos (no se repiten en el caso)
    cobertura = models.CharField(max_length=10, choices=COBERTURAS, default='SIN')
    fuera_de_dominio = models.BooleanField(default=False)
    motivo_uso = models.CharField('Motivo (uso inadecuado)', max_length=120, blank=True)
    supervisor_avisado = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado', '-id']
        verbose_name = 'consulta a Lucho'
        verbose_name_plural = 'consultas a Lucho'


class ConfigAsistente(models.Model):
    umbral_uso_indebido = models.PositiveSmallIntegerField(
        'Avisar al supervisor desde (consultas fuera de tema en 7 días)', default=3)
    supervisor = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   help_text='Vacío = el jefe del usuario (o quien aprueba fichas)')

    class Meta:
        verbose_name = 'configuración del asistente'

    @classmethod
    def actual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
