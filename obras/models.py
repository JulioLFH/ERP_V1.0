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
             ('INMOBILIARIA', 'Proyecto inmobiliario (venta de unidades)'),
             ('SERVICIO', 'Proyecto de servicios (arquitectura, ingeniería, consultoría, supervisión)')]
    SISTEMAS = [('SUMA_ALZADA', 'Suma alzada'), ('PRECIOS_UNITARIOS', 'Precios unitarios'),
                ('LLAVE_EN_MANO', 'Llave en mano'), ('MIXTO', 'Mixto')]
    ESTADOS = [('LICITACION', 'Propuesta / licitación'), ('ADJUDICADA', 'Adjudicada'), ('EJECUCION', 'En ejecución'),
               ('PARALIZADA', 'Paralizada'), ('RECEPCION', 'En recepción'), ('LIQUIDADA', 'Liquidada'),
               ('CERRADA', 'Cerrada'), ('NO_ADJUDICADA', 'No adjudicada (propuesta perdida)')]
    PRORRATEOS = [('AREA', 'Por área techada'), ('VALOR', 'Por precio de lista'),
                  ('MIXTO', 'Mixto: área techada y precio de lista')]
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
    margen_meta_pct = models.DecimalField(
        'Margen meta %', max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='HR-14: se alerta si el margen proyectado al cierre baja de este %. Vacío = el parámetro '
                  'OBR_MARGEN_META_PCT')
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
    prorrateo_area_pct = models.DecimalField('Mixto: peso del área (%)', max_digits=5, decimal_places=2,
                                             default=Decimal('50'), help_text='El resto pesa el precio de lista')
    fecha_terminacion = models.DateField('Terminación (conformidad de obra)', null=True, blank=True, editable=False,
                                         help_text='Los inmuebles pasan de «en construcción» a «terminados» y se deja '
                                                   'de activar costos (intereses y gastos posteriores van a resultados)')
    cuenta_recaudadora = models.ForeignKey(
        'finanzas.Cuenta', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name='Cuenta recaudadora del proyecto', help_text='Cuenta del banco que financia o del fideicomiso: los '
                                                                 'cobros de los compradores van a ella')
    banco_financiador = models.CharField('Banco que financia / supervisa', max_length=120, blank=True)
    # proyecto de servicios (EMP-02, SRV-02, SRV-06): presupuesto de horas y gastos; el residente es el jefe de proyecto
    horas_presupuestadas = models.DecimalField('Horas presupuestadas', max_digits=10, decimal_places=2, default=D0,
                                               blank=True)
    gastos_presupuestados = models.DecimalField('Gastos presupuestados S/ (sin personal)', max_digits=14,
                                                decimal_places=2, default=D0, blank=True)
    centro_costo = models.ForeignKey('contabilidad.CentroCosto', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+', editable=False, verbose_name='Centro de costo')
    almacen = models.ForeignKey('core.Almacen', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                editable=False, verbose_name='Almacén de obra')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']

    def __str__(self):
        return f'{self.codigo} {self.nombre}'

    @classmethod
    def de_centro(cls, centro_costo_id):
        """La obra de un centro de costo (o None): sus compras y recepciones van a su almacén de obra."""
        if not centro_costo_id:
            return None
        return cls.objects.filter(centro_costo_id=centro_costo_id).select_related('almacen').first()

    def save(self, *args, **kwargs):
        if not self.codigo:
            from core.models import Serie
            _, numero = Serie.siguiente('OBR', 'OB')  # Serie.tipo admite 3 caracteres
            self.codigo = f'OB{int(numero):04d}'
        super().save(*args, **kwargs)
        if not self.centro_costo_id or (not self.almacen_id and self.tipo != 'SERVICIO'):
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
            prefijo = 'Proyecto' if self.tipo == 'SERVICIO' else 'Obra'  # SRV-12
            cambios['centro_costo'] = CentroCosto.objects.create(
                codigo=codigo, nombre=f'{prefijo} {self.nombre}'[:100], tipo='PRODUCCION', padre=padre)
        if not self.almacen_id and self.tipo != 'SERVICIO':  # un proyecto de servicios no tiene almacén (EMP-02)
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
    # nueva versión del S10 sobre la obra (adicional, deductivo o nueva meta) con la resolución que la aprueba
    MOTIVOS = [('', 'Versión inicial'), ('ADICIONAL', 'Adicional de obra'), ('DEDUCTIVO', 'Deductivo de obra'),
               ('ADICIONAL_DEDUCTIVO', 'Adicional con deductivo vinculado'), ('NUEVA_META', 'Nueva meta / reformulación'),
               ('CORRECCION', 'Corrección de metrados o precios')]
    motivo = models.CharField('Motivo de la versión', max_length=19, choices=MOTIVOS, blank=True)
    resolucion = models.CharField('Resolución / documento que la aprueba', max_length=120, blank=True)
    fecha_resolucion = models.DateField('Fecha de la resolución', null=True, blank=True)
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

    @property
    def clave(self):
        """Subpresupuesto e ítem: une la misma partida entre versiones del presupuesto y entre contractual y meta."""
        return f'{self.subpresupuesto.codigo if self.subpresupuesto_id else ""}|{self.item}'

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
             ('LOCAL', 'Local comercial'), ('OFICINA', 'Oficina'), ('CASA', 'Casa'), ('LOTE', 'Lote'), ('OTRO', 'Otro'),
             ('COMUN', 'Área común (no se vende)')]
    VIVIENDA = ('DEPARTAMENTO', 'CASA')
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

    @property
    def vendible(self):
        """Las áreas comunes no se venden: su costo lo llevan las unidades vendibles."""
        return self.tipo != 'COMUN'


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
    provision_garantia = models.DecimalField('Provisión de garantías', max_digits=14, decimal_places=2, default=D0,
                                             editable=False)
    exonerada_igv = models.BooleanField('Exonerada del IGV (vivienda)', default=False, editable=False)
    motivo_anulacion = models.CharField(max_length=200, blank=True, editable=False)
    fecha_anulacion = models.DateField(null=True, blank=True, editable=False)
    penalidad = models.DecimalField('Penalidad retenida', max_digits=14, decimal_places=2, default=D0, editable=False)
    # ficha del comprador (prevención del lavado de activos, UIF-SBS)
    FONDOS = [('', '—'), ('AHORROS', 'Ahorros propios'), ('CREDITO', 'Crédito hipotecario'),
              ('VENTA_BIEN', 'Venta de otro bien'), ('HERENCIA', 'Herencia o donación'),
              ('PRESTAMO', 'Préstamo de terceros'), ('NEGOCIO', 'Ingresos del negocio'), ('OTRO', 'Otro')]
    ocupacion = models.CharField('Ocupación / actividad del comprador', max_length=120, blank=True)
    origen_fondos = models.CharField('Origen de los fondos', max_length=10, choices=FONDOS, blank=True)
    origen_fondos_detalle = models.CharField('Detalle del origen de los fondos', max_length=200, blank=True)
    es_pep = models.BooleanField('Persona expuesta políticamente (PEP)', default=False)
    pep_cargo = models.CharField('Cargo público (PEP)', max_length=120, blank=True)
    declaracion_fondos = models.BooleanField('Declaración jurada de origen de fondos firmada', default=False,
                                             help_text='Adjúntela en los sustentos de la venta')
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

    @property
    def ficha_completa(self):
        """Datos mínimos del comprador para la prevención del lavado de activos."""
        return bool(self.ocupacion and self.origen_fondos and self.declaracion_fondos and
                    (not self.es_pep or self.pep_cargo))


class CuotaInmueble(models.Model):
    CONCEPTOS = [('ARRAS', 'Arras de retractación'), ('SEPARACION', 'Separación'), ('INICIAL', 'Cuota inicial'),
                 ('CUOTA', 'Cuota'), ('BANCO', 'Desembolso del banco'), ('BONO', 'Bono / subsidio')]
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
    """Cobro de una cuota: su comprobante de anticipo (IGV sobre la construcción) y el ingreso a caja / bancos. Las
    arras de retractación no llevan comprobante hasta la minuta (entonces se emite y se aplica)."""
    cuota = models.ForeignKey(CuotaInmueble, on_delete=models.CASCADE, related_name='cobros')
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    comprobante = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    movimiento = models.ForeignKey('finanzas.Movimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    aplicacion = models.ForeignKey('finanzas.Aplicacion', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', help_text='Arras aplicadas al comprobante emitido con la minuta')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']

    @property
    def es_arras(self):
        return self.cuota.concepto == 'ARRAS'


class DevolucionInmueble(models.Model):
    """Devolución al comprador que desiste: la nota de crédito del anticipo (o las arras, sin comprobante) y el pago."""
    venta = models.ForeignKey(VentaInmueble, on_delete=models.CASCADE, related_name='devoluciones')
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    nota_credito = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    movimiento = models.ForeignKey('finanzas.Movimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']


# ---------------------------------------------------------------- ejecución: adelantos y valorizaciones (OBR-13/14)
class AdelantoObra(models.Model):
    """Adelanto directo o para materiales que paga el cliente: su factura va a anticipos de clientes (122, con IGV y
    detracción) y se amortiza en las facturas de las valorizaciones (deducción del anticipo)."""
    TIPOS = [('DIRECTO', 'Adelanto directo'), ('MATERIALES', 'Adelanto para materiales')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='adelantos')
    tipo = models.CharField(max_length=10, choices=TIPOS, default='DIRECTO')
    fecha = models.DateField(default=timezone.localdate)
    monto = models.DecimalField('Monto sin IGV', max_digits=16, decimal_places=2)
    factura = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, related_name='adelantos_obra')
    reclasificada = models.BooleanField('Factura emitida como venta y pasada a anticipo', default=False,
                                        editable=False)
    glosa = models.CharField(max_length=250, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', 'fecha', 'id']
        verbose_name = 'adelanto de obra'
        verbose_name_plural = 'adelantos de obra'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.factura.numero_completo}'

    @property
    def amortizado(self):
        """Lo deducido en las facturas de valorización vigentes (sin IGV)."""
        from django.db.models import Sum
        s = self.factura.regularizaciones.filter(documento__estado='REGISTRADO').aggregate(s=Sum('subtotal'))['s']
        return -(s or D0)

    @property
    def saldo(self):
        return self.monto - self.amortizado


class Valorizacion(models.Model):
    """Valorización mensual de la obra sobre el presupuesto contractual: metrado del periodo por partida (acumulado y
    saldo), gastos generales y utilidad del presupuesto, amortización de los adelantos, reintegro por la fórmula
    polinómica (menos su deducción por los adelantos) y retención del fondo de garantía. La aprueba el supervisor y
    se factura desde aquí."""
    ESTADOS = [('BORRADOR', 'Borrador'), ('POR_APROBAR', 'Por aprobar (supervisor)'), ('APROBADA', 'Aprobada'),
               ('FACTURADA', 'Facturada'), ('ANULADA', 'Anulada')]
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, related_name='valorizaciones')
    presupuesto = models.ForeignKey(Presupuesto, on_delete=models.PROTECT, related_name='valorizaciones',
                                    verbose_name='Presupuesto contractual')
    numero = models.PositiveSmallIntegerField('N°', editable=False)
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6)
    fecha_inicio = models.DateField('Desde')
    fecha_fin = models.DateField('Hasta')
    estado = models.CharField(max_length=11, choices=ESTADOS, default='BORRADOR', editable=False)
    costo_directo = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    gastos_generales = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    utilidad = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    subtotal = models.DecimalField('Valorizado sin IGV', max_digits=16, decimal_places=2, default=D0, editable=False)
    amort_directo = models.DecimalField('Amortización del adelanto directo', max_digits=16, decimal_places=2,
                                        default=D0)
    amort_materiales = models.DecimalField('Amortización del adelanto para materiales', max_digits=16,
                                           decimal_places=2, default=D0)
    k = models.DecimalField('Coeficiente K del mes', max_digits=8, decimal_places=3, null=True, blank=True,
                            editable=False)
    reintegro = models.DecimalField('Reintegro (fórmula polinómica)', max_digits=16, decimal_places=2, default=D0)
    deduccion_reintegro = models.DecimalField('Deducción del reintegro por adelantos', max_digits=16,
                                              decimal_places=2, default=D0)
    retencion_garantia = models.DecimalField('Retención del fondo de garantía', max_digits=16, decimal_places=2,
                                             default=D0)
    base_factura = models.DecimalField('Base de la factura', max_digits=16, decimal_places=2, default=D0,
                                       editable=False)
    igv = models.DecimalField(max_digits=16, decimal_places=2, default=D0, editable=False)
    total_factura = models.DecimalField('Total de la factura', max_digits=16, decimal_places=2, default=D0,
                                        editable=False)
    supervisor = models.CharField('Supervisor / inspector que aprueba', max_length=150, blank=True)
    fecha_aprobacion = models.DateField('Aprobada por el supervisor el', null=True, blank=True)
    observacion = models.CharField('Observación del supervisor', max_length=300, blank=True)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    factura = models.ForeignKey('ventas.Venta', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                editable=False)
    aplicacion_garantia = models.ForeignKey('finanzas.Aplicacion', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='+', editable=False)
    glosa = models.CharField(max_length=250, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', '-numero']
        unique_together = [('obra', 'numero')]
        verbose_name = 'valorización'
        verbose_name_plural = 'valorizaciones'

    def __str__(self):
        return f'Valorización N° {self.numero} {self.obra.codigo} ({self.periodo[4:]}/{self.periodo[:4]})'

    @property
    def editable(self):
        return self.estado in ('BORRADOR', 'POR_APROBAR')

    @property
    def reintegro_neto(self):
        return self.reintegro - self.deduccion_reintegro

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = Valorizacion.objects.filter(obra=self.obra).order_by('-numero').first()
            self.numero = (ultimo.numero + 1) if ultimo else 1
        super().save(*args, **kwargs)


class LineaValorizacion(models.Model):
    valorizacion = models.ForeignKey(Valorizacion, on_delete=models.CASCADE, related_name='lineas')
    partida = models.ForeignKey(Partida, on_delete=models.PROTECT, related_name='valorizado')
    metrado = models.DecimalField('Metrado del periodo', max_digits=16, decimal_places=4, default=D0)
    precio = models.DecimalField(max_digits=16, decimal_places=2, default=D0)
    monto = models.DecimalField(max_digits=18, decimal_places=2, default=D0)

    class Meta:
        ordering = ['partida__subpresupuesto__codigo', 'partida__orden']


class DevolucionGarantia(models.Model):
    """Cobro del fondo de garantía que retuvo el cliente (al recibir o liquidar la obra)."""
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, related_name='devoluciones_garantia')
    fecha = models.DateField(default=timezone.localdate)
    monto = models.DecimalField(max_digits=16, decimal_places=2)
    movimiento = models.ForeignKey('finanzas.Movimiento', on_delete=models.PROTECT, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', 'fecha']


# ---------------------------------------------------------------- reajuste: fórmula polinómica (OBR-21)
class IndiceUnificado(models.Model):
    """Índice unificado de precios de la construcción (INEI) del mes, por área geográfica (1 a 6)."""
    AREAS = [(str(n), f'Área {n}') for n in range(1, 7)]
    codigo = models.CharField('Código del índice', max_length=2)
    area = models.CharField('Área geográfica', max_length=1, choices=AREAS, default='1')
    periodo = models.CharField('Mes (AAAAMM)', max_length=6)
    valor = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['-periodo', 'area', 'codigo']
        unique_together = [('codigo', 'area', 'periodo')]
        verbose_name = 'índice unificado'
        verbose_name_plural = 'índices unificados'

    def __str__(self):
        return f'IU {self.codigo} área {self.area} {self.periodo}: {self.valor}'


class FormulaPolinomica(models.Model):
    """Fórmula polinómica de reajuste del contrato (DS 011-79-VC): K = Σ coeficiente × (Ir / Io). Se importa del S10 o
    de Excel o se escribe a mano; los índices base (Io) son los del mes del presupuesto base."""
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='formulas')
    numero = models.PositiveSmallIntegerField('N°', default=1)
    nombre = models.CharField(max_length=150, blank=True)
    subpresupuesto = models.CharField('Subpresupuesto al que se aplica', max_length=10, blank=True,
                                      help_text='Código del subpresupuesto; vacío = todo el presupuesto')
    area = models.CharField('Área geográfica', max_length=1, choices=IndiceUnificado.AREAS, default='1')
    periodo_base = models.CharField('Mes de los índices base (AAAAMM)', max_length=6)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['obra', 'numero']
        unique_together = [('obra', 'numero')]
        verbose_name = 'fórmula polinómica'
        verbose_name_plural = 'fórmulas polinómicas'

    def __str__(self):
        return f'Fórmula {self.numero} {self.nombre}'.strip()

    @property
    def suma_coeficientes(self):
        return sum((m.coeficiente for m in self.monomios.all()), D0)


class Monomio(models.Model):
    """Término de la fórmula. Un monomio compuesto (0.150 × [0.6 × IU21 + 0.4 × IU43]) se registra en dos filas con
    el mismo símbolo y su coeficiente ya multiplicado (0.090 y 0.060)."""
    formula = models.ForeignKey(FormulaPolinomica, on_delete=models.CASCADE, related_name='monomios')
    simbolo = models.CharField('Símbolo', max_length=10, help_text='Ej. J, M, E, GU')
    descripcion = models.CharField('Descripción', max_length=120, blank=True)
    coeficiente = models.DecimalField(max_digits=6, decimal_places=3)
    indice = models.CharField('Código del índice (INEI)', max_length=2)

    class Meta:
        ordering = ['formula', 'id']


# ---------------------------------------------------------------- planeamiento: cronograma y avance físico (OBR-17)
class ProgramacionPartida(models.Model):
    """Metrado programado de la partida en el mes (cronograma importado del S10 o de MS Project, o escrito)."""
    partida = models.ForeignKey(Partida, on_delete=models.CASCADE, related_name='programacion')
    periodo = models.CharField('Mes (AAAAMM)', max_length=6)
    metrado = models.DecimalField(max_digits=16, decimal_places=4, default=D0)

    class Meta:
        ordering = ['partida', 'periodo']
        unique_together = [('partida', 'periodo')]


class AvancesVigentes(models.Manager):
    """Los avances anulados no cuentan en el acumulado, la curva S ni las valorizaciones (OBR-23)."""

    def get_queryset(self):
        return super().get_queryset().filter(anulado=False)


class AvancePartida(models.Model):
    """Avance físico (metrado ejecutado) de una partida en una fecha, con foto y responsable: alimenta la
    valorización. No se borra ni se corrige con negativos: se anula con su motivo."""
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='avances')
    partida = models.ForeignKey(Partida, on_delete=models.PROTECT, related_name='avances')
    fecha = models.DateField(default=timezone.localdate)
    metrado = models.DecimalField('Metrado ejecutado', max_digits=16, decimal_places=4)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    nota = models.CharField(max_length=250, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    anulado = models.BooleanField(default=False, editable=False)
    motivo_anulacion = models.CharField('Motivo de la anulación', max_length=250, blank=True, editable=False)
    anulado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', editable=False)
    anulado_en = models.DateTimeField(null=True, blank=True, editable=False)

    objects = AvancesVigentes()
    todos = models.Manager()

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'avance físico'
        verbose_name_plural = 'avances físicos'


# ---------------------------------------------------------------- mano de obra: tareo y construcción civil (OBR-18)
class Tareo(models.Model):
    """Parte diario de mano de obra de una cuadrilla: horas de cada trabajador por partida."""
    ESTADOS = [('BORRADOR', 'Borrador'), ('CERRADO', 'Cerrado')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='tareos')
    fecha = models.DateField(default=timezone.localdate)
    cuadrilla = models.CharField(max_length=60, blank=True, help_text='Ej. Cuadrilla de concreto, encofrado')
    capataz = models.ForeignKey('planillas.Trabajador', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    estado = models.CharField(max_length=8, choices=ESTADOS, default='BORRADOR', editable=False)
    observaciones = models.CharField(max_length=250, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'Tareo {self.fecha:%d/%m/%Y} {self.cuadrilla}'.strip()


class LineaTareo(models.Model):
    tareo = models.ForeignKey(Tareo, on_delete=models.CASCADE, related_name='lineas')
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, related_name='tareos')
    partida = models.ForeignKey(Partida, on_delete=models.SET_NULL, null=True, blank=True, related_name='tareos')
    horas = models.DecimalField('Horas normales', max_digits=4, decimal_places=1, default=Decimal('8'))
    horas_60 = models.DecimalField('Horas extra 60 %', max_digits=4, decimal_places=1, default=D0)
    horas_100 = models.DecimalField('Horas extra 100 %', max_digits=4, decimal_places=1, default=D0)

    class Meta:
        ordering = ['tareo', 'id']


class PlanillaCivil(models.Model):
    """Planilla semanal del régimen de construcción civil de la obra: se calcula desde los tareos con los jornales y
    porcentajes vigentes (parámetros) y su costo va al centro de costo de la obra y a sus partidas."""
    ESTADOS = [('BORRADOR', 'Borrador'), ('CERRADA', 'Cerrada'), ('PAGADA', 'Pagada'), ('ANULADA', 'Anulada')]
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, related_name='planillas_civil')
    numero = models.PositiveSmallIntegerField('Semana N°', editable=False)
    desde = models.DateField()
    hasta = models.DateField()
    estado = models.CharField(max_length=8, choices=ESTADOS, default='BORRADOR', editable=False)
    movimiento_pago = models.ForeignKey('finanzas.Movimiento', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', '-desde']
        unique_together = [('obra', 'numero')]
        verbose_name = 'planilla de construcción civil'
        verbose_name_plural = 'planillas de construcción civil'

    def __str__(self):
        return f'Planilla civil {self.obra.codigo} semana {self.numero} ({self.desde:%d/%m}–{self.hasta:%d/%m/%Y})'

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = PlanillaCivil.objects.filter(obra=self.obra).order_by('-numero').first()
            self.numero = (ultimo.numero + 1) if ultimo else 1
        super().save(*args, **kwargs)

    @property
    def periodo(self):
        return self.hasta.strftime('%Y%m')


class LineaPlanillaCivil(models.Model):
    planilla = models.ForeignKey(PlanillaCivil, on_delete=models.CASCADE, related_name='lineas')
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, related_name='+')
    categoria = models.CharField(max_length=8)
    dias = models.DecimalField(max_digits=4, decimal_places=2, default=D0)
    horas = models.DecimalField(max_digits=6, decimal_places=1, default=D0)
    horas_60 = models.DecimalField(max_digits=6, decimal_places=1, default=D0)
    horas_100 = models.DecimalField(max_digits=6, decimal_places=1, default=D0)
    jornal = models.DecimalField(max_digits=10, decimal_places=2, default=D0)
    basico = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    dominical = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    buc = models.DecimalField('BUC', max_digits=12, decimal_places=2, default=D0)
    extras = models.DecimalField('Horas extra', max_digits=12, decimal_places=2, default=D0)
    movilidad = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    pension = models.DecimalField('ONP / AFP', max_digits=12, decimal_places=2, default=D0)
    conafovicer = models.DecimalField('CONAFOVICER', max_digits=12, decimal_places=2, default=D0)
    neto = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    essalud = models.DecimalField('EsSalud', max_digits=12, decimal_places=2, default=D0)
    sctr = models.DecimalField('SCTR', max_digits=12, decimal_places=2, default=D0)
    cts = models.DecimalField('CTS', max_digits=12, decimal_places=2, default=D0)
    vacaciones = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    gratificacion = models.DecimalField('Gratificación', max_digits=12, decimal_places=2, default=D0)
    costo = models.DecimalField('Costo total', max_digits=12, decimal_places=2, default=D0)
    partidas = models.JSONField(default=dict, help_text='{partida_id: horas} del tareo')

    class Meta:
        ordering = ['planilla', 'trabajador__apellido_paterno']

    @property
    def ingresos(self):
        return self.basico + self.dominical + self.buc + self.extras + self.movilidad


# ---------------------------------------------------------------- equipos: parte diario (OBR-19)
class ParteEquipo(models.Model):
    """Parte diario del equipo en la obra (horómetro, horas trabajadas y en espera, combustible, operador y
    partida): su costo (horas × tarifa) se compara con la meta de equipos del APU; el horómetro pasa a Flota."""
    PROPIEDADES = [('PROPIO', 'Propio'), ('ALQUILADO', 'Alquilado')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='partes_equipo')
    numero = models.CharField('N° de parte', max_length=20, blank=True)
    fecha = models.DateField(default=timezone.localdate)
    equipo = models.ForeignKey('produccion.Equipo', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                               help_text='Equipo propio o alquilado registrado en Flota')
    descripcion = models.CharField('Equipo (si no está en Flota)', max_length=120, blank=True)
    propiedad = models.CharField(max_length=9, choices=PROPIEDADES, default='PROPIO')
    proveedor = models.ForeignKey('core.Tercero', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                  verbose_name='Proveedor del alquiler')
    partida = models.ForeignKey(Partida, on_delete=models.SET_NULL, null=True, blank=True, related_name='partes_equipo')
    operador = models.CharField(max_length=120, blank=True)
    horometro_inicio = models.DecimalField('Horómetro inicial', max_digits=12, decimal_places=1, null=True,
                                           blank=True)
    horometro_fin = models.DecimalField('Horómetro final', max_digits=12, decimal_places=1, null=True, blank=True)
    horas = models.DecimalField('Horas trabajadas', max_digits=6, decimal_places=1, default=D0)
    horas_espera = models.DecimalField('Horas en espera (standby)', max_digits=6, decimal_places=1, default=D0)
    combustible = models.DecimalField('Combustible (galones)', max_digits=10, decimal_places=2, default=D0)
    tarifa = models.DecimalField('Tarifa hora-máquina S/', max_digits=12, decimal_places=2, default=D0)
    tarifa_espera = models.DecimalField('Tarifa en espera S/', max_digits=12, decimal_places=2, default=D0)
    costo = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    compra = models.ForeignKey('compras.Compra', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                               verbose_name='Factura del alquiler')
    observacion = models.CharField(max_length=200, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'parte diario de equipo'
        verbose_name_plural = 'partes diarios de equipo'

    def __str__(self):
        return f'Parte {self.numero or self.pk} {self.nombre_equipo} {self.fecha:%d/%m/%Y}'

    @property
    def nombre_equipo(self):
        return str(self.equipo) if self.equipo_id else self.descripcion

    def save(self, *args, **kwargs):
        from core.models import r2
        self.costo = r2(self.horas * self.tarifa + self.horas_espera * self.tarifa_espera)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------- subcontratos (OBR-20)
class Subcontrato(models.Model):
    """Contrato con un subcontratista por partidas del presupuesto (precios unitarios o suma alzada), con adelanto,
    fondo de garantía y detracción. Se valoriza cada mes y cada valorización genera sus conformidades (HES)."""
    MODALIDADES = [('PRECIOS_UNITARIOS', 'Precios unitarios'), ('SUMA_ALZADA', 'Suma alzada')]
    ESTADOS = [('BORRADOR', 'Borrador'), ('VIGENTE', 'Vigente (con orden de servicio)'), ('LIQUIDADO', 'Liquidado'),
               ('ANULADO', 'Anulado')]
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, related_name='subcontratos')
    numero = models.CharField('N°', max_length=20, editable=False)
    proveedor = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, related_name='+',
                                  verbose_name='Subcontratista')
    descripcion = models.CharField('Objeto del subcontrato', max_length=250)
    modalidad = models.CharField(max_length=17, choices=MODALIDADES, default='PRECIOS_UNITARIOS')
    fecha = models.DateField(default=timezone.localdate)
    adelanto_pct = models.DecimalField('Adelanto %', max_digits=5, decimal_places=2, default=D0)
    garantia_pct = models.DecimalField('Fondo de garantía %', max_digits=5, decimal_places=2, default=Decimal('5'))
    detraccion = models.BooleanField('Sujeto a detracción (contratos de construcción)', default=True)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='BORRADOR', editable=False)
    orden_compra = models.ForeignKey('compras.OrdenCompra', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False, verbose_name='Orden de servicio')
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', '-fecha', '-id']

    def __str__(self):
        return f'Subcontrato {self.numero} {self.proveedor.nombre}'

    def save(self, *args, **kwargs):
        if not self.numero:
            from core.models import Serie
            _, numero = Serie.siguiente('SBC', 'SC')
            self.numero = f'SC-{int(numero):05d}'
        super().save(*args, **kwargs)

    @property
    def monto(self):
        return sum((l.monto for l in self.lineas.all()), D0)


class LineaSubcontrato(models.Model):
    subcontrato = models.ForeignKey(Subcontrato, on_delete=models.CASCADE, related_name='lineas')
    partida = models.ForeignKey(Partida, on_delete=models.SET_NULL, null=True, blank=True, related_name='subcontratado')
    descripcion = models.CharField('Descripción', max_length=250)
    unidad = models.CharField(max_length=12, blank=True)
    metrado = models.DecimalField(max_digits=16, decimal_places=4, default=D0)
    precio = models.DecimalField('Precio unitario sin IGV', max_digits=16, decimal_places=4, default=D0)
    oc_item = models.ForeignKey('compras.OrdenCompraItem', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', editable=False)

    class Meta:
        ordering = ['subcontrato', 'id']

    @property
    def monto(self):
        from core.models import r2
        return r2(self.metrado * self.precio)


class ValorizacionSubcontrato(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('APROBADA', 'Aprobada (HES generadas)'), ('ANULADA', 'Anulada')]
    subcontrato = models.ForeignKey(Subcontrato, on_delete=models.PROTECT, related_name='valorizaciones')
    numero = models.PositiveSmallIntegerField('N°', editable=False)
    periodo = models.CharField('Periodo (AAAAMM)', max_length=6)
    estado = models.CharField(max_length=8, choices=ESTADOS, default='BORRADOR', editable=False)
    bruto = models.DecimalField('Valorizado sin IGV', max_digits=16, decimal_places=2, default=D0, editable=False)
    amortizacion = models.DecimalField('Amortización del adelanto', max_digits=16, decimal_places=2, default=D0)
    garantia = models.DecimalField('Fondo de garantía retenido', max_digits=16, decimal_places=2, default=D0)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['subcontrato', '-numero']
        unique_together = [('subcontrato', 'numero')]

    def __str__(self):
        return f'Valorización {self.numero} del {self.subcontrato}'

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = ValorizacionSubcontrato.objects.filter(subcontrato=self.subcontrato).order_by('-numero').first()
            self.numero = (ultimo.numero + 1) if ultimo else 1
        super().save(*args, **kwargs)

    @property
    def neto(self):
        return self.bruto - self.amortizacion - self.garantia


class LineaValSubcontrato(models.Model):
    valorizacion = models.ForeignKey(ValorizacionSubcontrato, on_delete=models.CASCADE, related_name='lineas')
    linea = models.ForeignKey(LineaSubcontrato, on_delete=models.PROTECT, related_name='valorizado')
    metrado = models.DecimalField('Metrado del periodo', max_digits=16, decimal_places=4, default=D0)
    monto = models.DecimalField(max_digits=16, decimal_places=2, default=D0)
    hes = models.ForeignKey('compras.ConformidadServicio', on_delete=models.SET_NULL, null=True, blank=True,
                            related_name='+', editable=False)

    class Meta:
        ordering = ['valorizacion', 'id']

# ---------------------------------------------------------------- servicios profesionales por proyecto (SRV-01 a 06)
class EntregableProyecto(models.Model):
    """Entregable del contrato de servicios (anteproyecto, proyecto, expediente, supervisión mensual): se entrega, el
    cliente da su conformidad y recién entonces se factura (con la amortización del adelanto). El atraso calcula la
    penalidad."""
    ESTADOS = [('PENDIENTE', 'Pendiente'), ('ENTREGADO', 'Entregado al cliente'), ('OBSERVADO', 'Observado por el cliente'),
               ('CONFORME', 'Conforme (aprobado por el cliente)'), ('FACTURADO', 'Facturado'), ('ANULADO', 'Anulado')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='entregables', verbose_name='Proyecto')
    numero = models.PositiveSmallIntegerField('N°', default=1)
    nombre = models.CharField(max_length=150, help_text='Ej. Anteproyecto, Expediente técnico, Supervisión de marzo')
    porcentaje = models.DecimalField('% del contrato', max_digits=5, decimal_places=2, default=D0)
    monto = models.DecimalField('Monto sin IGV', max_digits=14, decimal_places=2, default=D0,
                                blank=True, help_text='Vacío o 0 = el % del monto contratado')
    fecha_comprometida = models.DateField('Fecha comprometida')
    horas_presupuestadas = models.DecimalField(max_digits=10, decimal_places=2, default=D0)
    penalidad_diaria_pct = models.DecimalField('Penalidad por día de atraso (% del monto)', max_digits=6,
                                               decimal_places=3, default=Decimal('0.100'))
    penalidad_maxima_pct = models.DecimalField('Penalidad máxima (% del monto)', max_digits=5, decimal_places=2,
                                               default=Decimal('10'))
    estado = models.CharField(max_length=9, choices=ESTADOS, default='PENDIENTE', editable=False)
    fecha_entrega = models.DateField(null=True, blank=True, editable=False)
    conformidad_fecha = models.DateField(null=True, blank=True, editable=False)
    conformidad_por = models.CharField('Conformidad de', max_length=150, blank=True, editable=False)
    observacion = models.CharField(max_length=250, blank=True, editable=False)
    penalidad = models.DecimalField(max_digits=14, decimal_places=2, default=D0, editable=False)
    factura = models.ForeignKey('ventas.Venta', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                editable=False)

    class Meta:
        ordering = ['obra', 'numero']
        verbose_name = 'entregable del proyecto'
        verbose_name_plural = 'entregables del proyecto'

    def __str__(self):
        return f'{self.numero}. {self.nombre}'

    @property
    def valor(self):
        """Monto sin IGV del entregable (el propio o el % del contratado sin IGV)."""
        if self.monto:
            return self.monto
        from core.models import Empresa
        base = self.obra.monto_contratado / (1 + Empresa.actual().igv_tasa / 100) if self.obra.monto_contratado else D0
        return (base * self.porcentaje / 100).quantize(Decimal('0.01'))

    @property
    def dias_atraso(self):
        fin = self.fecha_entrega or (timezone.localdate() if self.estado == 'PENDIENTE' else None)
        return max((fin - self.fecha_comprometida).days, 0) if fin else 0


class ParteHoras(models.Model):
    """Horas de una persona en un proyecto en la semana (y entregable): las aprueba el jefe de proyecto (el residente
    de la obra) y se costean con el costo hora de la persona."""
    ESTADOS = [('BORRADOR', 'Borrador'), ('ENVIADO', 'Enviado'), ('APROBADO', 'Aprobado'), ('RECHAZADO', 'Rechazado')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='partes_horas', verbose_name='Proyecto')
    entregable = models.ForeignKey(EntregableProyecto, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='partes')
    semana = models.DateField('Semana (lunes)')
    trabajador = models.ForeignKey('planillas.Trabajador', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+', help_text='Personal en planilla')
    persona = models.CharField('o independiente', max_length=120, blank=True,
                               help_text='Especialista con recibo por honorarios')
    horas = models.DecimalField(max_digits=6, decimal_places=2)
    descripcion = models.CharField('Qué se hizo', max_length=250, blank=True)
    costo_hora = models.DecimalField('Costo hora S/', max_digits=10, decimal_places=2, default=D0)
    estado = models.CharField(max_length=9, choices=ESTADOS, default='BORRADOR', editable=False)
    motivo_rechazo = models.CharField(max_length=200, blank=True, editable=False)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', editable=False)
    # SRV-10: asiento que llevó el costo de las horas del personal en planilla al centro de costo del proyecto
    asiento_distribucion = models.ForeignKey('contabilidad.Asiento', on_delete=models.SET_NULL, null=True,
                                             blank=True, related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-semana', 'obra', 'id']
        verbose_name = 'parte de horas'
        verbose_name_plural = 'partes de horas'

    @property
    def nombre_persona(self):
        return str(self.trabajador) if self.trabajador_id else self.persona

    @property
    def costo(self):
        return (self.horas * self.costo_hora).quantize(Decimal('0.01'))


class GarantiaContrato(models.Model):
    """Carta fianza o póliza del contrato (fiel cumplimiento, adelanto, seriedad de oferta) con su vencimiento."""
    TIPOS = [('FIEL_CUMPLIMIENTO', 'Fiel cumplimiento'), ('ADELANTO', 'Por adelanto'), ('SERIEDAD', 'Seriedad de oferta'),
             ('OTRA', 'Otra')]
    ESTADOS = [('VIGENTE', 'Vigente'), ('RENOVADA', 'Renovada'), ('DEVUELTA', 'Devuelta'), ('EJECUTADA', 'Ejecutada')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='garantias', verbose_name='Obra o proyecto')
    tipo = models.CharField(max_length=17, choices=TIPOS, default='FIEL_CUMPLIMIENTO')
    entidad = models.CharField('Banco o aseguradora', max_length=120)
    numero = models.CharField('N° de carta / póliza', max_length=60, blank=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    emision = models.DateField('Emisión')
    vencimiento = models.DateField()
    estado = models.CharField(max_length=9, choices=ESTADOS, default='VIGENTE')
    notas = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['vencimiento']
        verbose_name = 'garantía del contrato'
        verbose_name_plural = 'garantías y cartas fianza'

    @property
    def por_vencer(self):
        from datetime import timedelta
        return self.estado == 'VIGENTE' and self.vencimiento <= timezone.localdate() + timedelta(days=15)


RUBROS_PROYECTO = [('HORAS', 'Horas del personal'), ('ESPECIALISTAS', 'Especialistas independientes'),
                   ('VIAJES', 'Viajes y movilidad'), ('OFICINA', 'Gastos de oficina'), ('OTROS', 'Otros gastos')]


class RubroProyecto(models.Model):
    """Presupuesto del proyecto de servicios por rubro (SRV-11): se compara con lo comprometido (OC/OS aprobadas sin
    factura) y lo gastado (partes de horas, recibos, facturas, rendiciones y reparto al centro del proyecto)."""
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='rubros', verbose_name='Proyecto')
    rubro = models.CharField(max_length=13, choices=RUBROS_PROYECTO)
    monto = models.DecimalField('Presupuesto S/ (sin IGV)', max_digits=14, decimal_places=2, default=D0)
    cuentas = models.CharField('Cuentas del gasto', max_length=100, blank=True,
                               help_text='Prefijos de cuenta separados por espacio (ej. 631 6371). Vacío = los del rubro')

    class Meta:
        ordering = ['obra', 'id']
        unique_together = [('obra', 'rubro')]
        verbose_name = 'rubro del presupuesto del proyecto'
        verbose_name_plural = 'presupuesto del proyecto por rubro'

    def __str__(self):
        return self.get_rubro_display()


class CierreServicioProyecto(models.Model):
    """Servicio en proceso y el ingreso por avance (NIIF 15) del proyecto al cierre de un mes (SRV-06): el asiento
    registra la diferencia con el mes anterior."""
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='cierres_servicio')
    periodo = models.CharField('Mes (AAAAMM)', max_length=6)
    costo_proceso = models.DecimalField('Costo en proceso (sin facturar)', max_digits=14, decimal_places=2, default=D0)
    ingreso_devengado = models.DecimalField('Ingreso por avance no facturado', max_digits=14, decimal_places=2,
                                            default=D0)
    asiento = models.ForeignKey('contabilidad.Asiento', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-periodo', 'obra']
        unique_together = [('obra', 'periodo')]


# ---------------------------------------------------------------- gestión del contrato y del proyecto (v1.86)
class AdendaContrato(models.Model):
    """HR-03: adenda del contrato (monto y/o plazo) con su motivo y resolución. Al aprobarla cambia el monto
    contratado y el plazo de la obra; la versión del presupuesto (adicional o deductivo) es la que se valoriza."""
    TIPOS = [('MONTO', 'Monto (adicional o reducción)'), ('PLAZO', 'Ampliación de plazo'),
             ('MONTO_PLAZO', 'Monto y plazo'), ('OTRO', 'Otra modificación (sin monto ni plazo)')]
    ESTADOS = [('BORRADOR', 'Por aprobar'), ('APROBADA', 'Aprobada'), ('ANULADA', 'Anulada')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='adendas')
    numero = models.PositiveSmallIntegerField('N°', editable=False)
    fecha = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=11, choices=TIPOS, default='MONTO')
    monto = models.DecimalField('Variación del monto (con IGV)', max_digits=16, decimal_places=2, default=D0,
                                help_text='Negativo = reducción o deductivo')
    plazo_dias = models.IntegerField('Variación del plazo (días)', default=0)
    motivo = models.CharField(max_length=300)
    resolucion = models.CharField('Resolución / documento', max_length=120, blank=True)
    fecha_resolucion = models.DateField('Fecha de la resolución', null=True, blank=True)
    presupuesto = models.ForeignKey(Presupuesto, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    verbose_name='Versión del presupuesto (adicional / deductivo)')
    estado = models.CharField(max_length=8, choices=ESTADOS, default='BORRADOR', editable=False)
    monto_anterior = models.DecimalField(max_digits=16, decimal_places=2, null=True, editable=False)
    plazo_anterior = models.IntegerField(null=True, editable=False)
    motivo_anulacion = models.CharField(max_length=250, blank=True, editable=False)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', 'numero']
        unique_together = [('obra', 'numero')]
        verbose_name = 'adenda del contrato'
        verbose_name_plural = 'adendas del contrato'

    def __str__(self):
        return f'Adenda N° {self.numero} {self.obra.codigo}'

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = AdendaContrato.objects.filter(obra=self.obra).order_by('-numero').first()
            self.numero = (ultimo.numero + 1) if ultimo else 1
        super().save(*args, **kwargs)


class EventoContrato(models.Model):
    """HR-04: penalidades, observaciones, reclamos y controversias del contrato: con los atrasos de pago del cliente
    forman su riesgo contractual."""
    TIPOS = [('PENALIDAD', 'Penalidad aplicada'), ('OBSERVACION', 'Observación del cliente o la supervisión'),
             ('RECLAMO', 'Reclamo'), ('CONTROVERSIA', 'Controversia / arbitraje'), ('OTRO', 'Otro')]
    GRAVEDADES = [('BAJA', 'Baja'), ('MEDIA', 'Media'), ('ALTA', 'Alta')]
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='eventos_contrato')
    fecha = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=12, choices=TIPOS, default='OBSERVACION')
    gravedad = models.CharField(max_length=5, choices=GRAVEDADES, default='MEDIA')
    monto = models.DecimalField('Monto (penalidad o reclamo) S/', max_digits=14, decimal_places=2, default=D0)
    descripcion = models.CharField('Descripción', max_length=300)
    resuelto = models.BooleanField(default=False)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'evento del contrato'
        verbose_name_plural = 'eventos del contrato'


class EscenarioCronograma(models.Model):
    """HR-06: foto del cronograma (metrado programado por partida y mes) para comparar alternativas y volver a una."""
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='escenarios')
    presupuesto = models.ForeignKey(Presupuesto, on_delete=models.CASCADE, related_name='escenarios')
    nombre = models.CharField(max_length=120)
    descripcion = models.CharField('Descripción', max_length=250, blank=True)
    datos = models.JSONField(default=dict, help_text='{clave de partida: {AAAAMM: metrado}}')
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['obra', '-creado', '-id']
        verbose_name = 'escenario del cronograma'
        verbose_name_plural = 'escenarios del cronograma'

    def __str__(self):
        return self.nombre


class SocioConsorcio(models.Model):
    """SRV-05: socio del consorcio de la obra con su participación: a cada uno se le atribuyen ingresos, costos e IGV."""
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name='socios')
    tercero = models.ForeignKey('core.Tercero', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                verbose_name='Socio (contacto)')
    nombre = models.CharField('Razón social', max_length=200, blank=True, help_text='Si no está en contactos')
    ruc = models.CharField('RUC', max_length=11, blank=True)
    participacion_pct = models.DecimalField('Participación %', max_digits=5, decimal_places=2)
    es_la_empresa = models.BooleanField('Es nuestra empresa', default=False)
    operador = models.BooleanField('Operador (lleva la contabilidad del consorcio)', default=False)

    class Meta:
        ordering = ['obra', '-es_la_empresa', '-participacion_pct', 'id']
        verbose_name = 'socio del consorcio'
        verbose_name_plural = 'socios del consorcio'

    def __str__(self):
        return self.tercero.nombre if self.tercero_id else self.nombre


class EvaluacionSubcontrato(models.Model):
    """HR-11: evaluación del subcontratista al cerrar su subcontrato (plazo, calidad y seguridad de 1 a 5)."""
    NOTAS = [(1, '1 · Deficiente'), (2, '2 · Regular'), (3, '3 · Bueno'), (4, '4 · Muy bueno'), (5, '5 · Excelente')]
    subcontrato = models.OneToOneField(Subcontrato, on_delete=models.CASCADE, related_name='evaluacion')
    fecha = models.DateField(default=timezone.localdate)
    plazo = models.PositiveSmallIntegerField('Cumplimiento del plazo', choices=NOTAS, default=3)
    calidad = models.PositiveSmallIntegerField(choices=NOTAS, default=3)
    seguridad = models.PositiveSmallIntegerField('Seguridad (SST)', choices=NOTAS, default=3)
    comentario = models.CharField(max_length=300, blank=True)
    evaluado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'evaluación de subcontratista'
        verbose_name_plural = 'evaluaciones de subcontratistas'

    @property
    def puntaje(self):
        return (Decimal(self.plazo + self.calidad + self.seguridad) / 3).quantize(Decimal('0.1'))
