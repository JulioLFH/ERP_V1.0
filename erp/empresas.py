"""Multiempresa: cada empresa tiene su propia base de datos (datos, usuarios y configuración totalmente separados).

- settings.EMPRESAS = {alias: nombre} con las bases configuradas (la principal es 'default').
- Al iniciar sesión se elige la empresa; queda en la sesión (las sesiones viven en la base principal) y el
  middleware la activa en cada petición. El enrutador manda todas las consultas a la base de la empresa activa.
- Se agrega una empresa con la variable de entorno EMPRESAS_EXTRA (JSON):
    {"pauno2": {"nombre": "Otra empresa S.A.C.", "url": "postgres://usuario:clave@host/base"}}
  y luego: python manage.py migrar_empresas (crea las tablas y el administrador en cada base).
"""
from contextvars import ContextVar

from django.conf import settings

_actual = ContextVar('empresa_actual', default='default')

# tablas que siempre están en la base principal (la sesión dice qué empresa usar)
APPS_PRINCIPAL = {'sessions'}


def empresas():
    return getattr(settings, 'EMPRESAS', {'default': 'Empresa principal'})


def actual():
    return _actual.get()


def activar(alias):
    """Activa la empresa (alias de base de datos) para lo que resta de la petición; devuelve el token para restaurar."""
    if alias not in empresas():
        alias = 'default'
    return _actual.set(alias)


def restaurar(token):
    _actual.reset(token)


def es_multiempresa():
    return len(empresas()) > 1


def razon_social(alias):
    """Razón social registrada en la base de la empresa (o el nombre configurado)."""
    from django.core.cache import cache
    clave = f'razon_social_{alias}'
    nombre = cache.get(clave)
    if nombre is None:
        try:
            from core.models import Empresa
            e = Empresa.objects.using(alias).first()
        except Exception:  # base aún no migrada o no disponible: el nombre configurado (sin guardarlo)
            return empresas().get(alias, alias)
        nombre = e.razon_social if e else empresas()[alias]
        cache.set(clave, nombre, 300)
    return nombre


def clave_cache(key, key_prefix, version):
    """Las claves de caché se separan por empresa (ej. listas del historial, razón social)."""
    if key.startswith('razon_social_'):
        return f'{key_prefix}:{version}:{key}'
    return f'{actual()}:{key_prefix}:{version}:{key}'


class EmpresaRouter:
    """Todas las consultas van a la base de la empresa activa; las sesiones, a la principal."""

    def db_for_read(self, model, **hints):
        return 'default' if model._meta.app_label in APPS_PRINCIPAL else actual()

    def db_for_write(self, model, **hints):
        return 'default' if model._meta.app_label in APPS_PRINCIPAL else actual()

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, **hints):
        if db not in empresas():
            return None  # otras bases (ej. 'origen' de la migración) se manejan por su cuenta
        return True  # cada empresa tiene todas las tablas


class EmpresaMiddleware:
    """Activa la empresa de la sesión antes de cargar el usuario (va entre Session y Authentication)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        alias = request.session.get('empresa', 'default') if es_multiempresa() else 'default'
        token = activar(alias)
        request.empresa_alias = actual()
        try:
            return self.get_response(request)
        finally:
            restaurar(token)
