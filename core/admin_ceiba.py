"""Usuario «administrador» de Ceiba en cada implementación.

Al terminar la primera instalación (el asistente lo completa el cliente con su propio administrador), el sistema crea
además el usuario «administrador» de Ceiba —superusuario de la empresa principal, y por eso corporativo de todo el
grupo— con una contraseña segura que se envía SOLO por correo a CORREO_INSTALACION (el dueño de las implementaciones).
La contraseña no se guarda en ningún archivo: si el correo no sale, el usuario no se crea y se puede reintentar con
«python manage.py admin_ceiba» (que también repone la contraseña con --reponer).
"""
import logging
import os
import secrets
import string

from django.contrib.auth import get_user_model

log = logging.getLogger(__name__)
USUARIO = 'administrador'
SIMBOLOS = '#$%*-_'


def correo_ceiba():
    return os.environ.get('CORREO_INSTALACION', '').strip()


def nombre_usuario():
    return os.environ.get('ADMIN_CEIBA_USUARIO', '').strip() or USUARIO


def clave_segura(largo=16):
    while True:
        c = ''.join(secrets.choice(string.ascii_letters + string.digits + SIMBOLOS) for _ in range(largo))
        if (any(x.islower() for x in c) and any(x.isupper() for x in c) and any(x.isdigit() for x in c)
                and any(x in SIMBOLOS for x in c)):
            return c


def crear(url_base, reponer=False):
    """Crea (o con reponer=True, le pone una contraseña nueva a) el administrador de Ceiba y envía su acceso por
    correo. Devuelve (ok, mensaje). Siempre en la base principal."""
    from erp.empresas import activar, restaurar

    from . import correo
    from .models import Empresa
    destino = correo_ceiba()
    if not destino:
        return False, 'No hay CORREO_INSTALACION: no se sabe a quién enviar el acceso.'
    User = get_user_model()
    usuario = nombre_usuario()
    token = activar('default')
    try:
        u = User.objects.filter(username=usuario).first()
        if u and not reponer:
            return False, f'El usuario «{usuario}» ya existe (use --reponer para enviarle una contraseña nueva).'
        clave = clave_segura()
        creado = u is None
        if creado:
            u = User.objects.create_superuser(usuario, destino, clave, first_name='Administrador',
                                              last_name='Ceiba')
        else:
            u.set_password(clave)
            u.is_active = u.is_superuser = u.is_staff = True
            u.save()
        empresa = Empresa.actual()
        url = url_base.rstrip('/')
        try:
            correo.enviar([destino], f'Ceiba ERP · {empresa.razon_social} · usuario administrador',
                          'registration/correo_admin_ceiba.html',
                          {'usuario': usuario, 'clave': clave, 'enlace': f'{url}/login/',
                           'dominio': url.split('//', 1)[-1], 'nuevo': creado}, solo_ceiba=True)
        except Exception as exc:  # sin correo no queda una contraseña que nadie conoce
            log.error('No se pudo enviar el acceso del administrador de Ceiba: %s', exc)
            if creado:
                u.delete()
            return False, f'No se pudo enviar el correo ({exc}); el usuario no se creó.'
        return True, (f'Usuario «{usuario}» {"creado" if creado else "con contraseña nueva"}: '
                      f'acceso enviado a {destino}.')
    finally:
        restaurar(token)
