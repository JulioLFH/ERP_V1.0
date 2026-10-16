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
    tarifa_km = models.DecimalField('Tarifa por km S/', max_digits=8, decimal_places=4, default=D0,
                                    help_text='Kilometraje en vehículo propio: el gasto es km × tarifa. 0 = no aplica')
    exige_proyecto = models.BooleanField('Exige proyecto', default=False,
                                         help_text='Técnicos en campo: el gasto se carga a un proyecto y sitio')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'categoría de gasto'
        verbose_name_plural = 'categorías de gasto'

    def __str__(self):
        return self.nombre

    def cuenta_en(self, fecha):
        """Cuenta contable vigente en la fecha (historial de cambios de imputación)."""
        cambios = list(self.historial_cuentas.order_by('vigente_desde', 'id'))
        if not cambios:
            return self.cuenta_contable
        vigentes = [c for c in cambios if c.vigente_desde <= fecha]
        if vigentes:
            return vigentes[-1].cuenta
        return cambios[0].cuenta_anterior or self.cuenta_contable


class CategoriaCuentaHistorial(models.Model):
    """Cambio de la cuenta contable de una categoría, con su fecha de vigencia (los gastos anteriores conservan la
    cuenta que tenían)."""
    categoria = models.ForeignKey(CategoriaGasto, on_delete=models.CASCADE, related_name='historial_cuentas')
    cuenta_anterior = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                        related_name='+')
    cuenta = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, related_name='+')
    vigente_desde = models.DateField()
    motivo = models.CharField(max_length=250, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-vigente_desde', '-id']


class IndicadorImpuesto(models.Model):
    """Tratamiento del IGV de cada gasto: define si da crédito fiscal (registro de compras) y la tasa con la que se
    valida base × tasa = IGV."""
    codigo = models.CharField('Código', max_length=4, unique=True)
    nombre = models.CharField(max_length=80)
    tasa = models.DecimalField('Tasa IGV %', max_digits=5, decimal_places=2, default=D0)
    credito_fiscal = models.BooleanField('Da crédito fiscal', default=False)
    codigo_externo = models.CharField('Código en el ERP externo', max_length=10, blank=True,
                                      help_text='Indicador de impuesto en SAP u otro ERP (exportación)')
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['orden', 'codigo']
        verbose_name = 'indicador de impuesto'
        verbose_name_plural = 'indicadores de impuesto'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class MotivoObservacion(models.Model):
    codigo = models.CharField('Código', max_length=20, unique=True)
    nombre = models.CharField(max_length=80)
    exige_comentario = models.BooleanField('Exige comentario', default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'motivo de observación'
        verbose_name_plural = 'motivos de observación'

    def __str__(self):
        return self.nombre


class PlantillaDistribucion(models.Model):
    """Reparto reutilizable de un gasto entre varios centros de costo (los porcentajes suman 100)."""
    nombre = models.CharField(max_length=80, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'plantilla de distribución'
        verbose_name_plural = 'plantillas de distribución'

    def __str__(self):
        return self.nombre


class PlantillaDistribucionLinea(models.Model):
    plantilla = models.ForeignKey(PlantillaDistribucion, on_delete=models.CASCADE, related_name='lineas')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, related_name='+')
    porcentaje = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        ordering = ['id']


class Proyecto(models.Model):
    codigo = models.CharField('Código', max_length=20, unique=True)
    nombre = models.CharField(max_length=150)
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     help_text='Los gastos del proyecto sin centro de costo van a este')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class Sitio(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='sitios')
    codigo = models.CharField('Código', max_length=20)
    nombre = models.CharField(max_length=150)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['proyecto', 'codigo']
        unique_together = [('proyecto', 'codigo')]

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


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
             ('KM', 'Kilometraje (vehículo propio)'), ('SC', 'Sin comprobante'), ('00', 'Otros')]
    ESTADOS = [('PENDIENTE', 'Pendiente'), ('APROBADA', 'Aprobada'), ('OBSERVADA', 'Observada'),
               ('RECHAZADA', 'Rechazada')]
    ORIGENES = [('WEB', 'Pantalla'), ('MOVIL', 'App del celular'), ('WHATSAPP', 'WhatsApp'), ('CORREO', 'Correo'),
                ('TARJETA', 'Estado de cuenta de tarjeta'), ('API', 'API')]
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
    indicador = models.ForeignKey(IndicadorImpuesto, on_delete=models.PROTECT, null=True, blank=True,
                                  verbose_name='Indicador de impuesto')
    categoria = models.ForeignKey(CategoriaGasto, on_delete=models.PROTECT, verbose_name='Categoría')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo')
    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, null=True, blank=True)
    sitio = models.ForeignKey(Sitio, on_delete=models.PROTECT, null=True, blank=True)
    kilometros = models.DecimalField('Kilómetros', max_digits=10, decimal_places=2, default=D0)
    destino = models.CharField('Destino (movilidad)', max_length=150, blank=True)
    glosa = models.CharField(max_length=200)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='PENDIENTE')
    motivo = models.ForeignKey(MotivoObservacion, on_delete=models.PROTECT, null=True, blank=True, editable=False,
                               verbose_name='Motivo de la observación')
    comentario = models.CharField('Comentario del aprobador', max_length=250, blank=True)
    alertas = models.CharField('Fuera de política', max_length=400, blank=True, editable=False)
    justificacion = models.CharField('Justificación', max_length=250, blank=True,
                                     help_text='Obligatoria si el gasto sale de la política')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                               editable=False)
    gasto = models.ForeignKey('finanzas.GastoRendicion', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+', editable=False)
    origen = models.CharField(max_length=8, choices=ORIGENES, default='WEB', editable=False)
    estado_sunat = models.CharField('Validación SUNAT', max_length=200, blank=True, editable=False)
    sunat_valido = models.BooleanField(null=True, editable=False)
    validado_en = models.DateTimeField(null=True, blank=True, editable=False)
    ruc_estado = models.CharField('Estado del RUC', max_length=80, blank=True, editable=False)

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
        """Factura a nombre de la empresa con un indicador que da crédito fiscal: va al registro de compras."""
        return (self.tipo_comprobante == '01' and self.a_nombre_empresa and len(self.ruc) == 11
                and self.indicador_id is not None and self.indicador.credito_fiscal)

    @property
    def distribuida(self):
        return self.pk is not None and self.distribucion.exists()

    @property
    def sin_comprobante(self):
        return self.tipo_comprobante == 'SC' or (self.tipo_comprobante not in ('PM', 'KM') and not self.numero)

    def save(self, *args, **kwargs):
        self.serie = (self.serie or '').strip().upper()
        self.numero = (self.numero or '').strip()
        if self.numero.isdigit():
            self.numero = str(int(self.numero))  # igual que el XML y el registro de compras (sin ceros a la izquierda)
        moneda_entrega = self.rendicion.entrega.moneda
        if self.moneda == moneda_entrega:
            self.importe = self.total
        elif moneda_entrega == 'PEN':
            self.importe = (self.total * self.tipo_cambio).quantize(Decimal('0.01'))
        else:
            self.importe = (self.total / (self.tipo_cambio or 1)).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)


class DistribucionLinea(models.Model):
    """Parte de un gasto cargada a un centro de costo (el asiento lleva una línea por centro)."""
    linea = models.ForeignKey(LineaGasto, on_delete=models.CASCADE, related_name='distribucion')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, related_name='+')
    porcentaje = models.DecimalField(max_digits=6, decimal_places=2)
    importe = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    gasto = models.ForeignKey('finanzas.GastoRendicion', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+')

    class Meta:
        ordering = ['id']


class ObservacionLinea(models.Model):
    """Cada observación o rechazo de un gasto, con su motivo estándar (reporte de errores por usuario y área)."""
    rendicion = models.ForeignKey(Rendicion, on_delete=models.CASCADE, related_name='observaciones')
    linea = models.ForeignKey(LineaGasto, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    documento = models.CharField(max_length=60, blank=True)
    estado = models.CharField(max_length=9)
    motivo = models.ForeignKey(MotivoObservacion, on_delete=models.PROTECT, related_name='+')
    comentario = models.CharField(max_length=250, blank=True)
    rendidor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']


class HistorialRendicion(models.Model):
    rendicion = models.ForeignKey(Rendicion, on_delete=models.CASCADE, related_name='historial')
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    de_estado = models.CharField(max_length=14, blank=True)
    a_estado = models.CharField(max_length=14)
    nivel = models.PositiveSmallIntegerField(null=True, blank=True)
    comentario = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['fecha', 'id']

    @property
    def de_display(self):
        return dict(Rendicion.ESTADOS).get(self.de_estado, '')

    @property
    def a_display(self):
        return dict(Rendicion.ESTADOS).get(self.a_estado, self.a_estado)


class TarjetaCorporativa(models.Model):
    """Tarjeta de la empresa a nombre de un trabajador. Funciona como un fondo (entrega a rendir de tipo tarjeta):
    cada cargo del estado de cuenta es un gasto que el titular sustenta con su comprobante; la cuenta contable de la
    tarjeta (por pagar al banco) recibe los gastos y el pago de la tarjeta la cancela."""
    nombre = models.CharField(max_length=80, help_text='Ej. Visa BCP Gerencia')
    banco = models.CharField(max_length=40, blank=True)
    ultimos4 = models.CharField('Últimos 4 dígitos', max_length=4)
    titular = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+',
                                help_text='Trabajador titular de la tarjeta')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, related_name='+',
                                        limit_choices_to={'imputable': True},
                                        help_text='Cuenta por pagar de la tarjeta (ej. 4699 o una subcuenta)')
    categoria_defecto = models.ForeignKey(CategoriaGasto, on_delete=models.PROTECT, related_name='+',
                                          verbose_name='Categoría por defecto de los cargos')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True)
    entrega = models.OneToOneField('finanzas.EntregaRendir', on_delete=models.PROTECT, null=True, blank=True,
                                   editable=False, related_name='tarjeta')
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'tarjeta corporativa'
        verbose_name_plural = 'tarjetas corporativas'

    def __str__(self):
        return f'{self.nombre} ****{self.ultimos4}'


class MovimientoTarjeta(models.Model):
    tarjeta = models.ForeignKey(TarjetaCorporativa, on_delete=models.PROTECT, related_name='movimientos')
    fecha = models.DateField()
    descripcion = models.CharField('Descripción', max_length=200)
    referencia = models.CharField(max_length=60, blank=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    linea = models.OneToOneField(LineaGasto, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='movimiento_tarjeta')
    importado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']
        unique_together = [('tarjeta', 'fecha', 'descripcion', 'monto', 'referencia')]

    @property
    def con_comprobante(self):
        return self.linea_id is not None and not self.linea.sin_comprobante

    @property
    def dias(self):
        return (timezone.localdate() - self.fecha).days


class ConfigRendicion(models.Model):
    """Integraciones de la rendición de gastos (una por empresa): WhatsApp, buzón de correo y lectura con IA."""
    categoria_defecto = models.ForeignKey(CategoriaGasto, on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name='+', verbose_name='Categoría de los gastos recibidos',
                                          help_text='Para los borradores que llegan por WhatsApp, correo o la app')
    # cierre del mes
    dia_corte = models.PositiveSmallIntegerField(
        'Día de corte de rendiciones', default=0,
        help_text='Lo aprobado después de este día se contabiliza el mes siguiente. 0 = sin corte')
    dias_aviso_corte = models.PositiveSmallIntegerField(
        'Avisar días antes del corte', default=3,
        help_text='Recordatorio a rendidores con borradores y a aprobadores con pendientes')
    # lectura de comprobantes sin QR (OCR / IA)
    ia_activa = models.BooleanField('Leer comprobantes con IA', default=False)
    ia_api_key = models.CharField('Clave API de Anthropic', max_length=200, blank=True,
                                  help_text='Vacío = variable de entorno ANTHROPIC_API_KEY del servidor')
    ia_modelo = models.CharField('Modelo', max_length=40, default='claude-opus-5-5')
    # WhatsApp Business Cloud API
    whatsapp_activo = models.BooleanField('Recibir gastos por WhatsApp', default=False)
    whatsapp_phone_id = models.CharField('Phone number ID', max_length=40, blank=True)
    whatsapp_token = models.CharField('Token de acceso', max_length=400, blank=True)
    whatsapp_verify_token = models.CharField('Token de verificación del webhook', max_length=80, blank=True)
    whatsapp_app_secret = models.CharField('App secret (firma del webhook)', max_length=120, blank=True)
    # buzón que recibe comprobantes reenviados (IMAP)
    correo_activo = models.BooleanField('Recibir gastos por correo', default=False)
    correo_direccion = models.EmailField('Dirección para reenviar comprobantes', blank=True)
    correo_servidor = models.CharField('Servidor IMAP', max_length=120, blank=True)
    correo_puerto = models.PositiveIntegerField('Puerto IMAP', default=993)
    correo_usuario = models.CharField('Usuario IMAP', max_length=120, blank=True)
    correo_clave = models.CharField('Contraseña IMAP', max_length=200, blank=True)

    class Meta:
        verbose_name = 'configuración de rendiciones'

    @classmethod
    def actual(cls):
        return cls.objects.first() or cls.objects.create()


# ---------------------------------------------------------------- integración con un ERP externo (p. ej. SAP)
class MapeoCuentaERP(models.Model):
    """Cuenta del ERP externo que corresponde a una cuenta de Ceiba (vacío = el mismo código)."""
    cuenta = models.OneToOneField('contabilidad.CuentaContable', on_delete=models.CASCADE, related_name='mapeo_erp')
    codigo_externo = models.CharField('Cuenta en el ERP externo', max_length=20)

    class Meta:
        ordering = ['cuenta__codigo']


class EnvioERP(models.Model):
    """Lote de gastos contabilizados exportado al ERP externo."""
    ESTADOS = [('GENERADO', 'Generado (por enviar)'), ('PARCIAL', 'Con errores'), ('CONCILIADO', 'Conciliado')]
    numero = models.CharField('N°', max_length=20, editable=False)
    creado = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='GENERADO')
    es_reintento = models.BooleanField(default=False)

    class Meta:
        ordering = ['-creado', '-id']
        verbose_name = 'envío al ERP externo'
        verbose_name_plural = 'envíos al ERP externo'

    def __str__(self):
        return self.numero

    def save(self, *args, **kwargs):
        if not self.numero:
            from .models import _siguiente
            self.numero = _siguiente('EX', 'EX')
        super().save(*args, **kwargs)


class EnvioERPDetalle(models.Model):
    ESTADOS = [('ENVIADO', 'Enviado'), ('ACEPTADO', 'Aceptado'), ('ERROR', 'Con error')]
    envio = models.ForeignKey(EnvioERP, on_delete=models.CASCADE, related_name='detalles')
    linea = models.ForeignKey(LineaGasto, on_delete=models.PROTECT, related_name='envios_erp')
    importe_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='ENVIADO')
    mensaje = models.CharField(max_length=300, blank=True)
    referencia_externa = models.CharField('Documento en el ERP externo', max_length=40, blank=True)
    intento = models.PositiveSmallIntegerField(default=1)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
