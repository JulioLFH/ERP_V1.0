"""v1.117.0: token de Decolecta compartido por todos los Ceiba ERP del servidor.

El panel de servicios (http://127.0.0.1:4321 › Ajustes) lo guarda en un archivo JSON fuera de las bases de datos
(settings.TOKENS_COMPARTIDOS, por defecto Ceiba_Servicios\\compartido.json). Tipo de cambio, padrón RUC y la lectura de
comprobantes usan ese token; si el panel no tiene ninguno, el de la empresa (Ajustes › Empresa), como antes.
El archivo se vuelve a leer cuando cambia: no hace falta reiniciar los ERP.
"""
import json
import logging
import os

from django.conf import settings

log = logging.getLogger(__name__)
_memo = {'ruta': None, 'mtime': None, 'datos': {}}


def _compartidos():
    ruta = getattr(settings, 'TOKENS_COMPARTIDOS', '') or ''
    if not ruta:
        return {}
    try:
        mtime = os.path.getmtime(ruta)
    except OSError:
        return {}
    if _memo['ruta'] == ruta and _memo['mtime'] == mtime:
        return _memo['datos']
    try:
        with open(ruta, encoding='utf-8') as f:
            datos = json.load(f)
    except (OSError, ValueError) as exc:
        log.warning('No se pudo leer el archivo de tokens compartidos %s: %s', ruta, exc)
        datos = {}
    _memo.update(ruta=ruta, mtime=mtime, datos=datos if isinstance(datos, dict) else {})
    return _memo['datos']


def compartido():
    """Token de Decolecta del panel de servicios ('' si no hay)."""
    return str(_compartidos().get('decolecta_token') or '').strip()


def de_la_empresa():
    from .models import Empresa
    empresa = Empresa.objects.only('token_tipo_cambio').first()
    return (empresa.token_tipo_cambio or '').strip() if empresa else ''


def token():
    """El token compartido del panel; si no hay, el de la empresa activa."""
    return compartido() or de_la_empresa()


def oculto(valor):
    """Para mostrarlo sin revelarlo: ••••1234."""
    valor = (valor or '').strip()
    return f'••••{valor[-4:]}' if len(valor) > 8 else ('••••' if valor else '')
