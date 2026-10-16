"""Recordatorio diario de entregas a rendir vencidas y de cargos de tarjeta corporativa sin comprobante: correo al
rendidor (o titular) y a su jefe directo.

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
                return
        # cargos de tarjeta corporativa sin comprobante: un aviso por titular
        por_titular = {}
        for f in rd.sin_comprobante():
            por_titular.setdefault(f['m'].tarjeta.titular, []).append(f)
        for titular, filas in por_titular.items():
            usuarios, jefes = rd.usuarios_de(titular)
            vencidos = any(f['dias'] > 30 for f in filas)  # con más de 30 días también se avisa al jefe
            correos = sorted({u.email for u in usuarios + (jefes if vencidos else []) if u.email})
            if not correos:
                continue
            try:
                enviar(correos, f'Cargos de tarjeta sin comprobante: {len(filas)}', 'finanzas/correo_tarjeta.html',
                       {'titular': titular, 'filas': filas})
                enviados += 1
            except ErrorCorreo as exc:
                self.stderr.write(f'{titular}: {exc}')
                return
        # corte del mes: rendidores con borradores u observadas y aprobadores con pendientes
        corte, avisos = rd.avisos_de_corte()
        for usuario, pendientes in avisos:
            if not usuario.email:
                continue
            try:
                enviar([usuario.email], f'Corte de rendiciones el {corte:%d/%m/%Y}', 'finanzas/correo_corte.html',
                       {'corte': corte, 'pendientes': pendientes, 'usuario': usuario})
                enviados += 1
            except ErrorCorreo as exc:
                self.stderr.write(f'{usuario}: {exc}')
                return
        self.stdout.write(f'{enviados} recordatorio(s) enviados.')
