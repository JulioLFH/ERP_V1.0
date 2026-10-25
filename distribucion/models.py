"""Distribución (v1.83): hojas de reparto por vehículo y conductor con sus paradas (guías o pedidos) y el resultado
de cada entrega."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

D0 = Decimal('0')


class HojaReparto(models.Model):
    ESTADOS = [('PROGRAMADA', 'Programada'), ('EN_RUTA', 'En ruta'), ('CERRADA', 'Cerrada'), ('ANULADA', 'Anulada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    vehiculo = models.ForeignKey('logistica.Vehiculo', on_delete=models.PROTECT, null=True, blank=True,
                                 verbose_name='Vehículo', related_name='+')
    conductor = models.ForeignKey('logistica.Conductor', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='+')
    almacen = models.ForeignKey('core.Almacen', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                verbose_name='Sale de (almacén)')
    ayudante = models.CharField(max_length=120, blank=True)
    zona = models.CharField('Zona o ruta', max_length=80, blank=True)
    observaciones = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PROGRAMADA')
    salida = models.DateTimeField(null=True, blank=True)
    retorno = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'hoja de reparto'
        verbose_name_plural = 'hojas de reparto'

    def __str__(self):
        return self.numero or f'Hoja {self.pk}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.numero:
            self.numero = f'HR-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(numero=self.numero)


class Parada(models.Model):
    ESTADOS = [('PENDIENTE', 'Pendiente'), ('ENTREGADO', 'Entregado'), ('PARCIAL', 'Entrega parcial'),
               ('NO_ENTREGADO', 'No entregado')]
    MOTIVOS = [('', '—'), ('CERRADO', 'Local cerrado'), ('AUSENTE', 'Cliente ausente'),
               ('RECHAZO', 'Cliente rechazó la mercadería'), ('DIRECCION', 'Dirección errada o no ubicada'),
               ('SIN_PAGO', 'Sin dinero para el pago contra entrega'), ('TIEMPO', 'No alcanzó el tiempo de la ruta'),
               ('OTRO', 'Otro')]
    hoja = models.ForeignKey(HojaReparto, on_delete=models.CASCADE, related_name='paradas')
    orden = models.PositiveIntegerField(default=0)
    guia = models.ForeignKey('logistica.GuiaRemision', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='paradas_reparto')
    pedido = models.ForeignKey('ventas.Cotizacion', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='paradas_reparto')
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+')
    direccion = models.CharField('Dirección', max_length=250)
    contacto = models.CharField('Contacto / teléfono', max_length=120, blank=True)
    bultos = models.PositiveIntegerField(default=0)
    peso = models.DecimalField('Peso (kg)', max_digits=12, decimal_places=2, default=D0)
    cobrar = models.DecimalField('Cobrar contra entrega', max_digits=14, decimal_places=2, default=D0)
    estado = models.CharField(max_length=12, choices=ESTADOS, default='PENDIENTE')
    motivo = models.CharField('Motivo', max_length=10, choices=MOTIVOS, blank=True)
    observacion = models.CharField('Observación', max_length=250, blank=True)
    recibido_por = models.CharField('Recibió', max_length=120, blank=True)
    cobrado = models.DecimalField('Cobrado', max_digits=14, decimal_places=2, default=D0)
    entregado_en = models.DateTimeField(null=True, blank=True)
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ['orden', 'id']

    def __str__(self):
        return f'{self.orden}. {self.cliente}'

    @property
    def documento(self):
        return self.guia or self.pedido
