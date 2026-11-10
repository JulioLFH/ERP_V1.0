"""REQ-03: crea o cambia la contraseña de un operador de la plataforma Ceiba (consola /plataforma/).

    python manage.py operador_plataforma ceiba --settings=erp.settings_postgres
    python manage.py operador_plataforma ceiba --desactivar --settings=erp.settings_postgres

La contraseña se pide en pantalla (no queda en el historial de comandos)."""
import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Crea un operador de la plataforma Ceiba o cambia su contraseña.'

    def add_arguments(self, parser):
        parser.add_argument('usuario')
        parser.add_argument('--nombre', default='')
        parser.add_argument('--desactivar', action='store_true', help='Le quita el acceso a la consola')

    def handle(self, *args, usuario, nombre, desactivar, **opciones):
        from core.models import OperadorPlataforma
        op = OperadorPlataforma.objects.using('default').filter(usuario__iexact=usuario).first()
        if desactivar:
            if op is None:
                raise CommandError(f'No existe el operador {usuario}.')
            op.activo = False
            op.save(using='default')
            self.stdout.write(f'Operador {op.usuario} desactivado.')
            return
        clave = getpass.getpass('Contraseña nueva: ')
        if clave != getpass.getpass('Repita la contraseña: '):
            raise CommandError('Las contraseñas no coinciden.')
        try:
            validate_password(clave)
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages))
        creado = op is None
        op = op or OperadorPlataforma(usuario=usuario)
        op.nombre = nombre or op.nombre
        op.activo = True
        op.fijar_clave(clave)
        op.save(using='default')
        self.stdout.write(f'Operador {op.usuario} {"creado" if creado else "actualizado"}. Ingresa en /plataforma/.')
