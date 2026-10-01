FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DEBUG=0 SECRET_KEY=build python manage.py collectstatic --no-input

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --no-input && gunicorn erp.wsgi --bind 0.0.0.0:${PORT} --workers 3"]
