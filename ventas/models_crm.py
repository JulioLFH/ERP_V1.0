"""CRM ampliado (v1.81): configuración, contactos del cliente, cartera, equipos y metas, campañas, postventa,
consentimiento de datos personales, contratos con renovación e historial de etapas y reasignaciones."""
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

D0 = Decimal('0')
USUARIO = settings.AUTH_USER_MODEL


def _token():
    return secrets.token_urlsafe(18)


class ConfigCRM(models.Model):
    """Configuración del CRM de la empresa (una por empresa). Los números (días, probabilidades, montos) son
    parámetros con vigencia: Ajustes › Parámetros tributarios, grupo CRM."""
    ASIGNACIONES = [('RESPONSABLE', 'A un responsable fijo'), ('ROTATIVO', 'Rotativo entre los miembros del equipo')]
    token_web = models.CharField('Clave del formulario web', max_length=40, default=_token, editable=False)
    web_activo = models.BooleanField('Formulario web activo', default=False)
    asignacion_web = models.CharField('Asignar los prospectos de la web', max_length=11, choices=ASIGNACIONES,
                                      default='RESPONSABLE')
    responsable_web = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                        verbose_name='Responsable de los prospectos de la web')
    equipo_web = models.ForeignKey('ventas.EquipoVenta', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Equipo que atiende la web (rotativo)')
    ultimo_asignado = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                        editable=False)
    avisar_ganadas = models.ManyToManyField(USUARIO, blank=True, related_name='+',
                                            verbose_name='Avisar las oportunidades ganadas a (operaciones)',
                                            help_text='Responsables de obra, producción o logística')
    nombres_etapas = models.JSONField('Nombres de las etapas', default=dict, blank=True,
                                      help_text='Opcional: {"PROSPECTO": "Contacto inicial", ...}')
    finalidad_datos = models.CharField(
        'Finalidad del tratamiento de datos', max_length=250,
        default='Atender su consulta, enviarle propuestas comerciales y mantener la relación comercial')
    # buzón comercial (ventas@…): cada correo nuevo es una oportunidad (CRM-09)
    correo_activo = models.BooleanField('Leer el buzón comercial', default=False)
    correo_servidor = models.CharField('Servidor IMAP', max_length=120, blank=True, help_text='Ej. imap.gmail.com')
    correo_puerto = models.PositiveIntegerField('Puerto IMAP', default=993)
    correo_usuario = models.CharField('Usuario IMAP (ventas@…)', max_length=120, blank=True)
    correo_clave = models.CharField('Contraseña IMAP', max_length=200, blank=True)

    class Meta:
        verbose_name = 'configuración del CRM'

    @classmethod
    def actual(cls):
        return cls.objects.order_by('pk').first() or cls.objects.create()


class EquipoVenta(models.Model):
    """Equipo de ventas con su jefe (ve y reasigna las oportunidades del equipo: CRM-15)."""
    nombre = models.CharField(max_length=80)
    jefe = models.ForeignKey(USUARIO, on_delete=models.PROTECT, related_name='equipos_jefe')
    miembros = models.ManyToManyField(USUARIO, blank=True, related_name='equipos_venta')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'equipo de ventas'
        verbose_name_plural = 'equipos de ventas'

    def __str__(self):
        return self.nombre


class MetaVenta(models.Model):
    """Meta mensual por vendedor o por equipo (CRM-08)."""
    periodo = models.CharField('Mes (AAAAMM)', max_length=6)
    vendedor = models.ForeignKey(USUARIO, on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    equipo = models.ForeignKey(EquipoVenta, on_delete=models.CASCADE, null=True, blank=True, related_name='metas')
    monto = models.DecimalField('Meta S/ (sin IGV)', max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['-periodo']
        verbose_name = 'meta de ventas'
        verbose_name_plural = 'metas de ventas'


class Campana(models.Model):
    """Campaña o canal de captación con su costo (CRM-18)."""
    CANALES = [('FERIA', 'Feria / evento'), ('ANUNCIOS', 'Anuncios en internet'), ('REDES', 'Redes sociales'),
               ('VOLANTEO', 'Volanteo'), ('CORREO', 'Correo masivo'), ('REFERIDOS', 'Programa de referidos'),
               ('RADIO_TV', 'Radio / TV / prensa'), ('OTRO', 'Otro')]
    nombre = models.CharField(max_length=120)
    canal = models.CharField(max_length=10, choices=CANALES, default='OTRO')
    inicio = models.DateField(default=timezone.localdate)
    fin = models.DateField(null=True, blank=True)
    costo = models.DecimalField('Costo S/', max_digits=14, decimal_places=2, default=D0)
    notas = models.CharField(max_length=250, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['-inicio', 'nombre']
        verbose_name = 'campaña'

    def __str__(self):
        return self.nombre


class ContactoCliente(models.Model):
    """Persona del cliente con su cargo y rol en la compra (CRM-06)."""
    ROLES = [('DECIDE', 'Decide la compra'), ('TECNICO', 'Técnico / usuario'), ('COMPRAS', 'Compras'),
             ('PAGOS', 'Pagos / tesorería'), ('GERENCIA', 'Gerencia'), ('OTRO', 'Otro')]
    tercero = models.ForeignKey('core.Tercero', on_delete=models.CASCADE, related_name='contactos_crm',
                                verbose_name='Cliente')
    nombre = models.CharField(max_length=120)
    cargo = models.CharField(max_length=80, blank=True)
    rol = models.CharField(max_length=8, choices=ROLES, default='OTRO')
    telefono = models.CharField('Teléfono / celular', max_length=40, blank=True)
    email = models.EmailField(blank=True)
    principal = models.BooleanField('Contacto principal', default=False)
    activo = models.BooleanField(default=True)
    notas = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['-principal', 'nombre']
        verbose_name = 'contacto del cliente'
        verbose_name_plural = 'contactos de clientes'

    def __str__(self):
        return f'{self.nombre} ({self.get_rol_display()})'


class CarteraCliente(models.Model):
    """Vendedor responsable del cliente (cartera): se reasigna al desactivar al vendedor (CRM-15)."""
    tercero = models.OneToOneField('core.Tercero', on_delete=models.CASCADE, related_name='cartera_crm')
    responsable = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = 'cartera del cliente'


class ConsentimientoDatos(models.Model):
    """Consentimiento para el tratamiento de datos personales (Ley 29733 y su reglamento): de un prospecto, un
    contacto o un cliente persona natural (CRM-20)."""
    CANALES = [('WEB', 'Formulario web'), ('VERBAL', 'Verbal (registrado por el vendedor)'),
               ('ESCRITO', 'Escrito / firmado'), ('CORREO', 'Correo'), ('WHATSAPP', 'WhatsApp'), ('OTRO', 'Otro')]
    ESTADOS = [('OTORGADO', 'Otorgado'), ('REVOCADO', 'Revocado (baja de comunicaciones)'),
               ('SUPRIMIDO', 'Datos suprimidos')]
    titular = models.CharField('Titular de los datos', max_length=150)
    documento = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    tercero = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    contacto = models.ForeignKey(ContactoCliente, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='consentimientos')
    oportunidad = models.ForeignKey('ventas.Oportunidad', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='consentimientos')
    canal = models.CharField(max_length=8, choices=CANALES, default='VERBAL')
    finalidad = models.CharField(max_length=250)
    fecha = models.DateTimeField(default=timezone.now)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='OTORGADO', editable=False)
    fecha_baja = models.DateTimeField(null=True, blank=True, editable=False)
    registrado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'consentimiento de datos personales'
        verbose_name_plural = 'consentimientos de datos personales'

    def __str__(self):
        return f'{self.titular} · {self.get_estado_display()}'


class CambioEtapa(models.Model):
    """Historial de etapas de la oportunidad (conversión por etapa y ciclo de venta: CRM-05)."""
    oportunidad = models.ForeignKey('ventas.Oportunidad', on_delete=models.CASCADE, related_name='cambios')
    de = models.CharField(max_length=12, blank=True)
    a = models.CharField(max_length=12)
    fecha = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['fecha', 'id']


class Reasignacion(models.Model):
    """Traspaso de la cartera de un vendedor a otro (al desactivarlo o por decisión del jefe): CRM-15."""
    de = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    a = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, related_name='+')
    oportunidades = models.PositiveIntegerField(default=0)
    actividades = models.PositiveIntegerField(default=0)
    clientes = models.PositiveIntegerField(default=0)
    motivo = models.CharField(max_length=200, blank=True)
    fecha = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'reasignación de cartera'
        verbose_name_plural = 'reasignaciones de cartera'


class CasoPostventa(models.Model):
    """Reclamo, garantía o consulta de un cliente después de la venta (CRM-17)."""
    TIPOS = [('RECLAMO', 'Reclamo'), ('GARANTIA_OBRA', 'Garantía de obra'),
             ('GARANTIA_PRODUCTO', 'Garantía de producto'), ('CONSULTA', 'Consulta'),
             ('ENTREGA_UNIDAD', 'Entrega de unidad: observaciones'), ('OTRO', 'Otro')]
    ESTADOS = [('ABIERTO', 'Abierto'), ('EN_PROCESO', 'En proceso'), ('CERRADO', 'Cerrado')]
    numero = models.CharField('N°', max_length=12, editable=False)
    tercero = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+', verbose_name='Cliente')
    tipo = models.CharField(max_length=17, choices=TIPOS, default='RECLAMO')
    asunto = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    oportunidad = models.ForeignKey('ventas.Oportunidad', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='casos')
    obra = models.ForeignKey('obras.Obra', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                             verbose_name='Obra o proyecto')
    venta = models.ForeignKey('ventas.Venta', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              verbose_name='Comprobante')
    responsable = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    fecha_limite = models.DateField('Plazo de respuesta', null=True, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ABIERTO')
    solucion = models.TextField('Solución', blank=True)
    cerrado_en = models.DateField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   editable=False)

    class Meta:
        ordering = ['estado', '-creado']
        verbose_name = 'caso de postventa'
        verbose_name_plural = 'casos de postventa'

    def __str__(self):
        return f'{self.numero} {self.asunto}'

    def save(self, *args, **kwargs):
        if not self.numero:
            from core.models import Serie
            _, numero = Serie.siguiente('PVT', 'PV')
            self.numero = f'PV-{int(numero):05d}'
        if self.estado == 'CERRADO' and not self.cerrado_en:
            self.cerrado_en = timezone.localdate()
        super().save(*args, **kwargs)

    @property
    def vencido(self):
        return self.estado != 'CERRADO' and self.fecha_limite and self.fecha_limite < timezone.localdate()


class EncuestaSatisfaccion(models.Model):
    """Encuesta de satisfacción al cerrar la obra, la entrega o el caso (CRM-17)."""
    tercero = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+', verbose_name='Cliente')
    motivo = models.CharField('Motivo de la encuesta', max_length=150,
                              help_text='Ej. cierre de la obra OB0003, entrega del dpto. D-301, caso PV-00012')
    obra = models.ForeignKey('obras.Obra', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    caso = models.ForeignKey(CasoPostventa, on_delete=models.SET_NULL, null=True, blank=True, related_name='encuestas')
    fecha = models.DateField(default=timezone.localdate)
    satisfaccion = models.PositiveSmallIntegerField('Satisfacción (1 a 5)', default=5)
    recomendaria = models.PositiveSmallIntegerField('¿Nos recomendaría? (0 a 10)', default=10)
    comentario = models.TextField(blank=True)
    registrado_por = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                       editable=False)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'encuesta de satisfacción'
        verbose_name_plural = 'encuestas de satisfacción'


class ContratoServicio(models.Model):
    """Contrato con fecha de término: avisa su renovación (CRM-12, servicios)."""
    ESTADOS = [('VIGENTE', 'Vigente'), ('RENOVADO', 'Renovado'), ('TERMINADO', 'Terminado')]
    tercero = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+', verbose_name='Cliente')
    descripcion = models.CharField('Servicio', max_length=200)
    inicio = models.DateField()
    fin = models.DateField('Termina el')
    valor = models.DecimalField('Valor del contrato S/', max_digits=14, decimal_places=2, default=D0)
    aviso_dias = models.PositiveSmallIntegerField('Avisar con (días)', default=30)
    responsable = models.ForeignKey(USUARIO, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    estado = models.CharField(max_length=9, choices=ESTADOS, default='VIGENTE')
    oportunidad_renovacion = models.ForeignKey('ventas.Oportunidad', on_delete=models.SET_NULL, null=True, blank=True,
                                               related_name='+', editable=False)

    class Meta:
        ordering = ['fin']
        verbose_name = 'contrato de servicio'
        verbose_name_plural = 'contratos de servicio'

    def __str__(self):
        return f'{self.descripcion} ({self.tercero.nombre})'

    @property
    def por_renovar(self):
        from datetime import timedelta
        return self.estado == 'VIGENTE' and self.fin <= timezone.localdate() + timedelta(days=self.aviso_dias)
