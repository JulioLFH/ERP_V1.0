"""Consulta integrada de validez de comprobantes de pago (API SUNAT).

Credenciales: SUNAT Operaciones en Línea > Empresas > Comprobantes de pago > Consulta de validez >
Credenciales de API SUNAT (client_id y client_secret), registradas en Ajustes > Empresa.
"""
import json
import urllib.parse
import urllib.request

from django.core.cache import cache

from .models import Empresa

TOKEN_URL = 'https://api-seguridad.sunat.gob.pe/v1/clientesextranet/{client_id}/oauth2/token/'
SCOPE = 'https://api.sunat.gob.pe/v1/contribuyente/contribuyentes'
CONSULTA_URL = 'https://api.sunat.gob.pe/v1/contribuyente/contribuyentes/{ruc}/validarcomprobante'
ESTADOS_CP = {'0': 'NO EXISTE', '1': 'ACEPTADO', '2': 'ANULADO', '3': 'AUTORIZADO', '4': 'NO AUTORIZADO'}
ESTADOS_RUC = {'00': 'ACTIVO', '01': 'BAJA PROVISIONAL', '02': 'BAJA PROV. POR OFICIO', '03': 'SUSPENSION TEMPORAL',
               '10': 'BAJA DEFINITIVA', '11': 'BAJA DE OFICIO', '22': 'INHABILITADO-VENT.UNICA'}
CONDICION = {'00': 'HABIDO', '09': 'PENDIENTE', '11': 'POR VERIFICAR', '12': 'NO HABIDO', '20': 'NO HALLADO'}
VALIDOS = ('1', '3')


class ErrorConsulta(Exception):
    pass


def configurada():
    e = Empresa.actual()
    return bool(e.sunat_client_id and e.sunat_client_secret)


def _post(url, datos, cabeceras):
    req = urllib.request.Request(url, data=datos, headers={'User-Agent': 'Ceiba-ERP', **cabeceras}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _token(empresa):
    clave = f'sunat-token-{empresa.sunat_client_id}'
    token = cache.get(clave)
    if token:
        return token
    datos = urllib.parse.urlencode({'grant_type': 'client_credentials', 'scope': SCOPE,
                                    'client_id': empresa.sunat_client_id,
                                    'client_secret': empresa.sunat_client_secret}).encode()
    try:
        resp = _post(TOKEN_URL.format(client_id=empresa.sunat_client_id), datos,
                     {'Content-Type': 'application/x-www-form-urlencoded'})
    except Exception as exc:
        raise ErrorConsulta(f'SUNAT no entregó el token (revise client_id y client_secret): {exc}') from exc
    token = resp.get('access_token')
    if not token:
        raise ErrorConsulta(f'SUNAT no entregó el token: {resp}')
    cache.set(clave, token, max(int(resp.get('expires_in', 3600)) - 120, 60))
    return token


def validar(ruc_emisor, tipo, serie, numero, fecha, monto):
    """Consulta el comprobante. Devuelve {'codigo', 'estado', 'valido', 'detalle'}; lanza ErrorConsulta."""
    empresa = Empresa.actual()
    if not configurada():
        raise ErrorConsulta('Registre las credenciales de la API SUNAT en Ajustes > Empresa.')
    cuerpo = {'numRuc': ruc_emisor, 'codComp': tipo, 'numeroSerie': serie, 'numero': str(int(numero)),
              'fechaEmision': fecha.strftime('%d/%m/%Y'), 'monto': f'{monto:.2f}'}
    try:
        resp = _post(CONSULTA_URL.format(ruc=empresa.ruc), json.dumps(cuerpo).encode(),
                     {'Content-Type': 'application/json', 'Authorization': f'Bearer {_token(empresa)}'})
    except ErrorConsulta:
        raise
    except Exception as exc:
        raise ErrorConsulta(f'No se pudo consultar a SUNAT: {exc}') from exc
    if not resp.get('success'):
        raise ErrorConsulta(f'SUNAT: {resp.get("message") or resp}')
    data = resp.get('data') or {}
    codigo = str(data.get('estadoCp', ''))
    partes = [f'Comprobante {ESTADOS_CP.get(codigo, codigo or "sin estado")}']
    if data.get('estadoRuc'):
        partes.append(f'RUC {ESTADOS_RUC.get(data["estadoRuc"], data["estadoRuc"])}')
    if data.get('condDomiRuc'):
        partes.append(CONDICION.get(data['condDomiRuc'], data['condDomiRuc']))
    partes += data.get('observaciones') or []
    return {'codigo': codigo, 'estado': ESTADOS_CP.get(codigo, codigo), 'valido': codigo in VALIDOS,
            'detalle': ' · '.join(partes)}
