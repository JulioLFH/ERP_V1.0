"""Configuración de Ceiba ERP — lista para desarrollo local y despliegue en la nube.

Variables de entorno (ver .env.example):
  SECRET_KEY, DEBUG, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, DATABASE_URL
"""
import json
import os
import sys
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# Versión visible en el menú del usuario; actualizarla junto con CHANGELOG.md y la etiqueta de git
ERP_NOMBRE = 'Ceiba ERP'
ERP_VERSION = '1.22.1'

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-insegura-cambiar-en-produccion')
DEBUG = os.environ.get('DEBUG', '1') == '1'

ALLOWED_HOSTS = [h.strip() for h in os.environ.get('ALLOWED_HOSTS', '*').split(',') if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]

# Render / Railway exponen el hostname público en estas variables
for var in ('RENDER_EXTERNAL_HOSTNAME', 'RAILWAY_PUBLIC_DOMAIN'):
    host = os.environ.get(var)
    if host:
        ALLOWED_HOSTS.append(host)
        CSRF_TRUSTED_ORIGINS.append(f'https://{host}')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
    'compras',
    'ventas',
    'finanzas',
    'logistica',
    'contabilidad',
    'inventario',
    'proveedores',
    'produccion',
    'activos',
    'planillas',
    'historial',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'erp.empresas.EmpresaMiddleware',  # multiempresa: activa la base de la empresa de la sesión
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.SeguridadMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'core.auditoria.AuditoriaMiddleware',
    'core.middleware.AccesoModulosMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'erp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.empresa',
            ],
        },
    },
]

WSGI_APPLICATION = 'erp.wsgi.application'

# SQLite en local; en la nube basta con definir DATABASE_URL (PostgreSQL)
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}
# Solo para trasladar una base migrada en local a la nube (manage.py trasladar_migracion)
if os.environ.get('MIGRACION_ORIGEN'):
    DATABASES['origen'] = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ['MIGRACION_ORIGEN']}

# Multiempresa (erp/empresas.py): una base de datos por empresa. EMPRESAS_EXTRA = JSON
# {"alias": {"nombre": "Razón social", "url": "postgres://…"}} o {"alias": {"nombre": "…", "base": "erp_alias"}}
# (otra base en el mismo servidor de la principal); la principal es 'default'.
# o simplemente los nombres: EMPRESAS_EXTRA = "OTRA S.A.C." (varias separadas por ';').
from erp.empresas import config_base, leer_empresas_extra  # noqa: E402

EMPRESAS = {'default': os.environ.get('EMPRESA_PRINCIPAL', 'Empresa principal')}
for _alias, _cfg in leer_empresas_extra(os.environ.get('EMPRESAS_EXTRA')).items():
    DATABASES[_alias] = config_base(_cfg, DATABASES['default'])
    EMPRESAS[_alias] = _cfg.get('nombre', _alias)
if 'test' in sys.argv[1:2]:  # una segunda empresa para las pruebas de multiempresa
    DATABASES.setdefault('empresa2', {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'empresa2.sqlite3',
                                      'TEST': {'NAME': None}})
    EMPRESAS.setdefault('empresa2', 'Empresa de prueba 2')
DATABASE_ROUTERS = ['erp.empresas.EmpresaRouter']
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                      'KEY_FUNCTION': 'erp.empresas.clave_cache'}}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
]

LANGUAGE_CODE = 'es'
TIME_ZONE = 'America/Lima'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG
                    else 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS: el navegador solo usa HTTPS con el sistema durante un año
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', '1') == '1'
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False  # el dominio de Render es compartido
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

# Política de seguridad de contenido (CSP): solo recursos propios y los CDN que usan las pantallas
CONTENT_SECURITY_POLICY = '; '.join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'self'",
    "object-src 'none'",
])

# Panel de administración de Django: en producción solo existe si se define ADMIN_URL (una ruta difícil de
# adivinar) y solo lo abren superusuarios desde las IP de ADMIN_IPS (separadas por coma; vacío = cualquiera)
ADMIN_URL = os.environ.get('ADMIN_URL', 'admin/' if DEBUG else '').strip().strip('/')
ADMIN_IPS = [ip.strip() for ip in os.environ.get('ADMIN_IPS', '').split(',') if ip.strip()]
