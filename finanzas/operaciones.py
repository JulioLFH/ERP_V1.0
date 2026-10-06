"""Operaciones de tesorería sin caja o en lote: anticipos, letras, cheques, entregas a rendir y pagos masivos.

Cada función valida y graba dentro de una transacción; los errores de negocio se devuelven como ErrorTesoreria
con un mensaje para el usuario."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.forms import periodo_cerrado
from core.models import D0, r2

from .models import (Aplicacion, CanjeLetras, Cheque, EntregaRendir, GastoRendicion, Letra, LineaPagoMasivo,
                     Movimiento, PagoMasivo, _siguiente)


class ErrorTesoreria(Exception):
    pass


def _periodo_abierto(fecha):
    if periodo_cerrado(fecha.strftime('%Y%m')):
        raise ErrorTesoreria(f'El periodo contable {fecha:%m/%Y} está cerrado.')


def _es_venta(doc):
    return doc._meta.model_name == 'venta'


def _validar_documento(doc, monto, moneda=None):
    if monto <= 0:
        raise ErrorTesoreria(f'{doc}: el monto debe ser mayor a cero.')
    if doc.estado != 'REGISTRADO' or doc.tipo_comprobante == '07':
        raise ErrorTesoreria(f'{doc}: no se puede cancelar (anulado o nota de crédito).')
    if moneda and doc.moneda != moneda:
        raise ErrorTesoreria(f'{doc} está en {doc.moneda} y la operación en {moneda}: use documentos de la misma '
                             f'moneda.')
    if monto > doc.saldo:
        raise ErrorTesoreria(f'{doc}: el monto ({monto:,.2f}) supera el saldo ({doc.saldo:,.2f}).')


# ---------------------------------------------------------------- anticipos
def anticipos_disponibles(tercero=None, tipo=None):
    """Movimientos de anticipo vigentes con saldo por aplicar (INGRESO = de clientes, EGRESO = a proveedores)."""
    qs = Movimiento.objects.filter(concepto='ANTICIPO').select_related('cuenta', 'tercero').order_by('fecha', 'id')
    if tercero is not None:
        qs = qs.filter(tercero=tercero)
    if tipo:
        qs = qs.filter(tipo=tipo)
    return [m for m in qs if m.anticipo_disponible > 0]


@transaction.atomic
def aplicar_anticipo(anticipo, aplicaciones, fecha, usuario):
    """aplicaciones: [(documento, monto)] del mismo tercero y moneda del anticipo."""
    anticipo = Movimiento.objects.select_for_update().get(pk=anticipo.pk)
    if not anticipo.es_anticipo:
        raise ErrorTesoreria('El movimiento no es un anticipo vigente.')
    if not anticipo.tercero_id:
        raise ErrorTesoreria('El anticipo no tiene cliente / proveedor: edítelo antes de aplicarlo.')
    _periodo_abierto(fecha)
    if fecha < anticipo.fecha:
        raise ErrorTesoreria('La aplicación no puede ser anterior a la fecha del anticipo.')
    total = sum((m for _, m in aplicaciones), D0)
    if total > anticipo.anticipo_disponible:
        raise ErrorTesoreria(f'Se aplican {total:,.2f} y el anticipo solo tiene {anticipo.anticipo_disponible:,.2f} '
                             f'disponibles.')
    creadas = []
    for doc, monto in aplicaciones:
        if _es_venta(doc) != (anticipo.tipo == 'INGRESO'):
            raise ErrorTesoreria('Un anticipo de cliente se aplica a ventas y uno a proveedor, a compras.')
        if doc.tercero_id != anticipo.tercero_id:
            raise ErrorTesoreria(f'{doc} es de otro {"cliente" if _es_venta(doc) else "proveedor"}.')
        _validar_documento(doc, monto, anticipo.cuenta.moneda)
        creadas.append(Aplicacion.objects.create(
            origen='ANTICIPO', fecha=fecha, anticipo=anticipo, monto_doc=monto, creado_por=usuario,
            glosa=f'Anticipo {anticipo.voucher}', **{'venta' if _es_venta(doc) else 'compra': doc}))
    return creadas


def anular_aplicacion(ap, motivo, usuario):
    if len((motivo or '').strip()) < 10:
        raise ErrorTesoreria('Explique el motivo de la anulación (mínimo 10 caracteres).')
    if ap.origen == 'CANJE':
        raise ErrorTesoreria('Un canje por letras se anula completo desde el canje.')
    _periodo_abierto(ap.fecha)
    ap.estado = 'ANULADO'
    ap.glosa = f'{ap.glosa} [Anulada: {motivo.strip()}]'[:250]
    ap.save()


# ---------------------------------------------------------------- letras
def _numero_letra(tipo):
    return _siguiente('LET', 'LC' if tipo == 'COBRAR' else 'LP')


@transaction.atomic
def canjear(tipo, tercero, fecha, moneda, tipo_cambio, documentos, letras, usuario, glosa=''):
    """Canjea documentos [(doc, monto)] por letras [(vencimiento, monto, numero o '')] del mismo importe total."""
    _periodo_abierto(fecha)
    if not documentos:
        raise ErrorTesoreria('Elija al menos un comprobante a canjear.')
    if not letras:
        raise ErrorTesoreria('Indique al menos una letra (vencimiento y monto).')
    total_docs = sum((m for _, m in documentos), D0)
    total_letras = sum((m for _, m, _ in letras), D0)
    if total_docs != total_letras:
        raise ErrorTesoreria(f'El total de las letras ({total_letras:,.2f}) debe ser igual al de los comprobantes '
                             f'canjeados ({total_docs:,.2f}).')
    canje = CanjeLetras.objects.create(tipo=tipo, tercero=tercero, fecha=fecha, moneda=moneda,
                                       tipo_cambio=tipo_cambio if moneda == 'USD' else Decimal('1'), glosa=glosa,
                                       creado_por=usuario)
    for doc, monto in documentos:
        if _es_venta(doc) != (tipo == 'COBRAR') or doc.tercero_id != tercero.pk:
            raise ErrorTesoreria(f'{doc} no corresponde al {"cliente" if tipo == "COBRAR" else "proveedor"}.')
        _validar_documento(doc, monto, moneda)
        Aplicacion.objects.create(origen='CANJE', fecha=fecha, canje=canje, monto_doc=monto, creado_por=usuario,
                                  glosa=f'Canje {canje.numero}', **{'venta' if _es_venta(doc) else 'compra': doc})
    for vencimiento, monto, numero in letras:
        if monto <= 0:
            raise ErrorTesoreria('Cada letra debe tener un monto mayor a cero.')
        if vencimiento < fecha:
            raise ErrorTesoreria('El vencimiento de una letra no puede ser anterior a la fecha del canje.')
        Letra.objects.create(numero=numero or _numero_letra(tipo), tipo=tipo, canje=canje, tercero=tercero,
                             moneda=moneda, tipo_cambio=canje.tipo_cambio, monto=monto, fecha_giro=fecha,
                             fecha_vencimiento=vencimiento, creado_por=usuario)
    return canje


@transaction.atomic
def anular_canje(canje, motivo, usuario):
    if len((motivo or '').strip()) < 10:
        raise ErrorTesoreria('Explique el motivo de la anulación (mínimo 10 caracteres).')
    _periodo_abierto(canje.fecha)
    for letra in canje.letras.all():
        if letra.movimientos.exists() or letra.estado not in ('CARTERA', 'ANULADA'):
            raise ErrorTesoreria(f'{letra} ya tiene cobros/pagos o cambió de estado: no se puede anular el canje.')
    canje.letras.update(estado='ANULADA', fecha_estado=timezone.localdate())
    canje.aplicaciones.update(estado='ANULADO')
    canje.estado = 'ANULADO'
    canje.glosa = f'{canje.glosa} [Anulado: {motivo.strip()}]'[:250]
    canje.save()
    for ap in Aplicacion.todos.filter(canje=canje):  # recentraliza el periodo
        ap.save()


ESTADOS_MANUALES = ('CARTERA', 'COBRANZA', 'DESCUENTO', 'PROTESTADA')


def cambiar_estado_letra(letra, estado, fecha, banco=None, codigo=''):
    if letra.estado not in Letra.ABIERTAS:
        raise ErrorTesoreria(f'{letra} está {letra.get_estado_display().lower()}: no cambia de estado.')
    if estado not in ESTADOS_MANUALES:
        raise ErrorTesoreria('Estado no permitido.')
    if estado in ('COBRANZA', 'DESCUENTO') and banco is None:
        raise ErrorTesoreria('Indique el banco donde se envía la letra.')
    letra.estado, letra.fecha_estado = estado, fecha
    if banco is not None:
        letra.banco, letra.codigo_banco = banco, codigo or letra.codigo_banco
    letra.save()


@transaction.atomic
def renovar(letra, nuevas, fecha, usuario):
    """Reemplaza el saldo de la letra por nuevas letras [(vencimiento, monto)] (los intereses se facturan aparte)."""
    letra = Letra.objects.select_for_update().get(pk=letra.pk)
    if letra.estado not in Letra.ABIERTAS:
        raise ErrorTesoreria(f'{letra} no se puede renovar ({letra.get_estado_display().lower()}).')
    saldo = letra.saldo
    total = sum((m for _, m in nuevas), D0)
    if not nuevas or total != saldo:
        raise ErrorTesoreria(f'Las nuevas letras deben sumar el saldo de la letra ({saldo:,.2f}); suman {total:,.2f}.')
    creadas = []
    for vencimiento, monto in nuevas:
        if monto <= 0 or vencimiento <= fecha:
            raise ErrorTesoreria('Cada nueva letra necesita monto mayor a cero y un vencimiento futuro.')
        creadas.append(Letra.objects.create(
            numero=_numero_letra(letra.tipo), tipo=letra.tipo, canje=letra.canje, renovada_de=letra,
            tercero=letra.tercero, moneda=letra.moneda, tipo_cambio=letra.tipo_cambio, monto=monto, fecha_giro=fecha,
            fecha_vencimiento=vencimiento, creado_por=usuario, glosa=f'Renovación de {letra.numero}'))
    # lo ya cobrado se queda en la letra original; su saldo pasa a las nuevas (misma cuenta 123 / 423)
    letra.estado, letra.fecha_estado = 'RENOVADA', fecha
    letra.glosa = f'{letra.glosa} Renovada por {", ".join(n.numero for n in creadas)}'.strip()[:250]
    letra.save()
    return creadas


def actualizar_letra(letra):
    """Tras un cobro/pago: cancelada si no tiene saldo (vuelve a cartera si se anuló el cobro)."""
    letra.refresh_from_db()
    if letra.estado in ('ANULADA', 'RENOVADA'):
        return
    if letra.saldo <= 0 and letra.estado != 'CANCELADA':
        letra.estado, letra.fecha_estado = 'CANCELADA', timezone.localdate()
        letra.save(update_fields=['estado', 'fecha_estado'])
    elif letra.saldo > 0 and letra.estado == 'CANCELADA':
        letra.estado = 'CARTERA'
        letra.save(update_fields=['estado'])


# ---------------------------------------------------------------- cheques
def registrar_cheque(movimientos, numero, usuario, cheque=None, banco_emisor=''):
    """Cheque de una cobranza (recibido) o pago (emitido) con medio CHEQUE; si ya estaba en cartera se vincula."""
    if not movimientos:
        return None
    m0 = movimientos[0]
    total = sum((m.monto for m in movimientos), D0)
    if cheque is None:
        if not numero:
            raise ErrorTesoreria('Indique el N° de cheque en "N° operación / cheque".')
        cheque = Cheque.objects.create(
            tipo='RECIBIDO' if m0.tipo == 'INGRESO' else 'EMITIDO', numero=numero, cuenta=m0.cuenta,
            banco_emisor=banco_emisor, tercero=m0.tercero, moneda=m0.cuenta.moneda, monto=total,
            fecha_emision=m0.fecha, estado='COBRADO' if m0.tipo == 'INGRESO' else 'CARTERA',
            fecha_estado=m0.fecha if m0.tipo == 'INGRESO' else None, creado_por=usuario,
            glosa=m0.glosa[:250])
    else:
        if cheque.estado != 'CARTERA' or cheque.tipo != 'RECIBIDO':
            raise ErrorTesoreria(f'{cheque} ya no está en cartera.')
        if cheque.fecha_pago and m0.fecha < cheque.fecha_pago:
            raise ErrorTesoreria(f'{cheque} es diferido: se puede depositar desde el {cheque.fecha_pago:%d/%m/%Y}.')
        if total != cheque.monto:
            raise ErrorTesoreria(f'Los montos aplicados ({total:,.2f}) deben sumar el importe del cheque '
                                 f'({cheque.monto:,.2f}).')
        cheque.cuenta, cheque.estado, cheque.fecha_estado = m0.cuenta, 'COBRADO', m0.fecha
        cheque.save()
    Movimiento.objects.filter(pk__in=[m.pk for m in movimientos]).update(cheque=cheque)
    return cheque


@transaction.atomic
def cambiar_estado_cheque(cheque, estado, fecha, motivo, usuario):
    """COBRADO: el banco lo pagó. RECHAZADO / ANULADO: se anulan sus movimientos (el documento vuelve a deber)."""
    if cheque.estado in ('RECHAZADO', 'ANULADO'):
        raise ErrorTesoreria(f'{cheque} ya está {cheque.get_estado_display().lower()}.')
    if estado == 'COBRADO':
        if cheque.tipo == 'RECIBIDO' and not cheque.movimientos.exists():
            raise ErrorTesoreria('Un cheque recibido en cartera se deposita desde "Cobranzas" (aplicándolo a los '
                                 'comprobantes del cliente).')
        cheque.estado, cheque.fecha_estado = 'COBRADO', fecha
        cheque.save()
        return
    if estado not in ('RECHAZADO', 'ANULADO'):
        raise ErrorTesoreria('Estado no permitido.')
    if len((motivo or '').strip()) < 10:
        raise ErrorTesoreria('Explique el motivo (mínimo 10 caracteres).')
    ahora = timezone.now()
    for m in cheque.movimientos.all():
        if m.conciliado:
            raise ErrorTesoreria(f'{m.voucher} está conciliado: quite primero la conciliación.')
        _periodo_abierto(m.fecha)
        m.estado, m.motivo_anulacion = 'ANULADO', f'Cheque {cheque.numero} {estado.lower()}: {motivo.strip()}'[:250]
        m.anulado_por, m.anulado_en = usuario, ahora
        m.save()
        if m.letra_id:
            actualizar_letra(m.letra)
    cheque.estado, cheque.fecha_estado = estado, fecha
    cheque.glosa = f'{cheque.glosa} [{motivo.strip()}]'[:250]
    cheque.save()


# ---------------------------------------------------------------- entregas a rendir y caja chica
def _mov_entrega(entrega, cuenta, fecha, tipo, monto, usuario, glosa, numero_operacion='', medio='TRANSFERENCIA'):
    if monto <= 0:
        raise ErrorTesoreria('El monto debe ser mayor a cero.')
    if cuenta.moneda != entrega.moneda:
        raise ErrorTesoreria(f'La cuenta {cuenta} no es de la moneda del fondo ({entrega.moneda}).')
    _periodo_abierto(fecha)
    if tipo == 'EGRESO':
        error = cuenta.error_sobregiro(monto, fecha=fecha)
        if error:
            raise ErrorTesoreria(error)
    return Movimiento.objects.create(
        cuenta=cuenta, fecha=fecha, tipo=tipo, concepto='CAJA_CHICA', medio_pago=medio,
        numero_operacion=numero_operacion, tercero=entrega.responsable, monto=monto, entrega=entrega,
        glosa=glosa[:250], creado_por=usuario)


@transaction.atomic
def crear_entrega(entrega, cuenta, monto, numero_operacion, medio, usuario):
    entrega.creado_por = usuario
    if entrega.tipo == 'CAJA_CHICA' and not entrega.monto_fondo:
        entrega.monto_fondo = monto
    entrega.save()
    _mov_entrega(entrega, cuenta, entrega.fecha, 'EGRESO', monto, usuario,
                 f'{entrega.get_tipo_display()} {entrega.numero} - {entrega.motivo}', numero_operacion, medio)
    return entrega


def _abierta(entrega):
    if entrega.estado != 'ABIERTA':
        raise ErrorTesoreria(f'{entrega.numero} está liquidada.')


@transaction.atomic
def rendir_gasto(entrega, gasto, usuario):
    _abierta(entrega)
    _periodo_abierto(gasto.fecha)
    if gasto.monto <= 0:
        raise ErrorTesoreria('El monto del gasto debe ser mayor a cero.')
    gasto.entrega, gasto.creado_por = entrega, usuario
    gasto.save()
    return gasto


@transaction.atomic
def rendir_compra(entrega, compra, monto, fecha, usuario):
    """Factura registrada en compras y pagada por el responsable con el dinero del fondo."""
    _abierta(entrega)
    _periodo_abierto(fecha)
    _validar_documento(compra, monto, entrega.moneda)
    return Aplicacion.objects.create(origen='RENDICION', fecha=fecha, compra=compra, entrega=entrega,
                                     monto_doc=monto, creado_por=usuario, glosa=f'Rendición {entrega.numero}')


@transaction.atomic
def movimiento_fondo(entrega, accion, cuenta, monto, fecha, usuario, numero_operacion=''):
    """devolver (ingreso del saldo no usado), reembolsar (egreso de lo gastado de más), reponer (caja chica)."""
    _abierta(entrega)
    if accion == 'devolver':
        if monto > entrega.saldo:
            raise ErrorTesoreria(f'Solo tiene {entrega.saldo:,.2f} por devolver.')
        return _mov_entrega(entrega, cuenta, fecha, 'INGRESO', monto, usuario,
                            f'Devolución del saldo {entrega.numero}', numero_operacion)
    if accion == 'reembolsar':
        if monto > -entrega.saldo:
            raise ErrorTesoreria(f'Solo hay {max(-entrega.saldo, D0):,.2f} por reembolsar al responsable.')
        return _mov_entrega(entrega, cuenta, fecha, 'EGRESO', monto, usuario,
                            f'Reembolso de gastos {entrega.numero}', numero_operacion)
    if accion in ('reponer', 'ampliar'):
        return _mov_entrega(entrega, cuenta, fecha, 'EGRESO', monto, usuario,
                            f'{"Reposición" if accion == "reponer" else "Ampliación"} {entrega.numero}',
                            numero_operacion)
    raise ErrorTesoreria('Acción no válida.')


def liquidar(entrega, fecha):
    _abierta(entrega)
    if entrega.saldo != 0:
        raise ErrorTesoreria(f'El saldo en poder del responsable es {entrega.saldo:,.2f}: registre la devolución o '
                             f'el reembolso antes de liquidar.')
    entrega.estado, entrega.fecha_liquidacion = 'LIQUIDADA', fecha
    entrega.save()


# ---------------------------------------------------------------- pagos masivos
@transaction.atomic
def crear_pago_masivo(cuenta, fecha, lineas, usuario, glosa=''):
    """lineas: [(compra, monto)] en la moneda de la cuenta; genera el lote (el archivo sale de él)."""
    if not lineas:
        raise ErrorTesoreria('Elija al menos un comprobante.')
    pago = PagoMasivo.objects.create(cuenta=cuenta, fecha=fecha, glosa=glosa, creado_por=usuario)
    for compra, monto in lineas:
        _validar_documento(compra, monto, cuenta.moneda)
        LineaPagoMasivo.objects.create(pago=pago, compra=compra, monto=monto)
    return pago


def datos_banco(tercero, cuenta):
    """(tipo de abono, cuenta destino): misma entidad -> cuenta propia; otra -> CCI (interbancaria)."""
    if tercero.banco and tercero.banco == cuenta.banco and tercero.cuenta_bancaria:
        return 'PROPIO BANCO', tercero.cuenta_bancaria
    if tercero.cci:
        return 'INTERBANCARIA (CCI)', tercero.cci
    return 'SIN CUENTA', ''


@transaction.atomic
def registrar_pago_masivo(pago, usuario, numero_operacion=''):
    pago = PagoMasivo.objects.select_for_update().get(pk=pago.pk)
    if pago.estado != 'BORRADOR':
        raise ErrorTesoreria(f'{pago} ya está {pago.get_estado_display().lower()}.')
    _periodo_abierto(pago.fecha)
    error = pago.cuenta.error_sobregiro(pago.total, fecha=pago.fecha)
    if error:
        raise ErrorTesoreria(error)
    for linea in pago.lineas.select_related('compra__tercero'):
        compra = type(linea.compra).objects.con_saldos().get(pk=linea.compra_id)
        _validar_documento(compra, linea.monto, pago.cuenta.moneda)
        linea.movimiento = Movimiento.objects.create(
            cuenta=pago.cuenta, fecha=pago.fecha, tipo='EGRESO', concepto='PAGO', medio_pago='TRANSFERENCIA',
            numero_operacion=numero_operacion or pago.numero, tercero=compra.tercero, compra=compra,
            monto=linea.monto, monto_doc=linea.monto, creado_por=usuario,
            glosa=(pago.glosa or f'Pago masivo {pago.numero}')[:250])
        linea.save(update_fields=['movimiento'])
    pago.estado = 'PAGADO'
    pago.save(update_fields=['estado'])
    return pago


def r2_o_none(valor):
    try:
        return r2(Decimal(str(valor).replace(',', '')))
    except Exception:
        return None
