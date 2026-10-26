from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models, transaction
from django.db.models import OuterRef, Q, Subquery, Sum, Value
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
    ('50', 'DUA / DAM (importación)'),
    ('91', 'Comprobante de no domiciliado'),
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


DIAS_AVISO_VENCIMIENTO = 30  # lotes que vencen en este plazo aparecen en las alertas


class Empresa(models.Model):
    ruc = models.CharField('RUC', max_length=11)
    razon_social = models.CharField('Razón social', max_length=200)
    nombre_comercial = models.CharField(max_length=200, blank=True)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    telefono = models.CharField('Teléfono', max_length=50, blank=True)
    email = models.EmailField(blank=True)
    igv_tasa = models.DecimalField('Tasa IGV %', max_digits=5, decimal_places=2, default=Decimal('18.00'))
    regla_salida = models.CharField('Regla de salida de lotes', max_length=6, default='FEFO',
                                    choices=[('FEFO', 'FEFO: sale primero lo que vence primero'),
                                             ('FIFO', 'FIFO: sale primero lo que ingresó primero'),
                                             ('MANUAL', 'Manual: se indica el lote en cada salida')],
                                    help_text='La de los productos que no indican otra (INV-29)')
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
    oc_directa_con_contrato = models.BooleanField(
        'OC directa con contrato marco', default=True,
        help_text='El MRP y la reposición crean la orden de compra directamente (sin solicitud de pedido) para los '
                  'productos con proveedor habitual y contrato marco vigente; lo demás pasa por solicitud de pedido')
    CONTROL_PRESUPUESTO = [('', 'No controlar'), ('AVISAR', 'Avisar'), ('BLOQUEAR', 'Bloquear')]
    presupuesto_solped = models.CharField(
        'Presupuesto en solicitudes de pedido', max_length=8, choices=CONTROL_PRESUPUESTO, default='AVISAR',
        blank=True, help_text='Si la solicitud supera lo que queda del presupuesto de su centro de costo y cuenta (real '
                              'más lo comprometido en solicitudes y órdenes de compra)')
    tope_diferencias_inventario = models.DecimalField(
        'Tope de diferencias de inventario S/', max_digits=12, decimal_places=2, default=Decimal('5000'),
        help_text='Inventario físico: el jefe de almacén aprueba diferencias hasta este valor absoluto; más, la '
                  'contraloría o la gerencia (permiso «Aprobar diferencias de inventario sobre el tope»)')
    tolerancia_cantidad = models.DecimalField(
        'Tolerancia de cantidad (±)', max_digits=10, decimal_places=2, default=Decimal('1'),
        help_text='Diferencia permitida entre la cantidad facturada por el proveedor y la recibida / pedida')
    tolerancia_precio = models.DecimalField(
        'Tolerancia de precio unitario (±)', max_digits=10, decimal_places=4, default=Decimal('1'),
        help_text='Diferencia permitida entre el precio facturado y el de la orden de compra')
    tolerancia_total = models.DecimalField(
        'Tolerancia del monto total (±)', max_digits=10, decimal_places=2, default=Decimal('1'),
        help_text='Diferencia permitida entre el total de la factura del proveedor y el calculado con sus líneas')
    tolerancia_cantidad_pct = models.DecimalField(
        'Tolerancia de cantidad (±%)', max_digits=5, decimal_places=2, default=Decimal('0'),
        help_text='0 = solo el importe. Con ambos vale el más estricto')
    tolerancia_precio_pct = models.DecimalField(
        'Tolerancia de precio unitario (±%)', max_digits=5, decimal_places=2, default=Decimal('0'),
        help_text='0 = solo el importe. Con ambos vale el más estricto. Cada proveedor puede tener las suyas')
    tolerancia_total_pct = models.DecimalField(
        'Tolerancia del monto total (±%)', max_digits=5, decimal_places=2, default=Decimal('0'),
        help_text='0 = solo el importe. Con ambos vale el más estricto')
    DECIMALES_COSTO = [('AUTO', 'Automático: 2, o 4 si el costo es menor a 1'), ('2', '2 decimales'),
                       ('4', '4 decimales')]
    decimales_costo = models.CharField(
        'Decimales del costo unitario en pantalla', max_length=4, choices=DECIMALES_COSTO, default='AUTO',
        help_text='Solo cómo se muestra: el costo se guarda con 4 a 6 decimales. Cantidades e importes van con 2')
    alerta_precio_pct = models.DecimalField(
        'Alerta de precio en la orden de compra (%)', max_digits=5, decimal_places=2, default=Decimal('10'),
        help_text='La OC avisa si un precio supera el último pagado por el producto en más de este %. 0 = sin aviso')
    sunat_client_id = models.CharField(
        'SUNAT API: client_id', max_length=100, blank=True,
        help_text='Credenciales de "Consulta de validez de comprobantes" (SUNAT Operaciones en Línea > '
                  'Empresas > Comprobantes de pago > Credenciales de API SUNAT)')
    sunat_client_secret = models.CharField('SUNAT API: client_secret', max_length=200, blank=True)
    # ---- SIRE (registros de ventas y compras): API con usuario SOL; la clave SOL no se guarda, se pide al usarla
    sire_client_id = models.CharField(
        'SIRE: client_id', max_length=100, blank=True,
        help_text='Credenciales de API SUNAT generadas en SUNAT Operaciones en Línea con el usuario SOL que '
                  'opera el SIRE. Vacío = las de la consulta de validez')
    sire_client_secret = models.CharField('SIRE: client_secret', max_length=200, blank=True)
    sire_usuario_sol = models.CharField('SIRE: usuario SOL', max_length=30, blank=True,
                                        help_text='Solo el usuario (sin el RUC). La clave SOL se pide cada vez '
                                                  'y no se guarda en el sistema')
    # ---- migración: hasta esta fecha la contabilidad es la del sistema anterior (se importa tal cual)
    fecha_corte_contable = models.DateField(
        'Contabilidad del sistema anterior hasta', null=True, blank=True, editable=False,
        help_text='Hasta esta fecha los libros son los asientos importados del sistema anterior; el ERP contabiliza '
                  'desde el día siguiente')
    # ---- plan de cuentas: se cambia solo con «manage.py adoptar_pcge2026» (rehace cuentas y referencias)
    PLANES_CONTABLES = [('PCGE2019', 'PCGE modificado 2019'), ('PCGE2026', 'PCGE 2026')]
    plan_contable = models.CharField('Plan contable', max_length=8, choices=PLANES_CONTABLES, default='PCGE2019',
                                     editable=False)
    # ---- control interno
    # ---- seguridad de acceso
    DOBLE_FACTOR = [('', 'Opcional para cada usuario'), ('APROBADORES', 'Obligatoria para administradores y '
                                                                      'aprobadores'),
                    ('TODOS', 'Obligatoria para todos')]
    exigir_doble_factor = models.CharField('Verificación en dos pasos', max_length=12, choices=DOBLE_FACTOR,
                                           blank=True, default='')
    clave_largo_minimo = models.PositiveSmallIntegerField('Contraseña: largo mínimo', default=8)
    clave_mayuscula = models.BooleanField('Contraseña: exige mayúscula y minúscula', default=True)
    clave_numero = models.BooleanField('Contraseña: exige un número', default=True)
    clave_simbolo = models.BooleanField('Contraseña: exige un símbolo', default=False)
    clave_vence_dias = models.PositiveSmallIntegerField('Contraseña: vence a los (días)', default=0,
                                                        help_text='0 = no vence')
    clave_historial = models.PositiveSmallIntegerField('Contraseña: no repetir las últimas', default=3)
    intentos_bloqueo = models.PositiveSmallIntegerField('Bloquear tras intentos fallidos', default=5)
    minutos_inactividad = models.PositiveSmallIntegerField('Cerrar sesión tras minutos sin actividad', default=30,
                                                           help_text='0 = no cerrar')
    aviso_equipo_nuevo = models.BooleanField('Avisar por correo el ingreso desde un equipo nuevo', default=True)
    dias_escalamiento = models.PositiveSmallIntegerField(
        'Escalar aprobaciones a los (días)', default=3,
        help_text='Si un nivel de aprobación pasa estos días sin respuesta, también puede aprobarlo el jefe del '
                  'aprobador. 0 = no escalar')
    # MAN-14: consumo de materiales de las órdenes de producción (cada receta puede tener la suya)
    POLITICAS_CONSUMO = [('LIBRE', 'Libre: se registra lo consumido'),
                         ('TOLERANCIA', 'Con tolerancia: fuera del ± % lo aprueba otro usuario, con motivo'),
                         ('ESTRICTO', 'Estricta: se consume exactamente la receta')]
    politica_consumo = models.CharField('Consumo en las órdenes de producción', max_length=10,
                                        choices=POLITICAS_CONSUMO, default='LIBRE')
    tolerancia_consumo_pct = models.DecimalField('Tolerancia del consumo ± %', max_digits=5, decimal_places=2,
                                                 default=Decimal('5'))
    segregacion_estricta = models.BooleanField(
        'Segregación de funciones estricta', default=False,
        help_text='Nadie aprueba lo que registró (órdenes de compra, requerimientos, planillas), ni siquiera el '
                  'administrador, mientras haya otro usuario activo que pueda hacerlo')
    # ---- giro y régimen (asistente «Giro y régimen tributario»; se cambian allí y quedan en la auditoría)
    GIROS = [('COMERCIAL', 'Comercializadora'), ('MANUFACTURA', 'Manufactura'),
             ('CONSTRUCCION', 'Construcción por contrato'), ('INMOBILIARIA', 'Inmobiliaria (venta de inmuebles)'),
             ('SERVICIOS', 'Servicios'),
             ('SERVICIOS_PROYECTO', 'Servicios profesionales por proyecto (arquitectura, ingeniería, consultoría, '
                                    'supervisión)')]
    REGIMENES = [('GENERAL', 'Régimen General'), ('MYPE', 'Régimen MYPE Tributario'),
                 ('RER', 'Régimen Especial (RER)'), ('NRUS', 'Nuevo RUS')]
    COSTEOS = [('NATURALEZA', 'Sin costeo: gasto directo por naturaleza (sin clase 9)'),
               ('PROCESOS', 'Costeo por procesos (manufactura)'), ('ORDENES', 'Costeo por órdenes u obras')]
    giros = models.JSONField('Giros del negocio', default=list, blank=True, editable=False)
    ciiu = models.CharField('CIIU', max_length=10, blank=True, editable=False)
    actividad_economica = models.CharField('Actividad económica (SUNAT)', max_length=200, blank=True, editable=False)
    regimen_tributario = models.CharField('Régimen tributario', max_length=7, choices=REGIMENES, default='GENERAL',
                                          editable=False)
    sistema_costeo = models.CharField('Sistema de costeo', max_length=10, choices=COSTEOS, default='PROCESOS',
                                      editable=False)
    modulos_ocultos = models.JSONField('Módulos que no usa la empresa', default=list, blank=True, editable=False)

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


BANCOS = [('', '---'), ('BCP', 'BCP'), ('BBVA', 'BBVA'), ('SCOTIABANK', 'Scotiabank'), ('INTERBANK', 'Interbank'),
          ('BN', 'Banco de la Nación'), ('BANBIF', 'BanBif'), ('PICHINCHA', 'Pichincha'), ('GNB', 'GNB'),
          ('OTRO', 'Otro')]


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
    # FIN-03 y FIN-04: cobranza y programación de pagos
    gestor_cobranza = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+', verbose_name='Gestor de cobranza')
    bloqueado_mora = models.BooleanField('Bloqueado por mora', default=False, editable=False,
                                         help_text='Lo marca la cobranza al superar los días de mora del parámetro')
    proveedor_critico = models.BooleanField('Proveedor crítico', default=False,
                                            help_text='Se paga primero en la propuesta de pagos')
    descuento_pronto_pago_pct = models.DecimalField('Descuento por pronto pago %', max_digits=5, decimal_places=2,
                                                    default=D0, help_text='Proveedor: descuento si se paga a tiempo')
    dias_pronto_pago = models.PositiveSmallIntegerField('Días para el pronto pago', default=0,
                                                        help_text='Desde la emisión de la factura')
    lista_precios = models.ForeignKey('ventas.ListaPrecios', on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='clientes', verbose_name='Lista de precios',
                                      help_text='Precios especiales del cliente (mayorista, distribuidor…)')
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Ubigeo de la dirección (para guías)')
    registro_mtc = models.CharField('Registro MTC', max_length=20, blank=True,
                                    help_text='Solo empresas de transporte (guía transportista)')
    # cuenta donde se le paga (pagos masivos a bancos)
    banco = models.CharField(max_length=12, choices=BANCOS, blank=True)
    cuenta_bancaria = models.CharField('N° de cuenta', max_length=30, blank=True,
                                       help_text='Cuenta en el mismo banco desde el que se paga')
    cci = models.CharField('CCI', max_length=20, blank=True, help_text='Código de cuenta interbancario (20 dígitos)')
    cuenta_detracciones = models.CharField('Cuenta de detracciones (BN)', max_length=20, blank=True)
    # tolerancias propias del proveedor para sus facturas (vacío = las de Ajustes › Empresa)
    tolerancia_precio = models.DecimalField('Tolerancia de precio (±)', max_digits=10, decimal_places=4, null=True,
                                            blank=True, help_text='Solo proveedores. Vacío = la de la empresa')
    tolerancia_precio_pct = models.DecimalField('Tolerancia de precio (±%)', max_digits=5, decimal_places=2,
                                                null=True, blank=True, help_text='Vacío = la de la empresa')
    tolerancia_total = models.DecimalField('Tolerancia del total (±)', max_digits=10, decimal_places=2, null=True,
                                           blank=True, help_text='Solo proveedores. Vacío = la de la empresa')
    tolerancia_total_pct = models.DecimalField('Tolerancia del total (±%)', max_digits=5, decimal_places=2,
                                               null=True, blank=True, help_text='Vacío = la de la empresa')
    # proveedor del exterior (importaciones): no tiene RUC; su comprobante se registra como 91 (no domiciliado)
    INCOTERMS = [('', '—'), ('EXW', 'EXW'), ('FCA', 'FCA'), ('FAS', 'FAS'), ('FOB', 'FOB'), ('CFR', 'CFR'),
                 ('CIF', 'CIF'), ('CPT', 'CPT'), ('CIP', 'CIP'), ('DAP', 'DAP'), ('DPU', 'DPU'), ('DDP', 'DDP')]
    pais = models.CharField('País', max_length=60, blank=True, help_text='Vacío = Perú')
    incoterm = models.CharField('Incoterm habitual', max_length=3, choices=INCOTERMS, blank=True)
    moneda = models.CharField('Moneda habitual', max_length=3, choices=[('', '—'), ('PEN', 'Soles'),
                                                                       ('USD', 'Dólares')], blank=True)
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
    ('ENVASE', 'Envases y embalajes'),
    ('ACTIVO', 'Activo fijo (solo compra)'),
    ('SERVICIO', 'Servicio'),
]
PREFIJOS_PRODUCTO = {'MERCADERIA': 'ME', 'MATERIA_PRIMA': 'MP', 'SEMIELABORADO': 'SE', 'PRODUCTO_TERMINADO': 'PT',
                     'SUMINISTRO': 'SU', 'ENVASE': 'EM', 'ACTIVO': 'AF', 'SERVICIO': 'SV'}
# Juego de cuentas por defecto (PCGE 2019; en el PCGE 2026 se traducen con pcge.codigo): existencias, compra, venta,
# costo de ventas / consumo
CUENTAS_PRODUCTO = {
    'MERCADERIA': ('20111', '6011', '70111', '69111'),
    'MATERIA_PRIMA': ('2411', '6021', '70111', '69111'),
    'SEMIELABORADO': ('2311', '6021', '7021', '6921'),
    'PRODUCTO_TERMINADO': ('2111', '6011', '7021', '6921'),
    'SUMINISTRO': ('2521', '6032', '7599', '6561'),
    'ENVASE': ('2611', '6041', '70111', '69111'),
    'ACTIVO': ('', '3369', '7599', ''),
    'SERVICIO': ('', '6399', '7041', ''),
}
CAMPOS_CUENTA = ('cuenta_existencias', 'cuenta_compra', 'cuenta_venta', 'cuenta_costo')
# clases de cuenta permitidas en cada campo del producto (INV-12): no se puede poner «101 Caja» como existencias
PREFIJOS_CAMPO = {'cuenta_existencias': ('20', '21', '22', '23', '24', '25', '26', '27', '28'),
                  'cuenta_compra': ('60', '33', '63'), 'cuenta_venta': ('70', '75'), 'cuenta_costo': ('69', '65')}


def tipo_de_clase(clase):
    return {'SERVICIO': 'SERVICIO', 'ACTIVO': 'ACTIVO'}.get(clase, 'BIEN')


_TABLA_CUENTAS_TIPO = set()


def _tabla_cuentas_tipo(alias):
    if alias not in _TABLA_CUENTAS_TIPO:
        from django.db import connections
        if 'core_cuentastipoproducto' not in connections[alias].introspection.table_names():
            return False
        _TABLA_CUENTAS_TIPO.add(alias)
    return True


def cuentas_de_clase(clase, CuentaContable=None):
    """{campo: CuentaContable} del tipo de producto: la tabla editable de Contabilidad › Cuentas por tipo de producto
    (clase de valoración) y, lo que no esté configurado, el juego por defecto del PCGE."""
    from contabilidad import pcge
    if CuentaContable is None:
        from contabilidad.models import CuentaContable
    alias = CuentaContable.objects.db
    plan = pcge.plan(alias)
    salida = {}
    codigos = CUENTAS_PRODUCTO.get(clase, CUENTAS_PRODUCTO['MERCADERIA'])
    for campo, codigo in zip(CAMPOS_CUENTA, codigos):
        salida[campo] = CuentaContable.objects.filter(codigo=pcge.codigo(codigo, plan)).first() if codigo else None
    # las migraciones antiguas llaman a asignar_cuentas antes de que exista la tabla (una consulta fallida dejaría
    # la transacción de PostgreSQL inservible): se mira primero si la tabla existe
    fila = (CuentasTipoProducto.objects.using(alias).filter(clase=clase).first()
            if _tabla_cuentas_tipo(alias) else None)
    if fila is not None:
        for campo in CAMPOS_CUENTA:
            if getattr(fila, f'{campo}_id'):
                salida[campo] = CuentaContable.objects.filter(pk=getattr(fila, f'{campo}_id')).first()
    return salida


def asignar_cuentas(producto, CuentaContable):
    """Completa las cuentas vacías del producto con las de su tipo (acepta modelo histórico)."""
    for campo, cuenta in cuentas_de_clase(producto.clase, CuentaContable).items():
        if cuenta is not None and not getattr(producto, f'{campo}_id'):
            setattr(producto, campo, cuenta)


class Producto(models.Model):
    TIPOS = [('BIEN', 'Bien (inventariable)'), ('SERVICIO', 'Servicio'), ('ACTIVO', 'Activo fijo (no inventariable)')]

    def precio_para(self, lista=None, cantidad=Decimal('1'), fecha=None):
        """(precio sin IGV, descuento %) según la lista de precios y el tramo de cantidad; si no hay, el precio de
        venta del producto."""
        fecha = fecha or timezone.localdate()
        if lista is not None and lista.vigente(fecha):
            tramo = (lista.precios.filter(producto=self, cantidad_minima__lte=cantidad)
                     .order_by('-cantidad_minima').first())
            if tramo:
                return (tramo.precio if tramo.precio is not None else self.precio_venta), tramo.descuento_pct
        return self.precio_venta, D0
    CLASES = CLASES_PRODUCTO
    UNIDADES = [('NIU', 'Unidad'), ('KGM', 'Kilogramo'), ('LTR', 'Litro'), ('MTR', 'Metro'),
                ('BX', 'Caja'), ('PK', 'Paquete'), ('GLL', 'Galón'), ('MIL', 'Millar'), ('RO', 'Rollo'),
                ('MTQ', 'Metro cúbico'), ('HUR', 'Hora'), ('ZZ', 'Servicio'),
                # construcción (catálogo 03 de SUNAT)
                ('BG', 'Bolsa'), ('MTK', 'Metro cuadrado'), ('FTK', 'Pie cuadrado'), ('TNE', 'Tonelada'),
                ('DAY', 'Día')]

    # ---- general
    codigo = models.CharField('Código', max_length=30, unique=True, blank=True,
                              help_text='Vacío = automático según el tipo (ej. MP000001)')
    codigo_alterno = models.CharField('Código alterno', max_length=30, blank=True, db_index=True,
                                      help_text='Código en otro sistema (ej. el insumo del S10): evita duplicar el '
                                                'producto al importar')
    nombre = models.CharField(max_length=200)
    clase = models.CharField('Tipo de producto', max_length=20, choices=CLASES, default='MERCADERIA')
    tipo = models.CharField(max_length=10, choices=TIPOS, default='BIEN', editable=False)
    unidad = models.CharField('Unidad de medida', max_length=5, choices=UNIDADES, default='NIU')
    marca = models.CharField(max_length=80, blank=True)
    codigo_barras = models.CharField('Código de barras', max_length=40, blank=True)
    peso = models.DecimalField('Peso por unidad (kg)', max_digits=12, decimal_places=3, default=D0,
                               help_text='Calcula el peso bruto de las guías de remisión')
    # ---- reportes de producción: conversión a kg y toneladas
    peso_neto = models.DecimalField('Peso neto por unidad (kg)', max_digits=12, decimal_places=4, default=D0,
                                    help_text='Kilos de producto en una unidad base (convierte a kg y TM en los '
                                              'reportes). En kg no hace falta. Vacío = el peso bruto')
    familia = models.CharField(max_length=60, blank=True, help_text='Agrupa productos en los reportes. Ej. Salsas')
    UNIDADES_REPORTE = [('BASE', 'Unidad base del producto'), ('KG', 'Kilogramos'), ('TM', 'Toneladas')]
    unidad_reporte = models.CharField('Unidad de reporte', max_length=4, choices=UNIDADES_REPORTE, default='KG')
    centro_beneficio = models.ForeignKey('contabilidad.CentroBeneficio', on_delete=models.SET_NULL, null=True,
                                         blank=True, related_name='+', verbose_name='Línea de negocio',
                                         help_text='Ventas, costo de ventas e inventario del producto van a esta línea')
    control = models.CharField('Control de existencias', max_length=5, blank=True, default='',
                               choices=[('', 'Sin lote ni serie'), ('LOTE', 'Por lote (con vencimiento)'),
                                        ('SERIE', 'Por número de serie (unidad por unidad)')],
                               help_text='Farmacia y alimentos: por lote. Equipos: por número de serie')
    # ---- lotes y vencimientos (INV-28, INV-29)
    exige_vencimiento = models.BooleanField('Lote con vencimiento obligatorio', default=True,
                                            help_text='Por lote: no ingresa un lote sin vencimiento')
    regla_salida = models.CharField('Regla de salida de lotes', max_length=6, blank=True, default='',
                                    choices=[('', 'La de la empresa (Ajustes › Empresa)'),
                                             ('FEFO', 'FEFO: sale primero lo que vence primero'),
                                             ('FIFO', 'FIFO: sale primero lo que ingresó primero'),
                                             ('MANUAL', 'Manual: se indica el lote en cada salida')])
    vida_util_dias = models.PositiveIntegerField('Vida útil (días)', default=0,
                                                 help_text='Vencimiento = fabricación + vida útil (0 = no se calcula)')
    vencimiento_mes = models.BooleanField('Vence por mes', default=False,
                                          help_text='El vencimiento es el último día del mes (MM/AAAA)')
    vida_remanente_recepcion = models.PositiveIntegerField(
        'Vida remanente mínima para recibir (días)', default=0, help_text='0 = sin mínimo')
    vida_remanente_despacho = models.PositiveIntegerField(
        'Vida remanente mínima para despachar (días)', default=0, help_text='0 = sin mínimo')
    exige_proveedor_calificado = models.BooleanField(
        'Solo de proveedores calificados', default=False,
        help_text='CAL-10: la orden de compra y la recepción exigen un proveedor aprobado y vigente para el producto')
    es_epp = models.BooleanField('Es EPP (equipo de protección personal)', default=False,
                                 help_text='INV-32: solo estos productos se entregan en SST › Entrega de EPP')
    afectacion_igv = models.CharField('Afectación del IGV', max_length=10, choices=AFECTACION_LINEA, blank=True,
                                      default='', help_text='Ej. libros (exonerados) o productos agrarios '
                                                            '(inafectos). Vacío = según el comprobante')
    # ---- detracción (SPOT): las facturas de compra y de venta del producto la toman de aquí
    detraccion_codigo = models.CharField('Bien/servicio con detracción', max_length=3, blank=True, default='',
                                         help_text='Catálogo 54 SUNAT. Vacío = no está sujeto a detracción')
    detraccion_pct = models.DecimalField('% Detracción', max_digits=5, decimal_places=2, default=D0,
                                         help_text='La factura lo toma si no indica otro y su total supera S/ 700')
    descripcion = models.TextField('Descripción', blank=True)
    activo = models.BooleanField(default=True)
    imagen = models.ForeignKey('ArchivoSustento', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                               editable=False)
    # ---- variantes (talla, color...): cada variante es un producto con su propio stock y kardex
    es_plantilla = models.BooleanField('Plantilla de variantes', default=False, editable=False,
                                       help_text='Agrupa variantes; no se usa en documentos ni tiene stock')
    plantilla = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='variantes',
                                  editable=False, verbose_name='Variante de')
    atributos = models.JSONField('Atributos', default=dict, blank=True, editable=False)
    # ---- compras
    puede_comprarse = models.BooleanField('Se puede comprar', default=True)
    precio_compra = models.DecimalField('Precio de compra (sin IGV)', max_digits=12, decimal_places=4, default=D0,
                                        help_text='Precio referencial para órdenes de compra')
    proveedor = models.ForeignKey('Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                  verbose_name='Proveedor habitual', limit_choices_to={'tipo__in': ['PROVEEDOR', 'AMBOS']})
    unidad_compra = models.CharField('Unidad de compra', max_length=5, choices=UNIDADES, blank=True,
                                     help_text='Vacío = la misma unidad de medida')
    factor_compra = models.DecimalField('Unidades por unidad de compra', max_digits=12, decimal_places=4,
                                        default=Decimal('1'),
                                        help_text='Ej. se compra por caja de 12 y se almacena por unidad: 12. Las '
                                                  'órdenes y facturas de compra van en unidad de compra; el almacén '
                                                  'en unidad de medida')
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
    PLANIFICACIONES = [('MRP', 'MRP: según la demanda'), ('REORDEN', 'Punto de reorden'), ('NINGUNA', 'Sin planificar')]
    ESTRATEGIAS = [('MTS', 'Para stock (MTS)'), ('MTO', 'Contra pedido (MTO)')]
    tipo_planificacion = models.CharField(
        'Tipo de planificación', max_length=8, choices=PLANIFICACIONES, default='MRP',
        help_text='MRP: según pedidos, plan de demanda y recetas. Punto de reorden: se repone al llegar al punto de '
                  'reorden, fuera del MRP (suministros indirectos). Sin planificar: el MRP no lo propone')
    estrategia = models.CharField(
        'Estrategia', max_length=3, choices=ESTRATEGIAS, default='MTS',
        help_text='Para stock: se planifica con el plan de demanda y el stock disponible. Contra pedido: se fabrica o '
                  'compra solo por los pedidos de venta, sin usar el stock libre')
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

    @property
    def stock_variantes(self):
        return self.variantes.aggregate(t=models.Sum('stock'))['t'] or D0

    def save(self, *args, **kwargs):
        self.tipo = tipo_de_clase(self.clase)
        if self.clase == 'ACTIVO':
            self.puede_venderse = False
        if self._state.adding and self.clase == 'SUMINISTRO' and self.tipo_planificacion == 'MRP':
            self.tipo_planificacion = 'REORDEN'  # suministros indirectos: por punto de reorden, fuera del MRP
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
    def kg_por_unidad(self):
        """Kilos en una unidad base (para reportar en kg y TM); None si no se puede convertir."""
        if self.unidad == 'KGM':
            return Decimal('1')
        return self.peso_neto or self.peso or None

    @property
    def factor(self):
        return self.factor_compra if self.factor_compra and self.factor_compra > 0 else Decimal('1')

    def a_stock(self, cantidad_compra):
        """Cantidad de compra (cajas) -> unidades de almacén."""
        return cantidad_compra * self.factor

    def a_compra(self, cantidad_stock):
        """Unidades de almacén -> unidades de compra (redondeo hacia arriba a unidades enteras si hay factor)."""
        from decimal import ROUND_CEILING
        if self.factor == 1:
            return cantidad_stock
        return (cantidad_stock / self.factor).quantize(Decimal('1'), rounding=ROUND_CEILING)

    @property
    def unidad_de_compra(self):
        return self.unidad_compra or self.unidad

    @property
    def precio_referencia(self):
        """Activos (solo compra): su precio es el de compra; los demás, el de venta."""
        return self.precio_compra if self.clase == 'ACTIVO' else self.precio_venta

    @property
    def requiere_reposicion(self):
        limite = self.punto_reorden or self.stock_minimo
        return self.es_inventariable and not self.es_plantilla and limite > 0 and self.stock <= limite

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
                    codigo_sunat='', lotes=None, lotes_de=None, centro_costo=None, cuenta_contra=None, destino=None):
        """cantidad > 0 entrada, < 0 salida. Actualiza costo promedio en entradas y el stock del almacén.

        Productos con lote o serie: `lotes` = [(código, cantidad, vencimiento[, fabricación])] indica los lotes;
        `lotes_de` = la referencia de un movimiento que se revierte (vuelven a los mismos lotes). Sin ninguno, las
        salidas siguen la regla de salida (FEFO, FIFO o manual) sobre el stock libre (inventario/estados.py).
        `destino`: almacén al que va un traslado (un lote no libre solo se traslada a calidad o cuarentena).

        Una salida con `costo` (reversión de una entrada) sale a ese costo y no al promedio: el promedio se recalcula
        para que el valor del inventario quede como antes de la entrada (INV-16)."""
        cantidad = Decimal(cantidad)
        if not self.es_inventariable or cantidad == 0:
            return
        from inventario.cierre import validar_fecha
        validar_fecha(fecha or timezone.localdate())  # kardex cerrado: no se mueve el almacén en esa fecha
        almacen = almacen or Almacen.principal()
        almacen.validar_operacion(origen, concepto, referencia)  # operaciones permitidas del almacén
        from inventario.fisico import validar_movimiento
        validar_movimiento(self, almacen)  # producto en un inventario físico con bloqueo
        categoria = categoria_movimiento(origen, concepto)
        if cantidad < 0 and not lotes_de:  # INV-26: lo que está en calidad o bloqueado solo sale trasladándolo
            from inventario.estados import validar_almacen_origen
            validar_almacen_origen(self, almacen, categoria, concepto, origen, destino)
        with transaction.atomic():
            # se relee y bloquea el producto: dos líneas del mismo producto no deben pisarse
            actual = Producto.objects.select_for_update().get(pk=self.pk)
            if costo is not None:
                nuevo_stock = actual.stock + cantidad
                if nuevo_stock > 0:
                    total = actual.stock * actual.costo_promedio + cantidad * Decimal(costo)
                    if total >= 0:
                        actual.costo_promedio = (total / nuevo_stock).quantize(Decimal('0.0001'))
            saldo_inicial = actual.stock
            actual.stock += cantidad
            actual.save(update_fields=['stock', 'costo_promedio'])
            self.stock, self.costo_promedio = actual.stock, actual.costo_promedio
            sa, _ = StockAlmacen.objects.select_for_update().get_or_create(producto=self, almacen=almacen)
            sa.cantidad += cantidad
            sa.save(update_fields=['cantidad'])
            signo = 1 if cantidad > 0 else -1
            saldo = saldo_inicial
            for lote, parte in self._repartir_lotes(abs(cantidad), signo, almacen, lotes, lotes_de, categoria,
                                                    concepto, origen, destino, fecha):
                if lote is not None:
                    sl, _ = StockLote.objects.select_for_update().get_or_create(lote=lote, almacen=almacen)
                    sl.cantidad += signo * parte
                    sl.save(update_fields=['cantidad'])
                saldo += signo * parte
                Kardex.objects.create(
                    producto=self, almacen=almacen, fecha=fecha or timezone.localdate(),
                    tipo='ENTRADA' if signo > 0 else 'SALIDA', cantidad=parte, lote=lote,
                    costo_unitario=costo if costo is not None else actual.costo_promedio,
                    costo_promedio=actual.costo_promedio, saldo=saldo, referencia=referencia, origen=origen,
                    concepto=concepto, codigo_sunat=codigo_sunat or _codigo_sunat(origen, concepto, cantidad),
                    centro_costo=centro_costo, cuenta_contra=cuenta_contra,
                )

    def _requiere_liberacion(self):
        from django.apps import apps
        return apps.get_model('produccion', 'PlantillaCertificado').objects.filter(
            producto=self, requiere_liberacion=True).exists()

    def _repartir_lotes(self, cantidad, signo, almacen, lotes, lotes_de, categoria=None, concepto='', origen='',
                        destino=None, fecha=None):
        """[(Lote o None, cantidad)] del movimiento. Sin control de lotes: una sola parte sin lote. Un lote en
        cuarentena, rechazado, bloqueado o vencido no sale salvo las excepciones de inventario/estados.py; la salida
        sin lote indicado sigue la regla de salida sobre el stock libre."""
        if not self.control:
            return [(None, cantidad)]
        from inventario import estados
        partes, restante = [], cantidad
        if lotes:
            for datos in lotes:
                codigo, cant, vence = datos[0], datos[1], datos[2] if len(datos) > 2 else None
                fabricacion = datos[3] if len(datos) > 3 else None
                lote, creado = Lote.objects.get_or_create(producto=self, codigo=str(codigo).strip().upper()[:60])
                if signo > 0:
                    vence = estados.validar_ingreso(self, lote, creado, vence, fabricacion, categoria, fecha,
                                                    inicial=concepto in ('INICIAL', 'SALDO_INI'))
                    cambios = []
                    if vence and lote.vencimiento != vence:
                        lote.vencimiento = vence
                        cambios.append('vencimiento')
                    if fabricacion and not lote.fabricacion:
                        lote.fabricacion = fabricacion
                        cambios.append('fabricacion')
                    if creado and (self._requiere_liberacion() or getattr(self, '_ingreso_a_calidad', False)):
                        lote.estado_calidad = 'CUARENTENA'  # entra en calidad hasta que Calidad lo libere
                        cambios.append('estado_calidad')
                    if cambios:
                        lote.save(update_fields=cambios)
                else:
                    estados.validar_salida_lote(self, lote, categoria, concepto, origen, destino, fecha)
                partes.append((lote, Decimal(cant)))
                restante -= Decimal(cant)
        elif lotes_de:
            # se revierte un movimiento: los mismos lotes en sentido contrario
            origen = (Kardex.objects.filter(producto=self, tipo='SALIDA' if signo > 0 else 'ENTRADA',
                                            lote__isnull=False)
                      .filter(models.Q(referencia=lotes_de) | models.Q(referencia__startswith=f'{lotes_de} |'))
                      .values('lote').annotate(c=Sum('cantidad')).order_by('lote'))
            for fila in origen:
                if restante <= 0:
                    break
                parte = min(fila['c'], restante)
                partes.append((Lote.objects.get(pk=fila['lote']), parte))
                restante -= parte
        if restante > 0 and signo < 0:
            regla = estados.regla_de(self)
            excepcion = estados.salida_permitida(None, categoria, concepto, origen, destino)
            if regla == 'MANUAL' and not excepcion and not lotes_de:
                raise estados.ErrorLote(f'{self.nombre} sale con lote indicado (regla de salida manual): indique el '
                                        f'lote.')
            usados = {}
            for lote, parte in partes:
                usados[lote.pk] = usados.get(lote.pk, D0) + parte
            disponibles = estados.orden_salida(
                StockLote.objects.filter(lote__producto=self, almacen=almacen, cantidad__gt=0).select_related('lote'),
                regla)
            saltados = []
            for sl in disponibles:
                if restante <= 0:
                    break
                # INV-27: la salida automática solo toma lotes libres y vigentes (salvo destrucción o devolución)
                if not excepcion and not estados.utilizable(self, sl.lote, categoria, fecha):
                    saltados.append(sl.lote)
                    continue
                parte = min(sl.cantidad - usados.get(sl.lote_id, D0), restante)
                if parte > 0:
                    partes.append((sl.lote, parte))
                    restante -= parte
            if restante > 0 and saltados:
                detalle = ', '.join(f'{lt.codigo} ({"vencido" if lt.vencimiento and lt.vencimiento < (fecha or timezone.localdate()) else lt.get_estado_calidad_display().lower()})'
                                    for lt in saltados[:5])
                raise estados.ErrorLote(f'No hay stock libre suficiente de {self.nombre} en {almacen}: faltan '
                                        f'{restante.normalize():f}; no se toman los lotes {detalle}.')
        elif restante > 0:
            lote, _ = Lote.objects.get_or_create(producto=self, codigo='S/L')  # ingreso sin lote indicado
            partes.append((lote, restante))
            restante = D0
        if restante > 0:
            partes.append((None, restante))  # stock anterior al control de lotes
        return partes


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


# operaciones que un almacén puede permitir (CM-03, como en Odoo): vacío = todas
OPERACIONES_ALMACEN = [('COMPRAS', 'Recibe compras'), ('VENTAS', 'Entrega ventas'), ('TRASLADOS', 'Traslados'),
                       ('AJUSTES', 'Ajustes y saldos iniciales'), ('PRODUCCION', 'Producción (consumo y producto)'),
                       ('CONSUMO', 'Consumo interno y requerimientos'), ('MANTENIMIENTO', 'Salida a mantenimiento'),
                       ('DEVOLUCIONES', 'Devoluciones'), ('DESTRUCCION', 'Destrucción')]
CATEGORIA_OPERACION = {'REC_COMPRA': 'COMPRAS', 'SAL_VENTA': 'VENTAS', 'TRAS_ALM': 'TRASLADOS',
                       'TRAS_TRANS': 'TRASLADOS', 'REC_TRANS': 'TRASLADOS', 'TRAS_DESTR': 'DESTRUCCION',
                       'SAL_DESTR': 'DESTRUCCION', 'AJ_ING': 'AJUSTES', 'AJ_SAL': 'AJUSTES', 'SALDO_INI': 'AJUSTES',
                       'MANUF': 'PRODUCCION', 'CONS_INT': 'CONSUMO', 'CONS_REQ': 'CONSUMO',
                       'CONS_MANT': 'MANTENIMIENTO', 'DEV_CLI': 'DEVOLUCIONES', 'DEV_PROV': 'DEVOLUCIONES'}
CATEGORIA_ORIGEN = {'COMPRA': 'COMPRAS', 'VENTA': 'VENTAS', 'GUIA': 'TRASLADOS', 'AJUSTE': 'AJUSTES',
                    'SALDO_INICIAL': 'AJUSTES', 'INVENTARIO': 'AJUSTES'}


def categoria_movimiento(origen, concepto):
    if origen == 'OPERACION':
        return CATEGORIA_OPERACION.get(concepto)
    return CATEGORIA_ORIGEN.get(origen)


class Almacen(models.Model):
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    direccion = models.CharField('Dirección', max_length=250, blank=True)
    ubigeo = models.CharField(max_length=6, blank=True, help_text='Código de 6 dígitos (INEI)')
    codigo_sunat = models.CharField('Cód. establecimiento SUNAT', max_length=4, default='0000')
    es_principal = models.BooleanField('Principal', default=False)
    USOS = [('', 'Almacén normal'), ('TRANSITO', 'Mercadería en tránsito'),
            ('CALIDAD', 'En control de calidad (no libre)'), ('BLOQUEO', 'Stock bloqueado (no libre)'),
            ('DESTRUCCION', 'Cuarentena / por destruir'), ('TERCEROS', 'En poder de terceros (maquila)')]
    uso = models.CharField('Uso', max_length=12, choices=USOS, blank=True)
    tercero = models.ForeignKey('Tercero', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                help_text='Solo almacenes en poder de terceros: el maquilador que tiene la mercadería')
    CONTROLES = [('', 'No definido'), ('ANUAL', 'Inventario general anual'), ('CICLICO', 'Cíclico ABC'),
                 ('AMBOS', 'Cíclico ABC y general anual')]
    modelo_control = models.CharField('Modelo de control de inventario', max_length=8, choices=CONTROLES,
                                      blank=True, help_text='Cómo se audita el stock de este almacén (indicadores '
                                                            'de inventario físico)')
    operaciones = models.JSONField('Operaciones permitidas', default=list, blank=True,
                                   help_text='Vacío = todas. Se valida en cada movimiento de este almacén')
    responsables = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='almacenes_a_cargo',
                                          help_text='Usuarios responsables del almacén')
    activo = models.BooleanField(default=True)
    cerrado = models.BooleanField('Cerrado', default=False, editable=False,
                                  help_text='Almacén de una obra liquidada o cerrada: solo admite ajustes de inventario')

    class Meta:
        ordering = ['-es_principal', 'nombre']
        verbose_name = 'almacén'
        verbose_name_plural = 'almacenes'

    def __str__(self):
        return self.nombre

    def validar_operacion(self, origen, concepto, referencia=''):
        """El almacén permite el movimiento (operaciones permitidas). Las reversiones y anulaciones siempre pasan; un
        almacén cerrado (obra liquidada) solo admite ajustes de inventario."""
        if str(referencia).startswith(('Reversión', 'Anulación')):
            return
        if self.cerrado and origen != 'AJUSTE':
            from inventario.cierre import KardexCerrado
            raise KardexCerrado(f'El almacén {self} está cerrado (obra liquidada o cerrada): solo admite ajustes de '
                                f'inventario. Reabra la obra para moverlo.')
        if not self.operaciones:
            return
        categoria = categoria_movimiento(origen, concepto)
        if categoria and categoria not in self.operaciones:
            from inventario.cierre import KardexCerrado
            nombre = dict(OPERACIONES_ALMACEN).get(categoria, categoria).lower()
            raise KardexCerrado(f'El almacén {self} no permite esta operación ({nombre}). Operaciones permitidas: '
                                + ', '.join(dict(OPERACIONES_ALMACEN)[c].lower() for c in self.operaciones
                                            if c in dict(OPERACIONES_ALMACEN)) + '.')

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
    def de_tercero(cls, tercero):
        """Almacén virtual con la mercadería entregada al maquilador (se crea la primera vez)."""
        alm = cls.objects.filter(uso='TERCEROS', tercero=tercero).first()
        if alm is None:
            alm = cls.objects.create(codigo=f'MQ{tercero.pk:06d}'[:10], nombre=f'En poder de {tercero.nombre}'[:100],
                                     uso='TERCEROS', tercero=tercero)
        return alm

    @classmethod
    def principal(cls):
        alm = cls.objects.filter(es_principal=True, activo=True).first() or cls.objects.filter(activo=True).first()
        if alm is None:
            alm = cls.objects.create(codigo='ALM01', nombre='Almacén principal', es_principal=True)
        return alm


class Ubicacion(models.Model):
    """Lugar dentro del almacén (zona, pasillo, rack, nivel): ej. A-01-03."""
    almacen = models.ForeignKey(Almacen, on_delete=models.CASCADE, related_name='ubicaciones')
    codigo = models.CharField('Código', max_length=20, help_text='Ej. A-01-03 (zona-rack-nivel)')
    descripcion = models.CharField('Descripción', max_length=100, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['almacen', 'codigo']
        unique_together = [('almacen', 'codigo')]
        verbose_name = 'ubicación'
        verbose_name_plural = 'ubicaciones'

    def __str__(self):
        return self.codigo


class StockAlmacen(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='stocks')
    almacen = models.ForeignKey(Almacen, on_delete=models.CASCADE, related_name='stocks')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    ubicacion = models.ForeignKey(Ubicacion, on_delete=models.SET_NULL, null=True, blank=True, related_name='stocks',
                                  verbose_name='Ubicación habitual')
    # límites del producto en este almacén (vista Almacén de SAP); vacío = los generales de la ficha del producto
    stock_minimo = models.DecimalField('Mínimo', max_digits=14, decimal_places=2, null=True, blank=True)
    punto_reorden = models.DecimalField('Punto de reorden', max_digits=14, decimal_places=2, null=True, blank=True)
    stock_maximo = models.DecimalField('Máximo', max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = [('producto', 'almacen')]

    @property
    def bajo_minimo(self):
        return self.stock_minimo is not None and self.cantidad < self.stock_minimo

    @staticmethod
    def ubicaciones(almacen_id, productos_ids):
        """{producto_id: código de ubicación} en el almacén."""
        return dict(StockAlmacen.objects.filter(almacen_id=almacen_id, producto_id__in=productos_ids)
                    .exclude(ubicacion=None).values_list('producto_id', 'ubicacion__codigo'))


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
    PROVEEDORES = [('NINGUNO', 'Sin facturación electrónica'), ('NUBEFACT', 'Nubefact (OSE/PSE)'),
                   ('SUNAT', 'SUNAT directo (sistema del contribuyente)')]
    ENTORNOS = [('BETA', 'Pruebas (beta de SUNAT, sin validez tributaria)'), ('PRODUCCION', 'Producción')]
    proveedor = models.CharField(max_length=10, choices=PROVEEDORES, default='NINGUNO')
    ruta = models.URLField('Ruta / URL de la API', max_length=300, blank=True,
                           help_text='La entrega el proveedor (ej. https://api.nubefact.com/api/v1/xxxx)')
    token = models.CharField(max_length=200, blank=True)
    envio_automatico = models.BooleanField('Enviar al emitir', default=False,
                                           help_text='Envía facturas, boletas, notas y guías al guardarlas')
    # ---- SUNAT directo: el sistema arma el XML UBL 2.1, lo firma con el certificado de la empresa y lo envía
    entorno = models.CharField('Entorno de SUNAT', max_length=10, choices=ENTORNOS, default='BETA')
    sol_usuario = models.CharField('Usuario SOL secundario', max_length=30, blank=True,
                                   help_text='Producción: usuario secundario con el perfil de envío de comprobantes '
                                             '(sin el RUC). En pruebas se usa MODDATOS')
    sol_clave = models.CharField(max_length=300, blank=True, editable=False)  # cifrada (core/cifrado.py)
    certificado = models.BinaryField(null=True, blank=True, editable=False)  # archivo .pfx / .p12
    certificado_clave = models.CharField(max_length=300, blank=True, editable=False)  # cifrada
    certificado_nombre = models.CharField(max_length=200, blank=True, editable=False)
    certificado_vence = models.DateField(null=True, blank=True, editable=False)
    certificado_prueba = models.BooleanField(default=False, editable=False)
    # ---- guías de remisión directo: API REST de SUNAT con el mismo usuario SOL secundario
    gre_client_id = models.CharField(
        'Guías: client_id', max_length=100, blank=True,
        help_text='Credenciales de API SUNAT generadas en SUNAT Operaciones en Línea con el usuario SOL secundario, '
                  'con el servicio «GRE Emisión de Comprobantes»')
    gre_client_secret = models.CharField(max_length=300, blank=True, editable=False)  # cifrado

    class Meta:
        verbose_name = 'configuración de facturación electrónica'

    @classmethod
    def actual(cls):
        return cls.objects.first() or cls.objects.create()

    @property
    def activa(self):
        if self.proveedor == 'SUNAT':
            return bool(self.certificado) and (self.entorno == 'BETA' or bool(self.sol_usuario and self.sol_clave))
        return self.proveedor != 'NINGUNO' and bool(self.ruta and self.token)


class EnvioResumenSUNAT(models.Model):
    """Comunicación de baja (RA) o resumen diario (RC) enviados directo a SUNAT: van con ticket y se consulta su
    estado después."""
    TIPOS = [('RA', 'Comunicación de baja'), ('RC', 'Resumen diario de boletas')]
    tipo = models.CharField(max_length=2, choices=TIPOS)
    identificador = models.CharField(max_length=30)  # RA-AAAAMMDD-n
    fecha = models.DateField()
    ticket = models.CharField(max_length=40, blank=True)
    estado = models.CharField(max_length=12, default='ENVIADO')  # ENVIADO, ACEPTADO, RECHAZADO, ERROR
    respuesta = models.TextField(blank=True)
    venta = models.ForeignKey('ventas.Venta', on_delete=models.CASCADE, related_name='envios_resumen')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado', '-id']


class ArchivoSustento(models.Model):
    """Documento de sustento (PDF, imagen, Excel). Se guarda en la base de datos: el disco de la nube se borra."""
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=100, blank=True)
    datos = models.BinaryField(blank=True)
    tamano = models.PositiveIntegerField(default=0)
    ubicacion = models.CharField('Clave en el almacenamiento de objetos', max_length=300, blank=True,
                                 help_text='Si está, el archivo vive en el almacenamiento de objetos (S3 compatible) '
                                           'y no en la base de datos')
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'archivo de sustento'
        verbose_name_plural = 'archivos de sustento'

    def __str__(self):
        return self.nombre

    @property
    def contenido(self):
        """Bytes del archivo, de la base de datos o del almacenamiento de objetos."""
        if self.ubicacion:
            from .almacen_objetos import leer
            return leer(self.ubicacion)
        return bytes(self.datos or b'')


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


class ReglaAprobacion(models.Model):
    """Motor único de aprobación (core/aprobaciones.py): quién aprueba cada nivel de un documento."""
    DOCUMENTOS = [('OC', 'Órdenes de compra'), ('SOLPED', 'Solicitudes de pedido'),
                  ('REQUERIMIENTO', 'Requerimientos de materiales'), ('RENDICION', 'Rendiciones de gastos'),
                  ('VACACION', 'Vacaciones y permisos'), ('MOVIMIENTO', 'Movimientos de personal'),
                  ('PEDIDO', 'Pedidos de venta (tarifa, descuentos y crédito)'),
                  ('HES', 'Conformidades de servicio (HES)')]
    TIPOS = [('JEFE', 'Jefe del solicitante'), ('RESPONSABLE_CC', 'Responsable del centro de costo'),
             ('USUARIO', 'Usuario específico'), ('PUESTO', 'Ocupante de un puesto'),
             ('JERARQUIA', 'Nivel N de la jerarquía (jefe del jefe…)')]
    APLICA = [('', 'Todos'), ('URGENTE', 'Solo urgentes'), ('ACTIVO', 'Activo fijo'), ('SERVICIO', 'Servicios'),
              ('MATERIAL', 'Materiales'), ('MERCADERIA', 'Mercadería'), ('GASTO', 'Gastos'),
              ('BAJO_TARIFA', 'Pedidos bajo la tarifa o con descuento'), ('EXCESO_CREDITO', 'Pedidos que exceden el '
                                                                                         'crédito del cliente')]
    documento = models.CharField(max_length=14, choices=DOCUMENTOS)
    nivel = models.PositiveSmallIntegerField(default=1, help_text='1 aprueba primero, luego 2, 3…')
    tipo = models.CharField('Quién aprueba', max_length=14, choices=TIPOS, default='JEFE')
    aprobador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='+', help_text='Solo para «Usuario específico»')
    puesto = models.ForeignKey('planillas.Puesto', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                               help_text='Solo para «Ocupante de un puesto»')
    niveles_arriba = models.PositiveSmallIntegerField('Niveles arriba', default=2,
                                                      help_text='Solo para «Nivel N de la jerarquía»: 2 = el jefe '
                                                                'del jefe')
    monto_desde = models.DecimalField('Desde el monto S/', max_digits=14, decimal_places=2, default=D0,
                                      help_text='0 = siempre')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+',
                                     help_text='Aplica a los documentos de este centro (o sus subcentros). Vacío = '
                                               'todos')
    reemplazo = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+', verbose_name='Aprobador de reemplazo',
                                  help_text='Si la regla no encuentra a quién (sin jefe, puesto vacante): aprueba este '
                                            'usuario. Vacío = se pasa al siguiente nivel')
    aplica_a = models.CharField('Tipo de documento', max_length=16, choices=APLICA, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['documento', 'nivel', 'monto_desde', 'id']
        verbose_name = 'regla de aprobación'
        verbose_name_plural = 'reglas de aprobación'

    def __str__(self):
        return f'{self.get_documento_display()} · nivel {self.nivel}: {self.get_tipo_display()}'


class Delegacion(models.Model):
    """Suplencia: mientras dure, el suplente aprueba lo que le toca al titular (vacaciones, viajes)."""
    titular = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='delegaciones')
    suplente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='suplencias')
    desde = models.DateField()
    hasta = models.DateField()
    motivo = models.CharField(max_length=150, blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-desde']
        verbose_name = 'delegación de aprobaciones'
        verbose_name_plural = 'delegaciones de aprobaciones'

    def __str__(self):
        return f'{self.suplente} por {self.titular} ({self.desde:%d/%m/%Y} – {self.hasta:%d/%m/%Y})'


class HistorialAprobacion(models.Model):
    """Rastro de cada aprobación, rechazo, observación o escalamiento de los documentos del motor único."""
    documento = models.CharField(max_length=14)
    objeto_id = models.PositiveIntegerField()
    descripcion = models.CharField(max_length=120, blank=True)
    accion = models.CharField(max_length=20)
    nivel = models.PositiveSmallIntegerField(null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    comentario = models.CharField(max_length=300, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']
        indexes = [models.Index(fields=['documento', 'objeto_id'])]


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
    # rendición de gastos: quién es el usuario como trabajador (sus entregas a rendir) y quién es su jefe directo
    trabajador = models.ForeignKey('Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   verbose_name='Rinde gastos como',
                                   help_text='Trabajador (responsable de entregas a rendir) que es este usuario')
    jefe = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='subordinados', verbose_name='Jefe directo',
                             help_text='Aprueba sus rendiciones de gastos (si así lo indican las reglas)')
    celular = models.CharField(max_length=20, blank=True,
                               help_text='Con código de país (51...). Identifica al usuario cuando envía gastos por '
                                         'WhatsApp')
    # ---- permisos por pantalla, datos sensibles y filtros por dato
    perfil_acceso = models.ForeignKey('PerfilAcceso', on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='usuarios', verbose_name='Perfil de acceso aplicado')
    pantallas = models.JSONField(default=dict, blank=True,
                                 help_text='{ruta: OCULTA | LECTURA | SIN_EXPORTAR}; sin entrada = acceso completo')
    ver_sueldos = models.BooleanField('Ve sueldos y boletas', default=True)
    ver_saldos = models.BooleanField('Ve saldos de caja y bancos', default=True)
    centros = models.ManyToManyField('contabilidad.CentroCosto', blank=True, related_name='+',
                                     verbose_name='Solo estos centros de costo',
                                     help_text='Trabajadores, planillas y solicitudes de estos centros. Vacío = todos')
    cuentas = models.ManyToManyField('finanzas.Cuenta', blank=True, related_name='+',
                                     verbose_name='Solo estas cuentas de caja y bancos', help_text='Vacío = todas')
    vendedor = models.CharField('Solo las ventas del vendedor', max_length=80, blank=True,
                                help_text='Comprobantes y pedidos de este vendedor. Vacío = todos')
    clave_cambiada_en = models.DateTimeField('Contraseña cambiada el', null=True, blank=True, editable=False)

    class Meta:
        verbose_name = 'perfil de usuario'
        verbose_name_plural = 'perfiles de usuario'

    def __str__(self):
        return f'Permisos de {self.usuario}'


class FiltroGuardado(models.Model):
    """Filtros de un reporte guardados por el usuario (se vuelven a aplicar con un clic)."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='filtros_guardados')
    reporte = models.CharField(max_length=60)
    nombre = models.CharField(max_length=60)
    parametros = models.CharField(max_length=1000)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['reporte', 'nombre']
        unique_together = [('usuario', 'reporte', 'nombre')]


class CuentasTipoProducto(models.Model):
    """Cuentas por tipo de producto (clase de valoración, como OBYC de SAP): el producto las hereda al crearse y solo
    se cambian en su ficha por excepción. Lo vacío toma el juego por defecto del PCGE."""
    clase = models.CharField('Tipo de producto', max_length=20, choices=CLASES_PRODUCTO, unique=True)
    cuenta_existencias = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                           blank=True, related_name='+', verbose_name='Existencias (20-28)')
    cuenta_compra = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='+', verbose_name='Compra (60)')
    cuenta_variacion = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                         blank=True, related_name='+', verbose_name='Variación / consumo a producción (61)')
    cuenta_venta = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', verbose_name='Venta (70)')
    cuenta_costo = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', verbose_name='Costo de ventas (69)')
    cuenta_produccion = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                          blank=True, related_name='+', verbose_name='Producción almacenada (71)')
    cuenta_diferencia_precio = models.ForeignKey(
        'contabilidad.CuentaContable', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name='Diferencia de precio de compra',
        help_text='Vacío = la diferencia se reparte entre el inventario y el costo de ventas')

    class Meta:
        ordering = ['clase']
        verbose_name = 'cuentas por tipo de producto'
        verbose_name_plural = 'cuentas por tipo de producto'

    def __str__(self):
        return self.get_clase_display()


class DocumentoCliente(models.Model):
    """Documento que el cliente del estudio contable sube para su contabilidad (comprobantes, estados de cuenta…);
    el contador lo marca como procesado u observado."""
    TIPOS = [('COMPRA', 'Comprobantes de compra'), ('VENTA', 'Comprobantes de venta'),
             ('BANCO', 'Estado de cuenta bancario'), ('PLANILLA', 'Documentos de personal'), ('OTRO', 'Otro')]
    ESTADOS = [('RECIBIDO', 'Recibido'), ('PROCESADO', 'Procesado'), ('OBSERVADO', 'Observado')]
    tipo = models.CharField(max_length=10, choices=TIPOS, default='COMPRA')
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6)
    descripcion = models.CharField('Descripción', max_length=200, blank=True)
    archivo = models.ForeignKey('ArchivoSustento', on_delete=models.PROTECT, related_name='+')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='RECIBIDO')
    comentario = models.CharField('Comentario del contador', max_length=300, blank=True)
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado', '-id']
        verbose_name = 'documento del cliente'
        verbose_name_plural = 'documentos del cliente'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.periodo[4:]}/{self.periodo[:4]}'


class PerfilAcceso(models.Model):
    """Perfil reutilizable (rol): módulos, acciones, pantallas, datos sensibles y límite de aprobación que se
    aplican de una vez a varios usuarios (ej. «Asistente de compras», «Jefe de almacén»)."""
    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.CharField('Descripción', max_length=200, blank=True)
    modulos = models.JSONField(default=list, blank=True)
    acciones = models.JSONField(default=list, blank=True)
    pantallas = models.JSONField(default=dict, blank=True)
    ver_costos = models.BooleanField('Ve costos de inventario', default=False)
    ver_sueldos = models.BooleanField('Ve sueldos y boletas', default=True)
    ver_saldos = models.BooleanField('Ve saldos de caja y bancos', default=True)
    limite_aprobacion = models.DecimalField('Aprueba órdenes de compra hasta S/', max_digits=14, decimal_places=2,
                                            default=D0)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'perfil de acceso'
        verbose_name_plural = 'perfiles de acceso'

    def __str__(self):
        return self.nombre


class PropuestaSIRE(models.Model):
    """Propuesta del registro de ventas (RVIE) o de compras (RCE) que SUNAT arma en el SIRE para un periodo:
    traída por la API (ticket → archivo) o subida a mano. Guarda el archivo como sustento y los comprobantes leídos
    para cruzarlos con lo registrado y crear lo que falte."""
    LIBROS = [('RVIE', 'Registro de ventas (RVIE)'), ('RCE', 'Registro de compras (RCE)')]
    ESTADOS = [('SOLICITADA', 'Solicitada a SUNAT'), ('LISTA', 'Lista'), ('ERROR', 'Error')]
    ORIGENES = [('API', 'API SUNAT'), ('ARCHIVO', 'Archivo subido')]
    libro = models.CharField(max_length=4, choices=LIBROS)
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6)
    origen = models.CharField(max_length=8, choices=ORIGENES, default='API')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='SOLICITADA')
    ticket = models.CharField(max_length=20, blank=True)
    mensaje = models.TextField(blank=True)
    archivo = models.BinaryField(null=True, blank=True, editable=False)
    nombre_archivo = models.CharField(max_length=200, blank=True)
    registros = models.JSONField(default=list, blank=True)
    creados = models.PositiveIntegerField('Comprobantes creados desde la propuesta', default=0)
    ticket_aceptacion = models.CharField(max_length=20, blank=True)
    aceptada_en = models.DateTimeField(null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado', '-id']
        verbose_name = 'propuesta SIRE'
        verbose_name_plural = 'propuestas SIRE'

    def __str__(self):
        return f'{self.libro} {self.periodo[4:]}/{self.periodo[:4]}'


class EmpresaGrupo(models.Model):
    """Registro de las empresas del grupo creadas desde el sistema (vive siempre en la base principal). Cada una tiene
    su propia base de datos en el mismo servidor; al arrancar y cada minuto el sistema las agrega a sus conexiones."""
    ESTADOS = [('CREANDO', 'Creando'), ('LISTA', 'Lista'), ('ERROR', 'Error al crear'), ('INACTIVA', 'Inactiva')]
    alias = models.SlugField(max_length=30, unique=True)
    base = models.CharField('Base de datos', max_length=63, unique=True)
    razon_social = models.CharField('Razón social', max_length=200)
    ruc = models.CharField('RUC', max_length=11)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='CREANDO')
    mensaje = models.TextField(blank=True)
    creado_por = models.CharField(max_length=150, blank=True)
    # opciones de creación: dirección y ubigeo (del padrón RUC) y empresa modelo de la que se copian series y
    # parámetros {'direccion', 'ubigeo', 'modelo'}
    datos = models.JSONField(default=dict, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado']
        verbose_name = 'empresa del grupo'
        verbose_name_plural = 'empresas del grupo'

    def __str__(self):
        return self.razon_social


class UsuarioCorporativo(models.Model):
    """Usuario de la empresa principal que maneja varias empresas del grupo con un solo ingreso: cambia de empresa
    sin volver a iniciar sesión y, si se le permite, crea empresas nuevas. (Vive en la base principal.)
    Los administradores de la empresa principal son corporativos de todas las empresas sin necesidad de registro."""
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='corporativo')
    todas = models.BooleanField('Todas las empresas del grupo', default=True)
    empresas = models.JSONField(default=list, blank=True, help_text='Alias de las empresas si no son todas')
    puede_crear = models.BooleanField('Puede crear empresas', default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'usuario corporativo'


class ChequeoServicio(models.Model):
    """Resultado de cada chequeo del servicio (cada 5 minutos con el comando chequear_servicio o el monitor externo
    que consulta /salud/): alimenta la página pública de estado y la disponibilidad del mes."""
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    ok = models.BooleanField()
    milisegundos = models.PositiveIntegerField(default=0)
    componentes = models.JSONField(default=dict)  # {componente: 'OPERATIVO' | 'DEGRADADO' | 'CAIDO'}

    class Meta:
        ordering = ['-fecha']


class ProveedorSSO(models.Model):
    """Inicio de sesión corporativo (OpenID Connect): Microsoft Entra ID (Office 365), Google Workspace u otro.
    El usuario entra con su cuenta de la empresa y se reconoce por su correo, que debe coincidir con el de un usuario
    activo del sistema (los módulos y permisos siguen siendo los del sistema)."""
    TIPOS = [('MICROSOFT', 'Microsoft (Office 365 / Entra ID)'), ('GOOGLE', 'Google Workspace'),
             ('OIDC', 'Otro (OpenID Connect)')]
    tipo = models.CharField(max_length=10, choices=TIPOS, default='MICROSOFT')
    nombre = models.CharField('Texto del botón', max_length=60, default='Ingresar con Microsoft')
    client_id = models.CharField('ID de la aplicación (client_id)', max_length=200)
    client_secret = models.CharField(max_length=500, blank=True, editable=False)  # cifrado
    tenant = models.CharField('Inquilino (tenant) de Microsoft', max_length=100, blank=True,
                              help_text='ID o dominio del directorio (ej. miempresa.onmicrosoft.com). Vacío = '
                                        '«organizations»')
    emisor = models.URLField('Emisor (issuer) OpenID', max_length=300, blank=True,
                             help_text='Solo para «Otro»: la URL base que publica /.well-known/openid-configuration')
    dominios = models.CharField('Dominios de correo permitidos', max_length=300, blank=True,
                                help_text='Separados por coma (ej. miempresa.com.pe). Vacío = cualquiera')
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'inicio de sesión corporativo'

    def __str__(self):
        return self.nombre

    @property
    def url_emisor(self):
        if self.tipo == 'GOOGLE':
            return 'https://accounts.google.com'
        if self.tipo == 'MICROSOFT':
            return f'https://login.microsoftonline.com/{self.tenant or "organizations"}/v2.0'
        return (self.emisor or '').rstrip('/')

    def dominio_permitido(self, correo):
        permitidos = [d.strip().lower().lstrip('@') for d in self.dominios.split(',') if d.strip()]
        return not permitidos or correo.lower().rsplit('@', 1)[-1] in permitidos


class HistorialClave(models.Model):
    """Contraseñas anteriores (solo su hash) para no repetir las últimas N."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    clave = models.CharField(max_length=200)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado', '-id']


class DispositivoConocido(models.Model):
    """Equipos desde los que el usuario ya ingresó: un ingreso desde uno nuevo se avisa por correo."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    huella = models.CharField(max_length=64, db_index=True)
    navegador = models.CharField(max_length=200, blank=True)
    ip = models.CharField(max_length=45, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    ultimo = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-ultimo']
        unique_together = [('usuario', 'huella')]


class Incidencia(models.Model):
    """Problema reportado por un usuario («Reportar problema») o error del sistema registrado solo (los errores
    iguales se agrupan por su firma y se cuentan). El administrador los revisa en Ajustes › Incidencias."""
    TIPOS = [('REPORTE', 'Reportado por un usuario'), ('ERROR', 'Error del sistema')]
    ESTADOS = [('ABIERTA', 'Abierta'), ('REVISION', 'En revisión'), ('RESUELTA', 'Resuelta'),
               ('DESCARTADA', 'Descartada')]
    tipo = models.CharField(max_length=8, choices=TIPOS, default='REPORTE')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ABIERTA')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    url = models.CharField('Página', max_length=500, blank=True)
    modulo = models.CharField('Módulo', max_length=40, blank=True)
    descripcion = models.TextField('Qué pasó')
    detalle = models.TextField('Detalle técnico', blank=True)
    navegador = models.CharField(max_length=300, blank=True)
    firma = models.CharField(max_length=40, blank=True, db_index=True, help_text='Agrupa los errores iguales')
    veces = models.PositiveIntegerField(default=1)
    ultima_vez = models.DateTimeField(default=timezone.now)
    avisado_en = models.DateTimeField(null=True, blank=True, editable=False)
    nota = models.TextField('Nota de la revisión', blank=True)
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ultima_vez', '-id']
        verbose_name = 'incidencia'

    def __str__(self):
        return f'Incidencia {self.pk}: {self.descripcion[:60]}'


class ControlIp(models.Model):
    """Restricción del ingreso por IP (una sola fila, en la base principal: vale para todas las empresas)."""
    activo = models.BooleanField('Solo las IP de la lista pueden usar el ERP', default=False)
    modificado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'control de ingreso por IP'

    @classmethod
    def actual(cls):
        return cls.objects.using('default').first() or cls(activo=False)


class IpPermitida(models.Model):
    """IP (o red, ej. 192.168.1.0/24) autorizada a usar el ERP cuando el control por IP está activo."""
    ip = models.CharField('IP o red', max_length=50, unique=True,
                          help_text='Una IP (190.12.34.56) o una red completa (192.168.1.0/24)')
    descripcion = models.CharField('Descripción', max_length=120, blank=True,
                                   help_text='Ej. Oficina Lima, casa del gerente, red interna')
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ip']
        verbose_name = 'IP permitida'
        verbose_name_plural = 'IP permitidas'

    def __str__(self):
        return self.ip


class AccesoIp(models.Model):
    """IP desde la que alguien abrió el ERP: cuándo, cuántas veces, qué pidió y si se le dejó entrar."""
    ip = models.CharField(max_length=45, unique=True)
    primera_vez = models.DateTimeField(auto_now_add=True)
    ultima_vez = models.DateTimeField()
    veces = models.PositiveIntegerField(default=0)
    bloqueos = models.PositiveIntegerField('Veces rechazada', default=0)
    ultima_ruta = models.CharField('Última página', max_length=300, blank=True)
    navegador = models.CharField(max_length=300, blank=True)
    usuario = models.CharField('Último usuario', max_length=150, blank=True)
    permitida = models.BooleanField('Entró', default=True)

    class Meta:
        ordering = ['-ultima_vez']
        verbose_name = 'acceso por IP'
        verbose_name_plural = 'accesos por IP'

    def __str__(self):
        return self.ip


class ApiToken(models.Model):
    """Clave de acceso a la API (/api/v1/) de un usuario: actúa con sus módulos y permisos. Solo se guarda el hash
    SHA-256; la clave completa se muestra una sola vez al crearla."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_tokens')
    nombre = models.CharField(max_length=80, help_text='Para qué se usa. Ej. Tienda online, Power BI')
    prefijo = models.CharField(max_length=12, editable=False)
    clave_hash = models.CharField(max_length=64, unique=True, editable=False)
    solo_lectura = models.BooleanField('Solo lectura', default=True, help_text='Desmarcado: también puede registrar')
    expira = models.DateField('Expira el', null=True, blank=True, help_text='Vacío = no expira')
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    ultimo_uso = models.DateTimeField('Último uso', null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'clave de API'
        verbose_name_plural = 'claves de API'

    def __str__(self):
        return f'{self.nombre} ({self.prefijo}…)'

    @staticmethod
    def hash_de(clave):
        import hashlib
        return hashlib.sha256(clave.encode()).hexdigest()

    @classmethod
    def crear(cls, usuario, nombre, solo_lectura=True, expira=None):
        """(token, clave en claro). La clave no se puede volver a ver."""
        import secrets
        clave = 'ceiba_' + secrets.token_urlsafe(32)
        token = cls.objects.create(usuario=usuario, nombre=nombre, prefijo=clave[:12], clave_hash=cls.hash_de(clave),
                                   solo_lectura=solo_lectura, expira=expira)
        return token, clave

    @classmethod
    def autenticar(cls, clave):
        token = cls.objects.select_related('usuario').filter(clave_hash=cls.hash_de(clave or ''), activo=True).first()
        if token is None or not token.usuario.is_active or (token.expira and token.expira < timezone.localdate()):
            return None
        return token


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
    lote = models.ForeignKey('Lote', on_delete=models.PROTECT, null=True, blank=True, related_name='kardex')
    # consumos: cuenta de gasto y centro de costo propios del movimiento (requerimientos internos)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    cuenta_contra = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                      blank=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']


class Lote(models.Model):
    """Lote o número de serie de un producto con control (trazabilidad y vencimiento)."""
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='lotes')
    codigo = models.CharField('Lote / N° de serie', max_length=60)
    vencimiento = models.DateField('Vence', null=True, blank=True)
    fabricacion = models.DateField('Fabricación', null=True, blank=True)
    # liberación por Calidad (certificado de análisis): en cuarentena o rechazado no se vende
    ESTADOS_CALIDAD = [('LIBRE', 'Sin control de calidad'), ('CUARENTENA', 'En cuarentena'),
                       ('LIBERADO', 'Liberado por Calidad'), ('RECHAZADO', 'Rechazado por Calidad'),
                       ('BLOQUEADO', 'Bloqueado')]
    BLOQUEADOS = ('CUARENTENA', 'RECHAZADO', 'BLOQUEADO')
    estado_calidad = models.CharField('Calidad', max_length=10, choices=ESTADOS_CALIDAD, default='LIBRE')
    motivo_estado = models.CharField('Motivo del estado', max_length=250, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['producto__nombre', 'vencimiento', 'codigo']
        unique_together = [('producto', 'codigo')]

    @property
    def bloqueado(self):
        return self.estado_calidad in self.BLOQUEADOS

    @property
    def estado_stock(self):
        """Libre, En calidad o Bloqueado (INV-26)."""
        from inventario.estados import estado_de_lote
        return {'LIBRE': 'Libre', 'CALIDAD': 'En calidad', 'BLOQUEADO': 'Bloqueado'}[estado_de_lote(self)]

    def __str__(self):
        return f'{self.codigo}' + (f' (vence {self.vencimiento:%d/%m/%Y})' if self.vencimiento else '')

    @property
    def stock(self):
        return self.stocks.aggregate(s=Sum('cantidad'))['s'] or D0

    @property
    def vencido(self):
        return bool(self.vencimiento and self.vencimiento < timezone.localdate())

    @property
    def dias_para_vencer(self):
        return (self.vencimiento - timezone.localdate()).days if self.vencimiento else None


class StockLote(models.Model):
    lote = models.ForeignKey(Lote, on_delete=models.CASCADE, related_name='stocks')
    almacen = models.ForeignKey(Almacen, on_delete=models.CASCADE, related_name='+')
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=D0)

    class Meta:
        unique_together = [('lote', 'almacen')]


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
    descuento_pct = models.DecimalField('Desc. %', max_digits=5, decimal_places=2, default=D0)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    afectacion = models.CharField('IGV', max_length=10, choices=AFECTACION_LINEA, blank=True, default='')

    class Meta:
        abstract = True
        # las líneas salen en el orden en que se registraron (comprobante, PDF, envío a SUNAT): PostgreSQL no lo
        # garantiza sin un orden explícito
        ordering = ['id']

    def save(self, *args, **kwargs):
        self.subtotal = r2(self.cantidad * self.precio_neto)
        super().save(*args, **kwargs)

    @property
    def precio_neto(self):
        """Valor unitario después del descuento de la línea (base del costo de compra)."""
        return self.precio_unitario * (1 - (self.descuento_pct or D0) / 100)

    @property
    def bruto(self):
        return r2(self.cantidad * self.precio_unitario)

    @property
    def descuento(self):
        """Importe del descuento de la línea (sin IGV)."""
        return self.bruto - self.subtotal

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
        aplicacion = apps.get_model('finanzas', 'Aplicacion')
        fk = self.model._meta.model_name
        movs = movimiento.objects.filter(**{fk: OuterRef('pk')}).values(fk)
        apls = aplicacion.objects.filter(**{fk: OuterRef('pk')}).values(fk)
        notas = self.model.objects.filter(doc_referencia=OuterRef('pk'), estado='REGISTRADO').values('doc_referencia')
        nc, nd = notas.filter(tipo_comprobante='07'), notas.filter(tipo_comprobante='08')
        return self.annotate(
            ann_pagado=_suma_sub(movs, 'monto_doc') + _suma_sub(apls, 'monto_doc'),
            ann_pagado_pen=_suma_sub(movs, 'monto_doc_pen') + _suma_sub(apls, 'monto_doc_pen'),
            ann_nc=_suma_sub(nc, 'total'), ann_nd=_suma_sub(nd, 'total'),
            ann_nc_pen=_suma_sub(nc, 'total_pen'), ann_nd_pen=_suma_sub(nd, 'total_pen'))

    def cobrables(self):
        """Sin el historial ya cancelado del sistema anterior (nunca tiene saldo: no hace falta recorrerlo)."""
        return self.exclude(es_historico=True, es_saldo_inicial=False)

    def de_gestion(self):
        """Ventas/compras para reportes y análisis: las del sistema y el historial importado (sin los saldos
        iniciales de una sola línea, que no tienen detalle)."""
        return self.filter(Q(es_saldo_inicial=False) | Q(es_historico=True))


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
    # constancia del depósito de la detracción en el Banco de la Nación (compras: PLE 8.1, campos 32 y 33)
    detraccion_constancia = models.CharField('N° constancia de detracción', max_length=40, blank=True,
                                             help_text='Se llena con el N° de operación del depósito de detracción '
                                                       'registrado en Finanzas')
    detraccion_fecha = models.DateField('Fecha de la constancia', null=True, blank=True)
    glosa = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_COMPROBANTE, default='REGISTRADO')
    almacen = models.ForeignKey(Almacen, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Almacén')
    stock_aplicado = models.BooleanField('Movió almacén', default=False, editable=False)
    es_saldo_inicial = models.BooleanField(
        'Saldo inicial', default=False, editable=False,
        help_text='Documento pendiente de antes de usar el sistema: se cobra/paga, pero no va a los registros '
                  'de ventas/compras ni a SUNAT y se contabiliza contra la apertura (5911)')
    pagado_anterior = models.DecimalField(
        'Pagado en el sistema anterior', max_digits=14, decimal_places=2, default=D0, editable=False,
        help_text='Saldos iniciales: lo ya cobrado o pagado antes de usar el sistema (el saldo es el resto)')
    es_historico = models.BooleanField(
        'Histórico', default=False, editable=False,
        help_text='Importado del sistema anterior para consulta y reportes: no va al registro/PLE ni a SUNAT, no '
                  'mueve stock ni genera asientos, y no se edita. Si no es además saldo inicial, ya está cancelado')
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

    UMBRAL_DETRACCION = Decimal('700')  # monto mínimo de la operación sujeta al SPOT (S/)

    def proponer_detraccion(self):
        """Factura nueva sin % de detracción: toma el de sus productos (el más alto) si el total supera S/ 700.
        En ventas también el código del bien o servicio. Devuelve True si la aplicó."""
        if self.detraccion_pct or self.tipo_comprobante != '01' or \
                self.total * self.tc_efectivo <= self.UMBRAL_DETRACCION:
            return False
        sujetos = [i.producto for i in self.items.select_related('producto')
                   if i.producto_id and i.producto.detraccion_pct]
        if not sujetos:
            return False
        producto = max(sujetos, key=lambda p: p.detraccion_pct)
        self.detraccion_pct = producto.detraccion_pct
        if hasattr(self, 'detraccion_codigo') and producto.detraccion_codigo:
            self.detraccion_codigo = producto.detraccion_codigo
        self.calcular_totales()
        return True

    # ---- detracción: depósito y constancia
    def sincronizar_constancia(self, mov):
        """N° y fecha de la constancia desde el depósito de detracción (movimiento de Finanzas con concepto
        Detracción) del comprobante: se llenan si están vacíos; si ese depósito se anula, pasan al siguiente vigente
        o quedan vacíos. Lo registrado a mano no se reemplaza."""
        numero = (mov.numero_operacion or '').strip()[:40]
        if mov.estado == 'VIGENTE':
            if self.detraccion_constancia or not numero:
                return
            datos = {'detraccion_constancia': numero, 'detraccion_fecha': mov.fecha}
        elif numero and self.detraccion_constancia == numero:
            otro = (self.movimientos.filter(concepto='DETRACCION').exclude(pk=mov.pk).exclude(numero_operacion='')
                    .order_by('-fecha', '-pk').first())
            datos = {'detraccion_constancia': otro.numero_operacion.strip()[:40] if otro else '',
                     'detraccion_fecha': otro.fecha if otro else None}
        else:
            return
        type(self).objects.filter(pk=self.pk).update(**datos)
        for campo, valor in datos.items():
            setattr(self, campo, valor)

    @property
    def sujeto_a_detraccion(self):
        return (self.detraccion_monto > 0 and self.tipo_comprobante == '01' and self.estado == 'REGISTRADO'
                and not self.es_historico)

    def _cobro_sin_detraccion(self):
        """Fecha en que lo cobrado/pagado fuera de la detracción superó el neto sin ella (el cliente o nosotros
        pagamos el total sin depositar la detracción), o None."""
        tope = self.neto - self.detraccion_monto - self.pagado_anterior
        pagos = [(m.fecha, m.monto_doc) for m in self.movimientos.exclude(concepto='DETRACCION')]
        pagos += [(a.fecha, a.monto_doc) for a in self.aplicaciones.all()]
        acumulado = D0
        for fecha, monto in sorted(pagos, key=lambda p: p[0]):
            acumulado += monto
            if acumulado > tope + Decimal('0.01'):
                return fecha
        return None

    def _detraccion_depositada(self):
        """Depósitos de detracción registrados en Finanzas (moneda del documento)."""
        return self.movimientos.filter(concepto='DETRACCION').aggregate(s=Sum('monto_doc'))['s'] or D0

    @property
    def detraccion_estado(self):
        """(estado, fecha del cobro/pago total o None) de la detracción del comprobante:
        DEPOSITADA (tiene constancia), SIN_CONSTANCIA (depositada en Finanzas, falta el N° de constancia),
        SIN_DEPOSITO (se cobró/pagó el total sin depositar la detracción) o PENDIENTE (aún no se deposita).
        None si no está sujeto a detracción."""
        if not self.sujeto_a_detraccion:
            return None
        if self.detraccion_constancia:
            return 'DEPOSITADA', None
        if self._detraccion_depositada() >= self.detraccion_monto - Decimal('1'):  # depósito sin decimales
            return 'SIN_CONSTANCIA', None
        fecha = self._cobro_sin_detraccion()
        return ('SIN_DEPOSITO', fecha) if fecha else ('PENDIENTE', None)

    @property
    def detraccion_por_depositar(self):
        """Detracción aún no depositada (moneda del documento): no se paga al proveedor ni se cobra al cliente."""
        estado = self.detraccion_estado
        if not estado or estado[0] != 'PENDIENTE':
            return D0
        return max(self.detraccion_monto - self._detraccion_depositada(), D0)

    @property
    def detraccion_plazo(self):
        """Ventas cobradas por el total: el proveedor deposita la detracción hasta el 5.° día hábil siguiente al
        cobro (no considera feriados)."""
        estado = self.detraccion_estado
        if not estado or estado[0] != 'SIN_DEPOSITO':
            return None
        fecha, habiles = estado[1], 0
        while habiles < 5:
            fecha += timedelta(days=1)
            if fecha.weekday() < 5:
                habiles += 1
        return fecha

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

    def _cancelado(self, campo):
        """Cobrado/pagado con caja y bancos más lo cancelado sin caja (anticipos, canje por letras, rendiciones)."""
        return ((self.movimientos.aggregate(s=Sum(campo))['s'] or D0)
                + (self.aplicaciones.aggregate(s=Sum(campo))['s'] or D0))

    @property
    def pagado(self):
        return self._anotado('ann_pagado', lambda: self._cancelado('monto_doc'))

    @property
    def historico_cancelado(self):
        return self.es_historico and not self.es_saldo_inicial

    @property
    def es_nota_aplicada(self):
        return self.tipo_comprobante in ('07', '08') and bool(self.doc_referencia_id)

    @property
    def saldo(self):
        if self.estado == 'ANULADO' or self.es_nota_aplicada or self.historico_cancelado:
            return D0
        return self.neto - self.pagado - self.pagado_anterior

    @property
    def pagado_anterior_pen(self):
        return r2(self.pagado_anterior * self.tc_efectivo)

    @property
    def saldo_pen(self):
        """Saldo en soles con los mismos importes que usa la contabilidad (cuentas 12 y 42)."""
        if self.estado == 'ANULADO' or self.es_nota_aplicada or self.historico_cancelado:
            return D0
        pagado = self._anotado('ann_pagado_pen', lambda: self._cancelado('monto_doc_pen'))
        return (self.total_pen - self._anotado('ann_nc_pen', lambda: self._notas('07', 'total_pen'))
                + self._anotado('ann_nd_pen', lambda: self._notas('08', 'total_pen'))
                - self.ret_pen + self.perc_pen - pagado - self.pagado_anterior_pen)

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

    def _cantidad_stock(self, item):
        """Cantidad que mueve el almacén (las compras convierten de unidad de compra a unidad de almacén)."""
        return item.cantidad

    def aplicar_stock(self):
        if self.stock_aplicado or self.estado == 'ANULADO':
            return
        if not self.almacen_id:
            self.almacen = Almacen.principal()
        signo = self._signo_stock()
        # notas de crédito: la mercadería vuelve a (o sale de) los lotes del comprobante que modifican
        ref = str(self.doc_referencia) if getattr(self, 'doc_referencia_id', None) else None
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                costo = self._costo_entrada(item) if signo > 0 else None
                item.producto.mover_stock(signo * self._cantidad_stock(item), str(self), costo=costo,
                                          fecha=self.fecha_emision,
                                          almacen=self.almacen, origen=self._meta.model_name.upper(), lotes_de=ref)
        self.stock_aplicado = True
        self.save(update_fields=['stock_aplicado', 'almacen'])

    def _costos_del_movimiento(self, tipo):
        """{producto_id: costo unitario} con que el documento movió el kardex (promedio ponderado de sus líneas)."""
        totales = {}
        for producto, cantidad, costo in (Kardex.objects.filter(referencia=str(self), tipo=tipo)
                                          .values_list('producto_id', 'cantidad', 'costo_unitario')):
            c, v = totales.get(producto, (D0, D0))
            totales[producto] = (c + cantidad, v + cantidad * (costo or D0))
        return {p: (v / c).quantize(Decimal('0.0001')) for p, (c, v) in totales.items() if c}

    def revertir_stock(self):
        if not self.stock_aplicado:
            return
        signo = -self._signo_stock()
        costos = self._costos_del_movimiento('ENTRADA' if signo < 0 else 'SALIDA')
        for item in self.items.select_related('producto'):
            if item.producto and item.producto.es_inventariable:
                # con la fecha del documento: el costo del periodo queda neto y coincide con la contabilidad; y al
                # costo con que se movió (no al promedio del momento), así el valor del inventario no cambia (INV-16)
                item.producto.mover_stock(signo * self._cantidad_stock(item), f'Reversión {self}',
                                          costo=costos.get(item.producto_id),
                                          fecha=self.fecha_emision,
                                          almacen=self.almacen, origen=self._meta.model_name.upper(),
                                          lotes_de=str(self))
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


class ParametroTributario(models.Model):
    """Valor de un parámetro legal o de política (UIT, tasas, umbrales, porcentajes) desde una fecha: cada operación
    usa el vigente a su fecha y queda la historia. Los valores por defecto y su norma están en core.parametros."""
    clave = models.CharField(max_length=40)
    valor = models.DecimalField(max_digits=16, decimal_places=4)
    desde = models.DateField('Vigente desde')
    nota = models.CharField('Norma o sustento', max_length=200, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['clave', '-desde']
        unique_together = [('clave', 'desde')]
        verbose_name = 'parámetro tributario'
        verbose_name_plural = 'parámetros tributarios'

    def __str__(self):
        return f'{self.clave} = {self.valor} desde {self.desde:%d/%m/%Y}'


class ReglaValidacion(models.Model):
    """Registros de SST y de calidad que exigen la validación de un especialista: el registro validado y firmado
    (PDF o imagen) se adjunta antes de darlo por validado (core.validacion)."""
    tipo = models.CharField('Tipo de registro', max_length=40, unique=True)
    requiere = models.BooleanField('Requiere validación', default=True)
    especialista = models.CharField('Quién valida', max_length=120, blank=True,
                                    help_text='Ej. jefe o supervisor de SST, médico ocupacional, comité de SST, '
                                              'auditor')

    class Meta:
        ordering = ['tipo']
        verbose_name = 'regla de validación'
        verbose_name_plural = 'reglas de validación'

    def __str__(self):
        return self.tipo

class PreferenciaLista(models.Model):
    """Lo que cada usuario elige en una lista (LIS-01): columnas visibles y filtros guardados («Mis recepciones»)."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preferencias_lista')
    lista = models.CharField(max_length=60)
    columnas = models.JSONField(default=list, blank=True)
    filtros = models.JSONField(default=list, blank=True, help_text='[{nombre, query}]')

    class Meta:
        unique_together = [('usuario', 'lista')]
        verbose_name = 'preferencia de lista'
        verbose_name_plural = 'preferencias de listas'


class ConsultaRUC(models.Model):
    """Consulta al padrón RUC de SUNAT hecha desde el módulo Consulta RUC (v1.83): historial y datos para registrar
    al cliente o proveedor sin volver a consultar."""
    ruc = models.CharField('RUC', max_length=11, db_index=True)
    razon_social = models.CharField('Razón social', max_length=250, blank=True)
    estado = models.CharField(max_length=30, blank=True)
    condicion = models.CharField('Condición', max_length=30, blank=True)
    direccion = models.CharField('Dirección', max_length=300, blank=True)
    ubigeo = models.CharField(max_length=6, blank=True)
    datos = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=250, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        verbose_name = 'consulta RUC'
        verbose_name_plural = 'consultas RUC'

    def __str__(self):
        return f'{self.ruc} {self.razon_social}'.strip()

    @property
    def alerta(self):
        return (self.estado not in ('', 'ACTIVO')) or (self.condicion not in ('', 'HABIDO'))


class FirmaElectronica(models.Model):
    """Firma electrónica (v1.84, CAL-03): el usuario vuelve a escribir su contraseña al firmar; queda su nombre, la
    fecha y hora, el significado («Liberado por QA», «Revisado», «Aprobado») y el registro firmado (21 CFR 11 /
    Anexo 11 de las BPM)."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    nombre = models.CharField(max_length=150)
    significado = models.CharField(max_length=120)
    modelo = models.CharField(max_length=100)
    objeto_id = models.CharField(max_length=40)
    objeto = models.CharField(max_length=200)
    ip = models.CharField(max_length=45, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'firma electrónica'
        verbose_name_plural = 'firmas electrónicas'

    def __str__(self):
        return f'{self.nombre} · {self.significado} · {timezone.localtime(self.fecha):%d/%m/%Y %H:%M}'
