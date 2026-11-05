"""Motor común de los agentes: una pregunta del usuario -> llamadas a Claude con las herramientas del módulo hasta
la respuesta. Controla el permiso del agente, el tope de gasto mensual y el número de consultas al ERP; guarda la
conversación (solo se agrega al final, como la necesita la API) y la bitácora de cada herramienta."""
import logging
import os
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .herramientas import ejecutar
from .models import ConfigAgentes, LlamadaHerramienta, Mensaje
from .registro import AGENTES, instrucciones

log = logging.getLogger(__name__)
D0 = Decimal('0')
MILLON = Decimal('1000000')
# US$ por millón de tokens: entrada, salida, lectura de caché, escritura de caché (5 min). Estimado para el tope.
PRECIOS = {'claude-opus-5-5': (Decimal('4'), Decimal('20'), Decimal('0.20'), Decimal('5')),
           'claude-sonnet-5-5': (Decimal('2'), Decimal('10'), Decimal('0.20'), Decimal('2.50')),
           'claude-haiku-5-5': (Decimal('0.10'), Decimal('0.50'), Decimal('0.01'), Decimal('0.125'))}
# respaldo del servidor si el modelo rechaza la consulta (Haiku no lo tiene)
CON_RESPALDO = {'claude-opus-5-5', 'claude-sonnet-5-5'}
MAX_TOKENS = 16000


class ErrorAgente(Exception):
    pass


def configurado(cfg=None):
    cfg = cfg or ConfigAgentes.actual()
    return cfg.activo and bool(cfg.api_key or os.environ.get('ANTHROPIC_API_KEY'))


def gasto_del_mes():
    hoy = timezone.localdate()
    return Mensaje.objects.filter(creado__year=hoy.year, creado__month=hoy.month).aggregate(
        s=Sum('costo_usd'))['s'] or D0


def bloqueo(agente, usuario, cfg=None):
    """Texto si el usuario no puede preguntar ahora a este agente; '' si puede."""
    from core.permisos import puede
    cfg = cfg or ConfigAgentes.actual()
    if not configurado(cfg):
        return 'Los agentes con IA no están activos en esta empresa (Ajustes › Agentes con IA).'
    if not puede(usuario, agente.permiso):
        return f'Su usuario no tiene el permiso «Usar el {agente.nombre.lower()} con IA».'
    if cfg.tope_mensual_usd and gasto_del_mes() >= cfg.tope_mensual_usd:
        return (f'Se llegó al tope de gasto mensual de los agentes (US$ {cfg.tope_mensual_usd:,.2f}): vuelven a '
                f'responder el próximo mes o cuando el administrador suba el tope.')
    return ''


def costo(modelo, uso):
    entrada, salida, lectura, escritura = PRECIOS.get(modelo, PRECIOS['claude-opus-5-5'])
    return ((uso['entrada'] * entrada + uso['salida'] * salida + uso['lectura'] * lectura
             + uso['escritura'] * escritura) / MILLON).quantize(Decimal('0.0001'))


def _cliente(cfg):
    import anthropic
    return anthropic.Anthropic(api_key=cfg.api_key or None, timeout=120.0)


def _sumar(uso, u):
    uso['entrada'] += u.input_tokens or 0
    uso['salida'] += u.output_tokens or 0
    uso['lectura'] += getattr(u, 'cache_read_input_tokens', 0) or 0
    uso['escritura'] += getattr(u, 'cache_creation_input_tokens', 0) or 0


def _texto(respuesta):
    return '\n'.join(b.text for b in respuesta.content if b.type == 'text').strip()


def responder(conversacion, pregunta, usuario, cliente=None):
    """Responde la pregunta en la conversación y devuelve el Mensaje del agente. Lanza ErrorAgente si no se puede
    preguntar (permiso, configuración o tope)."""
    import anthropic
    from core.models import Empresa
    agente = AGENTES[conversacion.agente]
    cfg = ConfigAgentes.actual()
    motivo = bloqueo(agente, usuario, cfg)
    if motivo:
        raise ErrorAgente(motivo)
    pregunta = (pregunta or '').strip()[:4000]
    if not pregunta:
        raise ErrorAgente('Escriba su pregunta.')
    Mensaje.objects.create(conversacion=conversacion, rol='USUARIO', texto=pregunta)
    cliente = cliente or _cliente(cfg)
    # la conversación solo crece al final; si este turno falla no se guarda nada de él en el historial
    historial = list(conversacion.historial) + [{'role': 'user', 'content': [{'type': 'text', 'text': pregunta}]}]
    params = {'model': cfg.modelo, 'max_tokens': MAX_TOKENS, 'thinking': {'type': 'adaptive'},
              'output_config': {'effort': cfg.esfuerzo}, 'cache_control': {'type': 'ephemeral'},
              'system': instrucciones(agente, Empresa.actual(), usuario, timezone.localdate()),
              'tools': [h.definicion() for h in agente.herramientas]}
    if cfg.modelo in CON_RESPALDO:
        params.update(betas=['server-side-fallback-2026-07-01'], fallbacks='default')
    uso = {'entrada': 0, 'salida': 0, 'lectura': 0, 'escritura': 0}
    llamadas, texto, error = [], '', False
    try:
        for vuelta in range(cfg.max_herramientas + 1):
            ultima = vuelta == cfg.max_herramientas  # sin más consultas al ERP: debe responder con lo que tiene
            r = cliente.beta.messages.create(messages=historial, **params,
                                             **({'tool_choice': {'type': 'none'}} if ultima else {}))
            _sumar(uso, r.usage)
            historial.append({'role': 'assistant',
                              'content': [b.to_dict(mode='json', exclude_none=True) for b in r.content]})
            if r.stop_reason == 'refusal':
                texto = 'No puedo ayudar con esa consulta. Pruebe a formularla de otra manera.'
                break
            if r.stop_reason != 'tool_use':
                texto = _texto(r) or 'No obtuve una respuesta.'
                if r.stop_reason == 'max_tokens':
                    texto += '\n\n(La respuesta se cortó por su largo: pida un resumen más corto.)'
                break
            resultados = []
            for b in r.content:
                if b.type != 'tool_use':
                    continue
                h = agente.herramienta(b.name)
                salida, ok = ejecutar(h, b.input, usuario) if h else (f'Herramienta desconocida: {b.name}', False)
                llamadas.append((b.name, b.input if isinstance(b.input, dict) else {}, salida, ok))
                resultados.append({'type': 'tool_result', 'tool_use_id': b.id, 'content': salida,
                                   **({} if ok else {'is_error': True})})
            historial.append({'role': 'user', 'content': resultados})  # todos los resultados en un solo mensaje
    except anthropic.AuthenticationError:
        texto, error = 'La clave API de Anthropic no es válida (Ajustes › Agentes con IA).', True
    except anthropic.RateLimitError:
        texto, error = 'El servicio de IA está ocupado en este momento: intente en un minuto.', True
    except anthropic.APIConnectionError:
        texto, error = 'No hay conexión con el servicio de IA: revise el internet del servidor.', True
    except anthropic.APIStatusError as exc:
        log.warning('Agente %s: %s', agente.clave, exc)
        texto, error = f'El servicio de IA respondió con un error ({exc.status_code}): intente de nuevo.', True
    with transaction.atomic():
        m = Mensaje.objects.create(conversacion=conversacion, rol='AGENTE', texto=texto, error=error,
                                   modelo=cfg.modelo, tokens_entrada=uso['entrada'], tokens_salida=uso['salida'],
                                   tokens_cache_lectura=uso['lectura'], tokens_cache_escritura=uso['escritura'],
                                   costo_usd=costo(cfg.modelo, uso))
        LlamadaHerramienta.objects.bulk_create([
            LlamadaHerramienta(mensaje=m, herramienta=n, entrada=e, resultado=s[:4000], ok=ok)
            for n, e, s, ok in llamadas])
        if not error:
            conversacion.historial = historial
        if not conversacion.titulo:
            conversacion.titulo = pregunta[:120]
        conversacion.save()
    return m
