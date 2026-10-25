from django.db import models

from core.models import ComprobanteBase, DocumentoBase, ElectronicoMixin, ItemBase


class ListaPrecios(models.Model):
    """Precios por cliente, canal o volumen. El cliente puede tener una lista; la venta la toma de él (o se elige)."""
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100, help_text='Ej. Mayoristas, Distribuidores, Tienda online')
    vigente_desde = models.DateField('Vigente desde', null=True, blank=True)
    vigente_hasta = models.DateField('Vigente hasta', null=True, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'lista de precios'
        verbose_name_plural = 'listas de precios'

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    def vigente(self, fecha):
        return self.activa and (not self.vigente_desde or self.vigente_desde <= fecha) and (
            not self.vigente_hasta or fecha <= self.vigente_hasta)


class PrecioLista(models.Model):
    """Precio del producto en la lista, por tramo de cantidad (desde `cantidad_minima`)."""
    lista = models.ForeignKey(ListaPrecios, on_delete=models.CASCADE, related_name='precios')
    producto = models.ForeignKey('core.Producto', on_delete=models.CASCADE, related_name='+')
    cantidad_minima = models.DecimalField('Desde cantidad', max_digits=14, decimal_places=2, default=1)
    precio = models.DecimalField('Precio sin IGV', max_digits=14, decimal_places=4, null=True, blank=True,
                                 help_text='Vacío = precio de venta del producto con el descuento')
    descuento_pct = models.DecimalField('Descuento %', max_digits=5, decimal_places=2, default=0)

    class Meta:
        ordering = ['producto__nombre', 'cantidad_minima']
        unique_together = [('lista', 'producto', 'cantidad_minima')]


class Cotizacion(DocumentoBase):
    TIPOS = [('COT', 'Cotización / Proforma'), ('PED', 'Orden de pedido')]
    tipo = models.CharField(max_length=3, choices=TIPOS, default='COT')
    validez_dias = models.PositiveIntegerField('Validez (días)', default=15)
    fecha_entrega = models.DateField('Fecha de entrega', null=True, blank=True,
                                     help_text='Pedido: el flujo de caja espera el cobro en la entrega + la condición '
                                               'de pago')
    vendedor = models.CharField(max_length=80, blank=True)
    # aprobación por niveles (motor único, documento PEDIDO): bajo la tarifa, exceso de crédito, etc.
    niveles = models.JSONField(default=list, blank=True, editable=False)
    nivel_aprobado = models.PositiveSmallIntegerField(default=0, editable=False)
    motivo_aprobacion = models.CharField('Por qué requiere aprobación', max_length=300, blank=True, editable=False)
    # CRM-03: la cotización nace de una oportunidad (o la crea, CRM-14) y vuelve a ella
    oportunidad = models.ForeignKey('ventas.Oportunidad', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='cotizaciones', verbose_name='Oportunidad (CRM)')
    # v1.83: pedido tomado por el vendedor en TomaPedido (sus pedidos del celular)
    tomado_por = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, editable=False,
                                   related_name='+', verbose_name='Tomado por (TomaPedido)')

    class Meta(DocumentoBase.Meta):
        verbose_name = 'cotización / pedido'
        verbose_name_plural = 'cotizaciones / pedidos'


class CotizacionItem(ItemBase):
    documento = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name='items')


class Venta(ComprobanteBase, ElectronicoMixin):
    MOTIVOS_NC = [
        ('', '---'),
        ('01', '01 NC: Anulación de la operación / ND: Intereses por mora'),
        ('02', '02 NC: Anulación por error en el RUC / ND: Aumento de valor'),
        ('03', '03 NC: Corrección por error en la descripción / ND: Penalidades'),
        ('04', '04 Descuento global'),
        ('06', '06 Devolución total'),
        ('07', '07 Devolución por ítem'),
        ('09', '09 Disminución en el valor'),
        ('13', '13 Ajuste de cuotas / fechas'),
    ]
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='ventas', verbose_name='Cotización / pedido')
    doc_referencia = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='notas', verbose_name='Doc. que modifica (NC/ND)')
    motivo_nota = models.CharField('Motivo NC/ND', max_length=2, choices=MOTIVOS_NC, blank=True)
    vendedor = models.CharField(max_length=80, blank=True)
    detraccion_codigo = models.CharField('Bien/servicio con detracción', max_length=3, blank=True, default='35',
                                         help_text='Catálogo 54 SUNAT; solo si la venta tiene detracción')
    # el cliente pagó el total: la empresa (proveedor) depositó la detracción en su cuenta del BN (transferencia)
    detraccion_deposito = models.ForeignKey('finanzas.Movimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='+', editable=False,
                                            verbose_name='Depósito de la detracción hecho por la empresa')
    descontar_stock = models.BooleanField('Mover almacén', default=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo',
                                     help_text='Canal o sucursal de la venta; la línea de negocio sale del producto')
    lista_precios = models.ForeignKey(ListaPrecios, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+', verbose_name='Lista de precios',
                                      help_text='Vacío = la lista del cliente')
    cuotas = models.PositiveSmallIntegerField('N° de cuotas', default=1,
                                              help_text='Venta al crédito: cuotas iguales; la primera vence en la '
                                                        'fecha de vencimiento')
    dias_entre_cuotas = models.PositiveSmallIntegerField('Días entre cuotas', default=30)

    class Meta(ComprobanteBase.Meta):
        verbose_name = 'venta'
        unique_together = [('tipo_comprobante', 'serie', 'numero')]

    def _signo_stock(self):
        return 1 if self.es_nota_credito else -1

    @property
    def cronograma_cuotas(self):
        """[(n, fecha, importe)] del crédito (lo que se informa a SUNAT: neto de detracción y retención)."""
        from datetime import timedelta
        from decimal import Decimal
        if self.forma_pago != 'CREDITO' or not self.fecha_vencimiento:
            return []
        pendiente = self.total - self.detraccion_monto - self.retencion_monto
        n = max(self.cuotas or 1, 1)
        base = (pendiente / n).quantize(Decimal('0.01'))
        salida = []
        for i in range(1, n + 1):
            importe = pendiente - base * (n - 1) if i == n else base
            salida.append((i, self.fecha_vencimiento + timedelta(days=self.dias_entre_cuotas * (i - 1)), importe))
        return salida


class VentaItem(ItemBase):
    documento = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='items')
    # deducción de un anticipo (regularización): línea negativa que referencia la factura del anticipo
    anticipo_de = models.ForeignKey(Venta, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='regularizaciones', verbose_name='Deduce el anticipo')


# ---------------------------------------------------------------- CRM
class Oportunidad(models.Model):
    """Negocio en seguimiento (embudo comercial): de prospecto a ganado o perdido."""
    ETAPAS = [('PROSPECTO', 'Prospecto'), ('CALIFICADO', 'Calificado'), ('PROPUESTA', 'Propuesta / cotización'),
              ('NEGOCIACION', 'Negociación'), ('GANADA', 'Ganada'), ('PERDIDA', 'Perdida')]
    ABIERTAS = ('PROSPECTO', 'CALIFICADO', 'PROPUESTA', 'NEGOCIACION')
    PROBABILIDAD = {'PROSPECTO': 10, 'CALIFICADO': 25, 'PROPUESTA': 50, 'NEGOCIACION': 75, 'GANADA': 100,
                    'PERDIDA': 0}
    ORIGENES = [('', '---'), ('REFERIDO', 'Referido'), ('WEB', 'Web / redes'), ('LLAMADA', 'Llamada'),
                ('VISITA', 'Visita'), ('FERIA', 'Feria / evento'), ('CARTERA', 'Cliente de la cartera'),
                ('CORREO', 'Correo'), ('WHATSAPP', 'WhatsApp'), ('COTIZACION', 'Cotización sin oportunidad'),
                ('RENOVACION', 'Renovación de contrato'), ('OTRO', 'Otro')]
    # CRM-07: motivos de pérdida en catálogo (con el competidor)
    MOTIVOS_PERDIDA = [('', '---'), ('PRECIO', 'Precio'), ('PLAZO', 'Plazo de entrega / ejecución'),
                       ('COMPETIDOR', 'Eligió a un competidor'), ('SIN_PRESUPUESTO', 'Sin presupuesto'),
                       ('REQUISITOS', 'No cumplimos requisitos / experiencia'), ('DESISTIO', 'El cliente desistió'),
                       ('SIN_RESPUESTA', 'Sin respuesta del cliente'), ('OTRO', 'Otro')]
    nombre = models.CharField('Oportunidad', max_length=150)
    tercero = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                verbose_name='Cliente')
    prospecto = models.CharField('Prospecto (aún no es cliente)', max_length=150, blank=True)
    contacto = models.CharField('Persona de contacto', max_length=120, blank=True)
    telefono = models.CharField('Teléfono', max_length=40, blank=True)
    email = models.EmailField(blank=True)
    etapa = models.CharField(max_length=12, choices=ETAPAS, default='PROSPECTO')
    monto = models.DecimalField('Monto estimado S/', max_digits=14, decimal_places=2, default=0)
    probabilidad = models.PositiveSmallIntegerField('Probabilidad %', null=True, blank=True,
                                                    help_text='Vacío = la de la etapa')
    fecha_cierre = models.DateField('Cierre estimado', null=True, blank=True)
    origen = models.CharField(max_length=10, choices=ORIGENES, blank=True)
    responsable = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    verbose_name='Vendedor responsable')
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    motivo_perdida_tipo = models.CharField('Motivo de pérdida', max_length=15, choices=MOTIVOS_PERDIDA, blank=True)
    motivo_perdida = models.CharField('Detalle del motivo de pérdida', max_length=200, blank=True)
    competidor = models.CharField('Competidor que ganó', max_length=120, blank=True)
    campana = models.ForeignKey('ventas.Campana', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='oportunidades', verbose_name='Campaña')
    unidad = models.ForeignKey('obras.Unidad', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                               verbose_name='Unidad inmobiliaria de interés')
    fecha_inicio = models.DateField('Inicio probable (para operaciones)', null=True, blank=True,
                                   help_text='Cuándo empezaría la obra, la producción o el servicio')
    cerrada_en = models.DateField('Cerrada el', null=True, blank=True, editable=False)
    notas = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-actualizado']
        verbose_name = 'oportunidad'
        verbose_name_plural = 'oportunidades'

    def __str__(self):
        return self.nombre

    @property
    def cliente(self):
        return self.tercero.nombre if self.tercero_id else self.prospecto

    @property
    def prob(self):
        if self.probabilidad is not None:
            return self.probabilidad
        from .crm_servicios import probabilidad_de
        return probabilidad_de(self.etapa)

    @property
    def abierta(self):
        return self.etapa in self.ABIERTAS

    @property
    def ponderado(self):
        return self.monto * self.prob / 100


class ActividadCRM(models.Model):
    TIPOS = [('LLAMADA', 'Llamada'), ('REUNION', 'Reunión'), ('VISITA', 'Visita'), ('CORREO', 'Correo / WhatsApp'),
             ('TAREA', 'Tarea'), ('NOTA', 'Nota')]
    # una visita de cartera puede no tener oportunidad (CRM-12): va al cliente
    oportunidad = models.ForeignKey(Oportunidad, on_delete=models.CASCADE, related_name='actividades', null=True,
                                    blank=True)
    tercero = models.ForeignKey('core.Tercero', on_delete=models.CASCADE, null=True, blank=True,
                                related_name='actividades_crm', verbose_name='Cliente')
    tipo = models.CharField(max_length=8, choices=TIPOS, default='LLAMADA')
    fecha = models.DateField()
    hora = models.TimeField(null=True, blank=True)
    descripcion = models.CharField('Descripción', max_length=250)
    responsable = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    verbose_name='Responsable', help_text='Vacío = quien la registra')
    recordatorio = models.BooleanField('Recordarme', default=True,
                                       help_text='Aparece en Inicio (campana) y se avisa por correo el día')
    hecha = models.BooleanField('Realizada', default=False)
    resultado = models.CharField('Resultado', max_length=250, blank=True)
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, editable=False)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, editable=False)
    avisada = models.BooleanField(default=False, editable=False)
    usuario = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['hecha', 'fecha', 'hora', 'id']

    def __str__(self):
        return f'{self.get_tipo_display()} {self.fecha:%d/%m/%Y}: {self.descripcion}'

    @property
    def a_cargo(self):
        return self.responsable or self.usuario

    @property
    def cliente(self):
        if self.tercero_id:
            return self.tercero.nombre
        return self.oportunidad.cliente if self.oportunidad_id else ''


from .models_crm import (  # noqa: E402,F401
    CambioEtapa, Campana, CarteraCliente, CasoPostventa, ConfigCRM, ConsentimientoDatos, ContactoCliente,
    ContratoServicio, EncuestaSatisfaccion, EquipoVenta, MetaVenta, Reasignacion)
