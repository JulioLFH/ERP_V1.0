"""Lee el buzón de comprobantes (IMAP) y crea borradores en la rendición abierta del remitente.

Programarlo cada pocos minutos (cron o tarea programada): python manage.py leer_correo_rendiciones
- El remitente se identifica por el correo de su usuario.
- XML de factura electrónica: el gasto se llena con sus datos exactos (RUC, serie, número, base, IGV, total).
- PDF o fotos: se adjuntan; si la lectura con IA está activa, se sugieren los datos.
Los mensajes procesados se marcan como leídos."""
import email
import imaplib
import logging
from email.header import decode_header, make_header
from email.utils import parseaddr

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finanzas import comprobantes as cp
from finanzas import rendiciones as rd
from finanzas.models import ConfigRendicion

log = logging.getLogger(__name__)
ADJUNTOS = ('.xml', '.pdf', '.jpg', '.jpeg', '.png', '.webp', '.zip')


def _nombre(parte):
    nombre = parte.get_filename() or ''
    try:
        return str(make_header(decode_header(nombre)))
    except Exception:
        return nombre


def procesar(mensaje):
    """Crea los gastos de un correo. Devuelve (usuario, [líneas]) o (None, [])."""
    remitente = parseaddr(mensaje.get('From', ''))[1].lower()
    user = get_user_model().objects.filter(email__iexact=remitente, is_active=True).first() if remitente else None
    if user is None:
        log.info('Correo de %s ignorado: no es un usuario del sistema.', remitente)
        return None, []
    asunto = str(make_header(decode_header(mensaje.get('Subject', '') or '')))
    xmls, otros = [], []
    for parte in mensaje.walk():
        nombre = _nombre(parte)
        if not nombre.lower().endswith(ADJUNTOS):
            continue
        contenido = parte.get_payload(decode=True) or b''
        (xmls if nombre.lower().endswith(('.xml', '.zip')) else otros).append(
            (contenido, nombre, parte.get_content_type()))
    lineas = []
    for contenido, nombre, tipo in xmls:  # cada XML es un comprobante; su PDF (mismo nombre) va con él
        try:
            datos = cp.desde_xml(contenido, nombre)
        except cp.ErrorLectura as exc:
            log.info('XML %s no leído: %s', nombre, exc)
            continue
        base = nombre.rsplit('.', 1)[0].lower()
        pareja = [o for o in otros if o[1].rsplit('.', 1)[0].lower() == base]
        otros = [o for o in otros if o not in pareja]
        lineas.append(rd.borrador_recibido(user, datos, [(contenido, nombre, 'application/xml')] + pareja,
                                           origen='CORREO'))
    for contenido, nombre, tipo in otros:
        datos = {'glosa': asunto[:200]} if asunto else {}
        if cp.ia_configurada():
            try:
                datos.update(cp.leer_con_ia(contenido, tipo))
            except cp.ErrorLectura as exc:
                log.info('Adjunto %s sin lectura automática: %s', nombre, exc)
        lineas.append(rd.borrador_recibido(user, datos, [(contenido, nombre, tipo)], origen='CORREO'))
    return user, lineas


class Command(BaseCommand):
    help = 'Crea borradores de gastos con los comprobantes reenviados al buzón de rendiciones.'

    def handle(self, *args, **options):
        cfg = ConfigRendicion.actual()
        if not cfg.correo_activo or not (cfg.correo_servidor and cfg.correo_usuario and cfg.correo_clave):
            raise CommandError('Configure el buzón en Rendición de gastos › Integraciones.')
        imap = imaplib.IMAP4_SSL(cfg.correo_servidor, cfg.correo_puerto or 993)
        total = 0
        try:
            imap.login(cfg.correo_usuario, cfg.correo_clave)
            imap.select('INBOX')
            _, ids = imap.search(None, 'UNSEEN')
            for num in ids[0].split():
                _, partes = imap.fetch(num, '(RFC822)')
                mensaje = email.message_from_bytes(partes[0][1])
                try:
                    _, lineas = procesar(mensaje)
                    total += len(lineas)
                except rd.ErrorRendicion as exc:
                    self.stderr.write(f'{mensaje.get("From")}: {exc}')
                imap.store(num, '+FLAGS', '\\Seen')
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        self.stdout.write(f'{total} gasto(s) creados desde el correo.')
