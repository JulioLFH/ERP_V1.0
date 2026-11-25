"""IA del CRM con Groq (v1.117.10). La clave se guarda cifrada en CRM › Configuración.

- extraer(): de un correo o mensaje que entra saca teléfono, empresa, RUC y qué pide el cliente, para llenar la
  oportunidad nueva (registrar() en crm_web). Si la IA falla, la oportunidad se crea igual, sin esos datos.
- sugerir(): redacta una respuesta para el cliente con lo que escribió; el vendedor la revisa antes de enviarla
  (bandeja de mensajes y ficha de la oportunidad). No inventa precios ni plazos.

Usa la API compatible con OpenAI de Groq por HTTPS (sin librerías extra)."""
import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

URL = 'https://api.groq.com/openai/v1/chat/completions'
MODELO = 'openai/gpt-oss-120b'  # el mismo de la plataforma de requerimientos
ORIGENES = {'CORREO', 'MESSENGER', 'INSTAGRAM', 'WHATSAPP', 'TELEGRAM', 'CHAT_WEB'}  # llegan como texto libre


class ErrorIA(Exception):
    pass


def config():
    from .models import ConfigCRM
    return ConfigCRM.actual()


def disponible(cfg=None):
    cfg = cfg or config()
    return bool(cfg.ia_activa and cfg.ia_api_key)


def _chat(cfg, sistema, usuario, json_=False, max_tokens=1500, timeout=20):
    modelo = (cfg.ia_modelo or MODELO).strip()
    cuerpo = {'model': modelo, 'temperature': 0.2, 'max_tokens': max_tokens,
              'messages': [{'role': 'system', 'content': sistema}, {'role': 'user', 'content': usuario}]}
    if 'gpt-oss' in modelo:
        cuerpo['reasoning_effort'] = 'low'  # respuestas rápidas: el webhook no debe esperar mucho
    if json_:
        cuerpo['response_format'] = {'type': 'json_object'}
    pedido = urllib.request.Request(URL, data=json.dumps(cuerpo).encode('utf-8'), method='POST', headers={
        'Authorization': f'Bearer {cfg.ia_api_key}', 'Content-Type': 'application/json', 'User-Agent': 'CeibaERP-CRM'})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as r:
            datos = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ErrorIA('La clave de Groq no es válida (CRM › Configuración).') from exc
        if exc.code == 429:
            raise ErrorIA('Se alcanzó el límite de Groq; inténtelo en un momento.') from exc
        raise ErrorIA(f'Groq respondió con error {exc.code}.') from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ErrorIA('No se pudo conectar con Groq.') from exc
    try:
        return (datos['choices'][0]['message'].get('content') or '').strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ErrorIA('Groq devolvió una respuesta inesperada.') from exc


EXTRAER = (
    'Eres un asistente que lee el primer mensaje de un posible cliente de una empresa peruana y extrae sus datos de '
    'contacto. Responde SOLO un objeto JSON con estas claves: "telefono" (el número tal como lo escribió, con código '
    'de país si lo puso), "empresa" (razón social o nombre de la empresa del cliente, no la nuestra), "ruc" (11 '
    'dígitos) e "interes" (qué pide, en máximo 8 palabras, por ejemplo "cotización de ERP para 3 usuarios"). Usa "" '
    'en lo que no aparezca. No inventes datos ni los deduzcas del correo electrónico.')


def _limpiar(datos):
    telefono = re.sub(r'[^\d+ ()-]', '', str(datos.get('telefono') or ''))
    telefono = telefono.strip() if len(re.sub(r'\D', '', telefono)) >= 6 else ''
    ruc = re.sub(r'\D', '', str(datos.get('ruc') or ''))
    ruc = ruc if len(ruc) == 11 and ruc[:2] in ('10', '15', '17', '20') else ''
    return {'telefono': telefono[:40], 'empresa': ' '.join(str(datos.get('empresa') or '').split())[:150], 'ruc': ruc,
            'interes': ' '.join(str(datos.get('interes') or '').split())[:120]}


def extraer(texto, nombre='', email='', cfg=None):
    """{telefono, empresa, ruc, interes} del mensaje (cadenas vacías si no aparecen). Lanza ErrorIA."""
    cfg = cfg or config()
    contenido = _chat(cfg, EXTRAER, f'Remitente: {nombre} <{email}>\n\nMensaje:\n{(texto or "")[:4000]}',
                      json_=True, timeout=15)
    try:
        datos = json.loads(contenido)
    except ValueError as exc:
        raise ErrorIA('La IA devolvió una respuesta que no se pudo leer.') from exc
    return _limpiar(datos if isinstance(datos, dict) else {})


def datos_para_oportunidad(datos, origen):
    """Para registrar(): los datos que la IA encuentre en el mensaje, sin frenar la creación si falla."""
    if origen not in ORIGENES or not (datos.get('mensaje') or '').strip():
        return {}
    try:
        cfg = config()
        if not disponible(cfg):
            return {}
        return extraer(datos['mensaje'], datos.get('nombre', ''), datos.get('email', ''), cfg)
    except ErrorIA as exc:
        log.warning('CRM IA: no se leyeron los datos del mensaje: %s', exc)
    except Exception as exc:  # noqa: BLE001  (la oportunidad se crea igual)
        log.warning('CRM IA: error inesperado al leer el mensaje: %s', exc)
    return {}


SUGERIR = (
    'Eres asesor comercial de {empresa} (Perú). Redacta la respuesta al cliente en español, cordial, clara y breve '
    '(máximo 120 palabras), lista para enviar por {medio}. Responde a lo que pidió y propone el siguiente paso '
    '(una llamada, una reunión o los datos que faltan para cotizar). No inventes precios, plazos, descuentos ni '
    'características que no estén en la conversación. {firma} Devuelve solo el texto del mensaje, sin comillas ni '
    'explicaciones.')


def sugerir(historial, medio, cliente='', vendedor='', cfg=None):
    """Texto sugerido para responder. historial: [(entrante, texto)] del más antiguo al más nuevo. Lanza ErrorIA."""
    from core.models import Empresa
    cfg = cfg or config()
    empresa = Empresa.actual()
    nombre_empresa = getattr(empresa, 'nombre_comercial', '') or empresa.razon_social
    correo = medio == 'correo'
    firma = (f'Empieza con un saludo{f" a {cliente}" if cliente else ""} y termina con una despedida y la firma '
             f'«{vendedor or "Equipo comercial"} – {nombre_empresa}».' if correo else
             'Es un chat: sin firma ni asunto, con un saludo corto.')
    conversacion = '\n'.join(f'{"CLIENTE" if entrante else "NOSOTROS"}: {texto}' for entrante, texto in historial[-20:])
    texto = _chat(cfg, SUGERIR.format(empresa=nombre_empresa, medio=medio, firma=firma),
                  f'Cliente: {cliente or "(sin nombre)"}\n\nConversación:\n{conversacion[-6000:]}', timeout=30)
    if not texto:
        raise ErrorIA('La IA no devolvió una respuesta; inténtelo de nuevo.')
    return texto[:2000]


def probar(cfg):
    """Prueba la clave con una pregunta mínima. Lanza ErrorIA."""
    return _chat(cfg, 'Responde solo: OK', 'Prueba de conexión', max_tokens=200, timeout=20)
