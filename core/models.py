from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models, transaction
from django.db.models import OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

D0 = Decimal('0')
D2 = Decimal('0.01')


def r2(valor):
    return Decimal(valor or 0).quantize(D2, ROUND_HALF_UP)


# ---------------------------------------------------------------- catálogos SUNAT
TIPO_COMPROBANTE = [
    ('01', 'Factura'),
    ('03', 'Boleta de Venta'),
    ('07', 'Nota de Crédito'),
    ('08', 'Nota de Débito'),
    ('02', 'Recibo por Honorarios'),
    ('12', 'Ticket'),
    ('14', 'Recibo Servicios Públicos'),
    ('00', 'Otros'),
]
MONEDAS = [('PEN', 'Soles (S/)'), ('USD', 'Dólares (US$)')]
TIPO_OPERACION = [
    ('GRAVADA', 'Gravada (IGV)'),
    ('EXONERADA', 'Exonerada'),
    ('INAFECTA', 'Inafecta'),
    ('EXPORTACION', 'Exportación'),
    ('GRATUITA', 'Gratuita'),
]
# Afectación del IGV de cada línea (vacío = la del comprobante); Tabla 7 SUNAT: 10 gravado, 20 exonerado, 30 inafecto
AFECTACION_LINEA = [('', 'Según el comprobante'), ('GRAVADA', 'Gravada'), ('EXONERADA', 'Exonerada'),
                    ('INAFECTA', 'Inafecta')]
FORMA_PAGO = [('CONTADO', 'Contado'), ('CREDITO', 'Crédito')]
ESTADO_COMPROBANTE = [('REGISTRADO', 'Registrado'), ('ANULADO', 'Anulado')]
ESTADO_DOCUMENTO = [
    ('PENDIENTE', 'Pendiente'),
    ('APROBADO', 'Aprobado'),
    ('ATENDIDO', 'Atendido / Facturado'),
    ('ANULADO', 'Anulado'),
]


class Empresa(models.Model):
    ruc = models.CharField('RUC', max_length=11)
    razon_social = models.CharField('Razón social', max_length=200)
    nombre_comercial = models.CharField(max_length=200, blank=True)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    telefono = models.CharField('Teléfono', max_length=50, blank=True)
    email = models.EmailField(blank=True)
    igv_tasa = models.DecimalField('Tasa IGV %', max_digits=5, decimal_places=2, default=Decimal('18.00'))
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Ubigeo del domicilio fiscal (6 dígitos)')
    registro_mtc = models.CharField('Registro MTC', max_length=20, blank=True,
                                    help_text='Solo si emite guías como transportista')
    permitir_stock_negativo = models.BooleanField(
        'Permitir vender sin stock', default=False,
        help_text='Si está desmarcado, no se puede vender ni despachar más de lo que hay en el almacén')
    FUENTES_TC = [('SUNAT', 'SUNAT (gratuito, apis.net.pe)'), ('SBS', 'SBS - promedio ponderado (Decolecta, requiere token)')]
    fuente_tipo_cambio = models.CharField(
        'Fuente del tipo de cambio', max_length=5, choices=FUENTES_TC, default='SUNAT',
        help_text='SBS: tipo de cambio promedio ponderado publicado por la SBS. Si no responde se usa SUNAT.')
    token_tipo_cambio = models.CharField(
        'Token Decolecta (tipo de cambio SBS)', max_length=200, blank=True,
        help_text='Se obtiene al registrarse en decolecta.com (API de tipo de cambio SBS)')
    # ---- control de crédito de clientes
    bloquear_deuda_vencida = models.BooleanField(
        'Bloquear crédito con deuda vencida', default=True,
        help_text='No se emiten ventas al crédito a clientes con comprobantes vencidos e impagos')
    dias_gracia = models.PositiveIntegerField('Días de gracia', default=0,
                                              help_text='Días después del vencimiento antes de bloquear el crédito')
    # ---- compras y portal de proveedores
    exigir_orden_compra = models.BooleanField(
        'Exigir orden de compra', default=True,
        help_text='Las compras de mercadería (facturas y boletas) solo se registran desde una orden de compra')
    tolerancia_cantidad = models.DecimalField(
        'Tolerancia de cantidad (±)', max_digits=10, decimal_places=2, default=Decimal('1'),
        help_text='Diferencia permitida entre la cantidad facturada por el proveedor y la recibida / pedida')
    tolerancia_precio = models.DecimalField(
        'Tolerancia de precio unitario (±)', max_digits=10, decimal_places=4, default=Decimal('1'),
        help_text='Diferencia permitida entre el precio facturado y el de la orden de compra')
    tolerancia_total = models.DecimalField(
        'Tolerancia del monto total (±)', max_digits=10, decimal_places=2, default=Decimal('1'),
        help_text='Diferencia permitida entre el total de la factura del proveedor y el calculado con sus líneas')
    sunat_client_id = models.CharField(
        'SUNAT API: client_id', max_length=100, blank=True,
        help_text='Credenciales de "Consulta de validez de comprobantes" (SUNAT Operaciones en Línea > '
                  'Empresas > Comprobantes de pago > Credenciales de API SUNAT)')
    sunat_client_secret = models.CharField('SUNAT API: client_secret', max_length=200, blank=True)

    class Meta:
        verbose_name = 'empresa'

    def __str__(self):
        return self.razon_social

    @classmethod
    def actual(cls):
        empresa = cls.objects.first()
        if empresa is None:
            empresa = cls.objects.create(ruc='20000000001', razon_social='MI EMPRESA S.A.C.')
        return empresa

    def como_tercero(self):
        """La propia empresa como destinatario (guías de traslado entre establecimientos, motivo 04)."""
        tercero, creado = Tercero.objects.get_or_create(
            tipo_doc='6', numero_doc=self.ruc,
            defaults={'tipo': 'AMBOS', 'nombre': self.razon_social, 'direccion': self.direccion,
                      'ubigeo': self.ubigeo})
        if not creado and (tercero.nombre != self.razon_social or not tercero.direccion):
            tercero.nombre, tercero.direccion = self.razon_social, tercero.direccion or self.direccion
            tercero.save(update_fields=['nombre', 'direccion'])
        return tercero


class Tercero(models.Model):
    TIPOS = [('CLIENTE', 'Cliente'), ('PROVEEDOR', 'Proveedor'), ('AMBOS', 'Cliente y proveedor')]
    TIPOS_DOC = [('6', 'RUC'), ('1', 'DNI'), ('4', 'Carné de extranjería'), ('7', 'Pasaporte'), ('0', 'Otros')]

    tipo = models.CharField(max_length=10, choices=TIPOS, default='CLIENTE')
    tipo_doc = models.CharField('Tipo doc.', max_length=1, choices=TIPOS_DOC, default='6')
    numero_doc = models.CharField('N° documento', max_length=15)
    nombre = models.CharField('Nombre / Razón social', max_length=200)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    zona = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField('Teléfono', max_length=50, blank=True)
    dias_credito = models.PositiveIntegerField('Días de crédito', default=0)
    limite_credito = models.DecimalField('Límite de crédito S/', max_digits=14, decimal_places=2, default=D0,
                                         help_text='Deuda máxima permitida en ventas al crédito. 0 = sin límite')
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Ubigeo de la dirección (para guías)')
    registro_mtc = models.CharField('Registro MTC', max_length=20, blank=True,
                                    help_text='Solo empresas de transporte (guía transportista)')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'cliente / proveedor'
        verbose_name_plural = 'clientes / proveedores'

    def __str__(self):
        return f'{self.numero_doc} - {self.nombre}'


# Tipo de producto: define el prefijo del código automático, si es inventariable y su juego de cuentas
CLASES_PRODUCTO = [
    ('MERCADERIA', 'Mercadería'),
    ('MATERIA_PRIMA', 'Materia prima'),
    ('SEMIELABORADO', 'Semi elaborado'),
    ('PRODUCTO_TERMINADO', 'Producto terminado'),
    ('SUMINISTRO', 'Suministros y materiales'),
    ('ACTIVO', 'Activo fijo (solo compra)'),
    ('SERVICIO', 'Servicio'),
]
PREFIJOS_PRODUCTO = {'MERCADERIA': 'ME', 'MATERIA_PRIMA': 'MP', 'SEMIELABORADO': 'SE', 'PRODUCTO_TERMINADO': 'PT',
                     'SUMINISTRO': 'SU', 'ACTIVO': 'AF', 'SERVICIO': 'SV'}
# Juego de cuentas por defecto (PCGE): existencias, compra, venta, costo de ventas / consumo
CUENTAS_PRODUCTO = {
    'MERCADERIA': ('20111', '6011', '70111', '69111'),
    'MATERIA_PRIMA': ('2411', '6021', '70111', '69111'),
    'SEMIELABORADO': ('2311', '6021', '7021', '6921'),
    'PRODUCTO_TERMINADO': ('2111', '6011', '7021', '6921'),
    'SUMINISTRO': ('2521', '6032', '7599', '6561'),
    'ACTIVO': ('', '3369', '7599', ''),
    'SERVICIO': ('', '6399', '7041', ''),
}
CAMPOS_CUENTA = ('cuenta_existencias', 'cuenta_compra', 'cuenta_venta', 'cuenta_costo')


def tipo_de_clase(clase):
    return {'SERVICIO': 'SERVICIO', 'ACTIVO': 'ACTIVO'}.get(clase, 'BIEN')


def asignar_cuentas(producto, CuentaContable):
    """Completa las cuentas vacías del producto con el juego por defecto de su tipo (acepta modelo histórico)."""
    codigos = CUENTAS_PRODUCTO.get(producto.clase, CUENTAS_PRODUCTO['MERCADERIA'])
    for campo, codigo in zip(CAMPOS_CUENTA, codigos):
        if codigo and not getattr(producto, f'{campo}_id'):
            setattr(producto, campo, CuentaContable.objects.filter(codigo=codigo).first())


class Producto(models.Model):
    TIPOS = [('BIEN', 'Bien (inventariable)'), ('SERVICIO', 'Servicio'), ('ACTIVO', 'Activo fijo (no inventariable)')]
    CLASES = CLASES_PRODUCTO
    UNIDADES = [('NIU', 'Unidad'), ('KGM', 'Kilogramo'), ('LTR', 'Litro'), ('MTR', 'Metro'),
                ('BX', 'Caja'), ('PK', 'Paquete'), ('GLL', 'Galón'), ('ZZ', 'Servicio')]

    # ---- general
    codigo = models.CharField('Código', max_length=30, unique=True, blank=True,
                              help_text='Vacío = automático según el tipo (ej. MP000001)')
    nombre = models.CharField(max_length=200)
    clase = models.CharField('Tipo de producto', max_length=20, choices=CLASES, default='MERCADERIA')
    tipo = models.CharField(max_length=10, choices=TIPOS, default='BIEN', editable=False)
    unidad = models.CharField('Unidad de medida', max_length=5, choices=UNIDADES, default='NIU')
    marca = models.CharField(max_length=80, blank=True)
    codigo_barras = models.CharField('Código de barras', max_length=40, blank=True)
    peso = models.DecimalField('Peso por unidad (kg)', max_digits=12, decimal_places=3, default=D0,
                               help_text='Calcula el peso bruto de las guías de remisión')
    afectacion_igv = models.CharField('Afectación del IGV', max_length=10, choices=AFECTACION_LINEA, blank=True,
                                      default='', help_text='Ej. libros (exonerados) o productos agrarios '
                                                            '(inafectos). Vacío = según el comprobante')
    descripcion = models.TextField('Descripción', blank=True)
    activo = models.BooleanField(default=True)
    # ---- compras
    puede_comprarse = models.BooleanField('Se puede comprar', default=True)
    precio_compra = models.DecimalField('Precio de compra (sin IGV)', max_digits=12, decimal_places=4, default=D0,
                                        help_text='Precio referencial para órdenes de compra')
    proveedor = models.ForeignKey('Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                  verbose_name='Proveedor habitual', limit_choices_to={'tipo__in': ['PROVEEDOR', 'AMBOS']})
    unidad_compra = models.CharField('Unidad de compra', max_length=5, choices=UNIDADES, blank=True,
                                     help_text='Vacío = la misma unidad de medida')
    # ---- ventas
    puede_venderse = models.BooleanField('Se puede vender', default=True)
    precio_venta = models.DecimalField('Precio venta (sin IGV)', max_digits=12, decimal_places=2, default=D0)
    # ---- contabilidad (juego de cuentas)
    cuenta_existencias = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                           blank=True, related_name='+', limit_choices_to={'imputable': True},
                                           verbose_name='Cuenta de existencias (inventario)')
    cuenta_compra = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                      blank=True, related_name='+', limit_choices_to={'imputable': True},
                                      verbose_name='Cuenta de compra / gasto')
    cuenta_venta = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                     blank=True, related_name='+', limit_choices_to={'imputable': True},
                                     verbose_name='Cuenta de ventas (ingreso)')
    cuenta_costo = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                     blank=True, related_name='+', limit_choices_to={'imputable': True},
                                     verbose_name='Cuenta de costo de ventas / consumo')
    # ---- planificación
    stock_minimo = models.DecimalField('Stock mínimo', max_digits=14, decimal_places=2, default=D0)
    stock_maximo = models.DecimalField('Stock máximo', max_digits=14, decimal_places=2, default=D0)
    punto_reorden = models.DecimalField('Punto de reorden', max_digits=14, decimal_places=2, default=D0,
                                        help_text='Al llegar a esta cantidad se debe pedir')
    lote_compra = models.DecimalField('Lote mínimo de compra', max_digits=14, decimal_places=2, default=D0)
    tiempo_entrega = models.PositiveIntegerField('Tiempo de entrega (días)', default=0)
    almacen_defecto = models.ForeignKey('Almacen', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+', verbose_name='Almacén por defecto')
    # ---- calculados
    costo_promedio = models.DecimalField('Costo promedio', max_digits=12, decimal_places=4, default=D0)
    stock = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'

    def save(self, *args, **kwargs):
        self.tipo = tipo_de_clase(self.clase)
        if self.clase == 'ACTIVO':
            self.puede_venderse = False
        if not self.codigo:
            self.codigo = self.siguiente_codigo(self.clase)
        if kwargs.get('update_fields') is None:
            asignar_cuentas(self, apps.get_model('contabilidad', 'CuentaContable'))
        super().save(*args, **kwargs)

    @classmethod
    def siguiente_codigo(cls, clase):
        """Prefijo del tipo + correlativo de 6 dígitos (MP000001), saltando los códigos ya usados."""
        prefijo = PREFIJOS_PRODUCTO.get(clase, 'PR')
        while True:
            _, numero = Serie.siguiente('PRD', prefijo)
            codigo = f'{prefijo}{int(numero):06d}'
            if not cls.objects.filter(codigo=codigo).exists():
                return codigo

    @property
    def es_inventariable(self):
        return self.tipo == 'BIEN'

    @property
    def precio_referencia(self):
        """Activos (solo compra): su precio es el de compra; los demás, el de venta."""
        return self.precio_compra if self.clase == 'ACTIVO' else self.precio_venta

    @property
    def requiere_reposicion(self):
        limite = self.punto_reorden or self.stock_minimo
        return self.es_inventariable and limite > 0 and self.stock <= limite

    @property
    def valorizado(self):
        return r2(self.stock * self.costo_promedio)

    @property
    def bajo_minimo(self):
        return self.es_inventariable and self.stock <= self.stock_minimo

    def stock_en(self, almacen):
        return StockAlmacen.objects.filter(producto=self, almacen=almacen).values_list(
            'cantidad', flat=True).first() or D0

    def mover_stock(self, cantidad, referencia, costo=None, fecha=None, almacen=None, origen='', concepto='',
                    codigo_sunat=''):
        """cantidad > 0 entrada, < 0 salida. Actualiza costo promedio en entradas y el stock del almacén."""
        cantidad = Decimal(cantidad)
        if not self.es_inventariable or cantidad == 0:
            return
        from inventario.cierre import validar_fecha
        validar_fecha(fecha or timezone.localdate())  # kardex cerrado: no se mueve el almacén en esa fecha
        almacen = almacen or Almacen.principal()
        with transaction.atomic():
            # se relee y bloquea el producto: dos líneas del mismo producto no deben pisarse
            actual = Producto.objects.select_for_update().get(pk=self.pk)
            if cantidad > 0 and costo is not None:
                nuevo_stock = actual.stock + cantidad
                if nuevo_stock > 0:
                    total = actual.stock * actual.costo_promedio + cantidad * Decimal(costo)
                    actual.costo_promedio = (total / nuevo_stock).quantize(Decimal('0.0001'))
            actual.stock += cantidad
            actual.save(update_fields=['stock', 'costo_promedio'])
            self.stock, self.costo_promedio = actual.stock, actual.costo_promedio
            sa, _ = StockAlmacen.objects.select_for_update().get_or_create(producto=self, almacen=almacen)
            sa.cantidad += cantidad
            sa.save(update_fields=['cantidad'])
            Kardex.objects.create(
                producto=self, almacen=almacen, fecha=fecha or timezone.localdate(),
                tipo='ENTRADA' if cantidad > 0 else 'SALIDA', cantidad=abs(cantidad),
                costo_unitario=costo if costo is not None else actual.costo_promedio,
                costo_promedio=actual.costo_promedio, saldo=actual.stock, referencia=referencia, origen=origen,
                concepto=concepto, codigo_sunat=codigo_sunat or _codigo_sunat(origen, concepto, cantidad),
            )


def _codigo_sunat(origen, concepto, cantidad):
    """Tabla 12 SUNAT para los movimientos que no lo indican."""
    entrada = cantidad > 0
    if origen == 'VENTA':
        return '05' if entrada else '01'
    if origen == 'COMPRA':
        return '02' if entrada else '06'
    if origen == 'AJUSTE':
        return '16' if concepto == 'INICIAL' else '28'
    if origen == 'GUIA':
        return '21' if entrada else '11'
    return '99'


class Almacen(models.Model):
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Código de 6 dígitos (INEI)')
    codigo_sunat = models.CharField('Cód. establecimiento SUNAT', max_length=4, default='0000')
    es_principal = models.BooleanField('Principal', default=False)
    USOS = [('', 'Almacén normal'), ('TRANSITO', 'Mercadería en tránsito'),
            ('DESTRUCCION', 'Cuarentena / por destruir')]
    uso = models.CharField('Uso', max_length=12, choices=USOS, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['-es_principal', 'nombre']
        verbose_name = 'almacén'
        verbose_name_plural = 'almacenes'

    def __str__(self):
        return self.nombre

    @classmethod
    def especial(cls, uso):
        """Almacén virtual de tránsito o de destrucción (se crea la primera vez que se necesita)."""
        nombres = {'TRANSITO': ('ALM-TR', 'Mercadería en tránsito'),
                   'DESTRUCCION': ('ALM-DS', 'Cuarentena / por destruir')}
        alm = cls.objects.filter(uso=uso).first()
        if alm is None:
            codigo, nombre = nombres[uso]
            alm = cls.objects.create(codigo=codigo, nombre=nombre, uso=uso)
        return alm

    @classmethod
    def principal(cls):
        alm = cls.objects.filter(es_principal=True, activo=True).first() or cls.objects.filter(activo=True).first()
        if alm is None:
            alm = cls.objects.create(codigo='ALM01', nombre='Almacén principal', es_principal=True)
        return alm


class StockAlmacen(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='stocks')
    almacen = models.ForeignKey(Almacen, on_delete=models.CASCADE, related_name='stocks')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        unique_together = [('producto', 'almacen')]


class TipoCambio(models.Model):
    fecha = models.DateField(unique=True)
    compra = models.DecimalField(max_digits=8, decimal_places=3)
    venta = models.DecimalField(max_digits=8, decimal_places=3)
    fuente = models.CharField(max_length=20, default='SUNAT')

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'tipo de cambio'
        verbose_name_plural = 'tipos de cambio'

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y} C {self.compra} V {self.venta}'


class FacturacionConfig(models.Model):
    """Conexión con el OSE/PSE para comprobantes y guías electrónicas."""
    PROVEEDORES = [('NINGUNO', 'Sin facturación electrónica'), ('NUBEFACT', 'Nubefact (OSE/PSE)')]
    proveedor = models.CharField(max_length=10, choices=PROVEEDORES, default='NINGUNO')
    ruta = models.URLField('Ruta / URL de la API', max_length=300, blank=True,
                           help_text='La entrega el proveedor (ej. https://api.nubefact.com/api/v1/xxxx)')
    token = models.CharField(max_length=200, blank=True)
    envio_automatico = models.BooleanField('Enviar al emitir', default=False,
                                           help_text='Envía facturas, boletas, notas y guías al guardarlas')

    class Meta:
        verbose_name = 'configuración de facturación electrónica'

    @classmethod
    def actual(cls):
        return cls.objects.first() or cls.objects.create()

    @property
    def activa(self):
        return self.proveedor != 'NINGUNO' and bool(self.ruta and self.token)


class ArchivoSustento(models.Model):
    """Documento de sustento (PDF, imagen, Excel). Se guarda en la base de datos: el disco de la nube se borra."""
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=100, blank=True)
    datos = models.BinaryField()
    tamano = models.PositiveIntegerField(default=0)
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'archivo de sustento'
        verbose_name_plural = 'archivos de sustento'

    def __str__(self):
        return self.nombre


class Sustento(models.Model):
    """Vínculo entre un documento del ERP (asiento, movimiento, operación, comprobante...) y su sustento.
    Solo se agregan: no se borran (trazabilidad)."""
    archivo = models.ForeignKey(ArchivoSustento, on_delete=models.PROTECT, related_name='vinculos')
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    objeto = GenericForeignKey('content_type', 'object_id')
    descripcion = models.CharField('Descripción', max_length=200, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado']
        indexes = [models.Index(fields=['content_type', 'object_id'])]


class Bitacora(models.Model):
    """Auditoría: quién creó, modificó, anuló o eliminó cada registro, cuándo y qué cambió."""
    ACCIONES = [('CREAR', 'Creó'), ('MODIFICAR', 'Modificó'), ('ELIMINAR', 'Eliminó'), ('ANULAR', 'Anuló'),
                ('EXTORNAR', 'Extornó'), ('ADJUNTAR', 'Adjuntó sustento'), ('ACCESO', 'Inició sesión'),
                ('ENVIAR', 'Envió al cliente')]
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    accion = models.CharField(max_length=10, choices=ACCIONES)
    modelo = models.CharField(max_length=60, db_index=True)
    modelo_nombre = models.CharField(max_length=80)
    objeto_id = models.CharField(max_length=40, db_index=True)
    objeto = models.CharField(max_length=200)
    cambios = models.JSONField(default=dict, blank=True)
    motivo = models.CharField(max_length=300, blank=True)
    ip = models.CharField(max_length=45, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'registro de auditoría'
        verbose_name_plural = 'bitácora de auditoría'


class IntentoAcceso(models.Model):
    """Inicios de sesión fallidos: tras 5 en 15 minutos el usuario queda bloqueado temporalmente."""
    usuario = models.CharField(max_length=150, db_index=True)
    ip = models.CharField(max_length=45, blank=True)
    creado = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'intento de acceso fallido'
        verbose_name_plural = 'intentos de acceso fallidos'


class PerfilUsuario(models.Model):
    """Permisos finos del usuario dentro de sus módulos: acciones (anular, aprobar...), almacenes y series.
    Sin perfil el usuario puede todo en sus módulos (comportamiento anterior)."""
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil')
    acciones = models.JSONField(default=list, blank=True)
    almacenes = models.ManyToManyField('Almacen', blank=True, related_name='+',
                                       help_text='Vacío = todos los almacenes')
    series = models.ManyToManyField('Serie', blank=True, related_name='+', help_text='Vacío = todas las series')
    limite_aprobacion = models.DecimalField('Aprueba órdenes de compra hasta S/', max_digits=14, decimal_places=2,
                                            default=D0, help_text='0 = sin límite')

    class Meta:
        verbose_name = 'perfil de usuario'
        verbose_name_plural = 'perfiles de usuario'

    def __str__(self):
        return f'Permisos de {self.usuario}'


class SegundoFactor(models.Model):
    """Doble factor (TOTP, app autenticadora): secreto, estado y códigos de respaldo (guardados con hash)."""
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='segundo_factor')
    secreto = models.CharField(max_length=64)
    activo = models.BooleanField(default=False)
    activado_en = models.DateTimeField(null=True, blank=True)
    ultimo_paso = models.BigIntegerField(default=0, help_text='Último código usado: no se acepta dos veces')
    codigos_respaldo = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = 'doble factor'
        verbose_name_plural = 'doble factor'

    def __str__(self):
        return f'Doble factor de {self.usuario}'


class CorreoConfig(models.Model):
    """Servidor de correo saliente (SMTP) para enviar órdenes de compra y conformidades a proveedores."""
    servidor = models.CharField('Servidor SMTP', max_length=120, blank=True, help_text='Ej. smtp.gmail.com')
    puerto = models.PositiveIntegerField(default=587)
    seguridad = models.CharField(max_length=4, choices=[('TLS', 'STARTTLS (587)'), ('SSL', 'SSL (465)'),
                                                        ('', 'Ninguna')], default='TLS', blank=True)
    usuario = models.CharField(max_length=120, blank=True)
    clave = models.CharField('Contraseña', max_length=200, blank=True,
                             help_text='En Gmail u Outlook use una "contraseña de aplicación"')
    remitente = models.EmailField('Correo remitente', blank=True, help_text='Vacío = el usuario')
    nombre_remitente = models.CharField('Nombre del remitente', max_length=100, blank=True,
                                        help_text='Vacío = razón social de la empresa')
    copia = models.EmailField('Enviar copia a', blank=True, help_text='Opcional: copia oculta de cada envío')

    class Meta:
        verbose_name = 'configuración de correo'

    @classmethod
    def actual(cls):
        return cls.objects.first() or cls.objects.create()

    @property
    def activa(self):
        return bool(self.servidor and (self.remitente or self.usuario))


class Kardex(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='kardex')
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, related_name='kardex')
    fecha = models.DateField()
    tipo = models.CharField(max_length=10)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    costo_promedio = models.DecimalField(max_digits=12, decimal_places=4, default=D0)
    origen = models.CharField(max_length=10, blank=True, help_text='VENTA, COMPRA, GUIA, AJUSTE')
    concepto = models.CharField(max_length=10, blank=True,
                                help_text='Ajustes: INICIAL/SOBRANTE/MERMA/CONSUMO. Operaciones: código del tipo')
    codigo_sunat = models.CharField('Tabla 12 SUNAT', max_length=2, blank=True)
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=4, default=D0)
    saldo = models.DecimalField(max_digits=14, decimal_places=2)
    referencia = models.CharField(max_length=120)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']


class Serie(models.Model):
    """Correlativos de comprobantes, cotizaciones, órdenes y vouchers."""
    TIPOS = TIPO_COMPROBANTE + [('OC', 'Orden de compra'), ('COT', 'Cotización / Proforma'),
                                ('PED', 'Orden de pedido'), ('VOU', 'Voucher caja/bancos'),
                                ('09', 'Guía de remisión remitente'), ('31', 'Guía de remisión transportista'),
                                ('PRD', 'Código de productos (serie = prefijo del tipo)')]
    tipo = models.CharField(max_length=3, choices=TIPOS)
    serie = models.CharField(max_length=4)
    correlativo = models.PositiveIntegerField('Último correlativo', default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = [('tipo', 'serie')]
        ordering = ['tipo', 'serie']

    def __str__(self):
        return f'{self.serie} ({self.get_tipo_display()})'

    @classmethod
    def siguiente(cls, tipo, serie=None):
        """Devuelve (serie, numero) incrementando el correlativo de forma atómica."""
        with transaction.atomic():
            qs = cls.objects.select_for_update().filter(tipo=tipo, activo=True)
            if serie:
                qs = qs.filter(serie=serie)
            obj = qs.first()
            if obj is None:
                obj = cls.objects.create(tipo=tipo, serie=serie or tipo[:4].upper())
                obj = cls.objects.select_for_update().get(pk=obj.pk)
            obj.correlativo += 1
            obj.save(update_fields=['correlativo'])
            return obj.serie, str(obj.correlativo).zfill(8)


# ---------------------------------------------------------------- bases abstractas
class ItemBase(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, null=True, blank=True)
    descripcion = models.CharField('Descripción', max_length=250)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('1'))
    precio_unitario = models.DecimalField('Valor unit. (sin IGV)', max_digits=14, decimal_places=4, default=D0)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    afectacion = models.CharField('IGV', max_length=10, choices=AFECTACION_LINEA, blank=True, default='')

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.subtotal = r2(self.cantidad * self.precio_unitario)
        super().save(*args, **kwargs)

    def afectacion_en(self, doc):
        """Afectación real de la línea: exportación y operaciones gratuitas mandan sobre la línea; los
        documentos sin IGV (recibo por honorarios, nota de venta) son inafectos."""
        if doc.tipo_operacion in ('EXPORTACION', 'GRATUITA'):
            return doc.tipo_operacion
        afectacion = self.afectacion or doc.tipo_operacion
        if afectacion == 'GRAVADA' and getattr(doc, 'tipo_comprobante', '01') in ('02', '00'):
            return 'INAFECTA'
        return afectacion


class TotalesMixin(models.Model):
    tipo_operacion = models.CharField('Operación', max_length=12, choices=TIPO_OPERACION, default='GRAVADA')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    tipo_cambio = models.DecimalField('T.C.', max_digits=8, decimal_places=3, default=Decimal('1.000'))
    base_imponible = models.DecimalField('Base imponible', max_digits=14, decimal_places=2, default=D0)
    no_gravado = models.DecimalField('Exon./Inaf./Export.', max_digits=14, decimal_places=2, default=D0)
    exonerado = models.DecimalField('Op. exonerada', max_digits=14, decimal_places=2, default=D0)
    inafecto = models.DecimalField('Op. inafecta', max_digits=14, decimal_places=2, default=D0)
    igv = models.DecimalField('IGV', max_digits=14, decimal_places=2, default=D0)
    icbper = models.DecimalField('ICBPER', max_digits=10, decimal_places=2, default=D0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        abstract = True

    @property
    def simbolo(self):
        return 'US$' if self.moneda == 'USD' else 'S/'

    @property
    def lleva_igv(self):
        return self.tipo_operacion == 'GRAVADA' and getattr(self, 'tipo_comprobante', '01') not in ('02', '00')

    @property
    def exportacion(self):
        return self.no_gravado - self.exonerado - self.inafecto

    @property
    def afectaciones_mixtas(self):
        return len({i.afectacion_en(self) for i in self.items.all()}) > 1

    def calcular_totales(self):
        """Totales por afectación de cada línea: gravada (base + IGV), exonerada, inafecta o exportación."""
        sumas = {'GRAVADA': D0, 'EXONERADA': D0, 'INAFECTA': D0, 'EXPORTACION': D0, 'GRATUITA': D0}
        for i in self.items.all():
            sumas[i.afectacion_en(self)] += i.subtotal
        self.base_imponible = r2(sumas['GRAVADA'])
        self.exonerado, self.inafecto = r2(sumas['EXONERADA']), r2(sumas['INAFECTA'])
        self.no_gravado = self.exonerado + self.inafecto + r2(sumas['EXPORTACION'])
        self.igv = r2(self.base_imponible * Empresa.actual().igv_tasa / 100)
        self.total = self.base_imponible + self.no_gravado + self.igv + self.icbper


class DocumentoBase(TotalesMixin):
    """Cotizaciones / órdenes de compra (documentos no tributarios)."""
    numero = models.CharField(max_length=20, editable=False)
    fecha = models.DateField(default=timezone.localdate)
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT)
    condicion_pago = models.CharField('Condición de pago', max_length=100, blank=True)
    glosa = models.TextField('Observaciones', blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_DOCUMENTO, default='PENDIENTE')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ['-fecha', '-id']

    def __str__(self):
        return self.numero


def _dec():
    return models.DecimalField(max_digits=16, decimal_places=2)


def _suma_sub(qs, campo):
    """Subconsulta SUM(campo) agrupada por la referencia externa (0 si no hay filas)."""
    sub = qs.annotate(suma_sub=Sum(campo)).values('suma_sub')[:1]
    return Coalesce(Subquery(sub, output_field=_dec()), Value(D0), output_field=_dec())


class ComprobanteQuerySet(models.QuerySet):
    def con_saldos(self):
        """Anota pagos y notas en una sola consulta (evita una consulta por documento al calcular saldos)."""
        movimiento = apps.get_model('finanzas', 'Movimiento')
        fk = self.model._meta.model_name
        movs = movimiento.objects.filter(**{fk: OuterRef('pk')}).values(fk)
        notas = self.model.objects.filter(doc_referencia=OuterRef('pk'), estado='REGISTRADO').values('doc_referencia')
        nc, nd = notas.filter(tipo_comprobante='07'), notas.filter(tipo_comprobante='08')
        return self.annotate(
            ann_pagado=_suma_sub(movs, 'monto_doc'), ann_pagado_pen=_suma_sub(movs, 'monto_doc_pen'),
            ann_nc=_suma_sub(nc, 'total'), ann_nd=_suma_sub(nd, 'total'),
            ann_nc_pen=_suma_sub(nc, 'total_pen'), ann_nd_pen=_suma_sub(nd, 'total_pen'))


class ComprobanteBase(TotalesMixin):
    """Comprobante de pago SUNAT (compras y ventas)."""
    tipo_comprobante = models.CharField('Tipo', max_length=2, choices=TIPO_COMPROBANTE, default='01')
    serie = models.CharField(max_length=4, blank=True)
    numero = models.CharField('Número', max_length=10, blank=True)
    fecha_emision = models.DateField('Fecha emisión', default=timezone.localdate)
    fecha_vencimiento = models.DateField('Fecha vencimiento', null=True, blank=True)
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6, blank=True)
    tercero = models.ForeignKey(Tercero, on_delete=models.PROTECT)
    forma_pago = models.CharField('Forma de pago', max_length=8, choices=FORMA_PAGO, default='CONTADO')
    detraccion_pct = models.DecimalField('% Detracción', max_digits=5, decimal_places=2, default=D0)
    detraccion_monto = models.DecimalField('Detracción', max_digits=14, decimal_places=2, default=D0)
    retencion_pct = models.DecimalField('% Retención', max_digits=5, decimal_places=2, default=D0)
    retencion_monto = models.DecimalField('Retención', max_digits=14, decimal_places=2, default=D0)
    percepcion_pct = models.DecimalField('% Percepción', max_digits=5, decimal_places=2, default=D0)
    percepcion_monto = models.DecimalField('Percepción', max_digits=14, decimal_places=2, default=D0)
    glosa = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_COMPROBANTE, default='REGISTRADO')
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Almacén')
    stock_aplicado = models.BooleanField('Movió almacén', default=False, editable=False)
    es_saldo_inicial = models.BooleanField(
        'Saldo inicial', default=False, editable=False,
        help_text='Documento pendiente de antes de usar el sistema: se cobra/paga, pero no va a los registros '
                  'de ventas/compras ni a SUNAT y se contabiliza contra la apertura (5911)')
    # Importes en soles calculados una sola vez (registro, cuentas por cobrar/pagar y contabilidad usan los mismos)
    total_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    base_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    nograv_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    igv_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    icbper_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    ret_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    perc_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    detr_pen = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    motivo_anulacion = models.CharField('Motivo de anulación', max_length=250, blank=True)
    anulado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    anulado_en = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    objects = ComprobanteQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ['-fecha_emision', '-id']

    def __str__(self):
        return f'{self.get_tipo_comprobante_display()} {self.numero_completo}'

    @property
    def tc_efectivo(self):
        return self.tipo_cambio if self.moneda == 'USD' else Decimal('1')

    def calcular_pen(self):
        tc = self.tc_efectivo
        self.total_pen, self.igv_pen = r2(self.total * tc), r2(self.igv * tc)
        self.icbper_pen, self.nograv_pen = r2(self.icbper * tc), r2(self.no_gravado * tc)
        self.base_pen = self.total_pen - self.igv_pen - self.icbper_pen - self.nograv_pen
        self.ret_pen, self.perc_pen = r2(self.retencion_monto * tc), r2(self.percepcion_monto * tc)
        self.detr_pen = r2(self.detraccion_monto * tc)

    def anular(self, usuario=None, motivo=''):
        self.revertir_stock()
        self.estado = 'ANULADO'
        self.motivo_anulacion = motivo[:250]
        self.anulado_por = usuario if usuario and usuario.is_authenticated else None
        self.anulado_en = timezone.now()
        self.save()

    @property
    def numero_completo(self):
        return f'{self.serie}-{self.numero}' if self.serie else self.numero

    @property
    def es_nota_credito(self):
        return self.tipo_comprobante == '07'

    @property
    def signo(self):
        return -1 if self.es_nota_credito else 1

    def calcular_totales(self):
        super().calcular_totales()
        self.detraccion_monto = r2(self.total * self.detraccion_pct / 100)
        self.retencion_monto = r2(self.total * self.retencion_pct / 100)
        self.percepcion_monto = r2(self.total * self.percepcion_pct / 100)

    def save(self, *args, **kwargs):
        if not self.periodo and self.fecha_emision:
            self.periodo = self.fecha_emision.strftime('%Y%m')
        if not self.fecha_vencimiento:
            self.fecha_vencimiento = self.fecha_emision
        self.calcular_pen()
        if kwargs.get('update_fields') is not None and 'total' in kwargs['update_fields']:
            kwargs['update_fields'] = list(kwargs['update_fields']) + [
                'total_pen', 'base_pen', 'nograv_pen', 'igv_pen', 'icbper_pen', 'ret_pen', 'perc_pen', 'detr_pen']
        super().save(*args, **kwargs)

    # ---- saldos (usan las anotaciones de .con_saldos() si existen; si no, consultan)
    def _anotado(self, nombre, consulta):
        if nombre in self.__dict__:
            return self.__dict__[nombre] or D0
        return consulta() or D0

    def _notas(self, tipo, campo):
        return self.notas.filter(estado='REGISTRADO', tipo_comprobante=tipo).aggregate(s=Sum(campo))['s']

    @property
    def total_documento(self):
        """Total ajustado por notas de crédito/débito que lo referencian."""
        return (self.total - self._anotado('ann_nc', lambda: self._notas('07', 'total'))
                + self._anotado('ann_nd', lambda: self._notas('08', 'total')))

    @property
    def neto(self):
        return self.total_documento - self.retencion_monto + self.percepcion_monto

    @property
    def pagado(self):
        return self._anotado('ann_pagado', lambda: self.movimientos.aggregate(s=Sum('monto_doc'))['s'])

    @property
    def es_nota_aplicada(self):
        return self.tipo_comprobante in ('07', '08') and bool(self.doc_referencia_id)

    @property
    def saldo(self):
        if self.estado == 'ANULADO' or self.es_nota_aplicada:
            return D0
        return self.neto - self.pagado

    @property
    def saldo_pen(self):
        """Saldo en soles con los mismos importes que usa la contabilidad (cuentas 12 y 42)."""
        if self.estado == 'ANULADO' or self.es_nota_aplicada:
            return D0
        pagado = self._anotado('ann_pagado_pen', lambda: self.movimientos.aggregate(s=Sum('monto_doc_pen'))['s'])
        return (self.total_pen - self._anotado('ann_nc_pen', lambda: self._notas('07', 'total_pen'))
                + self._anotado('ann_nd_pen', lambda: self._notas('08', 'total_pen'))
                - self.ret_pen + self.perc_pen - pagado)

    @property
    def dias_vencido(self):
        if not self.fecha_vencimiento:
            return 0
        return (timezone.localdate() - self.fecha_vencimiento).days

    # ---- almacén
    def _signo_stock(self):
        raise NotImplementedError

    def _costo_entrada(self, item):
        return None

    def aplicar_stock(self):
        if self.stock_aplicado or self.estado == 'ANULADO':
            return
        if not self.almacen_id:
            self.almacen = Almacen.principal()
        signo = self._signo_stock()
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                costo = self._costo_entrada(item) if signo > 0 else None
                item.producto.mover_stock(signo * item.cantidad, str(self), costo=costo, fecha=self.fecha_emision,
                                          almacen=self.almacen, origen=self._meta.model_name.upper())
        self.stock_aplicado = True
        self.save(update_fields=['stock_aplicado', 'almacen'])

    def revertir_stock(self):
        if not self.stock_aplicado:
            return
        signo = -self._signo_stock()
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                # con la fecha del documento: el costo del periodo queda neto y coincide con la contabilidad
                item.producto.mover_stock(signo * item.cantidad, f'Reversión {self}', fecha=self.fecha_emision,
                                          almacen=self.almacen, origen=self._meta.model_name.upper())
        self.stock_aplicado = False
        self.save(update_fields=['stock_aplicado'])


class ElectronicoMixin(models.Model):
    """Estado del envío al OSE/SUNAT (comprobantes de venta y guías)."""
    ESTADOS_SUNAT = [('NO_ENVIADO', 'No enviado'), ('PENDIENTE', 'Pendiente SUNAT'), ('ACEPTADO', 'Aceptado'),
                     ('RECHAZADO', 'Rechazado'), ('ERROR', 'Error de envío'), ('BAJA', 'Comunicado de baja')]
    estado_sunat = models.CharField('Estado SUNAT', max_length=10, choices=ESTADOS_SUNAT, default='NO_ENVIADO')
    sunat_descripcion = models.TextField('Respuesta SUNAT', blank=True)
    enlace_pdf = models.URLField(max_length=500, blank=True)
    enlace_xml = models.URLField(max_length=500, blank=True)
    enlace_cdr = models.URLField(max_length=500, blank=True)
    codigo_hash = models.CharField(max_length=100, blank=True)
    cadena_qr = models.TextField(blank=True)
    fecha_envio = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
