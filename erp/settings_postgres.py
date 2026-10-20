"""Ceiba ERP sobre PostgreSQL local (el mismo motor que se usará en el servidor).

    python manage.py runserver 8001 --settings=erp.settings_postgres

Los datos de conexión se leen de las variables de entorno o del archivo local `.env.postgres` (no se sube al
repositorio). Cada empresa tiene su propia base en el mismo servidor PostgreSQL:
    default  -> ceiba_principal      (PG_BASE_PRINCIPAL)
    empresa2 -> ceiba_empresa2       (PG_BASE_EMPRESA2)
Guía completa: docs/POSTGRESQL.md
"""
import os

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR


def _leer_env(ruta):
    datos = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                clave, valor = linea.split('=', 1)
                datos[clave.strip()] = valor.strip()
    return datos


_ENV = _leer_env(BASE_DIR / '.env.postgres')


def _dato(clave, defecto=''):
    return os.environ.get(clave) or _ENV.get(clave) or defecto


_CONEXION = {
    'ENGINE': 'django.db.backends.postgresql',
    'USER': _dato('PG_USUARIO', 'ceiba'),
    'PASSWORD': _dato('PG_CLAVE'),
    'HOST': _dato('PG_HOST', '127.0.0.1'),
    'PORT': _dato('PG_PUERTO', '5432'),
    'CONN_MAX_AGE': 600,
    'CONN_HEALTH_CHECKS': True,
}
DATABASES = {
    'default': {**_CONEXION, 'NAME': _dato('PG_BASE_PRINCIPAL', 'ceiba_principal')},
    'empresa2': {**_CONEXION, 'NAME': _dato('PG_BASE_EMPRESA2', 'ceiba_empresa2')},
}
EMPRESAS = {'default': os.environ.get('EMPRESA_PRINCIPAL', 'Empresa principal'), 'empresa2': 'SEGUNDA EMPRESA'}


def _empresas_registradas():
    """Empresas creadas desde el sistema (Ajustes › Empresas del grupo): así migrate y los comandos las conocen."""
    import sys
    if 'test' in sys.argv[1:2]:
        return []
    try:
        import psycopg
        with psycopg.connect(dbname=DATABASES['default']['NAME'], user=_CONEXION['USER'],
                             password=_CONEXION['PASSWORD'], host=_CONEXION['HOST'], port=_CONEXION['PORT'],
                             connect_timeout=3) as conexion:
            return conexion.execute("SELECT alias, base, razon_social FROM core_empresagrupo "
                                    "WHERE estado NOT IN ('ERROR', 'INACTIVA')").fetchall()
    except Exception:  # sin servidor o sin la tabla todavía: solo las empresas configuradas
        return []


for _alias, _base, _nombre in _empresas_registradas():
    DATABASES.setdefault(_alias, {**_CONEXION, 'NAME': _base})
    EMPRESAS.setdefault(_alias, _nombre)
SESSION_COOKIE_NAME = 'ceiba_pg_sesion'  # no se mezcla con las sesiones de las bases SQLite
CSRF_COOKIE_NAME = 'ceiba_pg_csrf'
