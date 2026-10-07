"""Planillas (régimen laboral peruano): trabajadores, parámetros, AFP, conceptos y planillas mensuales, de
gratificación y de CTS. El cálculo está en calculo.py."""
from datetime import date
from decimal import Decimal

from django.db import models

D0 = Decimal('0')


class Parametro(models.Model):
    """Valores legales del año. Revíselos cada año (RMV, UIT) en Planillas › Configuración."""
    anio = models.PositiveIntegerField('Año', unique=True)
    rmv = models.DecimalField('Remuneración mínima vital S/', max_digits=10, decimal_places=2)
    uit = models.DecimalField('UIT S/', max_digits=10, decimal_places=2)
    asignacion_familiar_pct = models.DecimalField('Asignación familiar % de la RMV', max_digits=5, decimal_places=2,
                                                  default=Decimal('10'))
    essalud_pct = models.DecimalField('EsSalud %', max_digits=5, decimal_places=2, default=Decimal('9'))
    onp_pct = models.DecimalField('ONP %', max_digits=5, decimal_places=2, default=Decimal('13'))
    bonificacion_extraordinaria_pct = models.DecimalField('Bonificación extraordinaria % (Ley 30334)', max_digits=5,
                                                          decimal_places=2, default=Decimal('9'))
    horas_jornada = models.DecimalField('Horas de la jornada diaria', max_digits=4, decimal_places=2,
                                        default=Decimal('8'))

    class Meta:
        ordering = ['-anio']
        verbose_name = 'parámetros del año'

    def __str__(self):
        return str(self.anio)

    @classmethod
    def del_anio(cls, anio):
        p = cls.objects.filter(anio__lte=anio).order_by('-anio').first() or cls.objects.order_by('anio').first()
        if p is None:
            raise ValueError('Registre los parámetros de planilla (RMV, UIT) en Planillas › Configuración.')
        return p

    @property
    def asignacion_familiar(self):
        return (self.rmv * self.asignacion_familiar_pct / 100).quantize(Decimal('0.01'))


class AFP(models.Model):
    """Tasas vigentes de cada AFP (las publica la SBS; actualícelas cuando cambien)."""
    codigo = models.CharField('Código', max_length=10, unique=True)
    nombre = models.CharField(max_length=60)
    aporte_pct = models.DecimalField('Aporte obligatorio %', max_digits=5, decimal_places=2, default=Decimal('10'))
    prima_pct = models.DecimalField('Prima de seguro %', max_digits=5, decimal_places=2, default=D0)
    comision_flujo_pct = models.DecimalField('Comisión sobre la remuneración (flujo) %', max_digits=5,
                                             decimal_places=2, default=D0)
    comision_mixta_pct = models.DecimalField('Comisión mixta sobre la remuneración %', max_digits=5,
                                             decimal_places=2, default=D0)
    tope_prima = models.DecimalField('Remuneración máxima asegurable S/', max_digits=10, decimal_places=2,
                                     default=D0, help_text='Tope para la prima de seguro. 0 = sin tope')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'AFP'
        verbose_name_plural = 'AFP'

    def __str__(self):
        return self.nombre


class ConceptoPlanilla(models.Model):
    """Concepto de la boleta: su código de la tabla 22 de PLAME y la cuenta contable."""
    TIPOS = [('INGRESO', 'Ingreso'), ('DESCUENTO', 'Descuento al trabajador'),
             ('APORTE', 'Aporte del empleador')]
    clave = models.CharField(max_length=20, unique=True, editable=False)
    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=10, choices=TIPOS, editable=False)
    codigo_plame = models.CharField('Código PLAME (tabla 22)', max_length=4, blank=True)
    cuenta = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='+', verbose_name='Cuenta contable',
                               help_text='Ingresos y aportes: cuenta de gasto (62). Descuentos: cuenta por pagar '
                                         '(40, 41) o por cobrar al trabajador (14)')
    cuenta_pasivo = models.ForeignKey('contabilidad.CuentaContable', on_delete=models.PROTECT, null=True,
                                      blank=True, related_name='+', verbose_name='Cuenta por pagar (aportes)',
                                      help_text='Solo aportes del empleador: ej. 4031 EsSalud')
    orden = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ['orden']
        verbose_name = 'concepto de planilla'
        verbose_name_plural = 'conceptos de planilla'

    def __str__(self):
        return self.nombre


class Trabajador(models.Model):
    TIPOS_DOC = [('01', 'DNI'), ('04', 'Carné de extranjería'), ('07', 'Pasaporte')]
    TIPOS = [('EMPLEADO', 'Empleado'), ('OBRERO', 'Obrero')]
    REGIMENES = [('GENERAL', 'Régimen general'), ('PEQUENA', 'Pequeña empresa (REMYPE)'),
                 ('MICRO', 'Microempresa (REMYPE)')]
    PENSIONES = [('ONP', 'ONP (Sistema Nacional de Pensiones)'), ('AFP', 'AFP (Sistema Privado de Pensiones)'),
                 ('NINGUNO', 'Sin sistema de pensiones')]
    COMISIONES = [('FLUJO', 'Comisión sobre la remuneración (flujo)'), ('MIXTA', 'Comisión mixta')]

    tipo_doc = models.CharField('Tipo de documento', max_length=2, choices=TIPOS_DOC, default='01')
    numero_doc = models.CharField('N° documento', max_length=15, unique=True)
    apellido_paterno = models.CharField(max_length=60)
    apellido_materno = models.CharField(max_length=60, blank=True)
    nombres = models.CharField(max_length=80)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    sexo = models.CharField(max_length=1, choices=[('M', 'Masculino'), ('F', 'Femenino')], blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField('Teléfono', max_length=30, blank=True)
    direccion = models.CharField('Dirección', max_length=200, blank=True)
    # ---- laboral
    fecha_ingreso = models.DateField('Fecha de ingreso', null=True, blank=True)
    fecha_cese = models.DateField('Fecha de cese', null=True, blank=True)
    motivo_cese = models.CharField('Motivo de cese', max_length=100, blank=True)
    cargo = models.CharField(max_length=80, blank=True)
    tipo = models.CharField('Tipo de trabajador', max_length=10, choices=TIPOS, default='EMPLEADO')
    regimen = models.CharField('Régimen laboral', max_length=10, choices=REGIMENES, default='GENERAL')
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     verbose_name='Centro de costo',
                                     help_text='Define el destino del gasto: producción (90), administración '
                                               '(94) o ventas (95)')
    sueldo = models.DecimalField('Remuneración básica mensual S/', max_digits=10, decimal_places=2, default=D0)
    turno = models.ForeignKey('Turno', on_delete=models.SET_NULL, null=True, blank=True, related_name='trabajadores',
                              help_text='Horario para el control de asistencia (tardanzas, faltas y horas extra)')
    asignacion_familiar = models.BooleanField('Asignación familiar', default=False,
                                              help_text='Tiene hijos menores de 18 años (o estudiando hasta los 24)')
    # ---- pensiones
    sistema_pensiones = models.CharField('Sistema de pensiones', max_length=8, choices=PENSIONES, default='ONP')
    afp = models.ForeignKey(AFP, on_delete=models.PROTECT, null=True, blank=True, verbose_name='AFP')
    comision_afp = models.CharField('Tipo de comisión', max_length=5, choices=COMISIONES, default='FLUJO')
    cuspp = models.CharField('CUSPP', max_length=12, blank=True)
    # ---- quinta categoría: lo percibido y retenido en el año antes de usar el sistema (o en otro empleador)
    quinta_anio = models.PositiveIntegerField('Año de los datos previos', null=True, blank=True)
    quinta_remuneracion_previa = models.DecimalField('Remuneraciones previas del año S/', max_digits=12,
                                                     decimal_places=2, default=D0,
                                                     help_text='Percibidas en el año antes de la primera planilla '
                                                               'del sistema (incluye gratificaciones)')
    quinta_retencion_previa = models.DecimalField('Retenciones de quinta previas S/', max_digits=12,
                                                  decimal_places=2, default=D0)
    # ---- pagos
    banco = models.CharField('Banco (sueldo)', max_length=30, blank=True)
    cuenta_sueldo = models.CharField('Cuenta de sueldo', max_length=30, blank=True)
    banco_cts = models.CharField('Banco (CTS)', max_length=30, blank=True)
    cuenta_cts = models.CharField('Cuenta CTS', max_length=30, blank=True)

    class Meta:
        ordering = ['apellido_paterno', 'apellido_materno', 'nombres']
        verbose_name = 'trabajador'
        verbose_name_plural = 'trabajadores'

    def __str__(self):
        return f'{self.nombre_completo} ({self.numero_doc})'

    @property
    def nombre_completo(self):
        return ' '.join(x for x in (self.apellido_paterno, self.apellido_materno) if x) + f', {self.nombres}'

    @property
    def datos_completos(self):
        return bool(self.fecha_ingreso and self.sueldo > 0 and (self.sistema_pensiones != 'AFP' or self.afp_id))

    def activo_en(self, desde, hasta):
        """¿Tuvo vínculo laboral en algún día del rango?"""
        return bool(self.fecha_ingreso and self.fecha_ingreso <= hasta and
                    (self.fecha_cese is None or self.fecha_cese >= desde))

    @property
    def activo(self):
        return self.fecha_cese is None or self.fecha_cese >= date.today()


class Planilla(models.Model):
    TIPOS = [('MENSUAL', 'Remuneraciones del mes'), ('GRATIFICACION', 'Gratificación (julio / diciembre)'),
             ('CTS', 'CTS (mayo / noviembre)'), ('LIQUIDACION', 'Liquidación de beneficios sociales (ceses del mes)')]
    ESTADOS = [('BORRADOR', 'Borrador'), ('CALCULADA', 'Calculada'), ('CERRADA', 'Cerrada'),
               ('PAGADA', 'Pagada')]
    tipo = models.CharField(max_length=14, choices=TIPOS, default='MENSUAL')
    periodo = models.CharField(max_length=6, help_text='AAAAMM')
    fecha_pago = models.DateField('Fecha de pago', null=True, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR')
    observaciones = models.TextField(blank=True)
    movimiento_pago = models.ForeignKey('finanzas.Movimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-periodo', 'tipo']
        unique_together = [('tipo', 'periodo')]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.periodo[4:]}/{self.periodo[:4]}'

    @property
    def anio(self):
        return int(self.periodo[:4])

    @property
    def mes(self):
        return int(self.periodo[4:])

    def totales(self):
        agg = self.filas.aggregate(i=models.Sum('total_ingresos'), d=models.Sum('total_descuentos'),
                                   a=models.Sum('total_aportes'), n=models.Sum('neto'))
        return {k: agg[k] or D0 for k in agg}


class FilaPlanilla(models.Model):
    """Un trabajador en la planilla: datos del mes (editables) y resultados del cálculo."""
    planilla = models.ForeignKey(Planilla, on_delete=models.CASCADE, related_name='filas')
    trabajador = models.ForeignKey(Trabajador, on_delete=models.PROTECT, related_name='filas')
    # ---- datos del periodo
    dias_laborados = models.DecimalField('Días del periodo', max_digits=5, decimal_places=2, default=Decimal('30'))
    dias_falta = models.DecimalField('Faltas', max_digits=5, decimal_places=2, default=D0)
    dias_vacaciones = models.DecimalField('Vacaciones', max_digits=5, decimal_places=2, default=D0)
    dias_vacaciones_vendidas = models.DecimalField(
        'Vacaciones vendidas', max_digits=5, decimal_places=2, default=D0,
        help_text='Días de descanso compensados con remuneración (máx. 15 por año, art. 19 D. Leg. 713)')
    despido_arbitrario = models.BooleanField('Despido arbitrario (indemnización)', default=False,
                                             help_text='Solo liquidaciones: calcula la indemnización del art. 38 '
                                                       'del D. S. 003-97-TR')
    dias_subsidio = models.DecimalField('Descanso médico subsidiado', max_digits=5, decimal_places=2, default=D0,
                                        help_text='Días pagados por EsSalud (desde el día 21 de incapacidad)')
    horas_extra_25 = models.DecimalField('Horas extra 25%', max_digits=6, decimal_places=2, default=D0)
    horas_extra_35 = models.DecimalField('Horas extra 35%', max_digits=6, decimal_places=2, default=D0)
    otros_ingresos = models.DecimalField('Otros ingresos afectos', max_digits=10, decimal_places=2, default=D0)
    ingresos_no_afectos = models.DecimalField('Movilidad / no afectos', max_digits=10, decimal_places=2, default=D0)
    adelantos = models.DecimalField(max_digits=10, decimal_places=2, default=D0)
    otros_descuentos = models.DecimalField('Otros descuentos', max_digits=10, decimal_places=2, default=D0)
    # ---- resultados
    remuneracion_afecta = models.DecimalField(max_digits=12, decimal_places=2, default=D0, editable=False)
    total_ingresos = models.DecimalField(max_digits=12, decimal_places=2, default=D0, editable=False)
    total_descuentos = models.DecimalField(max_digits=12, decimal_places=2, default=D0, editable=False)
    total_aportes = models.DecimalField(max_digits=12, decimal_places=2, default=D0, editable=False)
    neto = models.DecimalField(max_digits=12, decimal_places=2, default=D0, editable=False)
    detalle_calculo = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ['trabajador__apellido_paterno', 'trabajador__apellido_materno', 'trabajador__nombres']
        unique_together = [('planilla', 'trabajador')]

    def __str__(self):
        return f'{self.planilla} · {self.trabajador}'

    def monto(self, clave):
        return sum((l.monto for l in self.lineas.all() if l.concepto.clave == clave), D0)


class Vacacion(models.Model):
    """Días de vacaciones gozados o vendidos; el récord vacacional se calcula en calculo.record_vacacional."""
    TIPOS = [('GOCE', 'Goce vacacional (descanso)'), ('VENTA', 'Venta / compensación (trabaja y cobra)')]
    trabajador = models.ForeignKey(Trabajador, on_delete=models.CASCADE, related_name='vacaciones')
    tipo = models.CharField(max_length=5, choices=TIPOS, default='GOCE')
    fecha_inicio = models.DateField('Desde')
    fecha_fin = models.DateField('Hasta')
    anio_servicio = models.PositiveIntegerField('Año de servicio que se descuenta', null=True, blank=True,
                                                help_text='Vacío = el periodo pendiente más antiguo')
    observaciones = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_inicio']
        verbose_name = 'vacaciones'
        verbose_name_plural = 'vacaciones'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.trabajador.nombre_completo} {self.fecha_inicio:%d/%m/%Y}'

    @property
    def dias(self):
        return (self.fecha_fin - self.fecha_inicio).days + 1

    def dias_en(self, desde, hasta):
        """Días del registro dentro del rango (para la planilla del mes)."""
        inicio, fin = max(self.fecha_inicio, desde), min(self.fecha_fin, hasta)
        return max((fin - inicio).days + 1, 0)


class Turno(models.Model):
    """Horario de trabajo: entrada, salida, refrigerio y tolerancia para las tardanzas."""
    nombre = models.CharField(max_length=60, help_text='Ej. Mañana, Tarde, Noche, Administrativo')
    hora_entrada = models.TimeField('Entrada')
    hora_salida = models.TimeField('Salida', help_text='Si es menor que la entrada, el turno termina al día siguiente')
    refrigerio_min = models.PositiveSmallIntegerField('Refrigerio (minutos)', default=60,
                                                      help_text='No se cuenta como tiempo trabajado')
    tolerancia_min = models.PositiveSmallIntegerField('Tolerancia (minutos)', default=5)
    dias = models.CharField('Días laborables', max_length=7, default='123456', help_text='1 = lunes … 7 = domingo')

    class Meta:
        ordering = ['hora_entrada', 'nombre']
        verbose_name = 'turno'

    def __str__(self):
        return f'{self.nombre} ({self.hora_entrada:%H:%M}-{self.hora_salida:%H:%M})'

    @property
    def horas_jornada(self):
        """Horas efectivas del turno (sin refrigerio)."""
        from datetime import datetime, timedelta
        inicio = datetime.combine(date.today(), self.hora_entrada)
        fin = datetime.combine(date.today(), self.hora_salida)
        if fin <= inicio:
            fin += timedelta(days=1)
        return Decimal((fin - inicio).seconds - self.refrigerio_min * 60) / 3600

    def laborable(self, fecha):
        return str(fecha.isoweekday()) in self.dias


class Marcacion(models.Model):
    """Entrada y salida del trabajador en un día (del reloj marcador, importadas de Excel, o registradas a mano)."""
    ORIGENES = [('RELOJ', 'Reloj / Excel'), ('MANUAL', 'Manual')]
    trabajador = models.ForeignKey(Trabajador, on_delete=models.CASCADE, related_name='marcaciones')
    fecha = models.DateField()
    entrada = models.TimeField(null=True, blank=True)
    salida = models.TimeField(null=True, blank=True)
    origen = models.CharField(max_length=6, choices=ORIGENES, default='MANUAL')
    observacion = models.CharField(max_length=120, blank=True,
                                   help_text='Ej. Permiso, descanso médico, comisión de servicio (no cuenta como falta)')
    justificada = models.BooleanField('Falta o tardanza justificada', default=False)

    class Meta:
        ordering = ['-fecha', 'trabajador']
        unique_together = [('trabajador', 'fecha')]
        verbose_name = 'marcación'
        verbose_name_plural = 'marcaciones'


class LineaPlanilla(models.Model):
    fila = models.ForeignKey(FilaPlanilla, on_delete=models.CASCADE, related_name='lineas')
    concepto = models.ForeignKey(ConceptoPlanilla, on_delete=models.PROTECT, related_name='+')
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    base = models.DecimalField(max_digits=12, decimal_places=2, default=D0, help_text='Base de cálculo')

    class Meta:
        ordering = ['concepto__orden']
