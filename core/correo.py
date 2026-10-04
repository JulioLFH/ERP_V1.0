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


def enviar(destinatarios, asunto, plantilla, contexto):
    """Envía un correo HTML. Lanza ErrorCorreo con un mensaje claro si no se pudo."""
    cfg = CorreoConfig.actual()
    if not cfg.activa:
        raise ErrorCorreo('Configure el correo saliente en Ajustes > Correo saliente.')
    destinatarios = [d for d in destinatarios if d]
    if not destinatarios:
        raise ErrorCorreo('El destinatario no tiene correo electrónico registrado.')
    empresa = Empresa.actual()
    contexto = {'empresa': empresa, **contexto}
    html = render_to_string(plantilla, contexto)
    remitente = formataddr((cfg.nombre_remitente or empresa.razon_social, cfg.remitente or cfg.usuario))
    conexion = get_connection('django.core.mail.backends.smtp.EmailBackend', host=cfg.servidor, port=cfg.puerto,
                              username=cfg.usuario or None, password=cfg.clave or None,
                              use_tls=cfg.seguridad == 'TLS', use_ssl=cfg.seguridad == 'SSL', timeout=20)
    mensaje = EmailMultiAlternatives(asunto, strip_tags(html), remitente, destinatarios,
                                     bcc=[cfg.copia] if cfg.copia else None, connection=conexion,
                                     reply_to=[empresa.email] if empresa.email else None)
    mensaje.attach_alternative(html, 'text/html')
    try:
        mensaje.send()
    except Exception as exc:  # autenticación, servidor caído, puerto bloqueado...
        log.warning('No se pudo enviar el correo "%s": %s', asunto, exc)
        raise ErrorCorreo(f'No se pudo enviar el correo: {exc}') from exc
