"""Facturación electrónica a través de un OSE/PSE (Nubefact, API JSON v1).

Nubefact firma el XML, lo envía a SUNAT y devuelve PDF, XML, CDR, hash y QR.
Credenciales: Maestros > Facturación electrónica (ruta y token que entrega Nubefact;
la cuenta DEMO de Nubefact permite probar sin validez tributaria).
"""
import json
import urllib.error
import urllib.request
from decimal import Decimal

from django.utils import timezone

from .models import Empresa, FacturacionConfig, r2

TIPO_NUBEFACT = {'01': 1, '03': 2, '07': 3, '08': 4}
TIPO_GUIA_NUBEFACT = {'09': 7, '31': 8}
TIPO_IGV = {'GRAVADA': 1, 'EXONERADA': 8, 'INAFECTA': 9, 'EXPORTACION': 16, 'GRATUITA': 6}


class ErrorFacturacion(Exception):
    pass


def _fecha(f):
    return f.strftime('%d-%m-%Y') if f else ''


def _num(v, dec=2):
    return float(round(Decimal(v or 0), dec))


def _post(payload):
    cfg = FacturacionConfig.actual()
    if not cfg.activa:
        raise ErrorFacturacion('La facturación electrónica no está configurada (Maestros > Facturación electrónica).')
    req = urllib.request.Request(
        cfg.ruta, data=json.dumps(payload).encode('utf-8'), method='POST',
        headers={'Authorization': f'Token token="{cfg.token}"', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode('utf-8'))
            raise ErrorFacturacion(f"{data.get('errors') or data} (código {data.get('codigo', exc.code)})")
        except (ValueError, AttributeError):
            raise ErrorFacturacion(f'Error HTTP {exc.code} del proveedor.')
    except urllib.error.URLError as exc:
        raise ErrorFacturacion(f'No se pudo conectar con el proveedor: {exc.reason}')


def _guardar_respuesta(doc, data):
    if data.get('aceptada_por_sunat'):
        doc.estado_sunat = 'ACEPTADO'
    elif data.get('sunat_responsecode') not in (None, '', '0') or data.get('sunat_soap_error'):
        doc.estado_sunat = 'RECHAZADO'
    else:
        doc.estado_sunat = 'PENDIENTE'
    doc.sunat_descripcion = ' '.join(str(x) for x in (data.get('sunat_description'), data.get('sunat_note'),
                                                      data.get('sunat_soap_error')) if x)
    doc.enlace_pdf = data.get('enlace_del_pdf') or doc.enlace_pdf
    doc.enlace_xml = data.get('enlace_del_xml') or doc.enlace_xml
    doc.enlace_cdr = data.get('enlace_del_cdr') or doc.enlace_cdr
    doc.codigo_hash = data.get('codigo_hash') or doc.codigo_hash
    doc.cadena_qr = data.get('cadena_para_codigo_qr') or doc.cadena_qr
    doc.fecha_envio = timezone.now()
    doc.save()
    return doc


def _registrar_error(doc, exc):
    doc.estado_sunat = 'ERROR'
    doc.sunat_descripcion = str(exc)
    doc.fecha_envio = timezone.now()
    doc.save(update_fields=['estado_sunat', 'sunat_descripcion', 'fecha_envio'])


# ---------------------------------------------------------------- comprobantes de venta
def payload_comprobante(venta):
    if venta.tipo_comprobante not in TIPO_NUBEFACT:
        raise ErrorFacturacion('Solo facturas, boletas y notas de crédito/débito son electrónicas.')
    empresa = Empresa.actual()
    cli = venta.tercero
    tipo_igv = TIPO_IGV.get(venta.tipo_operacion, 1)
    tasa = empresa.igv_tasa / 100 if venta.lleva_igv else Decimal('0')
    items = []
    for i in venta.items.select_related('producto'):
        igv = r2(i.subtotal * tasa)
        items.append({
            'unidad_de_medida': i.producto.unidad if i.producto else 'NIU',
            'codigo': i.producto.codigo if i.producto else '',
            'descripcion': i.descripcion,
            'cantidad': _num(i.cantidad),
            'valor_unitario': _num(i.precio_unitario, 4),
            'precio_unitario': _num(i.precio_unitario * (1 + tasa), 4),
            'descuento': '',
            'subtotal': _num(i.subtotal),
            'tipo_de_igv': tipo_igv,
            'igv': _num(igv),
            'total': _num(i.subtotal + igv),
            'anticipo_regularizacion': 'false',
        })
    ref = venta.doc_referencia
    payload = {
        'operacion': 'generar_comprobante',
        'tipo_de_comprobante': TIPO_NUBEFACT[venta.tipo_comprobante],
        'serie': venta.serie,
        'numero': int(venta.numero),
        'sunat_transaction': 2 if venta.tipo_operacion == 'EXPORTACION' else (30 if venta.detraccion_monto else 1),
        'cliente_tipo_de_documento': cli.tipo_doc if cli.tipo_doc != '0' else '-',
        'cliente_numero_de_documento': cli.numero_doc,
        'cliente_denominacion': cli.nombre,
        'cliente_direccion': cli.direccion,
        'cliente_email': cli.email,
        'fecha_de_emision': _fecha(venta.fecha_emision),
        'fecha_de_vencimiento': _fecha(venta.fecha_vencimiento),
        'moneda': 2 if venta.moneda == 'USD' else 1,
        'tipo_de_cambio': _num(venta.tipo_cambio, 3) if venta.moneda == 'USD' else '',
        'porcentaje_de_igv': _num(empresa.igv_tasa),
        'total_gravada': _num(venta.base_imponible) or '',
        'total_inafecta': _num(venta.no_gravado) if venta.tipo_operacion == 'INAFECTA' else '',
        'total_exonerada': _num(venta.no_gravado) if venta.tipo_operacion == 'EXONERADA' else '',
        'total_exportacion': _num(venta.no_gravado) if venta.tipo_operacion == 'EXPORTACION' else '',
        'total_igv': _num(venta.igv),
        'total_impuestos_bolsas': _num(venta.icbper) or '',
        'total': _num(venta.total),
        'detraccion': 'true' if venta.detraccion_monto else 'false',
        'observaciones': venta.glosa,
        'documento_que_se_modifica_tipo': TIPO_NUBEFACT.get(ref.tipo_comprobante, '') if ref else '',
        'documento_que_se_modifica_serie': ref.serie if ref else '',
        'documento_que_se_modifica_numero': int(ref.numero) if ref and ref.numero.isdigit() else '',
        'tipo_de_nota_de_credito': int(venta.motivo_nota) if venta.tipo_comprobante == '07' and venta.motivo_nota else '',
        'tipo_de_nota_de_debito': int(venta.motivo_nota) if venta.tipo_comprobante == '08' and venta.motivo_nota else '',
        'enviar_automaticamente_a_la_sunat': 'true',
        'enviar_automaticamente_al_cliente': 'true' if cli.email else 'false',
        'condiciones_de_pago': venta.get_forma_pago_display(),
        'items': items,
    }
    if venta.detraccion_monto:
        payload.update({'detraccion_tipo': 37, 'detraccion_total': _num(venta.detraccion_monto),
                        'detraccion_porcentaje': _num(venta.detraccion_pct), 'medio_de_pago_detraccion': 1})
    if venta.forma_pago == 'CREDITO' and venta.tipo_comprobante == '01':
        payload['medio_de_pago'] = 'credito'
        payload['venta_al_credito'] = [{'cuota': 1, 'fecha_de_pago': _fecha(venta.fecha_vencimiento),
                                        'importe': _num(venta.neto - venta.detraccion_monto)}]
    return payload


def enviar_comprobante(venta):
    try:
        return _guardar_respuesta(venta, _post(payload_comprobante(venta)))
    except ErrorFacturacion as exc:
        _registrar_error(venta, exc)
        raise


def consultar_comprobante(venta):
    data = _post({'operacion': 'consultar_comprobante', 'tipo_de_comprobante': TIPO_NUBEFACT[venta.tipo_comprobante],
                  'serie': venta.serie, 'numero': int(venta.numero)})
    return _guardar_respuesta(venta, data)


def anular_comprobante(venta, motivo='ANULACION DE LA OPERACION'):
    """Comunicación de baja (facturas) / resumen de anulación (boletas)."""
    data = _post({'operacion': 'generar_anulacion', 'tipo_de_comprobante': TIPO_NUBEFACT[venta.tipo_comprobante],
                  'serie': venta.serie, 'numero': int(venta.numero), 'motivo': motivo[:100]})
    venta.estado_sunat = 'BAJA'
    venta.sunat_descripcion = data.get('sunat_description') or 'Comunicación de baja enviada.'
    venta.save(update_fields=['estado_sunat', 'sunat_descripcion'])
    return venta


# ---------------------------------------------------------------- guías de remisión
def payload_guia(guia):
    dest = guia.destinatario
    items = [{'unidad_de_medida': i.unidad, 'codigo': i.producto.codigo if i.producto else '',
              'descripcion': i.descripcion, 'cantidad': _num(i.cantidad)} for i in guia.items.select_related('producto')]
    payload = {
        'operacion': 'generar_guia',
        'tipo_de_comprobante': TIPO_GUIA_NUBEFACT[guia.tipo],
        'serie': guia.serie,
        'numero': int(guia.numero),
        'cliente_tipo_de_documento': dest.tipo_doc,
        'cliente_numero_de_documento': dest.numero_doc,
        'cliente_denominacion': dest.nombre,
        'cliente_direccion': dest.direccion,
        'cliente_email': dest.email,
        'fecha_de_emision': _fecha(guia.fecha_emision),
        'observaciones': guia.observaciones,
        'motivo_de_traslado': guia.motivo_traslado,
        'peso_bruto_total': _num(guia.peso_bruto, 3),
        'peso_bruto_unidad_de_medida': guia.unidad_peso,
        'numero_de_bultos': guia.numero_bultos,
        'tipo_de_transporte': guia.modalidad,
        'fecha_de_inicio_de_traslado': _fecha(guia.fecha_traslado),
        'punto_de_partida_ubigeo': guia.partida_ubigeo,
        'punto_de_partida_direccion': guia.partida_direccion,
        'punto_de_partida_codigo_establecimiento_sunat': guia.almacen_origen.codigo_sunat if guia.almacen_origen else '0000',
        'punto_de_llegada_ubigeo': guia.llegada_ubigeo,
        'punto_de_llegada_direccion': guia.llegada_direccion,
        'punto_de_llegada_codigo_establecimiento_sunat': guia.almacen_destino.codigo_sunat if guia.almacen_destino else '0000',
        'enviar_automaticamente_al_cliente': 'false',
        'items': items,
    }
    if guia.transportista:
        payload.update({'transportista_documento_tipo': guia.transportista.tipo_doc,
                        'transportista_documento_numero': guia.transportista.numero_doc,
                        'transportista_denominacion': guia.transportista.nombre,
                        'mtc': guia.transportista.registro_mtc})
    if guia.vehiculo:
        payload['transportista_placa_numero'] = guia.vehiculo.placa
        payload['tuc_vehiculo_principal'] = guia.vehiculo.certificado
    if guia.conductor:
        c = guia.conductor
        payload.update({'conductor_documento_tipo': c.tipo_doc, 'conductor_documento_numero': c.numero_doc,
                        'conductor_nombre': c.nombres, 'conductor_apellidos': c.apellidos,
                        'conductor_numero_licencia': c.licencia})
    if guia.tipo == '31' and guia.remitente:
        r = guia.remitente
        payload.update({'remitente_documento_tipo': r.tipo_doc, 'remitente_documento_numero': r.numero_doc,
                        'remitente_denominacion': r.nombre})
    if guia.venta:
        payload['documento_relacionado'] = [{'tipo': guia.venta.tipo_comprobante, 'serie': guia.venta.serie,
                                             'numero': guia.venta.numero}]
    elif guia.doc_relacionado:
        tipo, _, numero = guia.doc_relacionado.partition(' ')
        serie, _, num = numero.partition('-')
        payload['documento_relacionado'] = [{'tipo': tipo, 'serie': serie, 'numero': num}]
    return payload


def enviar_guia(guia):
    try:
        return _guardar_respuesta(guia, _post(payload_guia(guia)))
    except ErrorFacturacion as exc:
        _registrar_error(guia, exc)
        raise


def consultar_guia(guia):
    data = _post({'operacion': 'consultar_guia', 'tipo_de_comprobante': TIPO_GUIA_NUBEFACT[guia.tipo],
                  'serie': guia.serie, 'numero': int(guia.numero)})
    return _guardar_respuesta(guia, data)
