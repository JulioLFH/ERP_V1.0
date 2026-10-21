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


class CargaCombustible(models.Model):
    """Vale o carga de combustible (FLO-05): galones, precio, lectura y grifo; el rendimiento (km u horas por galón)
    se calcula contra la carga anterior a tanque lleno y se marca el desvío frente al rendimiento esperado. El costo
    va al centro de costo del equipo (el asiento sale de la factura del grifo registrada en Compras)."""
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, related_name='cargas')
    fecha = models.DateField(default=timezone.localdate)
    vale = models.CharField('N° de vale', max_length=30, blank=True)
    grifo = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              verbose_name='Grifo / proveedor')
    conductor = models.ForeignKey('logistica.Conductor', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+')
    tarjeta = models.CharField('Tarjeta de combustible', max_length=30, blank=True)
    galones = models.DecimalField(max_digits=10, decimal_places=3)
    precio_galon = models.DecimalField('Precio por galón S/', max_digits=10, decimal_places=4)
    importe = models.DecimalField('Importe S/', max_digits=12, decimal_places=2, default=D0, editable=False)
    lectura = models.DecimalField('Odómetro / horómetro', max_digits=12, decimal_places=1, null=True, blank=True)
    tanque_lleno = models.BooleanField('Tanque lleno', default=True,
                                       help_text='El rendimiento se calcula entre cargas a tanque lleno')
    rendimiento = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, editable=False)
    desvio = models.BooleanField('Desvío de rendimiento', default=False, editable=False)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Centro de costo',
                                     help_text='Vacío = el del equipo')
    observacion = models.CharField(max_length=200, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'carga de combustible'
        verbose_name_plural = 'cargas de combustible'

    def __str__(self):
        return f'Vale {self.vale or self.pk} · {self.equipo.codigo}'


class GastoEquipo(models.Model):
    """Otros gastos del equipo para su costo total (peajes, lavado, estacionamiento, multas, seguros…)."""
    TIPOS = [('PEAJE', 'Peaje'), ('LAVADO', 'Lavado'), ('ESTACIONAMIENTO', 'Estacionamiento'), ('MULTA', 'Multa'),
             ('SEGURO', 'Seguro'), ('LLANTAS', 'Llantas'), ('OTRO', 'Otro')]
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, related_name='gastos')
    fecha = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=15, choices=TIPOS, default='PEAJE')
    descripcion = models.CharField('Descripción', max_length=150, blank=True)
    documento = models.CharField('Comprobante', max_length=40, blank=True)
    importe = models.DecimalField('Importe S/', max_digits=12, decimal_places=2)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'gasto de equipo'
        verbose_name_plural = 'gastos de equipos'


class AsignacionConductor(models.Model):
    """Historial de conductores asignados al vehículo o equipo (FLO-07)."""
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.CASCADE, related_name='asignaciones')
    conductor = models.ForeignKey('logistica.Conductor', on_delete=models.PROTECT, related_name='asignaciones')
    desde = models.DateField(default=timezone.localdate)
    hasta = models.DateField(null=True, blank=True)
    observacion = models.CharField(max_length=200, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)

    class Meta:
        ordering = ['-desde', '-id']
        verbose_name = 'asignación de conductor'
        verbose_name_plural = 'asignaciones de conductores'


class Viaje(models.Model):
    """Viaje del vehículo propio (FLO-07): sale con la guía de remisión (transporte privado) con el km de salida y
    se cierra con el km de llegada, que entra como lectura del odómetro."""
    ESTADOS = [('EN_RUTA', 'En ruta'), ('CERRADO', 'Cerrado'), ('ANULADO', 'Anulado')]
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, related_name='viajes')
    conductor = models.ForeignKey('logistica.Conductor', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='viajes')
    guia = models.OneToOneField('logistica.GuiaRemision', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='viaje')
    salida = models.DateField(default=timezone.localdate)
    km_salida = models.DecimalField('Km de salida', max_digits=12, decimal_places=1, null=True, blank=True)
    llegada = models.DateField(null=True, blank=True)
    km_llegada = models.DecimalField('Km de llegada', max_digits=12, decimal_places=1, null=True, blank=True)
    origen = models.CharField(max_length=150, blank=True)
    destino = models.CharField(max_length=150, blank=True)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='EN_RUTA')
    observacion = models.CharField(max_length=200, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)

    class Meta:
        ordering = ['-salida', '-id']
        verbose_name = 'viaje'

    @property
    def recorrido(self):
        if self.km_llegada is not None and self.km_salida is not None:
            return self.km_llegada - self.km_salida
        return None


class PrestamoEquipo(models.Model):
    """Equipo prestado o en comodato a un cliente (FLO-07), con acta de entrega y de devolución."""
    ESTADOS = [('PRESTADO', 'Prestado'), ('DEVUELTO', 'Devuelto')]
    numero = models.CharField('Acta N°', max_length=20, editable=False)
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, related_name='prestamos')
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+')
    entrega = models.DateField('Fecha de entrega', default=timezone.localdate)
    devolucion_prevista = models.DateField('Devolución prevista', null=True, blank=True)
    devolucion = models.DateField('Fecha de devolución', null=True, blank=True)
    ubicacion = models.CharField('Ubicación en el cliente', max_length=150, blank=True)
    condiciones = models.TextField('Estado y condiciones de entrega', blank=True)
    estado_devolucion = models.TextField('Estado en la devolución', blank=True)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='PRESTADO')
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       related_name='+', editable=False)

    class Meta:
        ordering = ['-entrega', '-id']
        verbose_name = 'préstamo de equipo'
        verbose_name_plural = 'préstamos de equipos'

    def __str__(self):
        return f'Acta {self.numero}'

    @property
    def vencido(self):
        return self.estado == 'PRESTADO' and self.devolucion_prevista and \
            self.devolucion_prevista < timezone.localdate()
