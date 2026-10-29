"""Reglas de manufactura (al estilo SAP PP/CO):

- Versión de fabricación: receta + hoja de ruta por rango de lote y vigencia; la orden y el MRP la eligen solos.
- Costo estándar por periodo: se calcula y se libera; liberado queda fijo y las órdenes se comparan contra él.
- Variaciones de cada orden separadas por tipo (precio y cantidad de materiales, eficiencia y tarifa).
- MRP multinivel: demanda (pedidos, plan) -> necesidades netas -> órdenes planificadas de fabricar y comprar.
- Absorción: mano de obra y CIF absorbidos por las órdenes frente al gasto real de cada centro de costo."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Producto, Serie, r2

from .models import (ConsumoOrden, CostoEstandar, CorridaMRP, HoraHombre, HoraOrden, ListaMateriales,
                     OrdenProduccion, PlanDemanda, PropuestaMRP, SalidaOrden, VariacionOrden, VersionFabricacion)

D0 = Decimal('0')
D4 = Decimal('0.0001')


class ErrorProduccion(Exception):
    pass


def periodo_de(fecha):
    return fecha.strftime('%Y%m')


def fecha_de_periodo(periodo):
    """Fecha con que se eligen las versiones vigentes de un periodo: hoy en el mes en curso, el primer día en meses
    futuros y el último día en meses pasados."""
    from contabilidad.centralizar import _fin_mes
    inicio, fin, hoy = date(int(periodo[:4]), int(periodo[4:]), 1), _fin_mes(periodo), timezone.localdate()
    return fin if fin < hoy else max(inicio, hoy)


# ---------------------------------------------------------------- versiones de fabricación
def version_para(producto, cantidad, fecha=None):
    """Versión vigente para fabricar `cantidad` en `fecha`: la de rango de lote más específico."""
    fecha = fecha or timezone.localdate()
    candidatas = (VersionFabricacion.objects.filter(producto=producto, activa=True)
                  .select_related('lista', 'hoja').order_by('-lote_min', '-vigente_desde', 'codigo'))
    return next((v for v in candidatas if v.aplica(cantidad, fecha)), None)


def version_vigente(producto, fecha=None):
    """Versión para costear o planificar sin cantidad: la que aplica a su propio lote de costeo."""
    fecha = fecha or timezone.localdate()
    for v in (VersionFabricacion.objects.filter(producto=producto, activa=True).select_related('lista', 'hoja')
              .order_by('-vigente_desde', 'codigo')):
        if v.aplica(v.lote_estandar, fecha) or v.aplica(max(v.lote_min, Decimal('1')), fecha):
            return v
    return None


def receta_vigente(producto):
    v = version_vigente(producto)
    return v.lista if v else ListaMateriales.objects.filter(producto=producto, estado='APROBADA').order_by(
        '-creado').first()


def _version_de(origen):
    if isinstance(origen, VersionFabricacion):
        return origen
    return origen.versiones.filter(activa=True).select_related('hoja').first()


# ---------------------------------------------------------------- costo estándar
def estandar_de(producto, fecha=None):
    """Estándar liberado vigente: el del periodo de la fecha o, si no hay, el último liberado anterior."""
    periodo = periodo_de(fecha or timezone.localdate())
    return (CostoEstandar.objects.filter(producto=producto, estado='LIBERADO', periodo__lte=periodo)
            .order_by('-periodo').first())


def costo_insumo(producto, visitados=None, periodo=None, en_corrida=None):
    """Precio estándar de un insumo: fabricado -> su estándar (liberado o calculado en la misma corrida);
    comprado -> costo promedio o, si aún no tiene, precio de compra referencial."""
    if en_corrida and producto.pk in en_corrida:
        return en_corrida[producto.pk]
    visitados = set(visitados or ())
    if producto.pk not in visitados and producto.clase in ('SEMIELABORADO', 'PRODUCTO_TERMINADO'):
        liberado = estandar_de(producto, date(int(periodo[:4]), int(periodo[4:]), 1) if periodo else None)
        if liberado:
            return liberado.unitario
        origen = version_vigente(producto) or ListaMateriales.objects.filter(
            producto=producto, estado='APROBADA').order_by('-creado').first()
        if origen:
            return hoja_costos(origen, visitados | {producto.pk}, periodo=periodo, en_corrida=en_corrida)['unitario']
    if producto.costo_promedio:
        return producto.costo_promedio
    return producto.precio_compra or D0


def hoja_costos(origen, visitados=None, cantidad=None, periodo=None, en_corrida=None):
    """Hoja de costo estándar de una versión de fabricación (o de una receta) para `cantidad` unidades.

    Materiales con merma a su precio estándar; actividades de la hoja de ruta (preparación + ejecución, según la
    eficiencia del puesto) a las tarifas de mano de obra y máquina/CIF."""
    version = _version_de(origen)
    lista = version.lista if version else origen
    visitados = set(visitados or ()) | {lista.producto_id}
    unidades = cantidad or (version.lote_estandar if version else lista.cantidad_base) or Decimal('1')
    factor = unidades / lista.cantidad_base if lista.cantidad_base else Decimal('1')
    materiales, horas = [], []
    for c in lista.componentes.select_related('producto'):
        cant = (c.cantidad_con_merma * factor).quantize(D4)
        costo = costo_insumo(c.producto, visitados, periodo, en_corrida)
        materiales.append({'producto': c.producto, 'cantidad': cant, 'merma': c.merma, 'costo': costo,
                           'valor': r2(cant * costo)})
    if version and version.hoja_id:
        actividades = [(o.centro, o.secuencia, o.descripcion, o.horas_para(unidades), o.maquinistas, o.ayudantes)
                       for o in version.hoja.operaciones.select_related('centro')]
    else:  # recetas anteriores a las hojas de ruta: horas por lote dentro de la receta (una persona)
        actividades = [(o.centro, None, o.descripcion, (o.horas * factor).quantize(Decimal('0.01')), 1, 0)
                       for o in lista.operaciones.select_related('centro')]
    for centro, secuencia, descripcion, h, maquinistas, ayudantes in actividades:
        # máquina y CIF por hora máquina (con los drivers del puesto: kWh × tarifa por hora, MAN-22); mano de obra
        # por hora hombre (horas máquina × personas)
        mo = r2(h * (maquinistas + ayudantes) * centro.costo_hora_mo)
        maq, cif = r2(h * centro.costo_hora_maquina), r2(h * (centro.costo_hora_cif + centro.cif_drivers_hora))
        horas.append({'centro': centro, 'secuencia': secuencia, 'descripcion': descripcion, 'horas': h, 'mo': mo,
                      'maquina': maq, 'cif': cif, 'total': mo + maq + cif, 'maquinistas': maquinistas,
                      'ayudantes': ayudantes})
    tot_mat = sum((m['valor'] for m in materiales), D0)
    tot_mo = sum((h['mo'] for h in horas), D0)
    tot_maq = sum((h['maquina'] for h in horas), D0)
    tot_cif = sum((h['cif'] for h in horas), D0)
    total = tot_mat + tot_mo + tot_maq + tot_cif
    # NIC 2 párr. 14: los subproductos se valoran a su valor fijo; los coproductos reciben su % del costo conjunto
    subproductos, conjunto = [], total
    for s in lista.subproductos.select_related('producto').filter(tipo='SUBPRODUCTO'):
        cant = (s.cantidad * factor).quantize(D4)
        valor = min(r2(cant * s.valor_unitario), conjunto)
        conjunto -= valor
        subproductos.append({'producto': s.producto, 'tipo': s.tipo, 'cantidad': cant, 'valor': valor})
    resto = conjunto
    for s in lista.subproductos.select_related('producto').filter(tipo='COPRODUCTO'):
        cant = (s.cantidad * factor).quantize(D4)
        valor = r2(conjunto * s.participacion / 100)
        resto -= valor
        subproductos.append({'producto': s.producto, 'tipo': s.tipo, 'cantidad': cant, 'valor': valor})
    principal = max(resto, D0)
    return {'lista': lista, 'version': version, 'cantidad': unidades, 'materiales': materiales, 'horas': horas,
            'tot_materiales': tot_mat, 'tot_mano_obra': tot_mo, 'tot_maquina': tot_maq, 'tot_cif': tot_cif,
            'tot_conversion': tot_mo + tot_maq + tot_cif, 'total': total, 'subproductos': subproductos,
            'tot_subproductos': total - principal, 'principal': principal,
            'unitario': (principal / unidades).quantize(D4) if unidades else D0}


def _detalle_unitario(hoja):
    """Estándar por UNA unidad (lo que se guarda y con lo que se calculan las variaciones)."""
    n = hoja['cantidad'] or Decimal('1')
    return {
        'materiales': [{'producto': m['producto'].pk, 'nombre': m['producto'].nombre,
                        'cantidad': str((m['cantidad'] / n).quantize(Decimal('0.000001'))), 'precio': str(m['costo'])}
                       for m in hoja['materiales']],
        'actividades': [{'centro': h['centro'].pk, 'nombre': str(h['centro']), 'secuencia': h['secuencia'],
                         'horas': str((h['horas'] / n).quantize(Decimal('0.000001'))),
                         'personas': h['maquinistas'] + h['ayudantes'], 'mo': str(h['centro'].costo_hora_mo),
                         'maquina': str(h['centro'].costo_hora_maquina),
                         'cif': str(h['centro'].costo_hora_cif + h['centro'].cif_drivers_hora)}
                        for h in hoja['horas']],
        'subproductos': str((hoja['tot_subproductos'] / n).quantize(D4)),
        'lote': str(n),
    }


def _niveles():
    """{producto_id: nivel} (0 = producto final). Un insumo queda en el nivel más bajo en que aparece, así el MRP y
    el costeo procesan primero lo que se fabrica arriba (MRP) o abajo (costeo, en orden inverso)."""
    hijos = defaultdict(set)
    for v in VersionFabricacion.objects.filter(activa=True).select_related('lista'):
        for c in v.lista.componentes.all():
            hijos[v.producto_id].add(c.producto_id)
    nivel = defaultdict(int)
    for _ in range(25):  # suficiente para recetas reales; corta ciclos
        cambio = False
        for padre, componentes in hijos.items():
            for c in componentes:
                if nivel[c] < nivel[padre] + 1:
                    nivel[c] = nivel[padre] + 1
                    cambio = True
        if not cambio:
            break
    return nivel


def calcular_estandar(periodo, usuario=None, productos=None):
    """Corrida de costeo del periodo (marca): de abajo hacia arriba en la estructura, así cada semielaborado se
    costea antes que el producto que lo usa. Los estándares liberados no se tocan."""
    referencia = fecha_de_periodo(periodo)
    niveles = _niveles()
    versiones = []
    for v in VersionFabricacion.objects.filter(activa=True).select_related('producto', 'lista', 'hoja'):
        if productos and v.producto_id not in productos:
            continue
        if version_vigente(v.producto, referencia) == v:
            versiones.append(v)
    versiones.sort(key=lambda v: -niveles[v.producto_id])
    en_corrida, resultado = {}, []
    with transaction.atomic():
        for v in versiones:
            existente = CostoEstandar.objects.filter(producto=v.producto, periodo=periodo).first()
            if existente and existente.estado == 'LIBERADO':
                en_corrida[v.producto_id] = existente.unitario
                continue
            hoja = hoja_costos(v, periodo=periodo, en_corrida=en_corrida)
            n = hoja['cantidad']
            ce, _ = CostoEstandar.objects.update_or_create(producto=v.producto, periodo=periodo, defaults={
                'version': v, 'lote_costeo': n, 'materiales': (hoja['tot_materiales'] / n).quantize(D4),
                'mano_obra': (hoja['tot_mano_obra'] / n).quantize(D4),
                'maquina': (hoja['tot_maquina'] / n).quantize(D4), 'cif': (hoja['tot_cif'] / n).quantize(D4),
                'unitario': hoja['unitario'], 'detalle': _detalle_unitario(hoja), 'estado': 'CALCULADO',
                'usuario': usuario if usuario and usuario.is_authenticated else None})
            en_corrida[v.producto_id] = ce.unitario
            resultado.append(ce)
    return resultado


def liberar_estandar(periodo, usuario=None):
    """Fija los estándares calculados del periodo: desde ahora las órdenes se comparan contra ellos."""
    n = CostoEstandar.objects.filter(periodo=periodo, estado='CALCULADO').update(estado='LIBERADO')
    from core.auditoria import registrar
    for ce in CostoEstandar.objects.filter(periodo=periodo, estado='LIBERADO'):
        registrar('MODIFICAR', ce, {'Estado': ['Calculado', 'Liberado'], 'Costo unitario': str(ce.unitario)})
    return n


# ---------------------------------------------------------------- órdenes de producción
def explotar(orden):
    """Consumos y horas planificados de la orden según su versión de fabricación (receta + hoja de ruta)."""
    version = orden.version or version_para(orden.producto, orden.cantidad, orden.fecha)
    if version and not orden.version_id:
        orden.version, orden.lista = version, version.lista
        orden.save(update_fields=['version', 'lista'])
    lista = version.lista if version else orden.lista
    factor = orden.cantidad / lista.cantidad_base if lista.cantidad_base else Decimal('1')
    orden.consumos.all().delete()
    orden.horas.all().delete()
    orden.salidas.all().delete()
    for c in lista.componentes.all():
        cant = (c.cantidad_con_merma * factor).quantize(D4)
        ConsumoOrden.objects.create(orden=orden, producto_id=c.producto_id, cantidad_plan=cant, cantidad_real=cant,
                                    almacen_id=c.almacen_id, operacion=c.operacion, modo=c.modo_consumo)
    for s in lista.subproductos.all():
        cant = (s.cantidad * factor).quantize(D4)
        SalidaOrden.objects.create(orden=orden, producto_id=s.producto_id, tipo=s.tipo, cantidad_plan=cant,
                                   cantidad_real=cant, valor_unitario=s.valor_unitario, participacion=s.participacion)
    if version and version.hoja_id:
        for o in version.hoja.operaciones.select_related('centro'):
            h = o.horas_para(orden.cantidad)
            HoraOrden.objects.create(orden=orden, centro_id=o.centro_id, secuencia=o.secuencia,
                                     descripcion=f'{o.secuencia} {o.descripcion}', horas_plan=h, horas_real=h,
                                     maquinistas=o.maquinistas, ayudantes=o.ayudantes)
    else:
        for o in lista.operaciones.all():
            h = (o.horas * factor).quantize(Decimal('0.01'))
            HoraOrden.objects.create(orden=orden, centro_id=o.centro_id, descripcion=o.descripcion, horas_plan=h,
                                     horas_real=h)
    if not orden.centro_costo_id:
        # AUD-04: sin centro elegido, la orden lleva desde el borrador el del puesto de su primera operación
        primera = (orden.horas.filter(centro__centro_costo__isnull=False).select_related('centro')
                   .order_by('secuencia', 'id').first())
        if primera is not None:
            orden.centro_costo_id = primera.centro.centro_costo_id
            orden.save(update_fields=['centro_costo'])


def disponibilidad(orden):
    """Por insumo: lo que aún pide al almacén (sin lo ya entregado), el stock libre de su almacén de consumo, lo que
    está en calidad, bloqueado o vencido (no sale: MAN-20) y el faltante frente al stock libre."""
    from django.db.models import Sum

    from core.models import StockLote
    from inventario.estados import no_libre, vencido
    abiertas = ('BORRADOR',) + OrdenProduccion.ABIERTAS
    retenido, vencidos = no_libre(), vencido()
    filas = []
    for c in orden.consumos.select_related('producto', 'almacen'):
        almacen = c.almacen or orden.almacen_insumos
        stock = c.producto.stock_en(almacen)
        calidad, bloqueado = retenido.get((c.producto_id, almacen.pk), (D0, D0))
        sin_lote = D0
        if c.producto.control:  # MAN-20: en un producto con lote, lo que no tiene lote no sale (regularícelo)
            en_lotes = (StockLote.objects.filter(lote__producto=c.producto, almacen=almacen, cantidad__gt=0)
                        .aggregate(s=Sum('cantidad'))['s'] or D0)
            sin_lote = max(stock - en_lotes, D0)
        no_disponible = min(calidad + bloqueado + vencidos.get((c.producto_id, almacen.pk), D0) + sin_lote,
                            max(stock, D0))
        libre = stock - no_disponible
        requerido = c.por_entregar if orden.estado in abiertas else c.cantidad_plan
        filas.append({'consumo': c, 'requerido': requerido, 'stock': stock, 'libre': libre, 'sin_lote': sin_lote,
                      'no_disponible': no_disponible, 'faltante': max(requerido - libre, D0)})
    return filas


def confirmar(orden, usuario=None):
    """Numera la orden y fija su estándar: el liberado del periodo o, si aún no hay, uno provisional calculado hoy.
    Desde aquí el estándar de la orden ya no cambia aunque cambien los precios."""
    if orden.estado != 'BORRADOR':
        raise ErrorProduccion('Solo se confirman órdenes en borrador.')
    if not orden.consumos.exists():
        raise ErrorProduccion('La receta no tiene insumos.')
    with transaction.atomic():
        serie, numero = Serie.siguiente('OPR', 'OP01')
        orden.numero = f'{serie}-{numero}'
        liberado = estandar_de(orden.producto, orden.fecha)
        if liberado:
            orden.estandar, orden.costo_estandar_unit, orden.estandar_detalle = (liberado, liberado.unitario,
                                                                                  liberado.detalle)
        else:  # MAN-23: con la misma base que la planificación (la cantidad de la orden reparte la preparación)
            hoja = hoja_costos(orden.version or orden.lista, cantidad=orden.cantidad or None,
                               periodo=periodo_de(orden.fecha))
            orden.costo_estandar_unit = hoja['unitario']
            orden.estandar_detalle = {**_detalle_unitario(hoja), 'provisional': True}
        orden.estado = 'CONFIRMADA'
        orden.save()
    return orden


def iniciar(orden):
    if orden.estado != 'CONFIRMADA':
        raise ErrorProduccion('Solo se inician órdenes confirmadas.')
    orden.estado, orden.fecha_inicio = 'EN_PROCESO', timezone.localdate()
    orden.save(update_fields=['estado', 'fecha_inicio'])
    return orden


def _momento_termino(fecha):
    """(fecha, fecha y hora) del término: acepta fecha, fecha y hora o el texto de los formularios; por defecto, ahora."""
    from datetime import datetime
    ahora = timezone.localtime()
    if isinstance(fecha, str):
        texto = fecha.strip()
        try:
            fecha = datetime.fromisoformat(texto) if 'T' in texto or ' ' in texto else (
                date.fromisoformat(texto) if texto else None)
        except ValueError:
            raise ErrorProduccion('Fecha de término no válida.')
    if fecha is None:
        momento = ahora
    elif isinstance(fecha, datetime):
        momento = timezone.make_aware(fecha) if timezone.is_naive(fecha) else timezone.localtime(fecha)
    else:  # solo la fecha: hoy con la hora actual; otro día, al final de la jornada
        momento = ahora if fecha == ahora.date() else timezone.make_aware(datetime.combine(fecha, datetime.min.time())
                                                                          .replace(hour=18))
    if momento > ahora + timedelta(minutes=5):
        raise ErrorProduccion('La fecha de término no puede ser futura.')
    return momento.date(), momento


def terminar(orden, usuario, cantidad_producida, consumos, horas, fecha=None, personal=None, salidas=None,
             merma_a_gasto=False, fecha_contable=None, lote_producto='', fabricacion=None, vencimiento=None,
             lotes_insumos=None, motivos=None):
    """Registra la producción: salen los insumos realmente consumidos y entran el producto y sus subproductos.

    Costo de la orden = materiales consumidos + mano de obra + máquina + CIF (+ maquila):
    - máquina y CIF: horas máquina reales × su tarifa en el puesto;
    - mano de obra: horas hombre de cada maquinista o ayudante × el costo real de su hora (sueldo y cargas del
      trabajador) o, sin trabajador, la tarifa referencial del puesto. Sin detalle: horas máquina × cuadrilla.
    Subproductos a su valor fijo y coproductos con su % del costo conjunto (NIC 2 párr. 14); el producto principal
    lleva el resto ÷ todo lo producido. Con merma_a_gasto, el consumo sobre la receta (con su merma normal) para lo
    producido va a gasto (NIC 2 párr. 16) y no al producto.

    consumos: {consumo_id: cantidad real}; horas: {hora_id: horas máquina reales};
    personal: {hora_id: [{'rol', 'trabajador' (id o None), 'horas'}]}; salidas: {salida_id: cantidad producida}.

    INV-30: lote del producto terminado (por defecto el número de la orden), su fabricación (la fecha de término) y
    vencimiento (fabricación + vida útil si no se indica); lotes_insumos: {consumo_id: código de lote} (sin indicar,
    salen por la regla de salida). El kardex guarda qué lote de cada insumo entró en qué lote producido."""
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    if orden.estado not in ('CONFIRMADA', 'EN_PROCESO'):
        raise ErrorProduccion('Solo se terminan órdenes confirmadas o en proceso.')
    if not cantidad_producida or cantidad_producida <= 0:
        raise ErrorProduccion('Indique la cantidad producida.')
    fecha, momento = _momento_termino(fecha)
    # fecha contable (kardex y asientos) distinta de la de término: la del documento queda como referencia
    if isinstance(fecha_contable, str):
        try:
            fecha_contable = date.fromisoformat(fecha_contable.strip()) if fecha_contable.strip() else None
        except ValueError:
            raise ErrorProduccion('Fecha contable no válida.')
    contable = fecha_contable or fecha
    if contable > timezone.localdate():
        raise ErrorProduccion('La fecha contable no puede ser futura.')
    from inventario.cierre import error_cierre
    if error_cierre(contable):
        raise ErrorProduccion(error_cierre(contable))
    documento = fecha if contable != fecha else None
    personal = personal or {}
    salidas = salidas or {}
    # MAN-14: la política de consumo (estricta, con tolerancia o libre) antes de mover el almacén
    from .consumo import ErrorConsumo, revisar_terminar
    try:
        consumos = revisar_terminar(orden, consumos, cantidad_producida, usuario, motivos)
    except ErrorConsumo as exc:
        raise ErrorProduccion(str(exc)) from exc
    with transaction.atomic():
        lineas = list(orden.consumos.select_related('producto', 'almacen'))
        for c in lineas:
            real = consumos.get(c.pk, c.cantidad_real)
            if real < 0:
                raise ErrorProduccion(f'{c.producto.nombre}: el consumo no puede ser negativo.')
            c.cantidad_real = real
            c.save(update_fields=['cantidad_real'])
        from planillas.models import Trabajador
        trabajadores = {t.pk: t for t in Trabajador.objects.filter(
            pk__in={p.get('trabajador') for filas in personal.values() for p in filas if p.get('trabajador')})}
        mo = maq = cif = D0
        for h in orden.horas.select_related('centro'):
            h.horas_real = horas.get(h.pk, h.horas_real)
            if h.horas_real < 0:
                raise ErrorProduccion('Las horas no pueden ser negativas.')
            h.personal.all().delete()
            filas = personal.get(h.pk)
            if filas is None:  # sin detalle: la cuadrilla de la operación trabaja las horas máquina
                filas = ([{'rol': 'MAQUINISTA', 'horas': h.horas_real}] * h.maquinistas +
                         [{'rol': 'AYUDANTE', 'horas': h.horas_real}] * h.ayudantes)
            costo_mo = D0
            for p in filas:
                horas_h = Decimal(p.get('horas') or 0)
                if horas_h < 0:
                    raise ErrorProduccion('Las horas hombre no pueden ser negativas.')
                if not horas_h:
                    continue
                t = trabajadores.get(p.get('trabajador'))
                tarifa = t.costo_hora(fecha) if t else h.centro.costo_hora_mo
                linea = HoraHombre.objects.create(
                    hora=h, rol=p.get('rol') if p.get('rol') in dict(HoraHombre.ROLES) else 'MAQUINISTA',
                    trabajador=t, horas=horas_h, costo_hora=tarifa, costo=r2(horas_h * tarifa))
                costo_mo += linea.costo
            h.costo_mo = costo_mo
            h.costo_maquina = r2(h.horas_real * h.centro.costo_hora_maquina)
            # CIF por hora máquina con los drivers del puesto según su consumo por hora (MAN-22, como el estándar)
            h.costo_cif = r2(h.horas_real * (h.centro.costo_hora_cif + h.centro.cif_drivers_hora))
            h.save(update_fields=['horas_real', 'costo_mo', 'costo_maquina', 'costo_cif'])
            mo += h.costo_mo
            maq += h.costo_maquina
            cif += h.costo_cif
        # maquila: el servicio del tercero se suma al costo del producto (base de su factura si no se indicó)
        if orden.maquilador_id and not orden.costo_servicio and orden.compra_servicio_id:
            c = orden.compra_servicio
            orden.costo_servicio = c.total_pen - c.igv_pen
        # NIC 2 párr. 16 (solo si se indica): consumo sobre la receta para lo producido = merma anormal a gasto
        merma = D0
        if merma_a_gasto and orden.cantidad:
            for c in lineas:
                permitido = c.cantidad_plan * cantidad_producida / orden.cantidad
                if c.cantidad_real > permitido:
                    merma += (c.cantidad_real - permitido) * c.producto.costo_promedio
        merma = r2(merma)
        # costo de la orden con los mismos costos promedio con que saldrán los insumos (MAN-15: lo ya despachado a la
        # orden desde el almacén, a su costo de salida)
        materiales_est = sum(((c.cantidad_real - c.despachado) * c.producto.costo_promedio + c.valor_despachado
                              for c in lineas), D0)
        despachado = sum((c.valor_despachado for c in lineas), D0)
        conjunto = materiales_est + mo + maq + cif + orden.costo_servicio - merma
        lista_salidas = list(orden.salidas.select_related('producto'))
        for s in lista_salidas:
            s.cantidad_real = Decimal(salidas.get(s.pk, s.cantidad_real) or 0)
            if s.cantidad_real < 0:
                raise ErrorProduccion(f'{s.producto.nombre}: la cantidad no puede ser negativa.')
        if sum((s.participacion for s in lista_salidas if s.tipo == 'COPRODUCTO'), D0) >= 100:
            raise ErrorProduccion('Los coproductos no pueden llevarse el 100 % del costo conjunto.')
        for s in (x for x in lista_salidas if x.tipo == 'SUBPRODUCTO' and x.cantidad_real):
            unit = min(s.valor_unitario, (max(conjunto, D0) / s.cantidad_real)).quantize(D4)
            s.costo_unitario = unit
            conjunto -= s.cantidad_real * unit
        base_co = max(conjunto, D0)
        for s in (x for x in lista_salidas if x.tipo == 'COPRODUCTO' and x.cantidad_real):
            s.costo_unitario = (base_co * s.participacion / 100 / s.cantidad_real).quantize(D4)
        for s in lista_salidas:
            if not s.cantidad_real:
                s.costo_unitario = D0
            s.save(update_fields=['cantidad_real', 'costo_unitario'])
        tipo = TipoOperacion.objects.get(codigo='MANUF')
        # insumos que se consumen en otro almacén: salen con su propia operación de consumo a producción
        por_almacen = defaultdict(list)
        for c in lineas:
            if c.cantidad_real - c.despachado > 0:  # lo ya despachado a la orden no vuelve a salir
                por_almacen[c.almacen_id or orden.almacen_insumos_id].append(c)
        principal = orden.almacen_insumos_id
        op = Operacion.objects.create(
            tipo=tipo, fecha=contable, fecha_documento=documento, almacen_origen_id=principal,
            almacen_destino=orden.almacen_destino, referencia=orden.numero, creado_por=usuario,
            costo_adicional=mo + maq + cif + orden.costo_servicio - merma + despachado,
            glosa=f'Orden de producción {orden.numero}: {orden.producto.nombre}')
        lotes_insumos = lotes_insumos or {}
        for c in por_almacen.pop(principal, []):
            op.items.create(producto=c.producto, cantidad=r2(c.cantidad_real - c.despachado), rol='INSUMO',
                            lote=(lotes_insumos.get(c.pk) or '').strip()[:60] if c.producto.control else '')
        lote_pt = (lote_producto or '').strip().upper()[:60] or orden.numero
        op.items.create(producto=orden.producto, cantidad=cantidad_producida, rol='PRODUCTO',
                        lote=lote_pt if orden.producto.control == 'LOTE' else '',
                        fabricacion=(fabricacion or fecha) if orden.producto.control == 'LOTE' else None,
                        vencimiento=vencimiento if orden.producto.control == 'LOTE' else None)
        for s in lista_salidas:
            if s.cantidad_real > 0:
                op.items.create(producto=s.producto, cantidad=r2(s.cantidad_real), rol='SUBPROD',
                                costo_unitario=s.costo_unitario,
                                lote=orden.numero if s.producto.control == 'LOTE' else '')
        # insumos de otros almacenes de consumo: se trasladan al almacén de insumos y desde ahí se consumen
        for almacen_id, filas in por_almacen.items():
            traslado = Operacion.objects.create(
                tipo=TipoOperacion.objects.get(codigo='TRAS_ALM'), fecha=contable, fecha_documento=documento,
                almacen_origen_id=almacen_id,
                almacen_destino_id=principal, referencia=orden.numero, creado_por=usuario,
                glosa=f'Abastecimiento de {orden.numero}')
            for c in filas:
                traslado.items.create(producto=c.producto, cantidad=r2(c.cantidad_real - c.despachado))
                op.items.create(producto=c.producto, cantidad=r2(c.cantidad_real - c.despachado), rol='INSUMO')
            try:
                inv.confirmar(traslado, usuario)
            except inv.ErrorOperacion as exc:
                raise ErrorProduccion(f'Traslado de insumos: {exc}') from exc
        try:
            inv.confirmar(op, usuario)
        except inv.ErrorOperacion as exc:
            raise ErrorProduccion(str(exc)) from exc
        costos = {}
        for i in op.items.filter(rol='INSUMO'):
            costos[i.producto_id] = i.costo_unitario
        materiales = D0
        for c in lineas:
            del_almacen = (c.cantidad_real - c.despachado) * costos.get(c.producto_id, D0)
            c.costo_unitario = ((del_almacen + c.valor_despachado) / c.cantidad_real).quantize(D4) \
                if c.cantidad_real else costos.get(c.producto_id, D0)
            c.save(update_fields=['costo_unitario'])
            materiales += c.valor
        orden.costo_materiales, orden.costo_mano_obra = materiales, mo
        orden.costo_maquina, orden.costo_cif = maq, cif
        orden.merma_anormal = min(merma, materiales)
        orden.costo_subproductos = sum((s.costo for s in lista_salidas), D0)
        orden.costo_unitario = op.items.get(rol='PRODUCTO').costo_unitario
        # la fecha de término de la orden es la contable (periodo del costo); la real queda en terminado_en
        orden.cantidad_producida, orden.fecha_fin, orden.terminado_en = cantidad_producida, contable, momento
        orden.fecha_inicio = orden.fecha_inicio or contable
        orden.estado, orden.operacion = 'TERMINADA', op
        orden.lote_producido = lote_pt if orden.producto.control == 'LOTE' else ''
        orden.save()
        registrar_variaciones(orden)
    return orden


def enviar_a_maquilador(orden, usuario, fecha=None):
    """Traslada los insumos de la orden al almacén del maquilador (en poder de terceros) y desde ahí se consumen al
    terminarla. Lo que sobre se devuelve con un traslado normal."""
    from core.models import Almacen
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    if not orden.maquilador_id:
        raise ErrorProduccion('La orden no tiene maquilador.')
    if orden.estado not in ('CONFIRMADA', 'EN_PROCESO'):
        raise ErrorProduccion('Confirme la orden antes de enviar los materiales.')
    destino = Almacen.de_tercero(orden.maquilador)
    if orden.almacen_insumos_id == destino.pk:
        raise ErrorProduccion('Los materiales ya se enviaron al maquilador.')
    fecha = fecha or timezone.localdate()
    with transaction.atomic():
        lineas = list(orden.consumos.select_related('producto', 'almacen'))
        por_origen = defaultdict(list)
        for c in lineas:
            if c.cantidad_plan > 0:
                por_origen[c.almacen_id or orden.almacen_insumos_id].append(c)
        tipo = TipoOperacion.objects.get(codigo='TRAS_ALM')
        for origen, filas in por_origen.items():
            op = Operacion.objects.create(
                tipo=tipo, fecha=fecha, almacen_origen_id=origen, almacen_destino=destino, tercero=orden.maquilador,
                referencia=orden.numero, creado_por=usuario,
                glosa=f'Materiales para maquila {orden.numero} ({orden.maquilador.nombre})')
            for c in filas:
                op.items.create(producto=c.producto, cantidad=r2(c.cantidad_plan))
            try:
                inv.confirmar(op, usuario)
            except inv.ErrorOperacion as exc:
                raise ErrorProduccion(f'Envío de materiales: {exc}') from exc
        orden.consumos.update(almacen=None)
        orden.almacen_insumos = destino
        orden.estado = 'EN_PROCESO'
        orden.fecha_inicio = orden.fecha_inicio or fecha
        orden.save(update_fields=['almacen_insumos', 'estado', 'fecha_inicio'])
    return destino


def anular(orden, usuario, motivo):
    from inventario import servicios as inv
    if orden.estado == 'ANULADA':
        raise ErrorProduccion('La orden ya está anulada.')
    if orden.es_historica:
        raise ErrorProduccion('Es una orden del sistema anterior: su producción ya está en el inventario y la '
                              'contabilidad migrados; no se anula en el ERP.')
    if len((motivo or '').strip()) < 10:
        raise ErrorProduccion('Indique el motivo de la anulación (mínimo 10 caracteres).')
    if orden.liquidada_en:
        raise ErrorProduccion('La orden ya se liquidó: corrija con un costo tardío (no se anula).')
    with transaction.atomic():
        if orden.operacion_id and orden.operacion.estado == 'CONFIRMADO':
            try:  # revierte el almacén: vuelven los insumos y sale el producto
                inv.anular(orden.operacion, usuario, f'Anulación de {orden.numero}: {motivo}')
            except inv.ErrorOperacion as exc:
                raise ErrorProduccion(str(exc)) from exc
        from .parciales import anular_declaracion
        for d in orden.declaraciones.filter(anulada=False).order_by('-numero'):
            anular_declaracion(d, usuario, f'Anulación de la orden: {motivo}')
        # la devolución se imputa como en una orden abierta (sin recostear un ingreso ya revertido)
        OrdenProduccion.objects.filter(pk=orden.pk).update(estado='EN_PROCESO')
        _devolver_entregado(orden, usuario, motivo)
        revertidas, creadas = orden.salidas_revertidas, orden.devoluciones_creadas
        orden.refresh_from_db()
        orden.salidas_revertidas, orden.devoluciones_creadas = revertidas, creadas
        orden.variaciones.all().delete()
        orden.estado, orden.motivo_anulacion = 'ANULADA', motivo.strip()[:250]
        orden.anulado_por = usuario if usuario and usuario.is_authenticated else None
        orden.anulado_en = timezone.now()
        orden.save()
    return orden


def _devolver_entregado(orden, usuario, motivo):
    """MAN-20: al anular la orden se revierte lo que el almacén le entregó: cada «salida a orden de producción» se
    anula (si su mes está abierto y no tiene devoluciones) y lo que no se pueda anular vuelve con una devolución de
    orden de producción, al costo con que salió. Devuelve las operaciones anuladas y creadas (para avisarlas)."""
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    orden.salidas_revertidas, orden.devoluciones_creadas = [], []
    imputadas = Operacion.objects.filter(orden_imputada=orden, estado='CONFIRMADO')
    if not imputadas.filter(tipo__codigo='DEV_OP').exists():
        for s in imputadas.filter(tipo__codigo='SAL_OP').select_related('tipo').prefetch_related('items__producto'):
            from contabilidad.models import PeriodoContable
            if inv.errores_anulacion(s) or PeriodoContable.esta_cerrado(s.fecha.strftime('%Y%m')):
                continue
            inv.anular(s, usuario, f'Anulación de {orden.numero}: {motivo}'[:250])
            for i in s.items.all():  # lo anulado ya no está entregado a la orden
                c = ConsumoOrden.objects.filter(orden=orden, producto=i.producto).order_by('adicional', 'id').first()
                if c is not None:
                    valor = (i.cantidad * (i.costo_unitario or i.producto.costo_promedio)).quantize(Decimal('0.01'))
                    c.despachado = max(c.despachado - i.cantidad, D0)
                    c.valor_despachado = max(c.valor_despachado - valor, D0)
                    c.save(update_fields=['despachado', 'valor_despachado'])
            orden.salidas_revertidas.append(s)
    lineas = [c for c in orden.consumos.select_related('producto', 'almacen') if c.despachado > 0]
    if not lineas:
        return None
    por_almacen = defaultdict(list)
    for c in lineas:
        por_almacen[c.almacen_id or orden.almacen_insumos_id].append(c)
    tipo = TipoOperacion.objects.get(codigo='DEV_OP')
    for almacen_id, filas in por_almacen.items():
        op = Operacion.objects.create(
            tipo=tipo, fecha=timezone.localdate(), almacen_destino_id=almacen_id, centro_costo=orden.centro_costo,
            orden_imputada=orden, referencia=f'{orden.numero} anulada'[:60],
            creado_por=usuario if usuario and usuario.is_authenticated else None,
            glosa=f'Devolución por anulación de {orden.numero}: {motivo}'[:500])
        for c in filas:
            op.items.create(producto=c.producto, cantidad=r2(c.despachado),
                            costo_unitario=(c.valor_despachado / c.despachado).quantize(D4))
        try:
            inv.confirmar(op, usuario)
        except inv.ErrorOperacion as exc:
            raise ErrorProduccion(f'Devolución del material entregado: {exc}') from exc
        orden.devoluciones_creadas.append(op)
    ConsumoOrden.objects.filter(pk__in=[c.pk for c in lineas]).update(despachado=D0, valor_despachado=D0)
    return True


def desglose_estandar(orden):
    """MAN-23: de qué se compone el estándar unitario de la orden y con qué lote se calculó. Si el lote de costeo no es
    la cantidad de la orden, las horas por unidad del estándar (preparación repartida en ese lote) no son las que
    planifica la orden: la diferencia sale como variación de tamaño de lote."""
    d = orden.estandar_detalle or {}
    if not d or not orden.costo_estandar_unit:
        return None
    materiales = sum((Decimal(m['cantidad']) * Decimal(m['precio']) for m in d.get('materiales', [])), D0)
    mo = maq = cif = horas_std = D0
    for a in d.get('actividades', []):
        h = Decimal(a['horas'])
        horas_std += h
        mo += h * Decimal(a.get('personas', 1)) * Decimal(a['mo'])
        maq += h * Decimal(a.get('maquina', 0))
        cif += h * Decimal(a['cif'])
    lote = Decimal(d.get('lote') or 0)
    horas_plan = (sum((h.horas_plan for h in orden.horas.all()), D0) / orden.cantidad) if orden.cantidad else D0
    return {'materiales': r2(materiales), 'mo': r2(mo), 'maquina': r2(maq), 'cif': r2(cif),
            'subproductos': Decimal(d.get('subproductos') or 0), 'lote': lote, 'provisional': bool(d.get('provisional')),
            'horas_std': horas_std.quantize(D4), 'horas_plan': horas_plan.quantize(D4),
            'distinto': bool(lote and orden.cantidad and lote != orden.cantidad
                             and abs(horas_std - horas_plan) > Decimal('0.005'))}


# ---------------------------------------------------------------- variaciones
def _estandar_para(orden):
    """(materiales {producto: (cant por unidad, precio)},
    actividades {centro: [horas máquina por unidad, personas, tarifa MO, tarifa máquina, tarifa CIF]})."""
    d = orden.estandar_detalle or {}
    if not d:  # órdenes anteriores: estándar estimado con la receta actual
        d = _detalle_unitario(hoja_costos(orden.version or orden.lista))
    materiales = {}
    for m in d.get('materiales', []):
        cant, precio = Decimal(m['cantidad']), Decimal(m['precio'])
        anterior = materiales.get(m['producto'], (D0, precio))
        materiales[m['producto']] = (anterior[0] + cant, precio)
    actividades = defaultdict(lambda: [D0, D0, D0, D0, D0])
    for a in d.get('actividades', []):
        fila = actividades[a['centro']]
        horas = Decimal(a['horas'])
        personas = Decimal(a.get('personas', 1))
        # personas promedio ponderadas por horas (un puesto puede tener varias operaciones)
        fila[1] = ((fila[1] * fila[0] + personas * horas) / (fila[0] + horas)) if (fila[0] + horas) else personas
        fila[0] += horas
        fila[2], fila[3], fila[4] = Decimal(a['mo']), Decimal(a.get('maquina', 0)), Decimal(a['cif'])
    return materiales, dict(actividades)


def calcular_variaciones(orden):
    """{tipo: monto} de la orden terminada contra su estándar, para la cantidad realmente producida.

    precio de materiales = Σ cantidad real × (precio real − estándar)
    cantidad de materiales = Σ (cantidad real − estándar para lo producido) × precio estándar
    eficiencia de mano de obra = (horas hombre reales − estándar) × tarifa estándar
    eficiencia de máquina y CIF = (horas máquina reales − estándar) × tarifas estándar de máquina y CIF
    tarifa = costo real de mano de obra, máquina y CIF − horas reales × tarifas estándar"""
    q = orden.cantidad_producida
    std_mat, std_act = _estandar_para(orden)
    v = defaultdict(lambda: D0)
    reales = defaultdict(lambda: [D0, D0])
    for c in orden.consumos.all():
        reales[c.producto_id][0] += c.cantidad_real
        reales[c.producto_id][1] = c.costo_unitario
    for pid in set(std_mat) | set(reales):
        cant_std = std_mat.get(pid, (D0, D0))[0] * q
        precio_std = std_mat.get(pid, (D0, reales[pid][1]))[1]
        cant_real, precio_real = reales[pid]
        v['PRECIO_MAT'] += cant_real * (precio_real - precio_std)
        v['CANTIDAD_MAT'] += (cant_real - cant_std) * precio_std
    horas_reales = defaultdict(lambda: [D0, D0, D0, D0])  # horas máquina, horas hombre, mo real, máquina+cif real
    for h in orden.horas.select_related('centro').prefetch_related('personal'):
        fila = horas_reales[h.centro_id]
        fila[0] += h.horas_real
        fila[1] += h.horas_hombre
        fila[2] += h.costo_mo
        fila[3] += h.costo_maquina + h.costo_cif
    for cid in set(std_act) | set(horas_reales):
        h_std, personas, mo_std, maq_std, cif_std = std_act.get(cid, (D0, D0, D0, D0, D0))
        h_real, hh_real, mo_real, mc_real = horas_reales.get(cid, (D0, D0, D0, D0))
        h_std *= q
        mc_std = maq_std + cif_std
        if cid not in std_act:  # puesto no previsto: todo es variación de eficiencia
            mo_std = mo_real / hh_real if hh_real else D0
            mc_std = mc_real / h_real if h_real else D0
        v['EFICIENCIA_MO'] += (hh_real - h_std * personas) * mo_std
        v['EFICIENCIA_CIF'] += (h_real - h_std) * mc_std
        v['TARIFA'] += (mo_real - hh_real * mo_std) + (mc_real - h_real * mc_std)
    resultado = {k: r2(m) for k, m in v.items()}
    if orden.merma_anormal:  # el exceso de consumo está en la variación de cantidad, pero no entró al producto
        resultado['MERMA_ANORMAL'] = -orden.merma_anormal
    total_std = r2(orden.costo_estandar_unit * q)
    diferencia = orden.costo_total - total_std - sum(resultado.values(), D0)
    if diferencia:
        resultado['OTRAS'] = diferencia  # redondeos del estándar unitario
    return {k: m for k, m in resultado.items() if m}


def registrar_variaciones(orden):
    orden.variaciones.all().delete()
    for tipo, monto in calcular_variaciones(orden).items():
        VariacionOrden.objects.create(orden=orden, tipo=tipo, monto=monto)


def variaciones(orden):
    """Real vs estándar de una orden terminada por concepto (materiales, mano de obra, CIF) al volumen producido."""
    std_mat, std_act = _estandar_para(orden)
    q = orden.cantidad_producida
    e_mat = sum((cant * precio for cant, precio in std_mat.values()), D0) * q
    e_mo = sum((h * personas * mo for h, personas, mo, _, _ in std_act.values()), D0) * q
    e_maq = sum((h * maq for h, _, _, maq, _ in std_act.values()), D0) * q
    e_cif = sum((h * cif for h, _, _, _, cif in std_act.values()), D0) * q
    filas = [('Materiales', r2(e_mat), orden.costo_materiales), ('Mano de obra', r2(e_mo), orden.costo_mano_obra),
             ('Máquina', r2(e_maq), orden.costo_maquina), ('CIF', r2(e_cif), orden.costo_cif)]
    if orden.costo_subproductos:
        filas.append(('Asignado a subproductos', D0, -orden.costo_subproductos))
    salida = [{'concepto': c, 'estandar': e, 'real': r, 'variacion': r - e,
               'pct': ((r - e) / e * 100).quantize(Decimal('0.1')) if e else None} for c, e, r in filas]
    total_e, total_r = r2(orden.costo_estandar_unit * q), orden.costo_total
    salida.append({'concepto': 'Total', 'estandar': total_e, 'real': total_r, 'variacion': total_r - total_e,
                   'pct': ((total_r - total_e) / total_e * 100).quantize(Decimal('0.1')) if total_e else None})
    return salida


# ---------------------------------------------------------------- absorción de costos de planta
def _real_centro(periodo, ids, regex):
    from django.db.models import Sum

    from contabilidad.models import AsientoLinea
    agg = AsientoLinea.objects.filter(asiento__periodo=periodo, es_destino=False, centro_costo_id__in=ids,
                                      cuenta__codigo__regex=regex).aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or D0) - (agg['h'] or D0)


def absorcion(periodo):
    """Por centro de costo de planta: gasto real del periodo (contabilidad) frente a lo absorbido por las órdenes
    terminadas, separado en mano de obra (planilla, 62) y máquina + CIF (63 a 68: depreciación, energía,
    mantenimiento…). Positivo = subaplicación (costó más de lo que se cargó a los productos)."""
    from contabilidad.models import CentroCosto
    from contabilidad.centralizar import _rango
    desde, hasta = _rango(periodo)
    absorbido = defaultdict(lambda: [D0, D0])
    sin_centro = [D0, D0]
    for centro, mo, maq, cif, _ in _absorbido_por_puesto(desde, hasta):
        fila = absorbido[centro.centro_costo_id] if centro.centro_costo_id else sin_centro
        fila[0] += mo
        fila[1] += maq + cif
    centros = CentroCosto.objects.filter(Q(tipo__in=['PRODUCCION', 'SERVICIO']) | Q(pk__in=list(absorbido)))
    filas = []
    for cc in centros.order_by('codigo'):
        ids = cc.descendientes_ids()
        real_mo, real_cif = _real_centro(periodo, ids, r'^62'), _real_centro(periodo, ids, r'^6[3-8]')
        mo, cif = sum((absorbido[i][0] for i in ids if i in absorbido), D0), sum(
            (absorbido[i][1] for i in ids if i in absorbido), D0)
        real = real_mo + real_cif
        if not (real or mo or cif):
            continue
        filas.append({'centro': cc, 'real': real, 'real_mo': real_mo, 'real_cif': real_cif, 'mo': mo, 'cif': cif,
                      'absorbido': mo + cif, 'dif_mo': real_mo - mo, 'dif_cif': real_cif - cif,
                      'diferencia': real - mo - cif,
                      'pct': ((mo + cif) / real * 100).quantize(Decimal('0.1')) if real else None})
    return filas, sin_centro


def _absorbido_por_puesto(desde, hasta):
    """[(puesto, mano de obra, máquina, CIF, {horas, hh, orden})] absorbido por las órdenes en el rango: las
    terminadas en un paso (por su fecha de término) y, MAN-09 con producción parcial, cada declaración vigente por su
    fecha contable (horas hombre, horas máquina y drivers declarados)."""
    from .models import CargoDeclaracion, DeclaracionOrden
    salida = []
    for h in (HoraOrden.objects.filter(orden__estado='TERMINADA', orden__fecha_fin__range=[desde, hasta])
              .exclude(orden__declaraciones__isnull=False).distinct()
              .select_related('centro__centro_costo').prefetch_related('personal')):
        salida.append((h.centro, h.costo_mo, h.costo_maquina, h.costo_cif,
                       {'horas': h.horas_real, 'hh': h.horas_hombre, 'orden': h.orden_id}))
    for d in (DeclaracionOrden.objects.filter(anulada=False, fecha__range=[desde, hasta])
              .prefetch_related('personal', 'cargos')):
        for h in HoraOrden.objects.filter(orden_id=d.orden_id).select_related('centro__centro_costo'):
            horas = Decimal(str((d.horas or {}).get(str(h.pk), '0')))
            personal = [p for p in d.personal.all() if p.hora_id == h.pk]
            cargos = [c for c in d.cargos.all() if c.hora_id == h.pk]
            mo = sum((p.costo for p in personal), D0)
            if cargos:
                maq = sum((c.importe for c in cargos if c.tipo != 'DRIVER'), D0)
                cif = r2(horas * h.centro.costo_hora_cif) + sum((c.importe for c in cargos if c.tipo == 'DRIVER'), D0)
            else:
                maq, cif = r2(horas * h.centro.costo_hora_maquina), r2(horas * h.centro.costo_hora_cif)
            if horas or mo or maq or cif:
                salida.append((h.centro, mo, maq, cif, {'horas': horas, 'hh': sum((p.horas for p in personal), D0),
                                                        'orden': d.orden_id}))
    return salida


def absorcion_por_puesto(periodo):
    """Por puesto de trabajo: horas reales, mano de obra, máquina y CIF absorbidos en el periodo y la tarifa
    aplicada; el gasto real se compara por el centro de costo del puesto (absorcion)."""
    from contabilidad.centralizar import _rango
    desde, hasta = _rango(periodo)
    filas = defaultdict(lambda: {'horas': D0, 'hh': D0, 'mo': D0, 'maquina': D0, 'cif': D0, 'ordenes': set()})
    for centro, mo, maq, cif, extra in _absorbido_por_puesto(desde, hasta):
        f = filas[centro]
        f['horas'] += extra['horas']
        f['hh'] += extra['hh']
        f['mo'] += mo
        f['maquina'] += maq
        f['cif'] += cif
        f['ordenes'].add(extra['orden'])
    return sorted(({'puesto': c, **{k: v for k, v in f.items() if k != 'ordenes'}, 'ordenes': len(f['ordenes']),
                    'total': f['mo'] + f['maquina'] + f['cif']} for c, f in filas.items()),
                  key=lambda x: x['puesto'].codigo)


# ---------------------------------------------------------------- MRP
def _redondear(cantidad, lote):
    if not lote or lote <= 0:
        return cantidad.quantize(Decimal('0.01'), rounding=ROUND_CEILING)
    return (cantidad / lote).quantize(Decimal('1'), rounding=ROUND_CEILING) * lote


def ejecutar_mrp(horizonte, usuario=None):
    """Planificación de necesidades multinivel.

    Demanda bruta: pedidos de venta abiertos, plan de demanda y los insumos de las órdenes de producción abiertas.
    Disponible: stock − stock de seguridad (mínimo) + órdenes de producción abiertas + compras en camino.
    Por nivel (primero lo que se vende, después sus semielaborados y al final las materias primas): cada faltante
    genera una orden planificada; las de fabricar explotan su receta como demanda de sus insumos.

    Tipo de planificación del producto: solo entran al cálculo los de MRP; los de punto de reorden (suministros
    indirectos) se reponen aparte al bajar del punto de reorden y los sin planificar no se proponen.
    Estrategia: para stock (MTS) usa el plan de demanda y el stock libre; contra pedido (MTO) solo los pedidos y
    los consumos, sin stock libre ni de seguridad, con una orden por cada pedido."""
    from compras.models import OrdenCompraItem
    from ventas.models import CotizacionItem
    hoy = timezone.localdate()
    niveles = _niveles()
    demanda = defaultdict(list)    # producto -> [(fecha, cantidad, origen)]
    entradas = defaultdict(list)   # producto -> [(fecha, cantidad, origen)]
    for i in (CotizacionItem.objects.filter(documento__tipo='PED', documento__estado__in=['PENDIENTE', 'APROBADO'],
                                            producto__isnull=False, producto__tipo='BIEN')
              .select_related('documento')):
        demanda[i.producto_id].append((max(i.documento.fecha, hoy), i.cantidad, f'Pedido {i.documento.numero}'))
    for p in PlanDemanda.objects.filter(fecha__lte=horizonte, producto__estrategia='MTS'):
        demanda[p.producto_id].append((max(p.fecha, hoy), p.cantidad,
                                       f'Plan: {p.nota}' if p.nota else f'Plan del {p.fecha:%d/%m/%Y}'))
    for o in OrdenProduccion.objects.filter(estado__in=('BORRADOR',) + OrdenProduccion.ABIERTAS).prefetch_related(
            'consumos'):
        fecha = max(o.fecha, hoy)
        # MAN-18: una orden parcial aporta solo su saldo; sus insumos, lo que aún no salió del almacén
        entradas[o.producto_id].append((fecha, max(o.cantidad - o.cantidad_producida, D0),
                                        f'OP {o.numero or "borrador"}'))
        for c in o.consumos.all():
            demanda[c.producto_id].append((fecha, c.por_entregar, f'Insumo de OP {o.numero or "borrador"}'))
    from inventario.servicios import pendientes
    from inventario.models import Operacion, TipoOperacion
    recepcion = TipoOperacion.objects.filter(origen='ORDEN_COMPRA', activo=True).first()
    for oc_item in (OrdenCompraItem.objects.filter(documento__estado__in=['PENDIENTE', 'APROBADO'],
                                                   producto__isnull=False).select_related('documento')):
        oc = oc_item.documento
        pendiente = oc_item.cantidad
        if recepcion:
            pendiente = pendientes(Operacion(tipo=recepcion, orden_compra=oc)).get(oc_item.producto_id, (D0,))[0]
        if pendiente > 0:
            entradas[oc_item.producto_id].append((max(oc.fecha_entrega or oc.fecha, hoy), pendiente,
                                                  f'OC {oc.numero}'))
    # lo solicitado y aún no pedido en solicitudes de pedido (por liberar o liberadas) también llegará
    from compras.models import LineaSolicitud
    for l in (LineaSolicitud.objects.filter(solicitud__estado__in=['ENVIADA', 'OBSERVADA', 'LIBERADA', 'PARCIAL'],
                                            producto__isnull=False, cerrada=False)
              .select_related('solicitud').prefetch_related('items_oc__documento', 'items_oc__producto')):
        if l.pendiente > 0:
            entradas[l.producto_id].append((max(l.solicitud.fecha_requerida or l.solicitud.fecha, hoy), l.pendiente,
                                            f'SolPed {l.solicitud.numero}'))
    with transaction.atomic():
        corrida = CorridaMRP.objects.create(horizonte=horizonte,
                                            usuario=usuario if usuario and usuario.is_authenticated else None)
        procesados = set()
        from inventario.estados import no_libre_por_producto
        no_libre = no_libre_por_producto()  # INV-25: el MRP solo cuenta el stock libre
        while True:
            pendientes_ids = [pid for pid in demanda if pid not in procesados]
            if not pendientes_ids:
                break
            pid = min(pendientes_ids, key=lambda x: niveles[x])
            procesados.add(pid)
            p = Producto.objects.get(pk=pid)
            if not p.es_inventariable or p.tipo_planificacion != 'MRP':
                continue
            mto = p.estrategia == 'MTO'  # contra pedido: sin stock libre ni de seguridad, sin lotes
            disponible = D0 if mto else p.stock - no_libre.get(p.pk, D0) - (p.stock_minimo or D0)
            eventos = sorted([(f, c, o, 'E') for f, c, o in entradas[pid]] +
                             [(f, c, o, 'D') for f, c, o in demanda[pid] if f <= horizonte],
                             key=lambda e: (e[0], e[3] != 'E'))
            for fecha, cantidad, origen, clase in eventos:
                disponible += cantidad if clase == 'E' else -cantidad
                if disponible >= 0:
                    continue
                falta = -disponible
                version = version_para(p, falta, fecha) or version_vigente(p, fecha)
                if version:
                    cantidad_op = falta if mto else max(falta, version.lote_min)
                    if version.lote_max and cantidad_op > version.lote_max:
                        cantidad_op = falta  # se sugiere igual; el planificador la divide si hace falta
                    inicio = max(fecha - timedelta(days=version.dias_fabricacion or 0), hoy)
                    PropuestaMRP.objects.create(corrida=corrida, producto=p, tipo='PRODUCIR', nivel=niveles[pid],
                                                cantidad=r2(cantidad_op), fecha_necesidad=fecha, fecha_inicio=inicio,
                                                version=version, origen=origen[:250])
                    lista = version.lista
                    factor = cantidad_op / lista.cantidad_base if lista.cantidad_base else Decimal('1')
                    for c in lista.componentes.all():  # demanda dependiente de los insumos (nivel inferior)
                        demanda[c.producto_id].append((inicio, (c.cantidad_con_merma * factor).quantize(D4),
                                                       f'Insumo de OP planificada de {p.nombre}'))
                    disponible += cantidad_op
                else:  # comprar (también la mercadería de reventa): proveedor y plazo del registro info
                    from compras.registro_info import proveedor_y_plazo
                    proveedor, plazo, lote = proveedor_y_plazo(p)
                    cantidad_oc = _redondear(falta if mto else max(falta, lote or D0), None)
                    pedir = max(fecha - timedelta(days=plazo), hoy)
                    PropuestaMRP.objects.create(corrida=corrida, producto=p, tipo='COMPRAR', nivel=niveles[pid],
                                                cantidad=r2(cantidad_oc), fecha_necesidad=fecha, fecha_inicio=pedir,
                                                proveedor=proveedor, origen=origen[:250])
                    disponible += cantidad_oc
        _reponer_por_reorden(corrida, entradas, niveles, hoy)
    return corrida


def _reponer_por_reorden(corrida, entradas, niveles, hoy):
    """Productos por punto de reorden (suministros indirectos): fuera del MRP; si el stock más lo que ya viene en
    camino llega al punto de reorden (o al mínimo), se propone reponer hasta el máximo (o el punto), por lote."""
    for p in Producto.objects.filter(tipo_planificacion='REORDEN', tipo='BIEN', activo=True, es_plantilla=False):
        limite = p.punto_reorden or p.stock_minimo or D0
        if limite <= 0:
            continue
        proyectado = p.stock + sum((c for _, c, _ in entradas[p.pk]), D0)
        if proyectado > limite:
            continue
        cantidad = max((p.stock_maximo or limite) - proyectado, p.lote_compra or D0, Decimal('0.01'))
        origen = f'Punto de reorden: stock {r2(p.stock)} + en camino {r2(proyectado - p.stock)} ≤ {r2(limite)}'
        version = version_vigente(p, hoy)
        if version:
            PropuestaMRP.objects.create(corrida=corrida, producto=p, tipo='PRODUCIR', nivel=niveles[p.pk],
                                        cantidad=r2(cantidad), fecha_necesidad=hoy, fecha_inicio=hoy,
                                        version=version, origen=origen[:250])
        else:
            from compras.registro_info import proveedor_y_plazo
            proveedor, plazo, lote = proveedor_y_plazo(p)
            PropuestaMRP.objects.create(corrida=corrida, producto=p, tipo='COMPRAR', nivel=niveles[p.pk],
                                        cantidad=r2(_redondear(max(cantidad, lote or D0), None)),
                                        fecha_necesidad=hoy + timedelta(days=plazo),
                                        fecha_inicio=hoy, proveedor=proveedor, origen=origen[:250])


def convertir_en_orden(propuesta, usuario):
    """Orden planificada de fabricar -> orden de producción en borrador (con su versión y sus insumos)."""
    from core.models import Almacen
    if propuesta.tipo != 'PRODUCIR' or propuesta.convertida:
        raise ErrorProduccion('La propuesta ya se convirtió o no es de fabricación.')
    version = propuesta.version or version_para(propuesta.producto, propuesta.cantidad, propuesta.fecha_inicio)
    if version is None:
        raise ErrorProduccion(f'{propuesta.producto.nombre} no tiene versión de fabricación vigente.')
    with transaction.atomic():
        principal = Almacen.principal()
        destino = propuesta.producto.almacen_defecto or principal  # AUD-03: su almacén por defecto
        orden = OrdenProduccion.objects.create(
            producto=propuesta.producto, version=version, lista=version.lista, cantidad=propuesta.cantidad,
            fecha=propuesta.fecha_inicio, almacen_insumos=principal, almacen_destino=destino, creado_por=usuario,
            glosa=f'Generada por el MRP ({propuesta.origen})'[:500])
        explotar(orden)
        propuesta.orden_produccion = orden
        propuesta.save(update_fields=['orden_produccion'])
    return orden


def convertir_en_compras(propuestas, usuario, centro_costo):
    """Órdenes planificadas de comprar -> órdenes de compra pendientes, una por proveedor."""
    from compras.models import OrdenCompra, OrdenCompraItem
    if centro_costo is None:
        raise ErrorProduccion('Indique el centro de costo de las órdenes de compra.')
    por_proveedor = defaultdict(list)
    for p in propuestas:
        if p.tipo != 'COMPRAR' or p.convertida:
            continue
        if not p.proveedor_id:
            raise ErrorProduccion(f'{p.producto.nombre} no tiene proveedor habitual: asígnelo en el producto.')
        por_proveedor[p.proveedor_id].append(p)
    ordenes = []
    with transaction.atomic():
        for proveedor_id, filas in por_proveedor.items():
            serie, numero = Serie.siguiente('OC', 'OC01')
            oc = OrdenCompra.objects.create(numero=f'{serie}-{numero}', tercero_id=proveedor_id,
                                            fecha=timezone.localdate(), fecha_entrega=min(f.fecha_necesidad for f in filas),
                                            centro_costo=centro_costo, glosa='Generada por el MRP')
            for f in filas:  # la necesidad está en unidad de almacén; se pide en unidad de compra
                OrdenCompraItem.objects.create(documento=oc, producto=f.producto, descripcion=f.producto.nombre,
                                               cantidad=f.producto.a_compra(f.cantidad),
                                               precio_unitario=f.producto.precio_compra_unidad_compra)
                f.orden_compra = oc
                f.save(update_fields=['orden_compra'])
            oc.calcular_totales()
            oc.save()
            ordenes.append(oc)
    return ordenes


# ---------------------------------------------------------------- requerimiento de materiales
def requerimientos():
    """Insumos que piden las órdenes confirmadas o en proceso frente al stock y a lo pedido en compras."""
    from core.inventario import en_camino
    requerido = defaultdict(lambda: D0)
    ordenes = defaultdict(set)
    for c in (ConsumoOrden.objects.filter(orden__estado__in=OrdenProduccion.ABIERTAS)
              .only('producto_id', 'cantidad_real', 'despachado', 'backflush', 'orden__numero').select_related('orden')):
        if c.por_entregar:  # lo ya entregado a la orden (salidas y declaraciones) ya no se pide
            requerido[c.producto_id] += c.por_entregar
            ordenes[c.producto_id].add(c.orden.numero)
    camino = en_camino()
    from inventario.estados import no_libre_por_producto
    no_libre = no_libre_por_producto()
    filas = []
    for p in Producto.objects.filter(pk__in=requerido).select_related('proveedor').order_by('nombre'):
        disponible = p.stock - no_libre.get(p.pk, D0) + camino.get(p.pk, D0)
        faltante = requerido[p.pk] - disponible
        filas.append({'p': p, 'requerido': requerido[p.pk], 'stock': p.stock, 'camino': camino.get(p.pk, D0),
                      'faltante': max(faltante, D0), 'ordenes': sorted(ordenes[p.pk])})
    return filas


def carga_capacidad(desde, hasta):
    """Horas planificadas por puesto (órdenes abiertas en el rango) frente a su capacidad según calendario."""
    from .models import CentroTrabajo
    carga = defaultdict(lambda: D0)
    for h in HoraOrden.objects.filter(orden__estado__in=('BORRADOR',) + OrdenProduccion.ABIERTAS,
                                      orden__fecha__range=[desde, hasta]):
        carga[h.centro_id] += h.horas_plan
    filas = []
    for c in CentroTrabajo.objects.filter(activo=True):
        capacidad = c.capacidad_entre(desde, hasta)
        filas.append({'centro': c, 'capacidad': capacidad, 'carga': carga[c.pk],
                      'uso': (carga[c.pk] / capacidad * 100).quantize(Decimal('0.1')) if capacidad else None})
    return filas
