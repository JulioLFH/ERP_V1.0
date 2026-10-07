@echo off
rem Publica Ceiba ERP: sube a GitHub los cambios ya guardados (commits) y la etiqueta de la version.
rem Render detecta el cambio en la rama main y despliega solo (build.sh: dependencias, archivos, migraciones).
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Desplegar Ceiba ERP

echo ============================================
echo   Desplegar Ceiba ERP en Render
echo ============================================
echo.

where git >nul 2>nul || (echo [ERROR] No se encontro Git instalado. & goto :fin)
if not exist ".venv\Scripts\python.exe" (echo [ERROR] No existe el entorno .venv & goto :fin)

rem 1. Seguridad: datos reales e instrucciones privadas nunca van a GitHub
git ls-files | findstr /i /r "\.sqlite3$ ^migracion_local/ INSTRUCCIONES_CLAUDE" >nul
if not errorlevel 1 (
  echo [ERROR] Hay archivos con datos reales o privados en el repositorio:
  git ls-files | findstr /i /r "\.sqlite3$ ^migracion_local/ INSTRUCCIONES_CLAUDE"
  echo No se publica nada. Quitalos con: git rm --cached ARCHIVO
  goto :fin
)

rem 2. Cambios sin guardar (sin contar las instrucciones locales): no se publican
set PENDIENTES=
for /f "delims=" %%l in ('git status --porcelain ^| findstr /v /i "INSTRUCCIONES_CLAUDE"') do set PENDIENTES=1
if defined PENDIENTES (
  echo [AVISO] Hay cambios sin guardar en un commit; NO se van a publicar:
  git status --short | findstr /v /i "INSTRUCCIONES_CLAUDE"
  echo.
)

rem 3. Version y commits por publicar
for /f "tokens=2 delims='" %%v in ('findstr /c:"ERP_VERSION = " erp\settings.py') do set VER=%%v
echo Version a publicar: v%VER%
git fetch -q origin
set NUEVOS=
for /f "delims=" %%c in ('git log origin/main..HEAD --oneline') do set NUEVOS=1
if not defined NUEVOS (
  echo No hay commits nuevos: GitHub y Render ya tienen la ultima version.
  goto :fin
)
echo Cambios que se suben:
git log origin/main..HEAD --format="  - %%h %%s"
echo.

rem 4. Revision del sistema y pruebas
echo Revisando el sistema...
.venv\Scripts\python.exe manage.py check || (echo [ERROR] manage.py check fallo. No se publica. & goto :fin)
choice /c SN /m "Correr las pruebas automaticas (unos 2 minutos)"
if errorlevel 2 goto :confirmar
.venv\Scripts\python.exe manage.py test --parallel 1 || (echo [ERROR] Fallaron pruebas. No se publica. & goto :fin)

:confirmar
echo.
choice /c SN /m "Publicar v%VER% en GitHub y desplegar en Render"
if errorlevel 2 (echo Cancelado. & goto :fin)

rem 5. Etiqueta de la version (si aun no existe)
git rev-parse -q --verify "refs/tags/v%VER%" >nul || git tag "v%VER%"

rem 6. Subir
git push origin main || (echo [ERROR] No se pudo subir a GitHub. & goto :fin)
git push origin "v%VER%"

echo.
echo Listo. Render esta desplegando v%VER% (tarda unos 3 a 5 minutos).
echo Al terminar, la version se ve en el menu del usuario (arriba a la derecha).
start "" https://dashboard.render.com/web/srv-davbla7avr4c73bbnfbg/events
start "" https://erp-v1-j37b.onrender.com

:fin
echo.
pause
endlocal
