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
    ('APERTURA', 'Apertura'),
    ('COMPRA', 'Compra'),
    ('VENTA', 'Venta'),
    ('TESORERIA', 'Caja y bancos'),
    ('INVENTARIO', 'Inventario y costo de ventas'),
    ('CAMBIO', 'Diferencia de cambio'),
    ('ACTIVOS', 'Activos fijos (depreciación y bajas)'),
    ('COSTO', 'Costo de ventas (versión anterior)'),
]
# asientos que el sistema no regenera al centralizar
ORIGENES_FIJOS = ('MANUAL', 'APERTURA')


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


class CentroBeneficio(models.Model):
    """Línea de negocio: agrupa ventas, costos e inventario para el resultado por línea."""
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    responsable = models.CharField(max_length=120, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'centro de beneficio'
        verbose_name_plural = 'centros de beneficio'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class CentroCosto(models.Model):
    TIPOS = [('PRODUCCION', 'Producción'), ('SERVICIO', 'Servicio / apoyo a producción'),
             ('ADMINISTRACION', 'Administración'), ('VENTAS', 'Ventas'), ('FINANZAS', 'Finanzas')]
    # destino analítico del gasto según el tipo de centro (el resto usa el destino de la cuenta)
    DESTINO = {'PRODUCCION': '901', 'SERVICIO': '901', 'ADMINISTRACION': '941', 'VENTAS': '951'}

    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=15, choices=TIPOS, default='ADMINISTRACION',
                            help_text='Producción y servicio: el gasto va al costo de producción (90); '
                                      'administración (94); ventas (95)')
    padre = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='hijos',
                              verbose_name='Depende de')
    centro_beneficio = models.ForeignKey(CentroBeneficio, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='centros_costo', verbose_name='Centro de beneficio')
    responsable = models.CharField(max_length=120, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'centro de costo'
        verbose_name_plural = 'centros de costo'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    @property
    def nivel(self):
        n, c = 0, self.padre
        while c is not None and n < 20:
            n, c = n + 1, c.padre
        return n

    def descendientes_ids(self):
        """IDs del centro y de todos los que dependen de él."""
        todos = list(CentroCosto.objects.values_list('pk', 'padre_id'))
        ids, nuevos = {self.pk}, {self.pk}
        while nuevos:
            nuevos = {pk for pk, padre in todos if padre in nuevos} - ids
            ids |= nuevos
        return ids

    @property
    def beneficio(self):
        """Centro de beneficio propio o heredado del centro superior."""
        c, n = self, 0
        while c is not None and n < 20:
            if c.centro_beneficio_id:
                return c.centro_beneficio
            c, n = c.padre, n + 1
        return None


class PeriodoContable(models.Model):
    periodo = models.CharField(max_length=6, unique=True)
    cerrado = models.BooleanField(default=False)
    pendiente = models.BooleanField('Pendiente de centralizar', default=True,
                                    help_text='Hubo cambios en compras, ventas, caja/bancos o almacén')
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
    extorna = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='extornos',
                                verbose_name='Extorno de', editable=False)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['periodo', 'libro', 'numero']

    @property
    def extornado(self):
        return self.extornos.exists()

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
    centro_beneficio = models.ForeignKey(CentroBeneficio, on_delete=models.PROTECT, null=True, blank=True,
                                         verbose_name='Centro de beneficio')
    documento = models.CharField('Documento', max_length=40, blank=True)
    glosa = models.CharField(max_length=200, blank=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    debe_me = models.DecimalField('Debe US$', max_digits=14, decimal_places=2, default=D0)
    haber_me = models.DecimalField('Haber US$', max_digits=14, decimal_places=2, default=D0)
    es_destino = models.BooleanField(default=False)

    class Meta:
        ordering = ['id']
