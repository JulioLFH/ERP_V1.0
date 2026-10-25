"""Multiempresa: cada empresa tiene su propia base de datos (datos, usuarios y configuración totalmente separados).

- settings.EMPRESAS = {alias: nombre} con las bases configuradas (la principal es 'default').
- Al iniciar sesión se elige la empresa; queda en la sesión (las sesiones viven en la base principal) y el
  middleware la activa en cada petición. El enrutador manda todas las consultas a la base de la empresa activa.
- Se agrega una empresa con la variable de entorno EMPRESAS_EXTRA (JSON), con su propio servidor:
    {"empresa2": {"nombre": "Otra empresa S.A.C.", "url": "postgres://usuario:clave@host/base"}}
  o como otra base dentro del mismo servidor de la principal (sin otra clave ni otro costo):
    {"empresa2": {"nombre": "Otra empresa S.A.C.", "base": "erp_empresa2"}}
  y luego: python manage.py migrar_empresas (crea la base si falta, las tablas y el administrador en cada una).
"""
import re
from contextlib import ContextDecorator
from contextvars import ContextVar
from pathlib import Path

from django.conf import settings

_actual = ContextVar('empresa_actual', default='default')


def leer_empresas_extra(texto):
    """EMPRESAS_EXTRA: JSON {"alias": {...}} o, más simple, solo los nombres separados por ';'
    ("OTRA S.A.C.; TERCERA S.A.C." → empresa2/erp_empresa2, empresa3/erp_empresa3). Tolera comillas tipográficas
    y comillas alrededor; si no se entiende, avisa y sigue solo con la principal (no tumba el despliegue)."""
    import json
    import sys
    texto = (texto or '').strip()
    for a, b in (('“', '"'), ('”', '"'), ('‘', "'"), ('’', "'")):
        texto = texto.replace(a, b)
    if len(texto) > 1 and texto[0] == texto[-1] and texto[0] in '"\'' and texto[1:-1].strip().startswith('{'):
        texto = texto[1:-1].strip()
    if not texto:
        return {}
    if texto.startswith('{'):
        try:
            datos = json.loads(texto)
            if isinstance(datos, dict) and all(isinstance(c, dict) for c in datos.values()):
                return datos
        except ValueError:
            pass
        print('EMPRESAS_EXTRA no es un JSON válido: se ignora (solo la empresa principal)', file=sys.stderr)
        return {}
    nombres = [n.strip() for n in texto.split(';') if n.strip()]
    return {f'empresa{i}': {'nombre': n, 'base': f'erp_empresa{i}'} for i, n in enumerate(nombres, start=2)}


def config_base(cfg, principal):
    """Conexión de una empresa de EMPRESAS_EXTRA: su propia 'url' o una 'base' en el servidor de la principal."""
    import dj_database_url
    if cfg.get('url'):
        return dj_database_url.parse(cfg['url'], conn_max_age=600)
    nombre = cfg.get('base', '')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', nombre):
        raise ValueError(f'EMPRESAS_EXTRA: nombre de base no válido: {nombre!r} (minúsculas, números y _)')
    conf = {**principal, 'TEST': dict(principal.get('TEST', {}))}
    if 'sqlite' in conf['ENGINE']:
        conf['NAME'] = Path(principal['NAME']).with_name(f'{nombre}.sqlite3')
    else:
        conf['NAME'] = nombre
    return conf


def crear_base_si_falta(alias):
    """PostgreSQL: crea la base de la empresa en el servidor de la principal si aún no existe. Devuelve si la creó."""
    from django.db import connections
    conf, principal = settings.DATABASES[alias], settings.DATABASES['default']
    if 'postgresql' not in conf['ENGINE'] or (conf.get('HOST'), conf.get('PORT')) != \
            (principal.get('HOST'), principal.get('PORT')):
        return False  # SQLite se crea sola; otro servidor ya trae su base
    with connections['default'].cursor() as c:
        c.execute('SELECT 1 FROM pg_database WHERE datname = %s', [conf['NAME']])
        if c.fetchone():
            return False
        c.execute(f'CREATE DATABASE "{conf["NAME"]}"')  # nombre validado en config_base
    return True

# tablas que siempre están en la base principal (la sesión dice qué empresa usar; el registro del grupo y los
# usuarios corporativos son del grupo, no de una empresa)
APPS_PRINCIPAL = {'sessions'}
MODELOS_PRINCIPAL = {'empresagrupo', 'usuariocorporativo'}


def _de_principal(model):
    return model._meta.app_label in APPS_PRINCIPAL or (model._meta.app_label == 'core' and
                                                        model._meta.model_name in MODELOS_PRINCIPAL)


def nombre_base(alias):
    """Base de una empresa nueva en el servidor de la principal: ceiba_principal -> ceiba_<alias>."""
    principal = str(settings.DATABASES['default'].get('NAME', 'erp'))
    prefijo = Path(principal).stem.split('_')[0] if principal else 'erp'
    prefijo = re.sub(r'[^a-z0-9]', '', prefijo.lower()) or 'erp'
    return f'{prefijo}_{alias}'


def registrar_conexion(alias, base, nombre):
    """Agrega la empresa a las conexiones del proceso (sin reiniciar el servidor)."""
    from django.db import connections
    if alias not in settings.DATABASES:
        conf = config_base({'base': base}, settings.DATABASES['default'])
        settings.DATABASES[alias] = conf
        completas = connections.configure_settings({'default': dict(settings.DATABASES['default']),
                                                    alias: dict(conf)})  # exige la 'default' para completar
        connections.settings[alias] = completas[alias]
    if alias not in settings.EMPRESAS:
        settings.EMPRESAS[alias] = nombre


_SINCRONIZADO = {'hora': 0.0}


def sincronizar(forzar=False):
    """Carga las empresas creadas desde el sistema (registro en la base principal). Cada minuto como máximo."""
    import time
    if not forzar and time.monotonic() - _SINCRONIZADO['hora'] < 60:
        return
    _SINCRONIZADO['hora'] = time.monotonic()
    try:
        from core.models import EmpresaGrupo
        for e in EmpresaGrupo.objects.using('default').exclude(estado__in=['ERROR', 'INACTIVA']):
            registrar_conexion(e.alias, e.base, e.razon_social)
    except Exception:  # base principal aún sin la tabla (antes de migrar): solo las configuradas
        pass


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


class _AtomicEmpresa(ContextDecorator):
    """transaction.atomic() sin `using`: en la base de la empresa activa (no siempre en la principal). Resuelve la
    empresa al entrar (también cuando se usa como decorador) y crea un Atomic nuevo en cada uso (sin estado
    compartido entre hilos)."""

    def __init__(self, savepoint=True, durable=False):
        self.savepoint, self.durable = savepoint, durable

    def _recreate_cm(self):
        return _AtomicEmpresa(self.savepoint, self.durable)

    def __enter__(self):
        self._cm = _ORIGINAL['atomic'](using=actual(), savepoint=self.savepoint, durable=self.durable)
        return self._cm.__enter__()

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


_ORIGINAL = {}


def parchar_transacciones():
    """ALM-11: sin esto, en la segunda empresa un error a mitad de camino no deshacía lo ya grabado (el atomic()
    protegía la base principal). atomic, on_commit y set_rollback sin `using` van a la base de la empresa activa."""
    from django.db import transaction
    if _ORIGINAL:
        return
    _ORIGINAL.update(atomic=transaction.atomic, on_commit=transaction.on_commit,
                     set_rollback=transaction.set_rollback)

    def atomic(using=None, savepoint=True, durable=False):
        if callable(using):  # @transaction.atomic sin paréntesis
            return _AtomicEmpresa(savepoint, durable)(using)
        if using is None:
            return _AtomicEmpresa(savepoint, durable)
        return _ORIGINAL['atomic'](using, savepoint, durable)

    def on_commit(func, using=None, robust=False):
        return _ORIGINAL['on_commit'](func, using=using or actual(), robust=robust)

    def set_rollback(rollback, using=None):
        return _ORIGINAL['set_rollback'](rollback, using=using or actual())

    transaction.atomic, transaction.on_commit, transaction.set_rollback = atomic, on_commit, set_rollback


class EmpresaRouter:
    """Todas las consultas van a la base de la empresa activa; las sesiones, a la principal."""

    def db_for_read(self, model, **hints):
        return 'default' if _de_principal(model) else actual()

    def db_for_write(self, model, **hints):
        return 'default' if _de_principal(model) else actual()

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
        sincronizar()  # empresas creadas desde el sistema (también las creadas por otro proceso del servidor)
        alias = request.session.get('empresa', 'default') if es_multiempresa() else 'default'
        token = activar(alias)
        request.empresa_alias = actual()
        try:
            return self.get_response(request)
        finally:
            restaurar(token)
