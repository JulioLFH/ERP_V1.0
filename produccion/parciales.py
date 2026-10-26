"""Órdenes de producción con declaraciones parciales, cierre técnico y liquidación (MAN-18 a MAN-21), como en SAP:
declarar (CO11N + 101/261) → cierre técnico (TECO) → liquidación (KO88) → terminada (CLSD).

- Declarar, cada turno: el lote del producto entra al almacén al costo estándar de la orden (Ingreso de producción),
  se descuenta de la receta solo lo que falte respecto de lo ya entregado por el almacén (Consumo de la orden) y se
  registran sus horas. La orden queda «Parcial» con saldo = programado − declarado (y la planificación ve el saldo).
- Cierre técnico: no admite más declaraciones; con faltante pide el motivo (merma normal, anormal o saldo cancelado);
  el material entregado y no usado se devuelve al almacén (Devolución de orden de producción) o queda como merma.
- Liquidación: costo real (materiales entregados y descontados + horas + costos tardíos) − estándar declarado. La
  variación se reparte a los lotes que siguen en stock (ajuste de valor del kardex) y al costo de ventas lo ya vendido;
  con merma anormal va a gasto (NIC 2 párr. 16). La orden queda «Terminada».
- Costos tardíos (MAN-21): en una orden cerrada técnicamente entran a su liquidación; en una terminada van a otra
  orden abierta del mismo producto o quedan como gasto del periodo, sin anular la orden.
- Fin de mes: lo cargado a las órdenes abiertas y no declarado queda como productos en proceso (23 contra 71), con su
  reversión el primer día del mes siguiente (contabilidad.centralizar.asiento_proceso)."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import r2

from .models import ConsumoOrden, CostoTardio, DeclaracionOrden, HoraHombre, OrdenProduccion
from .servicios import ErrorProduccion, _momento_termino, registrar_variaciones

D0 = Decimal('0')
D2 = Decimal('0.01')


def _contable(fecha_contable, fecha):
    """Fecha contable de la declaración (o la del término): no futura, con el kardex y el periodo abiertos."""
    from contabilidad.models import PeriodoContable
    from inventario.cierre import error_cierre
    if isinstance(fecha_contable, str):
        try:
            fecha_contable = date.fromisoformat(fecha_contable.strip()) if fecha_contable.strip() else None
        except ValueError:
            raise ErrorProduccion('Fecha contable no válida.')
    contable = fecha_contable or fecha
    if contable > timezone.localdate():
        raise ErrorProduccion('La fecha contable no puede ser futura.')
    if error_cierre(contable):
        raise ErrorProduccion(error_cierre(contable))
    if PeriodoContable.esta_cerrado(contable.strftime('%Y%m')):
        raise ErrorProduccion(f'El periodo contable {contable:%m/%Y} está cerrado.')
    return contable


def _referencia(orden, numero):
    return f'{orden.numero} D{numero}'[:60]


def _confirmar(op, usuario):
    from inventario import servicios as inv
    try:
        inv.confirmar(op, usuario)
    except inv.ErrorOperacion as exc:
        raise ErrorProduccion(str(exc)) from exc
    return op


def por_descontar(orden, declarado):
    """{consumo: cantidad} que sale del almacén para llegar a lo que pide la receta para lo declarado acumulado
    (automático por receta). Lo ya entregado por salida a la orden cuenta para la receta (MAN-19)."""
    from .consumo import teorico
    salida = {}
    for c in orden.consumos.select_related('producto', 'almacen'):
        if c.adicional or c.modo == 'SALIDA':
            continue
        falta = r2(teorico(c, declarado) - c.despachado - c.backflush)
        if falta > 0:
            salida[c] = falta
    return salida


def _dec(valor, nombre='valor'):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except ArithmeticError:
        raise ErrorProduccion(f'{nombre}: número no válido.')


def _costear_horas(orden, declaracion, cantidad, horas, personal, fecha, cargos=None, usuario=None,
                   autorizar=False):
    """(mano de obra, máquina, CIF, {hora_id: horas}) del turno. Sin datos de una operación: las horas de la hoja de
    ruta en proporción a lo declarado, con su cuadrilla y los drivers del puesto según su consumo por hora.

    MAN-22: `cargos` = {hora_id: [{'tipo': 'MAQUINA' | 'PREPARACION' | 'DRIVER', 'driver': id, 'cantidad',
    'tarifa'}]} y `personal` = {hora_id: [{'rol', 'trabajador', 'horas', 'tarifa'}]}: varias personas, roles y drivers
    por operación. Cambiar una tarifa pide el permiso «editar tarifas»; con la política de consumo por tolerancia, las
    horas por encima de lo propuesto más la tolerancia las autoriza quien tiene «Aprobar consumos»."""
    from core.permisos import puede
    from planillas.models import Trabajador

    from .consumo import politica
    from .models import CargoDeclaracion, DriverPuesto
    horas, personal, cargos = horas or {}, personal or {}, cargos or {}
    trabajadores = {t.pk: t for t in Trabajador.objects.filter(
        pk__in={p.get('trabajador') for filas in personal.values() for p in filas if p.get('trabajador')})}
    puede_tarifa = usuario is not None and (usuario.is_superuser or puede(usuario, 'manufactura.editar_tarifas'))
    modo, tol = politica(orden)
    margen = 1 + max(tol, D0) / 100

    def tarifa_de(fila, propuesta, que):
        escrita = _dec(fila.get('tarifa'), f'Tarifa de {que}')
        if escrita is None or escrita == propuesta:
            return propuesta, False
        if escrita < 0:
            raise ErrorProduccion(f'La tarifa de {que} no puede ser negativa.')
        if not puede_tarifa:
            raise ErrorProduccion(f'Cambiar la tarifa de {que} requiere el permiso «Cambiar la tarifa al declarar».')
        return escrita, True

    mo = maq = cif = D0
    registro = {}
    for h in orden.horas.select_related('centro').prefetch_related('centro__drivers'):
        c = h.centro
        propuesta = (h.horas_plan * cantidad / orden.cantidad).quantize(D2) if orden.cantidad else D0
        filas_cargo = cargos.get(h.pk)
        prep = D0
        if filas_cargo is not None:
            hm = D0
            for f in filas_cargo:
                q = _dec(f.get('cantidad'), 'Cantidad') or D0
                if q < 0:
                    raise ErrorProduccion('Las cantidades de los drivers no pueden ser negativas.')
                if not q:
                    continue
                if f.get('tipo') in ('MAQUINA', 'PREPARACION'):
                    que = 'la hora máquina' if f['tipo'] == 'MAQUINA' else 'la preparación'
                    tarifa, editada = tarifa_de(f, c.costo_hora_maquina, que)
                    if f['tipo'] == 'MAQUINA':
                        hm += q
                    else:
                        prep += q
                    CargoDeclaracion.objects.create(declaracion=declaracion, hora=h, tipo=f['tipo'], cantidad=q,
                                                    unidad='h', tarifa=tarifa, importe=r2(q * tarifa),
                                                    tarifa_editada=editada)
                    maq += r2(q * tarifa)
                elif f.get('tipo') == 'DRIVER':
                    d = DriverPuesto.objects.filter(pk=f.get('driver') or 0, centro=c, activo=True).first()
                    if d is None:
                        raise ErrorProduccion(f'{c}: el driver elegido no es del puesto.')
                    tarifa, editada = tarifa_de(f, d.tarifa, d.nombre.lower())
                    CargoDeclaracion.objects.create(declaracion=declaracion, hora=h, tipo='DRIVER', driver=d,
                                                    cantidad=q, unidad=d.unidad, tarifa=tarifa,
                                                    importe=r2(q * tarifa), tarifa_editada=editada)
                    cif += r2(q * tarifa)
                else:
                    raise ErrorProduccion('Tipo de driver no válido.')
        else:
            if h.pk in horas and horas[h.pk] not in (None, ''):
                hm = Decimal(str(horas[h.pk]))
            else:
                hm = propuesta
            if hm < 0:
                raise ErrorProduccion('Las horas no pueden ser negativas.')
            if hm:
                CargoDeclaracion.objects.create(declaracion=declaracion, hora=h, tipo='MAQUINA', cantidad=hm,
                                                unidad='h', tarifa=c.costo_hora_maquina,
                                                importe=r2(hm * c.costo_hora_maquina))
            maq += r2(hm * c.costo_hora_maquina)
            for d in c.drivers_activos():  # drivers del puesto según su consumo estándar por hora máquina
                q = (hm * d.por_hora).quantize(Decimal('0.0001'))
                if q:
                    CargoDeclaracion.objects.create(declaracion=declaracion, hora=h, tipo='DRIVER', driver=d,
                                                    cantidad=q, unidad=d.unidad, tarifa=d.tarifa,
                                                    importe=r2(q * d.tarifa))
                    cif += r2(q * d.tarifa)
        maquina_total = hm + prep
        if modo in ('TOLERANCIA', 'ESTRICTO') and not autorizar and maquina_total > (propuesta * margen) + D2:
            raise ErrorProduccion(f'{c}: {maquina_total:,.2f} h máquina superan lo propuesto ({propuesta:,.2f} h) '
                                  f'más la tolerancia: lo autoriza quien tiene «Aprobar consumos».')
        cif += r2(maquina_total * c.costo_hora_cif)  # CIF por hora máquina del puesto
        filas = personal.get(h.pk)
        if filas is None:
            filas = ([{'rol': 'MAQUINISTA', 'horas': maquina_total}] * h.maquinistas +
                     [{'rol': 'AYUDANTE', 'horas': maquina_total}] * h.ayudantes)
        horas_hombre = D0
        for p in filas:
            horas_h = Decimal(str(p.get('horas') or 0))
            if horas_h < 0:
                raise ErrorProduccion('Las horas hombre no pueden ser negativas.')
            if not horas_h:
                continue
            horas_hombre += horas_h
            t = trabajadores.get(p.get('trabajador'))
            if t is not None and not t.activo_en(fecha, fecha):
                raise ErrorProduccion(f'{t.nombre_completo} no estaba en planilla el {fecha:%d/%m/%Y}.')
            tarifa, _ = tarifa_de(p, t.costo_hora(fecha) if t else c.costo_hora_mo, 'la hora hombre')
            linea = HoraHombre.objects.create(
                hora=h, declaracion=declaracion, trabajador=t, horas=horas_h, costo_hora=tarifa,
                rol=p.get('rol') if p.get('rol') in dict(HoraHombre.ROLES) else 'MAQUINISTA',
                costo=r2(horas_h * tarifa))
            mo += linea.costo
        cuadrilla = (h.maquinistas + h.ayudantes) or 1
        if (modo in ('TOLERANCIA', 'ESTRICTO') and not autorizar and personal.get(h.pk) is not None
                and horas_hombre > propuesta * cuadrilla * margen + D2):
            raise ErrorProduccion(f'{c}: {horas_hombre:,.2f} horas hombre superan lo propuesto '
                                  f'({propuesta * cuadrilla:,.2f} h) más la tolerancia: lo autoriza quien tiene '
                                  f'«Aprobar consumos».')
        registro[str(h.pk)] = str(maquina_total)
    return mo, maq, cif, registro


@transaction.atomic
def declarar(orden, usuario, cantidad, fecha=None, fecha_contable=None, lote='', fabricacion=None, vencimiento=None,
             horas=None, personal=None, nota='', lotes_insumos=None, autorizar=False, cargos=None):
    """Declara la producción de un turno (ver el docstring del módulo). Devuelve la DeclaracionOrden."""
    from inventario.estados import vencimiento_propuesto
    from inventario.models import Operacion, TipoOperacion

    from .consumo import politica
    orden = OrdenProduccion.objects.select_for_update().get(pk=orden.pk)
    if orden.estado not in OrdenProduccion.ABIERTAS:
        raise ErrorProduccion('Solo se declara producción en una orden confirmada, en proceso o parcial.')
    if orden.salidas.exists() or orden.maquilador_id:
        raise ErrorProduccion('Las órdenes con subproductos, coproductos o maquila se terminan en un paso '
                              '(«Terminar»).')
    try:
        cantidad = Decimal(str(cantidad or 0)).quantize(D2)
    except ArithmeticError:
        raise ErrorProduccion('Cantidad no válida.')
    if cantidad <= 0:
        raise ErrorProduccion('Indique la cantidad producida en el turno.')
    declarado = orden.cantidad_producida + cantidad
    _, tolerancia = politica(orden)
    tope = orden.cantidad * (1 + max(tolerancia, D0) / 100)
    if declarado > tope and not autorizar:
        raise ErrorProduccion(f'Se producirían {declarado:,.2f} de {orden.cantidad:,.2f} programados (tolerancia '
                              f'{tolerancia:g} %): la sobreproducción la autoriza quien tiene «Aprobar consumos».')
    if orden.consumos.filter(aprobacion='POR_APROBAR').exists():
        raise ErrorProduccion('Hay material agregado o consumos por aprobar: resuélvalos antes de declarar.')
    fecha, momento = _momento_termino(fecha)
    contable = _contable(fecha_contable, fecha)
    documento = fecha if contable != fecha else None
    numero = (orden.declaraciones.aggregate(m=Max('numero'))['m'] or 0) + 1
    ref = _referencia(orden, numero)
    decl = DeclaracionOrden.objects.create(orden=orden, numero=numero, fecha=contable, momento=momento,
                                           cantidad=cantidad, nota=(nota or '').strip()[:250],
                                           usuario=usuario if usuario and usuario.is_authenticated else None)
    # 1) materiales: solo lo que falta para la receta de lo declarado (puede ser nada)
    lotes_insumos = lotes_insumos or {}
    por_almacen = defaultdict(list)
    for c, q in por_descontar(orden, declarado).items():
        por_almacen[c.almacen_id or orden.almacen_insumos_id].append((c, q))
    tipo_consumo = TipoOperacion.objects.get(codigo='CONS_OP')
    materiales, consumos, primera = D0, {}, None
    for almacen_id, filas in por_almacen.items():
        op = Operacion.objects.create(
            tipo=tipo_consumo, fecha=contable, fecha_documento=documento, almacen_origen_id=almacen_id,
            orden_imputada=orden, centro_costo=orden.centro_costo, referencia=ref, creado_por=decl.usuario,
            glosa=f'Consumo de la declaración {numero} de {orden.numero}: {orden.producto.nombre}'[:500])
        for c, q in filas:
            op.items.create(producto=c.producto, cantidad=q,
                            lote=(lotes_insumos.get(c.pk) or '').strip()[:60] if c.producto.control else '')
        _confirmar(op, usuario)
        costos = {i.producto_id: i.costo_unitario for i in op.items.all()}
        for c, q in filas:
            valor = r2(q * costos.get(c.producto_id, D0))
            ConsumoOrden.objects.filter(pk=c.pk).update(backflush=c.backflush + q,
                                                        valor_backflush=c.valor_backflush + valor)
            materiales += valor
            consumos[str(c.pk)] = str(q)
        primera = primera or op
    # 2) horas del turno
    mo, maq, cif, registro = _costear_horas(orden, decl, cantidad, horas, personal, fecha, cargos=cargos,
                                            usuario=usuario, autorizar=autorizar)
    # 3) el lote del producto entra al costo estándar fijado al confirmar (sin estándar: lo cargado en el turno)
    std = orden.costo_estandar_unit or ((materiales + mo + maq + cif) / cantidad).quantize(Decimal('0.0001'))
    p = orden.producto
    con_lote = p.control == 'LOTE'
    lote_pt = ((lote or '').strip().upper()[:60] or f'{orden.numero}-{numero}') if con_lote else ''
    fabricado = fabricacion or fecha
    vence = vencimiento or (vencimiento_propuesto(p, fabricado) if con_lote else None)
    ingreso = Operacion.objects.create(
        tipo=TipoOperacion.objects.get(codigo='ENT_OP'), fecha=contable, fecha_documento=documento,
        almacen_destino=orden.almacen_destino, orden_imputada=orden, centro_costo=orden.centro_costo, referencia=ref,
        creado_por=decl.usuario, glosa=f'Producción declarada {numero} de {orden.numero}: {p.nombre}'[:500])
    ingreso.items.create(producto=p, cantidad=cantidad, costo_unitario=std, rol='PRODUCTO', lote=lote_pt,
                         fabricacion=fabricado if con_lote else None, vencimiento=vence if con_lote else None)
    _confirmar(ingreso, usuario)
    decl.lote, decl.fabricacion, decl.vencimiento = lote_pt, fabricado if con_lote else None, vence
    decl.costo_unitario, decl.materiales = std, materiales
    decl.costo_mo, decl.costo_maquina, decl.costo_cif, decl.horas = mo, maq, cif, registro
    decl.ingreso, decl.consumo, decl.consumos = ingreso, primera, consumos
    decl.save()
    orden.cantidad_producida = declarado
    orden.estado = 'PARCIAL'
    orden.fecha_inicio = orden.fecha_inicio or contable
    orden.lote_producido = lote_pt
    orden.save(update_fields=['cantidad_producida', 'estado', 'fecha_inicio', 'lote_producido'])
    return decl


@transaction.atomic
def anular_declaracion(decl, usuario, motivo):
    """Revierte el lote y sus consumos si el lote no se vendió ni se consumió (la orden aún no se liquida)."""
    from inventario import servicios as inv
    from inventario.models import Operacion
    orden = OrdenProduccion.objects.select_for_update().get(pk=decl.orden_id)
    if decl.anulada:
        raise ErrorProduccion('La declaración ya está anulada.')
    if orden.estado not in ('PARCIAL', 'CERRADA_TEC'):
        raise ErrorProduccion('Solo se anula una declaración de una orden parcial o cerrada técnicamente (aún no '
                              'liquidada).')
    if len((motivo or '').strip()) < 10:
        raise ErrorProduccion('Indique el motivo (mínimo 10 caracteres).')
    texto = f'Anulación de la declaración {decl.numero} de {orden.numero}: {motivo.strip()}'
    try:
        if decl.ingreso_id and decl.ingreso.estado == 'CONFIRMADO':
            inv.anular(decl.ingreso, usuario, texto)
        consumos = {int(k): Decimal(v) for k, v in (decl.consumos or {}).items()}
        for op in Operacion.objects.filter(tipo__codigo='CONS_OP', orden_imputada=orden, estado='CONFIRMADO',
                                           referencia=_referencia(orden, decl.numero)):
            costos = {i.producto_id: i.costo_unitario for i in op.items.all()}
            inv.anular(op, usuario, texto)
            for c in orden.consumos.filter(pk__in=consumos, producto_id__in=costos):
                q = consumos[c.pk]
                ConsumoOrden.objects.filter(pk=c.pk).update(
                    backflush=max(c.backflush - q, D0),
                    valor_backflush=max(c.valor_backflush - r2(q * costos[c.producto_id]), D0))
    except inv.ErrorOperacion as exc:
        raise ErrorProduccion(f'No se pudo anular: {exc}') from exc
    decl.personal.all().delete()
    decl.anulada = True
    decl.nota = f'{decl.nota} · Anulada: {motivo.strip()}'.strip(' ·')[:250]
    decl.save(update_fields=['anulada', 'nota'])
    orden.cantidad_producida = max(orden.cantidad_producida - decl.cantidad, D0)
    if not orden.declaraciones.filter(anulada=False).exists():
        orden.estado = 'EN_PROCESO'
    orden.save(update_fields=['cantidad_producida', 'estado'])
    return decl


@transaction.atomic
def cierre_tecnico(orden, usuario, motivo='', detalle='', devolver=True, fecha=None):
    """Bloquea nuevas declaraciones. Con faltante exige el motivo. Lo entregado y no usado (más de lo que pide la
    receta para lo declarado) vuelve al almacén con una devolución de orden de producción o queda como merma."""
    from inventario.models import Operacion, TipoOperacion

    from .consumo import teorico
    orden = OrdenProduccion.objects.select_for_update().get(pk=orden.pk)
    if orden.estado != 'PARCIAL':
        raise ErrorProduccion('El cierre técnico es de una orden con producción declarada (parcial).')
    faltante = orden.cantidad - orden.cantidad_producida
    if faltante > 0:
        if motivo not in dict(OrdenProduccion.MOTIVOS_FALTANTE) or not motivo:
            raise ErrorProduccion(f'Faltan {faltante:,.2f} de lo programado: indique el motivo del faltante.')
        if len((detalle or '').strip()) < 5:
            raise ErrorProduccion('Describa el motivo del faltante.')
    contable = _contable(fecha, timezone.localdate()) if fecha else timezone.localdate()
    # material agregado aprobado que aún no salió del almacén: se descuenta ahora (es parte del costo de la orden)
    tipo_consumo = TipoOperacion.objects.get(codigo='CONS_OP')
    pendientes = [(c, r2(c.cantidad_real - c.entregado)) for c in orden.consumos.select_related('producto')
                  if c.adicional and c.aprobacion == 'APROBADO' and r2(c.cantidad_real - c.entregado) > 0]
    if pendientes:
        op = Operacion.objects.create(
            tipo=tipo_consumo, fecha=contable, almacen_origen=orden.almacen_insumos, orden_imputada=orden,
            centro_costo=orden.centro_costo, referencia=f'{orden.numero} cierre'[:60], creado_por=usuario,
            glosa=f'Material agregado consumido al cierre técnico de {orden.numero}'[:500])
        for c, q in pendientes:
            op.items.create(producto=c.producto, cantidad=q)
        _confirmar(op, usuario)
        costos = {i.producto_id: i.costo_unitario for i in op.items.all()}
        for c, q in pendientes:
            ConsumoOrden.objects.filter(pk=c.pk).update(
                backflush=c.backflush + q, valor_backflush=c.valor_backflush + r2(q * costos.get(c.producto_id, D0)))
    sobrantes = []
    for c in orden.consumos.select_related('producto', 'almacen'):
        if c.adicional or c.modo == 'SALIDA' or c.despachado <= 0:
            continue
        sobra = r2(min(c.despachado, c.entregado - teorico(c, orden.cantidad_producida)))
        if sobra > 0:
            sobrantes.append((c, sobra))
    if sobrantes and devolver:
        op = Operacion.objects.create(
            tipo=TipoOperacion.objects.get(codigo='DEV_OP'), fecha=contable,
            almacen_destino=orden.almacen_insumos, orden_imputada=orden, centro_costo=orden.centro_costo,
            referencia=f'{orden.numero} cierre'[:60], creado_por=usuario,
            glosa=f'Devolución del material no usado al cierre técnico de {orden.numero}'[:500])
        for c, q in sobrantes:
            costo = (c.valor_despachado / c.despachado).quantize(Decimal('0.0001')) if c.despachado else None
            op.items.create(producto=c.producto, cantidad=q, costo_unitario=costo)
        _confirmar(op, usuario)  # resta lo devuelto de lo despachado a la orden (consumo.imputar)
    orden.refresh_from_db()
    orden.estado = 'CERRADA_TEC'
    orden.motivo_faltante = motivo if faltante > 0 else ''
    orden.detalle_cierre = (detalle or '').strip()[:250]
    orden.cerrada_tec_en = timezone.now()
    orden.cerrada_tec_por = usuario if usuario and usuario.is_authenticated else None
    orden.save(update_fields=['estado', 'motivo_faltante', 'detalle_cierre', 'cerrada_tec_en', 'cerrada_tec_por'])
    return orden


def _tardios(orden):
    """{MO, MAQUINA, OTRO} de los costos tardíos que entran a la liquidación de la orden."""
    from django.db.models import Q
    suma = defaultdict(lambda: D0)
    for t in CostoTardio.objects.filter(Q(orden=orden, destino='ORDEN') | Q(orden_destino=orden,
                                                                            destino='OTRA_ORDEN')):
        suma['MO' if t.tipo == 'MO' else ('MAQUINA' if t.tipo == 'MAQUINA' else 'OTRO')] += t.importe
    return suma


def costo_real(orden):
    """(materiales, mano de obra, máquina, CIF) cargados a la orden: lo entregado por el almacén (salidas a la orden y
    lo descontado al declarar), las horas de las declaraciones y los costos tardíos."""
    materiales = sum((c.valor_despachado + c.valor_backflush for c in orden.consumos.all()), D0)
    vigentes = orden.declaraciones.filter(anulada=False)
    mo = sum((d.costo_mo for d in vigentes), D0)
    maq = sum((d.costo_maquina for d in vigentes), D0)
    cif = sum((d.costo_cif for d in vigentes), D0)
    t = _tardios(orden)
    return materiales, mo + t['MO'], maq + t['MAQUINA'], cif + t['OTRO']


@transaction.atomic
def liquidar_orden(orden, usuario, fecha=None):
    """Liquidación final de la orden cerrada técnicamente: variación = costo real − estándar declarado, repartida a
    los lotes en stock (ajuste de valor del kardex) y al costo de ventas; con merma anormal, a gasto."""
    from contabilidad.automatico import marcar_pendiente

    from .liquidacion import _fecha_ajuste, _revalorizar
    orden = OrdenProduccion.objects.select_for_update().get(pk=orden.pk)
    if orden.estado != 'CERRADA_TEC':
        raise ErrorProduccion('Primero haga el cierre técnico de la orden.')
    contable = _contable(fecha, timezone.localdate()) if fecha else _contable(None, timezone.localdate())
    vigentes = list(orden.declaraciones.filter(anulada=False))
    declarado = sum((d.cantidad for d in vigentes), D0)
    if not declarado:
        raise ErrorProduccion('La orden no tiene producción declarada.')
    for c in orden.consumos.all():  # consumo real = lo que salió del almacén para la orden
        c.cantidad_real = c.despachado + c.backflush
        valor = c.valor_despachado + c.valor_backflush
        c.costo_unitario = (valor / c.cantidad_real).quantize(Decimal('0.0001')) if c.cantidad_real else D0
        c.save(update_fields=['cantidad_real', 'costo_unitario'])
    from .models import CargoDeclaracion
    for h in orden.horas.select_related('centro'):
        h.horas_real = sum((Decimal(d.horas.get(str(h.pk), '0')) for d in vigentes), D0)
        h.costo_mo = sum((p.costo for p in h.personal.filter(declaracion__anulada=False)), D0)
        cargos = list(CargoDeclaracion.objects.filter(hora=h, declaracion__anulada=False))
        if cargos:  # MAN-22: lo declarado por driver (tarifas del turno)
            h.costo_maquina = sum((x.importe for x in cargos if x.tipo != 'DRIVER'), D0)
            h.costo_cif = r2(h.horas_real * h.centro.costo_hora_cif) + sum(
                (x.importe for x in cargos if x.tipo == 'DRIVER'), D0)
        else:
            h.costo_maquina = r2(h.horas_real * h.centro.costo_hora_maquina)
            h.costo_cif = r2(h.horas_real * h.centro.costo_hora_cif)
        h.save(update_fields=['horas_real', 'costo_mo', 'costo_maquina', 'costo_cif'])
    materiales, mo, maq, cif = costo_real(orden)
    real = materiales + mo + maq + cif
    estandar = sum((d.valor_estandar for d in vigentes), D0)
    variacion = r2(real - estandar)
    a_inv = a_costo = a_gasto = D0
    producto = orden.producto
    if orden.motivo_faltante == 'MERMA_ANORMAL' and variacion > 0:
        a_gasto = variacion
    elif variacion:
        producto.refresh_from_db()
        en_stock = min(max(producto.stock, D0), declarado)
        a_inv = r2(variacion * en_stock / declarado)
        a_costo = variacion - a_inv
        ajuste = _fecha_ajuste(producto, contable)
        if a_inv and not _revalorizar(producto, a_inv, ajuste, f'Liquidación de {orden.numero}'):
            a_costo, a_inv = a_costo + a_inv, D0
    orden.costo_materiales, orden.costo_mano_obra = materiales, mo
    orden.costo_maquina, orden.costo_cif = maq, cif
    orden.merma_anormal, orden.costo_subproductos = a_gasto, D0
    orden.cantidad_producida = declarado
    orden.costo_unitario = ((real - a_gasto) / declarado).quantize(Decimal('0.0001'))
    orden.variacion_inventario, orden.variacion_costo_ventas, orden.variacion_gasto = a_inv, a_costo, a_gasto
    orden.fecha_fin = contable
    orden.terminado_en = max(d.momento for d in vigentes)
    orden.liquidada_en = timezone.now()
    orden.estado = 'TERMINADA'
    orden.save()
    registrar_variaciones(orden)
    marcar_pendiente(contable.strftime('%Y%m'))
    return orden


@transaction.atomic
def costo_tardio(orden, usuario, tipo, fecha, motivo, horas=None, importe=None, trabajador=None, centro=None,
                 orden_destino=None):
    """MAN-21: registra horas o costos olvidados. Cerrada técnicamente: van a su liquidación. Terminada: a otra orden
    abierta del mismo producto o, sin ella, gasto del periodo (no se capitaliza)."""
    if orden.estado not in ('CERRADA_TEC', 'TERMINADA', 'PARCIAL'):
        raise ErrorProduccion('Los costos tardíos se registran en una orden parcial, cerrada técnicamente o terminada.')
    if tipo not in dict(CostoTardio.TIPOS):
        raise ErrorProduccion('Elija el tipo de costo.')
    if len((motivo or '').strip()) < 10:
        raise ErrorProduccion('Indique el motivo (mínimo 10 caracteres).')
    if isinstance(fecha, str):
        try:
            fecha = date.fromisoformat(fecha.strip()) if fecha.strip() else None
        except ValueError:
            raise ErrorProduccion('Fecha no válida.')
    contable = _contable(fecha, timezone.localdate())
    horas = Decimal(str(horas or 0))
    if horas < 0:
        raise ErrorProduccion('Las horas no pueden ser negativas.')
    dia = fecha or contable  # PLAT-16: el trabajador debe haber estado en planilla ese día
    if trabajador is not None and not trabajador.activo_en(dia, dia):
        raise ErrorProduccion(f'{trabajador.nombre_completo} no estaba en planilla el {dia:%d/%m/%Y}'
                              + (f' (cese: {trabajador.fecha_cese:%d/%m/%Y}).' if trabajador.fecha_cese else '.'))
    if tipo == 'MO':
        tarifa = trabajador.costo_hora(contable) if trabajador else (centro.costo_hora_mo if centro else D0)
        importe = r2(horas * tarifa) if horas else Decimal(str(importe or 0))
    elif tipo == 'MAQUINA' and centro is not None and horas:
        importe = r2(horas * (centro.costo_hora_maquina + centro.costo_hora_cif + centro.cif_drivers_hora))
    importe = Decimal(str(importe or 0))
    if importe <= 0:
        raise ErrorProduccion('Indique las horas (con el trabajador o el puesto) o el importe.')
    if orden.estado == 'TERMINADA':
        if orden_destino is not None:
            if orden_destino.producto_id != orden.producto_id or orden_destino.estado not in (
                    OrdenProduccion.ABIERTAS + ('CERRADA_TEC',)):
                raise ErrorProduccion('La orden destino debe ser del mismo producto y estar abierta.')
            destino = 'OTRA_ORDEN'
        else:
            destino = 'GASTO'
    else:
        destino, orden_destino = 'ORDEN', None
    from core.auditoria import registrar
    t = CostoTardio.objects.create(
        orden=orden, tipo=tipo, destino=destino, orden_destino=orden_destino, trabajador=trabajador, centro=centro,
        horas=horas, importe=importe, fecha=contable, motivo=motivo.strip()[:250],
        usuario=usuario if usuario and usuario.is_authenticated else None)
    registrar('CREAR', orden, {'Costo tardío': f'{t.get_tipo_display()} S/ {importe:,.2f} · {t.get_destino_display()}'})
    return t


# ---------------------------------------------------------------- productos en proceso a fin de mes
def en_proceso(hasta):
    """[(orden, cargado, declarado al estándar, en proceso)] al cierre del día «hasta»: lo cargado a cada orden aún
    no liquidada (materiales entregados o descontados, horas de las declaraciones y costos tardíos) menos lo ya
    declarado al costo estándar."""
    from inventario.models import Operacion
    filas = []
    ids = set(Operacion.objects.filter(orden_imputada__isnull=False, estado='CONFIRMADO', fecha__lte=hasta)
              .values_list('orden_imputada_id', flat=True))
    ordenes = (OrdenProduccion.objects.filter(pk__in=ids, es_historica=False)
               .exclude(estado__in=['ANULADA', 'BORRADOR'])
               .exclude(estado='TERMINADA', fecha_fin__lte=hasta).select_related('producto'))
    for o in ordenes:
        materiales = D0
        for op in Operacion.objects.filter(orden_imputada=o, estado='CONFIRMADO', fecha__lte=hasta,
                                           tipo__codigo__in=['SAL_OP', 'DEV_OP', 'CONS_OP']).prefetch_related('items'):
            signo = -1 if op.tipo.clase == 'INGRESO' else 1
            materiales += signo * sum((r2(i.cantidad * i.costo_unitario) for i in op.items.all()), D0)
        declaraciones = o.declaraciones.filter(anulada=False, fecha__lte=hasta)
        horas = sum((d.costo_mo + d.costo_maquina + d.costo_cif for d in declaraciones), D0)
        tardios = sum((t.importe for t in CostoTardio.objects.filter(orden=o, destino='ORDEN', fecha__lte=hasta)),
                      D0)
        declarado = sum((d.valor_estandar for d in declaraciones), D0)
        cargado = materiales + horas + tardios
        proceso = r2(cargado - declarado)
        if proceso > 0:
            filas.append((o, cargado, declarado, proceso))
    return filas
