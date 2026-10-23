"""Cobranza y pago múltiple: comprobantes de varios clientes o proveedores en una sola operación, cada uno como
saldo (cobro o pago normal) o como detracción (depósito en el Banco de la Nación, con su propia constancia)."""
from decimal import Decimal

from django.db import transaction

from core.models import D0, r2

from .models import Movimiento
from .operaciones import ErrorTesoreria, _validar_sin_detraccion


def detraccion_pendiente(doc):
    """Detracción aún sin depositar del comprobante (moneda del documento), aunque no tenga el estado pendiente."""
    if not doc.sujeto_a_detraccion or doc.detraccion_constancia:
        return D0
    return max(doc.detraccion_monto - doc._detraccion_depositada(), D0)


def pendientes(modelo, tercero=None, hasta=None, moneda='', solo_detraccion=False, q='', limite=400):
    """Comprobantes con saldo de todos los terceros (o de uno), con lo sugerido como saldo y como detracción."""
    from django.db.models import Q
    qs = (modelo.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
          .select_related('tercero').order_by('fecha_vencimiento', 'id'))
    if tercero:
        qs = qs.filter(tercero=tercero)
    if hasta:
        qs = qs.filter(fecha_vencimiento__lte=hasta)
    if moneda:
        qs = qs.filter(moneda=moneda)
    if solo_detraccion:
        qs = qs.filter(detraccion_monto__gt=0, tipo_comprobante='01')
    if q:
        qs = qs.filter(Q(tercero__nombre__icontains=q) | Q(tercero__numero_doc__icontains=q) |
                       Q(serie__icontains=q) | Q(numero__icontains=q))
    docs = []
    for d in qs:
        if d.saldo <= 0:
            continue
        d.detr_pendiente = r2(min(detraccion_pendiente(d), d.saldo))
        d.sugerido = r2(d.saldo - d.detraccion_por_depositar)
        if solo_detraccion and not d.detr_pendiente:
            continue
        docs.append(d)
        if len(docs) >= limite:
            break
    return docs


@transaction.atomic
def registrar(modo, datos, lineas, usuario):
    """lineas: [(doc, es_detraccion, monto en la moneda del documento, constancia)]. Un movimiento por línea:
    el saldo en la cuenta de la operación y la detracción en la cuenta de detracciones, con su constancia como N° de
    operación (pasa al comprobante). Lanza ErrorTesoreria con todos los errores; si hay alguno no se registra nada."""
    from .views import _cobranza_cfg, _monto_en_cuenta
    cfg = _cobranza_cfg(modo)
    if not lineas:
        raise ErrorTesoreria('Elija al menos un comprobante e indique el monto.')
    errores, creados = [], []
    for doc, es_detraccion, monto, constancia in lineas:
        if monto <= 0:
            errores.append(f'{doc}: el monto debe ser mayor a cero.')
            continue
        if monto > doc.saldo + Decimal('0.01'):
            errores.append(f'{doc}: el monto ({monto:,.2f}) supera el saldo ({doc.saldo:,.2f}).')
            continue
        if es_detraccion:
            pendiente = detraccion_pendiente(doc)
            if not pendiente:
                errores.append(f'{doc}: no tiene detracción por depositar.')
                continue
            if monto > pendiente + Decimal('1'):  # el depósito puede ir redondeado a la unidad
                errores.append(f'{doc}: la detracción por depositar es {pendiente:,.2f}; marque «Saldo» para el resto.')
                continue
            cuenta = datos.get('cuenta_detraccion')
            if cuenta is None:
                errores.append('Elija la cuenta de las detracciones.')
                continue
        else:
            cuenta = datos['cuenta']
            if getattr(doc, 'pago_bloqueado', False):
                errores.append(f'{doc}: pago bloqueado por Compras ({doc.motivo_bloqueo_pago}).')
                continue
            if modo == 'pago':
                try:
                    _validar_sin_detraccion(doc, monto)
                except ErrorTesoreria as exc:
                    errores.append(str(exc))
                    continue
        concepto = 'DETRACCION' if es_detraccion else cfg['concepto']
        creados.append(Movimiento.objects.create(
            cuenta=cuenta, fecha=datos['fecha'], tipo=cfg['tipo'], concepto=concepto,
            medio_pago='DEPOSITO' if es_detraccion else datos['medio_pago'],
            numero_operacion=(constancia if es_detraccion else datos.get('numero_operacion', '')).strip()[:40],
            tercero=doc.tercero, monto=_monto_en_cuenta(monto, doc, cuenta, datos['fecha'], es_detraccion),
            monto_doc=monto, creado_por=usuario,
            glosa=(datos.get('glosa') or f'{"Detracción" if es_detraccion else concepto.title()} {doc}')[:250],
            **{cfg['fk']: doc}))
    if cfg['tipo'] == 'EGRESO' and creados and not errores:
        for cuenta in {m.cuenta for m in creados}:
            negativo = None if cuenta.admite_negativo else cuenta.primer_negativo(desde=datos['fecha'])
            if negativo:
                errores.append(f'Saldo insuficiente en {cuenta}: quedaría en {cuenta.simbolo} {negativo[1]:,.2f} el '
                               f'{negativo[0]:%d/%m/%Y}.')
    if errores:
        raise ErrorTesoreria(' | '.join(errores))
    if datos.get('sustento'):
        from core.sustentos import guardar_archivo, vincular
        archivo = guardar_archivo(datos['sustento'], usuario)
        for m in creados:
            vincular(m, archivo, usuario, datos.get('glosa') or '')
    return creados
