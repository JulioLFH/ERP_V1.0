"""Tipo de cambio USD/PEN publicado por SUNAT.

Se guarda en la tabla TipoCambio. Si la fecha no existe se consulta la API pública
apis.net.pe (fuente SUNAT); fines de semana y feriados usan el último día publicado.
"""
import json
import logging
import urllib.request
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache

from .models import TipoCambio

log = logging.getLogger(__name__)
API_URL = 'https://api.apis.net.pe/v1/tipo-cambio-sunat?fecha={fecha}'


def _consultar(fecha):
    """(compra, venta) o None si SUNAT no publicó esa fecha. Lanza excepción si no hay conexión."""
    req = urllib.request.Request(API_URL.format(fecha=fecha.isoformat()),
                                 headers={'User-Agent': 'ERP-V1', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    compra, venta = data.get('compra'), data.get('venta')
    if compra and venta:
        return Decimal(str(compra)), Decimal(str(venta))
    return None


def consultar_sunat(fecha):
    try:
        return _consultar(fecha)
    except Exception as exc:  # sin internet, API caída, fecha sin publicar (404)
        log.warning('No se pudo consultar el tipo de cambio %s: %s', fecha, exc)
        return None


def obtener(fecha=None, consultar=True):
    """TipoCambio de la fecha (consultando SUNAT si falta) o el último anterior registrado. Puede ser None."""
    fecha = min(fecha or date.today(), date.today())  # no existe tipo de cambio de fechas futuras
    tc = TipoCambio.objects.filter(fecha=fecha).first()
    if tc:
        return tc
    clave = f'tc-sin-dato-{fecha.isoformat()}'
    if consultar and not cache.get(clave):
        for i in range(7):  # SUNAT no publica fines de semana/feriados
            f = fecha - timedelta(days=i)
            if i and TipoCambio.objects.filter(fecha=f).exists():
                break
            try:
                valores = _consultar(f)
            except Exception as exc:
                log.warning('Tipo de cambio no disponible (%s): %s', f, exc)
                if getattr(exc, 'code', None) in (400, 404):
                    continue  # fecha sin publicar: probar el día anterior
                break  # sin conexión o límite de consultas (429): no insistir
            if valores:
                return TipoCambio.objects.update_or_create(
                    fecha=fecha, defaults={'compra': valores[0], 'venta': valores[1], 'fuente': 'SUNAT'})[0]
        cache.set(clave, True, 3600)
    return TipoCambio.objects.filter(fecha__lte=fecha).first()


def venta_del_dia(fecha=None, consultar=True):
    """Tipo de cambio venta (el que SUNAT exige para registrar compras y ventas). 1 si no hay dato."""
    tc = obtener(fecha, consultar)
    return tc.venta if tc else Decimal('1')
