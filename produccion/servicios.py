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

from .models import (ConsumoOrden, CostoEstandar, CorridaMRP, HoraOrden, ListaMateriales, OrdenProduccion,
                     PlanDemanda, PropuestaMRP, VariacionOrden, VersionFabricacion)

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
        actividades = [(o.centro, o.secuencia, o.descripcion, o.horas_para(unidades))
                       for o in version.hoja.operaciones.select_related('centro')]
    else:  # recetas anteriores a las hojas de ruta: horas por lote dentro de la receta
        actividades = [(o.centro, None, o.descripcion, (o.horas * factor).quantize(Decimal('0.01')))
                       for o in lista.operaciones.select_related('centro')]
    for centro, secuencia, descripcion, h in actividades:
        mo, cif = r2(h * centro.costo_hora_mo), r2(h * centro.costo_hora_cif)
        horas.append({'centro': centro, 'secuencia': secuencia, 'descripcion': descripcion, 'horas': h, 'mo': mo,
                      'cif': cif, 'total': mo + cif})
    tot_mat = sum((m['valor'] for m in materiales), D0)
    tot_mo = sum((h['mo'] for h in horas), D0)
    tot_cif = sum((h['cif'] for h in horas), D0)
    total = tot_mat + tot_mo + tot_cif
    return {'lista': lista, 'version': version, 'cantidad': unidades, 'materiales': materiales, 'horas': horas,
            'tot_materiales': tot_mat, 'tot_mano_obra': tot_mo, 'tot_cif': tot_cif, 'tot_conversion': tot_mo + tot_cif,
            'total': total, 'unitario': (total / unidades).quantize(D4) if unidades else D0}


def _detalle_unitario(hoja):
    """Estándar por UNA unidad (lo que se guarda y con lo que se calculan las variaciones)."""
    n = hoja['cantidad'] or Decimal('1')
    return {
        'materiales': [{'producto': m['producto'].pk, 'nombre': m['producto'].nombre,
                        'cantidad': str((m['cantidad'] / n).quantize(Decimal('0.000001'))), 'precio': str(m['costo'])}
                       for m in hoja['materiales']],
        'actividades': [{'centro': h['centro'].pk, 'nombre': str(h['centro']), 'secuencia': h['secuencia'],
                         'horas': str((h['horas'] / n).quantize(Decimal('0.000001'))),
                         'mo': str(h['centro'].costo_hora_mo), 'cif': str(h['centro'].costo_hora_cif)}
                        for h in hoja['horas']],
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
                'mano_obra': (hoja['tot_mano_obra'] / n).quantize(D4), 'cif': (hoja['tot_cif'] / n).quantize(D4),
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
    for c in lista.componentes.all():
        cant = (c.cantidad_con_merma * factor).quantize(D4)
        ConsumoOrden.objects.create(orden=orden, producto_id=c.producto_id, cantidad_plan=cant, cantidad_real=cant,
                                    almacen_id=c.almacen_id, operacion=c.operacion)
    if version and version.hoja_id:
        for o in version.hoja.operaciones.select_related('centro'):
            h = o.horas_para(orden.cantidad)
            HoraOrden.objects.create(orden=orden, centro_id=o.centro_id, secuencia=o.secuencia,
                                     descripcion=f'{o.secuencia} {o.descripcion}', horas_plan=h, horas_real=h)
    else:
        for o in lista.operaciones.all():
            h = (o.horas * factor).quantize(Decimal('0.01'))
            HoraOrden.objects.create(orden=orden, centro_id=o.centro_id, descripcion=o.descripcion, horas_plan=h,
                                     horas_real=h)


def disponibilidad(orden):
    """Por insumo: requerido, stock en su almacén de consumo y faltante."""
    filas = []
    for c in orden.consumos.select_related('producto', 'almacen'):
        stock = c.producto.stock_en(c.almacen or orden.almacen_insumos)
        requerido = c.cantidad_real if orden.estado in ('BORRADOR', 'CONFIRMADA', 'EN_PROCESO') else c.cantidad_plan
        filas.append({'consumo': c, 'requerido': requerido, 'stock': stock, 'faltante': max(requerido - stock, D0)})
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
        else:
            hoja = hoja_costos(orden.version or orden.lista, periodo=periodo_de(orden.fecha))
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


def terminar(orden, usuario, cantidad_producida, consumos, horas, fecha=None):
    """Registra la producción: salen los insumos realmente consumidos y entra el producto terminado, costeado con
    materiales + mano de obra + máquina/CIF (horas reales por puesto). Una orden confirmada se inicia sola.
    Calcula las variaciones contra el estándar fijado al confirmar.

    consumos: {consumo_id: cantidad real}; horas: {hora_id: horas reales}."""
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    if orden.estado not in ('CONFIRMADA', 'EN_PROCESO'):
        raise ErrorProduccion('Solo se terminan órdenes confirmadas o en proceso.')
    if not cantidad_producida or cantidad_producida <= 0:
        raise ErrorProduccion('Indique la cantidad producida.')
    if isinstance(fecha, str):
        try:
            fecha = date.fromisoformat(fecha) if fecha else None
        except ValueError:
            raise ErrorProduccion('Fecha de término no válida.')
    fecha = fecha or timezone.localdate()
    if fecha > timezone.localdate():
        raise ErrorProduccion('La fecha de término no puede ser futura.')
    from inventario.cierre import error_cierre
    if error_cierre(fecha):
        raise ErrorProduccion(error_cierre(fecha))
    with transaction.atomic():
        lineas = list(orden.consumos.select_related('producto', 'almacen'))
        for c in lineas:
            real = consumos.get(c.pk, c.cantidad_real)
            if real < 0:
                raise ErrorProduccion(f'{c.producto.nombre}: el consumo no puede ser negativo.')
            c.cantidad_real = real
            c.save(update_fields=['cantidad_real'])
        mo = cif = D0
        for h in orden.horas.select_related('centro'):
            h.horas_real = horas.get(h.pk, h.horas_real)
            if h.horas_real < 0:
                raise ErrorProduccion('Las horas no pueden ser negativas.')
            h.costo_mo, h.costo_cif = r2(h.horas_real * h.centro.costo_hora_mo), r2(h.horas_real * h.centro.costo_hora_cif)
            h.save(update_fields=['horas_real', 'costo_mo', 'costo_cif'])
            mo += h.costo_mo
            cif += h.costo_cif
        tipo = TipoOperacion.objects.get(codigo='MANUF')
        # insumos que se consumen en otro almacén: salen con su propia operación de consumo a producción
        por_almacen = defaultdict(list)
        for c in lineas:
            if c.cantidad_real > 0:
                por_almacen[c.almacen_id or orden.almacen_insumos_id].append(c)
        principal = orden.almacen_insumos_id
        op = Operacion.objects.create(
            tipo=tipo, fecha=fecha, almacen_origen_id=principal, almacen_destino=orden.almacen_destino,
            referencia=orden.numero, creado_por=usuario, costo_adicional=mo + cif,
            glosa=f'Orden de producción {orden.numero}: {orden.producto.nombre}')
        for c in por_almacen.pop(principal, []):
            op.items.create(producto=c.producto, cantidad=r2(c.cantidad_real), rol='INSUMO')
        op.items.create(producto=orden.producto, cantidad=cantidad_producida, rol='PRODUCTO',
                        lote=orden.numero if orden.producto.control == 'LOTE' else '')
        # insumos de otros almacenes de consumo: se trasladan al almacén de insumos y desde ahí se consumen
        for almacen_id, filas in por_almacen.items():
            traslado = Operacion.objects.create(
                tipo=TipoOperacion.objects.get(codigo='TRAS_ALM'), fecha=fecha, almacen_origen_id=almacen_id,
                almacen_destino_id=principal, referencia=orden.numero, creado_por=usuario,
                glosa=f'Abastecimiento de {orden.numero}')
            for c in filas:
                traslado.items.create(producto=c.producto, cantidad=r2(c.cantidad_real))
                op.items.create(producto=c.producto, cantidad=r2(c.cantidad_real), rol='INSUMO')
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
            c.costo_unitario = costos.get(c.producto_id, D0)
            c.save(update_fields=['costo_unitario'])
            materiales += c.valor
        orden.costo_materiales, orden.costo_mano_obra, orden.costo_cif = materiales, mo, cif
        orden.costo_unitario = op.items.get(rol='PRODUCTO').costo_unitario
        orden.cantidad_producida, orden.fecha_fin = cantidad_producida, fecha
        orden.fecha_inicio = orden.fecha_inicio or fecha
        orden.estado, orden.operacion = 'TERMINADA', op
        orden.save()
        registrar_variaciones(orden)
    return orden


def anular(orden, usuario, motivo):
    from inventario import servicios as inv
    if orden.estado == 'ANULADA':
        raise ErrorProduccion('La orden ya está anulada.')
    if len((motivo or '').strip()) < 10:
        raise ErrorProduccion('Indique el motivo de la anulación (mínimo 10 caracteres).')
    with transaction.atomic():
        if orden.operacion_id and orden.operacion.estado == 'CONFIRMADO':
            try:  # revierte el almacén: vuelven los insumos y sale el producto
                inv.anular(orden.operacion, usuario, f'Anulación de {orden.numero}: {motivo}')
            except inv.ErrorOperacion as exc:
                raise ErrorProduccion(str(exc)) from exc
        orden.variaciones.all().delete()
        orden.estado, orden.motivo_anulacion = 'ANULADA', motivo.strip()[:250]
        orden.anulado_por = usuario if usuario and usuario.is_authenticated else None
        orden.anulado_en = timezone.now()
        orden.save()
    return orden


# ---------------------------------------------------------------- variaciones
def _estandar_para(orden):
    """(materiales {producto: (cant por unidad, precio)}, actividades {centro: (horas por unidad, mo, cif)})."""
    d = orden.estandar_detalle or {}
    if not d:  # órdenes anteriores: estándar estimado con la receta actual
        d = _detalle_unitario(hoja_costos(orden.version or orden.lista))
    materiales = {}
    for m in d.get('materiales', []):
        cant, precio = Decimal(m['cantidad']), Decimal(m['precio'])
        anterior = materiales.get(m['producto'], (D0, precio))
        materiales[m['producto']] = (anterior[0] + cant, precio)
    actividades = defaultdict(lambda: [D0, D0, D0])
    for a in d.get('actividades', []):
        fila = actividades[a['centro']]
        fila[0] += Decimal(a['horas'])
        fila[1], fila[2] = Decimal(a['mo']), Decimal(a['cif'])
    return materiales, dict(actividades)


def calcular_variaciones(orden):
    """{tipo: monto} de la orden terminada contra su estándar, para la cantidad realmente producida.

    precio de materiales = Σ cantidad real × (precio real − estándar)
    cantidad de materiales = Σ (cantidad real − estándar para lo producido) × precio estándar
    eficiencia = Σ (horas reales − estándar) × tarifa estándar (mano de obra y máquina/CIF por separado)
    tarifa = Σ horas reales × (tarifa real − estándar)"""
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
    horas_reales = defaultdict(lambda: [D0, D0, D0])  # horas, mo real, cif real
    for h in orden.horas.select_related('centro'):
        fila = horas_reales[h.centro_id]
        fila[0] += h.horas_real
        fila[1] += h.costo_mo
        fila[2] += h.costo_cif
    for cid in set(std_act) | set(horas_reales):
        h_std, mo_std, cif_std = std_act.get(cid, (D0, D0, D0))
        h_real, mo_real, cif_real = horas_reales.get(cid, (D0, D0, D0))
        h_std *= q
        if cid not in std_act and h_real:  # puesto no previsto: todo es variación de eficiencia
            mo_std, cif_std = mo_real / h_real, cif_real / h_real
        v['EFICIENCIA_MO'] += (h_real - h_std) * mo_std
        v['EFICIENCIA_CIF'] += (h_real - h_std) * cif_std
        v['TARIFA'] += (mo_real + cif_real) - h_real * (mo_std + cif_std)
    resultado = {k: r2(m) for k, m in v.items()}
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
    e_mo = sum((h * mo for h, mo, _ in std_act.values()), D0) * q
    e_cif = sum((h * cif for h, _, cif in std_act.values()), D0) * q
    filas = [('Materiales', r2(e_mat), orden.costo_materiales), ('Mano de obra', r2(e_mo), orden.costo_mano_obra),
             ('Máquina y CIF', r2(e_cif), orden.costo_cif)]
    salida = [{'concepto': c, 'estandar': e, 'real': r, 'variacion': r - e,
               'pct': ((r - e) / e * 100).quantize(Decimal('0.1')) if e else None} for c, e, r in filas]
    total_e, total_r = r2(orden.costo_estandar_unit * q), orden.costo_total
    salida.append({'concepto': 'Total', 'estandar': total_e, 'real': total_r, 'variacion': total_r - total_e,
                   'pct': ((total_r - total_e) / total_e * 100).quantize(Decimal('0.1')) if total_e else None})
    return salida


# ---------------------------------------------------------------- absorción de costos de planta
def absorcion(periodo):
    """Por centro de costo de planta: gasto real del periodo (contabilidad, 62-68) frente a mano de obra y CIF
    absorbidos por las órdenes terminadas. Positivo = subaplicación (costó más de lo que se cargó a productos)."""
    from django.db.models import Sum

    from contabilidad.models import AsientoLinea, CentroCosto
    from contabilidad.centralizar import _rango
    desde, hasta = _rango(periodo)
    absorbido = defaultdict(lambda: [D0, D0])
    sin_centro = [D0, D0]
    for h in HoraOrden.objects.filter(orden__estado='TERMINADA', orden__fecha_fin__range=[desde, hasta]) \
            .select_related('centro'):
        fila = absorbido[h.centro.centro_costo_id] if h.centro.centro_costo_id else sin_centro
        fila[0] += h.costo_mo
        fila[1] += h.costo_cif
    centros = CentroCosto.objects.filter(Q(tipo__in=['PRODUCCION', 'SERVICIO']) | Q(pk__in=list(absorbido)))
    filas = []
    for cc in centros.order_by('codigo'):
        ids = cc.descendientes_ids()
        real = (AsientoLinea.objects.filter(asiento__periodo=periodo, es_destino=False, centro_costo_id__in=ids,
                                            cuenta__codigo__regex=r'^6[2-8]')
                .aggregate(s=Sum('debe'))['s'] or D0) - (
            AsientoLinea.objects.filter(asiento__periodo=periodo, es_destino=False, centro_costo_id__in=ids,
                                        cuenta__codigo__regex=r'^6[2-8]').aggregate(s=Sum('haber'))['s'] or D0)
        mo, cif = sum((absorbido[i][0] for i in ids if i in absorbido), D0), sum(
            (absorbido[i][1] for i in ids if i in absorbido), D0)
        if not (real or mo or cif):
            continue
        filas.append({'centro': cc, 'real': real, 'mo': mo, 'cif': cif, 'absorbido': mo + cif,
                      'diferencia': real - mo - cif,
                      'pct': ((mo + cif) / real * 100).quantize(Decimal('0.1')) if real else None})
    return filas, sin_centro


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
    genera una orden planificada; las de fabricar explotan su receta como demanda de sus insumos."""
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
    for p in PlanDemanda.objects.filter(fecha__lte=horizonte):
        demanda[p.producto_id].append((max(p.fecha, hoy), p.cantidad,
                                       f'Plan: {p.nota}' if p.nota else f'Plan del {p.fecha:%d/%m/%Y}'))
    for o in OrdenProduccion.objects.filter(estado__in=['BORRADOR', 'CONFIRMADA', 'EN_PROCESO']).prefetch_related(
            'consumos'):
        fecha = max(o.fecha, hoy)
        entradas[o.producto_id].append((fecha, o.cantidad, f'OP {o.numero or "borrador"}'))
        for c in o.consumos.all():
            demanda[c.producto_id].append((fecha, c.cantidad_real, f'Insumo de OP {o.numero or "borrador"}'))
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
    with transaction.atomic():
        corrida = CorridaMRP.objects.create(horizonte=horizonte,
                                            usuario=usuario if usuario and usuario.is_authenticated else None)
        procesados = set()
        while True:
            pendientes_ids = [pid for pid in demanda if pid not in procesados]
            if not pendientes_ids:
                break
            pid = min(pendientes_ids, key=lambda x: niveles[x])
            procesados.add(pid)
            p = Producto.objects.get(pk=pid)
            if not p.es_inventariable:
                continue
            disponible = p.stock - (p.stock_minimo or D0)
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
                    cantidad_op = max(falta, version.lote_min)
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
                else:
                    cantidad_oc = _redondear(max(falta, p.lote_compra or D0), None)
                    pedir = max(fecha - timedelta(days=p.tiempo_entrega or 0), hoy)
                    PropuestaMRP.objects.create(corrida=corrida, producto=p, tipo='COMPRAR', nivel=niveles[pid],
                                                cantidad=r2(cantidad_oc), fecha_necesidad=fecha, fecha_inicio=pedir,
                                                proveedor=p.proveedor, origen=origen[:250])
                    disponible += cantidad_oc
    return corrida


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
        orden = OrdenProduccion.objects.create(
            producto=propuesta.producto, version=version, lista=version.lista, cantidad=propuesta.cantidad,
            fecha=propuesta.fecha_inicio, almacen_insumos=principal, almacen_destino=principal, creado_por=usuario,
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
                                               precio_unitario=f.producto.precio_compra or D0)
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
    for c in ConsumoOrden.objects.filter(orden__estado__in=['CONFIRMADA', 'EN_PROCESO']).select_related('orden'):
        requerido[c.producto_id] += c.cantidad_real
        ordenes[c.producto_id].add(c.orden.numero)
    camino = en_camino()
    filas = []
    for p in Producto.objects.filter(pk__in=requerido).select_related('proveedor').order_by('nombre'):
        disponible = p.stock + camino.get(p.pk, D0)
        faltante = requerido[p.pk] - disponible
        filas.append({'p': p, 'requerido': requerido[p.pk], 'stock': p.stock, 'camino': camino.get(p.pk, D0),
                      'faltante': max(faltante, D0), 'ordenes': sorted(ordenes[p.pk])})
    return filas


def carga_capacidad(desde, hasta):
    """Horas planificadas por puesto (órdenes abiertas en el rango) frente a su capacidad según calendario."""
    from .models import CentroTrabajo
    carga = defaultdict(lambda: D0)
    for h in HoraOrden.objects.filter(orden__estado__in=['BORRADOR', 'CONFIRMADA', 'EN_PROCESO'],
                                      orden__fecha__range=[desde, hasta]):
        carga[h.centro_id] += h.horas_plan
    filas = []
    for c in CentroTrabajo.objects.filter(activo=True):
        capacidad = c.capacidad_entre(desde, hasta)
        filas.append({'centro': c, 'capacidad': capacidad, 'carga': carga[c.pk],
                      'uso': (carga[c.pk] / capacidad * 100).quantize(Decimal('0.1')) if capacidad else None})
    return filas
