"""Límites de frecuencia (v1.115) para lo público: conversaciones nuevas del chat web y del formulario web (SEG-05).

Cuenta en el caché (en producción es un caché en archivos compartido por los procesos del servidor) por ventanas
fijas: suficiente para frenar a un robot sin guardar nada en la base."""
import time

from django.core.cache import cache


def excede(clave, maximo, segundos=3600):
    """Suma un evento a `clave` y dice si ya pasó el `maximo` permitido en la ventana de `segundos`."""
    ventana = int(time.time() // segundos)
    llave = f'limite:{clave}:{ventana}'
    cache.add(llave, 0, segundos + 60)
    try:
        n = cache.incr(llave)
    except ValueError:  # la llave venció entre add e incr
        cache.set(llave, 1, segundos + 60)
        n = 1
    return n > maximo
