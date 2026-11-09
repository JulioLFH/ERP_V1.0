"""Canales de mensajería del CRM (v1.107): WhatsApp, Facebook Messenger, Instagram, Telegram y el chat de la página
web entran a una bandeja común; los formularios de anuncios de Facebook / Instagram y de TikTok crean la oportunidad.

- recibir(): el primer mensaje de una persona crea la oportunidad (como el formulario web: asignación por regla,
  «Responder hoy», aviso de duplicados); los siguientes se suman a la misma conversación. Si su oportunidad ya se
  cerró, un mensaje nuevo abre otra.
- enviar(): el vendedor responde desde la bandeja por el mismo canal. WhatsApp, Messenger e Instagram solo permiten
  responder dentro de las 24 horas del último mensaje del cliente (regla de Meta; después se requiere una plantilla
  aprobada). El chat web y Telegram no tienen ese límite.
- Los webhooks son públicos y se protegen así: Meta con la firma X-Hub-Signature-256 (app secret), Telegram con la
  cabecera secreta que se registra al conectar el bot, TikTok y el chat con la clave del enlace."""
import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from erp import empresas as emp

log = logging.getLogger(__name__)
GRAPH = 'https://graph.facebook.com/v20.0'
TELEGRAM = 'https://api.telegram.org'
VENTANA_META = timedelta(hours=24)
CON_VENTANA = ('WHATSAPP', 'MESSENGER', 'INSTAGRAM')
MAX_TEXTO = 2000
MAX_POR_HORA_CHAT = 30  # mensajes de un visitante del chat por hora (frena el abuso del enlace público)


class ErrorCanal(Exception):
    pass


def _http(url, datos=None, token=None):
    """GET (datos None) o POST JSON. Devuelve el JSON de la respuesta. Se reemplaza en las pruebas."""
    import urllib.request
    cabeceras = {'Content-Type': 'application/json'}
    if token:
        cabeceras['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=json.dumps(datos).encode() if datos is not None else None,
                                 headers=cabeceras, method='POST' if datos is not None else 'GET')
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or b'{}')


def _digitos(texto):
    return re.sub(r'\D', '', texto or '')


def _config():
    from .models import ConfigCRM
    return ConfigCRM.actual()


def _rendicion():
    from finanzas.models_rendicion import ConfigRendicion
    return ConfigRendicion.actual()


# ---------------------------------------------------------------- entrada común
def _oportunidad_abierta_por_telefono(telefono):
    from .models import Oportunidad
    d = _digitos(telefono)[-9:]
    if len(d) < 9:
        return None
    for o in Oportunidad.objects.filter(etapa__in=Oportunidad.ABIERTAS, telefono__contains=d[-4:]).order_by('-pk'):
        if _digitos(o.telefono)[-9:] == d:
            return o
    return None


def recibir(canal, externo, texto, nombre='', telefono='', email='', empresa='', ip=''):
    """Un mensaje que llega por un canal: lo guarda en su conversación y, si es el primero (o su oportunidad ya se
    cerró), crea la oportunidad. Devuelve la conversación."""
    from .crm_web import registrar
    from .models import ConversacionCanal, MensajeCanal
    texto = (texto or '').strip()[:MAX_TEXTO] or '(mensaje sin texto)'
    ahora = timezone.now()
    with transaction.atomic():
        conv, _ = ConversacionCanal.objects.select_for_update().get_or_create(
            canal=canal, externo=str(externo)[:120],
            defaults={'nombre': (nombre or '')[:150], 'telefono': (telefono or '')[:40]})
        if nombre and not conv.nombre:
            conv.nombre = nombre[:150]
        if telefono and not conv.telefono:
            conv.telefono = telefono[:40]
        o = conv.oportunidad
        if o is None or not o.abierta:
            o = _oportunidad_abierta_por_telefono(conv.telefono) if conv.telefono else None
            if o is None:
                etiqueta = dict(ConversacionCanal.CANALES)[canal]
                o = registrar({'nombre': conv.nombre or f'Contacto de {etiqueta}', 'empresa': empresa,
                               'telefono': conv.telefono, 'email': email, 'mensaje': texto[:1500]}, ip, origen=canal)
            conv.oportunidad = o
        else:
            o.save(update_fields=['actualizado'])
        conv.ultimo = conv.ultimo_entrante = ahora
        conv.sin_leer += 1
        conv.save()
        MensajeCanal.objects.create(conversacion=conv, entrante=True, texto=texto, fecha=ahora)
    return conv


def registrar_formulario(origen, campos, ip=''):
    """Formulario de anuncio (Facebook / Instagram o TikTok): {campo: valor} -> oportunidad."""
    from .crm_web import registrar

    import unicodedata

    def clave(k):  # «Teléfono» -> telefono, «phone_number» -> phonenumber
        return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', str(k).lower()).encode('ascii', 'ignore').decode())
    normal = {clave(k): str(v).strip() for k, v in campos.items() if str(v).strip()}

    def buscar(exactas, contiene=()):
        """Primero el nombre exacto del campo; luego uno que lo contenga (p. ej. phone_number, correo_electronico)."""
        for c in exactas:
            if normal.get(c):
                return normal[c]
        return next((v for k, v in normal.items() if any(c in k for c in contiene)), '')
    nombre = buscar(('fullname', 'nombrecompleto', 'name', 'nombre', 'nombres', 'nombreyapellido',
                     'nombresyapellidos', 'nombreyapellidos'))
    if not nombre:
        nombre = ' '.join(x for x in (buscar(('firstname', 'nombre')), buscar(('lastname', 'apellido', 'apellidos')))
                          if x)
    telefono = buscar(('phone', 'phonenumber', 'telefono', 'celular'), ('phone', 'telefono', 'celular', 'movil',
                                                                         'whatsapp'))
    email = buscar(('email', 'correo'), ('email', 'correo'))
    empresa = buscar(('company', 'companyname', 'empresa', 'razonsocial'), ('company', 'empresa'))
    if not (nombre or telefono or email):
        raise ErrorCanal('El formulario no trae nombre, teléfono ni correo.')
    detalle = '\n'.join(f'{k}: {v}' for k, v in campos.items() if str(v).strip())
    return registrar({'nombre': (nombre or email or telefono)[:120], 'empresa': empresa[:150],
                      'telefono': telefono[:40], 'email': email[:254] if '@' in email else '',
                      'mensaje': detalle[:1500]}, ip, origen=origen)


# ---------------------------------------------------------------- respuesta del vendedor
def puede_responder(conv):
    """(sí/no, motivo)."""
    if conv.canal in CON_VENTANA and (conv.ultimo_entrante is None or
                                      timezone.now() - conv.ultimo_entrante > VENTANA_META):
        return False, ('Pasaron más de 24 horas desde el último mensaje del cliente: Meta solo permite escribirle con '
                       'una plantilla aprobada. Llámelo o escríbale desde el botón de WhatsApp de la oportunidad.')
    return True, ''


def _enviar_por_canal(conv, texto):
    cfg = _config()
    if conv.canal == 'WHATSAPP':
        r = _rendicion()
        if not (r.whatsapp_phone_id and r.whatsapp_token):
            raise ErrorCanal('Falta la conexión de WhatsApp (Rendiciones › Integraciones).')
        _http(f'{GRAPH}/{r.whatsapp_phone_id}/messages', {'messaging_product': 'whatsapp', 'to': conv.externo,
                                                         'type': 'text', 'text': {'body': texto}}, r.whatsapp_token)
    elif conv.canal in ('MESSENGER', 'INSTAGRAM'):
        if not cfg.meta_token:
            raise ErrorCanal('Falta el token de la página de Facebook (CRM › Configuración).')
        _http(f'{GRAPH}/me/messages', {'recipient': {'id': conv.externo}, 'messaging_type': 'RESPONSE',
                                       'message': {'text': texto}}, cfg.meta_token)
    elif conv.canal == 'TELEGRAM':
        if not cfg.telegram_token:
            raise ErrorCanal('Falta el token del bot de Telegram (CRM › Configuración).')
        _http(f'{TELEGRAM}/bot{cfg.telegram_token}/sendMessage', {'chat_id': conv.externo, 'text': texto})
    # CHAT_WEB: el visitante lo recibe al consultar la conversación (no hay nada que enviar)


def enviar(conv, texto, usuario=None):
    """Responde por el canal de la conversación. Guarda el mensaje (también si falla, con el error)."""
    from .models import MensajeCanal
    texto = (texto or '').strip()[:MAX_TEXTO]
    if not texto:
        raise ErrorCanal('Escriba el mensaje.')
    ok, motivo = puede_responder(conv)
    if not ok:
        raise ErrorCanal(motivo)
    m = MensajeCanal(conversacion=conv, entrante=False, texto=texto, usuario=usuario, estado='ENVIADO')
    try:
        _enviar_por_canal(conv, texto)
    except ErrorCanal as exc:
        m.estado, m.error = 'ERROR', str(exc)[:250]
    except Exception as exc:  # noqa: BLE001  (red, token vencido, número inválido…)
        log.info('CRM: no se pudo enviar por %s a %s: %s', conv.canal, conv.externo, exc)
        m.estado, m.error = 'ERROR', f'El canal rechazó el envío: {exc}'[:250]
    m.save()
    conv.ultimo = m.fecha
    conv.save(update_fields=['ultimo'])
    if conv.oportunidad_id:
        conv.oportunidad.save(update_fields=['actualizado'])
    if m.estado == 'ERROR':
        raise ErrorCanal(m.error)
    return m


# ---------------------------------------------------------------- WhatsApp (lo llama el webhook de Rendiciones)
def whatsapp_entrante(mensaje, contactos=()):
    """Mensaje de WhatsApp de un número que no es de un usuario del sistema -> bandeja del CRM."""
    telefono = mensaje.get('from', '')
    if not telefono:
        return None
    tipo = mensaje.get('type')
    if tipo == 'text':
        texto = mensaje.get('text', {}).get('body', '')
    elif tipo in ('image', 'document', 'audio', 'video', 'sticker'):
        nombres = {'image': 'una imagen', 'document': 'un documento', 'audio': 'un audio', 'video': 'un video',
                   'sticker': 'un sticker'}
        texto = f'[Envió {nombres[tipo]}: véalo en el WhatsApp de la empresa] {mensaje.get(tipo, {}).get("caption", "")}'
    elif tipo == 'interactive':
        i = mensaje.get('interactive', {})
        texto = (i.get('button_reply') or i.get('list_reply') or {}).get('title', '')
    elif tipo == 'button':
        texto = mensaje.get('button', {}).get('text', '')
    else:
        texto = f'[Mensaje de tipo {tipo}]'
    nombre = next((c.get('profile', {}).get('name', '') for c in contactos if c.get('wa_id') == telefono), '')
    return recibir('WHATSAPP', telefono, texto, nombre=nombre, telefono=f'+{telefono}')


# ---------------------------------------------------------------- Facebook Messenger, Instagram y Lead Ads
def _firma_meta_valida(request, cfg):
    secreto = cfg.meta_app_secret or _rendicion().whatsapp_app_secret
    firma = request.META.get('HTTP_X_HUB_SIGNATURE_256', '')
    esperado = 'sha256=' + hmac.new(secreto.encode(), request.body, hashlib.sha256).hexdigest()
    return bool(secreto) and hmac.compare_digest(firma, esperado)


def _nombre_meta(cfg, id_externo, instagram):
    try:
        datos = _http(f'{GRAPH}/{id_externo}?fields={"name,username" if instagram else "name"}', token=cfg.meta_token)
        return datos.get('name') or datos.get('username') or ''
    except Exception:  # noqa: BLE001  (sin permiso para leer el perfil: queda sin nombre)
        return ''


def _lead_meta(cfg, leadgen_id):
    datos = _http(f'{GRAPH}/{leadgen_id}?fields=field_data,ad_name,form_id', token=cfg.meta_token)
    campos = {f.get('name', ''): ', '.join(f.get('values') or []) for f in datos.get('field_data', [])}
    if datos.get('ad_name'):
        campos['anuncio'] = datos['ad_name']
    return registrar_formulario('META_ADS', campos)


def _activar(alias):
    if alias not in emp.empresas():
        raise Http404
    return emp.activar(alias)


@csrf_exempt
def meta(request, alias):
    """Webhook de la app de Meta: Messenger (object=page), Instagram (object=instagram) y Lead Ads (leadgen)."""
    token = _activar(alias)
    try:
        cfg = _config()
        if not (cfg.meta_activo or cfg.meta_leads):
            return HttpResponseForbidden('Facebook / Instagram no está activado en el CRM.')
        if request.method == 'GET':
            if request.GET.get('hub.mode') == 'subscribe' and \
                    hmac.compare_digest(request.GET.get('hub.verify_token', ''), cfg.meta_verify_token):
                return HttpResponse(request.GET.get('hub.challenge', ''))
            return HttpResponseForbidden('Token de verificación incorrecto.')
        if not _firma_meta_valida(request, cfg):
            return HttpResponseForbidden('Firma inválida.')
        try:
            cuerpo = json.loads(request.body or b'{}')
        except ValueError:
            return HttpResponse(status=400)
        instagram = cuerpo.get('object') == 'instagram'
        for entrada in cuerpo.get('entry', []):
            for m in entrada.get('messaging', []) if cfg.meta_activo else []:
                mensaje = m.get('message') or {}
                remitente = (m.get('sender') or {}).get('id')
                if not remitente or not mensaje or mensaje.get('is_echo') or remitente == entrada.get('id'):
                    continue  # eco de lo que envió la página o un evento sin mensaje (leído, entregado)
                texto = mensaje.get('text') or ('[Envió un adjunto: véalo en la bandeja de Meta]'
                                                if mensaje.get('attachments') else '')
                try:
                    canal = 'INSTAGRAM' if instagram else 'MESSENGER'
                    from .models import ConversacionCanal
                    nuevo = not ConversacionCanal.objects.filter(canal=canal, externo=remitente).exists()
                    recibir(canal, remitente, texto, nombre=_nombre_meta(cfg, remitente, instagram) if nuevo else '')
                except Exception as exc:  # noqa: BLE001  (un mensaje con error no frena los demás)
                    log.warning('CRM Meta: mensaje no procesado: %s', exc)
            for cambio in entrada.get('changes', []) if cfg.meta_leads else []:
                if cambio.get('field') == 'leadgen' and cambio.get('value', {}).get('leadgen_id'):
                    try:
                        _lead_meta(cfg, cambio['value']['leadgen_id'])
                    except Exception as exc:  # noqa: BLE001
                        log.warning('CRM Meta: formulario no procesado: %s', exc)
        return HttpResponse('ok')
    finally:
        emp.restaurar(token)


# ---------------------------------------------------------------- Telegram
@csrf_exempt
def telegram(request, alias):
    """Webhook del bot de Telegram (lo registra «Conectar el bot» en la configuración, con la clave secreta)."""
    token = _activar(alias)
    try:
        cfg = _config()
        if not cfg.telegram_activo or request.method != 'POST':
            return HttpResponseForbidden('Telegram no está activado en el CRM.')
        if not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token', ''), cfg.telegram_secreto):
            return HttpResponseForbidden('Clave inválida.')
        try:
            update = json.loads(request.body or b'{}')
        except ValueError:
            return HttpResponse(status=400)
        m = update.get('message') or update.get('edited_message') or {}
        chat = m.get('chat') or {}
        if not chat.get('id') or chat.get('type', 'private') != 'private':
            return HttpResponse('ok')  # grupos y canales no son prospectos
        de = m.get('from') or {}
        nombre = ' '.join(x for x in (de.get('first_name'), de.get('last_name')) if x) or de.get('username', '')
        telefono = (m.get('contact') or {}).get('phone_number', '')
        texto = m.get('text') or m.get('caption') or ('Compartió su teléfono' if telefono else '[Envió un adjunto]')
        inicio = texto.startswith('/start')
        if inicio:
            texto = '(inició la conversación con el bot)'
        try:
            conv = recibir('TELEGRAM', chat['id'], texto, nombre=nombre, telefono=telefono)
            if inicio and cfg.chat_saludo:
                _http(f'{TELEGRAM}/bot{cfg.telegram_token}/sendMessage', {'chat_id': chat['id'],
                                                                           'text': cfg.chat_saludo})
                from .models import MensajeCanal
                MensajeCanal.objects.create(conversacion=conv, entrante=False, texto=cfg.chat_saludo, estado='ENVIADO')
        except Exception as exc:  # noqa: BLE001
            log.warning('CRM Telegram: mensaje no procesado: %s', exc)
        return HttpResponse('ok')
    finally:
        emp.restaurar(token)


def conectar_telegram(request, cfg):
    """Registra el webhook en Telegram (setWebhook) con la clave secreta. Devuelve el nombre del bot."""
    from django.urls import reverse
    from erp.empresas import actual
    if not cfg.telegram_token:
        raise ErrorCanal('Ingrese y guarde primero el token del bot.')
    url = request.build_absolute_uri(reverse('crm_telegram', args=[actual()]))
    if not url.startswith('https://'):
        raise ErrorCanal(f'Telegram exige una dirección https pública; la del sistema es {url}.')
    r = _http(f'{TELEGRAM}/bot{cfg.telegram_token}/setWebhook',
              {'url': url, 'secret_token': cfg.telegram_secreto, 'allowed_updates': ['message', 'edited_message']})
    if not r.get('ok'):
        raise ErrorCanal(r.get('description') or 'Telegram rechazó la conexión.')
    yo = _http(f'{TELEGRAM}/bot{cfg.telegram_token}/getMe')
    return (yo.get('result') or {}).get('username', '')


# ---------------------------------------------------------------- TikTok (formularios de anuncios)
@csrf_exempt
def tiktok(request, alias, clave):
    """Formularios de anuncios de TikTok (Lead Generation): el webhook de TikTok o la herramienta de integración
    (Zapier, Make) envía cada formulario en JSON a este enlace."""
    token = _activar(alias)
    try:
        cfg = _config()
        if not cfg.tiktok_activo or not hmac.compare_digest(clave, cfg.tiktok_clave):
            raise Http404
        if request.method != 'POST':
            return JsonResponse({'ok': False, 'error': 'Envíe el formulario por POST (JSON).'}, status=405)
        try:
            cuerpo = json.loads(request.body or b'{}')
        except ValueError:
            return JsonResponse({'ok': False, 'error': 'JSON no válido.'}, status=400)
        leads = cuerpo if isinstance(cuerpo, list) else cuerpo.get('leads') or cuerpo.get('data') or [cuerpo]
        if isinstance(leads, dict):
            leads = [leads]
        creadas = []
        for lead in leads[:50]:
            if not isinstance(lead, dict):
                continue
            campos = {}
            for clave_lista in ('answers', 'field_data', 'fields', 'user_info'):
                lista = lead.get(clave_lista)
                if isinstance(lista, list):
                    for f in lista:
                        if isinstance(f, dict):
                            k = f.get('name') or f.get('field_name') or f.get('question') or f.get('key') or ''
                            v = f.get('value') if 'value' in f else f.get('values') or f.get('answer') or ''
                            campos[str(k)] = ', '.join(map(str, v)) if isinstance(v, list) else str(v)
                elif isinstance(lista, dict):
                    campos.update({str(k): str(v) for k, v in lista.items()})
            campos.update({k: str(v) for k, v in lead.items() if isinstance(v, (str, int)) and k not in campos})
            try:
                creadas.append(registrar_formulario('TIKTOK', campos).pk)
            except ErrorCanal as exc:
                log.info('CRM TikTok: formulario sin datos de contacto: %s', exc)
        return JsonResponse({'ok': bool(creadas), 'oportunidades': creadas}, status=200 if creadas else 400)
    finally:
        emp.restaurar(token)


# ---------------------------------------------------------------- chat de la página web
def _cors(resp):
    resp['Access-Control-Allow-Origin'] = '*'  # el chat va en la página de la empresa (otro dominio)
    resp['Access-Control-Allow-Methods'] = 'GET, POST'
    return resp


def _mensajes_json(conv, desde=0):
    return [{'id': m.id, 'yo': m.entrante, 'texto': m.texto, 'hora': timezone.localtime(m.fecha).strftime('%H:%M')}
            for m in conv.mensajes.filter(id__gt=desde).exclude(estado='ERROR')[:200]]


@csrf_exempt
def chat(request, alias, clave):
    """API del chat web: POST envía un mensaje (el primero con nombre, contacto y consentimiento); GET ?c=&desde=
    trae los mensajes nuevos (las respuestas del vendedor)."""
    from .models import ConversacionCanal
    token = _activar(alias)
    try:
        cfg = _config()
        if not cfg.chat_activo or not hmac.compare_digest(clave, cfg.token_web):
            raise Http404
        c = (request.POST.get('c') if request.method == 'POST' else request.GET.get('c')) or ''
        conv = ConversacionCanal.objects.filter(canal='CHAT_WEB', externo=c).first() if c else None
        if request.method != 'POST':
            if conv is None:
                return _cors(JsonResponse({'ok': False, 'mensajes': []}, status=404))
            try:
                desde = int(request.GET.get('desde') or 0)
            except ValueError:
                desde = 0
            return _cors(JsonResponse({'ok': True, 'mensajes': _mensajes_json(conv, desde)}))
        if request.POST.get('sitio_web'):  # campo trampa: se responde como si estuviera bien
            return _cors(JsonResponse({'ok': True, 'c': secrets.token_urlsafe(24), 'mensajes': []}))
        texto = (request.POST.get('texto') or '').strip()
        if not texto:
            return _cors(JsonResponse({'ok': False, 'errores': ['Escriba su mensaje.']}, status=400))
        if conv is None:
            nombre = ' '.join((request.POST.get('nombre') or '').split())[:120]
            telefono = ' '.join((request.POST.get('telefono') or '').split())[:40]
            email = ' '.join((request.POST.get('email') or '').split())[:254]
            errores = []
            if not nombre:
                errores.append('Indique su nombre.')
            if not telefono and not email:
                errores.append('Indique su teléfono o su correo.')
            if not request.POST.get('consentimiento'):
                errores.append('Debe autorizar el uso de sus datos para que podamos responderle.')
            if errores:
                return _cors(JsonResponse({'ok': False, 'errores': errores}, status=400))
            c = secrets.token_urlsafe(24)
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or
                  request.META.get('REMOTE_ADDR', ''))[:45]
            conv = recibir('CHAT_WEB', c, texto, nombre=nombre, telefono=telefono, email=email, ip=ip)
        else:
            hace_una_hora = timezone.now() - timedelta(hours=1)
            if conv.mensajes.filter(entrante=True, fecha__gte=hace_una_hora).count() >= MAX_POR_HORA_CHAT:
                return _cors(JsonResponse({'ok': False, 'errores': ['Demasiados mensajes: espere unos minutos.']},
                                          status=429))
            recibir('CHAT_WEB', c, texto)
        try:
            desde = int(request.POST.get('desde') or 0)
        except ValueError:
            desde = 0
        return _cors(JsonResponse({'ok': True, 'c': conv.externo, 'mensajes': _mensajes_json(conv, desde)}))
    finally:
        emp.restaurar(token)


def chat_widget(request, alias, clave):
    """El script que se pega en la página: <script src="…/widget.js" async></script> dibuja la burbuja del chat."""
    token = _activar(alias)
    try:
        cfg = _config()
        if not cfg.chat_activo or not hmac.compare_digest(clave, cfg.token_web):
            raise Http404
        from django.urls import reverse
        contexto = {'api': request.build_absolute_uri(reverse('crm_chat', args=[alias, clave])),
                    'empresa': emp.razon_social(alias), 'saludo': cfg.chat_saludo,
                    'finalidad': cfg.finalidad_datos}
        resp = render(request, 'ventas/crm_chat_widget.js', {'config': json.dumps(contexto)},
                      content_type='application/javascript; charset=utf-8')
        resp['Cache-Control'] = 'public, max-age=300'
        return _cors(resp)
    finally:
        emp.restaurar(token)


# ---------------------------------------------------------------- bandeja de mensajes (usuarios del CRM)
def _visibles(user):
    from . import crm_servicios as srv
    from .models import ConversacionCanal, Oportunidad
    qs = ConversacionCanal.objects.select_related('oportunidad__responsable', 'oportunidad__tercero')
    if srv.ve_todo(user):
        return qs
    propias = srv.visibles(user, Oportunidad.objects.all()).values('pk')
    return qs.filter(Q(oportunidad__in=propias) | Q(oportunidad__isnull=True))


class RespuestaForm(forms.Form):
    texto = forms.CharField(label='Respuesta', max_length=MAX_TEXTO,
                            widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                                         'placeholder': 'Escriba su respuesta…'}))


@login_required
def bandeja(request):
    from .models import ConversacionCanal
    qs = _visibles(request.user)
    canal = request.GET.get('canal', '')
    if canal in dict(ConversacionCanal.CANALES):
        qs = qs.filter(canal=canal)
    if request.GET.get('sin_leer'):
        qs = qs.filter(sin_leer__gt=0)
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(telefono__icontains=q) | Q(oportunidad__nombre__icontains=q))
    from django.db.models import OuterRef, Subquery
    from .models import MensajeCanal
    ultimo = MensajeCanal.objects.filter(conversacion=OuterRef('pk')).order_by('-fecha', '-id')
    qs = qs.annotate(ultimo_texto=Subquery(ultimo.values('texto')[:1]),
                     ultimo_es_cliente=Subquery(ultimo.values('entrante')[:1]))
    return render(request, 'ventas/crm_bandeja.html', {
        'conversaciones': qs[:300], 'canales': ConversacionCanal.CANALES, 'canal': canal, 'q': q,
        'activos': canales_activos()})


def canales_activos():
    """[(canal, nombre, activo, nota)] para mostrar qué entra hoy al CRM."""
    cfg, r = _config(), _rendicion()
    wa = bool(r.whatsapp_phone_id and r.whatsapp_token and r.whatsapp_app_secret)
    return [
        ('CORREO', 'Correo de ventas', cfg.correo_activo, 'Cada correo nuevo crea una oportunidad'),
        ('WEB', 'Formulario de la página web', cfg.web_activo, 'Crea la oportunidad y agenda «Llamar hoy»'),
        ('WHATSAPP', 'WhatsApp', cfg.whatsapp_activo and wa,
         'Recibe y responde' if wa else 'Falta la conexión oficial (Rendiciones › Integraciones)'),
        ('MESSENGER', 'Facebook Messenger', cfg.meta_activo and bool(cfg.meta_token), 'Recibe y responde'),
        ('INSTAGRAM', 'Instagram', cfg.meta_activo and bool(cfg.meta_token), 'Recibe y responde (mensajes directos)'),
        ('META_ADS', 'Anuncios de Facebook / Instagram', cfg.meta_leads and bool(cfg.meta_token),
         'Cada formulario crea la oportunidad'),
        ('CHAT_WEB', 'Chat en la página web', cfg.chat_activo, 'Burbuja de chat; se responde desde la bandeja'),
        ('TELEGRAM', 'Telegram', cfg.telegram_activo and bool(cfg.telegram_token), 'Bot conectado al CRM'),
        ('TIKTOK', 'Anuncios de TikTok', cfg.tiktok_activo, 'Cada formulario crea la oportunidad'),
    ]


@login_required
def conversacion(request, pk):
    from . import crm_servicios as srv
    conv = get_object_or_404(_visibles(request.user), pk=pk)
    if conv.oportunidad_id and not srv.puede_ver(request.user, conv.oportunidad):
        return HttpResponseForbidden('Esta conversación es de otro vendedor.')
    if request.GET.get('json'):  # la pantalla abierta consulta los mensajes nuevos
        try:
            desde = int(request.GET.get('desde') or 0)
        except ValueError:
            desde = 0
        nuevos = list(conv.mensajes.filter(id__gt=desde).select_related('usuario'))
        if any(m.entrante for m in nuevos):
            type(conv).objects.filter(pk=conv.pk).update(sin_leer=0)
        return JsonResponse({'mensajes': [{'id': m.id, 'entrante': m.entrante, 'texto': m.texto,
                                           'hora': timezone.localtime(m.fecha).strftime('%d/%m %H:%M'),
                                           'usuario': (m.usuario.get_full_name() or m.usuario.username)
                                           if m.usuario_id else '', 'error': m.error} for m in nuevos]})
    form = RespuestaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            enviar(conv, form.cleaned_data['texto'], request.user)
            messages.success(request, 'Respuesta enviada.')
        except ErrorCanal as exc:
            messages.error(request, f'No se envió: {exc}')
        return redirect('crm:conversacion', pk)
    if conv.sin_leer:
        type(conv).objects.filter(pk=conv.pk).update(sin_leer=0)
    ok, motivo = puede_responder(conv)
    mensajes = list(conv.mensajes.select_related('usuario'))
    return render(request, 'ventas/crm_conversacion.html', {
        'conv': conv, 'mensajes': mensajes, 'form': form, 'puede': ok, 'motivo': motivo,
        'ultimo_id': mensajes[-1].id if mensajes else 0})
