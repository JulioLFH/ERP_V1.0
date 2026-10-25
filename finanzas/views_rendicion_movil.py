"""Rendición desde el celular (app web instalable, funciona sin internet), lectura de comprobantes (QR, RUC, IA) y
recepción de gastos por WhatsApp."""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from contabilidad.models import CentroCosto
from core import sustentos
from core.models import D0, Empresa

from . import comprobantes as cp
from . import rendiciones as rd
from .forms_rendicion import LineaGastoForm
from .models import CategoriaGasto, ConfigRendicion, LineaGasto, Proyecto, Rendicion
from core.utils import id_o_none

log = logging.getLogger(__name__)


def _json(datos, status=200):
    return JsonResponse(datos, status=status, json_dumps_params={'ensure_ascii': False})


# ---------------------------------------------------------------- app del celular (PWA)
@login_required
def movil(request):
    user = request.user
    abiertas = list(Rendicion.objects.filter(rendidor=user, estado__in=Rendicion.EDITABLES, entrega__estado='ABIERTA')
                    .select_related('entrega').order_by('-fecha', '-id')[:20])
    from .models import IndicadorImpuesto
    from .views_rendicion import _sugerencias
    corte = rd.proximo_corte()
    datos = {
        'rendiciones': [_rend(r) for r in abiertas],
        'indicadores': [{'id': i.pk, 'nombre': str(i), 'tasa': str(i.tasa)}
                        for i in IndicadorImpuesto.objects.filter(activo=True)],
        'sugerencias': _sugerencias(),
        'corte': corte.strftime('%d/%m/%Y') if corte else '',
        'por_aprobar': sum(1 for r in Rendicion.objects.filter(estado__in=Rendicion.POR_APROBAR)
                           if rd.puede_aprobar(user, r)),
        'entregas': [{'id': e.pk, 'texto': f'{e.numero} · {e.motivo[:40]}'}
                     for e in rd.entregas_para_rendir(user).exclude(tipo='TARJETA')[:20]],
        'categorias': [{'id': c.pk, 'nombre': c.nombre, 'tarifa_km': str(c.tarifa_km), 'movilidad': c.es_movilidad}
                       for c in CategoriaGasto.objects.filter(activo=True)],
        'centros': [{'id': c.pk, 'nombre': f'{c.codigo} {c.nombre}'} for c in CentroCosto.objects.filter(activo=True)],
        'proyectos': [{'id': p.pk, 'nombre': str(p)} for p in Proyecto.objects.filter(activo=True)],
        'ruc_empresa': Empresa.actual().ruc, 'ia': cp.ia_configurada(), 'usuario': user.username,
        'urls': {'gasto': reverse('rendiciones:movil_gasto'), 'ruc': reverse('rendiciones:ruc', args=['00000000000']),
                 'ia': reverse('rendiciones:leer_ia'), 'nueva': reverse('rendiciones:movil_nueva'),
                 'lista': reverse('rendiciones:lista'), 'duplicado': reverse('rendiciones:duplicado'),
                 'aprobar': reverse('rendiciones:movil_aprobar'),
                 'detalle': reverse('rendiciones:detalle', args=[0])},
    }
    return render(request, 'finanzas/rendicion_movil.html', {'datos': datos, 'version': settings.ERP_VERSION})


def _rend(r):
    """Rendición con el saldo en poder del responsable (lo que le entregaron, lo rendido y lo que falta)."""
    e = r.entrega
    tramite = sum((x.total for x in e.rendiciones.filter(estado__in=['BORRADOR', 'OBSERVADA', 'ENVIADA',
                                                                       'EN_REVISION', 'APROBADA'])), D0)
    return {'id': r.pk, 'texto': f'{r.numero} · {r.descripcion[:40]}', 'moneda': e.moneda, 'simbolo': e.simbolo,
            'entregado': f'{e.entregado:,.2f}', 'rendido': f'{e.rendido + tramite:,.2f}',
            'falta': f'{e.saldo - tramite:,.2f}', 'estado': r.get_estado_display()}


@login_required
def duplicado(request):
    """¿El comprobante ya fue rendido o está en el registro de compras? (aviso inmediato tras leer el QR)."""
    linea = LineaGasto(ruc=request.GET.get('ruc', '')[:11], tipo_comprobante=request.GET.get('tipo', '')[:2],
                       serie=request.GET.get('serie', '').upper()[:4], numero=request.GET.get('numero', '')[:10])
    if linea.numero.isdigit():
        linea.numero = str(int(linea.numero))
    return _json({'duplicado': rd.duplicado(linea)})


@login_required
def movil_aprobar(request, pk=None):
    """Aprobar desde el celular: rendiciones pendientes del usuario y, en cada una, gasto por gasto con su foto."""
    from .models import MotivoObservacion
    if pk is None:
        pendientes = [r for r in Rendicion.objects.filter(estado__in=Rendicion.POR_APROBAR)
                      .select_related('entrega__responsable') if rd.puede_aprobar(request.user, r)]
        return render(request, 'finanzas/rendicion_movil_aprobar.html', {'pendientes': pendientes})
    r = rd.visibles(request.user).filter(pk=pk).first()
    if r is None or not rd.puede_aprobar(request.user, r):
        return render(request, 'finanzas/rendicion_movil_aprobar.html', {'pendientes': [], 'no_corresponde': True})
    lineas = list(r.lineas.exclude(estado='RECHAZADA').select_related('categoria', 'indicador', 'centro_costo'))
    for l in lineas:
        l.fotos = list(sustentos.de(l))
    return render(request, 'finanzas/rendicion_movil_aprobar.html', {
        'r': r, 'lineas': lineas, 'motivos': MotivoObservacion.objects.filter(activo=True)})


@login_required
@require_POST
def movil_nueva(request):
    """Rendición nueva desde el celular (cuando el usuario no tiene una abierta)."""
    from .models import EntregaRendir
    try:
        entrega = rd.entregas_para_rendir(request.user).get(pk=request.POST.get('entrega'))
        r = rd.crear(entrega, request.user, request.POST.get('descripcion') or 'Gastos del celular')
    except (EntregaRendir.DoesNotExist, ValueError):
        return _json({'error': 'Entrega no válida.'}, 400)
    except rd.ErrorRendicion as exc:
        return _json({'error': str(exc)}, 400)
    return _json({'id': r.pk, 'texto': f'{r.numero} · {r.descripcion[:40]}', 'moneda': r.entrega.moneda})


@login_required
@require_POST
def movil_gasto(request):
    """Recibe un gasto (con sus fotos ya comprimidas) desde la app. Idempotente por `cliente_id`: si la conexión se
    corta y la app reintenta, no se duplica."""
    from django.core.cache import cache
    cliente_id = (request.POST.get('cliente_id') or '')[:64]
    clave = f'movil-gasto-{request.user.pk}-{cliente_id}'
    if cliente_id and cache.get(clave):
        return _json({'ok': True, 'linea': cache.get(clave), 'repetido': True})
    r = rd.visibles(request.user).filter(pk=id_o_none(request.POST.get('rendicion'))).first()
    if r is None:
        return _json({'error': 'Rendición no encontrada.'}, 404)
    form = LineaGastoForm(request.POST, request.FILES)
    if not form.is_valid():
        return _json({'error': 'Revise el gasto: ' + '; '.join(
            f'{form.fields[k].label if k in form.fields else ""} {" ".join(v)}'.strip()
            for k, v in form.errors.items())}, 400)
    try:
        linea = form.save(commit=False)
        linea.origen = 'MOVIL'
        linea = rd.guardar_linea(r, linea, request.user)
        for archivo in form.cleaned_data.get('fotos') or []:
            sustentos.adjuntar(linea, archivo, request.user, 'Comprobante (app del celular)')
        rd.evaluar(r)
    except rd.ErrorRendicion as exc:
        return _json({'error': str(exc)}, 400)
    if cliente_id:
        cache.set(clave, linea.pk, 7 * 86400)
    linea.refresh_from_db()
    return _json({'ok': True, 'linea': linea.pk, 'rendicion': r.numero, 'alertas': linea.alertas})


def manifest(request):
    return JsonResponse({
        'name': f'{settings.ERP_NOMBRE} · Rendición de gastos', 'short_name': 'Rendiciones',
        'start_url': reverse('rendiciones:movil'), 'scope': '/rendiciones/', 'display': 'standalone',
        'background_color': '#f4f6f8', 'theme_color': '#0b7285', 'lang': 'es',
        'icons': [{'src': static('img/ceiba-app.svg'), 'sizes': 'any', 'type': 'image/svg+xml', 'purpose': 'any'}],
    }, content_type='application/manifest+json')


SW = """// Service worker de la app de rendiciones (Ceiba ERP {version})
const CACHE = 'rendiciones-{version}';
const BASE = ['{movil}', '{js}', '{icono}'];
self.addEventListener('install', e => {{
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE)).then(() => self.skipWaiting()));
}});
self.addEventListener('activate', e => {{
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
}});
self.addEventListener('fetch', e => {{
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (e.request.mode === 'navigate' && url.pathname === '{movil}') {{
    // primero la red (datos al día); sin señal, la última copia guardada
    e.respondWith(fetch(e.request).then(r => {{
      const copia = r.clone(); caches.open(CACHE).then(c => c.put('{movil}', copia)); return r;
    }}).catch(() => caches.match('{movil}')));
    return;
  }}
  if (url.pathname.startsWith('/static/')) {{
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(resp => {{
      const copia = resp.clone(); caches.open(CACHE).then(c => c.put(e.request, copia)); return resp;
    }})));
  }}
}});
"""


def service_worker(request):
    js = static('js/rendicion_movil.js') + f'?v={settings.ERP_VERSION}'
    resp = HttpResponse(SW.format(version=settings.ERP_VERSION, movil=reverse('rendiciones:movil'), js=js,
                                  icono=static('img/ceiba-app.svg')), content_type='application/javascript')
    resp['Service-Worker-Allowed'] = '/rendiciones/'
    resp['Cache-Control'] = 'no-cache'
    return resp


# ---------------------------------------------------------------- lecturas
@login_required
def ruc(request, numero):
    try:
        return _json(cp.consultar_ruc(numero))
    except cp.ErrorLectura as exc:
        return _json({'error': str(exc)}, 400)


@login_required
@require_POST
def qr(request):
    try:
        return _json(cp.a_json(cp.leer_qr(request.POST.get('texto', ''))))
    except cp.ErrorLectura as exc:
        return _json({'error': str(exc)}, 400)


@login_required
@require_POST
def leer_ia(request):
    foto = request.FILES.get('foto')
    if foto is None or foto.size > 5 * 1024 * 1024:
        return _json({'error': 'Envíe la foto del comprobante (máx. 5 MB).'}, 400)
    try:
        return _json(cp.a_json(cp.leer_con_ia(foto.read(), foto.content_type)))
    except cp.ErrorLectura as exc:
        return _json({'error': str(exc)}, 400)


# ---------------------------------------------------------------- WhatsApp Business Cloud API
GRAPH = 'https://graph.facebook.com/v20.0'


def _graph(url, token, datos=None):
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                                 method='POST' if datos is not None else 'GET')
    with urllib.request.urlopen(req, timeout=20) as resp:
        cuerpo = resp.read()
        return cuerpo if resp.headers.get_content_type() not in ('application/json', 'text/javascript') \
            else json.loads(cuerpo or b'{}')


def _descargar_media(media_id, token):
    info = _graph(f'{GRAPH}/{media_id}', token)
    return _graph(info['url'], token), info.get('mime_type', 'image/jpeg')


def _responder(cfg, telefono, texto):
    try:
        _graph(f'{GRAPH}/{cfg.whatsapp_phone_id}/messages', cfg.whatsapp_token,
               {'messaging_product': 'whatsapp', 'to': telefono, 'type': 'text', 'text': {'body': texto[:1000]}})
    except Exception as exc:
        log.info('No se pudo responder por WhatsApp a %s: %s', telefono, exc)


def usuario_por_celular(telefono):
    """Usuario cuyo celular registrado coincide (se comparan los últimos 9 dígitos)."""
    from core.models import PerfilUsuario
    digitos = ''.join(c for c in str(telefono) if c.isdigit())[-9:]
    if len(digitos) < 9:
        return None
    for p in PerfilUsuario.objects.exclude(celular='').select_related('usuario'):
        if ''.join(c for c in p.celular if c.isdigit())[-9:] == digitos and p.usuario.is_active:
            return p.usuario
    return None


def procesar_mensaje(cfg, mensaje):
    """Una foto o PDF recibido: identifica al trabajador, lee el comprobante (QR o IA) y crea el borrador."""
    telefono = mensaje.get('from', '')
    user = usuario_por_celular(telefono)
    if user is None:
        _responder(cfg, telefono, 'Su número no está registrado en el sistema. Pida al administrador que lo agregue '
                                  'en su usuario (Ajustes › Usuarios › Celular).')
        return None
    tipo = mensaje.get('type')
    media = mensaje.get(tipo) if tipo in ('image', 'document') else None
    if not media:
        _responder(cfg, telefono, 'Envíe la foto del comprobante (o el PDF) para registrar el gasto.')
        return None
    contenido, mime = _descargar_media(media['id'], cfg.whatsapp_token)
    datos = {}
    try:
        datos = cp.leer_con_ia(contenido, mime) if cp.ia_configurada() else {}
    except cp.ErrorLectura as exc:
        log.info('WhatsApp: lectura no disponible: %s', exc)
    if media.get('caption'):
        datos['glosa'] = media['caption']
    try:
        linea = rd.borrador_recibido(user, datos, [(contenido, media.get('filename') or 'whatsapp.jpg', mime)],
                                     origen='WHATSAPP')
    except rd.ErrorRendicion as exc:
        _responder(cfg, telefono, f'No se registró el gasto: {exc}')
        return None
    resumen = f'{linea.get_tipo_comprobante_display()} {linea.documento}'.strip()
    _responder(cfg, telefono, f'Gasto registrado en {linea.rendicion.numero} como borrador: {resumen}, total '
                              f'{linea.total}. Revíselo y envíelo desde el sistema.')
    return linea


@csrf_exempt
def whatsapp(request):
    """Webhook de WhatsApp Business Cloud API (verificación GET y mensajes POST firmados)."""
    from erp.empresas import activar, empresas
    alias = request.GET.get('empresa')
    if alias and alias in empresas():
        activar(alias)
    cfg = ConfigRendicion.actual()
    if not cfg.whatsapp_activo:
        return HttpResponseForbidden('WhatsApp no está activado.')
    if request.method == 'GET':
        if request.GET.get('hub.mode') == 'subscribe' and cfg.whatsapp_verify_token and \
                request.GET.get('hub.verify_token') == cfg.whatsapp_verify_token:
            return HttpResponse(request.GET.get('hub.challenge', ''))
        return HttpResponseForbidden('Token de verificación incorrecto.')
    firma = request.META.get('HTTP_X_HUB_SIGNATURE_256', '')
    esperado = 'sha256=' + hmac.new(cfg.whatsapp_app_secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not cfg.whatsapp_app_secret or not hmac.compare_digest(firma, esperado):
        return HttpResponseForbidden('Firma inválida.')
    try:
        cuerpo = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponse(status=400)
    for entrada in cuerpo.get('entry', []):
        for cambio in entrada.get('changes', []):
            for mensaje in cambio.get('value', {}).get('messages', []):
                try:
                    procesar_mensaje(cfg, mensaje)
                except Exception as exc:  # un mensaje con error no debe frenar los demás
                    log.warning('WhatsApp: mensaje no procesado: %s', exc)
    return HttpResponse('ok')
