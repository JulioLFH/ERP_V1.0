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
        # sesión de la empresa principal abierta antes de marcarla (p. ej. iniciada antes de la v1.49): si el usuario
        # es corporativo se reconoce ahora, sin pedirle que vuelva a ingresar
        user = getattr(request, 'user', None)
        if (user is not None and user.is_authenticated and request.session.get('empresa', 'default') == 'default'
                and reg.actual() == 'default' and acceso_de(user)[0]):
            marcar(request, user, 'default')
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
    if not hasattr(request, '_empresas_de'):  # se calcula una vez por petición (el menú la usa en cada página)
        _, todas, permitidas, _ = acceso_de(u)
        request._empresas_de = [(a, reg.razon_social(a)) for a in reg.empresas()
                                if (todas or a == 'default' or a in permitidas) and tiene_modulos_en(a, u)]
    return request._empresas_de


def modulos_iniciales(principal):
    """Módulos con que se crea el corporativo en una empresa nueva para él (None = administrador). Los
    administradores de la principal y los corporativos creados antes de elegir módulos entran como administrador."""
    from .models import UsuarioCorporativo
    if principal.is_superuser:
        return None
    c = UsuarioCorporativo.objects.using('default').filter(usuario_id=principal.pk).first()
    return None if c is None or c.modulos is None else list(c.modulos)


def tiene_modulos_en(alias, principal):
    """Si el corporativo tiene algún módulo en esa empresa: con su usuario de allí o, si aún no existe, como el
    administrador que se le crearía. Sin módulos la empresa no se le muestra ni se puede entrar."""
    from .modulos import modulos_del_usuario
    User = get_user_model()
    token = reg.activar(alias)
    try:
        local = principal if alias == 'default' else User.objects.using(alias).filter(
            username=principal.username).first()
        if local is not None:
            return bool(modulos_del_usuario(local))
        modulos = modulos_iniciales(principal)  # aún no entró: los módulos con que se le crearía allí
        if modulos is None:
            return bool(modulos_del_usuario(User(username=principal.username, is_superuser=True, is_active=True)))
        from .modulos import _ocultos_empresa
        from .plataforma import modulos_permitidos
        return bool(set(modulos) & modulos_permitidos() - _ocultos_empresa())
    except Exception:  # base no disponible o en creación: se deja como antes (la entrada avisa si falla)
        return True
    finally:
        reg.restaurar(token)


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
        modulos = modulos_iniciales(principal)
        admin = modulos is None
        local = User(username=principal.username, email=principal.email, first_name=principal.first_name,
                     last_name=principal.last_name, is_superuser=admin, is_staff=admin, is_active=True)
        local.set_unusable_password()
        # en la bitácora de esa empresa el alta la hace el sistema (el usuario de la principal no existe allí)
        autor = getattr(_local, 'usuario', None)
        _local.usuario = None
        try:
            local.save(using=alias)
            if not admin:  # los módulos elegidos al crear el corporativo; luego se ajustan en esa empresa
                from django.contrib.auth.models import Group

                from .modulos import GRUPOS
                local.groups.set([Group.objects.using(alias).get_or_create(name=GRUPOS[c])[0]
                                  for c in modulos if c in GRUPOS])
        finally:
            _local.usuario = autor
    if not local.is_active:
        raise ErrorGrupo(f'El usuario {local.username} está desactivado en esa empresa.')
    return local


def autenticar_en(request, alias, username, password):
    """Ingreso directo a otra empresa con el usuario y la contraseña de la principal: solo para un usuario corporativo
    con acceso a esa empresa. Devuelve (usuario de esa empresa, usuario de la principal) o None. Corre con la empresa
    elegida activa; la clave se verifica en la base principal."""
    from django.contrib.auth.backends import ModelBackend

    from .doble_factor import tiene_doble_factor
    if alias == 'default' or alias not in reg.empresas() or not username or not password:
        return None
    token = reg.activar('default')  # la verificación (y una posible mejora del hash) va a la base principal
    try:
        principal = ModelBackend().authenticate(request, username=username, password=password)
        if principal is None:
            return None
        es, todas, permitidas, _ = acceso_de(principal)
        if not es or not (todas or alias in permitidas):
            return None
        if not tiene_modulos_en(alias, principal):
            raise ErrorGrupo('Su usuario no tiene módulos asignados en esta empresa.')
        if tiene_doble_factor(principal):
            raise ErrorGrupo('Su usuario corporativo tiene doble factor: ingrese por la empresa principal y pase a '
                             'esta empresa desde el Panel del grupo.')
    finally:
        reg.restaurar(token)
    local = _usuario_en(alias, principal)  # misma regla que el Panel del grupo (crea el espejo si falta)
    local.backend = BACKEND
    return local, principal


def marcar_corporativo(request, principal):
    """Deja la sesión vinculada al usuario corporativo de la principal (para pasar entre empresas)."""
    request.session[SESION] = {'id': principal.pk, 'username': principal.username}


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


def crear_empresa(razon_social, ruc, principal, sincrono=False, direccion='', ubigeo='', modelo='', copiar=None):
    """Registra la empresa y la crea (base, tablas, ficha y administrador). Con «modelo» (alias de otra empresa del
    grupo) copia de ella lo marcado en «copiar» (claves de COPIABLES; sin indicar: series y parámetros). Devuelve el
    registro EmpresaGrupo."""
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
    from . import ubigeo as ubigeos
    modelo = modelo if modelo in reg.empresas() else ''
    copiar = [c for c in (COPIA_POR_DEFECTO if copiar is None else copiar) if c in COPIABLES]
    if modelo and not copiar:
        raise ErrorGrupo('Marque qué copiar de la empresa modelo, o elija «Empezar sin copiar».')
    datos = {'direccion': (direccion or '').strip()[:250],
             'ubigeo': ubigeo if ubigeos.existe(ubigeo or '') else '',
             'modelo': modelo, 'copiar': copiar if modelo else []}
    registro = EmpresaGrupo.objects.using('default').create(alias=alias, base=base, razon_social=razon_social,
                                                            ruc=ruc, creado_por=principal.username, datos=datos,
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
            datos = registro.datos or {}
            e.direccion = datos.get('direccion') or e.direccion
            e.ubigeo = datos.get('ubigeo') or e.ubigeo
            e.save(using=alias)
            if datos.get('modelo'):
                EmpresaGrupo.objects.using('default').filter(pk=registro_id).update(
                    mensaje='Copiando la configuración de la empresa modelo…')
                copiar_configuracion(datos['modelo'], alias, datos.get('copiar', COPIA_POR_DEFECTO))
            from .perfiles_predefinidos import crear as crear_perfiles  # REQ-04: perfiles por jefatura
            crear_perfiles(using=alias)
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


# parámetros de Ajustes › Empresa que se copian de la empresa modelo (no los datos propios: RUC, nombre, credenciales)
PARAMETROS_MODELO = (
    'igv_tasa', 'permitir_stock_negativo', 'fuente_tipo_cambio', 'bloquear_deuda_vencida', 'dias_gracia',
    'exigir_orden_compra', 'oc_directa_con_contrato', 'presupuesto_solped', 'tope_diferencias_inventario',
    'tolerancia_cantidad', 'tolerancia_precio', 'tolerancia_total', 'tolerancia_cantidad_pct', 'tolerancia_precio_pct',
    'tolerancia_total_pct', 'alerta_precio_pct', 'exigir_doble_factor', 'clave_largo_minimo', 'clave_mayuscula',
    'clave_numero', 'clave_simbolo', 'clave_vence_dias', 'clave_historial', 'intentos_bloqueo', 'minutos_inactividad',
    'aviso_equipo_nuevo', 'dias_escalamiento', 'segregacion_estricta')


def copiar_configuracion(origen, destino, que=None):
    """Copia de la empresa modelo lo indicado en «que» (claves de COPIABLES, en su orden; sin indicar: series y
    parámetros). Nunca copia movimientos, saldos, correlativos, números de cuenta ni credenciales. Devuelve las
    etiquetas de lo copiado."""
    from .models import Empresa
    if Empresa.objects.using(origen).first() is None or Empresa.objects.using(destino).first() is None:
        return []
    que = set(COPIA_POR_DEFECTO if que is None else que)
    hechas = []
    for clave, (etiqueta, _, funcion) in COPIABLES.items():  # en orden: el plan de cuentas antes que lo que lo usa
        if clave in que:
            funcion(origen, destino)
            hechas.append(etiqueta)
    return hechas


def _copiar_parametros(origen, destino):
    from .models import Empresa
    modelo, nueva = Empresa.objects.using(origen).first(), Empresa.objects.using(destino).first()
    for campo in PARAMETROS_MODELO:
        if hasattr(modelo, campo):
            setattr(nueva, campo, getattr(modelo, campo))
    nueva.save(using=destino)


def _copiar_series(origen, destino):
    from .models import Serie
    existentes = set(Serie.objects.using(destino).values_list('tipo', 'serie'))
    Serie.objects.using(destino).bulk_create([
        Serie(tipo=s.tipo, serie=s.serie, correlativo=0, activo=s.activo)
        for s in Serie.objects.using(origen).all() if (s.tipo, s.serie) not in existentes])


def _por_codigo(modelo, base):
    return {o.codigo: o for o in modelo.objects.using(base).all()}


def _copiar_plan_contable(origen, destino):
    """Cuentas del modelo (las nuevas se crean y las existentes toman sus datos), con sus cuentas de destino."""
    from contabilidad.models import CuentaContable
    campos = ('nombre', 'naturaleza', 'imputable', 'activo', 'columna_balance', 'moneda', 'tipo_cambio', 'analisis',
              'centro_costo_regla')
    # select_related: el enrutador lee las relaciones de la empresa activa, no de la base del objeto
    origenes = list(CuentaContable.objects.using(origen).select_related('destino_debe', 'destino_haber')
                    .order_by('codigo'))
    actuales = _por_codigo(CuentaContable, destino)
    for c in origenes:
        nueva = actuales.get(c.codigo) or CuentaContable(codigo=c.codigo)
        for campo in campos:
            if hasattr(c, campo):
                setattr(nueva, campo, getattr(c, campo))
        nueva.save(using=destino)
        actuales[c.codigo] = nueva
    for c in origenes:  # segunda vuelta: las cuentas de destino (debe/haber) apuntan a cuentas ya creadas
        nueva = actuales[c.codigo]
        debe = actuales.get(c.destino_debe.codigo) if c.destino_debe_id else None
        haber = actuales.get(c.destino_haber.codigo) if c.destino_haber_id else None
        if (nueva.destino_debe_id, nueva.destino_haber_id) != (getattr(debe, 'pk', None), getattr(haber, 'pk', None)):
            nueva.destino_debe, nueva.destino_haber = debe, haber
            nueva.save(using=destino)


def _copiar_cuentas_operacion(origen, destino):
    """Cuentas por operación y por tipo de producto: la misma cuenta (por código) si existe en la empresa nueva."""
    from contabilidad.models import CuentaContable, CuentaDefecto
    cuentas = _por_codigo(CuentaContable, destino)
    for d in CuentaDefecto.objects.using(origen).select_related('cuenta'):
        cuenta = cuentas.get(d.cuenta.codigo)
        if cuenta is None:
            continue
        CuentaDefecto.objects.using(destino).update_or_create(
            clave=d.clave, defaults={'descripcion': d.descripcion, 'cuenta': cuenta})
    from .models import CuentasTipoProducto
    campos = [f.name for f in CuentasTipoProducto._meta.fields
              if f.is_relation and f.related_model is CuentaContable]
    for t in CuentasTipoProducto.objects.using(origen).select_related(*campos):
        valores = {}
        for campo in campos:
            c = getattr(t, campo)
            valores[campo] = cuentas.get(c.codigo) if c is not None else None
        CuentasTipoProducto.objects.using(destino).update_or_create(clase=t.clase, defaults=valores)


def _copiar_centros(origen, destino):
    """Centros de beneficio y de costo (con su jerarquía); sin responsables, que son usuarios de cada empresa."""
    from contabilidad.models import CentroBeneficio, CentroCosto
    beneficios = _por_codigo(CentroBeneficio, destino)
    for b in CentroBeneficio.objects.using(origen).all():
        nuevo = beneficios.get(b.codigo) or CentroBeneficio(codigo=b.codigo)
        nuevo.nombre, nuevo.responsable, nuevo.activo = b.nombre, b.responsable, b.activo
        nuevo.save(using=destino)
        beneficios[b.codigo] = nuevo
    centros, origenes = _por_codigo(CentroCosto, destino), list(CentroCosto.objects.using(origen).select_related(
        'centro_beneficio', 'padre'))
    for c in origenes:
        nuevo = centros.get(c.codigo) or CentroCosto(codigo=c.codigo)
        for campo in ('nombre', 'tipo', 'responsable', 'activo', 'valido_desde', 'valido_hasta'):
            setattr(nuevo, campo, getattr(c, campo))
        nuevo.centro_beneficio = beneficios.get(c.centro_beneficio.codigo) if c.centro_beneficio_id else None
        nuevo.padre = None
        nuevo.save(using=destino)
        centros[c.codigo] = nuevo
    for c in origenes:
        if c.padre_id:
            CentroCosto.objects.using(destino).filter(pk=centros[c.codigo].pk).update(
                padre=centros.get(c.padre.codigo))


def _copiar_almacenes(origen, destino):
    """Almacenes (sin stock, responsables ni tercero). Solo uno queda como principal."""
    from .models import Almacen
    actuales = _por_codigo(Almacen, destino)
    hay_principal = Almacen.objects.using(destino).filter(es_principal=True).exists()
    for a in Almacen.objects.using(origen).all():
        nuevo = actuales.get(a.codigo)
        if nuevo is None:
            nuevo = Almacen(codigo=a.codigo, es_principal=a.es_principal and not hay_principal)
            hay_principal = hay_principal or nuevo.es_principal
        for campo in ('nombre', 'direccion', 'ubigeo', 'codigo_sunat', 'uso', 'modelo_control', 'operaciones',
                      'activo'):
            if hasattr(a, campo):
                setattr(nuevo, campo, getattr(a, campo))
        nuevo.save(using=destino)


def _copiar_cajas_bancos(origen, destino):
    """Cajas y cuentas bancarias: nombre, banco, moneda y cuenta contable; sin número de cuenta, CCI ni saldo
    inicial (son de cada empresa)."""
    from contabilidad.models import CuentaContable
    from finanzas.models import Cuenta
    cuentas = _por_codigo(CuentaContable, destino)
    existentes = set(Cuenta.objects.using(destino).values_list('tipo', 'nombre', 'moneda'))
    for c in Cuenta.objects.using(origen).select_related('cuenta_contable'):
        if (c.tipo, c.nombre, c.moneda) in existentes:
            continue
        nueva = Cuenta(tipo=c.tipo, nombre=c.nombre, banco=c.banco, moneda=c.moneda,
                       es_detracciones=c.es_detracciones, activo=c.activo)
        for campo in ('incluir_en_efe', 'tipo_efectivo', 'permite_sobregiro'):
            if hasattr(c, campo):
                setattr(nueva, campo, getattr(c, campo))
        if c.cuenta_contable_id:
            nueva.cuenta_contable = cuentas.get(c.cuenta_contable.codigo)
        nueva.save(using=destino)


# REQ-01: lo que se puede copiar de la empresa modelo al crear una empresa: clave -> (etiqueta, ayuda, función).
# El orden importa: el plan de cuentas va antes de lo que apunta a sus cuentas.
COPIABLES = {
    'series': ('Series de comprobantes', 'con el correlativo en cero', _copiar_series),
    'parametros': ('Parámetros de la empresa', 'IGV, tolerancias, crédito, aprobaciones y contraseñas',
                   _copiar_parametros),
    'plan_contable': ('Plan contable', 'sus cuentas, incluidas las que agregó o modificó', _copiar_plan_contable),
    'cuentas_operacion': ('Cuentas por operación', 'y cuentas por tipo de producto', _copiar_cuentas_operacion),
    'centros': ('Centros de costo', 'y centros de beneficio', _copiar_centros),
    'almacenes': ('Almacenes', 'sin stock', _copiar_almacenes),
    'cajas_bancos': ('Cajas y bancos', 'sin número de cuenta ni saldos', _copiar_cajas_bancos),
}
COPIA_POR_DEFECTO = ('series', 'parametros')


def reintentar(registro, principal, sincrono=False):
    """Vuelve a crear una empresa que quedó con error (la base y las tablas que ya existían se conservan)."""
    from .models import EmpresaGrupo
    EmpresaGrupo.objects.using('default').filter(pk=registro.pk).update(estado='CREANDO', mensaje='Reintentando…')
    if sincrono:
        _crear(registro.pk, principal.pk)
    else:
        threading.Thread(target=_crear, args=(registro.pk, principal.pk), daemon=True,
                         name=f'crear-{registro.alias}').start()


def colgada(registro, minutos=2):
    """«Creando» sin la tarea que la crea (el servidor se reinició a mitad de camino): se puede reintentar. Se espera
    unos minutos desde el alta por si la crea otro proceso del servidor."""
    from datetime import timedelta

    from django.utils import timezone
    if registro is None or registro.estado != 'CREANDO':
        return False
    if any(t.name == f'crear-{registro.alias}' and t.is_alive() for t in threading.enumerate()):
        return False
    return registro.creado < timezone.now() - timedelta(minutes=minutos)
