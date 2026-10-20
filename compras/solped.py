"""Solicitud de pedido (SolPed, ME51N de SAP): la necesidad de compra que el área, el MRP, la reposición, un
requerimiento sin stock o una orden de mantenimiento piden y Compras convierte en orden de compra, licitación o pedido
contra contrato marco.

Borrador -> Por liberar (niveles secuenciales) -> Liberada -> Pedida en parte / Pedida -> Cerrada
(o Observada -> vuelve al solicitante; Rechazada; Anulada). Nadie libera su propia solicitud."""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.models import Empresa, PerfilUsuario, Serie, r2
from core.permisos import puede

from .models import LineaSolicitud, OrdenCompra, OrdenCompraItem, SolicitudPedido

log = logging.getLogger(__name__)
D0 = Decimal('0')


class ErrorSolped(Exception):
    pass


def _numero(tipo, serie):
    s, n = Serie.siguiente(tipo, serie)
    return f'{s}-{n}'


# ---------------------------------------------------------------- creación (todos los orígenes)
@transaction.atomic
def crear(cabecera, lineas, usuario, origen='MANUAL'):
    """cabecera: campos de SolicitudPedido; lineas: [dict de LineaSolicitud]. Queda en borrador."""
    lineas = [l for l in lineas if l.get('cantidad') and l['cantidad'] > 0]
    if not lineas:
        raise ErrorSolped('La solicitud no tiene ítems con cantidad.')
    sp = SolicitudPedido.objects.create(solicitante=usuario, origen=origen, **cabecera)
    for datos in lineas:
        p = datos.get('producto')
        datos.setdefault('descripcion', p.nombre if p else '')
        datos.setdefault('unidad', p.unidad if p else 'ZZ')
        LineaSolicitud.objects.create(solicitud=sp, **datos)
    return sp


def _contrato_para(producto, fecha=None):
    """(contrato, línea pactada) vigente del proveedor preferido (registro info) o habitual del producto, o
    (None, None)."""
    from .abastecimiento import contratos_vigentes
    from .registro_info import proveedor_y_plazo
    proveedor = proveedor_y_plazo(producto)[0]
    if proveedor is None:
        return None, None
    for c in contratos_vigentes(proveedor, fecha):
        linea = c.lineas.filter(producto=producto).first()
        if linea:
            return c, linea
    return None, None


def _oc_directa(filas, usuario, centro_costo, glosa):
    """[(producto, cantidad en unidad de almacén, fecha)] con contrato marco vigente -> una OC por contrato."""
    por_contrato = defaultdict(list)
    for producto, cantidad, fecha, contrato, pactada, origen in filas:
        por_contrato[contrato].append((producto, cantidad, fecha, pactada, origen))
    ordenes = []
    for contrato, items in por_contrato.items():
        oc = OrdenCompra.objects.create(
            numero=_numero('OC', 'OC01'), tercero=contrato.proveedor, fecha=timezone.localdate(), contrato=contrato,
            moneda=contrato.moneda, condicion_pago=contrato.condicion_pago[:100], centro_costo=centro_costo,
            fecha_entrega=min(f for _, _, f, _, _ in items), glosa=f'{glosa} contra el contrato {contrato.numero}'[:500])
        for producto, cantidad, _, pactada, origen in items:
            OrdenCompraItem.objects.create(documento=oc, producto=producto, descripcion=producto.nombre,
                                           cantidad=producto.a_compra(cantidad), precio_unitario=pactada.precio_unitario)
            if origen is not None:  # propuesta del MRP
                origen.orden_compra = oc
                origen.save(update_fields=['orden_compra'])
        oc.calcular_totales()
        oc.save()
        ordenes.append(oc)
    return ordenes


@transaction.atomic
def desde_mrp(propuestas, usuario, centro_costo):
    """Propuestas de compra del MRP -> una solicitud de pedido (liberada si ninguna regla pide liberarla). Con la opción
    de la empresa, lo que tiene proveedor habitual y contrato marco vigente va directo a orden de compra."""
    if centro_costo is None:
        raise ErrorSolped('Indique el centro de costo de la solicitud.')
    propuestas = [p for p in propuestas if p.tipo == 'COMPRAR' and not p.convertida]
    if not propuestas:
        raise ErrorSolped('Elija propuestas de compra aún no convertidas.')
    directas, lineas = [], []
    for p in propuestas:
        contrato, pactada = (_contrato_para(p.producto, p.fecha_inicio) if Empresa.actual().oc_directa_con_contrato
                             else (None, None))
        if contrato:
            directas.append((p.producto, p.cantidad, p.fecha_necesidad, contrato, pactada, p))
        else:
            lineas.append(p)
    ordenes = _oc_directa(directas, usuario, centro_costo, 'Generada por el MRP') if directas else []
    sp = None
    if lineas:
        sp = crear({'centro_costo': centro_costo, 'fecha_requerida': min(p.fecha_necesidad for p in lineas),
                    'tipo': 'MATERIAL', 'motivo': 'Necesidades de materiales del MRP'},
                   [{'producto': p.producto, 'cantidad': p.cantidad, 'proveedor_sugerido': p.proveedor or
                     p.producto.proveedor} for p in lineas], usuario, origen='MRP')
        for p in lineas:
            p.solicitud = sp
            p.save(update_fields=['solicitud'])
        enviar(sp, usuario, liberar_sin_reglas=True)
    return sp, ordenes


@transaction.atomic
def desde_reposicion(filas, usuario, centro_costo):
    """Sugerencia de compra (punto de reorden) -> una solicitud por proveedor habitual (o directo a OC si tiene
    contrato marco y la empresa lo permite)."""
    if centro_costo is None:
        raise ErrorSolped('Indique el centro de costo de las solicitudes.')
    hoy = timezone.localdate()
    por_proveedor, directas = defaultdict(list), []
    for f in filas:
        p = f['p']
        contrato, pactada = _contrato_para(p) if Empresa.actual().oc_directa_con_contrato else (None, None)
        if contrato:
            directas.append((p, f['unidades'], hoy + timedelta(days=p.tiempo_entrega or 0), contrato, pactada, None))
        else:
            por_proveedor[p.proveedor_id].append(f)
    ordenes = _oc_directa(directas, usuario, centro_costo, 'Reposición de stock') if directas else []
    solicitudes = []
    for _, grupo in por_proveedor.items():
        sp = crear({'centro_costo': centro_costo, 'tipo': 'MATERIAL',
                    'fecha_requerida': hoy + timedelta(days=max(f['p'].tiempo_entrega or 0 for f in grupo)),
                    'motivo': 'Reposición de stock (punto de reorden)'},
                   [{'producto': f['p'], 'cantidad': f['unidades'], 'proveedor_sugerido': f['p'].proveedor,
                     'precio_estimado': (f['precio'] / (f['p'].factor or 1)) if f['precio'] else D0} for f in grupo],
                   usuario, origen='REPOSICION')
        enviar(sp, usuario)
        solicitudes.append(sp)
    return solicitudes, ordenes


@transaction.atomic
def desde_requerimiento(req, usuario):
    """Lo que el almacén no tiene de un requerimiento aprobado -> solicitud de pedido vinculada (el área ve el
    avance en su requerimiento). None si no falta nada."""
    from inventario.requerimientos import faltantes
    filas = [(i, c) for i, c in faltantes(req) if not i.lineas_solped.exclude(
        solicitud__estado__in=('RECHAZADA', 'ANULADA')).exists()]
    if not filas:
        return None
    sp = crear({'centro_costo': req.centro_costo, 'fecha_requerida': req.fecha_requerida, 'tipo': 'MATERIAL',
                'motivo': f'Faltantes del requerimiento {req.numero}: {req.motivo}'[:250], 'requerimiento': req},
               [{'producto': i.producto, 'cantidad': c, 'proveedor_sugerido': i.producto.proveedor,
                 'requerimiento_item': i} for i, c in filas], usuario, origen='REQUERIMIENTO')
    enviar(sp, usuario)
    return sp


def iniciales_mantenimiento(ot):
    """(cabecera, líneas) para una solicitud desde una orden de mantenimiento: repuestos sin stock suficiente y el
    servicio externo, cargados a la orden y al centro de costo del equipo."""
    from contabilidad.models import CentroCosto
    equipo = ot.equipo
    centro = (equipo.centro.centro_costo if getattr(equipo, 'centro_id', None) and equipo.centro.centro_costo_id
              else CentroCosto.objects.filter(activo=True, tipo='PRODUCCION').first())
    lineas = []
    for r in ot.repuestos.select_related('producto', 'almacen'):
        falta = r.cantidad - max(r.producto.stock_en(r.almacen), D0)
        if falta > 0:
            lineas.append({'producto': r.producto, 'descripcion': r.producto.nombre, 'unidad': r.producto.unidad,
                           'cantidad': falta, 'proveedor_sugerido': r.producto.proveedor})
    if ot.proveedor_id or not lineas:
        lineas.append({'producto': None, 'descripcion': f'Servicio de mantenimiento: {ot.descripcion}'[:250],
                       'unidad': 'ZZ', 'cantidad': Decimal('1'), 'proveedor_sugerido': ot.proveedor})
    cabecera = {'centro_costo': centro, 'tipo': 'MATERIAL' if all(l['producto'] for l in lineas) else 'SERVICIO',
                'fecha_requerida': ot.fecha_programada, 'urgente': ot.tipo == 'CORRECTIVO',
                'motivo': f'{ot} · {equipo}: {ot.descripcion}'[:250]}
    return cabecera, lineas


# ---------------------------------------------------------------- liberación por niveles (motor único)
def calcular_niveles(sp):
    """[{nivel, usuario, nombre, desde}] según las reglas de aprobación de solicitudes (monto, centro, tipo)."""
    from core import aprobaciones
    try:
        return aprobaciones.calcular_niveles('SOLPED', sp.solicitante, sp.monto, sp.centro_costo, sp.tipo,
                                             sp.urgente)
    except aprobaciones.ErrorAprobacion as exc:
        raise ErrorSolped(str(exc)) from exc


def puede_liberar(user, sp):
    """Nadie libera su propia solicitud; el titular del nivel, su suplente o su jefe si el nivel escaló."""
    from core import aprobaciones
    if sp.estado != 'ENVIADA':
        return False
    return aprobaciones.puede_aprobar(user, 'SOLPED', sp.niveles, sp.nivel_aprobado, sp.solicitante_id)


def _validar(sp):
    lineas = list(sp.lineas.select_related('producto'))
    if not lineas:
        raise ErrorSolped('Agregue al menos un ítem.')
    errores = []
    for l in lineas:
        if l.cantidad <= 0:
            errores.append(f'{l.descripcion}: cantidad mayor a cero.')
        if not l.producto_id and not l.descripcion.strip():
            errores.append('Describa el servicio o material de cada ítem sin producto.')
        if l.producto_id and not l.producto.puede_comprarse:
            errores.append(f'{l.producto.nombre} no se compra (ficha del producto).')
    if sp.tipo == 'ACTIVO' and any(not (l.cuenta_contable_id or (l.producto_id and l.producto.clase == 'ACTIVO'))
                                   for l in lineas):
        errores.append('Solicitud de activo fijo: indique la cuenta del activo (33) en cada ítem.')
    if errores:
        raise ErrorSolped(' '.join(errores))


@transaction.atomic
def enviar(sp, usuario, liberar_sin_reglas=False, comentario=''):
    if not sp.editable:
        raise ErrorSolped(f'La solicitud está {sp.get_estado_display().lower()}: ya no se envía.')
    _validar(sp)
    avisos = control_presupuesto(sp)
    modo = Empresa.actual().presupuesto_solped
    if avisos and modo == 'BLOQUEAR':
        raise ErrorSolped('Supera el presupuesto: ' + ' '.join(avisos))
    if not sp.numero:
        sp.numero = _numero('SP', 'SP01')
    sp.aviso_presupuesto = (' '.join(avisos) if modo else '')[:300]
    niveles = calcular_niveles(sp)
    ahora = timezone.now()
    if not niveles and liberar_sin_reglas:  # MRP sin regla que aplique: queda liberada
        sp.estado, sp.niveles, sp.nivel_aprobado, sp.enviada_en, sp.liberada_en = 'LIBERADA', [], 0, ahora, ahora
        sp.comentario = ''
        sp.save()
        return sp
    sp.niveles = niveles or [{'nivel': 1, 'usuario': None, 'nombre': 'Liberador de solicitudes',
                              'desde': ahora.isoformat()}]
    sp.estado, sp.nivel_aprobado, sp.enviada_en, sp.comentario = 'ENVIADA', 0, ahora, (comentario or '')[:300]
    sp.save()
    from core.aprobaciones import registrar
    registrar('SOLPED', sp, usuario, 'ENVIADA', comentario)
    notificar(sp, 'enviada')
    return sp


@transaction.atomic
def liberar(sp, usuario, comentario=''):
    from core import aprobaciones
    if not puede_liberar(usuario, sp):
        raise ErrorSolped('No le corresponde liberar esta solicitud (nivel, permiso o es su propia solicitud).')
    sp.nivel_aprobado = sp.nivel_pendiente['nivel']
    aprobaciones.registrar('SOLPED', sp, usuario, 'LIBERADA', comentario, sp.nivel_aprobado)
    if sp.nivel_pendiente is None:
        sp.estado, sp.liberada_en = 'LIBERADA', timezone.now()
    else:
        aprobaciones.avanzar(sp.niveles, sp.nivel_aprobado)
    sp.comentario = (comentario or '')[:300]
    sp.save()
    notificar(sp, 'liberada' if sp.estado == 'LIBERADA' else 'enviada')
    return sp


@transaction.atomic
def observar(sp, usuario, comentario):
    if not puede_liberar(usuario, sp):
        raise ErrorSolped('No le corresponde revisar esta solicitud.')
    if len((comentario or '').strip()) < 5:
        raise ErrorSolped('Escriba qué debe corregir el solicitante.')
    sp.estado, sp.comentario = 'OBSERVADA', comentario.strip()[:300]
    sp.save()
    from core.aprobaciones import registrar
    registrar('SOLPED', sp, usuario, 'OBSERVADA', comentario, sp.nivel_pendiente['nivel'] if sp.nivel_pendiente
              else None)
    notificar(sp, 'observada')
    return sp


@transaction.atomic
def rechazar(sp, usuario, comentario):
    if not puede_liberar(usuario, sp):
        raise ErrorSolped('No le corresponde revisar esta solicitud.')
    if len((comentario or '').strip()) < 10:
        raise ErrorSolped('Indique el motivo del rechazo (mínimo 10 caracteres).')
    sp.estado, sp.comentario = 'RECHAZADA', comentario.strip()[:300]
    sp.save()
    from core.aprobaciones import registrar
    registrar('SOLPED', sp, usuario, 'RECHAZADA', comentario)
    notificar(sp, 'rechazada')
    return sp


@transaction.atomic
def anular(sp, usuario, motivo):
    if sp.estado in ('ANULADA', 'RECHAZADA', 'CERRADA'):
        raise ErrorSolped('La solicitud ya está cerrada.')
    if len((motivo or '').strip()) < 10:
        raise ErrorSolped('Indique el motivo de la anulación (mínimo 10 caracteres).')
    if OrdenCompraItem.objects.filter(linea_solped__solicitud=sp).exclude(documento__estado='ANULADO').exists():
        raise ErrorSolped('Ya tiene órdenes de compra: anúlelas primero o cierre lo que falta pedir.')
    if usuario.pk != sp.solicitante_id and not puede(usuario, 'requerimientos.liberar_solped'):
        raise ErrorSolped('Solo el solicitante o un liberador anula la solicitud.')
    sp.estado, sp.comentario = 'ANULADA', motivo.strip()[:300]
    sp.save()
    from produccion.models import PropuestaMRP
    PropuestaMRP.objects.filter(solicitud=sp).update(solicitud=None)  # el MRP puede volver a proponerla
    return sp


@transaction.atomic
def cerrar_lineas(sp, ids, usuario):
    """Compras da por terminadas líneas que no se pedirán (o lo que falta de ellas)."""
    if sp.estado not in SolicitudPedido.ABIERTAS:
        raise ErrorSolped('Solo se cierran líneas de solicitudes liberadas.')
    n = sp.lineas.filter(pk__in=ids).update(cerrada=True)
    if not n:
        raise ErrorSolped('Elija las líneas a cerrar.')
    actualizar_estados([sp])
    return n


def actualizar_estados(solicitudes):
    """Liberada / pedida en parte / pedida / cerrada según lo pedido en órdenes de compra."""
    vistas = set()
    for sp in solicitudes:
        if sp.pk in vistas or sp.estado not in ('LIBERADA', 'PARCIAL', 'PEDIDA', 'CERRADA'):
            continue
        vistas.add(sp.pk)
        lineas = list(sp.lineas.prefetch_related('items_oc__documento', 'items_oc__producto'))
        pedidas = [l for l in lineas if l.pedido > 0]
        if all(l.pendiente <= 0 for l in lineas):
            estado = 'PEDIDA' if pedidas else 'CERRADA'
        else:
            estado = 'PARCIAL' if pedidas else 'LIBERADA'
        if estado != sp.estado:
            SolicitudPedido.objects.filter(pk=sp.pk).update(estado=estado)
            sp.estado = estado


# ---------------------------------------------------------------- conversión (bandeja de Compras)
def lineas_abiertas(qs=None):
    """Líneas de solicitudes liberadas con cantidad por pedir (y que no están en una licitación abierta)."""
    qs = qs if qs is not None else LineaSolicitud.objects.all()
    qs = qs.filter(solicitud__estado__in=SolicitudPedido.ABIERTAS, cerrada=False).select_related(
        'solicitud__centro_costo', 'producto__proveedor', 'proveedor_sugerido').prefetch_related(
        'items_oc__documento', 'items_oc__producto', 'lineas_licitacion__licitacion')
    return [l for l in qs if l.pendiente > 0 and not l.en_licitacion]


def _lineas_para_convertir(ids):
    lineas = lineas_abiertas(LineaSolicitud.objects.filter(pk__in=ids))
    if not lineas:
        raise ErrorSolped('Elija líneas liberadas con cantidad por pedir.')
    return lineas


@transaction.atomic
def a_orden_compra(ids, usuario, proveedor=None, contrato=None):
    """Líneas -> órdenes de compra pendientes (pasan por su aprobación), una por proveedor y centro de costo. El
    proveedor: el indicado, el sugerido de la línea o el habitual del producto. Contra contrato marco: sus precios."""
    lineas = _lineas_para_convertir(ids)
    if contrato is not None:
        proveedor = contrato.proveedor
        pactados = {li.producto_id: li for li in contrato.lineas.all()}
        fuera = [l.descripcion for l in lineas if l.producto_id not in pactados]
        if fuera:
            raise ErrorSolped(f'No están en el contrato {contrato.numero}: {", ".join(fuera)}.')
    grupos = defaultdict(list)
    sin_proveedor = []
    for l in lineas:
        prov = proveedor or l.proveedor_sugerido or (l.producto.proveedor if l.producto_id else None)
        if prov is None:
            sin_proveedor.append(l.descripcion)
            continue
        grupos[(prov, l.solicitud.centro_costo)].append(l)
    if sin_proveedor:
        raise ErrorSolped('Sin proveedor sugerido ni habitual: ' + ', '.join(sin_proveedor) +
                          '. Elija el proveedor de la orden.')
    ordenes = []
    for (prov, centro), filas in grupos.items():
        numeros = sorted({l.solicitud.numero for l in filas})
        fechas = [l.solicitud.fecha_requerida for l in filas if l.solicitud.fecha_requerida]
        oc = OrdenCompra.objects.create(
            numero=_numero('OC', 'OC01'), tercero=prov, fecha=timezone.localdate(), centro_costo=centro,
            fecha_entrega=min(fechas) if fechas else None, contrato=contrato,
            moneda=contrato.moneda if contrato else 'PEN',
            condicion_pago=(contrato.condicion_pago if contrato else '')[:100],
            glosa=f'Solicitudes de pedido {", ".join(numeros)}'[:500])
        for l in filas:
            p = l.producto
            cantidad = p.a_compra(l.pendiente) if p else l.pendiente
            precio = (pactados[p.pk].precio_unitario if contrato else
                      (l.precio * (p.factor or 1) if p else l.precio))
            OrdenCompraItem.objects.create(documento=oc, producto=p, descripcion=l.descripcion, cantidad=cantidad,
                                           precio_unitario=precio, linea_solped=l)
        oc.calcular_totales()
        oc.save()
        ordenes.append(oc)
    actualizar_estados({l.solicitud for l in lineas})
    return ordenes


@transaction.atomic
def a_licitacion(ids, usuario, descripcion, fecha_limite=None):
    """Líneas -> licitación (cuadro comparativo); al adjudicarla, sus órdenes quedan vinculadas a las líneas."""
    from .abastecimiento import ErrorAbastecimiento, crear_licitacion
    from .models import Licitacion
    lineas = _lineas_para_convertir(ids)
    centros = {l.solicitud.centro_costo_id for l in lineas}
    if len(centros) > 1:
        raise ErrorSolped('Una licitación es de un solo centro de costo: elija líneas del mismo centro.')
    if len((descripcion or '').strip()) < 3:
        raise ErrorSolped('Indique qué se licita.')
    lic = Licitacion(descripcion=descripcion.strip()[:150], centro_costo=lineas[0].solicitud.centro_costo,
                     fecha_limite=fecha_limite,
                     condiciones='Solicitudes: ' + ', '.join(sorted({l.solicitud.numero for l in lineas})))
    try:
        return crear_licitacion(lic, [(l.producto, l.descripcion, l.producto.a_compra(l.pendiente) if l.producto_id
                                       else l.pendiente, l) for l in lineas], usuario)
    except ErrorAbastecimiento as exc:
        raise ErrorSolped(str(exc)) from exc


def exige_conformidad(oc):
    """La OC trae servicios de una solicitud de pedido: su factura exige la conformidad (hoja de entrada de
    servicio)."""
    return any(i.linea_solped.es_servicio for i in oc.items.select_related('linea_solped__producto')
               if i.linea_solped_id)


# ---------------------------------------------------------------- presupuesto (compromiso)
def _cuenta_de(linea):
    if linea.cuenta_contable_id:
        return linea.cuenta_contable
    return linea.producto.cuenta_compra if linea.producto_id and linea.producto.cuenta_compra_id else None


def control_presupuesto(sp):
    """Mensajes por cuenta y centro de costo cuya ejecución del año (real + comprometido + esta solicitud) supera el
    presupuesto anual. Comprometido: otras solicitudes por liberar o liberadas sin pedir y órdenes de compra vigentes
    sin facturar del mismo centro y cuenta."""
    from contabilidad.models import Presupuesto
    from contabilidad.presupuesto import real_por_mes
    anio = (sp.fecha or timezone.localdate()).year
    pres = Presupuesto.del_anio(anio)
    if pres is None:
        return []
    esta = defaultdict(lambda: D0)
    for l in sp.lineas.select_related('producto__cuenta_compra', 'cuenta_contable'):
        cuenta = _cuenta_de(l)
        if cuenta is not None:
            esta[cuenta] += l.valor
    if not esta:
        return []
    lineas_pres = {(pl.cuenta_id, pl.centro_costo_id): pl for pl in pres.lineas.filter(cuenta__in=list(esta))}
    reales = real_por_mes(anio, {c.pk for c in esta})
    avisos = []
    for cuenta, monto in esta.items():
        pl = lineas_pres.get((cuenta.pk, sp.centro_costo_id)) or lineas_pres.get((cuenta.pk, None))
        if pl is None:
            continue
        centro = pl.centro_costo_id
        real = sum((sum(v, D0) for (c, cc), v in reales.items() if c == cuenta.pk and (centro is None or cc == centro)),
                   D0)
        comprometido = D0
        otras = LineaSolicitud.objects.filter(
            solicitud__estado__in=('ENVIADA', 'LIBERADA', 'PARCIAL'), solicitud__fecha__year=anio).exclude(
            solicitud=sp).select_related('producto__cuenta_compra', 'cuenta_contable', 'solicitud').prefetch_related(
            'items_oc__documento', 'items_oc__producto')
        if centro:
            otras = otras.filter(solicitud__centro_costo_id=centro)
        for o in otras:
            if _cuenta_de(o) == cuenta:
                comprometido += r2(o.pendiente * o.precio)
        ocs = OrdenCompraItem.objects.filter(documento__estado__in=('PENDIENTE', 'APROBADO'),
                                             documento__fecha__year=anio).select_related(
            'producto__cuenta_compra', 'linea_solped__cuenta_contable', 'documento')
        if centro:
            ocs = ocs.filter(documento__centro_costo_id=centro)
        for i in ocs:
            c = (i.linea_solped.cuenta_contable if i.linea_solped_id and i.linea_solped.cuenta_contable_id
                 else i.producto.cuenta_compra if i.producto_id else None)
            if c == cuenta and not i.documento.compras.exclude(estado='ANULADO').exists():
                comprometido += i.subtotal
        disponible = pl.total - real - comprometido
        if monto > disponible:
            avisos.append(f'{cuenta.codigo} {cuenta.nombre}{f" · {pl.centro_costo.codigo}" if centro else ""}: '
                          f'presupuesto del año S/ {pl.total:,.2f}, ejecutado S/ {real:,.2f}, comprometido S/ '
                          f'{comprometido:,.2f}; esta solicitud S/ {monto:,.2f} supera lo disponible '
                          f'(S/ {max(disponible, D0):,.2f}).')
    return avisos


# ---------------------------------------------------------------- trazabilidad
def trazabilidad(sp):
    """Por línea: órdenes de compra, recepciones en el almacén, facturas y su pago."""
    from inventario.models import Operacion
    filas = []
    for l in sp.lineas.select_related('producto', 'requerimiento_item__requerimiento').prefetch_related(
            'items_oc__documento__tercero', 'lineas_licitacion__licitacion'):
        ocs = []
        for i in l.items_oc.all():
            oc = i.documento
            recepciones = list(Operacion.objects.filter(orden_compra=oc, estado='CONFIRMADO').values_list(
                'pk', 'numero', 'fecha'))
            facturas = [{'c': c, 'saldo': c.saldo} for c in oc.compras.exclude(estado='ANULADO').exclude(
                tipo_comprobante__in=['07', '08'])]
            ocs.append({'oc': oc, 'item': i, 'recepciones': recepciones, 'facturas': facturas,
                        'conformidades': list(oc.conformidades.all())})
        filas.append({'l': l, 'ocs': ocs, 'licitaciones': [x.licitacion for x in l.lineas_licitacion.all()]})
    return filas


# ---------------------------------------------------------------- reportes
TRAMOS = [(7, '0 a 7 días'), (15, '8 a 15 días'), (30, '16 a 30 días'), (None, 'Más de 30 días')]


def reporte(desde, hasta):
    qs = list(SolicitudPedido.objects.filter(fecha__range=(desde, hasta)).exclude(estado='BORRADOR')
              .select_related('centro_costo').prefetch_related('lineas__producto', 'lineas__items_oc__documento'))
    hoy = timezone.localdate()
    por_estado = defaultdict(lambda: {'n': 0, 'monto': D0})
    por_centro = defaultdict(lambda: {'n': 0, 'monto': D0, 'liberadas': 0, 'pedidas': 0})
    antiguedad = {t: {'n': 0, 'monto': D0, 'filas': []} for _, t in TRAMOS}
    t_liberacion, t_oc = [], []
    for sp in qs:
        monto = sp.monto
        por_estado[sp.estado]['n'] += 1
        por_estado[sp.estado]['monto'] += monto
        c = por_centro[sp.centro_costo]
        c['n'] += 1
        c['monto'] += monto
        c['liberadas'] += sp.estado in ('LIBERADA', 'PARCIAL', 'PEDIDA', 'CERRADA')
        c['pedidas'] += sp.estado in ('PARCIAL', 'PEDIDA')
        if sp.enviada_en and sp.liberada_en:
            t_liberacion.append((sp.liberada_en - sp.enviada_en).total_seconds() / 3600)
        fechas_oc = [i.documento.fecha for l in sp.lineas.all() for i in l.items_oc.all()
                     if i.documento.estado != 'ANULADO']
        if fechas_oc and sp.liberada_en:
            t_oc.append((min(fechas_oc) - timezone.localtime(sp.liberada_en).date()).days)
        if sp.estado in ('ENVIADA', 'OBSERVADA', 'LIBERADA', 'PARCIAL'):
            dias = (hoy - sp.fecha).days
            tramo = next(t for limite, t in TRAMOS if limite is None or dias <= limite)
            antiguedad[tramo]['n'] += 1
            antiguedad[tramo]['monto'] += monto
            antiguedad[tramo]['filas'].append((sp, dias))
    nombres = dict(SolicitudPedido.ESTADOS)
    return {
        'por_estado': [(nombres[e], v['n'], v['monto']) for e, v in sorted(por_estado.items(),
                                                                          key=lambda x: list(nombres).index(x[0]))],
        'por_centro': sorted(((c, v) for c, v in por_centro.items()), key=lambda x: -x[1]['monto']),
        'antiguedad': antiguedad,
        'horas_liberacion': round(sum(t_liberacion) / len(t_liberacion), 1) if t_liberacion else None,
        'dias_a_oc': round(sum(t_oc) / len(t_oc), 1) if t_oc else None,
        'total': len(qs)}


# ---------------------------------------------------------------- avisos (correo y WhatsApp)
_ASUNTOS = {'enviada': 'Solicitud de pedido por liberar', 'liberada': 'Solicitud de pedido liberada',
            'observada': 'Solicitud de pedido observada', 'rechazada': 'Solicitud de pedido rechazada'}


def destinatarios(sp, evento):
    if evento == 'enviada':
        n = sp.nivel_pendiente
        if n is None:
            return []
        if n['usuario']:  # el titular y sus suplentes vigentes
            from core.aprobaciones import aprobadores
            return [u for u in aprobadores(n) if u.is_active]
        return [u for u in get_user_model().objects.filter(is_active=True)
                if u.pk != sp.solicitante_id and puede(u, 'requerimientos.liberar_solped')]
    return [sp.solicitante]


def notificar(sp, evento):
    """Correo (y WhatsApp si la empresa lo tiene activo y el usuario su celular) al siguiente liberador o al
    solicitante. Si no está configurado no interrumpe el flujo."""
    usuarios = destinatarios(sp, evento)
    if not usuarios:
        return False
    pk = sp.pk
    transaction.on_commit(lambda: _avisar(pk, evento, [u.pk for u in usuarios]))
    return True


def _avisar(pk, evento, usuarios_ids):
    from core.correo import ErrorCorreo, enviar as enviar_correo
    sp = SolicitudPedido.objects.select_related('solicitante', 'centro_costo').get(pk=pk)
    usuarios = list(get_user_model().objects.filter(pk__in=usuarios_ids))
    correos = [u.email for u in usuarios if u.email]
    if correos:
        try:
            enviar_correo(correos, f'{_ASUNTOS[evento]}: {sp.numero}', 'compras/correo_solped.html',
                          {'sp': sp, 'evento': evento})
        except ErrorCorreo as exc:
            log.info('Aviso de la solicitud %s no enviado: %s', sp.numero, exc)
    try:
        from finanzas.models_rendicion import ConfigRendicion
        from finanzas.views_rendicion_movil import _responder
        cfg = ConfigRendicion.actual()
        if cfg.whatsapp_activo and cfg.whatsapp_token:
            texto = (f'Ceiba ERP · {_ASUNTOS[evento]}: {sp.numero} ({sp.centro_costo.codigo}) S/ {sp.monto:,.2f}. '
                     f'{sp.motivo}')
            for p in PerfilUsuario.objects.filter(usuario__in=usuarios).exclude(celular=''):
                _responder(cfg, p.celular, texto)
    except Exception as exc:  # el aviso nunca interrumpe el flujo
        log.info('WhatsApp de la solicitud %s no enviado: %s', sp.numero, exc)
