"""Tipo de cambio USD/PEN.

Se guarda en la tabla TipoCambio. Si la fecha no existe se consulta la fuente elegida en Ajustes > Empresa:
- SUNAT: API pública apis.net.pe (gratuita).
- SBS: tipo de cambio promedio ponderado de la SBS vía la API de Decolecta (requiere token). Si no responde
  o no hay token, se usa SUNAT.
Fines de semana y feriados usan el último día publicado.
"""
import json
import logging
import urllib.request
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache

from .models import Empresa, TipoCambio
from django.utils import timezone

log = logging.getLogger(__name__)
API_URL = 'https://api.apis.net.pe/v1/tipo-cambio-sunat?fecha={fecha}'
SBS_URL = 'https://api.decolecta.com/v1/tipo-cambio/sbs/average?currency=USD&date={fecha}'


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'Ceiba-ERP', 'Accept': 'application/json',
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _consultar_sunat(fecha):
    data = _get_json(API_URL.format(fecha=fecha.isoformat()))
    compra, venta = data.get('compra'), data.get('venta')
    if compra and venta:
        return Decimal(str(compra)), Decimal(str(venta))
    return None


def _consultar_sbs(fecha, token):
    data = _get_json(SBS_URL.format(fecha=fecha.isoformat()), {'Authorization': f'Bearer {token}'})
    compra, venta = data.get('buy_price'), data.get('sell_price')
    if compra and venta and data.get('date', fecha.isoformat()) == fecha.isoformat():
        return Decimal(str(compra)), Decimal(str(venta))
    return None


def fuente_actual():
    """('SBS', token) si la empresa eligió SBS y tiene token; si no ('SUNAT', '')."""
    empresa = Empresa.objects.only('fuente_tipo_cambio', 'token_tipo_cambio').first()
    if empresa and empresa.fuente_tipo_cambio == 'SBS' and empresa.token_tipo_cambio:
        return 'SBS', empresa.token_tipo_cambio
    return 'SUNAT', ''


def _consultar(fecha):
    """(compra, venta, fuente) o None si no se publicó esa fecha. Lanza excepción si no hay conexión."""
    fuente, token = fuente_actual()
    if fuente == 'SBS':
        try:
            valores = _consultar_sbs(fecha, token)
            if valores:
                return valores + ('SBS',)
        except Exception as exc:  # token vencido, límite de consultas, API caída: se usa SUNAT
            log.warning('Tipo de cambio SBS no disponible (%s): %s', fecha, exc)
    valores = _consultar_sunat(fecha)
    return valores + ('SUNAT',) if valores else None


def consultar_sunat(fecha):
    try:
        return _consultar(fecha)
    except Exception as exc:  # sin internet, API caída, fecha sin publicar (404)
        log.warning('No se pudo consultar el tipo de cambio %s: %s', fecha, exc)
        return None


def obtener(fecha=None, consultar=True):
    """TipoCambio de la fecha (consultando SUNAT si falta) o el último anterior registrado. Puede ser None."""
    fecha = min(fecha or timezone.localdate(), timezone.localdate())  # no existe tipo de cambio de fechas futuras
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
                    fecha=fecha, defaults={'compra': valores[0], 'venta': valores[1],
                                           'fuente': valores[2] if len(valores) > 2 else 'SUNAT'})[0]
        cache.set(clave, True, 3600)
    return TipoCambio.objects.filter(fecha__lte=fecha).first()


def dias_del_periodo(periodo):
    """Fechas del periodo AAAAMM hasta hoy (no hay tipo de cambio de fechas futuras)."""
    import calendar
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    hoy = timezone.localdate()
    ultimo = calendar.monthrange(anio, mes)[1]
    return [d for d in (date(anio, mes, dia) for dia in range(1, ultimo + 1)) if d <= hoy]


def descargar_periodo(periodo, reemplazar_manual=False):
    """REQ-07: descarga de la fuente configurada el tipo de cambio de cada día del periodo AAAAMM.

    Devuelve {'ok': n, 'sin_publicar': [fechas], 'fuentes': {...}, 'error': texto}. Los días que SUNAT no publica
    (fines de semana y feriados) no se guardan: usan el último día publicado. Los registrados a mano se respetan,
    salvo reemplazar_manual."""
    resultado = {'ok': 0, 'sin_publicar': [], 'fuentes': set(), 'error': ''}
    manuales = set() if reemplazar_manual else set(
        TipoCambio.objects.filter(fuente='MANUAL', fecha__in=dias_del_periodo(periodo)).values_list('fecha', flat=True))
    for f in dias_del_periodo(periodo):
        if f in manuales:
            continue
        try:
            valores = _consultar(f)
        except Exception as exc:
            if getattr(exc, 'code', None) in (400, 404):
                resultado['sin_publicar'].append(f)
                continue
            resultado['error'] = f'Sin respuesta de la fuente desde el {f:%d/%m/%Y} ({type(exc).__name__}).'
            break  # sin conexión o límite de consultas: no insistir con los demás días
        if not valores:
            resultado['sin_publicar'].append(f)
            continue
        fuente = valores[2] if len(valores) > 2 else 'SUNAT'
        TipoCambio.objects.update_or_create(fecha=f, defaults={'compra': valores[0], 'venta': valores[1],
                                                               'fuente': fuente})
        resultado['fuentes'].add(fuente)
        resultado['ok'] += 1
    return resultado


def venta_del_dia(fecha=None, consultar=True):
    """Tipo de cambio venta (el que SUNAT exige para registrar compras y ventas). 1 si no hay dato."""
    tc = obtener(fecha, consultar)
    return tc.venta if tc else Decimal('1')
