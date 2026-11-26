"""Operador logístico (Go Just In Time): solicitudes de recojo que llegan por los canales, guías del cliente que se
cargan con el QR, grupos consolidados por destino con la guía remitente de la empresa, el transporte tercero, la
llegada a destino con la guía del último tramo y la entrega con foto, DNI y firma.

Cada guía del cliente cambia de estado sola con el escaneo o la foto; el maestro de estados es configurable (nombre,
color, ícono, sigla y si es inicial) y cada cambio queda en la bitácora con quién, cuándo y el tiempo en el estado
anterior. El código del estado (ATENDIDO, CONFIRMADO…) es fijo: es lo que dispara cada cambio."""
import re
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

D0 = Decimal('0')


def solo_digitos(texto):
    return re.sub(r'\D', '', texto or '')


def clave_celular(texto):
    """Los celulares se comparan por sus últimos 9 dígitos (con o sin +51, espacios o guiones)."""
    return solo_digitos(texto)[-9:]


class ConfigOperador(models.Model):
    """Configuración del módulo (una por empresa)."""
    POR = [('DESTINATARIO', 'Una guía por destinatario y punto de llegada'), ('GUIA', 'Una guía por cada guía del cliente')]
    almacen = models.ForeignKey('core.Almacen', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                verbose_name='Almacén de la empresa (punto de partida de la consolidada)')
    serie_consolidada = models.CharField('Serie de la guía consolidada', max_length=4, default='T001')
    serie_ultimo_tramo = models.CharField('Serie de la guía del último tramo', max_length=4, default='T001')
    motivo_traslado = models.CharField('Motivo de traslado (catálogo 20)', max_length=2, default='13',
                                       help_text='Por confirmar con el contador: la empresa traslada mercadería de '
                                                 'terceros')
    descripcion_motivo = models.CharField('Descripción del motivo', max_length=100,
                                          default='Traslado de mercadería de terceros (operador logístico)')
    ultimo_tramo_por = models.CharField('Guía del último tramo', max_length=12, choices=POR, default='DESTINATARIO',
                                        help_text='Por confirmar con Go: una por destinatario o una por cliente')

    class Meta:
        verbose_name = verbose_name_plural = 'configuración del operador logístico'

    def __str__(self):
        return 'Configuración del operador logístico'

    @classmethod
    def actual(cls):
        return cls.objects.order_by('pk').first() or cls.objects.create()


class EstadoLogistico(models.Model):
    """Maestro de estados (los de applikalo, estado_recojo). El código no se edita: dispara el cambio automático."""
    # código -> (nombre, color, ícono, sigla, inicial, orden, cuándo cambia)
    BASE = {
        'ATENDIDO': ('Atendido', '#101d7e', 'bi-inbox', 'ini', True, 1, 'Inicial: la guía se registra (llega por '
                                                                       'correo con su XML)'),
        'CONFIRMADO': ('Confirmado', '#76bd04', 'bi-qr-code-scan', 'con', False, 2,
                       'El operador escanea el QR de la guía en el recojo'),
        'EN_CUSTODIA': ('En custodia', '#a61414', 'bi-building', 'cus', False, 3, 'El bulto entra al almacén de Go'),
        'EN_TRAYECTO': ('En trayecto', '#FFCE00', 'bi-truck', 'tra', False, 4,
                        'Se emite la guía consolidada y sale con el transporte'),
        'POR_ENTREGAR': ('Por entregar', '#d26000', 'bi-geo-alt', 'pen', False, 5,
                         'El operador de destino escanea el QR de la guía consolidada y toma la foto del recibo'),
        'EN_REPARTO': ('En reparto', '#0de00d', 'bi-signpost-split', 'rep', False, 6,
                       'Sale a reparto con la guía del último tramo hacia la ubicación del cliente'),
        'ENTREGADO': ('Entregado', '#0074a4', 'bi-check2-circle', 'ent', False, 7, 'Prueba de entrega con foto y DNI'),
        'ACTUALIZAR': ('Actualizar', '#FFCE00', 'bi-arrow-repeat', 'act', False, 8,
                       'Manual. Está dos veces en el maestro de applikalo: por confirmar con Go para qué se usa'),
        'ACTUALIZAR_2': ('Actualizar 2', '#9eb90c', 'bi-arrow-repeat', 'ac2', False, 9,
                         'Manual. Segunda fila «Actualizar» de applikalo: por confirmar con Go'),
    }
    codigo = models.CharField('Código', max_length=20, unique=True, editable=False)
    nombre = models.CharField(max_length=60)
    color = models.CharField(max_length=7, default='#6c757d', help_text='Hexadecimal, ej. #76bd04')
    icono = models.CharField('Ícono', max_length=40, blank=True, help_text='Bootstrap Icons, ej. bi-truck')
    sigla = models.CharField(max_length=5, blank=True)
    inicial = models.BooleanField('Es inicial', default=False)
    orden = models.PositiveSmallIntegerField(default=0)
    disparador = models.CharField('Cambia automáticamente cuando…', max_length=200, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['orden', 'id']
        verbose_name = 'estado logístico'
        verbose_name_plural = 'estados logísticos'

    def __str__(self):
        return self.nombre

    @classmethod
    def cargar_base(cls):
        """Crea los estados que falten (no cambia los que la empresa ya editó)."""
        for codigo, (nombre, color, icono, sigla, inicial, orden, disparador) in cls.BASE.items():
            cls.objects.get_or_create(codigo=codigo, defaults={
                'nombre': nombre, 'color': color, 'icono': icono, 'sigla': sigla, 'inicial': inicial,
                'orden': orden, 'disparador': disparador})

    @classmethod
    def de(cls, codigo):
        estado = cls.objects.filter(codigo=codigo).first()
        if estado is None:
            cls.cargar_base()
            estado = cls.objects.get(codigo=codigo)
        return estado

    @classmethod
    def el_inicial(cls):
        return cls.objects.filter(inicial=True, activo=True).order_by('orden').first() or cls.de('ATENDIDO')

    @property
    def texto_contraste(self):
        """Negro o blanco según la luminosidad del color de fondo."""
        try:
            r, g, b = (int(self.color[i:i + 2], 16) for i in (1, 3, 5))
        except (ValueError, IndexError):
            return '#fff'
        return '#000' if (r * 299 + g * 587 + b * 114) / 1000 > 150 else '#fff'


class ClienteLogistico(models.Model):
    """Lo que el servicio logístico necesita de la ficha del cliente."""
    tercero = models.OneToOneField('core.Tercero', on_delete=models.CASCADE, related_name='servicio_logistico')
    servicio_logistico = models.BooleanField('Cliente de servicio logístico', default=False,
                                             help_text='Sus mensajes crean solicitudes de recojo aunque no tenga '
                                                       'crédito (cliente con contrato)')
    almacen_direccion = models.CharField('Almacén de origen (dirección de recojo)', max_length=250, blank=True)
    almacen_ubigeo = models.CharField('Ubigeo del almacén', max_length=6, blank=True)
    almacen_referencia = models.CharField('Referencia', max_length=150, blank=True)
    contacto = models.CharField('Contacto en el almacén', max_length=120, blank=True)

    class Meta:
        verbose_name = 'cliente del servicio logístico'
        verbose_name_plural = 'clientes del servicio logístico'

    def __str__(self):
        return str(self.tercero)

    @property
    def tiene_credito(self):
        t = self.tercero
        return bool(t.dias_credito) or (t.limite_credito or D0) > 0

    @property
    def habilitado(self):
        """Solo los clientes con crédito o con la casilla «Cliente de servicio logístico» generan solicitudes."""
        return self.tercero.activo and (self.servicio_logistico or self.tiene_credito)

    @property
    def direccion_recojo(self):
        return self.almacen_direccion or self.tercero.direccion


class RemitenteAutorizado(models.Model):
    """Correo o celular autorizado para pedir recojos: el remitente del mensaje es la llave."""
    TIPOS = [('CORREO', 'Correo'), ('CELULAR', 'Celular (WhatsApp)')]
    cliente = models.ForeignKey(ClienteLogistico, on_delete=models.CASCADE, related_name='autorizados')
    tipo = models.CharField(max_length=7, choices=TIPOS)
    valor = models.CharField('Correo o celular', max_length=150)
    clave = models.CharField(max_length=150, editable=False, db_index=True)

    class Meta:
        ordering = ['tipo', 'valor']
        unique_together = [('tipo', 'clave')]
        verbose_name = 'remitente autorizado'
        verbose_name_plural = 'remitentes autorizados'

    def __str__(self):
        return self.valor

    @staticmethod
    def normalizar(tipo, valor):
        return (valor or '').strip().lower() if tipo == 'CORREO' else clave_celular(valor)

    def save(self, *args, **kwargs):
        self.valor = (self.valor or '').strip()
        self.clave = self.normalizar(self.tipo, self.valor)
        super().save(*args, **kwargs)


class SolicitudRecojo(models.Model):
    ESTADOS = [('ASIGNADO', 'Asignado'), ('ATENDIDO', 'Atendido'), ('ANULADA', 'Anulada')]
    CANALES = [('CORREO', 'Correo'), ('WHATSAPP', 'WhatsApp'), ('CEIBAGO', 'Ceiba GO (portal del cliente)'),
               ('MANUAL', 'Registro manual')]
    numero = models.CharField('N°', max_length=20, editable=False)
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='solicitudes_recojo')
    canal = models.CharField(max_length=8, choices=CANALES, default='MANUAL')
    remitente = models.CharField('Remitente (correo o celular)', max_length=150, blank=True)
    asunto = models.CharField(max_length=200, blank=True)
    mensaje = models.TextField(blank=True)
    almacen = models.CharField('Almacén de origen', max_length=250, blank=True)
    almacen_ubigeo = models.CharField(max_length=6, blank=True)
    bultos = models.PositiveIntegerField('Bultos indicados', null=True, blank=True)
    contacto = models.CharField(max_length=120, blank=True)
    fecha_recojo = models.DateField('Fecha de recojo pedida', null=True, blank=True)
    referencia_externa = models.CharField('Referencia en Ceiba GO', max_length=40, blank=True,
                                          help_text='Usuario o código con que el cliente la pidió en Ceiba GO')
    avisos = models.TextField('Avisos del canal', blank=True,
                              help_text='Documentos o imágenes que llegaron por WhatsApp (se ven en el teléfono)')
    estado = models.CharField(max_length=8, choices=ESTADOS, default='ASIGNADO')
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='recojos_asignados', verbose_name='Operador de recojo')
    atendida_en = models.DateTimeField(null=True, blank=True)
    atendida_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    creado = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-creado', '-id']
        verbose_name = 'solicitud de recojo'
        verbose_name_plural = 'solicitudes de recojo'

    def __str__(self):
        return self.numero or f'Solicitud {self.pk}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.numero:
            self.numero = f'SR-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(numero=self.numero)


class Consolidado(models.Model):
    """Grupo de guías recogidas con el mismo destino (departamento y provincia): viaja con una guía remitente de la
    empresa que lleva todas las guías del grupo como documentos relacionados."""
    ESTADOS = [('ARMANDO', 'Agrupando'), ('CONFIRMADO', 'Confirmado por logística'), ('EN_TRAYECTO', 'En trayecto'),
               ('EN_DESTINO', 'Recibido en destino'), ('CERRADO', 'Cerrado'), ('ANULADO', 'Anulado')]
    DOCS_TRANSPORTISTA = [('31', 'Guía de remisión transportista'), ('RECIBO', 'Recibo / ticket de la agencia')]
    numero = models.CharField('N°', max_length=20, editable=False)
    departamento = models.CharField(max_length=60)
    provincia = models.CharField(max_length=60)
    estado = models.CharField(max_length=11, choices=ESTADOS, default='ARMANDO')
    token = models.CharField(max_length=16, editable=False, unique=True)
    guia = models.OneToOneField('logistica.GuiaRemision', on_delete=models.PROTECT, null=True, blank=True,
                                related_name='consolidado', verbose_name='Guía remitente consolidada')
    agencia_destino = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='+', verbose_name='Agencia de destino')
    llegada_ubigeo = models.CharField('Ubigeo de llegada (agencia)', max_length=6, blank=True)
    llegada_direccion = models.CharField('Dirección de la agencia de destino', max_length=250, blank=True)
    # transporte tercero (bloque 5): su guía transportista o su recibo, por QR o foto
    transportista = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='+', verbose_name='Transportista (proveedor)')
    doc_transportista_tipo = models.CharField('Documento del transportista', max_length=6, choices=DOCS_TRANSPORTISTA,
                                              blank=True)
    doc_transportista_numero = models.CharField('Serie-número', max_length=30, blank=True)
    doc_transportista_fecha = models.DateField('Fecha', null=True, blank=True)
    doc_transportista_peso = models.DecimalField('Peso (kg)', max_digits=12, decimal_places=2, null=True, blank=True)
    doc_transportista_bultos = models.PositiveIntegerField('Bultos', null=True, blank=True)
    doc_transportista_qr = models.TextField('Texto del QR', blank=True)
    # llegada a destino (bloque 6)
    confirmado_en = models.DateTimeField(null=True, blank=True)
    confirmado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    recibido_en = models.DateTimeField(null=True, blank=True)
    recibido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    hoja_reparto = models.ForeignKey('distribucion.HojaReparto', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado', '-id']
        verbose_name = 'consolidado'
        verbose_name_plural = 'consolidados'

    def __str__(self):
        return f'{self.numero} · {self.destino}'

    @property
    def destino(self):
        return f'{self.provincia} ({self.departamento})' if self.provincia != self.departamento else self.departamento

    @property
    def qr_texto(self):
        """Lo que va en el QR de la hoja de despacho: el operador de destino lo escanea sin digitar."""
        return f'CEIBA-OL|CONSOLIDADO|{self.numero}|{self.token}'

    @property
    def abierto(self):
        return self.estado in ('ARMANDO', 'CONFIRMADO')

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_hex(8).upper()
        super().save(*args, **kwargs)
        if not self.numero:
            self.numero = f'CL-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(numero=self.numero)


class GuiaCliente(models.Model):
    """Guía remitente que emitió el cliente (su mercadería): se carga sin digitar, del XML que llega por correo o del
    QR en el recojo, y es la unidad que cambia de estado hasta la entrega."""
    ORIGENES = [('XML', 'XML del correo'), ('QR', 'Escaneo del QR'), ('LISTA', 'Elegida de la lista (QR ilegible)'),
                ('MANUAL', 'Registro manual')]
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='guias_logisticas')
    solicitud = models.ForeignKey(SolicitudRecojo, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='guias')
    emisor_ruc = models.CharField('RUC del emisor', max_length=11)
    emisor_nombre = models.CharField('Remitente', max_length=200, blank=True)
    tipo = models.CharField(max_length=2, default='09')
    serie = models.CharField(max_length=4)
    numero = models.CharField('Número', max_length=10)
    fecha_emision = models.DateField(null=True, blank=True)
    fecha_traslado = models.DateField(null=True, blank=True)
    motivo_traslado = models.CharField(max_length=2, blank=True)
    destinatario_tipo_doc = models.CharField(max_length=1, blank=True)
    destinatario_doc = models.CharField('Doc. del destinatario', max_length=15, blank=True)
    destinatario_nombre = models.CharField('Destinatario', max_length=200, blank=True)
    partida_ubigeo = models.CharField(max_length=6, blank=True)
    partida_direccion = models.CharField('Punto de partida', max_length=250, blank=True)
    llegada_ubigeo = models.CharField(max_length=6, blank=True)
    llegada_direccion = models.CharField('Punto de llegada', max_length=250, blank=True)
    departamento = models.CharField('Departamento de destino', max_length=60, blank=True)
    provincia = models.CharField('Provincia de destino', max_length=60, blank=True)
    bultos = models.PositiveIntegerField(default=0)
    peso = models.DecimalField('Peso bruto', max_digits=12, decimal_places=3, default=D0)
    unidad_peso = models.CharField(max_length=3, default='KGM')
    items = models.JSONField('Ítems', default=list, blank=True)
    origen_datos = models.CharField('Cargada desde', max_length=6, choices=ORIGENES, default='QR')
    qr_texto = models.TextField('Texto del QR', blank=True)
    datos_completos = models.BooleanField(default=True,
                                          help_text='No: el QR no trajo los datos (falta el XML o la lectura de SUNAT)')
    estado = models.ForeignKey(EstadoLogistico, on_delete=models.PROTECT, related_name='+')
    estado_desde = models.DateTimeField(default=timezone.now)
    consolidado = models.ForeignKey(Consolidado, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='guias')
    ultimo_tramo = models.ForeignKey('logistica.GuiaRemision', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='guias_cliente', verbose_name='Guía del último tramo')
    recogida_en = models.DateTimeField(null=True, blank=True)
    recogida_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    recojo_latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    recojo_longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # entrega final (bloque 7)
    entregado_en = models.DateTimeField(null=True, blank=True)
    entregado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    receptor_nombre = models.CharField('Recibió', max_length=120, blank=True)
    receptor_dni = models.CharField('DNI de quien recibe', max_length=15, blank=True)
    firma = models.TextField('Firma en pantalla', blank=True, help_text='Imagen PNG en base64')
    entrega_latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    entrega_longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado', '-id']
        unique_together = [('emisor_ruc', 'tipo', 'serie', 'numero')]
        verbose_name = 'guía del cliente'
        verbose_name_plural = 'guías de los clientes'

    def __str__(self):
        return f'{self.serie}-{self.numero} · {self.emisor_nombre or self.emisor_ruc}'

    @property
    def numero_completo(self):
        return f'{self.serie}-{self.numero}'

    @property
    def destino(self):
        if not self.departamento:
            return ''
        return f'{self.provincia} ({self.departamento})' if self.provincia and self.provincia != self.departamento \
            else self.departamento

    def poner_destino(self):
        """Departamento y provincia de destino desde el ubigeo de llegada (catálogo INEI)."""
        from core import ubigeo
        fila = ubigeo.tabla().get(self.llegada_ubigeo or '')
        if fila:
            self.departamento, self.provincia = fila[0].upper(), fila[1].upper()


class BitacoraEstado(models.Model):
    """Cada cambio de estado (de la guía del cliente o de la solicitud): quién, cuándo y cuánto estuvo en el anterior
    (como el wf_recojo de applikalo)."""
    ORIGENES = [('ESCANEO', 'Escaneo del QR'), ('FOTO', 'Foto'), ('LISTA', 'Elegida de la lista'),
                ('PANTALLA', 'Acción en pantalla'), ('SISTEMA', 'Automático'), ('CANAL', 'Mensaje del cliente')]
    guia = models.ForeignKey(GuiaCliente, on_delete=models.CASCADE, null=True, blank=True, related_name='bitacora')
    solicitud = models.ForeignKey(SolicitudRecojo, on_delete=models.CASCADE, null=True, blank=True,
                                  related_name='bitacora')
    de = models.CharField('Estado anterior', max_length=60, blank=True)
    a = models.CharField('Estado nuevo', max_length=60)
    estado = models.ForeignKey(EstadoLogistico, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    fecha = models.DateTimeField(default=timezone.now)
    segundos_anterior = models.PositiveBigIntegerField('Tiempo en el estado anterior (s)', null=True, blank=True)
    origen = models.CharField(max_length=8, choices=ORIGENES, default='SISTEMA')
    detalle = models.CharField(max_length=250, blank=True)
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ['fecha', 'id']
        verbose_name = 'cambio de estado'
        verbose_name_plural = 'bitácora de estados'

    def __str__(self):
        return f'{self.de or "—"} → {self.a}'

    @property
    def tiempo_anterior(self):
        """'2 d 3 h', '45 min'… o '' si es el primero."""
        s = self.segundos_anterior
        if s is None:
            return ''
        d, resto = divmod(s, 86400)
        h, resto = divmod(resto, 3600)
        m = resto // 60
        if d:
            return f'{d} d {h} h'
        if h:
            return f'{h} h {m} min'
        return f'{m} min' if m else 'menos de 1 min'


class RegistroCeibaGO(models.Model):
    """Lo que Ceiba GO manda a su cola de sincronización (venta, guía, CxC, almacén, costo, cobranza, baja, cierre).
    Se guarda tal cual llega para revisarlo; todavía no genera asientos ni comprobantes en el ERP."""
    TIPOS = [('venta', 'Venta (comprobante)'), ('gre', 'Guía de remisión'), ('cxc', 'Cuenta por cobrar'),
             ('almacen', 'Movimiento de almacén'), ('costo', 'Centro de costo del flete'), ('cobranza', 'Cobranza'),
             ('baja', 'Anulación / baja'), ('cierre', 'Conciliación de cierre')]
    tipo = models.CharField(max_length=9, choices=TIPOS)
    pedido = models.CharField('Pedido de Ceiba GO', max_length=40, blank=True, db_index=True)
    payload = models.JSONField('Datos recibidos')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', verbose_name='Clave de API de')
    recibido = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recibido', '-id']
        verbose_name = 'registro recibido de Ceiba GO'
        verbose_name_plural = 'registros recibidos de Ceiba GO'

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.pedido or "sin pedido"}'
