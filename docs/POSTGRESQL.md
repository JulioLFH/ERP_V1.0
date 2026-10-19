# Ceiba ERP sobre PostgreSQL

Guía para trabajar con PostgreSQL en local y llevar luego la base completa (tablas y datos) al servidor de la
empresa. Desde la versión 1.32.0 PostgreSQL es la base de trabajo; las bases SQLite (`cliente.sqlite3`,
`cliente_empresa2.sqlite3`) quedan como respaldo de solo consulta.

> Render ya no se usa para desplegar. El código se publica solo en GitHub; el servidor definitivo será propio (VPS).

---

## 1. Qué hay instalado

| Elemento | Valor |
|---|---|
| Motor | PostgreSQL 17 (servicio de Windows `postgresql-17`, se inicia con el equipo) |
| Servidor | `127.0.0.1`, puerto `5432` |
| Programas | `C:\Program Files\PostgreSQL\17\bin` (`psql`, `pg_dump`, `pg_restore`, `createdb`) |
| Usuario de la aplicación | `ceiba` (dueño de las bases, sin privilegios de superusuario) |
| Usuario administrador del motor | `postgres` (solo para crear usuarios y bases) |
| Base de la empresa principal | `ceiba_principal` (alias `default` en Django) |
| Base de la empresa de prueba | `ceiba_empresa2` (alias `empresa2`) |
| Ajustes de Django | `erp/settings_postgres.py` |
| Claves | archivo local `.env.postgres` (no se sube a GitHub) |

### El archivo `.env.postgres`

Está en la raíz del proyecto y **nunca** se sube al repositorio (`.gitignore` excluye `.env.*`). Contiene:

```
PG_SUPERUSUARIO=postgres
PG_SUPERCLAVE=<clave del usuario postgres>
PG_HOST=127.0.0.1
PG_PUERTO=5432
PG_USUARIO=ceiba
PG_CLAVE=<clave del usuario ceiba>
PG_BASE_PRINCIPAL=ceiba_principal
PG_BASE_EMPRESA2=ceiba_empresa2
```

Guárdelo también en un lugar seguro (gestor de contraseñas). Cualquier variable de entorno con el mismo nombre
tiene prioridad sobre el archivo.

Use `127.0.0.1` y no `localhost`: en este equipo la conexión por el nombre `localhost` falla.

---

## 2. Uso diario

Iniciar el ERP sobre PostgreSQL (puerto 8002):

```bash
.venv\Scripts\python.exe manage.py runserver 8002 --settings=erp.settings_postgres
```

En la app de Claude también está la configuración `erp-postgres` en `.claude/launch.json`.

Después de actualizar el código, aplique las migraciones **en las dos empresas**:

```bash
.venv\Scripts\python.exe manage.py migrate --database default --settings=erp.settings_postgres
```

```bash
.venv\Scripts\python.exe manage.py migrate --database empresa2 --settings=erp.settings_postgres
```

`migrate` sin `--database` solo actualiza la empresa principal. En el servidor se usa `migrar_empresas`, que
recorre todas.

Abrir una consola SQL:

```bash
"C:\Program Files\PostgreSQL\17\bin\psql.exe" -h 127.0.0.1 -U ceiba -d ceiba_principal
```

---

## 3. Cómo se migró de SQLite a PostgreSQL

Se hizo con el comando `copiar_a_postgres` (`core/management/commands/copiar_a_postgres.py`). Copia **todas las
tablas y todos los registros con los mismos ids**: usuarios, grupos y permisos, configuración, bitácora,
sustentos, historial del sistema anterior y datos de todos los módulos. Solo deja fuera las sesiones abiertas.

> **Cuidado:** desde ahora los datos nuevos se registran en PostgreSQL. Volver a ejecutar la copia con
> `--confirmar` **reemplaza** la base PostgreSQL con el contenido del SQLite, que ya no recibe cambios. Hágalo
> solo si quiere descartar lo registrado en PostgreSQL, y siempre después de un respaldo (sección 4).

Pasos (para repetirlo, por ejemplo con una base SQLite más reciente):

1. Crear el usuario y las bases (una sola vez, como `postgres`):
   ```sql
   CREATE ROLE ceiba LOGIN PASSWORD '<clave>';
   CREATE DATABASE ceiba_principal OWNER ceiba ENCODING 'UTF8' TEMPLATE template0;
   CREATE DATABASE ceiba_empresa2 OWNER ceiba ENCODING 'UTF8' TEMPLATE template0;
   ```
2. Dejar la base SQLite de origen al día:
   ```bash
   .venv\Scripts\python.exe manage.py migrate --settings=erp.settings_cliente
   ```
3. Crear las tablas en PostgreSQL con las migraciones (sección 2).
4. Ensayar la copia. No graba nada: muestra cuántos registros pasará y si hay datos que PostgreSQL no acepta.
   ```bash
   .venv\Scripts\python.exe manage.py copiar_a_postgres --origen cliente.sqlite3 --base default --settings=erp.settings_postgres
   ```
5. Copiar de verdad:
   ```bash
   .venv\Scripts\python.exe manage.py copiar_a_postgres --origen cliente.sqlite3 --base default --confirmar --settings=erp.settings_postgres
   ```
6. Repetir 4 y 5 para la empresa de prueba con `--origen cliente_empresa2.sqlite3 --base empresa2`.

Qué hace la copia:

- Vacía la base destino y copia todo **en una sola transacción**: si algo falla, el destino queda como estaba.
- Usa `COPY` de PostgreSQL (rápido aun con millones de registros) y valida las claves foráneas al final.
- Reinicia las secuencias para que los documentos nuevos sigan la numeración.
- Al terminar **verifica** cada tabla: misma cantidad de registros y misma suma de cada campo de importe.
- El origen SQLite solo se lee; nunca se modifica.

PostgreSQL es más estricto que SQLite: no acepta textos más largos que su campo ni caracteres nulos. El ensayo
los lista; con `--recortar` se ajustan (y quedan informados). En esta base no hubo ninguno.

### Resultado de la migración (19/10/2026)

| Empresa | Origen SQLite | Registros | Tablas | Tiempo | Verificación |
|---|---|---|---|---|---|
| Principal (`ceiba_principal`) | `cliente.sqlite3` (984 MB) | 2 447 945 | 174 | 188 s | cantidades e importes iguales |
| Prueba (`ceiba_empresa2`) | `cliente_empresa2.sqlite3` (3 MB) | 1 287 | 174 | 5 s | cantidades e importes iguales |

En PostgreSQL la base principal ocupa 909 MB y su respaldo `.dump`, 51,5 MB.

Comprobaciones posteriores:

- Las **349 pruebas automáticas** pasan sobre PostgreSQL (y sobre SQLite).
- Recorrido de **299 pantallas** de los 14 módulos con los datos reales en PostgreSQL (sobre una copia
  desechable): ninguna con error.
- **Respaldo y restauración**: el `.dump` restaurado en una base nueva tiene las mismas 176 tablas y las mismas
  2 448 097 filas que la original (incluye las tablas internas de Django).

---

## 4. Respaldos

Respaldar las dos bases (un archivo `.dump` por empresa en `respaldos\`, que no se sube a GitHub; borra los de
más de 30 días):

```bash
powershell -ExecutionPolicy Bypass -File scripts\respaldar_postgres.ps1
```

Para guardarlos en otro disco o conservarlos más tiempo:

```bash
powershell -ExecutionPolicy Bypass -File scripts\respaldar_postgres.ps1 -Destino D:\Respaldos\Ceiba -DiasConservar 90
```

El `.dump` (formato personalizado de `pg_dump`) contiene tablas, datos, índices, restricciones y secuencias: es
todo lo necesario para levantar la base en otro servidor.

**Respaldo automático diario**: ejecute una sola vez (crea la tarea «Ceiba ERP - respaldo diario» en el
Programador de tareas de Windows; si el equipo estaba apagado a esa hora, se ejecuta al encender):

```bash
powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo.ps1 -Destino D:\Respaldos\Ceiba -Hora 22:00
```

Para quitarla: `scripts\programar_respaldo.ps1 -Quitar`. Copie además los respaldos fuera del equipo (nube o disco
externo): un respaldo en el mismo disco no protege de una falla del disco.

**Probar un respaldo** (hágalo al menos una vez al mes; un respaldo que no se probó no es un respaldo):

```bash
powershell -ExecutionPolicy Bypass -File scripts\restaurar_postgres.ps1 -Archivo respaldos\ceiba_principal_AAAAMMDD_HHMM.dump -Base ceiba_principal_prueba -Confirmar
```

Restaure siempre en una base nueva (`..._prueba`), nunca sobre la base en uso.

---

## 5. Llevar la base al servidor (VPS)

La base viaja **completa** (tablas y datos) en el `.dump`. En el servidor no hace falta correr la copia desde
SQLite.

1. **Instalar PostgreSQL 17** en el servidor (Ubuntu: `sudo apt install postgresql-17`). Use la misma versión
   mayor o una más nueva.
2. **Crear el usuario y las bases** en el servidor, con una clave nueva y distinta de la local:
   ```sql
   CREATE ROLE ceiba LOGIN PASSWORD '<clave del servidor>';
   CREATE DATABASE ceiba_principal OWNER ceiba ENCODING 'UTF8' TEMPLATE template0;
   CREATE DATABASE ceiba_empresa2 OWNER ceiba ENCODING 'UTF8' TEMPLATE template0;
   ```
3. **Congelar los datos** locales: avise a los usuarios y detenga el ERP local para que nadie registre nada
   durante el traslado.
4. **Respaldar** en local (sección 4) y copiar los `.dump` al servidor (por ejemplo con `scp`).
5. **Restaurar** en el servidor:
   ```bash
   pg_restore -h 127.0.0.1 -U ceiba -d ceiba_principal --clean --if-exists --no-owner --exit-on-error --jobs 4 ceiba_principal_AAAAMMDD_HHMM.dump
   ```
6. **Configurar el ERP del servidor** con variables de entorno (no con `settings_postgres.py`, que es solo para
   local):
   ```
   DATABASE_URL=postgres://ceiba:<clave>@127.0.0.1:5432/ceiba_principal
   EMPRESAS_EXTRA={"empresa2": {"nombre": "SEGUNDA EMPRESA", "base": "ceiba_empresa2"}}
   SECRET_KEY=<clave larga y aleatoria, distinta de la local>
   DEBUG=0
   ALLOWED_HOSTS=erp.miempresa.pe
   CSRF_TRUSTED_ORIGINS=https://erp.miempresa.pe
   ```
   Cada empresa nueva se agrega en `EMPRESAS_EXTRA` con su propia base (`ceiba_empresa3`, …).
7. **Aplicar migraciones** de la versión instalada en todas las empresas:
   ```bash
   python manage.py migrar_empresas
   ```
8. **Comprobar**: ingresar con un usuario real, abrir los módulos principales y comparar el balance de
   comprobación y el stock valorizado con los del equipo local.
9. **Programar respaldos diarios** en el servidor (`pg_dump` con `cron`) y copiarlos fuera del servidor.

Recomendaciones para el servidor:

- PostgreSQL escuchando solo en `127.0.0.1` (la aplicación está en el mismo servidor); no abrir el puerto 5432
  a internet.
- Si la base va en un servidor aparte, abrir el 5432 solo a la IP del servidor web y exigir SSL.
- Ajustes iniciales para 8 GB de RAM: `shared_buffers = 2GB`, `effective_cache_size = 6GB`, `work_mem = 32MB`,
  `maintenance_work_mem = 512MB`.

---

## 6. Pruebas automáticas

Las pruebas siguen corriendo sobre SQLite (rápidas, sin servidor):

```bash
.venv\Scripts\python.exe manage.py test
```

Para comprobar que todo funciona también en PostgreSQL (recomendado antes de cada versión):

```bash
.venv\Scripts\python.exe manage.py test --noinput --settings=erp.settings_postgres
```

Crea y borra sus propias bases temporales (`test_ceiba_principal`, `test_ceiba_empresa2`); no toca los datos. Para
eso el usuario `ceiba` tiene el permiso `CREATEDB` (en el servidor de producción no hace falta darlo).

Además, **en cada subida a GitHub** las pruebas corren solas sobre PostgreSQL 17 (`.github/workflows/pruebas.yml`);
el resultado se ve en la pestaña *Actions* del repositorio.

## 6b. Monitoreo

- `http://<servidor>/salud/` responde `{"estado": "ok", ...}` (código 200) si cada base contesta y no tiene
  migraciones pendientes; si no, código 503. Configure un monitor de caídas (por ejemplo UptimeRobot) contra esa
  dirección.
- Cada error del sistema queda en **Ajustes › Incidencias y errores** (agrupado si se repite) y se avisa por correo
  a los administradores con correo registrado, como máximo una vez por hora por cada error.
- Los usuarios reportan problemas desde su menú › **Reportar un problema**.
- Los errores también se escriben en `logs/errores.log` (5 MB por archivo, 10 archivos; carpeta configurable con
  `CARPETA_LOGS`).

---

## 7. Problemas frecuentes

| Mensaje | Causa y solución |
|---|---|
| `failed to resolve host 'localhost'` | Use `PG_HOST=127.0.0.1` en `.env.postgres`. |
| `password authentication failed for user "ceiba"` | Clave distinta en `.env.postgres`. Cámbiela como `postgres`: `ALTER ROLE ceiba PASSWORD '<nueva>';` |
| `connection refused` | El servicio está detenido: `Start-Service postgresql-17` (PowerShell como administrador). |
| `La base … tiene migraciones pendientes` (copia) | Ejecute `migrate` en esa base antes de copiar. |
| `Datos que PostgreSQL no acepta tal cual` (copia) | Textos más largos que su campo o caracteres nulos: corríjalos o use `--recortar`. |
| La empresa de prueba no muestra lo nuevo | Faltó `migrate --database empresa2`. |
