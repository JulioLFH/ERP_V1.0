"""Lee el buzón comercial (ventas@…) y crea una oportunidad por cada correo nuevo (CRM-09), en cada empresa con el
buzón configurado en CRM › Configuración. Programarlo cada pocos minutos. Los mensajes procesados se marcan leídos.
v1.117.10: servir.py lo lanza cada 5 minutos. Una empresa con la clave mal no frena a las demás, y solo escribe en el
registro cuando entró algún correo o hubo un error (con -v 2, también cuando no hubo nada)."""
import email
import imaplib

from django.core.management.base import BaseCommand

from erp import empresas as emp


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
                total = 0
                try:
                    imap = imaplib.IMAP4_SSL(cfg.correo_servidor, cfg.correo_puerto or 993, timeout=60)
                    try:
                        imap.login(cfg.correo_usuario, cfg.correo_clave)
                        imap.select('INBOX')
                        _, ids = imap.search(None, 'UNSEEN')
                        for num in ids[0].split():
                            _, partes = imap.fetch(num, '(RFC822)')
                            try:
                                if procesar_correo(email.message_from_bytes(partes[0][1])) is not None:
                                    total += 1
                            except Exception as exc:  # noqa: BLE001  (un correo raro no frena a los siguientes)
                                self.stderr.write(f'CRM {alias}: un correo no se pudo convertir en oportunidad '
                                                  f'(queda leído en el buzón): {exc}')
                            imap.store(num, '+FLAGS', '\\Seen')
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
