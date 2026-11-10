"""REQ-07: descarga el tipo de cambio de un periodo completo (de la fuente configurada en cada empresa).

    python manage.py cargar_tipo_cambio --periodo 202610             empresa principal
    python manage.py cargar_tipo_cambio --periodo 202610 --todas     todas las empresas del grupo
    python manage.py cargar_tipo_cambio                              el mes en curso

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

    def handle(self, *args, **opciones):
        from core import tipo_cambio
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
