"""Celulares con la app Ceiba ERP (Ajustes › IP permitidas e intentos de ingreso › Celulares).

Cada app crea al instalarse una llave EC P-256 en el Android Keystore (no se puede copiar del equipo). Al abrirse
pide autorización: envía su llave pública, la versión, el modelo y la hora, firmados con la llave. El ERP verifica la
firma, registra el celular (pendiente hasta que el administrador lo aprueba) y, si está aprobado y su versión no es
menor a la mínima, le entrega un pase firmado por el servidor que dura PASE_HORAS. La app lo guarda como cookie y la
vista web lo envía en cada pedido: con el control por IP activo, el pase vale como una IP permitida.

La llave nunca viaja; la firma vence en VENTANA_MS y no se acepta dos veces. Mientras el ERP se publique por http,
el pase podría copiarse en la misma red mientras dure: por eso es corto y se revoca al instante con el celular."""
import base64
import hashlib
import json
import logging
import re
import time

from django.core import signing
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)

COOKIE = 'ceiba_app'
SAL = 'ceiba.app.pase'
PASE_HORAS = 8
VENTANA_MS = 10 * 60 * 1000      # diferencia máxima entre la hora del celular y la del servidor
INTENTOS_POR_IP = 30             # autorizaciones por IP cada 10 minutos (el resto se rechaza)
MAXIMO_PENDIENTES = 500          # no se registran más celulares pendientes (evita llenar la lista)
RUTA = '/app/autorizar/'
USO_CADA = 60                    # segundos entre escrituras del último uso de un celular


def version_tupla(texto):
    numeros = [int(n) for n in re.findall(r'\d+', texto or '')[:3]]
    return tuple(numeros + [0] * (3 - len(numeros)))


def version_vigente(version, minima):
    return version_tupla(version) >= version_tupla(minima)


def codigo_de(der):
    return hashlib.sha256(der).hexdigest()


def firma_valida(der, mensaje, firma):
    """Firma ECDSA P-256 con SHA-256 (la que produce el Android Keystore: «SHA256withECDSA»)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    try:
        llave = load_der_public_key(der)
        if not isinstance(llave, ec.EllipticCurvePublicKey) or llave.curve.name != 'secp256r1':
            return False
        llave.verify(firma, mensaje, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def mensaje_firmado(hora, version, clave_b64):
    return f'{hora}|{version}|{clave_b64}'.encode()


def emitir_pase(dispositivo):
    return signing.dumps({'c': dispositivo.codigo, 'v': dispositivo.version_app}, salt=SAL, compress=True)


def leer_pase(valor):
    """{'c': código, 'v': versión} del pase vigente o None."""
    if not valor:
        return None
    try:
        datos = signing.loads(valor, salt=SAL, max_age=PASE_HORAS * 3600)
    except signing.BadSignature:
        return None
    return datos if isinstance(datos, dict) and datos.get('c') else None


def _respuesta(estado, dispositivo=None, **extra):
    datos = {'estado': estado, **extra}
    if dispositivo is not None:
        datos['codigo'] = dispositivo.codigo_corto
    return datos


def autorizar(cuerpo, ip):
    """(datos para la app, código HTTP). Estados: OK (puede abrir el ERP), PENDIENTE, REVOCADO, ACTUALIZAR, HORA
    (la hora del celular está mal), INVALIDO u OCUPADO."""
    from . import acceso_ip
    from .models import ControlIp, DispositivoApp
    clave_intentos = f'app-autorizar:{ip}'
    intentos = cache.get(clave_intentos, 0)
    if intentos >= INTENTOS_POR_IP:
        return _respuesta('OCUPADO', mensaje='Demasiados intentos: espere unos minutos.'), 429
    cache.set(clave_intentos, intentos + 1, 600)
    try:
        datos = json.loads(cuerpo or b'{}')
        clave_b64 = str(datos['clave'])[:400]
        version = str(datos.get('version', ''))[:20]
        modelo = str(datos.get('modelo', ''))[:120]
        hora = int(datos['hora'])
        der = base64.b64decode(clave_b64, validate=True)
        firma = base64.b64decode(str(datos['firma'])[:200], validate=True)
    except (ValueError, KeyError, TypeError):
        return _respuesta('INVALIDO', mensaje='Pedido incompleto.'), 400
    ahora = int(time.time() * 1000)
    if abs(ahora - hora) > VENTANA_MS:
        return _respuesta('HORA', hora_servidor=ahora,
                          mensaje='La fecha y hora del celular no coinciden con las del servidor.'), 400
    if not firma_valida(der, mensaje_firmado(hora, version, clave_b64), firma):
        return _respuesta('INVALIDO', mensaje='La firma del celular no es válida.'), 400
    codigo = codigo_de(der)
    dispositivo = DispositivoApp.objects.using('default').filter(codigo=codigo).first()
    if dispositivo is None:
        if DispositivoApp.objects.using('default').filter(estado='PENDIENTE').count() >= MAXIMO_PENDIENTES:
            return _respuesta('OCUPADO', mensaje='Hay demasiados celulares pendientes de aprobación.'), 429
        dispositivo = DispositivoApp(codigo=codigo, clave_publica=clave_b64)
    elif hora <= dispositivo.ultima_firma:
        return _respuesta('INVALIDO', mensaje='Firma repetida.'), 400
    dispositivo.ultima_firma, dispositivo.version_app, dispositivo.modelo = hora, version, modelo
    dispositivo.ultima_ip, dispositivo.ultima_vez = ip, timezone.now()
    dispositivo.save(using='default')
    minima = ControlIp.actual().version_minima_app
    vigente = version_vigente(version, minima)
    extra = {'minima': minima, 'horas': PASE_HORAS}
    if dispositivo.estado == 'APROBADO' and vigente:
        extra['pase'] = emitir_pase(dispositivo)
    if acceso_ip.permitida(ip):  # control apagado o IP de la lista: entra como cualquier navegador
        return _respuesta('OK', dispositivo, **extra), 200
    if dispositivo.estado == 'REVOCADO':
        return _respuesta('REVOCADO', dispositivo, **extra), 403
    if dispositivo.estado == 'PENDIENTE':
        return _respuesta('PENDIENTE', dispositivo, **extra), 403
    if not vigente:
        return _respuesta('ACTUALIZAR', dispositivo, **extra), 403
    return _respuesta('OK', dispositivo, **extra), 200


def codigo_con_pase(request, estado):
    """Código del celular si el pedido trae un pase vigente de un celular aprobado con versión vigente."""
    datos = leer_pase(request.COOKIES.get(COOKIE))
    if datos is None or datos['c'] not in estado['aprobados']:
        return None
    if not version_vigente(datos.get('v', ''), estado['version_minima']):
        return None
    return datos['c']


_usos = {}


def marcar_uso(codigo, usuario, ip):
    """Último usuario e IP del celular (una escritura por minuto como máximo)."""
    ahora = time.monotonic()
    if ahora - _usos.get(codigo, 0) < USO_CADA:
        return
    _usos[codigo] = ahora
    try:
        from .models import DispositivoApp
        cambios = {'ultima_vez': timezone.now(), 'ultima_ip': ip}
        if usuario:
            cambios['usuario'] = usuario
        DispositivoApp.objects.using('default').filter(codigo=codigo).update(**cambios)
    except Exception:  # el registro nunca impide atender la página
        log.exception('No se pudo registrar el uso del celular %s', codigo[:8])
