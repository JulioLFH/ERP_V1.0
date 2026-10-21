"""Aprobación de pedidos de venta (VEN-11) con el motor único (core/aprobaciones.py, documento PEDIDO): el pedido
bajo la tarifa (precio menor al de la lista o más descuento) o que excede el crédito del cliente se aprueba por
niveles según las reglas («Pedidos bajo la tarifa» / «Pedidos que exceden el crédito»). Sin reglas lo aprueba quien
tenga «Aprobar pedidos de venta». Un pedido aprobado autoriza la venta que se emite desde él."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from core import aprobaciones
from core.models import Empresa, r2

D0 = Decimal('0')


class ErrorAprobacionPedido(Exception):
    pass


def lineas_bajo_tarifa(items, cliente, lista=None, fecha=None):
    """[texto] de las líneas con precio menor al de la lista o con más descuento. items: [(producto, cantidad,
    precio, descuento %)]."""
    lista = lista or (cliente.lista_precios if cliente else None)
    salida = []
    for producto, cantidad, precio, descuento in items:
        if producto is None:
            continue
        base, desc_lista = producto.precio_para(lista, cantidad or Decimal('1'), fecha)
        if not base:
            continue
        neto = (precio or D0) * (1 - (descuento or D0) / 100)
        permitido = base * (1 - (desc_lista or D0) / 100)
        if r2(neto) < r2(permitido):
            salida.append(f'{producto.codigo} {producto.nombre}: {neto:,.4f} bajo la tarifa {permitido:,.4f}')
    return salida


def exceso_credito(cliente, nuevo_pen, excluir_venta=None):
    """Texto si el cliente supera su límite de crédito o tiene deuda vencida con este importe; '' si no."""
    from finanzas.models import Letra

    from .models import Venta
    if cliente is None:
        return ''
    empresa = Empresa.actual()
    deuda = [v for v in Venta.objects.con_saldos().cobrables().filter(tercero=cliente, estado='REGISTRADO')
             .exclude(tipo_comprobante='07').exclude(pk=getattr(excluir_venta, 'pk', None)) if v.saldo > 0]
    textos = []
    if empresa.bloquear_deuda_vencida:
        limite_fecha = timezone.localdate() - timedelta(days=empresa.dias_gracia)
        vencidos = [v for v in deuda if v.fecha_vencimiento and v.fecha_vencimiento < limite_fecha]
        if vencidos:
            textos.append(f'{len(vencidos)} comprobante(s) vencido(s) e impago(s)')
    if cliente.limite_credito:
        letras = sum((r2(l.saldo * l.tc_efectivo) for l in Letra.objects.filter(
            tercero=cliente, tipo='COBRAR', estado__in=Letra.ABIERTAS)), D0)
        actual = sum((v.saldo_pen for v in deuda), D0) + letras
        if actual + nuevo_pen > cliente.limite_credito:
            textos.append(f'deuda S/ {actual:,.2f} + S/ {nuevo_pen:,.2f} supera el límite S/ '
                          f'{cliente.limite_credito:,.2f}')
    return '; '.join(textos)


def motivos(cot):
    """(tipo para las reglas, [motivos]) del pedido."""
    items = [(i.producto, i.cantidad, i.precio_unitario, i.descuento_pct) for i in
             cot.items.select_related('producto')]
    from .views import condicion_a_forma_pago
    bajo = lineas_bajo_tarifa(items, cot.tercero, None, cot.fecha)
    credito = (exceso_credito(cot.tercero, _total_pen(cot))
               if condicion_a_forma_pago(cot.condicion_pago, cot.tercero)[0] == 'CREDITO' else '')
    lista = [f'Bajo la tarifa: {t}' for t in bajo] + ([f'Crédito: {credito}'] if credito else [])
    tipo = 'BAJO_TARIFA' if bajo else ('EXCESO_CREDITO' if credito else '')
    return tipo, lista


def _total_pen(cot):
    return r2(cot.total * (cot.tipo_cambio or 1)) if cot.moneda == 'USD' else cot.total


def _solicitante(cot):
    from core.segregacion import creador
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(pk=creador(cot)).first()


def aprobar(cot, user):
    """Aprueba el nivel que le toca (o el pedido entero sin reglas). Devuelve el mensaje."""
    from core.permisos import puede
    tipo, lista = motivos(cot)
    cot.motivo_aprobacion = ' · '.join(lista)[:300]
    if aprobaciones.hay_reglas('PEDIDO'):
        if not cot.niveles:
            try:
                cot.niveles, cot.nivel_aprobado = aprobaciones.calcular_niveles(
                    'PEDIDO', _solicitante(cot), _total_pen(cot), None, tipo), 0
            except aprobaciones.ErrorAprobacion as exc:
                raise ErrorAprobacionPedido(str(exc)) from exc
            cot.save(update_fields=['niveles', 'nivel_aprobado', 'motivo_aprobacion'])  # queda a la espera
        if cot.niveles:
            s = _solicitante(cot)
            if not aprobaciones.puede_aprobar(user, 'PEDIDO', cot.niveles, cot.nivel_aprobado, s.pk if s else None):
                n = aprobaciones.pendiente(cot.niveles, cot.nivel_aprobado)
                raise ErrorAprobacionPedido(f'El pedido lo aprueba ahora el nivel {n["nivel"]}: {n["nombre"]}.'
                                            if n else 'No le corresponde aprobar este pedido.')
            cot.nivel_aprobado = aprobaciones.pendiente(cot.niveles, cot.nivel_aprobado)['nivel']
            aprobaciones.registrar('PEDIDO', cot, user, 'APROBADA', cot.motivo_aprobacion, cot.nivel_aprobado)
            siguiente = aprobaciones.pendiente(cot.niveles, cot.nivel_aprobado)
            if siguiente is not None:
                aprobaciones.avanzar(cot.niveles, cot.nivel_aprobado)
                cot.save(update_fields=['niveles', 'nivel_aprobado', 'motivo_aprobacion'])
                return f'{cot.numero}: nivel {cot.nivel_aprobado} aprobado; pasa a {siguiente["nombre"]}.'
            cot.estado = 'APROBADO'
            cot.save(update_fields=['estado', 'niveles', 'nivel_aprobado', 'motivo_aprobacion'])
            return f'{cot.numero}: aprobado en todos sus niveles.'
    if lista and not puede(user, 'ventas.aprobar_pedidos'):
        raise ErrorAprobacionPedido(f'El pedido requiere aprobación ({cot.motivo_aprobacion}): pídala a quien tenga '
                                    f'«Aprobar pedidos de venta» o configure las reglas (documento «Pedidos de venta»).')
    cot.estado = 'APROBADO'
    cot.save(update_fields=['estado', 'motivo_aprobacion'])
    aprobaciones.registrar('PEDIDO', cot, user, 'APROBADA', cot.motivo_aprobacion)
    return f'{cot.numero}: Aprobado.'
