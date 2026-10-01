"""Crea o actualiza el superusuario desde variables de entorno (útil en la nube sin consola).

  DJANGO_SUPERUSER_USERNAME  (por defecto: admin)
  DJANGO_SUPERUSER_PASSWORD  (obligatoria; si falta no hace nada)
  DJANGO_SUPERUSER_EMAIL     (opcional)
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Crea o actualiza el superusuario usando variables de entorno'

    def handle(self, *args, **opts):
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '').strip()
        if not password:
            self.stdout.write(self.style.WARNING(
                'DJANGO_SUPERUSER_PASSWORD no está definida: no se creó el usuario administrador.'))
            return
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', '').strip() or 'admin'
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()
        user, creado = get_user_model().objects.get_or_create(username=username, defaults={'email': email})
        user.is_staff = user.is_superuser = user.is_active = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f'Usuario administrador "{username}" {"creado" if creado else "actualizado"}.'))
