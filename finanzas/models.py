from decimal import Decimal

from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import MONEDAS, Tercero, r2

D0 = Decimal('0')


class Cuenta(models.Model):
    TIPOS = [('CAJA', 'Caja'), ('BANCO', 'Banco')]
    BANCOS = [('', '---'), ('BCP', 'BCP'), ('BBVA', 'BBVA'), ('SCOTIABANK', 'Scotiabank'),
              ('INTERBANK', 'Interbank'), ('BN', 'Banco de la Nación'), ('OTRO', 'Otro')]

    tipo = models.CharField(max_length=5, choices=TIPOS, default='BANCO')
    nombre = models.CharField(max_length=100)
    banco = models.CharField(max_length=12, choices=BANCOS, blank=True)
    numero = models.CharField('N° de cuenta', max_length=40, blank=True)
    cci = models.CharField('CCI', max_length=30, blank=True)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    es_detracciones = models.BooleanField('Cuenta de detracciones', default=False)
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                        blank=True, related_name='+', limit_choices_to={'imputable': True},
                                        help_text='Ej. 1011 Caja, 1041 Cuenta corriente. Vacío = cuenta por defecto')
    saldo_inicial = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    permite_sobregiro = models.BooleanField('Permite sobregiro', default=False,
                                            help_text='Si está desmarcado, no se aceptan egresos que dejen el saldo '
                                                      'en negativo')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return f'{self.nombre} ({self.moneda})'

    @property
    def simbolo(self):
        return 'US$' if self.moneda == 'USD' else 'S/'

    def saldo_al(self, fecha=None, solo_conciliado=False):
        qs = self.movimientos.all()
        if fecha:
            qs = qs.filter(fecha__lte=fecha)
        if solo_conciliado:
            qs = qs.filter(conciliado=True)
        agg = qs.aggregate(i=Sum('monto', filter=Q(tipo='INGRESO')), e=Sum('monto', filter=Q(tipo='EGRESO')))
        return self.saldo_inicial + (agg['i'] or D0) - (agg['e'] or D0)

    @property
    def saldo(self):
        return self.saldo_al()

    @property
    def saldo_conciliado(self):
        return self.saldo_al(solo_conciliado=True)

    def error_sobregiro(self, egreso, excluir=None):
        """Mensaje si el egreso deja la cuenta en negativo (y la cuenta no admite sobregiro)."""
        if self.permite_sobregiro or not egreso:
            return ''
        saldo = self.saldo
        if excluir is not None and excluir.pk and excluir.cuenta_id == self.pk:
            saldo += excluir.monto if excluir.tipo == 'EGRESO' else -excluir.monto
        if egreso > saldo:
            return (f'Saldo insuficiente en {self}: disponible {self.simbolo} {saldo:,.2f}, egreso '
                    f'{self.simbolo} {egreso:,.2f}. Si la cuenta tiene sobregiro autorizado, márquelo en la cuenta.')
        return ''


class Movimiento(models.Model):
    TIPOS = [('INGRESO', 'Ingreso'), ('EGRESO', 'Egreso')]
    CONCEPTOS = [
        ('COBRANZA', 'Cobranza de comprobantes'),
        ('PAGO', 'Pago a proveedores'),
        ('DETRACCION', 'Detracción'),
        ('ANTICIPO', 'Anticipo de clientes / a proveedores'),
        ('TRANSFERENCIA', 'Transferencia entre cuentas'),
        ('DEPOSITO', 'Depósito / Efectivo en tránsito'),
        ('CAJA_CHICA', 'Caja chica / Entregas a rendir'),
        ('PLANILLA', 'Planilla, CTS, gratificaciones'),
        ('TRIBUTOS', 'Impuestos (IGV, Renta) / AFP / EsSalud'),
        ('SERVICIOS', 'Servicios públicos'),
        ('PRESTAMO', 'Préstamos (personal / accionistas / bancarios)'),
        ('GASTO_BANCARIO', 'Gastos bancarios / ITF / comisiones'),
        ('OTRO', 'Otros'),
    ]
    MEDIOS = [('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia'), ('DEPOSITO', 'Depósito'),
              ('CHEQUE', 'Cheque'), ('TARJETA', 'Tarjeta'), ('YAPE_PLIN', 'Yape / Plin')]

    voucher = models.CharField(max_length=20, blank=True, editable=False)
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name='movimientos')
    fecha = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=7, choices=TIPOS)
    concepto = models.CharField(max_length=15, choices=CONCEPTOS, default='OTRO')
    medio_pago = models.CharField('Medio de pago', max_length=14, choices=MEDIOS, default='TRANSFERENCIA')
    numero_operacion = models.CharField('N° operación / cheque', max_length=40, blank=True)
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT, null=True, blank=True)
    venta = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, null=True, blank=True, related_name='movimientos')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, null=True, blank=True, related_name='movimientos')
    monto = models.DecimalField('Monto (moneda de la cuenta)', max_digits=14, decimal_places=2)
    monto_doc = models.DecimalField('Aplicado al documento (su moneda)', max_digits=14, decimal_places=2,
                                    default=D0, editable=False)
    monto_doc_pen = models.DecimalField('Importe aplicado en S/', max_digits=14, decimal_places=2, default=D0,
                                        editable=False,
                                        help_text='Monto × T.C. del comprobante (lo que se descuenta de la cuenta 12 '
                                                  'o 42)')
    glosa = models.CharField(max_length=250, blank=True)
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                        blank=True, related_name='+', limit_choices_to={'imputable': True},
                                        verbose_name='Cuenta contable (contrapartida)',
                                        help_text='Vacío = según el concepto (Contabilidad > Configuración)')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo')
    conciliado = models.BooleanField(default=False)
    fecha_conciliacion = models.DateField(null=True, blank=True)
    transferencia_par = models.OneToOneField('self', on_delete=models.SET_NULL, null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'{self.voucher} {self.get_tipo_display()} {self.monto}'

    @property
    def documento(self):
        return self.venta or self.compra

    def save(self, *args, **kwargs):
        if not self.voucher:
            from core.models import Serie
            serie, numero = Serie.siguiente('VOU', 'V001')
            self.voucher = f'{"I" if self.tipo == "INGRESO" else "E"}{numero}'
        doc = self.venta or self.compra
        if doc is None:
            self.monto_doc = self.monto_doc_pen = D0
        else:
            if not self.monto_doc:
                # misma moneda: se aplica lo mismo; si no, se convierte con el T.C. del documento
                if doc.moneda == self.cuenta.moneda:
                    self.monto_doc = self.monto
                elif doc.moneda == 'USD':
                    self.monto_doc = r2(self.monto / doc.tc_efectivo)
                else:
                    self.monto_doc = r2(self.monto * doc.tipo_cambio)
            self.monto_doc_pen = r2(self.monto_doc * doc.tc_efectivo)
        super().save(*args, **kwargs)
