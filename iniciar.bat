@echo off
rem Inicia Ceiba ERP en esta PC con la base de la empresa (cliente.sqlite3 + segunda empresa, con selector «Empresa»).
rem Para la base de demostracion (db.sqlite3): iniciar.bat demo
cd /d "%~dp0"
title Ceiba ERP

set AJUSTES=--settings=erp.settings_cliente
if /i "%~1"=="demo" set AJUSTES=
if not defined AJUSTES goto :entorno
if not exist "cliente.sqlite3" (
  echo [ERROR] No se encuentra cliente.sqlite3 en esta carpeta ^(la base de la empresa^). Copiela aqui.
  goto :fin
)

:entorno
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Falta el entorno .venv ^(PC nueva o carpeta copiada sin .venv^). Ejecute en esta carpeta:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  goto :fin
)

netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo Ceiba ERP ya esta abierto en otra ventana: se abre el navegador.
  start "" http://127.0.0.1:8000
  goto :fin
)

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
