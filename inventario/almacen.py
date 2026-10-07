"""Gestión de almacén avanzada: picking por olas (varios pedidos en un recorrido) e inventario cíclico ABC."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import Kardex, Producto, Serie, StockAlmacen

from .models import ConteoCiclico, ConteoItem, LineaOla, OlaPicking, Operacion, TipoOperacion

D0 = Decimal('0')
FRECUENCIA = {'A': 30, 'B': 90, 'C': 180}  # días entre conteos de cada clase
CORTES = (Decimal('80'), Decimal('95'))  # % acumulado del valor consumido: hasta 80 = A, hasta 95 = B


class ErrorAlmacen(Exception):
    pass


def _numero(tipo, serie):
    s, n = Serie.siguiente(tipo, serie)
    return f'{s}-{n}'


# ---------------------------------------------------------------- picking por olas
def pedidos_pendientes():
    """Órdenes de pedido de venta pendientes o aprobadas que aún no están en una ola abierta o preparada."""
    from ventas.models import Cotizacion
    return (Cotizacion.objects.filter(tipo='PED', estado__in=['PENDIENTE', 'APROBADO'])
            .exclude(olas__estado__in=['ABIERTA', 'PREPARADA']).select_related('tercero').order_by('fecha', 'id'))


@transaction.atomic
def crear_ola(almacen, pedidos, usuario, observaciones=''):
    """Junta los bienes de los pedidos por producto, con su ubicación habitual en el almacén, en orden de recorrido."""
    pedidos = list(pedidos)
    if not pedidos:
        raise ErrorAlmacen('Elija al menos un pedido.')
    ocupados = [p.numero for p in pedidos if p.olas.filter(estado__in=['ABIERTA', 'PREPARADA']).exists()]
    if ocupados:
        raise ErrorAlmacen(f'Ya están en otra ola: {", ".join(ocupados)}.')
    total, por_pedido_ = defaultdict(lambda: D0), defaultdict(lambda: defaultdict(lambda: D0))
    for ped in pedidos:
        for i in ped.items.select_related('producto'):
            if i.producto_id and i.producto.tipo == 'BIEN':
                total[i.producto_id] += i.cantidad
                por_pedido_[i.producto_id][ped.numero] += i.cantidad
    detalle = {pid: {n: format(c.normalize(), 'f') for n, c in d.items()} for pid, d in por_pedido_.items()}
    if not total:
        raise ErrorAlmacen('Los pedidos no tienen bienes por preparar.')
    ola = OlaPicking.objects.create(numero=_numero('OLA', 'OLA'), almacen=almacen, creado_por=usuario,
                                    observaciones=(observaciones or '')[:200])
    ola.pedidos.set(pedidos)
    lugares = StockAlmacen.ubicaciones(almacen.pk, list(total))
    for pid, cantidad in total.items():
        LineaOla.objects.create(ola=ola, producto_id=pid, ubicacion=lugares.get(pid, ''), cantidad=cantidad,
                                detalle=detalle[pid])
    return ola


def lineas_con_stock(ola):
    stock = dict(StockAlmacen.objects.filter(almacen=ola.almacen, producto_id__in=ola.lineas.values('producto'))
                 .values_list('producto_id', 'cantidad'))
    filas = []
    for li in ola.lineas.select_related('producto'):
        disponible = stock.get(li.producto_id, D0)
        filas.append({'l': li, 'stock': disponible, 'falta': max(li.cantidad - disponible, D0)})
    # recorrido: primero lo ubicado (por código de ubicación), al final lo que no tiene ubicación
    filas.sort(key=lambda f: (not f['l'].ubicacion, f['l'].ubicacion, f['l'].producto.nombre))
    return filas


def por_pedido(ola):
    """Separación (put-wall): qué va en cada pedido."""
    salida = defaultdict(list)
    for li in ola.lineas.select_related('producto'):
        for numero, cant in (li.detalle or {}).items():
            salida[numero].append((li.producto, Decimal(cant)))
    return sorted(salida.items())


@transaction.atomic
def confirmar_preparacion(ola, preparadas, usuario):
    """preparadas: {linea_id: cantidad}. Lo no indicado se toma como preparado completo."""
    if ola.estado != 'ABIERTA':
        raise ErrorAlmacen('La ola ya fue preparada o anulada.')
    for li in ola.lineas.all():
        cant = preparadas.get(li.pk, li.cantidad)
        if cant is None or cant < 0:
            raise ErrorAlmacen('Las cantidades preparadas no pueden ser negativas.')
        li.preparada = cant
        li.save(update_fields=['preparada'])
    ola.estado, ola.preparado_por, ola.preparado_en = 'PREPARADA', usuario, timezone.now()
    ola.save(update_fields=['estado', 'preparado_por', 'preparado_en'])
    return ola


# ---------------------------------------------------------------- inventario cíclico ABC
def clasificar_abc(almacen, dias=365, hoy=None):
    """{producto_id: 'A'|'B'|'C'} según el valor de lo que salió del almacén en el último año (Pareto): los que
    suman el 80 % del valor son A, hasta el 95 % B y el resto C. Los que tienen stock y no se movieron son C."""
    hoy = hoy or timezone.localdate()
    salidas = (Kardex.objects.filter(almacen=almacen, tipo='SALIDA', fecha__gt=hoy - timedelta(days=dias),
                                     producto__tipo='BIEN')
               .values_list('producto_id', 'cantidad', 'costo_promedio'))
    valor = defaultdict(lambda: D0)
    for pid, cant, costo in salidas:
        valor[pid] += cant * (costo or D0)
    total = sum(valor.values(), D0)
    clases, acumulado = {}, D0
    for pid, v in sorted(valor.items(), key=lambda x: -x[1]):
        pct_antes = acumulado / total * 100 if total else Decimal('100')
        acumulado += v
        clases[pid] = 'A' if pct_antes < CORTES[0] else 'B' if pct_antes < CORTES[1] else 'C'
    for pid in StockAlmacen.objects.filter(almacen=almacen).exclude(cantidad=0).values_list('producto_id', flat=True):
        clases.setdefault(pid, 'C')
    return clases, valor


def ultimos_conteos(almacen):
    return dict(ConteoItem.objects.filter(conteo__almacen=almacen, conteo__estado='CERRADO',
                                          contado__isnull=False)
                .values_list('producto_id').annotate(f=Max('conteo__fecha')))


def programa(almacen, hoy=None):
    """Productos del almacén con su clase, último conteo y la fecha en que toca contarlos (vencidos primero)."""
    hoy = hoy or timezone.localdate()
    clases, valor = clasificar_abc(almacen, hoy=hoy)
    ultimos = ultimos_conteos(almacen)
    stock = dict(StockAlmacen.objects.filter(almacen=almacen, producto_id__in=clases)
                 .values_list('producto_id', 'cantidad'))
    filas = []
    for p in Producto.objects.filter(pk__in=clases, activo=True):
        clase = clases[p.pk]
        ultimo = ultimos.get(p.pk)
        toca = ultimo + timedelta(days=FRECUENCIA[clase]) if ultimo else hoy
        filas.append({'p': p, 'clase': clase, 'valor': valor.get(p.pk, D0), 'stock': stock.get(p.pk, D0),
                      'ultimo': ultimo, 'toca': toca, 'vencido': toca <= hoy})
    filas.sort(key=lambda f: (f['toca'], f['clase'], f['p'].nombre))
    return filas


@transaction.atomic
def generar_conteo(almacen, usuario, clases=('A', 'B', 'C'), maximo=40, hoy=None):
    """Conteo con los productos a los que les toca (A primero), hasta `maximo` líneas, en orden de ubicación."""
    hoy = hoy or timezone.localdate()
    abiertos = set(ConteoItem.objects.filter(conteo__almacen=almacen, conteo__estado='ABIERTO')
                   .values_list('producto_id', flat=True))
    candidatos = [f for f in programa(almacen, hoy) if f['vencido'] and f['clase'] in clases
                  and f['p'].pk not in abiertos]
    candidatos.sort(key=lambda f: (f['clase'], f['toca']))
    candidatos = candidatos[:maximo]
    if not candidatos:
        raise ErrorAlmacen('No hay productos que toque contar (o ya están en un conteo abierto).')
    conteo = ConteoCiclico.objects.create(numero=_numero('CIC', 'CIC'), almacen=almacen, fecha=hoy,
                                          creado_por=usuario)
    lugares = StockAlmacen.ubicaciones(almacen.pk, [f['p'].pk for f in candidatos])
    for f in candidatos:
        ConteoItem.objects.create(conteo=conteo, producto=f['p'], clase=f['clase'], sistema=f['stock'],
                                  ubicacion=lugares.get(f['p'].pk, ''))
    return conteo


def _acta(conteo):
    from core.utils import excel_response
    return excel_response(f'Acta_{conteo.numero}', f'ACTA DE CONTEO CÍCLICO {conteo.numero} - {conteo.almacen}', [
        'Ubicación', 'Código', 'Producto', 'Clase', 'Stock sistema', 'Contado', 'Diferencia'],
        [[i.ubicacion, i.producto.codigo, i.producto.nombre, i.clase, i.sistema, i.contado, i.diferencia]
         for i in conteo.items.select_related('producto')]).content


@transaction.atomic
def cerrar_conteo(conteo, contados, usuario):
    """Registra lo contado y ajusta las diferencias (sobrantes y faltantes) con el acta del conteo como sustento.
    contados: {item_id: cantidad contada}; las líneas sin conteo quedan sin ajustar."""
    from core.sustentos import guardar_archivo, vincular

    from . import servicios
    if conteo.estado != 'ABIERTO':
        raise ErrorAlmacen('El conteo ya está cerrado.')
    items = list(conteo.items.select_related('producto'))
    for i in items:
        if i.pk in contados:
            if contados[i.pk] is not None and contados[i.pk] < 0:
                raise ErrorAlmacen(f'{i.producto.nombre}: el conteo no puede ser negativo.')
            i.contado = contados[i.pk]
            i.save(update_fields=['contado'])
    if not any(i.contado is not None for i in items):
        raise ErrorAlmacen('Registre al menos un conteo.')
    acta = guardar_archivo(None, usuario, datos=_acta(conteo), nombre=f'Acta_{conteo.numero}.xlsx',
                           tipo='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    for codigo, signo, campo in (('AJ_ING', 1, 'ajuste_ingreso'), ('AJ_SAL', -1, 'ajuste_salida')):
        lineas = [i for i in items if i.diferencia is not None and i.diferencia * signo > 0]
        if not lineas:
            continue
        op = Operacion.objects.create(
            tipo=TipoOperacion.objects.get(codigo=codigo), fecha=conteo.fecha, referencia=conteo.numero,
            glosa=f'Diferencias del conteo cíclico {conteo.numero}', creado_por=usuario,
            **({'almacen_destino': conteo.almacen} if signo > 0 else {'almacen_origen': conteo.almacen}))
        for i in lineas:
            op.items.create(producto=i.producto, cantidad=abs(i.diferencia),
                            costo_unitario=i.producto.costo_promedio if signo > 0 else None)
        vincular(op, acta, usuario, f'Acta del conteo cíclico {conteo.numero}')
        try:
            servicios.confirmar(op, usuario)
        except servicios.ErrorOperacion as exc:
            raise ErrorAlmacen(f'No se pudo ajustar: {exc}') from exc
        setattr(conteo, campo, op)
    conteo.estado, conteo.cerrado_por, conteo.cerrado_en = 'CERRADO', usuario, timezone.now()
    conteo.save()
    return conteo


def exactitud(almacen, desde):
    """Exactitud de inventario de los conteos cerrados desde la fecha: % de líneas sin diferencia."""
    qs = ConteoItem.objects.filter(conteo__almacen=almacen, conteo__estado='CERRADO', conteo__fecha__gte=desde,
                                   contado__isnull=False)
    total = qs.count()
    if not total:
        return None
    exactas = sum(1 for s, c in qs.values_list('sistema', 'contado') if s == c)
    return {'lineas': total, 'exactas': exactas, 'pct': (Decimal(exactas) / total * 100).quantize(Decimal('0.1'))}
