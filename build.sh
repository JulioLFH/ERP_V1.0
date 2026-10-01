#!/usr/bin/env bash
# Script de build para la nube (Render / Railway / cualquier PaaS Linux)
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Crea el superusuario la primera vez si se definen DJANGO_SUPERUSER_USERNAME / _PASSWORD / _EMAIL
if [ -n "$DJANGO_SUPERUSER_USERNAME" ]; then
  python manage.py createsuperuser --no-input || true
fi
