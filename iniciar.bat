@echo off
cd /d "%~dp0"
title CEIVA ERP
echo Iniciando CEIVA ERP en http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
