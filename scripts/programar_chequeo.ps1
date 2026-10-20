# Programa el chequeo del servicio cada 5 minutos (alimenta la página /estado/ y la disponibilidad del mes).
#
#   powershell -ExecutionPolicy Bypass -File scripts\programar_chequeo.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\programar_chequeo.ps1 -Minutos 10 -SinAviso
#   powershell -ExecutionPolicy Bypass -File scripts\programar_chequeo.ps1 -Quitar
#
# Crea (o reemplaza) la tarea "Ceiba ERP - chequeo del servicio" del usuario actual, que ejecuta
# manage.py chequear_servicio --avisar (avisa por correo a los administradores si hay una caída). Si respalda en otra
# carpeta, defina la variable de entorno CARPETA_RESPALDOS para que el chequeo vea el último respaldo.
param(
    [int]$Minutos = 5,
    [string]$Settings = 'erp.settings_postgres',
    [switch]$SinAviso,
    [switch]$Quitar
)
$ErrorActionPreference = 'Stop'
$nombre = 'Ceiba ERP - chequeo del servicio'
if ($Quitar) {
    Unregister-ScheduledTask -TaskName $nombre -Confirm:$false
    "Tarea '$nombre' eliminada."
    return
}
$raiz = Split-Path $PSScriptRoot -Parent
$python = Join-Path $raiz '.venv\Scripts\python.exe'
$aviso = if ($SinAviso) { '' } else { ' --avisar' }
$accion = New-ScheduledTaskAction -Execute $python -Argument "manage.py chequear_servicio --settings=$Settings$aviso" `
    -WorkingDirectory $raiz
$disparador = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $Minutos)
$opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger $disparador -Settings $opciones `
    -Description 'Chequeo del servicio de Ceiba ERP (manage.py chequear_servicio)' -Force | Out-Null
"Tarea '$nombre' programada cada $Minutos minutos. Estado público en: http://<servidor>/estado/"
