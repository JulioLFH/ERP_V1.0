"""Descarga del XML de un comprobante electrónico desde SUNAT (servicio «consultacpe», v1.116.1).

Con el RUC del emisor, el tipo, la serie y el número, SUNAT devuelve el XML (o el PDF o el CDR) del comprobante, también
de los que la empresa RECIBE (sus compras). Sirve para traer los productos de las compras creadas desde la propuesta
del SIRE, que solo trae totales.

SUNAT no publica un manual completo de este servicio: lo que sigue sale de la documentación de proveedores que lo usan y
se ajustó con la respuesta real de SUNAT. Se usa el mismo usuario SOL y la misma aplicación (client_id / client_secret)
del SIRE, con el «scope» del API de comprobantes; la aplicación debe tener habilitado el recurso de consulta de CPE en
SUNAT Operaciones en Línea › Credenciales de API SUNAT.
"""
import base64
import io
import json
import zipfile

from django.core.cache import cache

from . import sire_api
from .models import Empresa

SCOPE = 'https://api-cpe.sunat.gob.pe'
URL = 'https://api-cpe.sunat.gob.pe/v1/contribuyente/consultacpe/comprobantes/{ruc}-{tipo}-{serie}-{numero}-{origen}/{archivo}'
XML, PDF, CDR = '02', '01', '03'
ORIGEN_RECEPTOR, ORIGEN_EMISOR = '2', '1'  # quién consulta: la empresa como receptora (compras) o como emisora


class ErrorCPE(Exception):
    pass


def _clave_cache(e):
    return f'cpe-token-{e.ruc}-{e.sire_usuario_sol}'


def token():
    """Token del API de comprobantes con el usuario SOL y la aplicación del SIRE (se guarda en la caché)."""
    import urllib.parse
    e = Empresa.actual()
    guardado = cache.get(_clave_cache(e))
    if guardado:
        return guardado
    if not sire_api.configurada(e):
        raise ErrorCPE('Registre en Ajustes › Credenciales SUNAT y SIRE el client_id, el client_secret y el usuario '
                       'SOL del SIRE: son los mismos para traer los comprobantes de SUNAT.')
    clave = sire_api.clave_guardada(e)
    if not clave:
        raise ErrorCPE(sire_api.FALTA_CLAVE)
    cid, secreto = sire_api._credenciales(e)
    datos = urllib.parse.urlencode({'grant_type': 'password', 'scope': SCOPE, 'client_id': cid,
                                    'client_secret': secreto, 'username': f'{e.ruc}{e.sire_usuario_sol}',
                                    'password': clave}).encode()
    try:
        resp = sire_api._http('POST', sire_api.TOKEN_URL.format(client_id=cid), datos,
                              {'Content-Type': 'application/x-www-form-urlencoded'})
    except sire_api.SesionVencida as exc:
        raise ErrorCPE(sire_api.CLAVE_RECHAZADA) from exc
    except sire_api.ErrorSIRE as exc:
        raise ErrorCPE(str(exc)) from exc
    tok = resp.get('access_token')
    if not tok:
        raise ErrorCPE(f'SUNAT no entregó el token: {resp.get("error_description") or resp}')
    cache.set(_clave_cache(e), tok, max(int(resp.get('expires_in') or 3600) - 120, 60))
    return tok


def _extraer_xml(cuerpo):
    """El XML del comprobante desde la respuesta: XML directo, ZIP, o JSON con el archivo en base64."""
    datos = cuerpo
    if datos[:1] in (b'{', b'['):
        try:
            j = json.loads(datos.decode('utf-8', 'ignore'))
        except ValueError:
            j = None
        if isinstance(j, dict):
            texto = next((v for k, v in j.items() if isinstance(v, str) and len(v) > 100
                          and any(x in k.lower() for x in ('archivo', 'data', 'contenido', 'file', 'base64'))), None)
            if texto is None:
                raise ErrorCPE(f'SUNAT no devolvió el archivo: {sire_api._mensaje(datos.decode("utf-8", "ignore"))}')
            datos = base64.b64decode(texto)
    if datos[:2] == b'PK':
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            nombres = [n for n in z.namelist() if n.lower().endswith('.xml') and not
                       n.rsplit('/', 1)[-1].upper().startswith('R-')]
            if not nombres:
                raise ErrorCPE('El ZIP de SUNAT no trae el XML del comprobante.')
            return z.read(nombres[0])
    if datos.lstrip()[:1] == b'<':
        return datos
    raise ErrorCPE('SUNAT respondió algo que no es el XML del comprobante.')


def descargar_xml(ruc_emisor, tipo, serie, numero, origen=ORIGEN_RECEPTOR):
    """bytes del XML del comprobante (por defecto como receptor: las compras de la empresa)."""
    url = URL.format(ruc=ruc_emisor, tipo=tipo, serie=serie, numero=int(numero) if str(numero).isdigit() else numero,
                     origen=origen, archivo=XML)
    try:
        cuerpo = sire_api._http('GET', url, None, {'Authorization': f'Bearer {token()}'}, binario=True)
    except sire_api.SesionVencida as exc:
        cache.delete(_clave_cache(Empresa.actual()))
        raise ErrorCPE('SUNAT no da acceso a la consulta de comprobantes con esa aplicación: en Credenciales de API '
                       'SUNAT habilite el recurso de consulta de CPE (consultacpe). '
                       f'Respuesta de SUNAT: {getattr(exc, "respuesta", "")}') from exc
    except sire_api.ErrorSIRE as exc:
        raise ErrorCPE(str(exc)) from exc
    return _extraer_xml(cuerpo)
