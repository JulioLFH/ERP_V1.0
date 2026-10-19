# Programa el respaldo diario de las bases PostgreSQL de Ceiba ERP en el Programador de tareas de Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo.ps1 -Destino D:\Respaldos\Ceiba
#   powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo.ps1 -Hora 21:30 -DiasConservar 60
#   powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo.ps1 -Quitar
#
# Crea (o reemplaza) la tarea "Ceiba ERP - respaldo diario" del usuario actual, que ejecuta
# scripts\respaldar_postgres.ps1 todos los días a la hora indicada (y al encender si el equipo estaba apagado).
# Use un -Destino en otro disco y copie esa carpeta fuera del equipo (nube o disco externo). Guía: docs/POSTGRESQL.md
param(
    [string]$Destino = (Join-Path (Split-Path $PSScriptRoot -Parent) 'respaldos'),
    [string]$Hora = '22:00',
    [int]$DiasConservar = 30,
    [switch]$Quitar
)
$ErrorActionPreference = 'Stop'
$nombre = 'Ceiba ERP - respaldo diario'
if ($Quitar) {
    Unregister-ScheduledTask -TaskName $nombre -Confirm:$false
    "Tarea '$nombre' eliminada."
    return
}
$script = Join-Path $PSScriptRoot 'respaldar_postgres.ps1'
$argumentos = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Destino `"$Destino`" -DiasConservar $DiasConservar"
$accion = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argumentos -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
$opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger $disparador -Settings $opciones `
    -Description 'Respaldo diario de las bases PostgreSQL de Ceiba ERP (scripts\respaldar_postgres.ps1)' -Force | Out-Null
"Tarea '$nombre' programada todos los días a las $Hora. Respaldos en: $Destino (se conservan $DiasConservar días)."
"Para probarla ahora: Start-ScheduledTask -TaskName '$nombre'"
