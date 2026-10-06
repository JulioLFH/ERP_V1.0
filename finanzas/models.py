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
    letra = models.ForeignKey('Letra', on_delete=models.PROTECT, null=True, blank=True, related_name='movimientos',
                              editable=False)
    cheque = models.ForeignKey('Cheque', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos',
                               editable=False)
    entrega = models.ForeignKey('EntregaRendir', on_delete=models.PROTECT, null=True, blank=True,
                                related_name='movimientos', editable=False,
                                help_text='Entrega a rendir o fondo de caja chica que entrega o devuelve')
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
        return self.venta or self.compra or self.letra

    @property
    def requiere_sustento(self):
        return self.concepto in self.CONCEPTOS_CON_SUSTENTO and not (self.venta_id or self.compra_id)

    # ---- anticipos: lo recibido (o entregado) se aplica después a comprobantes del mismo tercero
    @property
    def es_anticipo(self):
        return self.concepto == 'ANTICIPO' and self.estado == 'VIGENTE'

    @property
    def anticipo_aplicado(self):
        return self.aplicaciones_anticipo.aggregate(s=Sum('monto_doc'))['s'] or D0

    @property
    def anticipo_disponible(self):
        return self.monto - self.anticipo_aplicado if self.es_anticipo else D0

    def save(self, *args, **kwargs):
        if not self.voucher:
            from core.models import Serie
            serie, numero = Serie.siguiente('VOU', 'V001')
            self.voucher = f'{"I" if self.tipo == "INGRESO" else "E"}{numero}'
        doc = self.venta or self.compra or self.letra
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


def _siguiente(tipo, prefijo):
    """Correlativo propio de cada documento de tesorería (LT-00000001, ER-00000001…)."""
    from core.models import Serie
    _, numero = Serie.siguiente(tipo, prefijo)
    return f'{prefijo}-{numero}'


# ---------------------------------------------------------------- letras
class CanjeLetras(models.Model):
    """Canje de facturas por letras: las facturas quedan canceladas y la deuda pasa a las letras (123 / 423)."""
    TIPOS = [('COBRAR', 'Letras por cobrar (clientes)'), ('PAGAR', 'Letras por pagar (proveedores)')]
    numero = models.CharField('N° de canje', max_length=20, editable=False)
    tipo = models.CharField(max_length=6, choices=TIPOS)
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT)
    fecha = models.DateField(default=timezone.localdate)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    tipo_cambio = models.DecimalField('T.C. del canje', max_digits=8, decimal_places=3, default=Decimal('1'))
    glosa = models.CharField(max_length=250, blank=True)
    estado = models.CharField(max_length=8, choices=Movimiento.ESTADOS, default='VIGENTE', editable=False)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'canje de letras'
        verbose_name_plural = 'canjes de letras'

    def __str__(self):
        return f'Canje {self.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _siguiente('CJL', 'CJ')
        super().save(*args, **kwargs)

    @property
    def tc_efectivo(self):
        return self.tipo_cambio if self.moneda == 'USD' else Decimal('1')

    @property
    def total(self):
        return self.letras.exclude(estado='ANULADA').aggregate(s=Sum('monto'))['s'] or D0


class Letra(models.Model):
    TIPOS = CanjeLetras.TIPOS
    ESTADOS = [('CARTERA', 'En cartera'), ('COBRANZA', 'En cobranza en el banco'),
               ('DESCUENTO', 'En descuento en el banco'), ('CANCELADA', 'Cancelada'), ('PROTESTADA', 'Protestada'),
               ('RENOVADA', 'Renovada'), ('ANULADA', 'Anulada')]
    ABIERTAS = ('CARTERA', 'COBRANZA', 'DESCUENTO', 'PROTESTADA')
    numero = models.CharField('N° de letra', max_length=20)
    tipo = models.CharField(max_length=6, choices=TIPOS)
    canje = models.ForeignKey(CanjeLetras, on_delete=models.PROTECT, null=True, blank=True, related_name='letras')
    renovada_de = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='renovaciones', verbose_name='Renueva a')
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT, verbose_name='Aceptante / girador')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    tipo_cambio = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal('1'))
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    fecha_giro = models.DateField('Fecha de giro', default=timezone.localdate)
    fecha_vencimiento = models.DateField('Vencimiento')
    banco = models.ForeignKey(Cuenta, on_delete=models.PROTECT, null=True, blank=True, related_name='letras',
                              help_text='Banco donde está en cobranza o descuento')
    codigo_banco = models.CharField('N° único / código del banco', max_length=40, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='CARTERA')
    fecha_estado = models.DateField(null=True, blank=True)
    glosa = models.CharField(max_length=250, blank=True)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha_vencimiento', 'id']

    def __str__(self):
        return f'Letra {self.numero}'

    @property
    def tipo_comprobante(self):  # documento de los asientos de cobro/pago
        return 'LT'

    @property
    def numero_completo(self):
        return self.numero

    @property
    def simbolo(self):
        return 'US$' if self.moneda == 'USD' else 'S/'

    @property
    def tc_efectivo(self):
        return self.tipo_cambio if self.moneda == 'USD' else Decimal('1')

    @property
    def pagado(self):
        return self.movimientos.aggregate(s=Sum('monto_doc'))['s'] or D0

    @property
    def saldo(self):
        if self.estado in ('ANULADA', 'RENOVADA'):
            return D0
        return self.monto - self.pagado

    @property
    def dias_vencido(self):
        return (timezone.localdate() - self.fecha_vencimiento).days


# ---------------------------------------------------------------- entregas a rendir y caja chica
class EntregaRendir(models.Model):
    TIPOS = [('ENTREGA', 'Entrega a rendir cuenta'), ('CAJA_CHICA', 'Fondo fijo de caja chica')]
    ESTADOS = [('ABIERTA', 'Abierta'), ('LIQUIDADA', 'Liquidada / cerrada')]
    numero = models.CharField('N°', max_length=20, editable=False)
    tipo = models.CharField(max_length=10, choices=TIPOS, default='ENTREGA')
    responsable = models.ForeignKey(Tercero, on_delete=models.PROTECT, verbose_name='Responsable',
                                    help_text='Trabajador que recibe el dinero y rinde cuenta')
    fecha = models.DateField(default=timezone.localdate)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    monto_fondo = models.DecimalField('Monto del fondo fijo', max_digits=14, decimal_places=2, default=D0,
                                      help_text='Solo caja chica: importe que se repone al rendir')
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                        blank=True, related_name='+', limit_choices_to={'imputable': True},
                                        help_text='Vacío = 1413 entregas a rendir o 1021 caja chica')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo de los gastos')
    motivo = models.CharField(max_length=250)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ABIERTA')
    fecha_liquidacion = models.DateField(null=True, blank=True)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'entrega a rendir / caja chica'
        verbose_name_plural = 'entregas a rendir y cajas chicas'

    def __str__(self):
        return f'{self.numero} {self.responsable.nombre}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _siguiente('ER', 'CC' if self.tipo == 'CAJA_CHICA' else 'ER')
        super().save(*args, **kwargs)

    @property
    def simbolo(self):
        return 'US$' if self.moneda == 'USD' else 'S/'

    def cuenta(self, cta=None):
        """Cuenta contable del fondo (lo que el responsable tiene en su poder)."""
        if self.cuenta_contable_id:
            return self.cuenta_contable
        if cta is None:
            from contabilidad.models import CuentaDefecto
            cta = CuentaDefecto.mapa()
        return cta['caja_chica_fondo'] if self.tipo == 'CAJA_CHICA' else cta['entregas_rendir']

    def _movs(self, tipo):
        return self.movimientos.filter(tipo=tipo).aggregate(s=Sum('monto'))['s'] or D0

    @property
    def entregado(self):
        return self._movs('EGRESO')

    @property
    def devuelto(self):
        return self._movs('INGRESO')

    @property
    def rendido(self):
        gastos = self.gastos.aggregate(s=Sum('monto'))['s'] or D0
        return gastos + (self.aplicaciones.aggregate(s=Sum('monto_doc'))['s'] or D0)

    @property
    def saldo(self):
        """En poder del responsable: entregado − devuelto − rendido (negativo = la empresa le debe)."""
        return self.entregado - self.devuelto - self.rendido

    @property
    def por_reponer(self):
        return max(self.monto_fondo - self.saldo, D0) if self.tipo == 'CAJA_CHICA' else D0


class GastoRendicion(models.Model):
    """Gasto rendido sin factura registrada en compras (ticket, recibo, planilla de movilidad, etc.)."""
    DOCUMENTOS = [('12', 'Ticket de máquina registradora'), ('03', 'Boleta de venta'), ('PM', 'Planilla de movilidad'),
                  ('RI', 'Recibo / declaración jurada'), ('00', 'Otros')]
    entrega = models.ForeignKey(EntregaRendir, on_delete=models.CASCADE, related_name='gastos')
    fecha = models.DateField(default=timezone.localdate)
    tipo_documento = models.CharField('Documento', max_length=2, choices=DOCUMENTOS, default='12')
    numero_documento = models.CharField('N° documento', max_length=30, blank=True)
    proveedor = models.CharField(max_length=150, blank=True)
    descripcion = models.CharField('Descripción', max_length=200)
    cuenta_contable = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, related_name='+',
                                        limit_choices_to={'imputable': True}, verbose_name='Cuenta de gasto')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo')
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']


# ---------------------------------------------------------------- aplicaciones (cancelación sin caja)
class Aplicacion(models.Model):
    """Cancela un comprobante sin mover caja: con un anticipo, con letras (canje) o con un fondo a rendir."""
    ORIGENES = [('ANTICIPO', 'Aplicación de anticipo'), ('CANJE', 'Canje por letras'),
                ('RENDICION', 'Pagado con entrega a rendir / caja chica')]
    origen = models.CharField(max_length=10, choices=ORIGENES)
    fecha = models.DateField(default=timezone.localdate)
    venta = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='aplicaciones')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='aplicaciones')
    monto_doc = models.DecimalField('Aplicado (moneda del documento)', max_digits=14, decimal_places=2)
    monto_doc_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    anticipo = models.ForeignKey(Movimiento, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='aplicaciones_anticipo')
    canje = models.ForeignKey(CanjeLetras, on_delete=models.PROTECT, null=True, blank=True,
                              related_name='aplicaciones')
    entrega = models.ForeignKey(EntregaRendir, on_delete=models.PROTECT, null=True, blank=True,
                                related_name='aplicaciones')
    glosa = models.CharField(max_length=250, blank=True)
    estado = models.CharField(max_length=8, choices=Movimiento.ESTADOS, default='VIGENTE', editable=False)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    objects = VigentesManager()
    todos = models.Manager()

    class Meta:
        ordering = ['-fecha', '-id']
        base_manager_name = 'todos'
        verbose_name = 'aplicación'
        verbose_name_plural = 'aplicaciones'

    def __str__(self):
        return f'{self.get_origen_display()} {self.documento}'

    @property
    def documento(self):
        return self.venta or self.compra

    def save(self, *args, **kwargs):
        self.monto_doc_pen = r2(self.monto_doc * self.documento.tc_efectivo)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------- cheques
class Cheque(models.Model):
    TIPOS = [('EMITIDO', 'Emitido por la empresa'), ('RECIBIDO', 'Recibido de clientes')]
    ESTADOS = [('CARTERA', 'Pendiente (girado / en cartera)'), ('COBRADO', 'Cobrado / depositado'),
               ('RECHAZADO', 'Rechazado'), ('ANULADO', 'Anulado')]
    tipo = models.CharField(max_length=8, choices=TIPOS)
    numero = models.CharField('N° de cheque', max_length=30)
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, null=True, blank=True, related_name='cheques',
                               help_text='Emitido: cuenta girada. Recibido: cuenta donde se deposita')
    banco_emisor = models.CharField('Banco emisor', max_length=60, blank=True)
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT, null=True, blank=True,
                                verbose_name='Beneficiario / girador')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    fecha_emision = models.DateField('Emisión', default=timezone.localdate)
    fecha_pago = models.DateField('Fecha de pago (diferido)', null=True, blank=True,
                                  help_text='Cheque diferido: no se puede cobrar antes de esta fecha')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='CARTERA')
    fecha_estado = models.DateField(null=True, blank=True)
    glosa = models.CharField(max_length=250, blank=True)
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_emision', '-id']

    def __str__(self):
        return f'Cheque {self.numero}'

    @property
    def simbolo(self):
        return 'US$' if self.moneda == 'USD' else 'S/'


# ---------------------------------------------------------------- pagos masivos
class PagoMasivo(models.Model):
    ESTADOS = [('BORRADOR', 'Archivo generado (por pagar)'), ('PAGADO', 'Pagos registrados'),
               ('ANULADO', 'Anulado')]
    numero = models.CharField(max_length=20, editable=False)
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name='pagos_masivos')
    fecha = models.DateField(default=timezone.localdate)
    glosa = models.CharField(max_length=250, blank=True)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='BORRADOR')
    creado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'pago masivo'
        verbose_name_plural = 'pagos masivos'

    def __str__(self):
        return f'Pago masivo {self.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = _siguiente('PM', 'PM')
        super().save(*args, **kwargs)

    @property
    def total(self):
        return self.lineas.aggregate(s=Sum('monto'))['s'] or D0


class LineaPagoMasivo(models.Model):
    pago = models.ForeignKey(PagoMasivo, on_delete=models.CASCADE, related_name='lineas')
    compra = models.ForeignKey('compras.Compra', on_delete=models.PROTECT, related_name='+')
    monto = models.DecimalField('Monto a pagar (moneda del documento)', max_digits=14, decimal_places=2)
    movimiento = models.ForeignKey(Movimiento, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['id']
