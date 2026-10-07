"""Ajustes locales para revisar la migración de un cliente: igual que settings.py pero con la base cliente.sqlite3.

    python manage.py runserver 8001 --settings=erp.settings_cliente

La base local del cliente no se sube al repositorio (*.sqlite3 está en .gitignore) y su nombre no va en el código:
la razón social se toma de la base (Ajustes › Empresa) o de la variable EMPRESA_PRINCIPAL.
"""
import os

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'cliente.sqlite3'}}
# igual que en la nube: una segunda empresa (base cliente_empresa2.sqlite3, se crea con migrar_empresas)
DATABASES['empresa2'] = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'cliente_empresa2.sqlite3'}
EMPRESAS = {'default': os.environ.get('EMPRESA_PRINCIPAL', 'Empresa principal'), 'empresa2': 'SEGUNDA EMPRESA'}
SESSION_COOKIE_NAME = 'ceiba_cliente_sesion'  # no se mezcla con la sesión del puerto 8000
CSRF_COOKIE_NAME = 'ceiba_cliente_csrf'
