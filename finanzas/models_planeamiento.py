"""Planeamiento y gestión financiera (v1.84): presupuesto de caja con escenarios (FIN-02), gestión de cobranza (FIN-03),
propuesta de pagos (FIN-04), líneas de crédito, depósitos a plazo, factoring y cartas fianza (FIN-05), devengo de
préstamos y arrendamientos NIIF 16 (FIN-06) y forwards de moneda (FIN-07)."""
from decimal import Decimal

from django.db import models
from django.utils import timezone

from core.models import MONEDAS, Tercero

D0 = Decimal('0')
ESCENARIOS = [('BASE', 'Base'), ('OPTIMISTA', 'Optimista'), ('PESIMISTA', 'Pesimista')]


# ---------------------------------------------------------------- FIN-02: presupuesto de caja
class PartidaCaja(models.Model):
    """Ingreso o egreso que el sistema no conoce (aporte de capital, venta de un activo, préstamo nuevo, utilidades):
    entra al presupuesto de caja en su mes, en todos los escenarios o en uno."""
    TIPOS = [('INGRESO', 'Ingreso'), ('EGRESO', 'Egreso')]
    fecha = models.DateField('Fecha prevista')
    concepto = models.CharField(max_length=150)
    tipo = models.CharField(max_length=7, choices=TIPOS, default='EGRESO')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    escenario = models.CharField(max_length=9, choices=[('', 'Todos los escenarios')] + ESCENARIOS, blank=True)
    repetir_meses = models.PositiveSmallIntegerField('Repetir (meses)', default=1,
                                                     help_text='1 = solo ese mes; 12 = todos los meses del año')
    notas = models.CharField(max_length=250, blank=True)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']
        verbose_name = 'partida manual de caja'
        verbose_name_plural = 'partidas manuales de caja'

    def __str__(self):
        return self.concepto


class ProyeccionGuardada(models.Model):
    """Foto del presupuesto de caja mensual para compararlo después con lo real (real vs proyectado)."""
    fecha = models.DateField(default=timezone.localdate)
    escenario = models.CharField(max_length=9, choices=ESCENARIOS, default='BASE')
    moneda = models.CharField(max_length=4, default='PEN', help_text='PEN, USD o TODO (consolidado en soles)')
    datos = models.JSONField(default=dict, help_text='{AAAAMM: {"ingresos": n, "egresos": n}}')
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y} {self.get_escenario_display()} {self.moneda}'


# ---------------------------------------------------------------- FIN-03: cobranza
class GestionCobranza(models.Model):
    """Gestión de cobranza (llamada, correo, WhatsApp, visita) con la promesa de pago del cliente."""
    TIPOS = [('LLAMADA', 'Llamada'), ('CORREO', 'Correo'), ('WHATSAPP', 'WhatsApp'), ('VISITA', 'Visita'),
             ('CARTA', 'Carta notarial'), ('OTRO', 'Otro')]
    PROMESAS = [('', 'Sin promesa'), ('PENDIENTE', 'Promesa pendiente'), ('CUMPLIDA', 'Cumplida'),
                ('INCUMPLIDA', 'Incumplida')]
    tercero = models.ForeignKey(Tercero, on_delete=models.CASCADE, related_name='gestiones_cobranza',
                                verbose_name='Cliente')
    venta = models.ForeignKey('ventas.Venta', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              verbose_name='Comprobante')
    fecha = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=8, choices=TIPOS, default='LLAMADA')
    resultado = models.CharField(max_length=250)
    promesa_fecha = models.DateField('Promete pagar el', null=True, blank=True)
    promesa_monto = models.DecimalField('Monto prometido', max_digits=14, decimal_places=2, null=True, blank=True)
    estado_promesa = models.CharField(max_length=10, choices=PROMESAS, blank=True, editable=False)
    usuario = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'gestión de cobranza'
        verbose_name_plural = 'gestiones de cobranza'


class RecordatorioCobranza(models.Model):
    """Recordatorio enviado (o fallido) de un comprobante: no se repite el mismo día de la regla."""
    venta = models.ForeignKey('ventas.Venta', on_delete=models.CASCADE, related_name='recordatorios_cobranza')
    dias = models.SmallIntegerField('Días respecto al vencimiento', help_text='-3 = tres días antes')
    canal = models.CharField(max_length=8, default='CORREO')
    enviado = models.BooleanField(default=False)
    error = models.CharField(max_length=250, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        unique_together = [('venta', 'dias', 'canal')]


class ProvisionCobranza(models.Model):
    """Estimación de cobranza dudosa por pérdida esperada (NIIF 9) al cierre del mes: el asiento lleva la diferencia
    con la provisión acumulada."""
    periodo = models.CharField('Mes (AAAAMM)', max_length=6, unique=True)
    calculada = models.DecimalField('Provisión calculada', max_digits=14, decimal_places=2)
    anterior = models.DecimalField('Provisión acumulada antes', max_digits=14, decimal_places=2, default=D0)
    detalle = models.JSONField(default=list)
    asiento = models.ForeignKey('contabilidad.Asiento', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-periodo']


# ---------------------------------------------------------------- FIN-04: propuesta de pagos
class PropuestaPago(models.Model):
    """Propuesta semanal de pagos: la arma tesorería por vencimiento, prioridad y caja disponible; la aprueba el
    gerente y recién entonces genera el pago masivo (archivo del banco)."""
    ESTADOS = [('BORRADOR', 'Borrador'), ('ENVIADA', 'Por aprobar'), ('APROBADA', 'Aprobada'),
               ('RECHAZADA', 'Rechazada'), ('GENERADA', 'Pago masivo generado'), ('ANULADA', 'Anulada')]
    numero = models.CharField(max_length=20, editable=False)
    fecha = models.DateField('Fecha de pago', default=timezone.localdate)
    hasta = models.DateField('Vencimientos hasta')
    cuenta = models.ForeignKey('finanzas.Cuenta', on_delete=models.PROTECT, related_name='+',
                               verbose_name='Banco desde el que se paga')
    disponible = models.DecimalField('Caja disponible al armarla', max_digits=14, decimal_places=2, default=D0)
    reserva = models.DecimalField('Saldo mínimo a mantener', max_digits=14, decimal_places=2, default=D0)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='BORRADOR', editable=False)
    motivo = models.CharField(max_length=250, blank=True, editable=False)
    aprobado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                     editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    pago_masivo = models.ForeignKey('finanzas.PagoMasivo', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', editable=False)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'propuesta de pagos'
        verbose_name_plural = 'propuestas de pagos'

    def __str__(self):
        return self.numero

    @property
    def total(self):
        return sum((l.monto_pen for l in self.lineas.filter(incluir=True)), D0)


class LineaPropuestaPago(models.Model):
    propuesta = models.ForeignKey(PropuestaPago, on_delete=models.CASCADE, related_name='lineas')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, related_name='+')
    prioridad = models.PositiveSmallIntegerField(default=3, help_text='1 = crítica')
    monto = models.DecimalField('A pagar (moneda del documento)', max_digits=14, decimal_places=2)
    monto_pen = models.DecimalField('En soles', max_digits=14, decimal_places=2, default=D0)
    descuento = models.DecimalField('Descuento por pronto pago', max_digits=14, decimal_places=2, default=D0)
    incluir = models.BooleanField(default=True)
    motivo = models.CharField('Por qué entra o queda fuera', max_length=150, blank=True)

    class Meta:
        ordering = ['prioridad', 'compra__fecha_vencimiento', 'id']


# ---------------------------------------------------------------- FIN-05: financiamiento e inversiones
class LineaCredito(models.Model):
    """Línea de crédito aprobada por un banco: la usan préstamos, cartas fianza, letras en descuento y factoring."""
    TIPOS = [('CAPITAL', 'Capital de trabajo'), ('FIANZAS', 'Cartas fianza'), ('DESCUENTO', 'Descuento de letras'),
             ('FACTORING', 'Factoring / confirming'), ('LEASING', 'Leasing'), ('GLOBAL', 'Global')]
    banco = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='+', verbose_name='Banco')
    tipo = models.CharField(max_length=9, choices=TIPOS, default='GLOBAL')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    limite = models.DecimalField('Límite aprobado', max_digits=14, decimal_places=2)
    tasa = models.DecimalField('Tasa referencial TEA %', max_digits=7, decimal_places=4, default=D0)
    vencimiento = models.DateField('Vence la línea', null=True, blank=True)
    activa = models.BooleanField(default=True)
    notas = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['banco__nombre', 'tipo']
        verbose_name = 'línea de crédito'
        verbose_name_plural = 'líneas de crédito'

    def __str__(self):
        return f'{self.banco.nombre} · {self.get_tipo_display()} ({self.moneda})'


class DepositoPlazo(models.Model):
    """Depósito a plazo o inversión con devengo mensual de intereses (cuenta 77 contra 1xx de intereses por cobrar)."""
    ESTADOS = [('VIGENTE', 'Vigente'), ('CANCELADO', 'Cancelado'), ('ANULADO', 'Anulado')]
    banco = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='+', verbose_name='Banco')
    cuenta = models.ForeignKey('finanzas.Cuenta', on_delete=models.PROTECT, related_name='+',
                               verbose_name='Cuenta de origen y de abono')
    numero = models.CharField('N° del depósito', max_length=40, blank=True)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    tasa = models.DecimalField('TEA %', max_digits=7, decimal_places=4)
    inicio = models.DateField(default=timezone.localdate)
    vencimiento = models.DateField()
    estado = models.CharField(max_length=9, choices=ESTADOS, default='VIGENTE', editable=False)
    devengado = models.DecimalField('Intereses devengados', max_digits=14, decimal_places=2, default=D0,
                                    editable=False)
    devengado_hasta = models.DateField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['vencimiento']
        verbose_name = 'depósito a plazo'
        verbose_name_plural = 'depósitos a plazo e inversiones'

    def __str__(self):
        return f'DPF {self.numero or self.pk} {self.banco.nombre}'


class CesionFactura(models.Model):
    """Factura negociable (Ley 29623) anotada en cuenta en CAVALI y su cesión (factoring) o el confirming del
    proveedor: adelanto neto de intereses y comisión; la factura queda cedida y la cobra la entidad."""
    TIPOS = [('NEGOCIABLE', 'Factura negociable (anotación en CAVALI)'), ('FACTORING', 'Factoring (cesión)'),
             ('CONFIRMING', 'Confirming de un cliente')]
    ESTADOS = [('ANOTADA', 'Anotada'), ('CEDIDA', 'Cedida / adelantada'), ('COBRADA', 'Cobrada por la entidad'),
               ('ANULADA', 'Anulada')]
    venta = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, related_name='cesiones')
    tipo = models.CharField(max_length=10, choices=TIPOS, default='FACTORING')
    entidad = models.ForeignKey(Tercero, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                verbose_name='Entidad de factoring')
    linea = models.ForeignKey(LineaCredito, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='cesiones')
    fecha = models.DateField(default=timezone.localdate)
    codigo_cavali = models.CharField('Código de anotación CAVALI', max_length=40, blank=True)
    monto = models.DecimalField('Monto cedido', max_digits=14, decimal_places=2, default=D0)
    interes = models.DecimalField('Interés (descuento)', max_digits=14, decimal_places=2, default=D0)
    comision = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='ANOTADA')
    movimiento = models.ForeignKey('finanzas.Movimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'factura negociable / factoring'
        verbose_name_plural = 'facturas negociables y factoring'

    @property
    def neto(self):
        return self.monto - self.interes - self.comision


class CartaFianza(models.Model):
    """Carta fianza que la empresa pidió al banco (fiel cumplimiento, adelanto, seriedad, aduanas): central de
    Finanzas, con su línea, comisión y alerta de vencimiento. Las de una obra también se ven aquí."""
    TIPOS = [('FIEL_CUMPLIMIENTO', 'Fiel cumplimiento'), ('ADELANTO', 'Por adelanto'), ('SERIEDAD', 'Seriedad de oferta'),
             ('ADUANAS', 'Aduanas'), ('OTRA', 'Otra')]
    ESTADOS = [('VIGENTE', 'Vigente'), ('RENOVADA', 'Renovada'), ('DEVUELTA', 'Devuelta'), ('EJECUTADA', 'Ejecutada')]
    banco = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='+', verbose_name='Banco')
    beneficiario = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='+')
    linea = models.ForeignKey(LineaCredito, on_delete=models.SET_NULL, null=True, blank=True, related_name='fianzas')
    garantia_obra = models.OneToOneField('obras.GarantiaContrato', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='carta_finanzas', editable=False)
    tipo = models.CharField(max_length=17, choices=TIPOS, default='FIEL_CUMPLIMIENTO')
    numero = models.CharField('N° de carta', max_length=60, blank=True)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    comision_pct = models.DecimalField('Comisión trimestral %', max_digits=6, decimal_places=3, default=D0)
    emision = models.DateField('Emisión')
    vencimiento = models.DateField()
    estado = models.CharField(max_length=9, choices=ESTADOS, default='VIGENTE')
    notas = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['vencimiento']
        verbose_name = 'carta fianza'
        verbose_name_plural = 'cartas fianza'

    @property
    def por_vencer(self):
        from datetime import timedelta
        return self.estado == 'VIGENTE' and self.vencimiento <= timezone.localdate() + timedelta(days=15)


# ---------------------------------------------------------------- FIN-06: devengo y NIIF 16
class DevengoPrestamo(models.Model):
    """Interés devengado del préstamo en el mes (aunque la cuota venza el mes siguiente)."""
    prestamo = models.ForeignKey('finanzas.Prestamo', on_delete=models.CASCADE, related_name='devengos')
    periodo = models.CharField(max_length=6)
    interes = models.DecimalField(max_digits=14, decimal_places=2)
    asiento = models.ForeignKey('contabilidad.Asiento', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')

    class Meta:
        unique_together = [('prestamo', 'periodo')]
        ordering = ['-periodo']


class Arrendamiento(models.Model):
    """Arrendamiento NIIF 16 (local, equipo o vehículo alquilado): activo por derecho de uso y pasivo por el valor
    presente de las cuotas a la tasa incremental; cada mes, depreciación lineal e interés del pasivo."""
    ESTADOS = [('VIGENTE', 'Vigente'), ('TERMINADO', 'Terminado'), ('ANULADO', 'Anulado')]
    numero = models.CharField(max_length=20, editable=False)
    arrendador = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='+')
    descripcion = models.CharField('Bien arrendado', max_length=150)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    cuota = models.DecimalField('Renta mensual (sin IGV)', max_digits=14, decimal_places=2)
    plazo = models.PositiveSmallIntegerField('Plazo (meses)')
    tasa = models.DecimalField('Tasa incremental TEA %', max_digits=7, decimal_places=4)
    inicio = models.DateField('Inicio del contrato')
    pago_anticipado = models.BooleanField('Renta adelantada (al inicio del mes)', default=True)
    costos_directos = models.DecimalField('Costos directos iniciales', max_digits=14, decimal_places=2, default=D0)
    pasivo_inicial = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    derecho_uso = models.DecimalField('Activo por derecho de uso', max_digits=14, decimal_places=2, default=D0,
                                      editable=False)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    estado = models.CharField(max_length=9, choices=ESTADOS, default='VIGENTE', editable=False)
    asiento_inicial = models.ForeignKey('contabilidad.Asiento', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-inicio', '-id']
        verbose_name = 'arrendamiento (NIIF 16)'
        verbose_name_plural = 'arrendamientos (NIIF 16)'

    def __str__(self):
        return f'{self.numero} {self.descripcion}'


class CuotaArrendamiento(models.Model):
    arrendamiento = models.ForeignKey(Arrendamiento, on_delete=models.CASCADE, related_name='cuotas')
    numero = models.PositiveSmallIntegerField()
    fecha = models.DateField()
    cuota = models.DecimalField(max_digits=14, decimal_places=2)
    interes = models.DecimalField(max_digits=14, decimal_places=2)
    amortizacion = models.DecimalField(max_digits=14, decimal_places=2)
    saldo = models.DecimalField('Pasivo al final', max_digits=14, decimal_places=2)
    depreciacion = models.DecimalField(max_digits=14, decimal_places=2)
    asiento = models.ForeignKey('contabilidad.Asiento', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')

    class Meta:
        ordering = ['arrendamiento', 'numero']
        unique_together = [('arrendamiento', 'numero')]


# ---------------------------------------------------------------- FIN-07: coberturas
class Forward(models.Model):
    """Forward de moneda con un banco: compra o venta de US$ a un tipo de cambio pactado en una fecha."""
    TIPOS = [('COMPRA', 'Compra de US$'), ('VENTA', 'Venta de US$')]
    ESTADOS = [('VIGENTE', 'Vigente'), ('LIQUIDADO', 'Liquidado'), ('ANULADO', 'Anulado')]
    banco = models.ForeignKey(Tercero, on_delete=models.PROTECT, related_name='+')
    tipo = models.CharField(max_length=6, choices=TIPOS, default='COMPRA')
    monto_usd = models.DecimalField('Monto US$', max_digits=14, decimal_places=2)
    tc_pactado = models.DecimalField('T.C. pactado', max_digits=8, decimal_places=4)
    fecha = models.DateField('Fecha de pacto', default=timezone.localdate)
    vencimiento = models.DateField()
    estado = models.CharField(max_length=9, choices=ESTADOS, default='VIGENTE')
    notas = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['vencimiento']
        verbose_name = 'forward de moneda'
        verbose_name_plural = 'forwards de moneda'
