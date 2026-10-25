"""Gestión de cobranza (FIN-03): cartera por cliente con antigüedad y DSO, gestiones y promesas de pago, recordatorios
por correo antes y después del vencimiento, bloqueo automático por mora y provisión de cobranza dudosa por pérdida
esperada (NIIF 9, enfoque simplificado con una matriz por tramos de antigüedad)."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

D0 = Decimal('0')
TRAMOS = [('Por vencer', None, 0, None), ('1-30', 1, 30, 'FIN_PROV_0_30_PCT'), ('31-60', 31, 60, 'FIN_PROV_31_60_PCT'),
          ('61-90', 61, 90, 'FIN_PROV_61_90_PCT'), ('91-180', 91, 180, 'FIN_PROV_91_180_PCT'),
          ('Más de 180', 181, None, 'FIN_PROV_181_PCT')]
RECORDATORIOS = (-3, 0, 7, 15, 30)  # días respecto al vencimiento


class ErrorCobranza(Exception):
    pass


def _r2(v):
    return Decimal(v).quantize(Decimal('0.01'))


def pendientes(tercero=None):
    """Comprobantes de venta con saldo (en soles), con sus días de mora."""
    from ventas.models import Venta
    hoy = timezone.localdate()
    qs = (Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
          .select_related('tercero'))
    if tercero is not None:
        qs = qs.filter(tercero=tercero)
    salida = []
    for v in qs:
        if v.saldo > 0:
            vence = v.fecha_vencimiento or v.fecha_emision
            v.dias_mora = max((hoy - vence).days, 0)
            salida.append(v)
    return salida


def tramo(dias):
    for i, (nombre, desde, hasta, _) in enumerate(TRAMOS):
        if desde is None and dias <= 0:
            return i
        if desde is not None and dias >= desde and (hasta is None or dias <= hasta):
            return i
    return 0


def dso(tercero=None, dias=90, hoy=None):
    """Días de cobro (DSO) = saldo por cobrar / ventas a crédito de los últimos «dias» × «dias»."""
    from django.db.models import Sum

    from ventas.models import Venta
    hoy = hoy or timezone.localdate()
    ventas = Venta.objects.filter(estado='REGISTRADO', fecha_emision__gt=hoy - timedelta(days=dias),
                                  fecha_emision__lte=hoy).exclude(tipo_comprobante='07')
    if tercero is not None:
        ventas = ventas.filter(tercero=tercero)
    vendido = ventas.aggregate(s=Sum('total'))['s'] or D0
    saldo = sum((v.saldo_pen for v in pendientes(tercero)), D0)
    return _r2(saldo * dias / vendido) if vendido else None


def cartera(gestor=None, vendedor=None):
    """Filas por cliente: saldo, vencido, antigüedad por tramo, mora máxima, DSO, gestor, última gestión y promesa."""
    from .models import GestionCobranza
    por_cliente = {}
    for v in pendientes():
        if vendedor and (v.vendedor or '') != vendedor:
            continue
        c = por_cliente.setdefault(v.tercero_id, {'tercero': v.tercero, 'saldo': D0, 'vencido': D0, 'mora': 0,
                                                  'tramos': [D0] * len(TRAMOS), 'docs': 0,
                                                  'vendedores': set()})
        c['saldo'] += v.saldo_pen
        c['docs'] += 1
        c['tramos'][tramo(v.dias_mora)] += v.saldo_pen
        if v.dias_mora > 0:
            c['vencido'] += v.saldo_pen
        c['mora'] = max(c['mora'], v.dias_mora)
        if v.vendedor:
            c['vendedores'].add(v.vendedor)
    ultimas = {}
    for g in GestionCobranza.objects.filter(tercero_id__in=por_cliente).order_by('tercero_id', '-fecha', '-id'):
        ultimas.setdefault(g.tercero_id, g)
    from django.db.models import Sum

    from ventas.models import Venta
    hoy = timezone.localdate()
    vendido = dict(Venta.objects.filter(estado='REGISTRADO', tercero_id__in=por_cliente,
                                        fecha_emision__gt=hoy - timedelta(days=90), fecha_emision__lte=hoy)
                   .exclude(tipo_comprobante='07').values('tercero_id').annotate(s=Sum('total'))
                   .values_list('tercero_id', 's'))
    filas = []
    for pk, c in por_cliente.items():
        if gestor is not None and c['tercero'].gestor_cobranza_id != getattr(gestor, 'pk', gestor):
            continue
        c['ultima'] = ultimas.get(pk)
        c['dso'] = _r2(c['saldo'] * 90 / vendido[pk]) if vendido.get(pk) else None
        filas.append(c)
    return sorted(filas, key=lambda c: (-c['vencido'], -c['saldo']))


def por_vendedor():
    """Saldo, vencido y antigüedad por vendedor (el del comprobante)."""
    filas = {}
    for v in pendientes():
        f = filas.setdefault(v.vendedor or 'Sin vendedor', {'saldo': D0, 'vencido': D0, 'tramos': [D0] * len(TRAMOS)})
        f['saldo'] += v.saldo_pen
        f['tramos'][tramo(v.dias_mora)] += v.saldo_pen
        if v.dias_mora > 0:
            f['vencido'] += v.saldo_pen
    return sorted(filas.items(), key=lambda kv: -kv[1]['vencido'])


# ---------------------------------------------------------------- gestiones y promesas
@transaction.atomic
def registrar_gestion(tercero, tipo, resultado, usuario, venta=None, promesa_fecha=None, promesa_monto=None):
    from .models import GestionCobranza
    if len((resultado or '').strip()) < 5:
        raise ErrorCobranza('Describa el resultado de la gestión (mínimo 5 caracteres).')
    if venta is not None and venta.tercero_id != tercero.pk:
        raise ErrorCobranza('El comprobante es de otro cliente.')
    if promesa_fecha and promesa_fecha < timezone.localdate():
        raise ErrorCobranza('La promesa de pago es para hoy o una fecha futura.')
    if promesa_monto is not None and promesa_monto <= 0:
        raise ErrorCobranza('El monto prometido debe ser mayor que cero.')
    return GestionCobranza.objects.create(
        tercero=tercero, venta=venta, tipo=tipo, resultado=resultado.strip()[:250], usuario=usuario,
        promesa_fecha=promesa_fecha, promesa_monto=promesa_monto,
        estado_promesa='PENDIENTE' if promesa_fecha else '')


def revisar_promesas(hoy=None):
    """Marca las promesas vencidas: cumplida si el cliente pagó desde la gestión al menos lo prometido (o su saldo
    quedó en cero); si no, incumplida (alerta). Devuelve las incumplidas nuevas."""
    from .models import GestionCobranza, Movimiento
    hoy = hoy or timezone.localdate()
    incumplidas = []
    for g in GestionCobranza.objects.filter(estado_promesa='PENDIENTE', promesa_fecha__lt=hoy).select_related('tercero'):
        pagado = sum((m.monto_doc_pen or m.monto for m in Movimiento.objects.filter(
            tercero=g.tercero, tipo='INGRESO', fecha__gte=g.fecha, fecha__lte=g.promesa_fecha)), D0)
        saldo = sum((v.saldo_pen for v in pendientes(g.tercero)), D0)
        cumplida = saldo <= 0 or (g.promesa_monto and pagado >= g.promesa_monto)
        g.estado_promesa = 'CUMPLIDA' if cumplida else 'INCUMPLIDA'
        g.save(update_fields=['estado_promesa'])
        if not cumplida:
            incumplidas.append(g)
    return incumplidas


# ---------------------------------------------------------------- bloqueo por mora
def actualizar_bloqueos(hoy=None):
    """Bloquea a los clientes con un comprobante vencido más días que el parámetro FIN_DIAS_BLOQUEO_MORA y desbloquea
    a los que ya pagaron. (bloqueados, desbloqueados)"""
    from core import parametros
    from core.models import Tercero
    dias = int(parametros.valor('FIN_DIAS_BLOQUEO_MORA', hoy))
    morosos = {v.tercero_id for v in pendientes() if dias and v.dias_mora > dias}
    bloqueados = Tercero.objects.filter(pk__in=morosos, bloqueado_mora=False).update(bloqueado_mora=True)
    desbloqueados = Tercero.objects.filter(bloqueado_mora=True).exclude(pk__in=morosos).update(bloqueado_mora=False)
    return bloqueados, desbloqueados


# ---------------------------------------------------------------- recordatorios
def recordatorios_de_hoy(hoy=None):
    """[(venta, días)] que tocan hoy según el calendario (−3, 0, +7, +15, +30 días del vencimiento) y no se enviaron."""
    from .models import RecordatorioCobranza
    hoy = hoy or timezone.localdate()
    enviados = set(RecordatorioCobranza.objects.filter(enviado=True).values_list('venta_id', 'dias'))
    salida = []
    for v in pendientes():
        vence = v.fecha_vencimiento or v.fecha_emision
        dias = (hoy - vence).days
        regla = max((d for d in RECORDATORIOS if d <= dias), default=None)
        if regla is None or (v.pk, regla) in enviados or dias - regla > 3:  # no se envía con atraso de días
            continue
        salida.append((v, regla))
    return salida


def texto_recordatorio(venta, dias):
    from core.models import Empresa
    empresa = Empresa.actual()
    vence = venta.fecha_vencimiento or venta.fecha_emision
    if dias < 0:
        inicio = f'le recordamos que el {vence:%d/%m/%Y} vence'
    elif dias == 0:
        inicio = 'le recordamos que hoy vence'
    else:
        inicio = f'a la fecha está vencido desde el {vence:%d/%m/%Y}'
    return (f'Estimado cliente {venta.tercero.nombre}:\n\n{inicio.capitalize()} el comprobante {venta} por '
            f'{venta.simbolo if hasattr(venta, "simbolo") else "S/"} {venta.saldo:,.2f} (saldo pendiente).\n'
            f'Si ya realizó el pago, por favor ignore este mensaje o envíenos la constancia.\n\n{empresa.razon_social}')


def enviar_recordatorios(hoy=None):
    """Envía por correo los recordatorios del día. (enviados, errores)"""
    from core.correo import ErrorCorreo, enviar_simple

    from .models import RecordatorioCobranza
    enviados = errores = 0
    for v, dias in recordatorios_de_hoy(hoy):
        correo = v.tercero.email
        try:
            if not correo:
                raise ErrorCorreo('El cliente no tiene correo registrado.')
            enviar_simple([correo], f'Recordatorio de pago {v}', texto_recordatorio(v, dias))
            ok, error = True, ''
            enviados += 1
        except ErrorCorreo as exc:
            ok, error = False, str(exc)[:250]
            errores += 1
        RecordatorioCobranza.objects.update_or_create(venta=v, dias=dias, canal='CORREO',
                                                      defaults={'enviado': ok, 'error': error})
    return enviados, errores


def enlace_whatsapp(venta, dias):
    """Enlace wa.me con el texto del recordatorio (lo envía el gestor desde su WhatsApp)."""
    from urllib.parse import quote
    numero = ''.join(ch for ch in (venta.tercero.telefono or '') if ch.isdigit())
    if not numero:
        return ''
    if len(numero) == 9:
        numero = '51' + numero
    return f'https://wa.me/{numero}?text={quote(texto_recordatorio(venta, dias))}'


# ---------------------------------------------------------------- provisión NIIF 9
def calcular_provision(hoy=None):
    """Pérdida esperada por tramo: saldo vencido × % del parámetro de su tramo. {'filas', 'total'}"""
    from core import parametros
    hoy = hoy or timezone.localdate()
    filas = []
    for i, (nombre, _, _, clave) in enumerate(TRAMOS):
        pct = parametros.valor(clave, hoy) if clave else D0
        saldo = sum((v.saldo_pen for v in pendientes() if tramo(v.dias_mora) == i), D0)
        filas.append({'tramo': nombre, 'saldo': _r2(saldo), 'pct': pct, 'provision': _r2(saldo * pct / 100)})
    return {'filas': filas, 'total': sum((f['provision'] for f in filas), D0)}


def provision_acumulada():
    """Saldo de la estimación de cobranza dudosa (cuenta 19, haber − debe)."""
    from django.db.models import Sum

    from contabilidad.models import AsientoLinea
    s = AsientoLinea.objects.filter(cuenta__codigo__startswith='19').aggregate(d=Sum('debe'), h=Sum('haber'))
    return (s['h'] or D0) - (s['d'] or D0)


@transaction.atomic
def registrar_provision(periodo, usuario):
    """Asiento del mes por la diferencia entre la provisión calculada y la acumulada: Debe 6841 / Haber 191 (o el
    extorno si baja). Se registra una vez por mes."""
    from contabilidad.models import Asiento, PeriodoContable
    from obras.servicios import _cuenta

    from .models import ProvisionCobranza
    from .proyeccion import _fin_de_mes
    if PeriodoContable.esta_cerrado(periodo):
        raise ErrorCobranza(f'El periodo {periodo[4:]}/{periodo[:4]} está cerrado.')
    if ProvisionCobranza.objects.filter(periodo=periodo, asiento__isnull=False).exists():
        raise ErrorCobranza('La provisión de este mes ya se registró.')
    from datetime import date
    fin = _fin_de_mes(date(int(periodo[:4]), int(periodo[4:]), 1))
    calc = calcular_provision(fin)
    anterior = provision_acumulada()
    diferencia = calc['total'] - anterior
    asiento = None
    if diferencia:
        gasto = _cuenta(['6841', '684'], 'Estimación de cuentas de cobranza dudosa')
        estimacion = _cuenta(['1911', '191'], 'Estimación de cuentas de cobranza dudosa – facturas')
        asiento = Asiento.objects.create(fecha=fin, libro='05', origen='MANUAL', creado_por=usuario,
                                         glosa=f'Pérdida esperada de cuentas por cobrar (NIIF 9) {periodo[4:]}/'
                                               f'{periodo[:4]}')
        for cuenta, debe in ((gasto, diferencia), (estimacion, -diferencia)):
            asiento.lineas.create(cuenta=cuenta, debe=max(debe, D0), haber=max(-debe, D0),
                                  glosa='Provisión por tramos de antigüedad')
    return ProvisionCobranza.objects.update_or_create(periodo=periodo, defaults={
        'calculada': calc['total'], 'anterior': anterior, 'asiento': asiento, 'creado_por': usuario,
        'detalle': [{**f, 'saldo': str(f['saldo']), 'pct': str(f['pct']), 'provision': str(f['provision'])}
                    for f in calc['filas']]})[0]
