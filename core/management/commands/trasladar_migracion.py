"""Traslada una base migrada y revisada en local (SQLite) a la base de la nube, reemplazando sus datos.

    $env:MIGRACION_ORIGEN = "C:/.../pauno.sqlite3"     # base revisada en local
    $env:DATABASE_URL = "<URL externa de la base de Render>"
    python manage.py trasladar_migracion               # muestra lo que hará (no graba)
    python manage.py trasladar_migracion --confirmar   # reemplaza los datos

Conserva en el destino: usuarios, grupos y permisos, perfiles de usuario, verificación en dos pasos, claves de API,
la configuración de facturación electrónica y de correo, los datos de la empresa (solo se actualizan RUC y razón
social) y la bitácora. Todo lo demás se borra y se copia del origen, en bloques (rápido aunque la base esté lejos)
y en una sola transacción: si algo falla no cambia nada.
"""
import time

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, transaction
from django.db.migrations.executor import MigrationExecutor

APPS = ['core', 'contabilidad', 'finanzas', 'compras', 'ventas', 'logistica', 'inventario', 'proveedores',
        'produccion', 'activos', 'planillas']
CONSERVAR = {'core.perfilusuario', 'core.segundofactor', 'core.apitoken', 'core.intentoacceso',
             'core.facturacionconfig', 'core.correoconfig', 'core.empresa', 'core.bitacora'}
LOTE = 1000


def modelos():
    return [m for m in apps.get_models() if m._meta.app_label in APPS and m._meta.label_lower not in CONSERVAR
            and not m._meta.proxy and m._meta.managed]


class Command(BaseCommand):
    help = 'Reemplaza los datos de la base destino (DATABASE_URL) con los de la base migrada en local.'

    def add_arguments(self, parser):
        parser.add_argument('--confirmar', action='store_true', help='Sin esto solo muestra lo que haría')

    def handle(self, confirmar, **_):
        if 'origen' not in connections.databases:
            raise CommandError('Defina MIGRACION_ORIGEN con la ruta de la base migrada (sqlite).')
        destino = connections['default']
        if destino.settings_dict['NAME'] == connections['origen'].settings_dict['NAME']:
            raise CommandError('El origen y el destino son la misma base.')
        for alias in ('origen', 'default'):
            plan = MigrationExecutor(connections[alias]).migration_plan(
                MigrationExecutor(connections[alias]).loader.graph.leaf_nodes())
            if plan:
                raise CommandError(f'La base "{alias}" tiene migraciones pendientes: ejecute migrate primero.')
        lista = modelos()
        conteo = {m: m._base_manager.using('origen').count() for m in lista}
        host = destino.settings_dict.get('HOST') or destino.settings_dict['NAME']
        self.stdout.write(f'Destino: {destino.vendor} {host}')
        self.stdout.write(f'Se copiarán {sum(conteo.values()):,} registros de {len(lista)} tablas; principales:')
        for m, n in sorted(conteo.items(), key=lambda x: -x[1])[:12]:
            self.stdout.write(f'  {m._meta.label}: {n:,}')
        if not confirmar:
            self.stdout.write(self.style.WARNING('Nada se grabó. Revise y vuelva a ejecutar con --confirmar.'))
            return
        inicio = time.time()
        tipos = self._mapa_tipos()
        with transaction.atomic(using='default'):
            self._vaciar(lista)
            self.stdout.write(f'[{time.time() - inicio:5.0f}s] Datos anteriores borrados')
            for m in lista:
                if conteo[m]:
                    self._copiar(m, tipos)
                    self.stdout.write(f'[{time.time() - inicio:5.0f}s] {m._meta.label}: {conteo[m]:,}')
            self._empresa()
            self._reiniciar_secuencias(lista)
        diferencias = [m._meta.label for m in lista if m._base_manager.using('default').count() != conteo[m]]
        if diferencias:
            raise CommandError(f'Conteos distintos tras copiar: {diferencias}')
        self.stdout.write(self.style.SUCCESS(f'Traslado completo en {time.time() - inicio:.0f} s.'))

    def _mapa_tipos(self):
        """ContentType del origen -> del destino (los ids difieren entre bases)."""
        from django.contrib.contenttypes.models import ContentType
        destino = {(c.app_label, c.model): c.pk for c in ContentType.objects.using('default').all()}
        return {c.pk: destino.get((c.app_label, c.model)) for c in ContentType.objects.using('origen').all()}

    def _vaciar(self, lista):
        from core.models import PerfilUsuario
        for campo in ('almacenes', 'series'):  # los perfiles se conservan, sus almacenes y series cambian
            getattr(PerfilUsuario, campo).through.objects.using('default').all().delete()
        for m in reversed(lista):
            m._base_manager.using('default').all()._raw_delete('default')

    def _copiar(self, modelo, tipos):
        from django.conf import settings
        usuarios = [f for f in modelo._meta.concrete_fields if f.is_relation and
                    f.related_model._meta.label == settings.AUTH_USER_MODEL]
        contenido = [f for f in modelo._meta.concrete_fields if f.is_relation and
                     f.related_model._meta.label_lower == 'contenttypes.contenttype']
        for f in usuarios:
            if not f.null:
                raise CommandError(f'{modelo._meta.label}.{f.name} exige un usuario: no se puede trasladar.')
        lote = []
        for obj in modelo._base_manager.using('origen').order_by('pk').iterator(chunk_size=LOTE):
            for f in usuarios:  # los usuarios del origen (local) no existen en el destino
                setattr(obj, f.attname, None)
            for f in contenido:
                setattr(obj, f.attname, tipos[getattr(obj, f.attname)])
            obj._state.db = 'default'
            obj._state.adding = True
            lote.append(obj)
            if len(lote) >= LOTE:
                modelo._base_manager.using('default').bulk_create(lote)
                lote = []
        if lote:
            modelo._base_manager.using('default').bulk_create(lote)

    def _empresa(self):
        from core.models import Empresa
        origen = Empresa.objects.using('origen').first()
        if origen:
            Empresa.objects.using('default').update(ruc=origen.ruc, razon_social=origen.razon_social)

    def _reiniciar_secuencias(self, lista):
        conexion = connections['default']
        sql = conexion.ops.sequence_reset_sql(no_style(), lista)
        with conexion.cursor() as cursor:
            for sentencia in sql:
                cursor.execute(sentencia)
