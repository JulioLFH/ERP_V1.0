"""Puente con Ceiba GO (el portal del cliente).

Ceiba GO es lo que usa el cliente; el ERP es lo que usa el personal de Go. Ceiba GO llama a estas rutas con una clave de
API del ERP (`Authorization: Bearer <clave>`, Mi cuenta › Claves de API) de un usuario con el módulo Operador
logístico. El cliente se identifica por su RUC o DNI (`numero_doc` del tercero).

    GET  /api/ceibago/cliente/?documento=…        ficha: habilitado, almacén de recojo, crédito y deuda
    POST /api/ceibago/solicitud/                  el cliente pide un recojo → Solicitud de recojo (canal Ceiba GO)
    GET  /api/ceibago/solicitudes/?documento=…    sus solicitudes con las guías, el estado y la bitácora
    GET  /api/ceibago/comprobantes/?documento=…   sus facturas y boletas con el saldo
    POST /api/ceibago/<tipo>/                     la cola de Ceiba GO (venta, gre, cxc, almacen, costo, cobranza,
                                                  baja, cierre): se guarda en «Recibido de Ceiba GO»
"""
from datetime import date

from django.db.models import Prefetch

from core.api import _cuerpo, api, error, respuesta, s_comprobante
from core.models import Tercero

from . import servicios
from .models import BitacoraEstado, GuiaCliente, RegistroCeibaGO, SolicitudRecojo

MODULO = 'operador_logistico'
TIPOS_COLA = {t for t, _ in RegistroCeibaGO.TIPOS}
MAX_SOLICITUDES = 50


def _tercero(documento):
    documento = (documento or '').strip()
    if not documento:
        return None, error('Indique el documento (RUC o DNI) del cliente.', 400)
    t = Tercero.objects.filter(numero_doc=documento).order_by('-activo', 'pk').first()
    if t is None:
        return None, error(f'El cliente {documento} no está registrado en el ERP de Go.', 404, codigo='no_registrado')
    return t, None


def _deuda(tercero):
    from ventas.models import Venta
    qs = Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO', tercero=tercero).exclude(
        tipo_comprobante='07')
    return sum((v.saldo for v in qs if v.saldo > 0), 0)


def s_cliente(t):
    p = servicios.perfil(t)
    return {'documento': t.numero_doc, 'nombre': t.nombre, 'activo': t.activo,
            'habilitado': bool(p and p.habilitado),
            'servicio_logistico': bool(p and p.servicio_logistico),
            'con_contrato': bool(p and p.con_contrato),
            'almacen': (p.direccion_recojo if p else t.direccion) or '',
            'contacto': (p.contacto if p else '') or '',
            'credito': {'dias': t.dias_credito, 'limite': t.limite_credito, 'deuda': _deuda(t)}}


def s_bitacora(b):
    return {'fecha': b.fecha, 'de': b.de, 'a': b.a, 'detalle': b.detalle, 'tiempo_anterior': b.tiempo_anterior,
            'color': b.estado.color if b.estado_id else ''}


def s_guia(g):
    return {'numero': g.numero_completo, 'emisor': g.emisor_nombre or g.emisor_ruc,
            'destinatario': g.destinatario_nombre, 'destino': g.destino, 'llegada': g.llegada_direccion,
            'bultos': g.bultos, 'peso': g.peso,
            'estado': {'codigo': g.estado.codigo, 'nombre': g.estado.nombre, 'color': g.estado.color},
            'estado_desde': g.estado_desde, 'recogida_en': g.recogida_en, 'entregado_en': g.entregado_en,
            'receptor': g.receptor_nombre,
            'bitacora': [s_bitacora(b) for b in g.bitacora.all()]}


def s_solicitud(s):
    return {'numero': s.numero, 'estado': s.estado, 'estado_nombre': s.get_estado_display(),
            'canal': s.get_canal_display(), 'creado': s.creado, 'fecha_recojo': s.fecha_recojo,
            'almacen': s.almacen, 'bultos': s.bultos, 'contacto': s.contacto, 'mensaje': s.mensaje,
            'referencia': s.referencia_externa, 'atendida_en': s.atendida_en,
            'bitacora': [s_bitacora(b) for b in s.bitacora.all()],
            'guias': [s_guia(g) for g in s.guias.all()]}


@api(MODULO)
def cliente(request):
    t, err = _tercero(request.GET.get('documento'))
    return err or respuesta(s_cliente(t))


def _fecha(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        raise ValueError('La fecha de recojo debe ir como AAAA-MM-DD.')


def _bultos(valor):
    if valor in (None, ''):
        return None
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise ValueError('Los bultos deben ser un número entero.')
    if n < 1 or n > 100000:
        raise ValueError('Los bultos deben estar entre 1 y 100 000.')
    return n


@api(MODULO, metodos=('POST',), escribir='operador_logistico.planificar')
def solicitud(request):
    datos = _cuerpo(request)
    if not isinstance(datos, dict):
        return error('Envíe un objeto JSON.')
    t, err = _tercero(datos.get('documento'))
    if err:
        return err
    p = servicios.perfil(t)
    if not (p and p.habilitado):
        return error('El cliente no tiene contrato de servicio logístico: su pedido sigue el proceso regular '
                     '(cotización con el área comercial).', 422, codigo='no_habilitado')
    try:
        campos = {'fecha': _fecha(datos.get('fecha')), 'bultos': _bultos(datos.get('bultos')),
                  'almacen': str(datos.get('almacen') or '').strip()[:250],
                  'contacto': str(datos.get('contacto') or '').strip()[:120]}
    except ValueError as exc:
        return error(str(exc), 422)
    mensaje = str(datos.get('mensaje') or '').strip()
    remitente = str(datos.get('usuario') or '').strip()
    sol = servicios.crear_solicitud(t, canal='CEIBAGO', remitente=remitente, asunto='Pedido desde Ceiba GO',
                                    mensaje=mensaje, usuario=request.user, **campos)
    referencia = str(datos.get('referencia') or '').strip()[:40]
    cambios = []
    if campos['almacen'] and sol.almacen != campos['almacen']:
        sol.almacen = campos['almacen']
        cambios.append('almacen')
    if referencia and not sol.referencia_externa:
        sol.referencia_externa = referencia
        cambios.append('referencia_externa')
    if cambios:
        sol.save(update_fields=cambios)
    return respuesta(s_solicitud(_con_detalle(SolicitudRecojo.objects.filter(pk=sol.pk)).get()), 201)


def _con_detalle(qs):
    guias = GuiaCliente.objects.select_related('estado').prefetch_related(
        Prefetch('bitacora', queryset=BitacoraEstado.objects.select_related('estado')))
    return qs.prefetch_related(Prefetch('bitacora', queryset=BitacoraEstado.objects.select_related('estado')),
                               Prefetch('guias', queryset=guias))


@api(MODULO)
def solicitudes(request):
    t, err = _tercero(request.GET.get('documento'))
    if err:
        return err
    qs = SolicitudRecojo.objects.filter(cliente=t)
    numero = (request.GET.get('numero') or '').strip()
    if numero:
        qs = qs.filter(numero=numero)
    qs = _con_detalle(qs)[:MAX_SOLICITUDES]
    return respuesta({'cliente': t.numero_doc, 'resultados': [s_solicitud(s) for s in qs]})


@api(MODULO)
def comprobantes(request):
    from ventas.models import Venta
    t, err = _tercero(request.GET.get('documento'))
    if err:
        return err
    qs = Venta.objects.con_saldos().filter(tercero=t).exclude(tipo_comprobante='07').select_related('tercero') \
        .order_by('-fecha_emision', '-pk')[:100]
    return respuesta({'cliente': t.numero_doc, 'resultados': [s_comprobante(v) for v in qs]})


@api(MODULO, metodos=('POST',))
def cola(request, tipo):
    if tipo not in TIPOS_COLA:
        return error(f'Tipo desconocido: {tipo}. Use uno de: {", ".join(sorted(TIPOS_COLA))}.', 404)
    datos = _cuerpo(request)
    if not isinstance(datos, dict):
        return error('Envíe un objeto JSON.')
    r = RegistroCeibaGO.objects.create(tipo=tipo, pedido=str(datos.get('pedido') or '')[:40], payload=datos,
                                       usuario=request.user)
    return respuesta({'ok': True, 'id': r.pk, 'tipo': tipo}, 201)
