@echo off
rem Inicia Ceiba ERP en esta PC con la base de trabajo PostgreSQL (ceiba_principal + ceiba_empresa2, selector «Empresa»).
rem   iniciar.bat           base de trabajo PostgreSQL (desde la version 1.32)
rem   iniciar.bat sqlite    base SQLite anterior (cliente.sqlite3), solo para consultar: ya no recibe datos nuevos
rem   iniciar.bat demo      base de demostracion (db.sqlite3)
rem Guia: docs\POSTGRESQL.md
cd /d "%~dp0"
title Ceiba ERP

set AJUSTES=--settings=erp.settings_postgres
set MODO=PostgreSQL
if /i "%~1"=="sqlite" (
  set AJUSTES=--settings=erp.settings_cliente
  set MODO=SQLite anterior ^(solo consulta^)
)
if /i "%~1"=="demo" (
  set AJUSTES=
  set MODO=demostracion
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Falta el entorno .venv ^(PC nueva o carpeta copiada sin .venv^). Ejecute en esta carpeta:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  goto :fin
)

if "%MODO%"=="PostgreSQL" (
  if not exist ".env.postgres" (
    echo [ERROR] Falta el archivo .env.postgres con la conexion a PostgreSQL. Ver docs\POSTGRESQL.md
    goto :fin
  )
  sc query postgresql-17 | findstr /c:"RUNNING" >nul
  if errorlevel 1 (
    echo [ERROR] El servicio de PostgreSQL ^(postgresql-17^) no esta iniciado.
    echo   Inicielo: clic derecho en PowerShell, Ejecutar como administrador, y escriba:  Start-Service postgresql-17
    goto :fin
  )
)
if /i "%~1"=="sqlite" if not exist "cliente.sqlite3" (
  echo [ERROR] No se encuentra cliente.sqlite3 en esta carpeta.
  goto :fin
)

netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo Ya hay un Ceiba ERP abierto en otra ventana ^(puerto 8000^): se abre el navegador.
  echo Si es el de otra base o no responde, cierre esa ventana y vuelva a ejecutar iniciar.bat.
  start "" http://127.0.0.1:8000
  goto :fin
)

echo Base de datos: %MODO%
echo Actualizando las bases de datos...
.venv\Scripts\python.exe manage.py migrate --no-input %AJUSTES% || goto :fin
if defined AJUSTES .venv\Scripts\python.exe manage.py migrate --no-input --database empresa2 %AJUSTES% || goto :fin

echo Iniciando Ceiba ERP en http://127.0.0.1:8000 ... ^(no cierre esta ventana^)
rem abre el navegador recien cuando el servidor contesta (evita la pagina "no se puede conectar")
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 90;$i++){try{Invoke-WebRequest http://127.0.0.1:8000/login/ -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process http://127.0.0.1:8000; break}catch{Start-Sleep 1}}"
.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000 %AJUSTES%

:fin
echo.
echo El ERP se detuvo. Si hay un error, esta arriba de este mensaje.
pause
