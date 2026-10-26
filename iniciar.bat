@echo off
rem Inicia Ceiba ERP en esta PC con la base de trabajo PostgreSQL (la de .env.postgres, selector «Empresa»).
rem   iniciar.bat           base de trabajo PostgreSQL (desde la version 1.32)
rem   iniciar.bat sqlite    base SQLite anterior (cliente.sqlite3), solo para consultar: ya no recibe datos nuevos
rem   iniciar.bat demo      base de demostracion (db.sqlite3)
rem Acepta conexiones de otras computadoras y de internet (abra el puerto en el router y en el firewall de Windows).
rem Puerto: linea PUERTO=8030 en .env.postgres (sin ella, 8000). Guia: docs\POSTGRESQL.md
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

set PUERTO=8000
if exist ".env.postgres" for /f "tokens=2 delims== " %%p in ('findstr /b /c:"PUERTO=" ".env.postgres"') do set PUERTO=%%p

netstat -ano | findstr /r /c:":%PUERTO% .*LISTENING" >nul
if not errorlevel 1 (
  echo Ya hay un Ceiba ERP abierto en otra ventana ^(puerto %PUERTO%^): se abre el navegador.
  echo Si es el de otra base o no responde, cierre esa ventana y vuelva a ejecutar iniciar.bat.
  start "" http://127.0.0.1:%PUERTO%
  goto :fin
)

if not "%MODO%"=="PostgreSQL" goto :arrancar
rem Modo normal (sin pantallas tecnicas de error); sin certificado: http y cookies sin "Secure"
set DEBUG=0
set SECURE_SSL_REDIRECT=0
set SECURE_HSTS_SECONDS=0
set COOKIES_SEGURAS=0
rem Clave secreta propia de esta instalacion: se crea una sola vez en .env.clave (no se sube a GitHub)
if not exist ".env.clave" .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(50))" > ".env.clave"
set /p SECRET_KEY=<".env.clave"

:arrancar
echo Base de datos: %MODO%
echo Actualizando las bases de datos...
.venv\Scripts\python.exe manage.py migrate --no-input %AJUSTES% || goto :fin
if defined AJUSTES .venv\Scripts\python.exe manage.py migrate --no-input --database empresa2 %AJUSTES% || goto :fin
rem estilos y scripts de las pantallas (en modo normal los sirve el propio sistema)
if "%MODO%"=="PostgreSQL" .venv\Scripts\python.exe manage.py collectstatic --no-input %AJUSTES% >nul || goto :fin

echo Iniciando Ceiba ERP en http://127.0.0.1:%PUERTO% ... ^(no cierre esta ventana^)
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%j in ("%%i") do echo Desde otra PC de la red: http://%%j:%PUERTO%
rem abre el navegador recien cuando el servidor contesta (evita la pagina "no se puede conectar")
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 90;$i++){try{Invoke-WebRequest http://127.0.0.1:%PUERTO%/login/ -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process http://127.0.0.1:%PUERTO%; break}catch{Start-Sleep 1}}"
.venv\Scripts\python.exe manage.py runserver 0.0.0.0:%PUERTO% %AJUSTES%

:fin
echo.
echo El ERP se detuvo. Si hay un error, esta arriba de este mensaje.
pause
