"""Cifrado simétrico (Fernet) de claves guardadas en la base: clave SOL secundaria y clave del certificado digital.
La llave sale de SECRET_KEY: sin ella (otro servidor con otra SECRET_KEY) las claves no se pueden leer y hay que
volver a ingresarlas."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIJO = 'fernet:'


def _fernet():
    llave = hashlib.sha256(f'ceiba-erp:{settings.SECRET_KEY}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(llave))


def cifrar(texto):
    if not texto:
        return ''
    return PREFIJO + _fernet().encrypt(texto.encode('utf-8')).decode('ascii')


def descifrar(valor):
    """El texto original; '' si está vacío o se cifró con otra SECRET_KEY."""
    if not valor:
        return ''
    if not valor.startswith(PREFIJO):
        return valor
    try:
        return _fernet().decrypt(valor[len(PREFIJO):].encode('ascii')).decode('utf-8')
    except InvalidToken:
        return ''
