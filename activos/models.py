"""Activos fijos: categorías (cuentas y tasa), registro de activos, depreciación mensual y bajas."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Tercero

D0 = Decimal('0')


class CategoriaActivo(models.Model):
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    cuenta_activo = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, related_name='+',
                                      limit_choices_to={'imputable': True}, verbose_name='Cuenta del activo (33/34)')
    cuenta_depreciacion = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT,
                                            related_name='+', null=True, blank=True,
                                            limit_choices_to={'imputable': True},
                                            verbose_name='Depreciación acumulada (39)')
    cuenta_gasto = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, related_name='+',
                                     null=True, blank=True, limit_choices_to={'imputable': True},
                                     verbose_name='Gasto de depreciación (68)')
    deprecia = models.BooleanField('Se deprecia', default=True, help_text='Los terrenos no se deprecian')
    tasa_anual = models.DecimalField('Tasa anual máxima SUNAT %', max_digits=5, decimal_places=2, default=D0,
                                     help_text='Edificaciones 5 %, vehículos 20 %, maquinaria 10 %, '
                                               'equipos de cómputo 25 %, otros 10 %')
    vida_util_meses = models.PositiveIntegerField('Vida útil por defecto (meses)', default=120)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'categoría de activo'
        verbose_name_plural = 'categorías de activos'

    def __str__(self):
        return self.nombre


class ActivoFijo(models.Model):
    ORIGENES = [('COMPRA', 'Compra registrada en el sistema'),
                ('SALDO_INICIAL', 'Saldo inicial (activo existente al empezar)'),
                ('OTRO', 'Otro (aporte, donación, construcción propia)')]
    ESTADOS = [('ACTIVO', 'En uso'), ('BAJA', 'Dado de baja'), ('ANULADO', 'Anulado')]
    MOTIVOS_BAJA = [('VENTA', 'Venta'), ('SINIESTRO', 'Siniestro / robo'), ('OBSOLESCENCIA', 'Obsolescencia / desuso'),
                    ('DONACION', 'Donación'), ('OTRO', 'Otro')]

    codigo = models.CharField('Código', max_length=20, unique=True, blank=True)
    nombre = models.CharField('Descripción', max_length=200)
    categoria = models.ForeignKey(CategoriaActivo, on_delete=models.PROTECT, related_name='activos',
                                  verbose_name='Categoría')
    marca = models.CharField(max_length=80, blank=True)
    modelo = models.CharField(max_length=80, blank=True)
    serie = models.CharField('N° de serie / placa', max_length=80, blank=True)
    ubicacion = models.CharField('Ubicación', max_length=120, blank=True)
    responsable = models.CharField(max_length=120, blank=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     verbose_name='Centro de costo', help_text='La depreciación se carga a este centro')
    # adquisición
    origen = models.CharField(max_length=15, choices=ORIGENES, default='COMPRA')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='activos', verbose_name='Factura de compra')
    producto = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    proveedor = models.ForeignKey(Tercero, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    documento = models.CharField('Documento de adquisición', max_length=60, blank=True,
                                 help_text='Factura, contrato o acta (si no está registrado en el sistema)')
    fecha_adquisicion = models.DateField('Fecha de adquisición')
    fecha_uso = models.DateField('Inicio de uso', help_text='La depreciación empieza en este mes')
    valor = models.DecimalField('Valor de adquisición S/', max_digits=14, decimal_places=2,
                                help_text='Costo sin IGV, en soles')
    valor_residual = models.DecimalField('Valor residual S/', max_digits=14, decimal_places=2, default=D0)
    vida_util_meses = models.PositiveIntegerField('Vida útil (meses)')
    dep_inicial = models.DecimalField('Depreciación acumulada inicial S/', max_digits=14, decimal_places=2,
                                      default=D0, help_text='Solo activos existentes: depreciación ya registrada')
    dep_inicial_hasta = models.DateField('Depreciado hasta', null=True, blank=True,
                                         help_text='Fecha hasta la que corresponde la depreciación inicial')
    # contabilidad del alta: la cuenta que usó la compra (si difiere de la categoría se reclasifica)
    cuenta_origen = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                      blank=True, related_name='+', editable=False)
    fecha_alta = models.DateField('Fecha de registro contable', default=timezone.localdate, editable=False)
    # estado
    estado = models.CharField(max_length=8, choices=ESTADOS, default='ACTIVO')
    fecha_baja = models.DateField(null=True, blank=True)
    motivo_baja = models.CharField(max_length=15, choices=MOTIVOS_BAJA, blank=True)
    detalle_baja = models.CharField('Detalle de la baja / anulación', max_length=250, blank=True)
    baja_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='+')
    baja_en = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'activo fijo'
        verbose_name_plural = 'activos fijos'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            from core.models import Serie
            while True:
                _, numero = Serie.siguiente('ACT', 'AF')
                codigo = f'AF{int(numero):06d}'
                if not ActivoFijo.objects.filter(codigo=codigo).exists():
                    self.codigo = codigo
                    break
        super().save(*args, **kwargs)

    @property
    def deprecia(self):
        return self.categoria.deprecia and self.vida_util_meses > 0

    @property
    def base_depreciable(self):
        return self.valor - self.valor_residual

    @property
    def depreciacion_registrada(self):
        return self.dep_inicial + sum((d.cuota for d in self.depreciaciones.all()), D0)

    @property
    def valor_neto(self):
        return self.valor - self.depreciacion_registrada

    @property
    def tasa(self):
        return (Decimal(1200) / self.vida_util_meses).quantize(Decimal('0.01')) if self.deprecia else D0

    @property
    def bloqueado(self):
        """Con depreciación registrada ya no cambian valor, vida útil, inicio de uso ni categoría."""
        return self.depreciaciones.exists()


class ProcesoDepreciacion(models.Model):
    """Cálculo de la depreciación de un mes: se hace en orden y solo se revierte el último."""
    periodo = models.CharField(max_length=6, unique=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    cantidad = models.PositiveIntegerField(default=0)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-periodo']
        verbose_name = 'depreciación del mes'
        verbose_name_plural = 'depreciaciones mensuales'

    def __str__(self):
        return f'Depreciación {self.periodo[4:]}/{self.periodo[:4]}'


class Depreciacion(models.Model):
    proceso = models.ForeignKey(ProcesoDepreciacion, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='lineas')
    activo = models.ForeignKey(ActivoFijo, on_delete=models.PROTECT, related_name='depreciaciones')
    periodo = models.CharField(max_length=6)
    meses = models.PositiveIntegerField(default=1, help_text='Más de 1 cuando incluye meses anteriores pendientes')
    cuota = models.DecimalField(max_digits=14, decimal_places=2)
    acumulada = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['periodo', 'activo__codigo']
        unique_together = [('activo', 'periodo')]
