#!/usr/bin/env bash
# Script de build para la nube (Render / Railway / cualquier PaaS Linux)
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Crea/actualiza el usuario administrador con DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_PASSWORD
python manage.py ensure_admin

# Datos de demostración (solo si SEED_DEMO=1 y la base está vacía)
if [ "$SEED_DEMO" = "1" ]; then
  python manage.py seed_demo
fi
