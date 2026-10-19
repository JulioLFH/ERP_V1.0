# Respaldo de las bases PostgreSQL de Ceiba ERP (formato personalizado de pg_dump: tablas, datos, índices y secuencias).
#
#   powershell -ExecutionPolicy Bypass -File scripts\respaldar_postgres.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\respaldar_postgres.ps1 -Destino D:\Respaldos -DiasConservar 60
#
# Lee usuario, clave, servidor y bases de .env.postgres. Deja un archivo .dump por empresa en la carpeta
# respaldos\ (no se sube a GitHub) y borra los respaldos de esa carpeta con más de -DiasConservar días.
# Guía: docs/POSTGRESQL.md
param(
    [string]$Destino = (Join-Path $PSScriptRoot '..\respaldos'),
    [int]$DiasConservar = 30,
    [string]$BinPostgres = 'C:\Program Files\PostgreSQL\17\bin'
)
$ErrorActionPreference = 'Stop'
$raiz = Split-Path $PSScriptRoot -Parent
$env_ = @{}
foreach ($linea in [IO.File]::ReadAllLines((Join-Path $raiz '.env.postgres'))) {
    if ($linea -match '^\s*([A-Z_0-9]+)\s*=\s*(.*)$') { $env_[$Matches[1]] = $Matches[2].Trim() }
}
$bases = @($env_['PG_BASE_PRINCIPAL'], $env_['PG_BASE_EMPRESA2']) | Where-Object { $_ }
New-Item -ItemType Directory -Force $Destino | Out-Null
$sello = Get-Date -Format 'yyyyMMdd_HHmm'
$env:PGPASSWORD = $env_['PG_CLAVE']
try {
    foreach ($base in $bases) {
        $archivo = Join-Path $Destino "${base}_$sello.dump"
        & (Join-Path $BinPostgres 'pg_dump.exe') --host $env_['PG_HOST'] --port $env_['PG_PUERTO'] `
            --username $env_['PG_USUARIO'] --format custom --compress 6 --no-owner --file $archivo $base
        if ($LASTEXITCODE -ne 0) { throw "pg_dump falló para $base" }
        '{0}: {1:N1} MB' -f $archivo, ((Get-Item $archivo).Length / 1MB)
    }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
Get-ChildItem $Destino -Filter '*.dump' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$DiasConservar) } |
    ForEach-Object { Remove-Item $_.FullName; "Respaldo antiguo eliminado: $($_.Name)" }
