"""Facturación electrónica a través de un OSE/PSE (Nubefact, API JSON v1).

Implementado según el manual oficial de Nubefact (API JSON para comprobantes y para Guías de
Remisión Electrónica GRE v1.7) y sus archivos de ejemplo. Nubefact firma el XML, lo envía a SUNAT y
devuelve PDF, XML, CDR, hash y QR. Las guías se envían en dos pasos: 'generar_guia' y luego
'consultar_guia' para obtener la respuesta de SUNAT.
Credenciales: Ajustes > Facturación electrónica (RUTA y TOKEN de la opción "API (Integración)").
"""
import json
import re
import urllib.error
import urllib.request
from decimal import Decimal

from django.utils import timezone

from .models import Empresa, FacturacionConfig, r2

TIPO_NUBEFACT = {'01': 1, '03': 2, '07': 3, '08': 4}
TIPO_GUIA_NUBEFACT = {'09': 7, '31': 8}
TIPO_IGV = {'GRAVADA': 1, 'EXONERADA': 8, 'INAFECTA': 9, 'EXPORTACION': 16, 'GRATUITA': 6}
NOTA_DEBITO = {'01': 1, '02': 2, '03': 3, '04': 4, '05': 5}

# Catálogo 54 SUNAT: código de bien o servicio sujeto a detracción -> código interno de Nubefact
DETRACCION_TIPOS = [
    ('35', '037 Demás servicios gravados con el IGV'),
    ('20', '022 Otros servicios empresariales'),
    ('12', '012 Intermediación laboral y tercerización'),
    ('17', '019 Arrendamiento de bienes muebles'),
    ('18', '020 Mantenimiento y reparación de bienes muebles'),
    ('19', '021 Movimiento de carga'),
    ('22', '024 Comisión mercantil'),
    ('23', '025 Fabricación de bienes por encargo'),
    ('25', '027 Servicio de transporte de carga'),
    ('28', '030 Contratos de construcción'),
    ('11', '011 Bienes gravados con el IGV, o renuncia a la exoneración'),
    ('10', '010 Residuos, subproductos, desechos, recortes y desperdicios'),
    ('9', '009 Arena y piedra'),
    ('8', '008 Madera'),
    ('38', '040 Bien inmueble gravado con IGV'),
]
ERRORES_NUBEFACT = {
    10: 'Token incorrecto o eliminado. Revise el TOKEN en Ajustes > Facturación electrónica.',
    11: 'La RUTA no es correcta. Cópiela de nuevo desde la opción "API (Integración)" de Nubefact.',
    12: 'La solicitud no tiene el formato correcto (Content-Type).',
    20: 'El archivo enviado no cumple con el formato establecido.',
}


class ErrorFacturacion(Exception):
    def __init__(self, mensaje, codigo=None):
        super().__init__(mensaje)
        self.codigo = codigo


class ErrorValidacion(ErrorFacturacion):
    """Datos incompletos o incorrectos detectados antes de enviar (el documento no llegó al OSE)."""


def _fecha(f):
    return f.strftime('%d-%m-%Y') if f else ''


def _num(v, dec=2):
    return float(round(Decimal(v or 0), dec))


def _sin_ceros(numero):
    """Nubefact pide el correlativo sin ceros a la izquierda."""
    try:
        return int(str(numero))
    except ValueError:
        raise ErrorFacturacion(f'El número "{numero}" no es un correlativo numérico.')


def _placa(placa):
    return re.sub(r'[^A-Z0-9]', '', (placa or '').upper())


def _enviar(cfg, payload, autorizacion):
    req = urllib.request.Request(cfg.ruta.strip(), data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Authorization': autorizacion, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _post(payload):
    cfg = FacturacionConfig.actual()
    if not cfg.activa:
        # no llegó al OSE: el documento queda "No enviado" y se puede enviar al configurar Nubefact
        raise ErrorValidacion('La facturación electrónica no está configurada (Ajustes > Facturación electrónica).')
    token = cfg.token.strip()
    # El manual indica enviar el token tal cual; algunas cuentas antiguas usan el formato Token token="..."
    formatos = [token, f'Token token="{token}"']
    ultimo = None
    for autorizacion in formatos:
        try:
            return _enviar(cfg, payload, autorizacion)
        except urllib.error.HTTPError as exc:
            try:
                data = json.loads(exc.read().decode('utf-8'))
            except (ValueError, AttributeError):
                raise ErrorFacturacion(f'Error HTTP {exc.code} del proveedor.')
            codigo = data.get('codigo')
            ultimo = ErrorFacturacion(f"{ERRORES_NUBEFACT.get(codigo, '')} {data.get('errors') or ''} "
                                      f"(código Nubefact {codigo or exc.code})".strip(), codigo)
            if codigo != 10:
                raise ultimo
        except urllib.error.URLError as exc:
            raise ErrorFacturacion(f'No se pudo conectar con el proveedor: {exc.reason}')
    raise ultimo


def probar_conexion():
    """Consulta un comprobante inexistente: si responde algo distinto a error de RUTA/TOKEN, la conexión es válida."""
    try:
        _post({'operacion': 'consultar_comprobante', 'tipo_de_comprobante': 1, 'serie': 'F001', 'numero': 99999999})
    except ErrorFacturacion as exc:
        texto = str(exc)
        if any(f'código Nubefact {c})' in texto for c in (10, 11, 12)) or 'No se pudo conectar' in texto \
                or 'no está configurada' in texto:
            return False, texto
    return True, 'Conexión correcta: Nubefact aceptó la RUTA y el TOKEN.'


def _guardar_respuesta(doc, data):
    if data.get('aceptada_por_sunat'):
        doc.estado_sunat = 'ACEPTADO'
    elif data.get('sunat_responsecode') not in (None, '', '0') or data.get('sunat_soap_error'):
        doc.estado_sunat = 'RECHAZADO'
    else:
        doc.estado_sunat = 'PENDIENTE'
    if data.get('anulado'):
        doc.estado_sunat = 'BAJA'
    es_boleta = getattr(doc, 'tipo_comprobante', '') in ('03', '07', '08') and (doc.serie or '').startswith('B')
    pendiente = ('Boleta registrada en el OSE: SUNAT la recibe en el resumen diario (puede tardar hasta el día '
                 'siguiente). Use "Consultar estado" más tarde.' if es_boleta else
                 'Enviado. SUNAT aún no responde: use "Consultar estado" en unos minutos.')
    doc.sunat_descripcion = ' '.join(str(x) for x in (data.get('sunat_description'), data.get('sunat_note'),
                                                      data.get('sunat_soap_error')) if x) or (
        pendiente if doc.estado_sunat == 'PENDIENTE' else '')
    enlace = data.get('enlace') or ''
    doc.enlace_pdf = data.get('enlace_del_pdf') or (f'{enlace}.pdf' if enlace and data.get('aceptada_por_sunat')
                                                    else doc.enlace_pdf)
    doc.enlace_xml = data.get('enlace_del_xml') or doc.enlace_xml
    doc.enlace_cdr = data.get('enlace_del_cdr') or doc.enlace_cdr
    doc.codigo_hash = data.get('codigo_hash') or doc.codigo_hash
    doc.cadena_qr = data.get('cadena_para_codigo_qr') or doc.cadena_qr
    doc.fecha_envio = timezone.now()
    doc.save()
    return doc


def _registrar_error(doc, exc):
    """Estados coherentes: si no llegó al OSE queda 'No enviado' con lo que hay que corregir;
    'Error de envío' solo cuando el OSE o la conexión respondieron con error."""
    if isinstance(exc, ErrorValidacion):
        doc.estado_sunat = 'NO_ENVIADO'
        doc.sunat_descripcion = f'No enviado. Corrija antes de enviar: {exc}'
    else:
        doc.estado_sunat = 'ERROR'
        doc.sunat_descripcion = str(exc)
        doc.fecha_envio = timezone.now()
    doc.save(update_fields=['estado_sunat', 'sunat_descripcion', 'fecha_envio'])


def _ya_existe(exc):
    return getattr(exc, 'codigo', None) == 23


# ---------------------------------------------------------------- comprobantes de venta
def validar_comprobante(venta):
    """Problemas que SUNAT/Nubefact rechazarían, para avisar antes de enviar."""
    errores = []
    if venta.tipo_comprobante not in TIPO_NUBEFACT:
        errores.append('Solo facturas, boletas y notas de crédito/débito son electrónicas.')
    serie = venta.serie or ''
    letra = 'B' if (venta.tipo_comprobante == '03' or (venta.doc_referencia and
                                                         venta.doc_referencia.tipo_comprobante == '03')) else 'F'
    if not serie.startswith(letra) or len(serie) != 4:
        errores.append(f'La serie debe tener 4 caracteres y empezar con "{letra}" (actual: "{serie}").')
    cli = venta.tercero
    if cli.tipo_doc == '6':
        from .forms import error_ruc
        if error_ruc(cli.numero_doc):
            errores.append(f'Cliente {cli.nombre}: {error_ruc(cli.numero_doc)} Corríjalo en Contactos.')
    if venta.tipo_comprobante == '01' and not (cli.direccion or '').strip():
        errores.append('La factura requiere la dirección del cliente.')
    if venta.tipo_comprobante in ('07', '08') and not venta.doc_referencia:
        errores.append('La nota debe indicar el comprobante que modifica.')
    if venta.tipo_comprobante in ('07', '08') and not venta.motivo_nota:
        errores.append('La nota debe indicar el motivo SUNAT.')
    if venta.tipo_comprobante == '08' and venta.motivo_nota not in NOTA_DEBITO:
        errores.append('Motivo de nota de débito no válido (use 01 intereses, 02 aumento de valor, 03 penalidades).')
    if not venta.items.exists():
        errores.append('El comprobante no tiene detalle de ítems.')
    return errores


def payload_comprobante(venta):
    errores = validar_comprobante(venta)
    if errores:
        raise ErrorValidacion(' '.join(errores))
    empresa = Empresa.actual()
    cli = venta.tercero
    gratuita = venta.tipo_operacion == 'GRATUITA'
    items, total_gratuita = [], Decimal('0')
    for i in venta.items.select_related('producto'):
        afectacion = i.afectacion_en(venta)  # cada línea con su tipo de IGV (gravado, exonerado, inafecto)
        tipo_igv = TIPO_IGV.get(afectacion, 1)
        tasa = empresa.igv_tasa / 100 if afectacion == 'GRAVADA' else Decimal('0')
        igv = r2(i.subtotal * tasa)
        if gratuita:
            total_gratuita += i.subtotal
        items.append({
            'unidad_de_medida': i.producto.unidad if i.producto else 'ZZ',
            'codigo': i.producto.codigo if i.producto else '',
            'descripcion': i.descripcion[:250],
            'cantidad': _num(i.cantidad, 4),
            'valor_unitario': _num(i.precio_unitario, 4),
            'precio_unitario': _num(i.precio_unitario * (1 + tasa), 4),
            # descuento de la línea (sin IGV): subtotal = cantidad × valor unitario − descuento
            'descuento': _num(i.descuento) if i.descuento else '',
            'subtotal': _num(i.subtotal),
            'tipo_de_igv': tipo_igv,
            'igv': _num(igv),
            'total': _num(i.subtotal + igv),
            'anticipo_regularizacion': 'false',
            'anticipo_documento_serie': '',
            'anticipo_documento_numero': '',
        })
    ref = venta.doc_referencia
    inafecta = venta.inafecto + venta.exportacion
    if venta.tipo_operacion == 'EXPORTACION':
        transaccion = 2
    elif venta.detraccion_monto:
        transaccion = 30
    else:
        transaccion = 1
    payload = {
        'operacion': 'generar_comprobante',
        'tipo_de_comprobante': TIPO_NUBEFACT[venta.tipo_comprobante],
        'serie': venta.serie,
        'numero': _sin_ceros(venta.numero),
        'sunat_transaction': transaccion,
        'cliente_tipo_de_documento': cli.tipo_doc if cli.tipo_doc not in ('', None) else '-',
        'cliente_numero_de_documento': cli.numero_doc,
        'cliente_denominacion': cli.nombre[:100],
        'cliente_direccion': (cli.direccion or '')[:100],
        'cliente_email': cli.email,
        'cliente_email_1': '',
        'cliente_email_2': '',
        'fecha_de_emision': _fecha(venta.fecha_emision),
        'fecha_de_vencimiento': _fecha(venta.fecha_vencimiento) if venta.fecha_vencimiento and
        venta.fecha_vencimiento > venta.fecha_emision else '',
        'moneda': 2 if venta.moneda == 'USD' else 1,
        'tipo_de_cambio': _num(venta.tipo_cambio, 3) if venta.moneda == 'USD' else '',
        'porcentaje_de_igv': _num(empresa.igv_tasa),
        'descuento_global': '', 'total_descuento': '', 'total_anticipo': '',
        'total_gravada': _num(venta.base_imponible) if venta.base_imponible else '',
        'total_inafecta': _num(inafecta) if inafecta else '',
        'total_exonerada': _num(venta.exonerado) if venta.exonerado else '',
        'total_igv': _num(venta.igv) if venta.igv else '',
        'total_gratuita': _num(total_gratuita) if gratuita else '',
        'total_otros_cargos': '',
        'total_impuestos_bolsas': _num(venta.icbper) if venta.icbper else '',
        'total': _num(venta.total),
        'detraccion': 'true' if venta.detraccion_monto else 'false',
        'observaciones': (venta.glosa or '')[:1000],
        'documento_que_se_modifica_tipo': TIPO_NUBEFACT.get(ref.tipo_comprobante, '') if ref else '',
        'documento_que_se_modifica_serie': ref.serie if ref else '',
        'documento_que_se_modifica_numero': _sin_ceros(ref.numero) if ref else '',
        'tipo_de_nota_de_credito': int(venta.motivo_nota) if venta.tipo_comprobante == '07' else '',
        'tipo_de_nota_de_debito': NOTA_DEBITO.get(venta.motivo_nota, '') if venta.tipo_comprobante == '08' else '',
        'enviar_automaticamente_a_la_sunat': 'true',
        'enviar_automaticamente_al_cliente': 'true' if cli.email else 'false',
        'condiciones_de_pago': venta.get_forma_pago_display(),
        'medio_de_pago': '',
        'placa_vehiculo': '',
        'orden_compra_servicio': '',
        'formato_de_pdf': '',
        'items': items,
    }
    if venta.detraccion_monto:
        payload.update({'detraccion_tipo': venta.detraccion_codigo or '35',
                        'detraccion_total': _num(venta.detraccion_monto),
                        'detraccion_porcentaje': _num(venta.detraccion_pct, 5),
                        'medio_de_pago_detraccion': '1'})
    if venta.retencion_monto:
        payload.update({'retencion_tipo': '1' if venta.retencion_pct <= 3 else '2',
                        'retencion_base_imponible': _num(venta.total), 'total_retencion': _num(venta.retencion_monto)})
    if venta.percepcion_monto:
        payload.update({'percepcion_tipo': '1', 'percepcion_base_imponible': _num(venta.total),
                        'total_percepcion': _num(venta.percepcion_monto),
                        'total_incluido_percepcion': _num(venta.total + venta.percepcion_monto)})
    if venta.forma_pago == 'CREDITO' and venta.tipo_comprobante == '01':
        pendiente = venta.total - venta.detraccion_monto - venta.retencion_monto
        payload['medio_de_pago'] = 'credito'
        payload['venta_al_credito'] = [{'cuota': 1, 'fecha_de_pago': _fecha(venta.fecha_vencimiento),
                                        'importe': _num(pendiente)}]
    for g in venta.guias.filter(estado='EMITIDA'):
        payload.setdefault('guias', []).append({'guia_tipo': 1, 'guia_serie_numero': g.numero_completo})
    return payload


def enviar_comprobante(venta):
    if venta.es_saldo_inicial or venta.es_historico:
        raise ErrorFacturacion('Es un documento emitido con el sistema anterior: no se envía a SUNAT.')
    try:
        return _guardar_respuesta(venta, _post(payload_comprobante(venta)))
    except ErrorFacturacion as exc:
        if _ya_existe(exc):
            # ya estaba registrado en el OSE (por ejemplo, se reenvió): se trae su estado real
            return consultar_comprobante(venta)
        _registrar_error(venta, exc)
        raise


def consultar_comprobante(venta):
    data = _post({'operacion': 'consultar_comprobante', 'tipo_de_comprobante': TIPO_NUBEFACT[venta.tipo_comprobante],
                  'serie': venta.serie, 'numero': _sin_ceros(venta.numero)})
    return _guardar_respuesta(venta, data)


def anular_comprobante(venta, motivo='ANULACION DE LA OPERACION'):
    """Comunicación de baja (facturas) / resumen de anulación (boletas)."""
    data = _post({'operacion': 'generar_anulacion', 'tipo_de_comprobante': TIPO_NUBEFACT[venta.tipo_comprobante],
                  'serie': venta.serie, 'numero': _sin_ceros(venta.numero), 'motivo': motivo[:100],
                  'codigo_unico': ''})
    venta.estado_sunat = 'BAJA'
    venta.sunat_descripcion = data.get('sunat_description') or (
        f"Comunicación de baja enviada (ticket SUNAT {data.get('sunat_ticket_numero') or 'pendiente'}).")
    venta.save(update_fields=['estado_sunat', 'sunat_descripcion'])
    return venta


# ---------------------------------------------------------------- guías de remisión
def validar_guia(guia):
    errores = []
    letra = 'T' if guia.tipo == '09' else 'V'
    if not (guia.serie or '').startswith(letra) or len(guia.serie or '') != 4:
        errores.append(f'La serie de la guía debe tener 4 caracteres y empezar con "{letra}" (actual: "{guia.serie}").')
    if guia.peso_bruto <= 0:
        errores.append('El peso bruto debe ser mayor a cero.')
    for nombre, ubigeo in (('partida', guia.partida_ubigeo), ('llegada', guia.llegada_ubigeo)):
        if not (ubigeo or '').isdigit() or len(ubigeo) != 6:
            errores.append(f'El ubigeo de {nombre} debe tener 6 dígitos.')
    if not (guia.destinatario.direccion or '').strip() and guia.tipo == '09':
        errores.append('El destinatario debe tener dirección.')
    privado = guia.tipo == '31' or guia.modalidad == '02'
    if privado:
        if not guia.vehiculo:
            errores.append('Falta el vehículo (placa).')
        if not guia.conductor:
            errores.append('Falta el conductor.')
        elif not (9 <= len(guia.conductor.licencia or '') <= 10):
            errores.append('La licencia del conductor debe tener entre 9 y 10 caracteres.')
    if guia.vehiculo and not (6 <= len(_placa(guia.vehiculo.placa)) <= 8):
        errores.append('La placa debe tener entre 6 y 8 letras o números, sin guiones.')
    if guia.tipo == '09' and guia.modalidad == '01' and not guia.transportista:
        errores.append('En transporte público indique la empresa de transporte.')
    if guia.tipo == '31' and not guia.remitente:
        errores.append('La guía transportista requiere el remitente.')
    if not guia.items.exists():
        errores.append('La guía no tiene bienes.')
    return errores


def payload_guia(guia):
    errores = validar_guia(guia)
    if errores:
        raise ErrorValidacion(' '.join(errores))
    empresa = Empresa.actual()
    # GRE remitente: "cliente" es el destinatario. GRE transportista: "cliente" es el remitente.
    cliente = guia.remitente if guia.tipo == '31' else guia.destinatario
    items = [{'unidad_de_medida': i.unidad, 'codigo': i.producto.codigo if i.producto else '',
              'descripcion': i.descripcion[:250], 'cantidad': str(_num(i.cantidad, 4))}
             for i in guia.items.select_related('producto')]
    payload = {
        'operacion': 'generar_guia',
        'tipo_de_comprobante': TIPO_GUIA_NUBEFACT[guia.tipo],
        'serie': guia.serie,
        'numero': str(_sin_ceros(guia.numero)),
        'cliente_tipo_de_documento': cliente.tipo_doc,
        'cliente_numero_de_documento': cliente.numero_doc,
        'cliente_denominacion': cliente.nombre[:100],
        'cliente_direccion': (cliente.direccion or '-')[:100],
        'cliente_email': cliente.email,
        'cliente_email_1': '',
        'cliente_email_2': '',
        'fecha_de_emision': _fecha(guia.fecha_emision),
        'observaciones': (guia.observaciones or '')[:1000],
        'peso_bruto_total': str(_num(guia.peso_bruto, 3)),
        'peso_bruto_unidad_de_medida': guia.unidad_peso,
        'fecha_de_inicio_de_traslado': _fecha(guia.fecha_traslado),
        'punto_de_partida_ubigeo': guia.partida_ubigeo,
        'punto_de_partida_direccion': guia.partida_direccion[:150],
        'punto_de_llegada_ubigeo': guia.llegada_ubigeo,
        'punto_de_llegada_direccion': guia.llegada_direccion[:150],
        'enviar_automaticamente_al_cliente': 'false',
        'formato_de_pdf': '',
        'items': items,
    }
    if guia.tipo == '09':
        payload.update({'motivo_de_traslado': guia.motivo_traslado,
                        'numero_de_bultos': str(guia.numero_bultos),
                        'tipo_de_transporte': guia.modalidad})
        if guia.motivo_traslado == '13':
            payload['motivo_de_traslado_otros_descripcion'] = guia.descripcion_motivo[:70]
        if guia.modalidad == '01':
            t = guia.transportista
            payload.update({'fecha_de_entrega_al_transportista': _fecha(guia.fecha_traslado),
                            'transportista_documento_tipo': '6', 'transportista_documento_numero': t.numero_doc,
                            'transportista_denominacion': t.nombre[:100]})
            if t.registro_mtc:
                payload['mtc'] = re.sub(r'[^A-Z0-9]', '', t.registro_mtc.upper())
    else:
        d = guia.destinatario
        payload.update({'destinatario_documento_tipo': d.tipo_doc, 'destinatario_documento_numero': d.numero_doc,
                        'destinatario_denominacion': d.nombre[:100]})
        if empresa.registro_mtc:
            payload['mtc'] = re.sub(r'[^A-Z0-9]', '', empresa.registro_mtc.upper())
    if guia.motivo_traslado in ('04', '18') and guia.tipo == '09':
        payload['punto_de_partida_codigo_establecimiento_sunat'] = (
            guia.almacen_origen.codigo_sunat if guia.almacen_origen else '0000')
        payload['punto_de_llegada_codigo_establecimiento_sunat'] = (
            guia.almacen_destino.codigo_sunat if guia.almacen_destino else '0000')
    if guia.vehiculo:
        payload['transportista_placa_numero'] = _placa(guia.vehiculo.placa)
        if guia.tipo == '31' and guia.vehiculo.certificado:
            payload['tuc_vehiculo_principal'] = _placa(guia.vehiculo.certificado)
    if guia.conductor and (guia.tipo == '31' or guia.modalidad == '02'):
        c = guia.conductor
        payload.update({'conductor_documento_tipo': c.tipo_doc, 'conductor_documento_numero': c.numero_doc,
                        'conductor_nombre': c.nombres, 'conductor_apellidos': c.apellidos,
                        'conductor_numero_licencia': c.licencia.upper()})
    relacionados = []
    if guia.venta:
        relacionados.append({'tipo': guia.venta.tipo_comprobante, 'serie': guia.venta.serie,
                             'numero': str(_sin_ceros(guia.venta.numero))})
    elif guia.doc_relacionado:
        m = re.match(r'^\s*(\d{2})\s+([A-Z0-9]{4})-(\d+)\s*$', guia.doc_relacionado.upper())
        if m:
            relacionados.append({'tipo': m.group(1), 'serie': m.group(2), 'numero': str(int(m.group(3)))})
    if relacionados:
        payload['documento_relacionado'] = relacionados
    return payload


def enviar_guia(guia):
    """Paso 1 'generar_guia'; inmediatamente intenta el paso 2 'consultar_guia'."""
    try:
        _guardar_respuesta(guia, _post(payload_guia(guia)))
    except ErrorFacturacion as exc:
        if not _ya_existe(exc):
            _registrar_error(guia, exc)
            raise
    try:
        return consultar_guia(guia)
    except ErrorFacturacion:
        return guia  # queda Pendiente; se consulta luego


def consultar_guia(guia):
    data = _post({'operacion': 'consultar_guia', 'tipo_de_comprobante': TIPO_GUIA_NUBEFACT[guia.tipo],
                  'serie': guia.serie, 'numero': str(_sin_ceros(guia.numero))})
    return _guardar_respuesta(guia, data)
