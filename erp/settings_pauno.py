"""Ajustes locales para revisar la migración de Pauno: igual que settings.py pero con la base pauno.sqlite3.

    python manage.py runserver 8001 --settings=erp.settings_pauno
"""
from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'pauno.sqlite3'}}
# igual que en la nube: una segunda empresa (base pauno_empresa2.sqlite3, se crea con migrar_empresas)
DATABASES['empresa2'] = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'pauno_empresa2.sqlite3'}
EMPRESAS = {'default': 'PRODUCTORA DE ALIMENTOS UNO S.A.C.', 'empresa2': 'SEGUNDA EMPRESA'}
SESSION_COOKIE_NAME = 'ceiba_pauno_sesion'  # no se mezcla con la sesión del puerto 8000
CSRF_COOKIE_NAME = 'ceiba_pauno_csrf'
