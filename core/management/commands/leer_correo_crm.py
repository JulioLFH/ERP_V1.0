"""Lee el buzón comercial (ventas@…) y crea una oportunidad por cada correo nuevo (CRM-09), en cada empresa con el
buzón configurado en CRM › Configuración. Programarlo cada pocos minutos. Los mensajes procesados se marcan leídos.
v1.117.10: servir.py lo lanza cada 5 minutos. Una empresa con la clave mal no frena a las demás, y solo escribe en el
registro cuando entró algún correo o hubo un error (con -v 2, también cuando no hubo nada).
v1.117.13: los correos que se volvieron oportunidad se mueven a la carpeta del CRM (por defecto «CRM», se crea sola).
Se trabaja con UID (no con el número de orden) para que mover un correo no cambie a cuál apuntan los demás."""
import base64
import email
import imaplib
import re

from django.core.management.base import BaseCommand

from erp import empresas as emp

RE_LIST = re.compile(r'\((?P<marcas>[^)]*)\) (?:"(?P<separador>[^"]*)"|NIL) (?P<nombre>.+)$')


def utf7_imap(texto):
    """Nombre de carpeta en UTF-7 modificado (RFC 3501), el que piden los servidores IMAP para tildes y eñes."""
    salida, bloque = [], []

    def soltar():
        if bloque:
            b64 = base64.b64encode(''.join(bloque).encode('utf-16-be')).decode().rstrip('=').replace('/', ',')
            salida.append(f'&{b64}-')
            bloque.clear()
    for c in texto:
        if 0x20 <= ord(c) <= 0x7e:
            soltar()
            salida.append('&-' if c == '&' else c)
        else:
            bloque.append(c)
    soltar()
    return ''.join(salida)


def entre_comillas(nombre):
    return '"' + nombre.replace('\\', '\\\\').replace('"', '\\"') + '"'


def carpetas(imap):
    """(separador, nombres) de las carpetas del buzón."""
    _, lineas = imap.list()
    separador, nombres = '.', []
    for linea in lineas or []:
        if not linea:
            continue
        linea = linea.decode('utf-8', 'replace') if isinstance(linea, bytes) else str(linea)
        m = RE_LIST.match(linea.strip())
        if not m:
            continue
        separador = m.group('separador') or separador
        nombre = m.group('nombre').strip()
        if len(nombre) >= 2 and nombre[0] == nombre[-1] == '"':
            nombre = nombre[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        nombres.append(nombre)
    return separador, nombres


def carpeta_crm(imap, nombre):
    """Nombre completo de la carpeta en el servidor y la crea si falta. Si las carpetas del buzón cuelgan de INBOX
    (INBOX.Sent, INBOX.Trash…, como Dovecot o cPanel), la del CRM también: INBOX.CRM."""
    separador, nombres = carpetas(imap)
    destino = utf7_imap(nombre)
    otras = [n for n in nombres if n.upper() != 'INBOX']
    prefijo = f'INBOX{separador}'
    if otras and all(n.upper().startswith(prefijo.upper()) for n in otras) \
            and not destino.upper().startswith(prefijo.upper()):
        destino = prefijo + destino
    if not any(n.upper() == destino.upper() for n in nombres):
        estado, detalle = imap.create(entre_comillas(destino))
        if estado != 'OK':
            raise imaplib.IMAP4.error(f'no se pudo crear la carpeta {nombre}: {detalle}')
        try:
            imap.subscribe(entre_comillas(destino))  # que los programas de correo la muestren
        except Exception:  # noqa: BLE001
            pass
    else:
        destino = next(n for n in nombres if n.upper() == destino.upper())
    return destino


def mover(imap, uids, destino):
    """Mueve los correos (UID) a la carpeta. MOVE si el servidor lo tiene; si no, copia y borra solo esos correos
    (UID EXPUNGE). Sin ninguna de las dos, los copia y los deja en la bandeja (nunca vacía la papelera del usuario)."""
    conjunto = ','.join(u.decode() if isinstance(u, bytes) else str(u) for u in uids)
    _, cap = imap.capability()
    capacidades = set((b' '.join(cap or []).decode('ascii', 'replace')).upper().split())
    if 'MOVE' in capacidades:
        estado, detalle = imap.uid('MOVE', conjunto, entre_comillas(destino))
        if estado != 'OK':
            raise imaplib.IMAP4.error(f'el servidor no movió los correos: {detalle}')
        return True
    estado, detalle = imap.uid('COPY', conjunto, entre_comillas(destino))
    if estado != 'OK':
        raise imaplib.IMAP4.error(f'el servidor no copió los correos: {detalle}')
    if 'UIDPLUS' not in capacidades:
        return False
    imap.uid('STORE', conjunto, '+FLAGS', '(\\Deleted)')
    imap.uid('EXPUNGE', conjunto)
    return True


class Command(BaseCommand):
    help = 'Crea oportunidades del CRM con los correos nuevos del buzón comercial.'

    def handle(self, *args, **opciones):
        from ventas.crm_web import procesar_correo
        from ventas.models import ConfigCRM
        emp.sincronizar(forzar=True)
        for alias in emp.empresas():
            token = emp.activar(alias)
            try:
                cfg = ConfigCRM.actual()
                if not (cfg.correo_activo and cfg.correo_servidor and cfg.correo_usuario and cfg.correo_clave):
                    continue
                total, a_mover = 0, []
                try:
                    imap = imaplib.IMAP4_SSL(cfg.correo_servidor, cfg.correo_puerto or 993, timeout=60)
                    try:
                        imap.login(cfg.correo_usuario, cfg.correo_clave)
                        imap.select('INBOX')
                        _, ids = imap.uid('SEARCH', None, 'UNSEEN')
                        for uid in (ids[0] or b'').split():
                            _, partes = imap.uid('FETCH', uid, '(RFC822)')
                            try:
                                if procesar_correo(email.message_from_bytes(partes[0][1])) is not None:
                                    total += 1
                                    a_mover.append(uid)
                            except Exception as exc:  # noqa: BLE001  (un correo raro no frena a los siguientes)
                                self.stderr.write(f'CRM {alias}: un correo no se pudo convertir en oportunidad '
                                                  f'(queda leído en el buzón): {exc}')
                            imap.uid('STORE', uid, '+FLAGS', '(\\Seen)')
                        if a_mover and (cfg.correo_carpeta or '').strip():
                            try:  # la oportunidad ya está creada: si no se puede mover, el correo queda leído
                                destino = carpeta_crm(imap, cfg.correo_carpeta.strip())
                                if not mover(imap, a_mover, destino):
                                    self.stderr.write(f'CRM {alias}: el servidor no permite mover correos; se '
                                                      f'copiaron a «{cfg.correo_carpeta}» y siguen en la bandeja.')
                            except Exception as exc:  # noqa: BLE001
                                self.stderr.write(f'CRM {alias}: los correos no se pudieron mover a la carpeta '
                                                  f'«{cfg.correo_carpeta}» (quedan leídos en la bandeja): {exc}')
                    finally:
                        try:
                            imap.logout()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as exc:  # noqa: BLE001  (clave mal, servidor caído: sigue con las demás empresas)
                    self.stderr.write(f'CRM {alias}: no se pudo leer el buzón {cfg.correo_usuario} en '
                                      f'{cfg.correo_servidor}: {exc}')
                    continue
                if total or opciones.get('verbosity', 1) > 1:
                    self.stdout.write(f'CRM {alias}: {total} oportunidad(es) desde el correo.')
            finally:
                emp.restaurar(token)
