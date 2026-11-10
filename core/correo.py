"""Envío de correos con el servidor configurado en Ajustes > Correo saliente."""
import logging
from email.utils import formataddr

from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import CorreoConfig, Empresa

log = logging.getLogger(__name__)


class ErrorCorreo(Exception):
    pass


def correo_ceiba():
    """REQ-06: el correo general de Ceiba si está configurado y activo; si no, None."""
    try:
        from .models import CorreoCeiba
        cfg = CorreoCeiba.objects.using('default').first()
    except Exception:  # base sin la tabla todavía
        return None
    return cfg if cfg is not None and cfg.activo and cfg.activa else None


def configuracion():
    """(configuración con que se envía, 'empresa' | 'ceiba') o (None, None) si no hay ninguna."""
    cfg = CorreoConfig.actual()
    if cfg.activa:
        return cfg, 'empresa'
    general = correo_ceiba()
    return (general, 'ceiba') if general else (None, None)


def enviar(destinatarios, asunto, plantilla, contexto, adjuntos=None, solo_ceiba=False):
    """Envía un correo HTML. Lanza ErrorCorreo con un mensaje claro si no se pudo.

    Usa el correo saliente de la empresa; si no lo configuró, el correo de Ceiba (REQ-06). solo_ceiba: para probar
    el correo de Ceiba. adjuntos: [(nombre, contenido en bytes, tipo MIME)]."""
    cfg, origen = (correo_ceiba(), 'ceiba') if solo_ceiba else configuracion()
    if cfg is None:
        raise ErrorCorreo('Configure el correo saliente en Ajustes > Correo saliente (o el administrador del grupo, '
                          'el correo de Ceiba en el Panel del grupo).')
    destinatarios = [d for d in destinatarios if d]
    if not destinatarios:
        raise ErrorCorreo('El destinatario no tiene correo electrónico registrado.')
    empresa = Empresa.actual()
    contexto = {'empresa': empresa, **contexto}
    html = render_to_string(plantilla, contexto)
    nombre = cfg.nombre_remitente or (empresa.razon_social if origen == 'empresa' else
                                      f'Ceiba ERP · {empresa.razon_social}')
    remitente = formataddr((nombre, cfg.remitente or cfg.usuario))
    conexion = get_connection('django.core.mail.backends.smtp.EmailBackend', host=cfg.servidor, port=cfg.puerto,
                              username=cfg.usuario or None, password=cfg.clave or None,
                              use_tls=cfg.seguridad == 'TLS', use_ssl=cfg.seguridad == 'SSL', timeout=20)
    mensaje = EmailMultiAlternatives(asunto, strip_tags(html), remitente, destinatarios,
                                     bcc=[cfg.copia] if cfg.copia else None, connection=conexion,
                                     reply_to=[empresa.email] if empresa.email else None)
    mensaje.attach_alternative(html, 'text/html')
    for nombre, contenido, tipo in adjuntos or []:
        mensaje.attach(nombre, contenido, tipo)
    try:
        mensaje.send()
    except Exception as exc:  # autenticación, servidor caído, puerto bloqueado...
        log.warning('No se pudo enviar el correo "%s": %s', asunto, exc)
        raise ErrorCorreo(f'No se pudo enviar el correo: {exc}') from exc


def enviar_simple(destinatarios, asunto, texto):
    """Aviso de texto (recordatorios y avisos del CRM) con la plantilla básica."""
    return enviar(destinatarios, asunto, 'core/correo_simple.html', {'texto': texto, 'asunto': asunto})
