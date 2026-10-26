@echo off
rem Publica Ceiba ERP en GitHub: sube los cambios ya guardados (commits) y la etiqueta de la version.
rem Desde v1.33 no hay despliegue en Render: cada copia se actualiza con "git pull" y se abre con iniciar.bat
rem (iniciar.bat aplica las migraciones a sus bases).
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Publicar Ceiba ERP

echo ============================================
echo   Publicar Ceiba ERP en GitHub
echo ============================================
echo.

where git >nul 2>nul || (echo [ERROR] No se encontro Git instalado. & goto :fin)
if not exist ".venv\Scripts\python.exe" (echo [ERROR] No existe el entorno .venv & goto :fin)

rem 1. Seguridad: datos reales, claves e instrucciones privadas nunca van a GitHub
git ls-files | findstr /i /r "\.sqlite3$ ^migracion_local/ INSTRUCCIONES_CLAUDE \.env\.postgres \.env\.clave Checklist_" >nul
if not errorlevel 1 (
  echo [ERROR] Hay archivos con datos reales o privados en el repositorio:
  git ls-files | findstr /i /r "\.sqlite3$ ^migracion_local/ INSTRUCCIONES_CLAUDE \.env\.postgres \.env\.clave Checklist_"
  echo No se publica nada. Quitalos con: git rm --cached ARCHIVO
  goto :fin
)

rem 2. Cambios sin guardar (sin contar los archivos locales): no se publican
set PENDIENTES=
for /f "delims=" %%l in ('git status --porcelain ^| findstr /v /i /c:"INSTRUCCIONES_CLAUDE" /c:"Checklist_" /c:"Iniciar ERP"') do set PENDIENTES=1
if defined PENDIENTES (
  echo [AVISO] Hay cambios sin guardar en un commit; NO se van a publicar:
  git status --short | findstr /v /i /c:"INSTRUCCIONES_CLAUDE" /c:"Checklist_" /c:"Iniciar ERP"
  echo.
)

rem 3. Version y commits por publicar
for /f "tokens=2 delims='" %%v in ('findstr /c:"ERP_VERSION = " erp\settings.py') do set VER=%%v
echo Version a publicar: v%VER%
git fetch -q origin
set NUEVOS=
for /f "delims=" %%c in ('git log origin/main..HEAD --oneline') do set NUEVOS=1
if not defined NUEVOS (
  echo No hay commits nuevos: GitHub ya tiene la ultima version.
  goto :fin
)
echo Cambios que se suben:
git log origin/main..HEAD --format="  - %%h %%s"
echo.

rem 4. Revision del sistema y pruebas (en PostgreSQL, como el sistema en uso)
echo Revisando el sistema...
.venv\Scripts\python.exe manage.py check --settings=erp.settings_postgres || (echo [ERROR] manage.py check fallo. No se publica. & goto :fin)
choice /c SN /m "Correr las pruebas automaticas (unos 15 minutos)"
if errorlevel 2 goto :confirmar
.venv\Scripts\python.exe manage.py test --noinput --settings=erp.settings_postgres || (echo [ERROR] Fallaron pruebas. No se publica. & goto :fin)

:confirmar
echo.
choice /c SN /m "Publicar v%VER% en GitHub"
if errorlevel 2 (echo Cancelado. & goto :fin)

rem 5. Etiqueta de la version (si aun no existe)
git rev-parse -q --verify "refs/tags/v%VER%" >nul || git tag "v%VER%"

rem 6. Subir
git push origin main || (echo [ERROR] No se pudo subir a GitHub. & goto :fin)
git push origin "v%VER%"

echo.
echo Listo: v%VER% esta en GitHub.
echo Para actualizar otra copia: en su carpeta ejecute "git pull" y abra iniciar.bat (migra sus bases).
echo La version instalada se ve en el menu del usuario (arriba a la derecha).
start "" https://github.com/JulioLFH/ERP_V1.0/tags

:fin
echo.
pause
endlocal
