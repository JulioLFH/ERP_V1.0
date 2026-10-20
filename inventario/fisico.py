"""Inventario físico (MI01–MI07 de SAP): documento con la foto del stock, conteo a ciegas (también desde el celular),
lista de diferencias con reconteo obligatorio fuera de la tolerancia, aprobación por monto (quien cuenta no aprueba)
y contabilización con ajustes de inventario (tabla 12 SUNAT 28) que llevan el acta como sustento.

Tipos: general (todo el almacén), cíclico ABC, aleatorio (sorteo con semilla registrada), por ubicación y stock en
cero. Opcionalmente bloquea los movimientos de sus productos mientras se cuenta."""
import random
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import Empresa, Kardex, Producto, Serie, StockAlmacen, StockLote
from core.permisos import puede

from .cierre import KardexCerrado
from .models import ConteoCiclico, ConteoItem, Operacion, TipoOperacion

D0 = Decimal('0')


class ErrorFisico(Exception):
    pass


class ProductoEnConteo(KardexCerrado):
    """Movimiento de un producto bloqueado por un inventario físico en curso (se avisa igual que el kardex
    cerrado)."""


def _numero():
    s, n = Serie.siguiente('INV', 'INV')
    return f'{s}-{n}'


# ---------------------------------------------------------------- bloqueo de movimientos
def validar_movimiento(producto, almacen):
    """Lanza ProductoEnConteo si el producto está en un inventario físico vigente con bloqueo en ese almacén."""
    if almacen is None:
        return
    doc = (ConteoCiclico.objects.filter(estado__in=ConteoCiclico.VIGENTES, bloquear=True, almacen=almacen,
                                        items__producto=producto).only('numero').first())
    if doc is not None:
        raise ProductoEnConteo(f'{producto.nombre} está en el inventario físico {doc.numero} del almacén {almacen}: '
                               f'no se mueve hasta contabilizarlo o anularlo (Inventario › Inventario físico).')


# ---------------------------------------------------------------- selección de productos por tipo
def _con_stock(almacen):
    return StockAlmacen.objects.filter(almacen=almacen, producto__activo=True, producto__tipo='BIEN')


def seleccion(almacen, tipo, n=20, semilla='', ubicacion='', clases=('A', 'B', 'C'), maximo=40, hoy=None):
    """Productos que entran al documento (ids, en orden) y la semilla usada en el sorteo."""
    if tipo == 'GENERAL':
        ids = list(_con_stock(almacen).exclude(cantidad=0).values_list('producto_id', flat=True))
    elif tipo == 'CICLICO':
        from .almacen import programa
        abiertos = set(ConteoItem.objects.filter(conteo__almacen=almacen, conteo__estado__in=ConteoCiclico.VIGENTES)
                       .values_list('producto_id', flat=True))
        filas = [f for f in programa(almacen, hoy) if f['vencido'] and f['clase'] in clases and
                 f['p'].pk not in abiertos]
        filas.sort(key=lambda f: (f['clase'], f['toca']))
        ids = [f['p'].pk for f in filas[:maximo]]
    elif tipo == 'ALEATORIO':
        candidatos = sorted(_con_stock(almacen).exclude(cantidad=0).values_list('producto_id', flat=True))
        semilla = semilla or timezone.now().strftime('%Y%m%d%H%M%S%f')
        ids = random.Random(semilla).sample(candidatos, min(n, len(candidatos)))
    elif tipo == 'UBICACION':
        if not ubicacion:
            raise ErrorFisico('Indique la ubicación o el pasillo a contar (ej. A-01).')
        ids = list(_con_stock(almacen).filter(ubicacion__codigo__istartswith=ubicacion)
                   .values_list('producto_id', flat=True))
    elif tipo == 'CERO':
        qs = _con_stock(almacen).filter(cantidad=0)
        if ubicacion:
            qs = qs.filter(ubicacion__codigo__istartswith=ubicacion)
        ids = list(qs.values_list('producto_id', flat=True))
    else:
        raise ErrorFisico('Tipo de inventario no válido.')
    return ids, semilla


@transaction.atomic
def crear(almacen, tipo, usuario, contador=None, bloquear=False, n=20, semilla='', ubicacion='',
          clases=('A', 'B', 'C'), maximo=40, tolerancia_pct=None, tolerancia_valor=None, fecha=None):
    """Documento con la foto del stock de los productos elegidos (una línea por lote en los productos con lote)."""
    hoy = fecha or timezone.localdate()
    ids, semilla = seleccion(almacen, tipo, n, semilla, ubicacion, clases, maximo, hoy)
    if not ids:
        raise ErrorFisico('No hay productos que contar con esos criterios.')
    doc = ConteoCiclico.objects.create(
        numero=_numero(), almacen=almacen, fecha=hoy, tipo=tipo, creado_por=usuario, contador=contador,
        bloquear=bloquear, semilla=semilla if tipo == 'ALEATORIO' else '', filtro_ubicacion=ubicacion[:20],
        foto_en=timezone.now(),
        **({'tolerancia_pct': tolerancia_pct} if tolerancia_pct is not None else {}),
        **({'tolerancia_valor': tolerancia_valor} if tolerancia_valor is not None else {}))
    clases_abc = {}
    if tipo == 'CICLICO':
        from .almacen import clasificar_abc
        clases_abc, _ = clasificar_abc(almacen, hoy=hoy)
    stocks = {s.producto_id: s for s in StockAlmacen.objects.filter(almacen=almacen, producto_id__in=ids)
              .select_related('ubicacion')}
    productos = {p.pk: p for p in Producto.objects.filter(pk__in=ids)}
    lotes = defaultdict(list)
    for sl in StockLote.objects.filter(almacen=almacen, lote__producto_id__in=ids).exclude(cantidad=0).select_related(
            'lote'):
        lotes[sl.lote.producto_id].append(sl)
    for pid in ids:
        p, s = productos[pid], stocks.get(pid)
        comun = {'conteo': doc, 'producto': p, 'clase': clases_abc.get(pid, ''),
                 'ubicacion': s.ubicacion.codigo if s and s.ubicacion_id else '', 'costo': p.costo_promedio}
        if p.control and lotes.get(pid):
            for sl in lotes[pid]:
                ConteoItem.objects.create(lote=sl.lote, sistema=sl.cantidad, **comun)
        else:
            ConteoItem.objects.create(sistema=s.cantidad if s else D0, **comun)
    return doc


# ---------------------------------------------------------------- conteo, reconteo y diferencias
def _movimientos_desde_foto(item):
    """Neto de lo que se movió en el almacén después de la foto (solo si el documento no bloquea)."""
    c = item.conteo
    if c.bloquear:
        return D0
    qs = Kardex.objects.filter(producto_id=item.producto_id, almacen_id=c.almacen_id, creado__gt=c.foto_en)
    if item.lote_id:
        qs = qs.filter(lote_id=item.lote_id)
    neto = D0
    for tipo, cant in qs.values_list('tipo', 'cantidad'):
        neto += cant if tipo == 'ENTRADA' else -cant
    return neto


@transaction.atomic
def registrar(doc, cantidades, usuario, reconteo=False, motivos=None):
    """cantidades = {item_id: cantidad contada}. El reconteo lo hace otro contador (si hay más de un usuario)."""
    if doc.estado not in ('ABIERTO', 'CONTADO'):
        raise ErrorFisico(f'El documento está {doc.get_estado_display().lower()}: ya no se cuenta.')
    items = {i.pk: i for i in doc.items.select_related('conteo')}
    hechos = 0
    ahora = timezone.now()
    for pk, cant in cantidades.items():
        i = items.get(pk)
        if i is None or cant is None:
            continue
        if cant < 0:
            raise ErrorFisico(f'{i.producto.nombre}: la cantidad no puede ser negativa.')
        if not reconteo and i.contado == cant:
            continue  # sin cambio (p. ej. se guardan solo los motivos): no se pierde el reconteo
        if reconteo:
            if not i.exige_reconteo and i.reconteo is None and not i.diferencia:
                continue
            if i.contado_por_id == usuario.pk and _hay_otro(usuario):
                raise ErrorFisico(f'{i.producto.nombre}: el reconteo lo hace un contador distinto al del primer '
                                  f'conteo.')
            i.reconteo, i.reconteo_por, i.reconteo_en = cant, usuario, ahora
        else:
            i.contado, i.contado_por, i.contado_en = cant, usuario, ahora
            i.reconteo, i.reconteo_por, i.reconteo_en = None, None, None
        i.movimientos = _movimientos_desde_foto(i)
        if motivos and motivos.get(pk) in dict(ConteoItem.MOTIVOS):
            i.motivo = motivos[pk]
        i.save()
        hechos += 1
    for pk, m in (motivos or {}).items():  # motivo de una diferencia sin volver a contar
        if pk in items and pk not in cantidades and m in dict(ConteoItem.MOTIVOS):
            ConteoItem.objects.filter(pk=pk).update(motivo=m)
    if not hechos and not motivos:
        raise ErrorFisico('Registre al menos una cantidad.')
    if not doc.items.filter(contado__isnull=True).exists():
        doc.estado = 'CONTADO'
        doc.save(update_fields=['estado'])
    return hechos


def _hay_otro(usuario):
    return get_user_model().objects.filter(is_active=True).exclude(pk=usuario.pk).exists()


def resumen(doc):
    items = list(doc.items.select_related('producto', 'lote', 'contado_por', 'reconteo_por'))
    contados = [i for i in items if i.contado is not None]
    con_dif = [i for i in contados if i.diferencia]
    faltante = sum((i.valor_diferencia for i in con_dif if i.valor_diferencia < 0), D0)
    sobrante = sum((i.valor_diferencia for i in con_dif if i.valor_diferencia > 0), D0)
    return {'items': items, 'total': len(items), 'contados': len(contados), 'con_diferencia': len(con_dif),
            'reconteos': [i for i in items if i.exige_reconteo], 'faltante': faltante, 'sobrante': sobrante,
            'neto': faltante + sobrante, 'absoluto': sobrante - faltante,
            'exactitud': (Decimal(len(contados) - len(con_dif)) / len(contados) * 100).quantize(Decimal('0.1'))
            if contados else None}


def _contadores(doc):
    return set(doc.items.exclude(contado_por=None).values_list('contado_por', flat=True)) | set(
        doc.items.exclude(reconteo_por=None).values_list('reconteo_por', flat=True))


def puede_aprobar(user, doc):
    """(bool, motivo). Quien contó no aprueba; hasta el tope de la empresa el jefe de almacén (Ajustes de
    inventario), más allá contraloría o gerencia."""
    if doc.estado != 'CONTADO':
        return False, 'Primero se cuentan todas las líneas.'
    if user.pk in _contadores(doc) and _hay_otro(user):
        return False, 'Quien contó no aprueba las diferencias (segregación de funciones).'
    r = resumen(doc)
    if r['reconteos']:
        return False, f'{len(r["reconteos"])} diferencia(s) fuera de la tolerancia esperan el reconteo.'
    if not puede(user, 'inventario.ajustar'):
        return False, 'Su usuario no aprueba ajustes de inventario.'
    tope = Empresa.actual().tope_diferencias_inventario
    if r['absoluto'] > tope and not puede(user, 'inventario.aprobar_mayor'):
        return False, (f'Las diferencias suman S/ {r["absoluto"]:,.2f}, más que el tope de S/ {tope:,.2f}: las aprueba '
                       f'contraloría o la gerencia.')
    return True, ''


@transaction.atomic
def aprobar(doc, usuario):
    ok, motivo = puede_aprobar(usuario, doc)
    if not ok:
        raise ErrorFisico(motivo)
    sin_motivo = [i.producto.nombre for i in resumen(doc)['items'] if i.diferencia and not i.motivo]
    if sin_motivo:
        raise ErrorFisico('Indique el motivo de cada diferencia: ' + ', '.join(sin_motivo[:5]) +
                          (' …' if len(sin_motivo) > 5 else ''))
    doc.estado, doc.aprobado_por, doc.aprobado_en = 'APROBADO', usuario, timezone.now()
    doc.save(update_fields=['estado', 'aprobado_por', 'aprobado_en'])
    return doc


def acta(doc):
    """Excel del acta de inventario: foto, conteos, reconteos, diferencias, motivos y quiénes intervinieron."""
    from core.utils import excel_response
    filas = []
    for i in doc.items.select_related('producto', 'lote', 'contado_por', 'reconteo_por'):
        filas.append([i.ubicacion, i.producto.codigo, i.producto.nombre, i.lote.codigo if i.lote_id else '',
                      i.clase, i.sistema, i.movimientos, i.contado, i.reconteo, i.diferencia, i.costo,
                      i.valor_diferencia, i.get_motivo_display() if i.motivo else '',
                      i.contado_por.username if i.contado_por_id else '',
                      i.reconteo_por.username if i.reconteo_por_id else ''])
    titulo = (f'ACTA DE INVENTARIO FÍSICO {doc.numero} ({doc.get_tipo_display()}) - {doc.almacen} - foto '
              f'{timezone.localtime(doc.foto_en):%d/%m/%Y %H:%M}')
    return excel_response(f'Acta_{doc.numero}', titulo, [
        'Ubicación', 'Código', 'Producto', 'Lote', 'Clase', 'Sistema (foto)', 'Movimientos después', 'Contado',
        'Reconteo', 'Diferencia', 'Costo unit.', 'Valor diferencia', 'Motivo', 'Contó', 'Recontó'], filas)


@transaction.atomic
def contabilizar(doc, usuario):
    """Ajustes de ingreso (sobrantes) y salida (faltantes) con el acta como sustento; el asiento sale en la
    centralización del periodo (cuenta de cada tipo de ajuste)."""
    from core.sustentos import guardar_archivo, vincular

    from . import servicios
    if doc.estado != 'APROBADO':
        raise ErrorFisico('Solo se contabiliza un inventario aprobado.')
    items = [i for i in doc.items.select_related('producto', 'lote') if i.diferencia]
    archivo = guardar_archivo(None, usuario, datos=acta(doc).content, nombre=f'Acta_{doc.numero}.xlsx',
                              tipo='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # se cierra antes de ajustar: el bloqueo de movimientos ya no aplica a sus propios ajustes
    doc.estado, doc.cerrado_por, doc.cerrado_en = 'CERRADO', usuario, timezone.now()
    doc.save(update_fields=['estado', 'cerrado_por', 'cerrado_en'])
    fecha = timezone.localdate()
    for codigo, signo, campo in (('AJ_ING', 1, 'ajuste_ingreso'), ('AJ_SAL', -1, 'ajuste_salida')):
        lineas = [i for i in items if i.diferencia * signo > 0]
        if not lineas:
            continue
        op = Operacion.objects.create(
            tipo=TipoOperacion.objects.get(codigo=codigo), fecha=fecha, referencia=doc.numero, creado_por=usuario,
            glosa=f'Diferencias del inventario físico {doc.numero} ({doc.get_tipo_display().lower()})',
            **({'almacen_destino': doc.almacen} if signo > 0 else {'almacen_origen': doc.almacen}))
        for i in lineas:
            op.items.create(producto=i.producto, cantidad=abs(i.diferencia),
                            lote=i.lote.codigo if i.lote_id else '',
                            vencimiento=i.lote.vencimiento if i.lote_id and signo > 0 else None,
                            costo_unitario=(i.costo or i.producto.costo_promedio) if signo > 0 else None)
        vincular(op, archivo, usuario, f'Acta del inventario físico {doc.numero}')
        try:
            servicios.confirmar(op, usuario)
        except servicios.ErrorOperacion as exc:
            raise ErrorFisico(f'No se pudo ajustar: {exc}') from exc
        setattr(doc, campo, op)
    doc.save(update_fields=['ajuste_ingreso', 'ajuste_salida'])
    vincular(doc, archivo, usuario, 'Acta del inventario físico')
    return doc


@transaction.atomic
def anular(doc, usuario):
    if doc.estado not in ConteoCiclico.VIGENTES:
        raise ErrorFisico('Solo se anula un inventario no contabilizado.')
    doc.estado = 'ANULADO'
    doc.save(update_fields=['estado'])
    return doc


# ---------------------------------------------------------------- indicadores
def indicadores(almacen=None, desde=None, hasta=None):
    """IRA por almacén, clase y contador; cumplimiento del programa cíclico; valor de diferencias por motivo y mes;
    productos sin conteo en 12 meses."""
    hoy = timezone.localdate()
    desde, hasta = desde or hoy - timedelta(days=90), hasta or hoy
    items = ConteoItem.objects.filter(conteo__estado='CERRADO', conteo__fecha__range=(desde, hasta),
                                      contado__isnull=False).select_related('conteo__almacen', 'contado_por',
                                                                           'producto')
    if almacen:
        items = items.filter(conteo__almacen=almacen)
    ira = {'almacen': defaultdict(lambda: [0, 0]), 'clase': defaultdict(lambda: [0, 0]),
           'contador': defaultdict(lambda: [0, 0])}
    por_motivo, por_mes = defaultdict(lambda: D0), defaultdict(lambda: [D0, D0])
    for i in items:
        exacto = not i.diferencia
        for clave, valor in (('almacen', str(i.conteo.almacen)), ('clase', i.clase or '—'),
                             ('contador', i.contado_por.username if i.contado_por_id else '—')):
            ira[clave][valor][0] += 1
            ira[clave][valor][1] += exacto
        if i.diferencia:
            v = i.valor_diferencia
            por_motivo[i.get_motivo_display() if i.motivo else 'Sin motivo'] += v
            mes = i.conteo.fecha.strftime('%Y-%m')
            por_mes[mes][0 if v < 0 else 1] += v

    def pct(par):
        return (Decimal(par[1]) / par[0] * 100).quantize(Decimal('0.1')) if par[0] else None
    salida = {k: sorted(((nombre, par[0], pct(par)) for nombre, par in v.items()), key=lambda x: x[0])
              for k, v in ira.items()}
    total = [sum(p[0] for p in ira['almacen'].values()), sum(p[1] for p in ira['almacen'].values())]
    # cumplimiento del programa cíclico: de lo que tocaba contar (vencido al inicio del rango), cuánto se contó
    cumplimiento = None
    if almacen:
        from .almacen import programa
        tocaban = [f for f in programa(almacen, desde) if f['vencido']]
        contados = set(items.values_list('producto_id', flat=True))
        if tocaban:
            hechos = sum(1 for f in tocaban if f['p'].pk in contados)
            cumplimiento = {'tocaban': len(tocaban), 'contados': hechos,
                            'pct': (Decimal(hechos) / len(tocaban) * 100).quantize(Decimal('0.1'))}
    # nunca contados (o no en los últimos 12 meses), con stock
    ultimos = dict(ConteoItem.objects.filter(conteo__estado='CERRADO', contado__isnull=False)
                   .values_list('producto_id').annotate(f=Max('conteo__fecha')))
    stock = StockAlmacen.objects.filter(producto__activo=True, producto__tipo='BIEN').exclude(cantidad=0)
    if almacen:
        stock = stock.filter(almacen=almacen)
    limite = hoy - timedelta(days=365)
    nunca = []
    for s in stock.select_related('producto', 'almacen').order_by('producto__nombre'):
        f = ultimos.get(s.producto_id)
        if f is None or f < limite:
            nunca.append({'p': s.producto, 'almacen': s.almacen, 'stock': s.cantidad, 'ultimo': f})
    return {'ira': salida, 'ira_total': pct(total) if total[0] else None, 'lineas': total[0],
            'cumplimiento': cumplimiento, 'por_motivo': sorted(por_motivo.items(), key=lambda x: x[1]),
            'por_mes': sorted((m, v[0], v[1]) for m, v in por_mes.items()), 'nunca': nunca, 'desde': desde,
            'hasta': hasta}
