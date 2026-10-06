#!/usr/bin/env bash
# Script de build para la nube (Render / Railway / cualquier PaaS Linux)
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
# Migra la base de cada empresa (multiempresa: EMPRESAS_EXTRA) y crea/actualiza su administrador con
# DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_PASSWORD
python manage.py migrar_empresas --no-color

# Datos de demostración (solo si SEED_DEMO=1 y la base está vacía)
if [ "$SEED_DEMO" = "1" ]; then
  python manage.py seed_demo
fi
