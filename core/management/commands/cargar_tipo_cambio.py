"""REQ-07: descarga el tipo de cambio de un periodo completo (de la fuente configurada en cada empresa).

    python manage.py cargar_tipo_cambio --periodo 202610             empresa principal
    python manage.py cargar_tipo_cambio --periodo 202610 --todas     todas las empresas del grupo
    python manage.py cargar_tipo_cambio                              el mes en curso
    python manage.py cargar_tipo_cambio --diario --todas             v1.116.8: solo los días que faltan (servir.py
                                                                     lo lanza cada hora)

Sirve para una tarea programada diaria (por ejemplo, el mes en curso para todas las empresas)."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from erp import empresas as reg


class Command(BaseCommand):
    help = 'Descarga el tipo de cambio de un periodo AAAAMM (por defecto el mes en curso).'

    def add_arguments(self, parser):
        parser.add_argument('--periodo', default='', help='AAAAMM (por defecto el mes en curso)')
        parser.add_argument('--todas', action='store_true', help='En todas las empresas del grupo')
        parser.add_argument('--reemplazar-manual', action='store_true',
                            help='Reemplazar también los días registrados a mano')
        parser.add_argument('--diario', action='store_true',
                            help='Solo los días que faltan del mes en curso, consultando cada día una vez para todas '
                                 'las empresas (carga automática de cada hora)')

    def handle(self, *args, **opciones):
        from core import tipo_cambio
        if opciones['diario']:
            return self.diario(tipo_cambio, opciones['todas'])
        periodo = (opciones['periodo'] or timezone.localdate().strftime('%Y%m')).replace('-', '')
        if not (len(periodo) == 6 and periodo.isdigit() and 1 <= int(periodo[4:]) <= 12):
            raise CommandError('Periodo no válido: use AAAAMM, por ejemplo 202610.')
        if periodo > timezone.localdate().strftime('%Y%m'):
            raise CommandError('El periodo aún no empieza.')
        reg.sincronizar(forzar=True)
        aliases = list(reg.empresas()) if opciones['todas'] else ['default']
        errores = 0
        for alias in aliases:
            token = reg.activar(alias)
            try:
                r = tipo_cambio.descargar_periodo(periodo, reemplazar_manual=opciones['reemplazar_manual'])
            finally:
                reg.restaurar(token)
            linea = (f'{reg.razon_social(alias)}: {r["ok"]} día(s) de {"/".join(sorted(r["fuentes"])) or "—"}, '
                     f'{len(r["sin_publicar"])} sin publicación')
            if r['error']:
                errores += 1
                self.stderr.write(f'{linea}. {r["error"]}')
            else:
                self.stdout.write(linea)
        if errores:
            raise CommandError(f'{errores} empresa(s) no pudieron descargar el periodo completo.')

    def diario(self, tipo_cambio, todas):
        """v1.116.8: escribe solo lo que cargó o el error (cada hora, sin nada que hacer, no escribe nada)."""
        reg.sincronizar(forzar=True)
        memo = {}
        for alias in (list(reg.empresas()) if todas else ['default']):
            token = reg.activar(alias)
            try:
                r = tipo_cambio.cargar_pendientes(memo)
            except Exception as exc:  # una empresa con la base caída no detiene a las demás
                r = {'ok': 0, 'faltan': 0, 'error': f'{type(exc).__name__}: {exc}'}
            finally:
                reg.restaurar(token)
            if r['ok'] or r['error']:
                linea = f'Tipo de cambio · {reg.razon_social(alias)}: {r["ok"]} de {r["faltan"]} día(s) cargados'
                (self.stderr if r['error'] else self.stdout).write(f'{linea}. {r["error"]}' if r['error'] else linea)
