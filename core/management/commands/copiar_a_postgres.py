"""Copia completa de una base SQLite de Ceiba ERP a una base PostgreSQL: todas las tablas y todos los registros,
con los mismos ids (usuarios, permisos, configuración, bitácora y sustentos incluidos).

    python manage.py migrate --database default --settings=erp.settings_postgres        # crea las tablas
    python manage.py copiar_a_postgres --origen cliente.sqlite3 --base default --settings=erp.settings_postgres
    python manage.py copiar_a_postgres --origen cliente.sqlite3 --base default --confirmar --settings=...

Sin --confirmar solo muestra lo que hará. Con --confirmar vacía la base destino y copia todo en una sola
transacción: si algo falla, el destino queda como estaba. Al final compara, tabla por tabla, la cantidad de
registros y la suma de los importes. El origen solo se lee: nunca se modifica.

PostgreSQL es más estricto que SQLite: si un texto supera el largo de su campo o trae caracteres nulos, el comando
lo informa y se detiene; con --recortar los ajusta (y lista cada campo afectado). Guía: docs/POSTGRESQL.md
"""
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, models, transaction
from django.db.migrations.executor import MigrationExecutor

ORIGEN = '_origen_sqlite'
NO_COPIAR = {'sessions.session'}  # las sesiones se vuelven a iniciar


def modelos():
    """Todos los modelos con tabla propia, incluidas las tablas intermedias de los campos de muchos a muchos."""
    salida = []
    for m in apps.get_models(include_auto_created=True):
        if m._meta.proxy or not m._meta.managed or m._meta.label_lower in NO_COPIAR:
            continue
        salida.append(m)
    return salida


class Command(BaseCommand):
    help = 'Copia todas las tablas y registros de una base SQLite a una base PostgreSQL (mismos ids).'

    def add_arguments(self, parser):
        parser.add_argument('--origen', required=True, help='Ruta de la base SQLite (por ejemplo cliente.sqlite3)')
        parser.add_argument('--base', default='default', help='Alias de la base PostgreSQL destino (default, empresa2…)')
        parser.add_argument('--confirmar', action='store_true', help='Sin esto solo muestra lo que haría')
        parser.add_argument('--recortar', action='store_true',
                            help='Recorta los textos más largos que su campo y quita caracteres nulos')

    def handle(self, origen, base, confirmar, recortar, **_):
        ruta = Path(origen).resolve()
        if not ruta.exists():
            raise CommandError(f'No existe la base de origen: {ruta}')
        if base not in connections.databases:
            raise CommandError(f'La base "{base}" no está configurada (use --settings=erp.settings_postgres).')
        destino = connections[base]
        if destino.vendor != 'postgresql':
            raise CommandError(f'La base "{base}" no es PostgreSQL sino {destino.vendor}.')
        connections.settings[ORIGEN] = connections.configure_settings({  # exige una 'default' para completar
            'default': dict(connections.settings['default']),
            ORIGEN: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(ruta)}})[ORIGEN]
        for alias, nombre in ((ORIGEN, 'origen'), (base, 'destino')):
            ejecutor = MigrationExecutor(connections[alias])
            if ejecutor.migration_plan(ejecutor.loader.graph.leaf_nodes()):
                raise CommandError(f'La base {nombre} tiene migraciones pendientes: ejecute migrate en ella primero.')

        lista = modelos()
        conteo = {m: m._base_manager.using(ORIGEN).count() for m in lista}
        self.stdout.write(f'Origen : SQLite {ruta} ({ruta.stat().st_size / 1048576:,.0f} MB)')
        self.stdout.write(f'Destino: PostgreSQL {destino.settings_dict["HOST"]}:{destino.settings_dict["PORT"]} '
                          f'base {destino.settings_dict["NAME"]}')
        self.stdout.write(f'{sum(conteo.values()):,} registros en {len(lista)} tablas; las más grandes:')
        for m, n in sorted(conteo.items(), key=lambda x: -x[1])[:10]:
            self.stdout.write(f'  {m._meta.db_table}: {n:,}')
        problemas = self._revisar(lista)
        if problemas:
            self.stdout.write(self.style.WARNING('Datos que PostgreSQL no acepta tal cual:'))
            for texto in problemas:
                self.stdout.write(f'  {texto}')
            if not recortar:
                raise CommandError('Corrija esos datos en el origen o vuelva a ejecutar con --recortar.')
        if not confirmar:
            self.stdout.write(self.style.WARNING('Nada se grabó. Revise y vuelva a ejecutar con --confirmar.'))
            return

        inicio = time.time()
        with transaction.atomic(using=base):
            with destino.cursor() as c:
                c.execute('SET CONSTRAINTS ALL DEFERRED')  # las claves foráneas se validan al final
                tablas = ', '.join(f'"{m._meta.db_table}"' for m in lista)
                c.execute(f'TRUNCATE {tablas} RESTART IDENTITY CASCADE')
            self.stdout.write(f'[{time.time() - inicio:6.0f} s] Base destino vaciada')
            for m in sorted(lista, key=lambda m: conteo[m]):
                if conteo[m]:
                    self._copiar(m, destino, recortar)
                    self.stdout.write(f'[{time.time() - inicio:6.0f} s] {m._meta.db_table}: {conteo[m]:,}')
            with destino.cursor() as c:
                for sentencia in destino.ops.sequence_reset_sql(no_style(), lista):
                    c.execute(sentencia)
        self.stdout.write(f'[{time.time() - inicio:6.0f} s] Copia grabada; verificando…')
        self._verificar(lista, conteo, base)
        self.stdout.write(self.style.SUCCESS(f'Copia completa y verificada en {time.time() - inicio:,.0f} s.'))

    # ------------------------------------------------------------------ revisión previa
    def _revisar(self, lista):
        """Textos más largos que su campo y caracteres nulos (SQLite los acepta; PostgreSQL no)."""
        problemas = []
        conexion = connections[ORIGEN]
        with conexion.cursor() as c:
            for m in lista:
                tabla = m._meta.db_table
                for f in m._meta.concrete_fields:
                    if not isinstance(f, (models.CharField, models.TextField)) or f.is_relation:
                        continue
                    col = conexion.ops.quote_name(f.column)
                    if isinstance(f, models.CharField) and f.max_length:
                        c.execute(f'SELECT COUNT(*), MAX(LENGTH({col})) FROM "{tabla}" WHERE LENGTH({col}) > %s',
                                  [f.max_length])
                        n, maximo = c.fetchone()
                        if n:
                            problemas.append(f'{tabla}.{f.column}: {n} valor(es) de hasta {maximo} caracteres '
                                             f'(máximo {f.max_length})')
                    c.execute(f"SELECT COUNT(*) FROM \"{tabla}\" WHERE INSTR({col}, CHAR(0)) > 0")
                    n = c.fetchone()[0]
                    if n:
                        problemas.append(f'{tabla}.{f.column}: {n} valor(es) con caracteres nulos')
        return problemas

    # ------------------------------------------------------------------ copia
    def _copiar(self, modelo, destino, recortar):
        from psycopg.types.json import Jsonb
        campos = modelo._meta.concrete_fields
        json = [i for i, f in enumerate(campos) if isinstance(f, models.JSONField)]
        textos = [(i, f.max_length if isinstance(f, models.CharField) else None) for i, f in enumerate(campos)
                  if isinstance(f, (models.CharField, models.TextField)) and not f.is_relation]
        columnas = ', '.join(destino.ops.quote_name(f.column) for f in campos)
        filas = modelo._base_manager.using(ORIGEN).order_by().values_list(*[f.attname for f in campos])
        cursor = destino.connection.cursor()  # cursor de psycopg (COPY: mucho más rápido que INSERT)
        with cursor.copy(f'COPY "{modelo._meta.db_table}" ({columnas}) FROM STDIN') as copia:
            for fila in filas.iterator(chunk_size=5000):
                if json or (recortar and textos):
                    fila = list(fila)
                    for i in json:
                        if fila[i] is not None:
                            fila[i] = Jsonb(fila[i])
                    if recortar:
                        for i, largo in textos:
                            v = fila[i]
                            if isinstance(v, str):
                                v = v.replace('\x00', '')
                                fila[i] = v[:largo] if largo else v
                copia.write_row(fila)

    # ------------------------------------------------------------------ verificación
    def _verificar(self, lista, conteo, base):
        """Misma cantidad de registros y misma suma de cada campo de importe en cada tabla."""
        errores = []
        for m in lista:
            n = m._base_manager.using(base).count()
            if n != conteo[m]:
                errores.append(f'{m._meta.db_table}: {conteo[m]:,} en el origen y {n:,} en el destino')
                continue
            decimales = [f for f in m._meta.concrete_fields if isinstance(f, models.DecimalField)]
            if not decimales or not n:
                continue
            sumas = {f.attname: models.Sum(f.attname) for f in decimales}
            o = m._base_manager.using(ORIGEN).aggregate(**sumas)
            d = m._base_manager.using(base).aggregate(**sumas)
            for f in decimales:
                a, b = o[f.attname] or Decimal('0'), d[f.attname] or Decimal('0')
                tolerancia = Decimal(1).scaleb(-f.decimal_places) * n  # redondeo de SQLite (guarda decimales como real)
                if abs(a - b) > tolerancia:
                    errores.append(f'{m._meta.db_table}.{f.column}: suma {a} en el origen y {b} en el destino')
        if errores:
            for e in errores:
                self.stdout.write(self.style.ERROR(f'  {e}'))
            raise CommandError('La verificación encontró diferencias (detalle arriba).')
        self.stdout.write(f'Verificado: {len(lista)} tablas con la misma cantidad de registros y los mismos importes.')
