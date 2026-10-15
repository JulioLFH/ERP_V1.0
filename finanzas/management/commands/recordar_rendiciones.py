"""Recordatorio diario de entregas a rendir vencidas: correo al rendidor y a su jefe directo.

Programarlo una vez al día (cron del servidor o tarea programada): python manage.py recordar_rendiciones"""
from django.core.management.base import BaseCommand

from core.correo import ErrorCorreo, enviar
from finanzas import rendiciones as rd


class Command(BaseCommand):
    help = 'Envía recordatorios de entregas a rendir vencidas al responsable y a su jefe.'

    def handle(self, *args, **options):
        enviados = 0
        for entrega, limite in rd.vencidas():
            usuarios, jefes = rd.usuarios_de(entrega.responsable)
            correos = sorted({u.email for u in usuarios + jefes if u.email})
            if not correos:
                continue
            try:
                enviar(correos, f'Entrega a rendir vencida: {entrega.numero}', 'finanzas/correo_recordatorio.html',
                       {'e': entrega, 'limite': limite})
                enviados += 1
            except ErrorCorreo as exc:
                self.stderr.write(f'{entrega.numero}: {exc}')
                break
        self.stdout.write(f'{enviados} recordatorio(s) enviados.')
