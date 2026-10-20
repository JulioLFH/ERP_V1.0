"""Grupo de empresas: usuario corporativo (un solo ingreso para varias empresas) y creación de empresas desde el
sistema.

- Usuario corporativo: un usuario de la empresa principal marcado como tal (o cualquier administrador de la
  principal). Al ingresar en la principal queda reconocido en la sesión y puede pasar a las empresas que se le
  asignaron sin volver a iniciar sesión. En cada empresa actúa con su mismo nombre de usuario: si allí no existe, se
  crea como administrador de esa empresa (sin contraseña propia: solo se entra por el grupo); si ya existe, usa sus
  permisos de esa empresa.
- Crear empresa: crea la base de datos en el mismo servidor, sus tablas y el plan contable, la ficha con RUC y razón
  social, y deja al usuario corporativo como administrador. Corre en segundo plano (tarda un par de minutos)."""
import re
import threading

from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.db import connections

from erp import empresas as reg

SESION = 'corporativo'
BACKEND = 'django.contrib.auth.backends.ModelBackend'


class ErrorGrupo(Exception):
    pass


# ---------------------------------------------------------------- usuario corporativo
def acceso_de(usuario_principal):
    """(es corporativo, todas, [alias], puede crear) de un usuario de la base principal."""
    from .models import UsuarioCorporativo
    if usuario_principal is None or not usuario_principal.is_active:
        return False, False, [], False
    if usuario_principal.is_superuser:
        return True, True, [], True
    c = UsuarioCorporativo.objects.filter(usuario_id=usuario_principal.pk).first()
    if c is None:
        return False, False, [], False
    return True, c.todas, list(c.empresas or []), c.puede_crear


def marcar(request, usuario, alias):
    """Al ingresar en la empresa principal, deja al usuario reconocido como corporativo en la sesión."""
    request.session.pop(SESION, None)
    if alias != 'default':
        return
    es, *_ = acceso_de(usuario)
    if es:
        request.session[SESION] = {'id': usuario.pk, 'username': usuario.username}


def corporativo(request):
    """El usuario de la principal detrás de la sesión (o None), siempre verificado contra la base principal."""
    datos = request.session.get(SESION)
    if not datos:
        return None
    if not hasattr(request, '_corporativo'):
        u = get_user_model().objects.using('default').filter(pk=datos.get('id'), is_active=True).first()
        request._corporativo = u if u and acceso_de(u)[0] else None
    return request._corporativo


def empresas_de(request):
    """[(alias, razón social)] a las que el corporativo de la sesión puede pasar."""
    u = corporativo(request)
    if u is None:
        return []
    _, todas, permitidas, _ = acceso_de(u)
    return [(a, reg.razon_social(a)) for a in reg.empresas() if todas or a == 'default' or a in permitidas]


def puede_crear(request):
    u = corporativo(request)
    return bool(u and acceso_de(u)[3])


def _usuario_en(alias, principal):
    """El usuario con que el corporativo actúa en la empresa: el mismo nombre; si no existe, se crea como
    administrador sin contraseña propia."""
    from .auditoria import _local
    User = get_user_model()
    local = User.objects.using(alias).filter(username=principal.username).first()
    if local is None:
        local = User(username=principal.username, email=principal.email, first_name=principal.first_name,
                     last_name=principal.last_name, is_superuser=True, is_staff=True, is_active=True)
        local.set_unusable_password()
        # en la bitácora de esa empresa el alta la hace el sistema (el usuario de la principal no existe allí)
        autor = getattr(_local, 'usuario', None)
        _local.usuario = None
        try:
            local.save(using=alias)
        finally:
            _local.usuario = autor
    if not local.is_active:
        raise ErrorGrupo(f'El usuario {local.username} está desactivado en esa empresa.')
    return local


def entrar(request, alias):
    """Pasa la sesión a otra empresa del grupo sin volver a ingresar."""
    principal = corporativo(request)
    if principal is None:
        raise ErrorGrupo('Su sesión no es corporativa: ingrese a la otra empresa con su usuario.')
    if alias not in dict(empresas_de(request)):
        raise ErrorGrupo('No tiene acceso a esa empresa.')
    datos = request.session[SESION]
    antes = request.user
    token = reg.activar(alias)
    try:
        local = principal if alias == 'default' else _usuario_en(alias, principal)
        auth_login(request, local, backend=BACKEND)  # cambia de usuario: la sesión se renueva
    finally:
        reg.restaurar(token)
    request.session['empresa'] = alias
    request.session[SESION] = datos  # se conserva el vínculo con el usuario corporativo
    # el ingreso queda en la bitácora de esa empresa (señal de inicio de sesión); en lo que resta de esta petición,
    # que empezó en la empresa anterior, la auditoría sigue con el usuario de esa empresa
    request.user = antes
    return local


# ---------------------------------------------------------------- crear empresa
def siguiente_alias():
    from .models import EmpresaGrupo
    usados = set(reg.empresas()) | set(EmpresaGrupo.objects.using('default').values_list('alias', flat=True))
    n = 2
    while f'empresa{n}' in usados:
        n += 1
    return f'empresa{n}'


def crear_empresa(razon_social, ruc, principal, sincrono=False):
    """Registra la empresa y la crea (base, tablas, ficha y administrador). Devuelve el registro EmpresaGrupo."""
    from .forms import error_ruc
    from .models import EmpresaGrupo
    razon_social, ruc = (razon_social or '').strip().upper(), (ruc or '').strip()
    if not razon_social:
        raise ErrorGrupo('Indique la razón social.')
    if error_ruc(ruc):
        raise ErrorGrupo(error_ruc(ruc))
    for alias in reg.empresas():
        try:
            from .models import Empresa
            e = Empresa.objects.using(alias).first()
        except Exception:  # base aún en creación
            continue
        if e and e.ruc == ruc:
            raise ErrorGrupo(f'El RUC {ruc} ya es de {e.razon_social} en el grupo.')
    if EmpresaGrupo.objects.using('default').filter(ruc=ruc).exclude(estado='ERROR').exists():
        raise ErrorGrupo(f'El RUC {ruc} ya se está creando o ya existe en el grupo.')
    alias = siguiente_alias()
    base = reg.nombre_base(alias)
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', base):
        raise ErrorGrupo(f'Nombre de base no válido: {base}')
    registro = EmpresaGrupo.objects.using('default').create(alias=alias, base=base, razon_social=razon_social,
                                                            ruc=ruc, creado_por=principal.username,
                                                            mensaje='Creando la base de datos…')
    if sincrono:
        _crear(registro.pk, principal.pk)
    else:
        threading.Thread(target=_crear, args=(registro.pk, principal.pk), daemon=True,
                         name=f'crear-{alias}').start()
    return registro


def _crear(registro_id, principal_id):
    from django.core.management import call_command

    from .models import Empresa, EmpresaGrupo
    registro = EmpresaGrupo.objects.using('default').get(pk=registro_id)
    alias = registro.alias
    try:
        reg.registrar_conexion(alias, registro.base, registro.razon_social)
        reg.crear_base_si_falta(alias)
        EmpresaGrupo.objects.using('default').filter(pk=registro_id).update(
            mensaje='Creando tablas y plan contable (un par de minutos)…')
        call_command('migrate', database=alias, interactive=False, verbosity=0)
        token = reg.activar(alias)
        try:
            e = Empresa.objects.using(alias).first() or Empresa(ruc=registro.ruc)
            e.ruc, e.razon_social = registro.ruc, registro.razon_social
            e.save(using=alias)
            principal = get_user_model().objects.using('default').get(pk=principal_id)
            _usuario_en(alias, principal)
        finally:
            reg.restaurar(token)
        from django.core.cache import cache
        cache.delete(f'razon_social_{alias}')
        EmpresaGrupo.objects.using('default').filter(pk=registro_id).update(
            estado='LISTA', mensaje='Empresa lista: ya puede entrar desde el menú de usuario.')
    except Exception as exc:  # cualquier falla queda registrada para reintentar
        EmpresaGrupo.objects.using('default').filter(pk=registro_id).update(
            estado='ERROR', mensaje=f'{type(exc).__name__}: {exc}'[:2000])
        settings.EMPRESAS.pop(alias, None)
    finally:
        if threading.current_thread() is not threading.main_thread():
            connections.close_all()


def reintentar(registro, principal, sincrono=False):
    """Vuelve a crear una empresa que quedó con error (la base y las tablas que ya existían se conservan)."""
    from .models import EmpresaGrupo
    EmpresaGrupo.objects.using('default').filter(pk=registro.pk).update(estado='CREANDO', mensaje='Reintentando…')
    if sincrono:
        _crear(registro.pk, principal.pk)
    else:
        threading.Thread(target=_crear, args=(registro.pk, principal.pk), daemon=True).start()
