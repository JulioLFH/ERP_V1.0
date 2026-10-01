web: gunicorn erp.wsgi --bind 0.0.0.0:$PORT --workers 3
release: python manage.py migrate --no-input
