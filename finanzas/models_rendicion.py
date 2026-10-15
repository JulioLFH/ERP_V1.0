"""Rendición de gastos (referencia: Rindegastos): categorías, políticas, reglas de aprobación, la rendición con sus
líneas de gasto y su bitácora. Cada rendición pertenece a una entrega a rendir o a un fondo de caja chica."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.models import MONEDAS

D0 = Decimal('0')


class CategoriaGasto(models.Model):
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=80)
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, related_name='+',
                                        limit_choices_to={'imputable': True}, verbose_name='Cuenta de gasto')
    exige_comprobante = models.BooleanField('Exige comprobante', default=True,
                                            help_text='Sin comprobante el gasto sale de la política')
    exige_centro = models.BooleanField('Exige centro de costo', default=False)
    tope = models.DecimalField('Tope por gasto S/', max_digits=12, decimal_places=2, default=D0,
                               help_text='0 = sin tope')
    tope_diario = models.DecimalField('Tope diario S/', max_digits=12, decimal_places=2, default=D0,
                                      help_text='Suma de la categoría en un mismo día. 0 = sin tope')
    es_movilidad = models.BooleanField('Es movilidad', default=False,
                                       help_text='Tope diario legal: 4% de la RMV vigente por día (planilla de '
                                                 'movilidad)')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'categoría de gasto'
        verbose_name_plural = 'categorías de gasto'

    def __str__(self):
        return self.nombre


class PoliticaGasto(models.Model):
    nombre = models.CharField(max_length=80)
    cargo = models.CharField(max_length=80, blank=True,
                             help_text='Cargo del trabajador (ficha de planillas). Vacío = política general')
    dias_para_rendir = models.PositiveSmallIntegerField('Días máximos para rendir', default=15,
                                                        help_text='Desde la entrega del dinero')
    tope_por_rendicion = models.DecimalField('Tope por rendición / viaje S/', max_digits=12, decimal_places=2,
                                             default=D0, help_text='0 = sin tope')
    tope_diario = models.DecimalField('Tope diario de gastos S/', max_digits=12, decimal_places=2, default=D0,
                                      help_text='Todas las categorías de un mismo día. 0 = sin tope')
    comprobante_obligatorio = models.BooleanField(
        'Comprobante obligatorio en todo gasto', default=False,
        help_text='Además de las categorías que ya lo exigen')
    no_permitidas = models.ManyToManyField(CategoriaGasto, blank=True, related_name='+',
                                           verbose_name='Categorías no permitidas')
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['cargo', 'nombre']
        verbose_name = 'política de gastos'
        verbose_name_plural = 'políticas de gastos'

    def __str__(self):
        return f'{self.nombre}' + (f' ({self.cargo})' if self.cargo else '')


class ReglaAprobacion(models.Model):
    TIPOS = [('JEFE', 'Jefe directo del rendidor'), ('USUARIO', 'Usuario específico')]
    nivel = models.PositiveSmallIntegerField(default=1, help_text='1 aprueba primero, luego 2, 3…')
    tipo = models.CharField(max_length=8, choices=TIPOS, default='JEFE')
    aprobador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='+', help_text='Solo para «Usuario específico»')
    monto_desde = models.DecimalField('Desde el monto S/', max_digits=14, decimal_places=2, default=D0,
                                      help_text='Aplica si el total de la rendición es mayor o igual. 0 = siempre')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     help_text='Aplica si algún gasto es de este centro (o sus subcentros). '
                                               'Vacío = todos')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nivel', 'monto_desde', 'id']
        verbose_name = 'regla de aprobación'
        verbose_name_plural = 'reglas de aprobación'

    def __str__(self):
        quien = self.aprobador if self.tipo == 'USUARIO' else 'jefe directo'
        return f'Nivel {self.nivel}: {quien}'


class Rendicion(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('ENVIADA', 'Enviada'), ('EN_REVISION', 'En revisión'),
               ('OBSERVADA', 'Observada'), ('APROBADA', 'Aprobada'), ('RECHAZADA', 'Rechazada'),
               ('CONTABILIZADA', 'Contabilizada'), ('REEMBOLSADA', 'Reembolsada'), ('DEVUELTA', 'Devuelta')]
    EDITABLES = ('BORRADOR', 'OBSERVADA')
    POR_APROBAR = ('ENVIADA', 'EN_REVISION')
    numero = models.CharField('N°', max_length=20, editable=False)
    entrega = models.ForeignKey('finanzas.EntregaRendir', on_delete=models.PROTECT, related_name='rendiciones',
                                verbose_name='Entrega a rendir / caja chica')
    fecha = models.DateField(default=timezone.localdate)
    descripcion = models.CharField('Motivo / viaje', max_length=200)
    estado = models.CharField(max_length=14, choices=ESTADOS, default='BORRADOR')
    rendidor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    niveles = models.JSONField(default=list, blank=True, editable=False,
                               help_text='Aprobadores calculados al enviar: [{nivel, usuario, nombre}]')
    nivel_aprobado = models.PositiveSmallIntegerField(default=0, editable=False)
    enviada_en = models.DateTimeField(null=True, blank=True, editable=False)
    aprobada_en = models.DateTimeField(null=True, blank=True, editable=False)
    contabilizada_en = models.DateTimeField(null=True, blank=True, editable=False)
    fecha_contable = models.DateField(null=True, blank=True, editable=False)
    descuento_planilla = models.DecimalField('Por descontar en planilla', max_digits=14, decimal_places=2,
                                             default=D0, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'rendición de gastos'
        verbose_name_plural = 'rendiciones de gastos'

    def __str__(self):
        return f'Rendición {self.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            from .models import _siguiente
            self.numero = _siguiente('RG', 'RG')
        super().save(*args, **kwargs)

    @property
    def simbolo(self):
        return self.entrega.simbolo

    @property
    def editable(self):
        return self.estado in self.EDITABLES

    def _suma(self, qs):
        return qs.aggregate(s=Sum('importe'))['s'] or D0

    @property
    def total(self):
        """Total en la moneda de la entrega (sin las líneas rechazadas)."""
        return self._suma(self.lineas.exclude(estado='RECHAZADA'))

    @property
    def total_presentado(self):
        return self._suma(self.lineas.all())

    @property
    def nivel_pendiente(self):
        """Siguiente nivel por aprobar: {nivel, usuario, nombre} o None."""
        for n in self.niveles:
            if n['nivel'] > self.nivel_aprobado:
                return n
        return None

    @property
    def fuera_de_politica(self):
        return self.lineas.exclude(alertas='').count()


class LineaGasto(models.Model):
    TIPOS = [('01', 'Factura'), ('03', 'Boleta de venta'), ('02', 'Recibo por honorarios'),
             ('12', 'Ticket de máquina registradora'), ('PM', 'Recibo / planilla de movilidad'),
             ('SC', 'Sin comprobante'), ('00', 'Otros')]
    ESTADOS = [('PENDIENTE', 'Pendiente'), ('APROBADA', 'Aprobada'), ('OBSERVADA', 'Observada'),
               ('RECHAZADA', 'Rechazada')]
    CON_RUC = ('01', '02')  # el proveedor se identifica con RUC
    rendicion = models.ForeignKey(Rendicion, on_delete=models.CASCADE, related_name='lineas')
    fecha = models.DateField(default=timezone.localdate)
    tipo_comprobante = models.CharField('Comprobante', max_length=2, choices=TIPOS, default='01')
    ruc = models.CharField('RUC / DNI del proveedor', max_length=11, blank=True)
    razon_social = models.CharField('Razón social', max_length=200, blank=True)
    serie = models.CharField(max_length=4, blank=True)
    numero = models.CharField('Número', max_length=10, blank=True)
    a_nombre_empresa = models.BooleanField('A nombre de la empresa', default=True,
                                           help_text='Facturas: emitida al RUC de la empresa (da crédito fiscal)')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    tipo_cambio = models.DecimalField('T.C.', max_digits=8, decimal_places=3, default=Decimal('1'))
    base = models.DecimalField('Base imponible', max_digits=14, decimal_places=2, default=D0)
    igv = models.DecimalField('IGV', max_digits=14, decimal_places=2, default=D0)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    importe = models.DecimalField('Importe en la moneda de la entrega', max_digits=14, decimal_places=2, default=D0,
                                  editable=False)
    categoria = models.ForeignKey(CategoriaGasto, on_delete=models.PROTECT, verbose_name='Categoría')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo')
    proyecto = models.CharField('Proyecto o sitio', max_length=80, blank=True)
    glosa = models.CharField(max_length=200)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='PENDIENTE')
    comentario = models.CharField('Comentario del aprobador', max_length=250, blank=True)
    alertas = models.CharField('Fuera de política', max_length=400, blank=True, editable=False)
    justificacion = models.CharField('Justificación', max_length=250, blank=True,
                                     help_text='Obligatoria si el gasto sale de la política')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                               editable=False)
    gasto = models.ForeignKey('finanzas.GastoRendicion', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+', editable=False)

    class Meta:
        ordering = ['fecha', 'id']
        verbose_name = 'gasto rendido'
        verbose_name_plural = 'gastos rendidos'

    def __str__(self):
        doc = f'{self.serie}-{self.numero}' if self.serie else self.numero
        return f'{self.get_tipo_comprobante_display()} {doc}'.strip()

    @property
    def documento(self):
        return f'{self.serie}-{self.numero}' if self.serie and self.numero else (self.numero or '')

    @property
    def con_credito_fiscal(self):
        """Factura válida a nombre de la empresa: va al registro de compras con crédito fiscal."""
        return self.tipo_comprobante == '01' and self.a_nombre_empresa and len(self.ruc) == 11

    def save(self, *args, **kwargs):
        moneda_entrega = self.rendicion.entrega.moneda
        if self.moneda == moneda_entrega:
            self.importe = self.total
        elif moneda_entrega == 'PEN':
            self.importe = (self.total * self.tipo_cambio).quantize(Decimal('0.01'))
        else:
            self.importe = (self.total / (self.tipo_cambio or 1)).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)


class HistorialRendicion(models.Model):
    rendicion = models.ForeignKey(Rendicion, on_delete=models.CASCADE, related_name='historial')
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    de_estado = models.CharField(max_length=14, blank=True)
    a_estado = models.CharField(max_length=14)
    comentario = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['fecha', 'id']

    @property
    def de_display(self):
        return dict(Rendicion.ESTADOS).get(self.de_estado, '')

    @property
    def a_display(self):
        return dict(Rendicion.ESTADOS).get(self.a_estado, self.a_estado)
