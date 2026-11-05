"""Agentes con IA por módulo (v1.104): configuración por empresa, conversaciones y la bitácora de cada consulta y de
cada herramienta que usó el agente (qué dato del ERP leyó y para quién)."""
from decimal import Decimal

from django.conf import settings
from django.db import models

D0 = Decimal('0')


class ConfigAgentes(models.Model):
    """Una por empresa: si los agentes están activos, la clave de Anthropic, el modelo y el tope de gasto."""
    MODELOS = [('claude-opus-5-5', 'Claude Opus 5.5 (recomendado)'), ('claude-sonnet-5-5', 'Claude Sonnet 5.5'),
               ('claude-haiku-5-5', 'Claude Haiku 5.5 (más económico)')]
    ESFUERZOS = [('low', 'Bajo (más rápido)'), ('medium', 'Medio'), ('high', 'Alto (más análisis)')]
    activo = models.BooleanField('Agentes activos', default=False)
    api_key = models.CharField('Clave API de Anthropic', max_length=200, blank=True,
                               help_text='Vacío = variable de entorno ANTHROPIC_API_KEY del servidor')
    modelo = models.CharField(max_length=40, choices=MODELOS, default='claude-opus-5-5')
    esfuerzo = models.CharField('Esfuerzo de razonamiento', max_length=6, choices=ESFUERZOS, default='medium')
    tope_mensual_usd = models.DecimalField('Tope de gasto mensual (US$)', max_digits=10, decimal_places=2,
                                           default=Decimal('30'),
                                           help_text='Al llegar al tope los agentes no responden hasta el mes siguiente. '
                                                     '0 = sin tope')
    max_herramientas = models.PositiveSmallIntegerField(
        'Consultas al ERP por pregunta', default=8,
        help_text='Cuántas veces puede leer datos del ERP el agente para responder una pregunta')

    class Meta:
        verbose_name = 'configuración de los agentes'

    @classmethod
    def actual(cls):
        return cls.objects.first() or cls.objects.create()


class Conversacion(models.Model):
    agente = models.CharField(max_length=30)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    titulo = models.CharField(max_length=120, blank=True)
    # mensajes tal como se envían a la API (con los bloques de razonamiento y herramientas): solo se agregan al final
    historial = models.JSONField(default=list, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-actualizado']
        verbose_name = 'conversación con un agente'
        verbose_name_plural = 'conversaciones con agentes'

    def __str__(self):
        return self.titulo or f'Conversación {self.pk}'


class Mensaje(models.Model):
    ROLES = [('USUARIO', 'Usuario'), ('AGENTE', 'Agente')]
    conversacion = models.ForeignKey(Conversacion, on_delete=models.CASCADE, related_name='mensajes')
    rol = models.CharField(max_length=7, choices=ROLES)
    texto = models.TextField()
    error = models.BooleanField(default=False)
    modelo = models.CharField(max_length=40, blank=True)
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_salida = models.PositiveIntegerField(default=0)
    tokens_cache_lectura = models.PositiveIntegerField(default=0)
    tokens_cache_escritura = models.PositiveIntegerField(default=0)
    costo_usd = models.DecimalField('Costo estimado (US$)', max_digits=10, decimal_places=4, default=D0)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado', 'id']


class LlamadaHerramienta(models.Model):
    """Cada dato del ERP que leyó el agente para responder (auditoría)."""
    mensaje = models.ForeignKey(Mensaje, on_delete=models.CASCADE, related_name='llamadas')
    herramienta = models.CharField(max_length=60)
    entrada = models.JSONField(default=dict, blank=True)
    resultado = models.TextField(blank=True)
    ok = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado', 'id']
