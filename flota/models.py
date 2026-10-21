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


class LecturaEquipo(models.Model):
    """Lectura de odómetro (km) u horómetro (horas) del equipo (SAP: documento de medición IK11). El parte diario
    queda por aprobar; las que vienen de un vale de combustible o de un viaje se aprueban con su documento. No se
    acepta una lectura menor a la anterior."""
    ORIGENES = [('PARTE', 'Parte diario'), ('COMBUSTIBLE', 'Vale de combustible'), ('VIAJE', 'Viaje / guía'),
                ('OT', 'Orden de mantenimiento'), ('CARGA', 'Carga masiva')]
    ESTADOS = [('PENDIENTE', 'Por aprobar'), ('APROBADA', 'Aprobada'), ('RECHAZADA', 'Rechazada')]
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, related_name='lecturas')
    fecha = models.DateField(default=timezone.localdate)
    valor = models.DecimalField('Lectura', max_digits=12, decimal_places=1)
    origen = models.CharField(max_length=11, choices=ORIGENES, default='PARTE')
    parte = models.CharField('Parte', max_length=20, blank=True)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='PENDIENTE')
    observacion = models.CharField(max_length=200, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-valor', '-id']
        verbose_name = 'lectura de equipo'
        verbose_name_plural = 'lecturas de equipos'

    def __str__(self):
        return f'{self.equipo.codigo} {self.valor} {self.equipo.unidad_medidor} ({self.fecha:%d/%m/%Y})'


class ComponenteEquipo(models.Model):
    """Estructura del equipo por niveles (ISO 14224: sistema › subsistema › componente › pieza) con su repuesto
    del catálogo y la lectura del último cambio. Los planes preventivos pueden ser de un componente."""
    NIVELES = [('SISTEMA', 'Sistema'), ('SUBSISTEMA', 'Subsistema'), ('COMPONENTE', 'Componente'),
               ('PIEZA', 'Pieza / repuesto')]
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, related_name='componentes')
    padre = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='hijos',
                              verbose_name='Pertenece a')
    nivel = models.CharField(max_length=10, choices=NIVELES, default='COMPONENTE')
    codigo = models.CharField('Código', max_length=30, blank=True)
    nombre = models.CharField(max_length=120)
    repuesto = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                 help_text='Repuesto del catálogo que se cambia')
    cantidad_repuesto = models.DecimalField('Cantidad', max_digits=10, decimal_places=2, default=Decimal('1'))
    lectura_ultimo_cambio = models.DecimalField('Lectura del último cambio', max_digits=12, decimal_places=1,
                                                null=True, blank=True)
    fecha_ultimo_cambio = models.DateField('Último cambio', null=True, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['equipo', 'codigo', 'id']
        verbose_name = 'componente de equipo'
        verbose_name_plural = 'componentes de equipos'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'.strip()

    @property
    def ruta(self):
        partes, c, n = [], self, 0
        while c is not None and n < 6:
            partes.append(c.nombre)
            c, n = c.padre, n + 1
        return ' › '.join(reversed(partes))

    @property
    def profundidad(self):
        return [n for n, _ in self.NIVELES].index(self.nivel)
