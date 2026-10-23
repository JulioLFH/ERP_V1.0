"""Obras (construcción): obra, recursos (insumos), presupuestos por versiones con partidas y análisis de precios
unitarios (APU), precios por presupuesto y biblioteca de partidas tipo. Diseño: docs/OBRAS.md (fase 1)."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

D0 = Decimal('0')
MONEDAS = [('PEN', 'Soles'), ('USD', 'Dólares')]


class Obra(models.Model):
    TIPOS = [('PRIVADA', 'Obra privada'), ('PUBLICA', 'Obra pública'),
             ('INMOBILIARIA', 'Proyecto inmobiliario (venta de unidades)')]
    SISTEMAS = [('SUMA_ALZADA', 'Suma alzada'), ('PRECIOS_UNITARIOS', 'Precios unitarios'),
                ('LLAVE_EN_MANO', 'Llave en mano'), ('MIXTO', 'Mixto')]
    ESTADOS = [('LICITACION', 'Propuesta / licitación'), ('ADJUDICADA', 'Adjudicada'), ('EJECUCION', 'En ejecución'),
               ('PARALIZADA', 'Paralizada'), ('RECEPCION', 'En recepción'), ('LIQUIDADA', 'Liquidada'),
               ('CERRADA', 'Cerrada'), ('NO_ADJUDICADA', 'No adjudicada (propuesta perdida)')]
    PRORRATEOS = [('AREA', 'Por área techada'), ('VALOR', 'Por precio de lista')]
    codigo = models.CharField('Código', max_length=10, unique=True, blank=True,
                              help_text='Vacío = automático (OB0001)')
    nombre = models.CharField(max_length=250)
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                limit_choices_to={'tipo__in': ['CLIENTE', 'AMBOS']})
    tipo = models.CharField(max_length=12, choices=TIPOS, default='PRIVADA')
    sistema = models.CharField('Sistema de contratación', max_length=18, choices=SISTEMAS, default='SUMA_ALZADA')
    contrato = models.CharField('N° de contrato', max_length=60, blank=True)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='PEN')
    monto_contratado = models.DecimalField('Monto contratado (con IGV)', max_digits=16, decimal_places=2, default=D0)
    fecha_contrato = models.DateField('Fecha del contrato', null=True, blank=True)
    fecha_inicio = models.DateField('Inicio de obra', null=True, blank=True)
    plazo_dias = models.PositiveIntegerField('Plazo (días calendario)', default=0)
    ubigeo = models.CharField(max_length=6, blank=True)
    direccion = models.CharField('Ubicación', max_length=250, blank=True)
    residente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+', verbose_name='Residente de obra')
    supervisor = models.CharField('Supervisor / inspector', max_length=150, blank=True)
    estado = models.CharField(max_length=13, choices=ESTADOS, default='LICITACION')
    gastos_generales_pct = models.DecimalField('Gastos generales %', max_digits=6, decimal_places=2,
                                               default=Decimal('10'))
    utilidad_pct = models.DecimalField('Utilidad %', max_digits=6, decimal_places=2, default=Decimal('10'))
    adelanto_directo_pct = models.DecimalField('Adelanto directo %', max_digits=5, decimal_places=2, default=D0)
    adelanto_materiales_pct = models.DecimalField('Adelanto para materiales %', max_digits=5, decimal_places=2,
                                                  default=D0)
    retencion_garantia_pct = models.DecimalField('Retención de garantía %', max_digits=5, decimal_places=2,
                                                 default=D0)
    area_m2 = models.DecimalField('Área de la obra (m²)', max_digits=14, decimal_places=2, default=D0, blank=True,
                                  help_text='Base para repartir gastos generales por m² (Contabilidad › Reparto de gastos)')
    # proyecto inmobiliario
    terreno_pct = models.DecimalField(
        'Terreno en el precio de venta (%)', max_digits=5, decimal_places=2, default=Decimal('50'), blank=True,
        help_text='Primera venta de inmuebles: el IGV no grava el valor del terreno, que la ley presume en el 50 % del '
                  'precio. Confírmelo con su asesor tributario')
    prorrateo = models.CharField('Costo de cada unidad', max_length=5, choices=PRORRATEOS, default='AREA')
    cuenta_recaudadora = models.ForeignKey(
        'finanzas.Cuenta', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name='Cuenta recaudadora del proyecto', help_text='Cuenta del banco que financia o del fideicomiso: los '
                                                                 'cobros de los compradores van a ella')
    banco_financiador = models.CharField('Banco que financia / supervisa', max_length=120, blank=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', editable=False, verbose_name='Centro de costo')
    almacen = models.ForeignKey('core.Almacen', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                editable=False, verbose_name='Almacén de obra')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            from core.models import Serie
            _, numero = Serie.siguiente('OBR', 'OB')  # Serie.tipo admite 3 caracteres
            self.codigo = f'OB{int(numero):04d}'
        super().save(*args, **kwargs)
        if not self.centro_costo_id or not self.almacen_id:
            self._crear_centro_y_almacen()

    def _crear_centro_y_almacen(self):
        """Centro de costo de la obra (hijo de OBRAS, costo de producción) y su almacén de obra."""
        from contabilidad.models import CentroCosto
        from core.models import Almacen
        cambios = {}
        if not self.centro_costo_id:
            padre, _ = CentroCosto.objects.get_or_create(codigo='OBRAS', defaults={
                'nombre': 'Obras', 'tipo': 'PRODUCCION'})
            codigo, n = self.codigo[:10], 1
            while CentroCosto.objects.filter(codigo=codigo).exists():
                n += 1
                codigo = f'{self.codigo[:7]}-{n}'[:10]
            cambios['centro_costo'] = CentroCosto.objects.create(
                codigo=codigo, nombre=f'Obra {self.nombre}'[:100], tipo='PRODUCCION', padre=padre)
        if not self.almacen_id:
            codigo, n = f'AO{self.codigo[2:]}'[:10], 1
            while Almacen.objects.filter(codigo=codigo).exists():
                n += 1
                codigo = f'AO{self.codigo[2:]}-{n}'[:10]
            cambios['almacen'] = Almacen.objects.create(codigo=codigo, nombre=f'Obra {self.nombre}'[:100],
                                                        direccion=self.direccion[:250], ubigeo=self.ubigeo)
        type(self).objects.filter(pk=self.pk).update(**cambios)
        for campo, valor in cambios.items():
            setattr(self, campo, valor)


class Recurso(models.Model):
    """Insumo de los análisis de precios unitarios: mano de obra, material, equipo o subcontrato."""
    TIPOS = [('MO', 'Mano de obra'), ('MAT', 'Materiales'), ('EQ', 'Equipos y herramientas'),
             ('SUB', 'Subcontratos')]
    CATEGORIAS = [('', '—'), ('CAPATAZ', 'Capataz'), ('OPERARIO', 'Operario'), ('OFICIAL', 'Oficial'),
                  ('PEON', 'Peón'), ('OPERADOR', 'Operador de equipo'), ('OTRO', 'Otro')]
    codigo = models.CharField('Código', max_length=20, unique=True, blank=True,
                              help_text='Vacío = automático; al importar se usa el código del archivo (ej. de S10)')
    nombre = models.CharField(max_length=250)
    unidad = models.CharField(max_length=12, help_text='hh, hm, m3, bls, kg, und… · %mo = porcentaje de la mano de '
                                                       'obra (herramientas manuales)')
    tipo = models.CharField(max_length=3, choices=TIPOS, default='MAT')
    categoria_mo = models.CharField('Categoría (mano de obra)', max_length=10, choices=CATEGORIAS, blank=True)
    producto = models.ForeignKey('core.Producto', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                 help_text='Material que pasa por el almacén: une el presupuesto con compras')
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', help_text='Equipo propio')
    indice_unificado = models.CharField('Índice unificado (INEI)', max_length=2, blank=True)
    precio = models.DecimalField('Precio referencial (sin IGV)', max_digits=14, decimal_places=4, default=D0)
    # sub-análisis (subpartida del S10): su precio sale de su propio análisis y la relación de insumos lo explota
    es_subanalisis = models.BooleanField('Sub-análisis', default=False,
                                         help_text='Ej. concreto f\'c=210: su precio sale de su análisis (cemento, arena, '
                                                   'piedra, agua) y la relación de insumos muestra esos recursos')
    rendimiento = models.DecimalField('Rendimiento del sub-análisis', max_digits=14, decimal_places=4, default=D0)
    jornada = models.DecimalField('Jornada del sub-análisis (h)', max_digits=5, decimal_places=2, default=Decimal('8'))
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return f'{self.codigo} {self.nombre} ({self.unidad})'

    @property
    def es_porcentaje_mo(self):
        return self.unidad.strip().lower().replace(' ', '') in ('%mo', '%m.o.', '%m.o')

    def save(self, *args, **kwargs):
        if not self.codigo:
            from core.models import Serie
            _, numero = Serie.siguiente('RCU', self.tipo)
            codigo = f'{self.tipo}{int(numero):06d}'
            while Recurso.objects.filter(codigo=codigo).exists():
                _, numero = Serie.siguiente('RCU', self.tipo)
                codigo = f'{self.tipo}{int(numero):06d}'
            self.codigo = codigo
        super().save(*args, **kwargs)


class ComponenteRecurso(models.Model):
    """Recurso del análisis de un sub-análisis (como RecursoAPU, pero del catálogo: vale para todos los presupuestos)."""
    padre = models.ForeignKey(Recurso, on_delete=models.CASCADE, related_name='componentes')
    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name='+')
    cuadrilla = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    cantidad = models.DecimalField('Aporte (cantidad)', max_digits=16, decimal_places=6, default=D0)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['orden', 'id']


class Presupuesto(models.Model):
    TIPOS = [('OFERTA', 'Oferta'), ('CONTRACTUAL', 'Contractual'), ('META', 'Meta (ejecución)')]
    ESTADOS = [('BORRADOR', 'Borrador'), ('APROBADO', 'Aprobado')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='presupuestos')
    tipo = models.CharField(max_length=11, choices=TIPOS, default='OFERTA')
    version = models.PositiveSmallIntegerField('Versión', default=1, editable=False)
    nombre = models.CharField(max_length=250, blank=True)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='BORRADOR', editable=False)
    fecha_precios = models.DateField('Precios al', default=timezone.localdate)
    gastos_generales_pct = models.DecimalField('Gastos generales %', max_digits=6, decimal_places=2,
                                               default=Decimal('10'))
    utilidad_pct = models.DecimalField('Utilidad %', max_digits=6, decimal_places=2, default=Decimal('10'))
    igv_pct = models.DecimalField('IGV %', max_digits=5, decimal_places=2, default=Decimal('18'))
    origen = models.CharField(max_length=40, blank=True, editable=False, help_text='Importado de… / copia de…')
    copia_de = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, editable=False,
                                 related_name='copias')
    # totales (los recalcula obras.calculos.recalcular)
    costo_directo = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    gastos_generales = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    utilidad = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    igv = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', 'tipo', '-version']
        unique_together = [('obra', 'tipo', 'version')]

    def __str__(self):
        return f'{self.obra.codigo} · {self.get_tipo_display()} v{self.version}'

    @property
    def editable(self):
        return self.estado == 'BORRADOR'

    def save(self, *args, **kwargs):
        if not self.pk:
            ultimo = Presupuesto.objects.filter(obra=self.obra, tipo=self.tipo).order_by('-version').first()
            self.version = (ultimo.version + 1) if ultimo else 1
        super().save(*args, **kwargs)


def clave_item(item):
    """'01.02.10' → '0001.0002.0010' para ordenar las partidas en su orden natural."""
    return '.'.join(p.zfill(4) for p in str(item).strip().split('.') if p != '')


class Subpresupuesto(models.Model):
    """Parte del presupuesto con su propia numeración (S10: 001 Estructuras, 002 Instalaciones…). Uno marcado como
    gastos generales reemplaza al % del pie por su total (gastos generales detallados)."""
    presupuesto = models.ForeignKey(Presupuesto, on_delete=models.CASCADE, related_name='subpresupuestos')
    codigo = models.CharField('Código', max_length=10)
    nombre = models.CharField(max_length=250)
    es_gastos_generales = models.BooleanField('Gastos generales detallados', default=False,
                                              help_text='Su total es el gasto general del presupuesto (en vez del %)')
    parcial = models.DecimalField(max_digits=18, decimal_places=2, default=D0, editable=False)

    class Meta:
        ordering = ['codigo', 'id']
        unique_together = [('presupuesto', 'codigo')]

    def __str__(self):
        return f'{self.codigo} {self.nombre}'


class Partida(models.Model):
    """Partida o título del presupuesto. El precio unitario sale de su análisis (APU); el título suma sus partidas."""
    presupuesto = models.ForeignKey(Presupuesto, on_delete=models.CASCADE, related_name='partidas')
    subpresupuesto = models.ForeignKey(Subpresupuesto, on_delete=models.CASCADE, null=True, blank=True,
                                       related_name='partidas')
    item = models.CharField('Ítem', max_length=40)
    orden = models.CharField(max_length=120, editable=False, db_index=True)
    descripcion = models.CharField('Descripción', max_length=400)
    unidad = models.CharField(max_length=12, blank=True)
    metrado = models.DecimalField(max_digits=16, decimal_places=4, default=D0)
    es_titulo = models.BooleanField('Título', default=False)
    precio_fijo = models.BooleanField('Precio sin análisis', default=False,
                                      help_text='Partida global o importada sin APU: el precio se escribe a mano')
    rendimiento = models.DecimalField('Rendimiento (unidades por jornada)', max_digits=14, decimal_places=4,
                                      default=D0)
    jornada = models.DecimalField('Jornada (horas)', max_digits=5, decimal_places=2, default=Decimal('8'))
    precio = models.DecimalField('Precio unitario', max_digits=16, decimal_places=2, default=D0)
    parcial = models.DecimalField(max_digits=18, decimal_places=2, default=D0, editable=False)
    partida_base = models.ForeignKey('PartidaBase', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)

    class Meta:
        ordering = ['subpresupuesto__codigo', 'orden', 'id']
        constraints = [
            models.UniqueConstraint(fields=['presupuesto', 'subpresupuesto', 'item'], name='partida_item_unico'),
            models.UniqueConstraint(fields=['presupuesto', 'item'], condition=models.Q(subpresupuesto__isnull=True),
                                    name='partida_item_unico_sin_sub'),
        ]

    def __str__(self):
        return f'{self.item} {self.descripcion}'

    @property
    def nivel(self):
        return self.item.count('.')

    def save(self, *args, **kwargs):
        self.item = self.item.strip()
        self.orden = clave_item(self.item)
        if self.es_titulo:
            self.unidad, self.metrado, self.precio_fijo = '', D0, False
        super().save(*args, **kwargs)


class RecursoAPU(models.Model):
    """Recurso del análisis de una partida. Mano de obra y equipos con cuadrilla: aporte = cuadrilla × jornada /
    rendimiento; si no, se escribe el aporte (cantidad por unidad de la partida)."""
    partida = models.ForeignKey(Partida, on_delete=models.CASCADE, related_name='recursos')
    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name='+')
    cuadrilla = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    cantidad = models.DecimalField('Aporte (cantidad)', max_digits=16, decimal_places=6, default=D0)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['orden', 'id']


class PrecioPresupuesto(models.Model):
    """Precio del recurso en el presupuesto: uno solo para todas las partidas (como en S10)."""
    presupuesto = models.ForeignKey(Presupuesto, on_delete=models.CASCADE, related_name='precios')
    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name='+')
    precio = models.DecimalField(max_digits=14, decimal_places=4, default=D0)

    class Meta:
        unique_together = [('presupuesto', 'recurso')]


class PartidaBase(models.Model):
    """Biblioteca de partidas tipo: el análisis se copia al presupuesto y se ajusta allí."""
    codigo = models.CharField('Código', max_length=20, unique=True, blank=True)
    descripcion = models.CharField('Descripción', max_length=400)
    unidad = models.CharField(max_length=12)
    rendimiento = models.DecimalField(max_digits=14, decimal_places=4, default=D0)
    jornada = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('8'))
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['descripcion']
        verbose_name = 'partida tipo'

    def __str__(self):
        return f'{self.codigo} {self.descripcion} ({self.unidad})'

    def save(self, *args, **kwargs):
        if not self.codigo:
            from core.models import Serie
            _, numero = Serie.siguiente('PTB', 'PB')
            self.codigo = f'PB{int(numero):05d}'
        super().save(*args, **kwargs)


class RecursoBase(models.Model):
    partida = models.ForeignKey(PartidaBase, on_delete=models.CASCADE, related_name='recursos')
    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name='+')
    cuadrilla = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    cantidad = models.DecimalField(max_digits=16, decimal_places=6, default=D0)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['orden', 'id']


# ---------------------------------------------------------------- licitaciones (CRM de constructoras)
class Licitacion(models.Model):
    """Proceso de selección (SEACE) o propuesta privada de una obra: la oportunidad del CRM, sus datos, cronograma,
    requisitos del postor y el resultado (buena pro o perdida)."""
    PROCEDIMIENTOS = [('PRIVADA', 'Propuesta privada / invitación'), ('LP', 'Licitación pública'),
                      ('CP', 'Concurso público'), ('AS', 'Adjudicación simplificada'),
                      ('SIE', 'Subasta inversa electrónica'), ('CD', 'Contratación directa'), ('OTRO', 'Otro')]
    RESULTADOS = [('EN_CURSO', 'En curso'), ('GANADA', 'Ganada (buena pro)'), ('PERDIDA', 'Perdida'),
                  ('DESIERTA', 'Desierta / cancelada')]
    MOTIVOS = [('', '—'), ('PRECIO', 'Precio'), ('EXPERIENCIA', 'Experiencia del postor'), ('PLAZO', 'Plazo'),
               ('TECNICA', 'Propuesta técnica'), ('DESCALIFICACION', 'Descalificación (requisitos)'), ('OTRO', 'Otro')]
    obra = models.OneToOneField(Obra, on_delete=models.CASCADE, related_name='licitacion')
    oportunidad = models.OneToOneField('ventas.Oportunidad', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='licitacion')
    procedimiento = models.CharField('Procedimiento', max_length=8, choices=PROCEDIMIENTOS, default='PRIVADA')
    numero = models.CharField('N° / nomenclatura (SEACE)', max_length=80, blank=True)
    valor_referencial = models.DecimalField('Valor referencial (con IGV)', max_digits=16, decimal_places=2,
                                            default=D0)
    consorcio = models.BooleanField('Se postula en consorcio', default=False)
    consorciados = models.CharField('Empresas del consorcio', max_length=250, blank=True)
    participacion_pct = models.DecimalField('Nuestra participación %', max_digits=5, decimal_places=2,
                                            default=Decimal('100'))
    resultado = models.CharField(max_length=8, choices=RESULTADOS, default='EN_CURSO', editable=False)
    motivo_perdida = models.CharField('Motivo', max_length=15, choices=MOTIVOS, blank=True, editable=False)
    detalle_resultado = models.CharField(max_length=250, blank=True, editable=False)
    ganador = models.CharField('Ganador', max_length=150, blank=True, editable=False)
    precio_ganador = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, editable=False)
    fecha_resultado = models.DateField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'licitación'
        verbose_name_plural = 'licitaciones'

    def __str__(self):
        return f'{self.get_procedimiento_display()} {self.numero or self.obra.nombre}'

    @property
    def oferta(self):
        """Presupuesto de oferta vigente (el aprobado más reciente o el último)."""
        ofertas = list(self.obra.presupuestos.filter(tipo='OFERTA'))
        return next((p for p in ofertas if p.estado == 'APROBADO'), ofertas[0] if ofertas else None)

    @property
    def pct_referencial(self):
        oferta = self.oferta
        if oferta is None or not self.valor_referencial:
            return None
        return (oferta.total / self.valor_referencial * 100).quantize(Decimal('0.01'))


class HitoLicitacion(models.Model):
    TIPOS = [('CONVOCATORIA', 'Convocatoria'), ('REGISTRO', 'Registro de participantes'),
             ('CONSULTAS', 'Consultas y observaciones'), ('ABSOLUCION', 'Absolución de consultas'),
             ('INTEGRACION', 'Integración de bases'), ('PRESENTACION', 'Presentación de ofertas'),
             ('BUENA_PRO', 'Otorgamiento de la buena pro'), ('CONSENTIMIENTO', 'Consentimiento de la buena pro'),
             ('FIRMA', 'Firma del contrato'), ('OTRO', 'Otro')]
    licitacion = models.ForeignKey(Licitacion, on_delete=models.CASCADE, related_name='hitos')
    tipo = models.CharField(max_length=14, choices=TIPOS)
    fecha = models.DateField()
    hora = models.TimeField(null=True, blank=True)
    hecho = models.BooleanField(default=False)
    nota = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['fecha', 'hora', 'id']


class RequisitoPostor(models.Model):
    """Lo que el postor debe acreditar: experiencia, personal clave, equipos, RNP, garantía… con su archivo
    (sustentos) y vencimiento."""
    TIPOS = [('EXPERIENCIA', 'Experiencia en obras similares'), ('PERSONAL', 'Personal clave'),
             ('EQUIPO', 'Equipamiento'), ('RNP', 'RNP vigente'), ('GARANTIA', 'Garantía de seriedad de oferta'),
             ('DOCUMENTO', 'Otro documento')]
    licitacion = models.ForeignKey(Licitacion, on_delete=models.CASCADE, related_name='requisitos')
    tipo = models.CharField(max_length=11, choices=TIPOS)
    descripcion = models.CharField('Descripción', max_length=250)
    monto = models.DecimalField('Monto (experiencia o garantía)', max_digits=16, decimal_places=2, null=True,
                                blank=True)
    vence = models.DateField('Vence', null=True, blank=True)
    cumple = models.BooleanField('Cumple / listo', default=False)

    class Meta:
        ordering = ['tipo', 'id']
        verbose_name = 'requisito del postor'

    def __str__(self):
        return f'{self.get_tipo_display()}: {self.descripcion}'


# ---------------------------------------------------------------- proyecto inmobiliario
class Unidad(models.Model):
    """Unidad vendible de un proyecto inmobiliario."""
    TIPOS = [('DEPARTAMENTO', 'Departamento'), ('ESTACIONAMIENTO', 'Estacionamiento'), ('DEPOSITO', 'Depósito'),
             ('LOCAL', 'Local comercial'), ('OFICINA', 'Oficina'), ('CASA', 'Casa'), ('LOTE', 'Lote'), ('OTRO', 'Otro')]
    ESTADOS = [('DISPONIBLE', 'Disponible'), ('SEPARADA', 'Separada'), ('VENDIDA', 'Vendida'),
               ('ENTREGADA', 'Entregada'), ('BLOQUEADA', 'Bloqueada')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='unidades')
    codigo = models.CharField('Código', max_length=20, help_text='Ej. D-301, E-12')
    tipo = models.CharField(max_length=15, choices=TIPOS, default='DEPARTAMENTO')
    piso = models.CharField(max_length=10, blank=True)
    area_techada = models.DecimalField('Área techada (m²)', max_digits=10, decimal_places=2, default=D0)
    area_libre = models.DecimalField('Área libre (m²)', max_digits=10, decimal_places=2, default=D0)
    dormitorios = models.PositiveSmallIntegerField(default=0)
    vista = models.CharField(max_length=60, blank=True, help_text='Ej. exterior, parque, interior')
    precio_lista = models.DecimalField('Precio de lista (con IGV)', max_digits=14, decimal_places=2, default=D0)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='DISPONIBLE', editable=False)
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['obra', 'piso', 'codigo']
        unique_together = [('obra', 'codigo')]
        verbose_name = 'unidad inmobiliaria'
        verbose_name_plural = 'unidades inmobiliarias'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.codigo}'


class VentaInmueble(models.Model):
    """Separación y venta de una o más unidades (departamento + estacionamiento…) con su cronograma de pagos. Los
    cobros son anticipos (122) con su comprobante; en la entrega se reconoce el ingreso y el costo de la unidad."""
    ESTADOS = [('SEPARADA', 'Separada'), ('VENDIDA', 'Vendida (minuta)'), ('ENTREGADA', 'Entregada'),
               ('ANULADA', 'Anulada')]
    FINANCIAMIENTOS = [('CONTADO', 'Contado'), ('DIRECTO', 'Cuotas con la empresa'),
                       ('HIPOTECARIO', 'Crédito hipotecario'), ('MIVIVIENDA', 'Nuevo Crédito MiVivienda'),
                       ('TECHO_PROPIO', 'Techo Propio')]
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, related_name='ventas_inmuebles')
    numero = models.CharField('N°', max_length=12, editable=False)
    cliente = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+', verbose_name='Comprador')
    unidades = models.ManyToManyField(Unidad, related_name='ventas')
    oportunidad = models.ForeignKey('ventas.Oportunidad', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Oportunidad (CRM)')
    vendedor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='+')
    fecha_separacion = models.DateField('Separación', default=timezone.localdate)
    vence_separacion = models.DateField('La separación vence el', null=True, blank=True)
    precio = models.DecimalField('Precio de venta (con IGV)', max_digits=14, decimal_places=2)
    financiamiento = models.CharField(max_length=12, choices=FINANCIAMIENTOS, default='CONTADO')
    banco = models.CharField('Banco del crédito', max_length=120, blank=True)
    terreno_pct = models.DecimalField('Terreno en el precio (%)', max_digits=5, decimal_places=2, default=Decimal('50'))
    estado = models.CharField(max_length=9, choices=ESTADOS, default='SEPARADA', editable=False)
    fecha_minuta = models.DateField('Firma de la minuta', null=True, blank=True)
    fecha_entrega = models.DateField('Entrega', null=True, blank=True, editable=False)
    ingreso = models.DecimalField('Ingreso reconocido (sin IGV)', max_digits=14, decimal_places=2, default=D0,
                                  editable=False)
    costo = models.DecimalField('Costo de las unidades', max_digits=14, decimal_places=2, default=D0, editable=False)
    motivo_anulacion = models.CharField(max_length=200, blank=True, editable=False)
    notas = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_separacion', '-id']
        verbose_name = 'venta de inmueble'
        verbose_name_plural = 'ventas de inmuebles'

    def __str__(self):
        return f'{self.numero} {", ".join(u.codigo for u in self.unidades.all())}'

    def save(self, *args, **kwargs):
        if not self.numero:
            from core.models import Serie
            _, numero = Serie.siguiente('VIN', 'VI')
            self.numero = f'VI-{int(numero):05d}'
        super().save(*args, **kwargs)

    @property
    def cobrado(self):
        return sum((c.monto for c in CobroInmueble.objects.filter(cuota__venta=self)), D0)

    @property
    def saldo(self):
        return self.precio - self.cobrado


class CuotaInmueble(models.Model):
    CONCEPTOS = [('SEPARACION', 'Separación'), ('INICIAL', 'Cuota inicial'), ('CUOTA', 'Cuota'),
                 ('BANCO', 'Desembolso del banco'), ('BONO', 'Bono / subsidio')]
    venta = models.ForeignKey(VentaInmueble, on_delete=models.CASCADE, related_name='cuotas')
    numero = models.PositiveSmallIntegerField()
    concepto = models.CharField(max_length=10, choices=CONCEPTOS, default='CUOTA')
    vence = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['venta', 'numero']

    def __str__(self):
        return f'{self.get_concepto_display()} {self.numero}'

    @property
    def cobrado(self):
        return sum((c.monto for c in self.cobros.all()), D0)

    @property
    def saldo(self):
        return self.monto - self.cobrado


class CobroInmueble(models.Model):
    """Cobro de una cuota: su comprobante de anticipo (IGV sobre la construcción) y el ingreso a caja / bancos."""
    cuota = models.ForeignKey(CuotaInmueble, on_delete=models.CASCADE, related_name='cobros')
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    comprobante = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, related_name='+')
    movimiento = models.ForeignKey('finanzas.Movimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']
