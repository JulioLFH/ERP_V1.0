"""Proyecto inmobiliario (venta de unidades): tablero de unidades, separación y venta con su cronograma de pagos,
cobro de cada cuota con su comprobante de anticipo, terminación del proyecto, entrega con el costo de cada unidad y
desistimiento con nota de crédito y penalidad.

Contabilidad (NIC 2, NIC 23 y NIIF 15: el ingreso se reconoce al entregar la unidad):
- Cada cobro emite su comprobante de anticipo. En la primera venta de inmuebles el IGV grava solo la construcción: el
  terreno (el % de la obra; la ley lo presume en el 50 % del valor de venta) va como línea inafecta. La vivienda dentro
  del límite de exoneración vigente (parámetro en UIT) va exonerada. El ingreso queda como anticipo recibido (122) hasta
  la entrega. Las arras de retractación dentro del % del parámetro no llevan IGV ni comprobante: van a la 4699 y con
  la minuta se emite su comprobante y se aplican a él.
- Cada mes los costos del proyecto (clase 6 con el centro de costo de la obra, incluidos los intereses del préstamo que
  lo financia) se activan como inmuebles en construcción (2341 contra la variación de la producción almacenada 7134)
  hasta la terminación (conformidad de obra): entonces el saldo pasa a inmuebles terminados (2141) y lo posterior va a
  resultados. El terreno y lo cargado directo a la 2341 con el centro de la obra ya están activados y suman al costo.
- En la entrega los anticipos pasan a la venta (122 contra 7024), el costo de las unidades sale de los inmuebles en
  construcción o terminados al costo de ventas (6924) por área techada, precio de lista o mixto, y se provisiona la
  garantía postventa (% del parámetro, NIC 37).
- En el desistimiento se emite la nota de crédito de lo que se devuelve; lo retenido como penalidad (primero las arras)
  pasa del anticipo o de las arras a otros ingresos.
Los gastos generales repartidos a la obra (Contabilidad › Reparto de gastos) no se activan: administración y ventas
no forman parte del costo del inmueble (NIC 2 párr. 16)."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import CobroInmueble, CuotaInmueble, DevolucionInmueble, Obra, Unidad, VentaInmueble

D0 = Decimal('0')
DIAS_AVISO = 3
ORIGENES_SIN_ACTIVAR = ('REPARTO', 'INMUEBLE')
COMERCIALES = ('LOCAL', 'OFICINA', 'LOTE')


class ErrorInmobiliario(Exception):
    pass


def r2(v):
    return Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _parametro(clave, fecha=None):
    from core import parametros
    return parametros.valor(clave, fecha)


def _sumar_meses(fecha, meses):
    mes = fecha.month - 1 + meses
    anio, mes = fecha.year + mes // 12, mes % 12 + 1
    dias = [31, 29 if anio % 4 == 0 and (anio % 100 or anio % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30,
            31][mes - 1]
    return fecha.replace(year=anio, month=mes, day=min(fecha.day, dias))


def _validar_periodo(fecha):
    from contabilidad.models import PeriodoContable
    if PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
        raise ErrorInmobiliario(f'El periodo {fecha:%m/%Y} está cerrado.')


def cronograma(precio, financiamiento, fecha, separacion=D0, inicial=D0, fecha_inicial=None, cuotas=0,
               primera_cuota=None, bono=D0, fecha_banco=None, arras=False):
    """[(concepto, vence, monto)] que suma el precio: separación (o arras de retractación), cuota inicial, cuotas
    mensuales con la empresa (financiamiento directo), bono o subsidio del Estado y el desembolso del banco (crédito
    hipotecario o MiVivienda) o, al contado, el saldo en la fecha de la cuota inicial."""
    precio, separacion, inicial, bono = (Decimal(x or 0) for x in (precio, separacion, inicial, bono))
    filas = []
    if separacion:
        filas.append(('ARRAS' if arras else 'SEPARACION', fecha, separacion))
    if inicial:
        filas.append(('INICIAL', fecha_inicial or fecha, inicial))
    if bono:
        filas.append(('BONO', fecha_banco or fecha_inicial or fecha, bono))
    resto = precio - separacion - inicial - bono
    if resto < 0:
        raise ErrorInmobiliario('La separación, la cuota inicial y el bono superan el precio.')
    if resto:
        if financiamiento == 'DIRECTO':
            n = max(int(cuotas or 1), 1)
            base = r2(resto / n)
            inicio = primera_cuota or _sumar_meses(fecha_inicial or fecha, 1)
            for i in range(n):
                filas.append(('CUOTA', _sumar_meses(inicio, i), resto - base * (n - 1) if i == n - 1 else base))
        elif financiamiento in ('HIPOTECARIO', 'MIVIVIENDA', 'TECHO_PROPIO'):
            filas.append(('BANCO', fecha_banco or _sumar_meses(fecha_inicial or fecha, 2), resto))
        else:
            filas.append(('CUOTA', fecha_inicial or fecha, resto))
    return filas


def exoneracion(unidades, precio, fecha):
    """¿La venta está exonerada del IGV? Primera venta de vivienda (departamento o casa, con su estacionamiento o
    depósito, sin locales ni oficinas) cuyo valor no supera el límite vigente en UIT (parámetro; 0 = sin
    exoneración)."""
    limite = _parametro('INM_EXONERACION_UIT', fecha)
    if not limite:
        return False
    tipos = {u.tipo for u in unidades}
    if not tipos & set(Unidad.VIVIENDA) or tipos & set(COMERCIALES):
        return False
    return Decimal(precio) <= limite * _parametro('UIT', fecha)


@transaction.atomic
def separar(obra, unidades, cliente, precio, usuario, filas, financiamiento='CONTADO', banco='', fecha=None,
            vence_separacion=None, oportunidad=None, notas=''):
    """Separación de una o más unidades con su cronograma de pagos [(concepto, vence, monto)]."""
    if obra.tipo != 'INMOBILIARIA':
        raise ErrorInmobiliario('La obra no es un proyecto inmobiliario.')
    fecha = fecha or timezone.localdate()
    unidades = list(Unidad.objects.select_for_update().filter(pk__in=[u.pk for u in unidades], obra=obra))
    if not unidades:
        raise ErrorInmobiliario('Elija al menos una unidad del proyecto.')
    comunes = [u.codigo for u in unidades if not u.vendible]
    if comunes:
        raise ErrorInmobiliario(f'Las áreas comunes no se venden: {", ".join(comunes)}.')
    ocupadas = [u.codigo for u in unidades if u.estado != 'DISPONIBLE']
    if ocupadas:
        raise ErrorInmobiliario(f'No están disponibles: {", ".join(ocupadas)}.')
    precio = Decimal(precio or 0)
    if precio <= 0:
        raise ErrorInmobiliario('Indique el precio de venta.')
    if not filas:
        filas = [('CUOTA', fecha, precio)]
    if sum((Decimal(m) for _, _, m in filas), D0) != precio:
        raise ErrorInmobiliario(f'El cronograma suma S/ {sum((Decimal(m) for _, _, m in filas), D0):,.2f} y el precio es '
                                f'S/ {precio:,.2f}.')
    if any(Decimal(m) <= 0 for _, _, m in filas):
        raise ErrorInmobiliario('Cada cuota del cronograma debe ser mayor a cero.')
    arras = sum((Decimal(m) for c, _, m in filas if c == 'ARRAS'), D0)
    tope = r2(precio * _parametro('INM_ARRAS_PCT', fecha) / 100)
    if arras > tope:
        raise ErrorInmobiliario(f'Las arras de retractación sin IGV llegan hasta S/ {tope:,.2f} '
                                f'({_parametro("INM_ARRAS_PCT", fecha):g} % del precio): registre el exceso como '
                                f'separación (con comprobante).')
    v = VentaInmueble.objects.create(
        obra=obra, cliente=cliente, precio=precio, financiamiento=financiamiento, banco=banco[:120],
        fecha_separacion=fecha, vence_separacion=vence_separacion, oportunidad=oportunidad,
        vendedor=usuario if usuario and usuario.is_authenticated else None, terreno_pct=obra.terreno_pct or D0,
        exonerada_igv=exoneracion(unidades, precio, fecha), notas=notas)
    v.unidades.set(unidades)
    Unidad.objects.filter(pk__in=[u.pk for u in unidades]).update(estado='SEPARADA')
    if oportunidad is not None and oportunidad.etapa in ('PROSPECTO', 'CALIFICADO', 'PROPUESTA'):
        _etapa(oportunidad, 'NEGOCIACION', monto=precio)
    for n, (concepto, vence, monto) in enumerate(sorted(filas, key=lambda f: f[1]), 1):
        CuotaInmueble.objects.create(venta=v, numero=n, concepto=concepto, vence=vence, monto=Decimal(monto))
    return v


def _etapa(oportunidad, etapa, monto=None, motivo=''):
    """La oportunidad del comprador en el CRM sigue a la venta: separación → negociación, minuta → ganada,
    anulación o desistimiento → perdida."""
    oportunidad.etapa = etapa
    if monto is not None:
        oportunidad.monto = monto
    if motivo:
        oportunidad.motivo_perdida = motivo[:200]
    oportunidad.save(update_fields=['etapa', 'monto', 'motivo_perdida', 'actualizado'])


@transaction.atomic
def firmar_minuta(venta, fecha=None, usuario=None):
    """La venta pasa a vendida; las arras recibidas pasan a ser parte del precio: se emite su comprobante de anticipo
    (nace el IGV) y se aplican a él."""
    from finanzas.models import Aplicacion
    if venta.estado != 'SEPARADA':
        raise ErrorInmobiliario(f'La venta está {venta.get_estado_display().lower()}.')
    fecha = fecha or timezone.localdate()
    _validar_periodo(fecha)
    venta.estado, venta.fecha_minuta = 'VENDIDA', fecha
    venta.save(update_fields=['estado', 'fecha_minuta'])
    venta.unidades.update(estado='VENDIDA')
    for cobro in CobroInmueble.objects.filter(cuota__venta=venta, cuota__concepto='ARRAS', comprobante=None):
        comp = _emitir_anticipo(venta, cobro.monto, fecha, 'Arras aplicadas al precio')
        ap = Aplicacion.objects.create(origen='ANTICIPO', fecha=fecha, venta=comp, anticipo=cobro.movimiento,
                                       monto_doc=comp.total, creado_por=_usuario(usuario),
                                       glosa=f'Arras de la venta {venta.numero} aplicadas con la minuta')
        cobro.comprobante, cobro.aplicacion = comp, ap
        cobro.save(update_fields=['comprobante', 'aplicacion'])
    if venta.oportunidad_id and venta.oportunidad.etapa != 'GANADA':
        _etapa(venta.oportunidad, 'GANADA', monto=venta.precio)
    return venta


def _usuario(usuario):
    return usuario if usuario is not None and usuario.is_authenticated else None


@transaction.atomic
def anular(venta, motivo):
    """Anula una venta sin cobros (con cobros: desistimiento, con devolución y penalidad)."""
    if venta.estado in ('ENTREGADA', 'ANULADA'):
        raise ErrorInmobiliario(f'La venta está {venta.get_estado_display().lower()}.')
    if not (motivo or '').strip():
        raise ErrorInmobiliario('Indique el motivo de la anulación (desistimiento, separación vencida…).')
    cobrado = venta.cobrado
    if cobrado:
        raise ErrorInmobiliario(f'Tiene cobros por S/ {cobrado:,.2f}: registre el desistimiento (nota de crédito, '
                                f'devolución y penalidad).')
    venta.estado, venta.motivo_anulacion = 'ANULADA', motivo.strip()[:200]
    venta.fecha_anulacion = timezone.localdate()
    venta.save(update_fields=['estado', 'motivo_anulacion', 'fecha_anulacion'])
    venta.unidades.update(estado='DISPONIBLE')
    if venta.oportunidad_id and venta.oportunidad.etapa in venta.oportunidad.ABIERTAS:
        _etapa(venta.oportunidad, 'PERDIDA', motivo=f'Venta {venta.numero} anulada: {venta.motivo_anulacion}')
    return venta


# ---------------------------------------------------------------- comprobantes de anticipo y cobros
def producto_anticipo():
    """Servicio «Anticipo de inmueble» cuya cuenta de ventas es la de anticipos de clientes (122)."""
    from contabilidad.models import CuentaDefecto
    from core.models import Producto
    p = Producto.objects.filter(codigo='ANTINMUEBLE').first()
    if p is None:
        p = Producto(codigo='ANTINMUEBLE', nombre='Anticipo de inmueble', clase='SERVICIO', unidad='ZZ',
                     puede_comprarse=False)
        p.cuenta_venta = CuentaDefecto.mapa().get('mov_ANTICIPO_INGRESO')
        p.save()
    return p


def partir(monto, terreno_pct, igv_tasa=Decimal('18')):
    """(construcción sin IGV, IGV, terreno) de un importe cobrado: el terreno es el % del valor de venta (sin IGV) y
    el IGV grava solo la construcción. El terreno lleva el redondeo para que la suma sea el importe."""
    p = Decimal(terreno_pct or 0) / 100
    tasa = Decimal(igv_tasa) / 100
    construccion = r2(monto * (1 - p) / ((1 - p) * (1 + tasa) + p)) if p < 1 else D0
    igv = r2(construccion * tasa)
    return construccion, igv, Decimal(monto) - construccion - igv


def _enviar_sunat(pk):
    from core import sunat
    from ventas.models import Venta
    try:
        sunat.enviar_comprobante(Venta.objects.get(pk=pk))
    except sunat.ErrorFacturacion:
        pass  # queda con el error; se reenvía desde el comprobante


def _enviar_si_corresponde(comp):
    from core import sunat
    from core.models import FacturacionConfig
    cfg = FacturacionConfig.actual()
    if cfg.activa and cfg.envio_automatico and comp.tipo_comprobante in sunat.TIPO_NUBEFACT:
        transaction.on_commit(lambda: _enviar_sunat(comp.pk))


def _items_anticipo(venta, monto, texto, terreno_pct=None):
    """[(afectación, valor sin IGV, descripción)] del importe: exonerado si la venta lo está; si no, construcción
    gravada y terreno inafecto."""
    from core.models import Empresa
    obra = venta.obra
    unidades = ', '.join(u.codigo for u in venta.unidades.all())
    if venta.exonerada_igv:
        return [('EXONERADA', Decimal(monto), f'{texto}: {unidades} – {obra.nombre} (vivienda exonerada del IGV)')]
    pct = venta.terreno_pct if terreno_pct is None else terreno_pct
    construccion, _, terreno = partir(monto, pct, Empresa.actual().igv_tasa)
    filas = []
    if construccion:
        filas.append(('GRAVADA', construccion, f'{texto}: construcción de {unidades} – {obra.nombre}'))
    if terreno:
        filas.append(('INAFECTA', terreno, f'{texto}: terreno de {unidades} ({pct:g} % del valor, no gravado con '
                                           f'IGV)'))
    return filas


def _emitir_anticipo(venta, monto, fecha, texto):
    """Comprobante de anticipo (factura si el comprador tiene RUC; si no, boleta) por el importe con IGV."""
    from core.models import Serie
    from ventas.forms import _serie_defecto
    from ventas.models import Venta, VentaItem
    cliente, obra = venta.cliente, venta.obra
    tipo = '01' if cliente.tipo_doc == '6' else '03'
    serie, numero = Serie.siguiente(tipo, _serie_defecto(tipo))
    comp = Venta.objects.create(
        tipo_comprobante=tipo, serie=serie, numero=numero, tercero=cliente, fecha_emision=fecha, forma_pago='CONTADO',
        tipo_operacion='GRAVADA', descontar_stock=False, centro_costo=obra.centro_costo, vendedor=(
            venta.vendedor.get_full_name() or venta.vendedor.username)[:80] if venta.vendedor_id else '',
        glosa=f'{texto} de la venta {venta.numero}: {", ".join(u.codigo for u in venta.unidades.all())} – '
              f'{obra.nombre}'[:500])
    producto = producto_anticipo()
    for afectacion, valor, descripcion in _items_anticipo(venta, monto, texto):
        VentaItem.objects.create(documento=comp, producto=producto, cantidad=1, precio_unitario=valor,
                                 afectacion=afectacion, descripcion=descripcion[:250])
    comp.calcular_totales()
    comp.save()
    _enviar_si_corresponde(comp)
    return comp


def _emitir_nota_credito(comp, monto, fecha, motivo):
    """Nota de crédito (anulación de la operación) por parte o todo un comprobante de anticipo, con la misma
    proporción de construcción, terreno o exonerado."""
    from core.models import Serie
    from ventas.forms import _serie_defecto
    from ventas.models import Venta, VentaItem
    serie, numero = Serie.siguiente('07', _serie_defecto('07', comp))
    nc = Venta.objects.create(
        tipo_comprobante='07', serie=serie, numero=numero, tercero=comp.tercero, fecha_emision=fecha,
        forma_pago='CONTADO', tipo_operacion='GRAVADA', descontar_stock=False, centro_costo=comp.centro_costo,
        doc_referencia=comp, motivo_nota='01', vendedor=comp.vendedor, glosa=f'Desistimiento: {motivo}'[:500])
    factor = Decimal(monto) / comp.total
    lineas = list(comp.items.all())
    restante = r2(Decimal(monto) - comp.igv * factor) if comp.igv else Decimal(monto)
    for n, i in enumerate(lineas):
        valor = restante if n == len(lineas) - 1 else r2(i.subtotal * factor)
        restante -= valor
        VentaItem.objects.create(documento=nc, producto=i.producto, cantidad=1, precio_unitario=valor,
                                 afectacion=i.afectacion, descripcion=f'Devolución: {i.descripcion}'[:250])
    nc.calcular_totales()
    # el IGV calculado puede diferir en un céntimo del proporcional: la línea gravada absorbe la diferencia
    diferencia = Decimal(monto) - nc.total
    if diferencia:
        ultima = nc.items.order_by('-id').first()
        ultima.precio_unitario += diferencia
        ultima.save()
        nc.calcular_totales()
    nc.save()
    _enviar_si_corresponde(nc)
    return nc


def tipo_cambio(fecha):
    from core.tipo_cambio import venta_del_dia
    tc = venta_del_dia(fecha, consultar=False)
    return tc if tc and tc != 1 else _parametro('TC_REFERENCIAL', fecha)


def _validar_bancarizacion(monto, fecha, medio_pago, numero_operacion):
    """Desde el monto de bancarización: ni efectivo ni sin N° de operación."""
    tope = _parametro('BANCARIZACION_SOLES', fecha)
    if tope and monto >= tope:
        if medio_pago == 'EFECTIVO':
            raise ErrorInmobiliario(f'Desde S/ {tope:,.2f} el pago se hace con un medio de pago (transferencia, '
                                    f'depósito o cheque), no en efectivo (bancarización).')
        if not (numero_operacion or '').strip():
            raise ErrorInmobiliario(f'Desde S/ {tope:,.2f} indique el N° de operación del medio de pago '
                                    f'(bancarización).')


@transaction.atomic
def cobrar(cuota, monto, fecha, cuenta, usuario, medio_pago='TRANSFERENCIA', numero_operacion='', sustento=None):
    """Cobro de una cuota en la cuenta recaudadora del proyecto: comprobante de anticipo y su cobranza; las arras de
    retractación, sin comprobante (a la cuenta de arras)."""
    from contabilidad.models import CuentaDefecto
    from finanzas import multiple
    from finanzas.models import Movimiento
    from finanzas.operaciones import ErrorTesoreria
    venta = cuota.venta
    obra = venta.obra
    monto = r2(monto or 0)
    if venta.estado in ('ANULADA', 'ENTREGADA'):
        raise ErrorInmobiliario(f'La venta está {venta.get_estado_display().lower()}.')
    if monto <= 0:
        raise ErrorInmobiliario('Indique el importe cobrado.')
    if monto > cuota.saldo:
        raise ErrorInmobiliario(f'El importe supera el saldo de la cuota (S/ {cuota.saldo:,.2f}).')
    cuenta = cuenta or obra.cuenta_recaudadora
    if cuenta is None:
        raise ErrorInmobiliario('Elija la cuenta donde ingresa el cobro (o la cuenta recaudadora del proyecto).')
    fecha = fecha or timezone.localdate()
    _validar_periodo(fecha)
    _validar_bancarizacion(monto, fecha, medio_pago, numero_operacion)
    if cuota.concepto == 'ARRAS' and venta.estado == 'SEPARADA':
        mov = Movimiento.objects.create(
            cuenta=cuenta, fecha=fecha, tipo='INGRESO', concepto='OTRO', medio_pago=medio_pago,
            numero_operacion=(numero_operacion or '')[:40], tercero=venta.cliente, monto=monto,
            cuenta_contable=CuentaDefecto.mapa()['inmuebles_arras'], creado_por=_usuario(usuario),
            glosa=f'Arras de retractación {venta.numero} {venta.cliente.nombre}'[:250])
        if sustento:
            from core.sustentos import adjuntar
            adjuntar(mov, sustento, usuario, 'Constancia de pago de las arras')
        return CobroInmueble.objects.create(cuota=cuota, fecha=fecha, monto=monto, movimiento=mov)
    concepto = (f'{cuota.get_concepto_display()}'
                f'{"" if cuota.concepto in ("SEPARACION", "INICIAL", "ARRAS") else f" {cuota.numero}"}')
    comp = _emitir_anticipo(venta, monto, fecha, f'Anticipo ({concepto.lower()})')
    datos = {'cuenta': cuenta, 'fecha': fecha, 'medio_pago': medio_pago, 'numero_operacion': numero_operacion or '',
             'glosa': f'Cobro {concepto} {venta.numero} {venta.cliente.nombre}'[:250], 'sustento': sustento}
    try:
        movimiento = multiple.registrar('cobranza', datos, [(comp, False, comp.total, '')], usuario)[0]
    except ErrorTesoreria as exc:
        raise ErrorInmobiliario(str(exc))
    return CobroInmueble.objects.create(cuota=cuota, fecha=fecha, monto=monto, comprobante=comp,
                                        movimiento=movimiento)


@transaction.atomic
def desistir(venta, motivo, penalidad, fecha, cuenta, usuario, medio_pago='TRANSFERENCIA', numero_operacion=''):
    """Desistimiento del comprador: retiene la penalidad (primero de las arras), emite la nota de crédito de lo que
    se devuelve de los anticipos, registra la devolución y libera las unidades."""
    from contabilidad.automatico import marcar_pendiente
    from contabilidad.models import CuentaDefecto
    from finanzas.models import Movimiento
    if venta.estado not in ('SEPARADA', 'VENDIDA'):
        raise ErrorInmobiliario(f'La venta está {venta.get_estado_display().lower()}.')
    motivo = (motivo or '').strip()
    if not motivo:
        raise ErrorInmobiliario('Indique el motivo del desistimiento.')
    fecha = fecha or timezone.localdate()
    _validar_periodo(fecha)
    cobros = list(CobroInmueble.objects.filter(cuota__venta=venta).select_related('comprobante', 'cuota'))
    cobrado = sum((c.monto for c in cobros), D0)
    penalidad = r2(penalidad or 0)
    if penalidad < 0 or penalidad > cobrado:
        raise ErrorInmobiliario(f'La penalidad va de 0 a lo cobrado (S/ {cobrado:,.2f}).')
    arras = sum((c.monto for c in cobros if c.comprobante_id is None), D0)
    retener_arras = min(penalidad, arras)
    devolver_arras = arras - retener_arras
    retener_comp = penalidad - retener_arras
    por_comprobante = defaultdict(lambda: D0)
    for c in cobros:
        if c.comprobante_id:
            por_comprobante[c.comprobante] += c.monto
    devolver_comp = sum(por_comprobante.values(), D0) - retener_comp
    if devolver_arras + devolver_comp > 0:
        if cuenta is None:
            raise ErrorInmobiliario('Elija la cuenta desde la que se devuelve el dinero.')
        _validar_bancarizacion(devolver_arras + devolver_comp, fecha, medio_pago, numero_operacion)
    cta = CuentaDefecto.mapa()
    comunes = {'cuenta': cuenta, 'fecha': fecha, 'tipo': 'EGRESO', 'concepto': 'OTRO', 'medio_pago': medio_pago,
               'numero_operacion': (numero_operacion or '')[:40], 'tercero': venta.cliente,
               'creado_por': _usuario(usuario)}
    if devolver_arras > 0:
        mov = Movimiento.objects.create(monto=devolver_arras, cuenta_contable=cta['inmuebles_arras'],
                                        glosa=f'Devolución de arras {venta.numero} {venta.cliente.nombre}'[:250],
                                        **comunes)
        DevolucionInmueble.objects.create(venta=venta, fecha=fecha, monto=devolver_arras, movimiento=mov)
    # se devuelve primero lo último cobrado; cada comprobante con su nota de crédito
    for comp in sorted(por_comprobante, key=lambda x: (x.fecha_emision, x.pk), reverse=True):
        parte = min(por_comprobante[comp], devolver_comp)
        if parte <= 0:
            break
        nc = _emitir_nota_credito(comp, parte, fecha, motivo)
        parte = nc.total  # lo que la nota de crédito anula (con su IGV redondeado) es lo que se devuelve
        devolver_comp -= parte
        mov = Movimiento.objects.create(monto=parte, venta=comp, monto_doc=-parte, cuenta_contable=cta['cliente'],
                                        glosa=f'Devolución {venta.numero} {venta.cliente.nombre}'[:250], **comunes)
        DevolucionInmueble.objects.create(venta=venta, fecha=fecha, monto=parte, nota_credito=nc, movimiento=mov)
    if cuenta is not None and not cuenta.admite_negativo:
        negativo = cuenta.primer_negativo(desde=fecha)
        if negativo:
            raise ErrorInmobiliario(f'Saldo insuficiente en {cuenta}: quedaría en {cuenta.simbolo} '
                                    f'{negativo[1]:,.2f} el {negativo[0]:%d/%m/%Y}.')
    venta.estado, venta.motivo_anulacion = 'ANULADA', f'Desistimiento: {motivo}'[:200]
    venta.fecha_anulacion, venta.penalidad = fecha, penalidad
    venta.save(update_fields=['estado', 'motivo_anulacion', 'fecha_anulacion', 'penalidad'])
    venta.unidades.update(estado='DISPONIBLE')
    if venta.oportunidad_id and venta.oportunidad.etapa in venta.oportunidad.ABIERTAS + ('GANADA',):
        _etapa(venta.oportunidad, 'PERDIDA', motivo=f'Desistimiento ({venta.numero}): {motivo}')
    marcar_pendiente(fecha.strftime('%Y%m'))
    return venta


# ---------------------------------------------------------------- costo de las unidades, terminación y entrega
def costo_proyecto(obra, desde=None, hasta=None, con_directos=True):
    """Costos del proyecto con el centro de costo de la obra: los gastos (clase 6, que se activan cada mes) y, con
    con_directos, lo cargado directo a inmuebles en construcción (terreno, licencias). Sin destinos, sin el reparto de
    gastos generales ni los asientos inmobiliarios."""
    from django.db.models import Q

    from contabilidad.models import AsientoLinea, CuentaDefecto
    if not obra.centro_costo_id:
        return D0
    cuentas = Q(cuenta__codigo__startswith='6')
    proceso = CuentaDefecto.objects.filter(clave='inmuebles_proceso').values_list('cuenta_id', flat=True).first()
    if con_directos and proceso:
        cuentas |= Q(cuenta_id=proceso)
    qs = AsientoLinea.objects.filter(cuentas, centro_costo_id=obra.centro_costo_id, es_destino=False).exclude(
        asiento__origen__in=ORIGENES_SIN_ACTIVAR)
    if desde:
        qs = qs.filter(asiento__fecha__gte=desde)
    if hasta:
        qs = qs.filter(asiento__fecha__lte=hasta)
    agg = qs.aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or D0) - (agg['h'] or D0)


def costo_activable(obra, hasta=None):
    """Costo acumulado del proyecto que se activa: hasta la terminación (lo posterior va a resultados)."""
    if obra.fecha_terminacion and (hasta is None or hasta > obra.fecha_terminacion):
        hasta = obra.fecha_terminacion
    return costo_proyecto(obra, hasta=hasta)


def pesos(obra):
    """{unidad_id: peso} de las unidades vendibles para repartir el costo (las áreas comunes no llevan costo propio:
    lo llevan las vendibles): área techada, precio de lista o mixto (si no hay áreas, el precio)."""
    unidades = [u for u in obra.unidades.all() if u.vendible]
    area = {u.pk: u.area_techada or D0 for u in unidades}
    valor = {u.pk: u.precio_lista or D0 for u in unidades}
    total_area, total_valor = sum(area.values(), D0), sum(valor.values(), D0)
    if obra.prorrateo == 'VALOR' or not total_area:
        return valor
    if obra.prorrateo == 'MIXTO' and total_valor:
        p = (obra.prorrateo_area_pct or D0) / 100
        return {k: p * area[k] / total_area + (1 - p) * valor[k] / total_valor for k in area}
    return area


def costo_unidades(obra, unidades, hasta=None, _pesos=None, _costo=None):
    p = _pesos if _pesos is not None else pesos(obra)
    total = sum(p.values(), D0)
    if not total:
        return D0
    costo = _costo if _costo is not None else costo_activable(obra, hasta=hasta)
    return r2(costo * sum((p.get(u.pk, D0) for u in unidades), D0) / total)


def _comprobantes(venta):
    from ventas.models import Venta
    ids = [i for i in venta.cuotas.values_list('cobros__comprobante_id', flat=True) if i]
    return Venta.objects.filter(pk__in=ids, estado='REGISTRADO')


def ingreso_de(venta):
    """Valor sin IGV (construcción + terreno o exonerado) de los comprobantes de anticipo vigentes de la venta, menos
    sus notas de crédito: el ingreso en la entrega o lo retenido en un desistimiento."""
    from ventas.models import Venta
    comps = _comprobantes(venta)
    agg = comps.aggregate(b=Sum('base_pen'), n=Sum('nograv_pen'))
    ncs = Venta.objects.filter(doc_referencia__in=comps, tipo_comprobante='07', estado='REGISTRADO').aggregate(
        b=Sum('base_pen'), n=Sum('nograv_pen'))
    return (agg['b'] or D0) + (agg['n'] or D0) - (ncs['b'] or D0) - (ncs['n'] or D0)


def arras_retenidas(venta):
    arras = (CobroInmueble.objects.filter(cuota__venta=venta, comprobante=None).aggregate(s=Sum('monto'))['s']
             or D0)
    devueltas = venta.devoluciones.filter(nota_credito=None).aggregate(s=Sum('monto'))['s'] or D0
    return arras - devueltas


@transaction.atomic
def terminar(obra, fecha=None):
    """Conformidad de obra: los inmuebles pasan a terminados y se deja de activar costos."""
    from contabilidad.automatico import marcar_pendiente
    if obra.tipo != 'INMOBILIARIA':
        raise ErrorInmobiliario('La obra no es un proyecto inmobiliario.')
    if obra.fecha_terminacion:
        raise ErrorInmobiliario(f'El proyecto ya se terminó el {obra.fecha_terminacion:%d/%m/%Y}.')
    fecha = fecha or timezone.localdate()
    _validar_periodo(fecha)
    obra.fecha_terminacion = fecha
    Obra.objects.filter(pk=obra.pk).update(fecha_terminacion=fecha)
    marcar_pendiente(fecha.strftime('%Y%m'))
    return obra


@transaction.atomic
def deshacer_terminacion(obra):
    from contabilidad.automatico import marcar_pendiente
    if not obra.fecha_terminacion:
        return obra
    _validar_periodo(obra.fecha_terminacion)
    periodo = obra.fecha_terminacion.strftime('%Y%m')
    obra.fecha_terminacion = None
    Obra.objects.filter(pk=obra.pk).update(fecha_terminacion=None)
    marcar_pendiente(periodo)
    return obra


@transaction.atomic
def entregar(venta, fecha=None):
    from contabilidad.automatico import marcar_pendiente
    fecha = fecha or timezone.localdate()
    if venta.estado != 'VENDIDA':
        raise ErrorInmobiliario('Solo se entregan ventas con la minuta firmada.' if venta.estado == 'SEPARADA' else
                                f'La venta está {venta.get_estado_display().lower()}.')
    if venta.saldo > 0:
        raise ErrorInmobiliario(f'Falta cobrar S/ {venta.saldo:,.2f}: la unidad se entrega pagada (incluido el '
                                f'desembolso del banco).')
    _validar_periodo(fecha)
    venta.estado, venta.fecha_entrega = 'ENTREGADA', fecha
    venta.ingreso = ingreso_de(venta)
    venta.costo = costo_unidades(venta.obra, list(venta.unidades.all()), hasta=fecha)
    venta.provision_garantia = r2(venta.ingreso * _parametro('INM_GARANTIA_PCT', fecha) / 100)
    venta.save(update_fields=['estado', 'fecha_entrega', 'ingreso', 'costo', 'provision_garantia'])
    venta.unidades.update(estado='ENTREGADA')
    marcar_pendiente(fecha.strftime('%Y%m'))
    return venta


def _cuenta_inmuebles(obra, fecha, cta):
    """Inmuebles en construcción hasta la terminación; después, terminados."""
    if obra.fecha_terminacion and fecha > obra.fecha_terminacion:
        return cta['inmuebles_terminados']
    return cta['inmuebles_proceso']


def asiento_inmobiliario(periodo, cta):
    """Costos del mes activados (hasta la terminación), traspaso a terminados, unidades entregadas (ingreso, costo y
    garantía) y desistimientos (penalidad retenida) del mes."""
    from contabilidad.centralizar import Borrador, _rango
    from contabilidad.models import Asiento
    obras = list(Obra.objects.filter(tipo='INMOBILIARIA').exclude(centro_costo=None).order_by('codigo'))
    if not obras:
        return None
    desde, hasta = _rango(periodo)
    a = Asiento(fecha=hasta, libro='05', origen='INMUEBLE',
                glosa=f'Proyectos inmobiliarios {periodo}: costos activados, entregas y desistimientos')
    b = Borrador(a)
    for obra in obras:
        fin = obra.fecha_terminacion
        if fin is None or fin >= desde:
            tope = min(hasta, fin) if fin else hasta
            costo = costo_proyecto(obra, desde, tope, con_directos=False)  # lo cargado directo a la 2341 ya está
            if costo:
                b.neto(cta['inmuebles_proceso'], cta['inmuebles_variacion'], costo, documento=obra.codigo,
                       glosa=f'Costos del mes activados: {obra.nombre}')
        if fin and desde <= fin <= hasta:
            entregado = sum((costo_unidades(obra, list(v.unidades.all()), hasta=v.fecha_entrega) for v in
                             obra.ventas_inmuebles.filter(estado='ENTREGADA', fecha_entrega__lte=fin)), D0)
            traspaso = costo_activable(obra) - entregado
            if traspaso:
                b.neto(cta['inmuebles_terminados'], cta['inmuebles_proceso'], traspaso, documento=obra.codigo,
                       glosa=f'Conformidad de obra {fin:%d/%m/%Y}: inmuebles terminados de {obra.nombre}')
        for v in (obra.ventas_inmuebles.filter(estado='ENTREGADA', fecha_entrega__range=[desde, hasta])
                  .select_related('cliente').prefetch_related('unidades')):
            ingreso = ingreso_de(v)
            costo_v = costo_unidades(obra, list(v.unidades.all()), hasta=v.fecha_entrega)
            garantia = r2(ingreso * _parametro('INM_GARANTIA_PCT', v.fecha_entrega) / 100)
            if (ingreso, costo_v, garantia) != (v.ingreso, v.costo, v.provision_garantia):
                VentaInmueble.objects.filter(pk=v.pk).update(ingreso=ingreso, costo=costo_v,
                                                             provision_garantia=garantia)
            glosa = f'Entrega {v.numero} {", ".join(u.codigo for u in v.unidades.all())} a {v.cliente.nombre}'
            b.add(cta['mov_ANTICIPO_INGRESO'], debe=ingreso, tercero=v.cliente, documento=v.numero, glosa=glosa)
            b.add(cta['inmuebles_venta'], haber=ingreso, documento=v.numero, centro_costo=obra.centro_costo,
                  glosa=glosa)
            b.add(cta['inmuebles_costo'], debe=costo_v, documento=v.numero, centro_costo=obra.centro_costo,
                  glosa=f'Costo: {glosa}')
            b.add(_cuenta_inmuebles(obra, v.fecha_entrega, cta), haber=costo_v, documento=v.numero,
                  glosa=f'Costo: {glosa}')
            if garantia:
                b.add(cta['inmuebles_garantia_gasto'], debe=garantia, documento=v.numero,
                      centro_costo=obra.centro_costo, glosa=f'Provisión de garantías: {glosa}')
                b.add(cta['inmuebles_garantia'], haber=garantia, documento=v.numero,
                      glosa=f'Provisión de garantías: {glosa}')
        for v in (obra.ventas_inmuebles.filter(estado='ANULADA', fecha_anulacion__range=[desde, hasta],
                                               penalidad__gt=0).select_related('cliente')):
            glosa = f'Penalidad por desistimiento {v.numero} {v.cliente.nombre}'
            retenido = ingreso_de(v)  # base de los anticipos que no se devolvieron (su IGV ya se declaró)
            if retenido:
                b.add(cta['mov_ANTICIPO_INGRESO'], debe=retenido, tercero=v.cliente, documento=v.numero, glosa=glosa)
                b.add(cta['inmuebles_penalidad'], haber=retenido, documento=v.numero, glosa=glosa)
            arras = arras_retenidas(v)
            if arras:
                b.add(cta['inmuebles_arras'], debe=arras, tercero=v.cliente, documento=v.numero,
                      glosa=f'Arras retenidas: {glosa}')
                b.add(cta['inmuebles_penalidad'], haber=arras, documento=v.numero, glosa=f'Arras retenidas: {glosa}')
    b.agregar_destinos()
    return b.grabar()


def al_volverse_inmobiliaria(obra):
    """Una obra que pasa a proyecto inmobiliario vuelve a centralizar sus meses con costos (para activarlos)."""
    from contabilidad.automatico import marcar_pendiente
    from contabilidad.models import AsientoLinea
    primera = (AsientoLinea.objects.filter(centro_costo_id=obra.centro_costo_id).order_by('asiento__fecha')
               .values_list('asiento__fecha', flat=True).first())
    if primera:
        marcar_pendiente(primera.strftime('%Y%m'))


# ---------------------------------------------------------------- tablero, reportes y alertas
def tablero(obra):
    """Unidades con su estado, costo asignado, precio sin IGV y margen; resumen por estado y costo por m²."""
    from core.models import Empresa
    igv = Empresa.actual().igv_tasa
    unidades = list(obra.unidades.all())
    p = pesos(obra)
    costo = costo_activable(obra)
    ventas = {}
    for v in obra.ventas_inmuebles.exclude(estado='ANULADA').prefetch_related('unidades').select_related('cliente'):
        grupo = list(v.unidades.all())
        lista = sum((u.precio_lista for u in grupo), D0)
        for u in grupo:
            parte = v.precio * (u.precio_lista / lista) if lista else v.precio / len(grupo)
            ventas[u.pk] = (v, parte)
    filas = []
    for u in unidades:
        v, precio = ventas.get(u.pk, (None, u.precio_lista if u.vendible else D0))
        if v is not None and v.exonerada_igv:
            u.valor = precio
        else:
            c, _, t = partir(precio, (v.terreno_pct if v else obra.terreno_pct), igv)
            u.valor = c + t  # sin IGV
        u.venta, u.precio = v, r2(precio)
        u.costo = costo_unidades(obra, [u], _pesos=p, _costo=costo)
        u.margen = u.valor - u.costo
        u.margen_pct = (u.margen * 100 / u.valor).quantize(Decimal('0.1')) if u.valor else None
        filas.append(u)
    vendibles = [u for u in filas if u.vendible]
    area = sum((u.area_techada for u in vendibles), D0)
    resumen = {}
    for estado, nombre in Unidad.ESTADOS:
        grupo = [u for u in vendibles if u.estado == estado]
        resumen[estado] = {'nombre': nombre, 'n': len(grupo), 'monto': sum((u.precio for u in grupo), D0)}
    vendidas = [u for u in vendibles if u.estado in ('SEPARADA', 'VENDIDA', 'ENTREGADA')]
    return {'unidades': filas, 'resumen': resumen, 'costo': costo, 'area': area,
            'costo_m2': r2(costo / area) if area else None,
            'avance_ventas': (Decimal(len(vendidas)) * 100 / len(vendibles)).quantize(Decimal('0.1')) if vendibles
            else None,
            'por_cobrar': sum((v.saldo for v in obra.ventas_inmuebles.exclude(estado='ANULADA')), D0),
            'margen': sum((u.margen for u in vendibles), D0)}


def inventario_unidades(obra, fecha=None):
    """Registro de inventario permanente valorizado por unidad: costo asignado y dónde está (en construcción,
    terminado o entregado con su fecha)."""
    fecha = fecha or timezone.localdate()
    p = pesos(obra)
    filas = []
    for u in obra.unidades.filter().order_by('piso', 'codigo'):
        if not u.vendible:
            continue
        venta = u.ventas.filter(estado='ENTREGADA', fecha_entrega__lte=fecha).first()
        hasta = venta.fecha_entrega if venta else fecha
        costo = costo_unidades(obra, [u], hasta=hasta, _pesos=p)
        if venta:
            situacion = f'Entregada el {venta.fecha_entrega:%d/%m/%Y} ({venta.numero})'
        elif obra.fecha_terminacion and fecha >= obra.fecha_terminacion:
            situacion = 'Terminada (inmuebles terminados)'
        else:
            situacion = 'En construcción'
        filas.append({'unidad': u, 'costo': costo, 'situacion': situacion, 'en_stock': venta is None})
    return filas


def anticipos_por_aplicar(obra=None):
    """Ventas aún no entregadas: lo cobrado como anticipo (122, sin IGV), las arras y el saldo."""
    qs = VentaInmueble.objects.filter(estado__in=('SEPARADA', 'VENDIDA')).select_related('obra', 'cliente')
    if obra is not None:
        qs = qs.filter(obra=obra)
    filas = []
    for v in qs:
        cobrado = v.cobrado
        if not cobrado:
            continue
        filas.append({'venta': v, 'cobrado': cobrado, 'anticipo': ingreso_de(v), 'arras': arras_retenidas(v),
                      'saldo': v.precio - cobrado})
    return filas


def cobranza_por_comprador(obra=None):
    hoy = timezone.localdate()
    qs = VentaInmueble.objects.exclude(estado='ANULADA').select_related('cliente', 'obra')
    if obra is not None:
        qs = qs.filter(obra=obra)
    grupos = {}
    for v in qs:
        g = grupos.setdefault(v.cliente_id, {'cliente': v.cliente, 'ventas': [], 'precio': D0, 'cobrado': D0,
                                            'vencido': D0})
        g['ventas'].append(v)
        g['precio'] += v.precio
        g['cobrado'] += v.cobrado
        g['vencido'] += sum((c.saldo for c in v.cuotas.filter(vence__lt=hoy)), D0)
    filas = sorted(grupos.values(), key=lambda g: -(g['precio'] - g['cobrado']))
    for g in filas:
        g['saldo'] = g['precio'] - g['cobrado']
    return filas


def flujo_proyecto(obra, meses=12):
    """Flujo de caja del proyecto por mes: lo cobrado a los compradores, lo pagado a proveedores con el centro de la
    obra (y los egresos con su centro) y, hacia adelante, las cuotas por cobrar."""
    from finanzas.models import Movimiento
    hoy = timezone.localdate()
    cobros = CobroInmueble.objects.filter(cuota__venta__obra=obra).exclude(movimiento=None)
    ingresos = defaultdict(lambda: D0)
    for c in cobros.select_related('movimiento'):
        ingresos[c.movimiento.fecha.strftime('%Y%m')] += c.monto
    for d in DevolucionInmueble.objects.filter(venta__obra=obra):
        ingresos[d.fecha.strftime('%Y%m')] -= d.monto
    egresos = defaultdict(lambda: D0)
    if obra.centro_costo_id:
        from django.db.models import Q
        for m in Movimiento.objects.filter(Q(compra__centro_costo=obra.centro_costo) |
                                           Q(centro_costo=obra.centro_costo), tipo='EGRESO'):
            egresos[m.fecha.strftime('%Y%m')] += m.monto
    proyectado = defaultdict(lambda: D0)
    for c in CuotaInmueble.objects.filter(venta__obra=obra).exclude(venta__estado__in=('ANULADA', 'ENTREGADA')):
        saldo = c.saldo
        if saldo > 0:
            proyectado[max(c.vence, hoy).strftime('%Y%m')] += saldo
    periodos = sorted(set(ingresos) | set(egresos) | set(proyectado))
    filas, acumulado = [], D0
    for p in periodos:
        neto = ingresos[p] - egresos[p]
        acumulado += neto
        filas.append({'periodo': p, 'ingresos': ingresos[p], 'egresos': egresos[p], 'neto': neto,
                      'acumulado': acumulado, 'por_cobrar': proyectado[p]})
    return filas


def registro_operaciones(desde, hasta):
    """Operaciones para el registro de la UIF: cobros individuales desde el umbral y compradores cuyos cobros del mes
    suman el umbral de operaciones múltiples (umbrales en dólares, al tipo de cambio del día)."""
    cobros = (CobroInmueble.objects.filter(fecha__range=[desde, hasta])
              .select_related('cuota__venta__cliente', 'cuota__venta__obra', 'movimiento').order_by('fecha'))
    individuales, por_mes = [], defaultdict(list)
    for c in cobros:
        tc = tipo_cambio(c.fecha)
        usd = r2(c.monto / tc)
        c.usd, c.tc = usd, tc
        if usd >= _parametro('UIF_RO_USD', c.fecha):
            individuales.append(c)
        por_mes[(c.cuota.venta.cliente_id, c.fecha.strftime('%Y%m'))].append(c)
    multiples = []
    for (_, periodo), lista in por_mes.items():
        total = sum((c.usd for c in lista), D0)
        if len(lista) > 1 and total >= _parametro('UIF_RO_MULTIPLE_USD', lista[0].fecha):
            multiples.append({'cliente': lista[0].cuota.venta.cliente, 'periodo': periodo, 'cobros': lista,
                              'usd': total})
    return {'individuales': individuales, 'multiples': multiples}


def unidades_al(fecha):
    """Unidades en propiedad de la empresa a una fecha (no entregadas): base para el impuesto predial (al 1 de enero)
    y su costo."""
    filas = []
    for obra in Obra.objects.filter(tipo='INMOBILIARIA').order_by('codigo'):
        p = pesos(obra)
        for u in obra.unidades.all():
            if not u.vendible or u.ventas.filter(estado='ENTREGADA', fecha_entrega__lte=fecha).exists():
                continue
            filas.append({'obra': obra, 'unidad': u, 'costo': costo_unidades(obra, [u], hasta=fecha, _pesos=p)})
    return filas


def cuotas_vencidas(obra=None, hasta=None):
    hasta = hasta or timezone.localdate()
    qs = (CuotaInmueble.objects.exclude(venta__estado='ANULADA').filter(vence__lt=hasta)
          .annotate(pagado=Coalesce(Sum('cobros__monto'), Value(D0), output_field=DecimalField()))
          .filter(pagado__lt=F('monto')).select_related('venta__cliente', 'venta__obra').order_by('vence'))
    return qs.filter(venta__obra=obra) if obra is not None else qs


def separaciones_por_vencer(dias=DIAS_AVISO):
    return (VentaInmueble.objects.filter(estado='SEPARADA', vence_separacion__isnull=False,
                                         vence_separacion__lte=timezone.localdate() + timedelta(days=dias))
            .select_related('cliente', 'obra').order_by('vence_separacion'))


def fichas_incompletas():
    """Ventas vigentes desde el umbral del registro de operaciones sin la ficha del comprador completa."""
    filas = []
    for v in VentaInmueble.objects.exclude(estado='ANULADA').select_related('cliente', 'obra'):
        fecha = v.fecha_separacion or date.today()
        if v.precio / tipo_cambio(fecha) >= _parametro('UIF_RO_USD', fecha) and not v.ficha_completa:
            filas.append(v)
    return filas
