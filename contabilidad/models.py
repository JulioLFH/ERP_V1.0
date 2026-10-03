from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.models import Tercero

D0 = Decimal('0')

LIBROS = [
    ('01', 'Caja y bancos'),
    ('05', 'Libro diario'),
    ('08', 'Registro de compras'),
    ('14', 'Registro de ventas'),
]
ORIGENES = [
    ('MANUAL', 'Manual'),
    ('COMPRA', 'Compra'),
    ('VENTA', 'Venta'),
    ('TESORERIA', 'Caja y bancos'),
    ('COSTO', 'Costo de ventas'),
]


class CuentaContable(models.Model):
    NATURALEZAS = [('DEUDORA', 'Deudora'), ('ACREEDORA', 'Acreedora')]

    codigo = models.CharField('Código', max_length=12, unique=True)
    nombre = models.CharField(max_length=200)
    naturaleza = models.CharField(max_length=9, choices=NATURALEZAS, default='DEUDORA')
    imputable = models.BooleanField('Acepta movimientos', default=True,
                                    help_text='Las cuentas de agrupación (con subcuentas) no reciben asientos')
    destino_debe = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                     verbose_name='Destino al debe', help_text='Ej. 94 Gastos administrativos')
    destino_haber = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                      verbose_name='Destino al haber', help_text='Ej. 79 Cargas imputables')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'cuenta contable'
        verbose_name_plural = 'plan de cuentas'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    @property
    def clase(self):
        return self.codigo[:1]

    @property
    def nivel(self):
        return len(self.codigo)


class CuentaDefecto(models.Model):
    """Cuenta que usa la centralización automática para cada tipo de operación."""
    clave = models.CharField(max_length=40, unique=True)
    descripcion = models.CharField('Operación', max_length=120)
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT)

    class Meta:
        ordering = ['id']
        verbose_name = 'cuenta por defecto'
        verbose_name_plural = 'configuración contable'

    def __str__(self):
        return f'{self.descripcion}: {self.cuenta}'

    @classmethod
    def mapa(cls):
        return {d.clave: d.cuenta for d in cls.objects.select_related('cuenta')}


class CentroCosto(models.Model):
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'centro de costo'
        verbose_name_plural = 'centros de costo'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class PeriodoContable(models.Model):
    periodo = models.CharField(max_length=6, unique=True)
    cerrado = models.BooleanField(default=False)
    fecha_centralizacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-periodo']

    def __str__(self):
        return f'{self.periodo[4:]}/{self.periodo[:4]}'

    @classmethod
    def esta_cerrado(cls, periodo):
        return cls.objects.filter(periodo=periodo, cerrado=True).exists()


class Asiento(models.Model):
    numero = models.CharField('Número', max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    periodo = models.CharField(max_length=6, editable=False)
    libro = models.CharField(max_length=2, choices=LIBROS, default='05')
    glosa = models.CharField(max_length=250)
    origen = models.CharField(max_length=10, choices=ORIGENES, default='MANUAL', editable=False)
    compra = models.ForeignKey('compras.Compra', on_delete=models.CASCADE, null=True, blank=True,
                               related_name='asientos')
    venta = models.ForeignKey('ventas.Venta', on_delete=models.CASCADE, null=True, blank=True, related_name='asientos')
    movimiento = models.ForeignKey('finanzas.Movimiento', on_delete=models.CASCADE, null=True, blank=True,
                                   related_name='asientos')
    moneda = models.CharField(max_length=3, default='PEN')
    tipo_cambio = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal('1'))
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['periodo', 'libro', 'numero']

    def __str__(self):
        return f'Asiento {self.numero}'

    def save(self, *args, **kwargs):
        self.periodo = self.fecha.strftime('%Y%m')
        if not self.numero:
            self.numero = self.siguiente_numero(self.periodo, self.libro)
        super().save(*args, **kwargs)

    @staticmethod
    def siguiente_numero(periodo, libro):
        prefijo = f'{libro}-{periodo}-'
        ultimo = (Asiento.objects.filter(numero__startswith=prefijo).order_by('-numero')
                  .values_list('numero', flat=True).first())
        n = int(ultimo.rsplit('-', 1)[1]) + 1 if ultimo else 1
        return f'{prefijo}{n:04d}'

    @property
    def totales(self):
        agg = self.lineas.aggregate(d=Sum('debe'), h=Sum('haber'))
        return agg['d'] or D0, agg['h'] or D0

    @property
    def cuadrado(self):
        d, h = self.totales
        return d == h

    @property
    def documento(self):
        return self.compra or self.venta or self.movimiento


class AsientoLinea(models.Model):
    asiento = models.ForeignKey(Asiento, on_delete=models.CASCADE, related_name='lineas')
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name='lineas')
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT, null=True, blank=True)
    centro_costo = models.ForeignKey(CentroCosto, on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo')
    documento = models.CharField('Documento', max_length=40, blank=True)
    glosa = models.CharField(max_length=200, blank=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    debe_me = models.DecimalField('Debe US$', max_digits=14, decimal_places=2, default=D0)
    haber_me = models.DecimalField('Haber US$', max_digits=14, decimal_places=2, default=D0)
    es_destino = models.BooleanField(default=False)

    class Meta:
        ordering = ['id']
