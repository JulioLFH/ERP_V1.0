# Restaura un respaldo .dump de Ceiba ERP en una base PostgreSQL (la crea si no existe).
#
#   powershell -ExecutionPolicy Bypass -File scripts\restaurar_postgres.ps1 -Archivo respaldos\ceiba_principal_20261019_1800.dump -Base ceiba_principal_prueba
#   ... -Confirmar      # sin esto solo muestra lo que hará
#
# ATENCIÓN: reemplaza el contenido de la base indicada. Para probar un respaldo use una base nueva
# (por ejemplo ceiba_principal_prueba) y nunca la base en uso. Lee la conexión de .env.postgres.
# Guía: docs/POSTGRESQL.md
param(
    [Parameter(Mandatory = $true)][string]$Archivo,
    [Parameter(Mandatory = $true)][string]$Base,
    [switch]$Confirmar,
    [string]$BinPostgres = 'C:\Program Files\PostgreSQL\17\bin'
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Archivo)) { throw "No existe el respaldo $Archivo" }
if ($Base -notmatch '^[a-z][a-z0-9_]{0,62}$') { throw 'Nombre de base no válido (minúsculas, números y _)' }
$raiz = Split-Path $PSScriptRoot -Parent
$env_ = @{}
foreach ($linea in [IO.File]::ReadAllLines((Join-Path $raiz '.env.postgres'))) {
    if ($linea -match '^\s*([A-Z_0-9]+)\s*=\s*(.*)$') { $env_[$Matches[1]] = $Matches[2].Trim() }
}
"Respaldo: $Archivo ({0:N1} MB)" -f ((Get-Item $Archivo).Length / 1MB)
"Destino : base $Base en $($env_['PG_HOST']):$($env_['PG_PUERTO']) (se reemplaza su contenido)"
if (-not $Confirmar) { 'Nada se hizo. Vuelva a ejecutar con -Confirmar.'; return }
$comunes = @('--host', $env_['PG_HOST'], '--port', $env_['PG_PUERTO'], '--username', $env_['PG_USUARIO'])
$env:PGPASSWORD = $env_['PG_CLAVE']
try {
    $existe = & (Join-Path $BinPostgres 'psql.exe') @comunes --dbname postgres --tuples-only --no-align `
        --command "SELECT 1 FROM pg_database WHERE datname = '$Base'"
    if (-not $existe) {
        & (Join-Path $BinPostgres 'createdb.exe') @comunes --encoding UTF8 --template template0 $Base
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear la base $Base" }
        "Base $Base creada"
    }
    & (Join-Path $BinPostgres 'pg_restore.exe') @comunes --dbname $Base --clean --if-exists --no-owner `
        --exit-on-error --jobs 4 $Archivo
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore terminó con errores' }
    "Restaurado en $Base"
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
