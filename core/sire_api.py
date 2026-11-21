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
6. Enviar lo registrado (SIR-01/02): reemplazo de la propuesta, no domiciliados y ajustes posteriores se suben como
   ZIP con TUS (subir_archivo) → numTicket; luego se registra el preliminar (registrar_preliminar).
7. Libro oficial: el registro ya generado del periodo (solicitar_registro → numTicket → archivoreporte) y su constancia
   de recepción en PDF (descargar_constancia).

El usuario y la clave SOL se registran una vez en Ajustes › Credenciales SUNAT y SIRE (la clave cifrada) y se usan solos para pedir el
token cada vez que vence: la sesión se mantiene sin pedir nada. Solo si faltan allí, o SUNAT no los acepta, se avisa
para corregirlos en Ajustes › Credenciales SUNAT y SIRE (el módulo SUNAT no pide la clave).
"""
import json
import re
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
FINALES = ('terminado', 'procesado', 'finalizado')  # estado del ticket ya resuelto en SUNAT (por su descripción)


class ErrorSIRE(Exception):
    pass


class SesionVencida(ErrorSIRE):
    """SUNAT rechazó el token (401 / 403): con la clave guardada se pide otro y se reintenta una vez."""


def _credenciales(empresa=None):
    e = empresa or Empresa.actual()
    return (e.sire_client_id or e.sunat_client_id), (e.sire_client_secret or e.sunat_client_secret)


def configurada(empresa=None):
    e = empresa or Empresa.actual()
    cid, secreto = _credenciales(e)
    return bool(cid and secreto and e.sire_usuario_sol and e.ruc)


def _clave_cache(e):
    return f'sire-token-{e.ruc}-{e.sire_usuario_sol}'


def clave_guardada(empresa=None):
    """La clave SOL del SIRE registrada en Ajustes › Credenciales SUNAT y SIRE ('' si no hay o no se puede leer)."""
    from .cifrado import descifrar
    e = empresa or Empresa.actual()
    return descifrar(e.sire_clave_sol)


def hay_token():
    """¿Se puede trabajar con SUNAT? (sesión activa o clave guardada en Ajustes › Credenciales SUNAT y SIRE)."""
    e = Empresa.actual()
    return bool(cache.get(_clave_cache(e)) or clave_guardada(e))


FALTA_CLAVE = ('Falta la clave SOL del SIRE en Ajustes › Credenciales SUNAT y SIRE. Regístrela allí una vez (con el usuario SOL) y el '
               'sistema mantiene la sesión con SUNAT solo.')


CLAVE_RECHAZADA = ('SUNAT rechazó el ingreso con el usuario SOL y la clave SOL del SIRE (o con el client_id y el '
                   'client_secret). Revíselos en Ajustes › Credenciales SUNAT y SIRE.')
SIN_PERMISO_SIRE = ('SUNAT aceptó el usuario y la clave SOL, pero no da acceso al SIRE con esas credenciales. En SUNAT '
                    'Operaciones en Línea revise que la aplicación del client_id (Credenciales de API SUNAT) tenga el '
                    'SIRE y, si es un usuario secundario, que tenga el perfil del SIRE; luego registre en Ajustes › '
                    'Credenciales SUNAT y SIRE el client_id y el client_secret del SIRE.')


def _http(metodo, url, datos=None, cabeceras=None, binario=False):
    req = urllib.request.Request(url, data=datos, method=metodo,
                                 headers={'User-Agent': 'Ceiba-ERP', 'Accept': 'application/json', **(cabeceras or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            cuerpo = resp.read()
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode('utf-8', 'ignore')[:500]
        if exc.code in (401, 403):
            error = SesionVencida('SUNAT no aceptó la sesión (token vencido, clave SOL errada o usuario sin permiso al '
                                  'SIRE). Revise la clave SOL en Ajustes › Credenciales SUNAT y SIRE.')
            error.respuesta = f'{exc.code}: {_mensaje(detalle) or "sin detalle"}'  # v1.115.4: lo que dijo SUNAT
            raise error from exc
        raise ErrorSIRE(_error_http(exc.code, detalle)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErrorSIRE(f'No se pudo conectar con SUNAT: {exc}') from exc
    if binario:
        return cuerpo
    texto = cuerpo.decode('utf-8', 'ignore').strip()
    return json.loads(texto) if texto else {}


DEMASIADAS_CONSULTAS = ('SUNAT está limitando las consultas de esta cuenta al SIRE (429: demasiadas solicitudes en '
                        'poco tiempo). No es un error de los datos: espere 15 minutos y vuelva a intentarlo una sola '
                        'vez, sin repetir el botón.')


def _error_http(codigo, detalle):
    """Mensaje para un error HTTP de SUNAT (v1.115.1): el 429 explica que hay que esperar; nunca muestra HTML."""
    if codigo == 429:
        return DEMASIADAS_CONSULTAS
    return f'SUNAT respondió {codigo}: {_mensaje(detalle)}'


def _mensaje(texto):
    """El mensaje legible de una respuesta de error de SUNAT ({cod, msg, errors: [{cod, msg}]}). Si SUNAT responde
    una página HTML (balanceador, mantenimiento), solo su título."""
    texto = texto or ''
    if texto.lstrip().startswith('<'):
        titulo = re.search(r'<title>(.*?)</title>', texto, re.I | re.S)
        return (titulo.group(1).strip() if titulo else 'respuesta no válida del servidor')[:200]
    try:
        datos = json.loads(texto)
    except ValueError:
        return texto[:500]
    errores = '; '.join(f'{e.get("cod")} {e.get("msg")}' for e in datos.get('errors') or [])
    return errores or datos.get('msg') or datos.get('error_description') or texto


def iniciar_sesion():
    """Pide el token con el usuario y la clave SOL de Ajustes › Credenciales SUNAT y SIRE. Lo deja en la caché por el tiempo que SUNAT
    indique."""
    e = Empresa.actual()
    from .cifrado import ilegible
    if ilegible(_credenciales(e)[1]):  # v1.115: guardado con otra llave del servidor; no se borra, se pide de nuevo
        raise ErrorSIRE('El client_secret del SIRE está guardado con otra llave del servidor y no se puede leer: '
                        'vuelva a ingresarlo en Ajustes › Credenciales SUNAT y SIRE.')
    if not configurada(e):
        raise ErrorSIRE('Registre en Ajustes › Credenciales SUNAT y SIRE el client_id, el client_secret y el usuario '
                        'SOL del SIRE.')
    clave_sol = clave_guardada(e)
    if not clave_sol:
        raise ErrorSIRE(FALTA_CLAVE)
    cid, secreto = _credenciales(e)
    datos = urllib.parse.urlencode({'grant_type': 'password', 'scope': SCOPE, 'client_id': cid,
                                    'client_secret': secreto, 'username': f'{e.ruc}{e.sire_usuario_sol}',
                                    'password': clave_sol}).encode()
    try:
        resp = _http('POST', TOKEN_URL.format(client_id=cid), datos,
                     {'Content-Type': 'application/x-www-form-urlencoded'})
    except SesionVencida as exc:
        raise ErrorSIRE(CLAVE_RECHAZADA) from exc
    token = resp.get('access_token')
    if not token:
        raise ErrorSIRE(f'SUNAT no entregó el token: {resp.get("error_description") or resp}')
    cache.set(_clave_cache(e), token, max(int(resp.get('expires_in') or 3600) - 120, 60))
    return token


def _token():
    e = Empresa.actual()
    token = cache.get(_clave_cache(e))
    if not token and clave_guardada(e):  # con la clave guardada no se vuelve a pedir
        token = iniciar_sesion()
    if not token:
        raise ErrorSIRE(FALTA_CLAVE)
    return token


def _api(metodo, ruta, binario=False):
    def llamar():
        return _http(metodo, BASE + ruta, b'' if metodo == 'POST' else None,
                     {'Authorization': f'Bearer {_token()}', 'Content-Type': 'application/json'}, binario=binario)
    return _con_sesion(llamar)


def _con_sesion(llamar):
    """Llama a SUNAT con el token vigente; si lo rechaza, pide otro con la clave guardada y reintenta una vez."""
    try:
        return llamar()
    except SesionVencida:
        e = Empresa.actual()
        if not clave_guardada(e):
            raise
        cache.delete(_clave_cache(e))  # el token pudo vencer antes de lo indicado: una nueva sesión y un reintento
        try:
            return llamar()
        except SesionVencida as exc:
            # el usuario y la clave SOL valen (SUNAT acaba de dar la sesión), pero esa sesión no entra al SIRE
            respuesta = getattr(exc, 'respuesta', '')
            raise ErrorSIRE(SIN_PERMISO_SIRE + (f' Respuesta de SUNAT: {respuesta}' if respuesta else '')) from exc


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
    texto = descripcion.lower()
    error = 'error' in texto or 'rechaz' in texto
    # v1.116.9: los envíos (reemplazo, 8.5, ajustes) terminan en «Procesado» (o «Procesado con Errores»), no en
    # «Terminado» como la propuesta: antes «Procesado» se quedaba para siempre en «SUNAT lo procesa»
    terminado = codigo == TERMINADO or texto.startswith(FINALES)
    return {'terminado': terminado, 'error': error, 'descripcion': descripcion, 'registro': r}


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


# ---------------------------------------------------------------- envío de archivos al SIRE (SIR-01 y SIR-02)
# Según los manuales «Servicios Web API SIRE Compras» (v28, servicios 5.3, 5.4, 5.5, 5.18, 5.19, 5.21) y «Servicio API
# del RVIE» (v31, servicios 5.3 y de ajustes posteriores): el ZIP con el TXT se sube con el protocolo TUS 1.0.0 (POST
# que crea la carga con los metadatos en «Upload-Metadata» y PATCH con el contenido); SUNAT responde el numTicket,
# que se sigue con consultaestadotickets (estado_ticket) como la propuesta.
SUBIR = {'propuesta': '/rvierce/receptorpropuesta/web/propuesta/upload',  # 5.3 reemplazo de la propuesta
         'preliminar': '/rvierce/receptorpreliminar/web/preliminar/upload',  # 5.5 no domiciliados (RCE)
         'ajustes': '/rvierce/receptorajustesposteriores/web/ajustesposteriores/upload'}  # 5.18 / 5.21
# (código de registro, oportunidad) -> (servicio, codProceso del Anexo I «Indicador de carga masiva»). Los de ajustes
# de periodos anteriores (04 y 05) se tomaron del Anexo I (general / simplificado): verifíquelos con el manual vigente.
PROCESO_CARGA = {
    ('080400', '02'): ('propuesta', '61'), ('140400', '02'): ('propuesta', '3'), ('080500', '02'): ('preliminar', '56'),
    ('080400', '03'): ('ajustes', '6'), ('140400', '03'): ('ajustes', '87'), ('080500', '03'): ('ajustes', '60'),
    ('080400', '04'): ('ajustes', '94'), ('140400', '04'): ('ajustes', '88'), ('080500', '04'): ('ajustes', '95'),
    ('080400', '05'): ('ajustes', '93'), ('140400', '05'): ('ajustes', '89'), ('080500', '05'): ('ajustes', '95'),
}
ORIGEN_SERVICIO_WEB = '2'
CORRELATIVO_MASIVO = '01'
# después de la carga: 5.4 registrar preliminar (reemplazo 8.4 y no domiciliados 8.5; en ventas, el de gestionlibro) y
# 5.19 enviar ajustes posteriores (RCE). Los ajustes del RVIE se generan luego en el portal SIRE.
REGISTRAR_PRELIMINAR = {'RCE': '/rce/preliminar/web/registroslibros/{periodo}/registrapreliminares',
                        'RVIE': '/rvierce/gestionlibro/web/registroslibros/{periodo}/registrapreliminar'}
REGISTRAR_AJUSTES = {'RCE': '/rce/ajustesposteriores/web/comprobantesajuspost/{periodo}/1/registrarajustesposterioresrc'}


def _http_crudo(metodo, url, datos, cabeceras):
    """(cabeceras, cuerpo) de la respuesta; los errores como en _http (401/403 = sesión vencida)."""
    req = urllib.request.Request(url, data=datos, method=metodo,
                                 headers={'User-Agent': 'Ceiba-ERP', 'Accept': 'application/json', **cabeceras})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode('utf-8', 'ignore')[:500]
        if exc.code in (401, 403):
            raise SesionVencida('SUNAT no aceptó la sesión al subir el archivo.') from exc
        raise ErrorSIRE(_error_http(exc.code, detalle)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErrorSIRE(f'No se pudo conectar con SUNAT: {exc}') from exc


def _tus_subir(url, contenido, metadatos):
    """Sube el archivo con TUS 1.0.0: crea la carga (POST) y envía todo el contenido en un PATCH. Devuelve el ticket."""
    import base64
    meta = ','.join(f'{k} {base64.b64encode(str(v).encode()).decode()}' for k, v in metadatos.items())

    def subir():
        cab = {'Authorization': f'Bearer {_token()}', 'Tus-Resumable': '1.0.0'}
        encabezados, _ = _http_crudo('POST', url, b'', {**cab, 'Upload-Length': str(len(contenido)),
                                                         'Upload-Metadata': meta})
        destino = {k.lower(): v for k, v in encabezados.items()}.get('location')
        if not destino:
            raise ErrorSIRE('SUNAT no indicó dónde subir el archivo (falta Location en la respuesta).')
        encabezados, cuerpo = _http_crudo('PATCH', urllib.parse.urljoin(url, destino), contenido, {
            **cab, 'Upload-Offset': '0', 'Content-Type': 'application/offset+octet-stream'})
        texto = (cuerpo or b'').decode('utf-8', 'ignore').strip()
        try:
            datos = json.loads(texto) if texto else {}
        except ValueError:
            datos = {}
        if isinstance(datos, dict):
            ticket = datos.get('numTicket')
        else:  # v1.115.1: SUNAT responde el ticket solo, como número («20260100000016»)
            ticket = str(datos) if str(datos).isdigit() else None
        ticket = ticket or {k.lower(): v for k, v in encabezados.items()}.get('numticket')
        if not ticket:
            raise ErrorSIRE(f'SUNAT recibió el archivo pero no devolvió ticket: {texto[:300] or "respuesta vacía"}')
        return str(ticket)
    return _con_sesion(subir)


def subir_archivo(libro, registro, oportunidad, periodo, nombre_zip, contenido):
    """Envía al SIRE el ZIP con el TXT (reemplazo, no domiciliados o ajuste posterior). Devuelve el número de ticket."""
    clave = PROCESO_CARGA.get((registro, oportunidad))
    if clave is None:
        raise ErrorSIRE(f'SUNAT no recibe el registro {registro} con la oportunidad {oportunidad}.')
    servicio, proceso = clave
    e = Empresa.actual()
    return _tus_subir(BASE + SUBIR[servicio], contenido, {
        'filename': nombre_zip, 'filetype': 'application/zip', 'numRuc': e.ruc, 'perTributario': periodo,
        'codOrigenEnvio': ORIGEN_SERVICIO_WEB, 'codProceso': proceso, 'codTipoCorrelativo': CORRELATIVO_MASIVO,
        'nomArchivoImportacion': nombre_zip, 'codLibro': COD_LIBRO[libro]})


def registrar_preliminar(libro, periodo, ajuste=False):
    """Registra en SUNAT el preliminar (5.4) o los ajustes posteriores (5.19) ya cargados. Devuelve el ticket ('' si
    SUNAT terminó sin ticket)."""
    rutas = REGISTRAR_AJUSTES if ajuste else REGISTRAR_PRELIMINAR
    if libro not in rutas:
        raise ErrorSIRE('Los ajustes posteriores del RVIE se generan en el portal SIRE después de la carga.')
    resp = _api('POST', rutas[libro].format(periodo=periodo))
    return str((resp or {}).get('numTicket') or '') if isinstance(resp, dict) else ''


# ---------------------------------------------------------------- libro oficial: el registro generado en el SIRE
# Según los manuales «Servicios Web API SIRE Compras» (v29, servicios 5.49, 5.51, 5.31 y 5.32) y «Servicios Web API
# SIRE Ventas» (v32, servicios 5.26, 5.27, 5.16 y 5.17) de cpe.sunat.gob.pe: el registro ya generado del periodo se pide
# con un servicio que devuelve numTicket; el ticket se sigue con consultaestadotickets (estado_ticket) y el ZIP se baja
# con archivoreporte (descargar_archivo), como la propuesta. La constancia de recepción (PDF) se baja con su nombre
# (LE + RUC + periodo + 00 + registro + indicadores + .pdf: el mismo nombre del TXT del registro con .pdf).
#
# VERIFICAR codProceso: el manual de compras describe codProceso como «00 / 01 / 02» pero su ejemplo usa 70 («Descarga
# RCE» del Anexo I); el de ventas usa en su ejemplo 23 («Generar reporte Libro RVIE»; el Anexo I también trae 74
# «Descarga RVIE»). Se toman los de los ejemplos de cada servicio: si SUNAT responde 1139 «Código de Proceso no
# permitido», cambie solo PROCESO_REGISTRO.
DESCARGAR_REGISTRO = {
    # RCE 5.51 «Descargar RCE por periodo»
    'RCE': '/rce/ajustesposteriores/web/ajustesposteriores/{periodo}/solicitardescarga',
    # RVIE 5.27 «Descargar RVIE por periodo»
    'RVIE': '/rvierce/gestionlibro/web/registroslibros/descarga',
}
PROCESO_REGISTRO = {'RCE': '70', 'RVIE': '23'}
# constancia de recepción: RCE 5.49 (devuelve los bytes del PDF) y RVIE 5.26 (devuelve el PDF en base64)
CONSTANCIA = {'RCE': '/rvierce/gestionlibro/web/registroslibros/constancia/constanciarecepcion?nomConstanciaRecepcion=',
              'RVIE': '/rvierce/gestionlibro/web/registroslibros/constancia/archivo?nomArchivo='}
CODIGO_REGISTRO = {'RCE': '080400', 'RVIE': '140400'}


def solicitar_registro(libro, periodo):
    """Pide a SUNAT el archivo del registro generado (libro oficial) del periodo; devuelve el número de ticket."""
    if libro == 'RCE':
        params = {'codTipoArchivo': 0, 'codMoneda': 'PEN', 'codProceso': PROCESO_REGISTRO[libro],
                  'codOrigen': ORIGEN_SERVICIO_WEB, 'lisPeriodos': periodo}
        ruta = DESCARGAR_REGISTRO[libro].format(periodo=periodo)
    else:
        params = {'perTributario': periodo, 'codOrigenEnvio': ORIGEN_SERVICIO_WEB, 'codTipoArchivo': 0,
                  'codProceso': PROCESO_REGISTRO[libro], 'codLibro': COD_LIBRO[libro]}
        ruta = DESCARGAR_REGISTRO[libro]
    resp = _api('GET', f'{ruta}?{urllib.parse.urlencode(params)}')
    ticket = resp.get('numTicket') if isinstance(resp, dict) else resp
    if not ticket or not str(ticket).strip().isdigit():
        raise ErrorSIRE(f'SUNAT no devolvió ticket del registro: {resp}')
    return str(ticket).strip()


def nombre_constancia(*nombres):
    """Nombre del PDF de la constancia de recepción a partir del nombre del TXT o ZIP del registro generado
    (LE…080400… / LE…140400…); '' si ninguno tiene esa forma."""
    for nombre in nombres:
        m = re.search(r'(LE\d{11}\d{6}00(?:080400|140400)\d+)', str(nombre or ''))
        if m:
            return m.group(1) + '.pdf'
    return ''


def _pdf(contenido):
    """Los bytes del PDF de la constancia: SUNAT lo entrega crudo, como arreglo de bytes o en base64 (en JSON o no)."""
    import base64
    datos = contenido or b''
    if datos[:4] == b'%PDF':
        return datos
    texto = datos.decode('utf-8', 'ignore').strip()
    try:
        valor = json.loads(texto)
    except ValueError:
        valor = texto
    if isinstance(valor, dict):
        valor = valor.get('archivoPdf') or valor.get('archivo') or valor.get('contenido') or ''
    if isinstance(valor, list):
        try:
            return bytes(b & 0xFF for b in valor)
        except TypeError:
            valor = ''
    try:
        pdf = base64.b64decode(str(valor or ''), validate=False)
    except ValueError:
        pdf = b''
    if pdf[:4] != b'%PDF':
        raise ErrorSIRE(f'SUNAT no entregó la constancia en PDF: {texto[:200] or "respuesta vacía"}')
    return pdf


def descargar_constancia(libro, nombre):
    """Bytes del PDF de la constancia de recepción del registro generado."""
    if not nombre:
        raise ErrorSIRE('Falta el nombre de la constancia (LE….pdf).')
    return _pdf(_api('GET', CONSTANCIA[libro] + urllib.parse.quote(nombre), binario=True))
