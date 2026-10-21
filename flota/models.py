"""Flota y equipos: documentos con vencimiento de equipos y conductores, lecturas de odómetro u horómetro,
componentes, combustible, viajes, asignación de conductores, préstamos (comodato) y gastos por equipo. El maestro es
produccion.Equipo (el vehículo de Logística es un equipo de tipo vehículo)."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

D0 = Decimal('0')


class DocumentoFlota(models.Model):
    """Documento con vencimiento de un equipo (SOAT, revisión técnica, MTC, póliza…) o de un conductor (licencia).
    Los que «bloquean» impiden usar el vehículo o el conductor en una guía de remisión cuando están vencidos."""
    TIPOS = [('SOAT', 'SOAT'), ('REVISION', 'Revisión técnica'), ('TARJETA', 'Tarjeta de propiedad'),
             ('POLIZA', 'Póliza de seguro'), ('MTC', 'Certificado MTC / habilitación'), ('GPS', 'Certificado GPS'),
             ('LICENCIA', 'Licencia de conducir'), ('OTRO', 'Otro')]
    BLOQUEAN = ('SOAT', 'REVISION', 'MTC', 'LICENCIA')
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, null=True, blank=True,
                               related_name='documentos')
    conductor = models.ForeignKey('logistica.Conductor', on_delete=models.CASCADE, null=True, blank=True,
                                  related_name='documentos')
    tipo = models.CharField(max_length=8, choices=TIPOS)
    numero = models.CharField('N°', max_length=40, blank=True)
    emisor = models.CharField('Emisor / aseguradora', max_length=100, blank=True)
    categoria = models.CharField('Categoría', max_length=20, blank=True, help_text='Licencia: A-IIb, A-IIIc…')
    emision = models.DateField('Emisión', null=True, blank=True)
    vencimiento = models.DateField('Vence')
    costo = models.DecimalField('Costo S/', max_digits=12, decimal_places=2, default=D0,
                                help_text='Para el costo del equipo (seguro, revisión…)')
    bloquea = models.BooleanField('Bloquea la guía si vence', default=True)
    archivo = models.BinaryField(null=True, blank=True, editable=False)
    archivo_nombre = models.CharField(max_length=150, blank=True)
    archivo_tipo = models.CharField(max_length=80, blank=True)
    renovado = models.BooleanField('Reemplazado por uno nuevo', default=False, editable=False)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['vencimiento', 'id']
        verbose_name = 'documento de flota'
        verbose_name_plural = 'documentos de flota'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero}'.strip()

    @property
    def titular(self):
        return self.equipo or self.conductor

    @property
    def dias(self):
        return (self.vencimiento - timezone.localdate()).days

    @property
    def vencido(self):
        return self.dias < 0
