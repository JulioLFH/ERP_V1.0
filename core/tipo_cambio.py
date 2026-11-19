"""Tipo de cambio USD/PEN.

Se guarda en la tabla TipoCambio. Si la fecha no existe se consulta la fuente elegida en Ajustes > Empresa:
- SUNAT: API pública apis.net.pe (gratuita). Con el token de Decolecta de la empresa, primero el tipo de cambio SUNAT
  de Decolecta (v1.116.8: la API gratuita responde 429 si se le consulta más de una vez cada ~15 segundos).
- SBS: tipo de cambio promedio ponderado de la SBS vía la API de Decolecta (requiere token). Si no responde
  o no hay token, se usa SUNAT.
Fines de semana y feriados usan el último día publicado.

v1.116.8: carga diaria (cargar_pendientes): servir.py la lanza cada hora y completa los días que falten del mes en
curso en todas las empresas, con una consulta por día (o una por mes con token) compartida entre las empresas.
"""
import json
import logging
import time
import urllib.request
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache

from .models import Empresa, TipoCambio
from django.utils import timezone

log = logging.getLogger(__name__)
API_URL = 'https://api.apis.net.pe/v1/tipo-cambio-sunat?fecha={fecha}'
SBS_URL = 'https://api.decolecta.com/v1/tipo-cambio/sbs/average?currency=USD&date={fecha}'
DECOLECTA_SUNAT_URL = 'https://api.decolecta.com/v1/tipo-cambio/sunat?date={fecha}'
DECOLECTA_SUNAT_MES_URL = 'https://api.decolecta.com/v1/tipo-cambio/sunat?month={mes}&year={anio}'
PAUSA = 20  # segundos entre consultas de la carga diaria (apis.net.pe gratuita: 429 si son más seguidas)
ESPERA_429 = 60  # ante un 429, se espera y se reintenta una vez


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'Ceiba-ERP', 'Accept': 'application/json',
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _consultar_sunat(fecha, token=''):
    if token:  # v1.116.8: el SUNAT de Decolecta con el token de la empresa; si falla, la API gratuita
        try:
            data = _get_json(DECOLECTA_SUNAT_URL.format(fecha=fecha.isoformat()), {'Authorization': f'Bearer {token}'})
            compra, venta = data.get('buy_price'), data.get('sell_price')
            if compra and venta and data.get('date', fecha.isoformat()) == fecha.isoformat():
                return Decimal(str(compra)), Decimal(str(venta))
        except Exception as exc:
            log.warning('Tipo de cambio SUNAT de Decolecta no disponible (%s): %s', fecha, exc)
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


def _mes_decolecta(anio, mes, token):
    """{fecha: (compra, venta)} del mes completo (tipo de cambio SUNAT de Decolecta): una sola consulta."""
    data = _get_json(DECOLECTA_SUNAT_MES_URL.format(mes=mes, anio=anio), {'Authorization': f'Bearer {token}'})
    filas = data if isinstance(data, list) else (data.get('data') or data.get('results') or [])
    valores = {}
    for f in filas:
        try:
            valores[date.fromisoformat(str(f['date'])[:10])] = (Decimal(str(f['buy_price'])),
                                                               Decimal(str(f['sell_price'])))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return valores


def _token():
    empresa = Empresa.objects.only('token_tipo_cambio').first()
    return (empresa.token_tipo_cambio or '') if empresa else ''


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
    valores = _consultar_sunat(fecha, token or _token())
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
    del_mes = {}
    fuente, token = fuente_actual()
    token = token or _token()
    if fuente == 'SUNAT' and token:  # v1.116.8: con token, el mes completo en una consulta (sin 429 por día)
        try:
            del_mes = _mes_decolecta(int(periodo[:4]), int(periodo[4:6]), token)
        except Exception as exc:
            log.warning('Tipo de cambio del periodo %s de Decolecta no disponible: %s', periodo, exc)
    for f in dias_del_periodo(periodo):
        if f in manuales:
            continue
        if f in del_mes:
            _guardar(f, del_mes[f], 'SUNAT')
            resultado['fuentes'].add('SUNAT')
            resultado['ok'] += 1
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


# ---------------------------------------------------------------- v1.116.8: carga diaria
def _guardar(fecha, valores, fuente):
    TipoCambio.objects.update_or_create(fecha=fecha, defaults={'compra': valores[0], 'venta': valores[1],
                                                               'fuente': fuente})


def _consultar_espaciado(fecha, memo, pausa, dormir):
    """(valores o None, error). Espacia las consultas; ante un 429 espera y reintenta una vez."""
    error = ''
    for intento in range(2):
        if memo.get('_consultas'):
            dormir(ESPERA_429 if intento else pausa)
        memo['_consultas'] = memo.get('_consultas', 0) + 1
        try:
            return _consultar(fecha), ''
        except Exception as exc:
            codigo = getattr(exc, 'code', None)
            if codigo in (400, 404):  # aún sin publicar (hoy temprano) o feriado
                return None, ''
            error = f'Sin respuesta de la fuente desde el {fecha:%d/%m/%Y} ({codigo or type(exc).__name__}).'
            if codigo != 429:
                break
    return None, error


def cargar_pendientes(memo=None, pausa=None, dormir=None):
    """Completa los días sin tipo de cambio del mes en curso (y de la última semana, al empezar el mes) de la empresa
    activa. `memo` se comparte entre las empresas del grupo: cada día se consulta una sola vez. Las consultas van
    espaciadas (`pausa`) y ante un 429 se espera y se reintenta una vez; si sigue, el resto queda para la próxima
    hora. Los días ya registrados (también los manuales) no se tocan. Devuelve {'ok': n, 'faltan': n, 'error': texto}."""
    memo = {} if memo is None else memo
    pausa = PAUSA if pausa is None else pausa
    dormir = dormir or time.sleep
    hoy = timezone.localdate()
    desde = min(hoy.replace(day=1), hoy - timedelta(days=7))
    fechas = [desde + timedelta(days=i) for i in range((hoy - desde).days + 1)]
    tienen = set(TipoCambio.objects.filter(fecha__in=fechas).values_list('fecha', flat=True))
    faltan = [f for f in fechas if f not in tienen]
    resultado = {'ok': 0, 'faltan': len(faltan), 'error': ''}
    if not faltan:
        return resultado
    fuente, token = fuente_actual()
    token = token or _token()
    if fuente == 'SUNAT' and token:  # con token: el mes completo en una consulta
        for anio, mes in sorted({(f.year, f.month) for f in faltan}):
            if ('SUNAT-MES', anio, mes) in memo:
                continue
            memo[('SUNAT-MES', anio, mes)] = True
            try:
                for f, v in _mes_decolecta(anio, mes, token).items():
                    memo.setdefault(('SUNAT', f), v + ('SUNAT',))
            except Exception as exc:
                log.warning('Tipo de cambio del mes %s/%s de Decolecta no disponible: %s', mes, anio, exc)
    for f in faltan:
        clave = (fuente, f)
        if clave not in memo:
            if memo.get('_detenido'):
                resultado['error'] = memo['_detenido']
                break
            valores, error = _consultar_espaciado(f, memo, pausa, dormir)
            if error:
                memo['_detenido'] = resultado['error'] = error  # sin conexión o límite: queda para la próxima hora
                break
            memo[clave] = valores
        valores = memo[clave]
        if valores:
            _guardar(f, valores, valores[2] if len(valores) > 2 else fuente)
            resultado['ok'] += 1
    return resultado
