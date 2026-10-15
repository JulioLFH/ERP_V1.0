"""Almacenamiento de objetos (S3 compatible: AWS S3, Cloudflare R2, DigitalOcean Spaces, Backblaze B2, MinIO) para
fotos y PDF de sustento, en vez de la base de datos.

Se activa con variables de entorno del servidor:
  OBJETOS_BUCKET, OBJETOS_ACCESS_KEY, OBJETOS_SECRET_KEY y, según el proveedor, OBJETOS_ENDPOINT y OBJETOS_REGION.
Sin ellas todo sigue en la base de datos. La conservación (al menos el plazo de prescripción tributaria, 5 años
más el año en curso) y el respaldo se configuran en el bucket: versionado y bloqueo de objetos (Object Lock) o una
regla de ciclo de vida que no borre antes de ese plazo. `manage.py mover_sustentos` traslada los archivos que hoy
están en la base de datos."""
import os
import re
import uuid

from django.utils import timezone

_cliente = None


class ErrorAlmacen(Exception):
    pass


def configurado():
    return bool(os.environ.get('OBJETOS_BUCKET') and os.environ.get('OBJETOS_ACCESS_KEY')
                and os.environ.get('OBJETOS_SECRET_KEY'))


def cliente():
    global _cliente
    if _cliente is None:
        import boto3
        _cliente = boto3.client(
            's3', endpoint_url=os.environ.get('OBJETOS_ENDPOINT') or None,
            region_name=os.environ.get('OBJETOS_REGION') or None,
            aws_access_key_id=os.environ['OBJETOS_ACCESS_KEY'],
            aws_secret_access_key=os.environ['OBJETOS_SECRET_KEY'])
    return _cliente


def _bucket():
    return os.environ['OBJETOS_BUCKET']


def clave_para(nombre):
    """empresa/AAAA/MM/uuid-nombre (cada empresa en su carpeta)."""
    from erp.empresas import actual
    limpio = re.sub(r'[^A-Za-z0-9._-]+', '_', nombre or 'archivo')[-80:]
    return f'{actual() or "default"}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}-{limpio}'


def guardar(datos, nombre, tipo=''):
    clave = clave_para(nombre)
    try:
        cliente().put_object(Bucket=_bucket(), Key=clave, Body=datos,
                             ContentType=tipo or 'application/octet-stream')
    except Exception as exc:
        raise ErrorAlmacen(f'No se pudo guardar el archivo en el almacenamiento de objetos: {exc}') from exc
    return clave


def leer(clave):
    try:
        return cliente().get_object(Bucket=_bucket(), Key=clave)['Body'].read()
    except Exception as exc:
        raise ErrorAlmacen(f'No se pudo leer el archivo {clave}: {exc}') from exc
