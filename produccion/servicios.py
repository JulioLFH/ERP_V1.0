"""Reglas de manufactura: costo estándar por receta, órdenes de producción y requerimiento de materiales."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import Producto, Serie, r2

from .models import ConsumoOrden, HoraOrden, ListaMateriales, OrdenProduccion

D0 = Decimal('0')
D4 = Decimal('0.0001')


class ErrorProduccion(Exception):
    pass


def receta_vigente(producto):
    return ListaMateriales.objects.filter(producto=producto, activa=True).order_by('-creado').first()


# ---------------------------------------------------------------- costo estándar
def costo_insumo(producto, visitados=None):
    """Costo unitario de un insumo: costo promedio del kardex; si no tiene (aún no se compra ni produce), su costo
    estándar por receta o, al final, su precio de compra referencial."""
    if producto.costo_promedio:
        return producto.costo_promedio
    receta = receta_vigente(producto)
    visitados = set(visitados or ())
    if receta and producto.pk not in visitados:
        return hoja_costos(receta, visitados | {producto.pk})['unitario']
    return producto.precio_compra or D0


def hoja_costos(lista, visitados=None, cantidad=None):
    """Costo estándar de la receta para `cantidad` unidades (por defecto, su lote base).

    Devuelve materiales, mano de obra, costos indirectos, total y unitario con el detalle de cada línea."""
    visitados = set(visitados or ()) | {lista.producto_id}
    factor = (cantidad / lista.cantidad_base) if cantidad and lista.cantidad_base else Decimal('1')
    materiales, horas = [], []
    for c in lista.componentes.select_related('producto'):
        cant = (c.cantidad_con_merma * factor).quantize(D4)
        costo = costo_insumo(c.producto, visitados)
        materiales.append({'producto': c.producto, 'cantidad': cant, 'merma': c.merma, 'costo': costo,
                           'valor': r2(cant * costo)})
    for o in lista.operaciones.select_related('centro'):
        h = (o.horas * factor).quantize(Decimal('0.01'))
        mo, cif = r2(h * o.centro.costo_hora_mo), r2(h * o.centro.costo_hora_cif)
        horas.append({'centro': o.centro, 'descripcion': o.descripcion, 'horas': h, 'mo': mo, 'cif': cif,
                      'total': mo + cif})
    tot_mat = sum((m['valor'] for m in materiales), D0)
    tot_mo = sum((h['mo'] for h in horas), D0)
    tot_cif = sum((h['cif'] for h in horas), D0)
    unidades = cantidad or lista.cantidad_base
    total = tot_mat + tot_mo + tot_cif
    return {'lista': lista, 'cantidad': unidades, 'materiales': materiales, 'horas': horas, 'tot_materiales': tot_mat,
            'tot_mano_obra': tot_mo, 'tot_cif': tot_cif, 'tot_conversion': tot_mo + tot_cif, 'total': total,
            'unitario': (total / unidades).quantize(D4) if unidades else D0}


# ---------------------------------------------------------------- órdenes de producción
def explotar(orden):
    """Consumos y horas planificados de la orden según su receta (se recalculan mientras no se inicie)."""
    lista = orden.lista
    factor = orden.cantidad / lista.cantidad_base if lista.cantidad_base else Decimal('1')
    orden.consumos.all().delete()
    orden.horas.all().delete()
    for c in lista.componentes.all():
        cant = (c.cantidad_con_merma * factor).quantize(D4)
        ConsumoOrden.objects.create(orden=orden, producto_id=c.producto_id, cantidad_plan=cant, cantidad_real=cant)
    for o in lista.operaciones.all():
        h = (o.horas * factor).quantize(Decimal('0.01'))
        HoraOrden.objects.create(orden=orden, centro_id=o.centro_id, descripcion=o.descripcion, horas_plan=h,
                                 horas_real=h)


def disponibilidad(orden):
    """Por insumo: requerido, stock en el almacén de insumos y faltante."""
    filas = []
    for c in orden.consumos.select_related('producto'):
        stock = c.producto.stock_en(orden.almacen_insumos)
        requerido = c.cantidad_real if orden.estado in ('BORRADOR', 'CONFIRMADA', 'EN_PROCESO') else c.cantidad_plan
        filas.append({'consumo': c, 'requerido': requerido, 'stock': stock, 'faltante': max(requerido - stock, D0)})
    return filas


def confirmar(orden, usuario=None):
    if orden.estado != 'BORRADOR':
        raise ErrorProduccion('Solo se confirman órdenes en borrador.')
    if not orden.consumos.exists():
        raise ErrorProduccion('La receta no tiene insumos.')
    with transaction.atomic():
        serie, numero = Serie.siguiente('OPR', 'OP01')
        orden.numero = f'{serie}-{numero}'
        orden.costo_estandar_unit = hoja_costos(orden.lista)['unitario']
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
    materiales + mano de obra + costos indirectos (horas reales por centro de trabajo).

    consumos: {consumo_id: cantidad real}; horas: {hora_id: horas reales}."""
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    if orden.estado not in ('CONFIRMADA', 'EN_PROCESO'):
        raise ErrorProduccion('Solo se terminan órdenes confirmadas o en proceso.')
    if not cantidad_producida or cantidad_producida <= 0:
        raise ErrorProduccion('Indique la cantidad producida.')
    fecha = fecha or timezone.localdate()
    with transaction.atomic():
        lineas = list(orden.consumos.select_related('producto'))
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
            h.save(update_fields=['horas_real'])
            mo += r2(h.horas_real * h.centro.costo_hora_mo)
            cif += r2(h.horas_real * h.centro.costo_hora_cif)
        op = Operacion.objects.create(
            tipo=TipoOperacion.objects.get(codigo='MANUF'), fecha=fecha, almacen_origen=orden.almacen_insumos,
            almacen_destino=orden.almacen_destino, referencia=orden.numero, creado_por=usuario,
            glosa=f'Orden de producción {orden.numero}: {orden.producto.nombre}', costo_adicional=mo + cif)
        for c in lineas:
            if c.cantidad_real > 0:
                op.items.create(producto=c.producto, cantidad=r2(c.cantidad_real), rol='INSUMO')
        # producto con control por lote: el lote de producción es el número de la orden
        op.items.create(producto=orden.producto, cantidad=cantidad_producida, rol='PRODUCTO',
                        lote=orden.numero if orden.producto.control == 'LOTE' else '')
        try:
            inv.confirmar(op, usuario)
        except inv.ErrorOperacion as exc:
            raise ErrorProduccion(str(exc)) from exc
        costos = {i.producto_id: i.costo_unitario for i in op.items.filter(rol='INSUMO')}
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
        orden.estado, orden.motivo_anulacion = 'ANULADA', motivo.strip()[:250]
        orden.anulado_por = usuario if usuario and usuario.is_authenticated else None
        orden.anulado_en = timezone.now()
        orden.save()
    return orden


def variaciones(orden):
    """Real vs estándar de una orden terminada, al volumen realmente producido."""
    estandar = hoja_costos(orden.lista, cantidad=orden.cantidad_producida)
    filas = [('Materiales', estandar['tot_materiales'], orden.costo_materiales),
             ('Mano de obra', estandar['tot_mano_obra'], orden.costo_mano_obra),
             ('Costos indirectos', estandar['tot_cif'], orden.costo_cif)]
    salida = [{'concepto': c, 'estandar': e, 'real': r, 'variacion': r - e,
               'pct': ((r - e) / e * 100).quantize(Decimal('0.1')) if e else None} for c, e, r in filas]
    total_e, total_r = estandar['total'], orden.costo_total
    salida.append({'concepto': 'Total', 'estandar': total_e, 'real': total_r, 'variacion': total_r - total_e,
                   'pct': ((total_r - total_e) / total_e * 100).quantize(Decimal('0.1')) if total_e else None})
    return salida


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
