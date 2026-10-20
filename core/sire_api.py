"""Conexión con la API del SIRE de SUNAT (registros de ventas RVIE y de compras RCE).

Según los manuales oficiales «Servicio API del Registro de Ventas e Ingresos Electrónico» (v31) y «Servicios Web API
SIRE Compras» (v28) publicados en cpe.sunat.gob.pe:
1. Token: POST api-seguridad .../clientessol/{client_id}/oauth2/token/ (grant_type=password, scope
   https://api-sire.sunat.gob.pe, username = RUC + usuario SOL, password = clave SOL).
2. Descargar propuesta: GET .../rvie/propuesta/web/propuesta/{periodo}/exportapropuesta (ventas) o
   .../rce/propuesta/web/propuesta/{periodo}/exportacioncomprobantepropuesta (compras) → numTicket.
3. Consultar el ticket: GET .../rvierce/gestionprocesosmasivos/web/masivo/consultaestadotickets → estado 06 Terminado
   y el nombre del archivo.
4. Descargar el archivo: GET .../rvierce/gestionprocesosmasivos/web/masivo/archivoreporte → ZIP con el TXT.
5. Aceptar la propuesta: POST .../rvie/propuesta/web/propuesta/{periodo}/aceptapropuesta (ventas) o
   .../rce/propuesta/web/registroslibros/{periodo}/aceptarpropuesta (compras) → numTicket.

La clave SOL no se guarda: se usa una vez para pedir el token, que queda en la caché mientras SUNAT lo da por válido.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from django.core.cache import cache

from .models import Empresa

TOKEN_URL = 'https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/'
SCOPE = 'https://api-sire.sunat.gob.pe'
BASE = 'https://api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros'
COD_LIBRO = {'RVIE': '140000', 'RCE': '080000'}
EXPORTAR = {'RVIE': '/rvie/propuesta/web/propuesta/{periodo}/exportapropuesta?codTipoArchivo=0',
            'RCE': '/rce/propuesta/web/propuesta/{periodo}/exportacioncomprobantepropuesta'
                   '?codTipoArchivo=0&codOrigenEnvio=2'}
ACEPTAR = {'RVIE': '/rvie/propuesta/web/propuesta/{periodo}/aceptapropuesta',
           'RCE': '/rce/propuesta/web/registroslibros/{periodo}/aceptarpropuesta'}
TICKETS = '/rvierce/gestionprocesosmasivos/web/masivo/consultaestadotickets'
ARCHIVO = '/rvierce/gestionprocesosmasivos/web/masivo/archivoreporte'
TERMINADO = '06'


class ErrorSIRE(Exception):
    pass


def _credenciales(empresa=None):
    e = empresa or Empresa.actual()
    return (e.sire_client_id or e.sunat_client_id), (e.sire_client_secret or e.sunat_client_secret)


def configurada(empresa=None):
    e = empresa or Empresa.actual()
    cid, secreto = _credenciales(e)
    return bool(cid and secreto and e.sire_usuario_sol and e.ruc)


def _clave_cache(e):
    return f'sire-token-{e.ruc}-{e.sire_usuario_sol}'


def hay_token():
    e = Empresa.actual()
    return bool(cache.get(_clave_cache(e)))


def _http(metodo, url, datos=None, cabeceras=None, binario=False):
    req = urllib.request.Request(url, data=datos, method=metodo,
                                 headers={'User-Agent': 'Ceiba-ERP', 'Accept': 'application/json', **(cabeceras or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            cuerpo = resp.read()
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode('utf-8', 'ignore')[:500]
        if exc.code in (401, 403):
            raise ErrorSIRE('SUNAT no aceptó la sesión (token vencido o sin permiso al SIRE). Vuelva a ingresar la '
                            'clave SOL.') from exc
        raise ErrorSIRE(f'SUNAT respondió {exc.code}: {_mensaje(detalle)}') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErrorSIRE(f'No se pudo conectar con SUNAT: {exc}') from exc
    if binario:
        return cuerpo
    texto = cuerpo.decode('utf-8', 'ignore').strip()
    return json.loads(texto) if texto else {}


def _mensaje(texto):
    """El mensaje legible de una respuesta de error de SUNAT ({cod, msg, errors: [{cod, msg}]})."""
    try:
        datos = json.loads(texto)
    except ValueError:
        return texto
    errores = '; '.join(f'{e.get("cod")} {e.get("msg")}' for e in datos.get('errors') or [])
    return errores or datos.get('msg') or datos.get('error_description') or texto


def iniciar_sesion(clave_sol):
    """Pide el token con la clave SOL (que no se guarda). Lo deja en la caché por el tiempo que SUNAT indique."""
    e = Empresa.actual()
    if not configurada(e):
        raise ErrorSIRE('Registre en Ajustes › Empresa el client_id, el client_secret y el usuario SOL del SIRE.')
    if not clave_sol:
        raise ErrorSIRE('Ingrese la clave SOL.')
    cid, secreto = _credenciales(e)
    datos = urllib.parse.urlencode({'grant_type': 'password', 'scope': SCOPE, 'client_id': cid,
                                    'client_secret': secreto, 'username': f'{e.ruc}{e.sire_usuario_sol}',
                                    'password': clave_sol}).encode()
    resp = _http('POST', TOKEN_URL.format(client_id=cid), datos,
                 {'Content-Type': 'application/x-www-form-urlencoded'})
    token = resp.get('access_token')
    if not token:
        raise ErrorSIRE(f'SUNAT no entregó el token: {resp.get("error_description") or resp}')
    cache.set(_clave_cache(e), token, max(int(resp.get('expires_in') or 3600) - 120, 60))
    return token


def _token():
    token = cache.get(_clave_cache(Empresa.actual()))
    if not token:
        raise ErrorSIRE('La sesión con SUNAT venció: ingrese de nuevo la clave SOL.')
    return token


def _api(metodo, ruta, binario=False):
    return _http(metodo, BASE + ruta, b'' if metodo == 'POST' else None,
                 {'Authorization': f'Bearer {_token()}', 'Content-Type': 'application/json'}, binario=binario)


def solicitar_propuesta(libro, periodo):
    """Pide a SUNAT el archivo de la propuesta del periodo; devuelve el número de ticket."""
    resp = _api('GET', EXPORTAR[libro].format(periodo=periodo))
    ticket = resp.get('numTicket')
    if not ticket:
        raise ErrorSIRE(f'SUNAT no devolvió ticket: {resp}')
    return str(ticket)


def estado_ticket(libro, periodo, ticket):
    """{'terminado', 'error', 'descripcion', 'registro'} del ticket (registro = datos para descargar el archivo)."""
    params = {'perIni': periodo, 'perFin': periodo, 'page': 1, 'perPage': 20, 'numTicket': ticket}
    if libro == 'RCE':
        params.update(codLibro=COD_LIBRO[libro], codOrigenEnvio=2)
    resp = _api('GET', f'{TICKETS}?{urllib.parse.urlencode(params)}')
    registros = resp.get('registros') or []
    r = next((x for x in registros if str(x.get('numTicket')) == str(ticket)), registros[0] if registros else None)
    if r is None:
        return {'terminado': False, 'error': False, 'descripcion': 'SUNAT aún no registra el ticket', 'registro': None}
    codigo = str(r.get('codEstadoProceso') or '')
    descripcion = r.get('desEstadoProceso') or codigo
    error = 'error' in descripcion.lower() or 'rechaz' in descripcion.lower()
    return {'terminado': codigo == TERMINADO or descripcion.lower().startswith('terminado'), 'error': error,
            'descripcion': descripcion, 'registro': r}


def descargar_archivo(libro, registro):
    """(nombre, bytes) del archivo de la propuesta (ZIP con el TXT)."""
    archivos = registro.get('archivoReporte') or []
    if not archivos:
        raise ErrorSIRE('El ticket terminó pero SUNAT no indicó el archivo de la propuesta.')
    a = archivos[0]
    detalle = registro.get('detalleTicket') or {}
    params = {'nomArchivoReporte': a.get('nomArchivoReporte'),
              'codTipoArchivoReporte': a.get('codTipoAchivoReporte') or a.get('codTipoArchivoReporte') or 'null',
              'perTributario': registro.get('perTributario'), 'codProceso': registro.get('codProceso'),
              'numTicket': detalle.get('numTicket') or registro.get('numTicket'), 'codLibro': COD_LIBRO[libro]}
    return a.get('nomArchivoReporte') or 'propuesta.zip', _api('GET', f'{ARCHIVO}?{urllib.parse.urlencode(params)}',
                                                               binario=True)


def aceptar_propuesta(libro, periodo):
    """Acepta la propuesta en SUNAT (queda como preliminar del registro del periodo). Devuelve el ticket."""
    resp = _api('POST', ACEPTAR[libro].format(periodo=periodo))
    return str(resp.get('numTicket') or '')
