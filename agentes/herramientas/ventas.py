"""Herramientas del agente de Ventas: clientes, deuda y crédito, ventas del periodo, pedidos, un documento y la
cobranza vencida. Respetan «Solo las ventas del vendedor» del perfil del usuario (core.politicas.filtrar_vendedor)."""
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from . import ErrorHerramienta, herramienta

D0 = Decimal('0')
PERMISO = 'ventas.agente'
LIMITE_FILAS = 30


def _r2(v):
    return (v or D0).quantize(Decimal('0.01'))


def _vendedor(qs, usuario):
    from core.politicas import filtrar_vendedor
    return filtrar_vendedor(qs, usuario)


def _cliente(cliente_id):
    from core.models import Tercero
    c = Tercero.objects.filter(pk=cliente_id, tipo__in=['CLIENTE', 'AMBOS']).first()
    if c is None:
        raise ErrorHerramienta(f'No hay un cliente con id {cliente_id}: búsquelo primero con buscar_cliente.')
    return c


def _deuda(usuario, cliente=None):
    """Comprobantes con saldo por cobrar (los del vendedor si el perfil lo limita)."""
    from ventas.models import Venta
    qs = Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
    if cliente is not None:
        qs = qs.filter(tercero=cliente)
    return [v for v in _vendedor(qs.select_related('tercero'), usuario) if v.saldo > 0]


def _fila_documento(v, hoy):
    return {'documento': f'{v.tipo_comprobante} {v.numero_completo}', 'emision': v.fecha_emision,
            'vencimiento': v.fecha_vencimiento, 'dias_vencido': max((hoy - v.fecha_vencimiento).days, 0)
            if v.fecha_vencimiento else 0, 'moneda': v.moneda, 'total': v.total, 'saldo': _r2(v.saldo),
            'saldo_soles': _r2(v.saldo_pen), 'vendedor': v.vendedor}


@herramienta('buscar_cliente', 'Busca clientes por nombre, RUC o DNI. Devuelve hasta 10 con su id (para las demás '
             'herramientas), días y límite de crédito y si está bloqueado por mora.',
             {'texto': {'type': 'string', 'description': 'Parte del nombre, RUC o DNI'}}, PERMISO)
def buscar_cliente(usuario, texto):
    from core.models import Tercero
    texto = (texto or '').strip()
    if len(texto) < 2:
        raise ErrorHerramienta('Escriba al menos 2 caracteres del nombre o del documento.')
    qs = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS']).filter(
        Q(nombre__icontains=texto) | Q(numero_doc__icontains=texto)).order_by('nombre')[:10]
    return [{'id': c.pk, 'documento': c.numero_doc, 'nombre': c.nombre, 'activo': c.activo,
             'dias_credito': c.dias_credito, 'limite_credito_soles': c.limite_credito,
             'bloqueado_por_mora': c.bloqueado_mora} for c in qs]


@herramienta('deuda_cliente', 'Deuda por cobrar de un cliente: comprobantes con saldo (vencidos y por vencer), letras '
             'pendientes, límite de crédito y crédito disponible, en soles.',
             {'cliente_id': {'type': 'integer', 'description': 'id del cliente (de buscar_cliente)'}}, PERMISO)
def deuda_cliente(usuario, cliente_id):
    from core.models import Empresa
    from finanzas.models import Letra
    cliente, hoy = _cliente(cliente_id), timezone.localdate()
    docs = sorted(_deuda(usuario, cliente), key=lambda v: v.fecha_vencimiento or v.fecha_emision)
    gracia = Empresa.actual().dias_gracia
    vencido = sum((v.saldo_pen for v in docs if v.fecha_vencimiento and v.fecha_vencimiento < hoy), D0)
    letras = [] if _solo_vendedor(usuario) else list(Letra.objects.filter(
        tercero=cliente, tipo='COBRAR', estado__in=Letra.ABIERTAS))  # las letras no tienen vendedor
    deuda = sum((v.saldo_pen for v in docs), D0) + sum((_r2(l.saldo * l.tc_efectivo) for l in letras), D0)
    return {'cliente': cliente.nombre, 'documento': cliente.numero_doc, 'bloqueado_por_mora': cliente.bloqueado_mora,
            'dias_credito': cliente.dias_credito, 'dias_de_gracia_empresa': gracia,
            'limite_credito_soles': cliente.limite_credito or 'sin límite',
            'deuda_total_soles': _r2(deuda), 'deuda_vencida_soles': _r2(vencido),
            'credito_disponible_soles': _r2(cliente.limite_credito - deuda) if cliente.limite_credito else 'sin límite',
            'solo_ventas_del_vendedor': _solo_vendedor(usuario),
            'comprobantes': [_fila_documento(v, hoy) for v in docs[:LIMITE_FILAS]],
            'comprobantes_no_listados': max(len(docs) - LIMITE_FILAS, 0),
            'letras': [{'numero': l.numero, 'vencimiento': l.fecha_vencimiento, 'moneda': l.moneda,
                        'saldo': _r2(l.saldo)} for l in letras[:LIMITE_FILAS]]}


AGRUPAR = ['cliente', 'vendedor', 'producto', 'tipo_comprobante', 'ninguno']


@herramienta('ventas_del_periodo', 'Ventas de un mes (comprobantes registrados, notas de crédito en negativo), en soles: '
             'valor de venta sin IGV y total con IGV, agrupadas (las 25 mayores).',
             {'periodo': {'type': 'string', 'description': 'Mes como AAAAMM, p. ej. 202610'},
              'agrupar_por': {'type': 'string', 'enum': AGRUPAR, 'description': 'Cómo agrupar'}}, PERMISO)
def ventas_del_periodo(usuario, periodo, agrupar_por):
    from core.comprobantes import normalizar_periodo
    from ventas.models import Venta, VentaItem
    periodo = normalizar_periodo(periodo)
    if not periodo:
        raise ErrorHerramienta('Periodo inválido: use AAAAMM (p. ej. 202610).')
    if agrupar_por not in AGRUPAR:
        raise ErrorHerramienta(f'agrupar_por debe ser uno de {AGRUPAR}.')
    qs = _vendedor(Venta.objects.de_gestion().filter(periodo=periodo, estado='REGISTRADO'), usuario)
    grupos = defaultdict(lambda: {'valor_venta_soles': D0, 'total_soles': D0, 'comprobantes': 0})
    total_valor = total = D0
    n = 0
    for v in qs.select_related('tercero'):
        valor = (v.base_pen + v.nograv_pen) * v.signo
        total_valor += valor
        total += v.total_pen * v.signo
        n += 1
        if agrupar_por in ('cliente', 'vendedor', 'tipo_comprobante'):
            clave = {'cliente': v.tercero.nombre, 'vendedor': v.vendedor or '(sin vendedor)',
                     'tipo_comprobante': v.get_tipo_comprobante_display()}[agrupar_por]
            g = grupos[clave]
            g['valor_venta_soles'] += valor
            g['total_soles'] += v.total_pen * v.signo
            g['comprobantes'] += 1
    if agrupar_por == 'producto':
        for desc, cod, sub, moneda, tc, tipo in VentaItem.objects.filter(documento__in=qs).values_list(
                'descripcion', 'producto__codigo', 'subtotal', 'documento__moneda', 'documento__tipo_cambio',
                'documento__tipo_comprobante'):
            g = grupos[f'{cod} {desc}' if cod else desc]
            g['valor_venta_soles'] += sub * (tc if moneda == 'USD' else 1) * (-1 if tipo == '07' else 1)
            g['comprobantes'] += 1
    filas = sorted(grupos.items(), key=lambda kv: kv[1]['valor_venta_soles'], reverse=True)[:25]
    return {'periodo': periodo, 'comprobantes': n, 'valor_venta_soles': _r2(total_valor), 'total_soles': _r2(total),
            'solo_ventas_del_vendedor': _solo_vendedor(usuario),
            # por producto no hay total con IGV (el IGV es del comprobante, no de la línea)
            'grupos': [{'grupo': k, **{c: (_r2(x) if isinstance(x, Decimal) else x) for c, x in g.items()
                                       if not (agrupar_por == 'producto' and c == 'total_soles')}}
                       for k, g in filas]}


def _solo_vendedor(usuario):
    from core.politicas import vendedor_de
    return bool(vendedor_de(usuario))


SITUACIONES = ['por_aprobar', 'por_facturar', 'abiertos']


@herramienta('pedidos_de_venta', 'Órdenes de pedido de venta: por aprobar (con el motivo y el nivel que falta), por '
             'facturar (aprobadas con saldo) o todas las abiertas. Hasta 30, las más recientes.',
             {'situacion': {'type': 'string', 'enum': SITUACIONES, 'description': 'Qué pedidos listar'}}, PERMISO)
def pedidos_de_venta(usuario, situacion):
    from core import aprobaciones
    from ventas.aprobacion_pedido import bloqueo
    from ventas.models import Cotizacion
    from ventas.seguimiento_pedido import situacion as situacion_pedido
    if situacion not in SITUACIONES:
        raise ErrorHerramienta(f'situacion debe ser una de {SITUACIONES}.')
    estados = {'por_aprobar': ['PENDIENTE'], 'por_facturar': ['APROBADO'], 'abiertos': ['PENDIENTE', 'APROBADO']}
    qs = _vendedor(Cotizacion.objects.filter(tipo='PED', estado__in=estados[situacion]), usuario)
    salida = []
    for c in qs.select_related('tercero').order_by('-fecha', '-id')[:LIMITE_FILAS]:
        fila = {'pedido': c.numero, 'fecha': c.fecha, 'cliente': c.tercero.nombre, 'moneda': c.moneda,
                'total': c.total, 'vendedor': c.vendedor, 'estado': c.get_estado_display(),
                'situacion': situacion_pedido(c), 'condicion_pago': c.condicion_pago}
        if c.estado == 'PENDIENTE':
            n = aprobaciones.pendiente(c.niveles, c.nivel_aprobado) if c.niveles else None
            fila.update(motivo_aprobacion=c.motivo_aprobacion or '', bloqueo=bloqueo(c),
                        nivel_pendiente=f'{n["nivel"]}: {n["nombre"]}' if n else '')
        salida.append(fila)
    return {'situacion': situacion, 'pedidos': salida}


@herramienta('documento_de_venta', 'Detalle de un comprobante (p. ej. F001-123, B001-45) o de una cotización / pedido '
             '(p. ej. PD01-00000012): cliente, importes, saldo, estado SUNAT, líneas y documentos relacionados.',
             {'numero': {'type': 'string', 'description': 'Serie y número'}}, PERMISO)
def documento_de_venta(usuario, numero):
    from ventas.models import Cotizacion, Venta
    texto = (numero or '').strip().upper().replace(' ', '')
    m = re.fullmatch(r'([A-Z0-9]{2,4})-0*(\d+)', texto)
    if not m:
        raise ErrorHerramienta('Indique serie y número, p. ej. F001-123.')
    serie, num = m.group(1), m.group(2)
    v = _vendedor(Venta.objects.con_saldos().filter(serie=serie, numero__regex=rf'^0*{num}$'), usuario).select_related(
        'tercero', 'cotizacion', 'doc_referencia').first()
    if v is not None:
        return {'tipo': v.get_tipo_comprobante_display(), 'documento': v.numero_completo, 'emision': v.fecha_emision,
                'vencimiento': v.fecha_vencimiento, 'cliente': v.tercero.nombre, 'documento_cliente': v.tercero.numero_doc,
                'forma_pago': v.get_forma_pago_display(), 'moneda': v.moneda, 'valor_venta': v.total - v.igv - v.icbper,
                'igv': v.igv, 'total': v.total, 'saldo': _r2(v.saldo), 'estado': v.get_estado_display(),
                'motivo_anulacion': v.motivo_anulacion, 'estado_sunat': v.get_estado_sunat_display(),
                'vendedor': v.vendedor, 'pedido_origen': v.cotizacion.numero if v.cotizacion_id else '',
                'modifica_a': str(v.doc_referencia) if v.doc_referencia_id else '',
                'notas_credito_debito': [str(n) for n in v.notas.filter(estado='REGISTRADO')],
                'lineas': [{'descripcion': i.descripcion, 'cantidad': i.cantidad, 'valor_unitario': i.precio_unitario,
                            'descuento_pct': i.descuento_pct, 'subtotal': i.subtotal} for i in v.items.all()[:50]]}
    c = _vendedor(Cotizacion.objects.filter(Q(numero=texto) | Q(numero__regex=rf'^{serie}-0*{num}$')),
                  usuario).select_related('tercero').first()
    if c is None:
        raise ErrorHerramienta(f'No se encontró {texto} (o su usuario no lo ve).')
    from ventas.seguimiento_pedido import avance, situacion
    filas = avance(c) if c.tipo == 'PED' else []
    return {'tipo': c.get_tipo_display(), 'numero': c.numero, 'fecha': c.fecha, 'cliente': c.tercero.nombre,
            'moneda': c.moneda, 'total': c.total, 'estado': c.get_estado_display(), 'situacion': situacion(c, filas),
            'vendedor': c.vendedor, 'condicion_pago': c.condicion_pago, 'motivo_aprobacion': c.motivo_aprobacion,
            'comprobantes': [str(v) for v in c.ventas.all()],
            'lineas': [{'descripcion': f['item'].descripcion, 'pedido': f['pedido'], 'facturado': f['facturado'],
                        'pendiente': f['pendiente'], 'valor_unitario': f['item'].precio_unitario} for f in filas]
            or [{'descripcion': i.descripcion, 'cantidad': i.cantidad, 'valor_unitario': i.precio_unitario}
                for i in c.items.all()[:50]]}


@herramienta('cobranza_vencida', 'Clientes con deuda vencida hace más de N días, de mayor a menor deuda vencida '
             '(hasta 20), en soles.',
             {'dias_minimos': {'type': 'integer', 'description': 'Días de atraso mínimos (0 = todo lo vencido)'}},
             PERMISO)
def cobranza_vencida(usuario, dias_minimos):
    hoy = timezone.localdate()
    limite = hoy - timedelta(days=max(int(dias_minimos or 0), 0))
    por_cliente = defaultdict(lambda: {'deuda_vencida_soles': D0, 'comprobantes': 0, 'max_dias_vencido': 0})
    for v in _deuda(usuario):
        if v.fecha_vencimiento and v.fecha_vencimiento < limite:
            g = por_cliente[(v.tercero_id, v.tercero.nombre)]
            g['deuda_vencida_soles'] += v.saldo_pen
            g['comprobantes'] += 1
            g['max_dias_vencido'] = max(g['max_dias_vencido'], (hoy - v.fecha_vencimiento).days)
    filas = sorted(por_cliente.items(), key=lambda kv: kv[1]['deuda_vencida_soles'], reverse=True)
    return {'dias_minimos': dias_minimos, 'clientes': len(filas),
            'total_vencido_soles': _r2(sum((g['deuda_vencida_soles'] for _, g in filas), D0)),
            'detalle': [{'cliente_id': k[0], 'cliente': k[1], **g, 'deuda_vencida_soles': _r2(g['deuda_vencida_soles'])}
                        for k, g in filas[:20]]}


HERRAMIENTAS = [buscar_cliente, deuda_cliente, ventas_del_periodo, pedidos_de_venta, documento_de_venta,
                cobranza_vencida]
