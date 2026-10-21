"""Padrón RUC de SUNAT vía la API de Decolecta (la misma cuenta del tipo de cambio SBS).

GET https://api.decolecta.com/v1/sunat/ruc/full?numero={ruc}  (Authorization: Bearer {token})
Devuelve razón social, estado, condición, dirección y ubigeo del domicilio fiscal, tipo de contribuyente, actividad
económica, trabajadores, sistema de emisión y de contabilidad, comercio exterior, si es agente de retención o buen
contribuyente y los locales anexos. Si el plan no incluye la consulta completa se usa la básica (/sunat/ruc). SUNAT
no publica correo ni teléfono: esos datos los completa el usuario.
"""
import json
import logging
import urllib.error
import urllib.request

from django.core.cache import cache

URL_COMPLETA = 'https://api.decolecta.com/v1/sunat/ruc/full?numero={ruc}'
URL = 'https://api.decolecta.com/v1/sunat/ruc?numero={ruc}'
log = logging.getLogger(__name__)
MENSAJES = {401: 'el token de Decolecta no es válido', 404: 'SUNAT no tiene ese RUC', 422: 'SUNAT no tiene ese RUC',
            429: 'se agotaron las consultas del plan de Decolecta'}


def _pedir(url, token):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json',
                                               'User-Agent': 'Ceiba-ERP'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _si_no(valor):
    if isinstance(valor, str):
        return valor.strip().upper() in ('SI', 'SÍ', 'S', 'TRUE', '1')
    return bool(valor)


def _texto(valor):
    if isinstance(valor, (list, tuple)):
        return ' · '.join(_texto(v) for v in valor if v)
    if isinstance(valor, dict):
        return ' '.join(str(v) for v in valor.values() if v)
    return ' '.join(str(valor or '').split())


def _locales(valor):
    """Locales anexos como lista de textos (la API puede mandar una lista o solo la cantidad)."""
    if isinstance(valor, list):
        return [_texto(v) for v in valor if v]
    return []


class ErrorPadron(Exception):
    pass


def consultar(ruc, token=''):
    """{'ruc', 'razon_social', 'estado', 'condicion', 'direccion', 'ubigeo', 'alerta'} o ErrorPadron."""
    from . import ubigeo
    from .forms import error_ruc
    from .models import Empresa
    ruc = (ruc or '').strip()
    if error_ruc(ruc):
        raise ErrorPadron(error_ruc(ruc))
    token = (token or '').strip() or Empresa.objects.values_list('token_tipo_cambio', flat=True).first() or ''
    if not token:
        raise ErrorPadron('Para traer los datos de SUNAT falta el token de Decolecta: regístrelo en Ajustes › Empresa '
                          '(«Token Decolecta», cuenta gratuita en decolecta.com).')
    clave = f'padron-ruc-{ruc}'
    r = cache.get(clave)
    if r is None:
        try:
            try:
                r = _pedir(URL_COMPLETA.format(ruc=ruc), token)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 404, 422, 429):
                    raise
                r = _pedir(URL.format(ruc=ruc), token)  # el plan no incluye la consulta completa
        except urllib.error.HTTPError as exc:
            raise ErrorPadron(f'No se pudo consultar el RUC: {MENSAJES.get(exc.code, f"error {exc.code}")}.') \
                from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.info('Consulta de padrón RUC %s no disponible: %s', ruc, exc)
            raise ErrorPadron('No se pudo conectar con el servicio de consulta RUC. Intente más tarde.') from exc
        cache.set(clave, r, 86400)
    codigo = str(r.get('ubigeo') or '').strip()
    locales = _locales(r.get('locales_anexos'))
    datos = {'ruc': ruc, 'razon_social': (r.get('razon_social') or '').strip(),
             'estado': (r.get('estado') or '').upper(), 'condicion': (r.get('condicion') or '').upper(),
             'direccion': _texto(r.get('direccion')),
             'ubigeo': codigo if ubigeo.existe(codigo) else '',
             'distrito': _texto(r.get('distrito')), 'provincia': _texto(r.get('provincia')),
             'departamento': _texto(r.get('departamento')),
             'tipo': _texto(r.get('tipo')), 'actividad_economica': _texto(r.get('actividad_economica')),
             'numero_trabajadores': _texto(r.get('numero_trabajadores')),
             'tipo_facturacion': _texto(r.get('tipo_facturacion')),
             'tipo_contabilidad': _texto(r.get('tipo_contabilidad')),
             'comercio_exterior': _texto(r.get('comercio_exterior')),
             'agente_retencion': _si_no(r.get('es_agente_retencion')),
             'buen_contribuyente': _si_no(r.get('es_buen_contribuyente')),
             'locales_anexos': locales,
             'n_locales': len(locales) or (int(r['locales_anexos']) if str(r.get('locales_anexos') or '')
                                           .isdigit() else 0)}
    if not datos['razon_social']:
        raise ErrorPadron('SUNAT no devolvió datos para ese RUC.')
    alertas = [x for x in (datos['estado'] if datos['estado'] not in ('', 'ACTIVO') else '',
                           datos['condicion'] if datos['condicion'] not in ('', 'HABIDO') else '') if x]
    datos['alerta'] = f"RUC {' · '.join(alertas)} en SUNAT" if alertas else ''
    return datos
