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
                                            help_text='Solo bancos con línea de sobregiro autorizada. Si está '
                                                      'desmarcado (o es caja) el saldo nunca puede quedar en negativo')
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

    @property
    def admite_negativo(self):
        """Solo un banco con sobregiro autorizado puede quedar en negativo; una caja nunca."""
        return self.tipo == 'BANCO' and self.permite_sobregiro

    def saldos_diarios(self, cambios=None, excluir_ids=(), saldo_inicial=None):
        """[(fecha, saldo al cierre del día)] en orden, aplicando cambios {fecha: delta} y sin los excluidos."""
        from collections import defaultdict
        deltas = defaultdict(lambda: D0)
        qs = self.movimientos.exclude(pk__in=[p for p in excluir_ids if p])
        for r in qs.values('fecha', 'tipo').annotate(t=Sum('monto')):
            deltas[r['fecha']] += r['t'] if r['tipo'] == 'INGRESO' else -r['t']
        for fecha, delta in (cambios or {}).items():
            deltas[fecha] += delta
        saldo = self.saldo_inicial if saldo_inicial is None else saldo_inicial
        salida = []
        for fecha in sorted(deltas):
            saldo += deltas[fecha]
            salida.append((fecha, saldo))
        return salida

    def primer_negativo(self, desde=None, **kwargs):
        """(fecha, saldo) del primer día con saldo negativo desde la fecha indicada, o None."""
        for fecha, saldo in self.saldos_diarios(**kwargs):
            if saldo < 0 and (desde is None or fecha >= desde):
                return fecha, saldo
        return None

    def error_sobregiro(self, egreso, excluir=None, fecha=None, cambios=None, excluir_ids=()):
        """Mensaje si el egreso (o los cambios) dejan la cuenta en negativo en algún día desde su fecha.

        Se revisa el saldo día por día: un egreso con fecha anterior a un ingreso no puede usar ese dinero.
        """
        if self.admite_negativo or not (egreso or cambios or excluir_ids or excluir):
            return ''
        from django.utils import timezone as tz
        fecha = fecha or tz.localdate()
        cambios = dict(cambios or {})
        if egreso:
            cambios[fecha] = cambios.get(fecha, D0) - egreso
        ids = list(excluir_ids) + ([excluir.pk] if excluir is not None and excluir.pk else [])
        desde = min(cambios) if cambios else fecha
        negativo = self.primer_negativo(desde=desde, cambios=cambios, excluir_ids=ids)
        if negativo:
            dia, saldo = negativo
            disponible = self.saldo_al(fecha) - sum(
                (m.monto if m.tipo == 'INGRESO' else -m.monto) for m in self.movimientos.filter(
                    pk__in=[i for i in ids if i], fecha__lte=fecha))
            return (f'Saldo insuficiente en {self}: la cuenta quedaría en {self.simbolo} {saldo:,.2f} el '
                    f'{dia:%d/%m/%Y} (disponible al {fecha:%d/%m/%Y}: {self.simbolo} {disponible:,.2f}). '
                    f'Registre primero el ingreso o use una fecha posterior.')
        return ''


class SaldoInicialCambio(models.Model):
    """Historial del saldo inicial de cada caja o banco: todo cambio lleva motivo y documento de sustento."""
    cuenta = models.ForeignKey(Cuenta, on_delete=models.CASCADE, related_name='cambios_saldo_inicial')
    anterior = models.DecimalField(max_digits=14, decimal_places=2)
    nuevo = models.DecimalField(max_digits=14, decimal_places=2)
    motivo = models.CharField(max_length=300)
    sustento = models.BinaryField(null=True, blank=True, editable=False)
    sustento_nombre = models.CharField(max_length=150, blank=True)
    sustento_tipo = models.CharField(max_length=100, blank=True)
    usuario = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'cambio de saldo inicial'
        verbose_name_plural = 'cambios de saldo inicial'


class VigentesManager(models.Manager):
    """Por defecto solo los movimientos vigentes: los anulados no cuentan en saldos, cobros ni contabilidad."""

    def get_queryset(self):
        return super().get_queryset().filter(estado='VIGENTE')


class Movimiento(models.Model):
    TIPOS = [('INGRESO', 'Ingreso'), ('EGRESO', 'Egreso')]
    ESTADOS = [('VIGENTE', 'Vigente'), ('ANULADO', 'Anulado')]
    # sin documento de compra/venta ni transferencia: el sustento es obligatorio
    CONCEPTOS_CON_SUSTENTO = ('DETRACCION', 'ANTICIPO', 'DEPOSITO', 'CAJA_CHICA', 'PLANILLA', 'TRIBUTOS', 'SERVICIOS',
                              'PRESTAMO', 'GASTO_BANCARIO', 'OTRO')
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
    estado = models.CharField(max_length=8, choices=ESTADOS, default='VIGENTE', editable=False)
    motivo_anulacion = models.CharField('Motivo de anulación', max_length=250, blank=True, editable=False)
    anulado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    editable=False)
    anulado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    objects = VigentesManager()
    todos = models.Manager()

    class Meta:
        ordering = ['-fecha', '-id']
        base_manager_name = 'todos'

    def __str__(self):
        return f'{self.voucher} {self.get_tipo_display()} {self.monto}'

    @property
    def documento(self):
        return self.venta or self.compra

    @property
    def requiere_sustento(self):
        return self.concepto in self.CONCEPTOS_CON_SUSTENTO and not (self.venta_id or self.compra_id)

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


class Extracto(models.Model):
    """Estado de cuenta del banco cargado (BCP, BBVA, Interbank o plantilla) para conciliar automáticamente."""
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name='extractos')
    formato = models.CharField(max_length=12, blank=True, help_text='Formato detectado del archivo')
    nombre_archivo = models.CharField(max_length=200, blank=True)
    desde = models.DateField(null=True, blank=True)
    hasta = models.DateField(null=True, blank=True)
    saldo_final = models.DecimalField('Saldo final según banco', max_digits=14, decimal_places=2, null=True,
                                      blank=True)
    cargado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']

    def __str__(self):
        return f'{self.cuenta.nombre} {self.desde or ""} - {self.hasta or ""}'

    @property
    def resumen(self):
        conteo = dict(self.lineas.values_list('estado').annotate(n=models.Count('id')))
        return {e: conteo.get(e, 0) for e, _ in LineaExtracto.ESTADOS}


class LineaExtracto(models.Model):
    ESTADOS = [('PENDIENTE', 'Pendiente'), ('CONCILIADA', 'Conciliada'), ('CREADA', 'Movimiento creado'),
               ('IGNORADA', 'Ignorada')]
    extracto = models.ForeignKey(Extracto, on_delete=models.CASCADE, related_name='lineas')
    fecha = models.DateField()
    descripcion = models.CharField(max_length=250, blank=True)
    operacion = models.CharField('N° operación', max_length=40, blank=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2, help_text='Abono positivo, cargo negativo')
    saldo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    movimiento = models.ForeignKey(Movimiento, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='lineas_extracto')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    regla = models.CharField('Cómo se concilió', max_length=60, blank=True)

    class Meta:
        ordering = ['fecha', 'id']

    @property
    def tipo(self):
        return 'INGRESO' if self.monto > 0 else 'EGRESO'
