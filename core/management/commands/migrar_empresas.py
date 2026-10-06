"""Multiempresa: aplica las migraciones y crea el administrador en la base de cada empresa configurada.

    python manage.py migrar_empresas            # todas las empresas (EMPRESAS_EXTRA + la principal)
    python manage.py migrar_empresas --solo pauno2
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand

from erp.empresas import activar, empresas, restaurar


class Command(BaseCommand):
    help = 'Migra la base de datos de cada empresa y crea su usuario administrador'

    def add_arguments(self, parser):
        parser.add_argument('--solo', default='', help='Alias de una sola empresa')

    def handle(self, *args, **opts):
        for alias, nombre in empresas().items():
            if opts['solo'] and alias != opts['solo']:
                continue
            self.stdout.write(self.style.MIGRATE_HEADING(f'Empresa {alias} ({nombre})'))
            # con la empresa activa, las migraciones de datos (plan de cuentas, conceptos…) escriben en su base
            token = activar(alias)
            try:
                call_command('migrate', database=alias, interactive=False, verbosity=opts['verbosity'])
                activar(alias)  # post_migrate vuelve a la principal
                call_command('ensure_admin')
            finally:
                restaurar(token)
