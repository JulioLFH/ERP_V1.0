"""Políticas de seguridad de acceso: permisos por pantalla (oculta, solo lectura, sin exportar), datos sensibles
(sueldos, saldos de caja y bancos), filtros por dato (centro de costo, cuenta, vendedor), política de contraseñas,
verificación en dos pasos obligatoria, cierre de sesión por inactividad y aviso de ingreso desde un equipo nuevo.

Todo se valida en el servidor (middleware y vistas): ocultar un botón no basta."""
import hashlib
import secrets
import time
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils import timezone

from .permisos import ACCIONES, perfil_de

NIVELES_PANTALLA = [('', 'Completo'), ('SIN_EXPORTAR', 'Sin exportar'), ('LECTURA', 'Solo lectura'),
                    ('OCULTA', 'Oculta')]

# pantallas con sueldos (sin el permiso «ve sueldos» no se abren) y con saldos de caja y bancos
RUTAS_SUELDOS = {'planillas:lista', 'planillas:nueva', 'planillas:detalle', 'planillas:boletas', 'planillas:plame',
                 'planillas:afp', 'planillas:costo_centros', 'planillas:movimientos', 'planillas:movimiento',
                 'planillas:movimiento_nuevo'}
RUTAS_SALDOS = {'finanzas:flujo', 'finanzas:flujo_proyectado', 'finanzas:conciliacion', 'finanzas:saldo_inicial', 'finanzas:extractos',
                'finanzas:extracto'}

# rutas que siempre se pueden abrir aunque falte activar el doble factor o cambiar la contraseña vencida
RUTAS_SEGURIDAD = {'logout', 'login', 'login_2fa', 'seguridad', 'cambiar_clave', 'cambiar_clave_ok', 'salud',
                   'cambiar_empresa', 'alertas_json', 'tipo_cambio_api', 'recuperar', 'recuperar_confirmar',
                   'recuperar_listo'}
# consultas automáticas de la pantalla: no cuentan como actividad del usuario
RUTAS_SIN_ACTIVIDAD = {'alertas_json', 'salud', 'tipo_cambio_api'}

COOKIE_EQUIPO = 'ceiba_equipo'


# ---------------------------------------------------------------- catálogo de pantallas (menú de cada módulo)
_CATALOGO = None


def catalogo():
    """[{clave, etiqueta, modulo, url}] de las opciones del menú: lo que se puede ocultar o limitar."""
    global _CATALOGO
    if _CATALOGO is None:
        from django.urls import NoReverseMatch, reverse

        from .modulos import MODULOS
        vistos, filas = set(), []
        for m in MODULOS:
            for etiqueta, destino in m['menu']:
                hijos = destino if isinstance(destino, list) else [(etiqueta, destino, '')]
                for texto, d, _ in hijos:
                    clave = d.partition('?')[0]
                    if clave in vistos:
                        continue
                    try:
                        url = reverse(clave)
                    except NoReverseMatch:
                        continue
                    if url == '/':
                        continue
                    vistos.add(clave)
                    filas.append({'clave': clave, 'modulo': m['clave'], 'modulo_nombre': m['nombre'],
                                  'etiqueta': texto if texto == etiqueta else f'{etiqueta} › {texto}', 'url': url})
        _CATALOGO = filas
    return _CATALOGO


def pantalla_de_ruta(path):
    """La opción del menú que corresponde a la página: la de URL exacta o la más larga que sea prefijo (el detalle
    /compras/ordenes/5/ es de «Órdenes de compra» /compras/ordenes/)."""
    mejor = None
    for p in catalogo():
        if path == p['url']:
            return p['clave']
        if path.startswith(p['url']) and (mejor is None or len(p['url']) > len(mejor['url'])):
            mejor = p
    return mejor['clave'] if mejor else None


def pantallas_de(user):
    """{clave: nivel} del usuario (vacío = acceso completo a sus pantallas)."""
    perfil = perfil_de(user)
    return dict(perfil.pantallas or {}) if perfil is not None else {}


def ocultas(user):
    return {c for c, n in pantallas_de(user).items() if n == 'OCULTA'}


def nivel_pantalla(user, path):
    restricciones = pantallas_de(user)
    if not restricciones:
        return ''
    clave = pantalla_de_ruta(path)
    return restricciones.get(clave, '') if clave else ''


def es_exportacion(request):
    """Descarga de datos: Excel, CSV, PLE, TXT o PDF de un reporte."""
    formato = (request.GET.get('formato') or '').lower()
    if formato and formato not in ('html', 'pantalla'):
        return True
    if any(request.GET.get(k) for k in ('exportar', 'excel', 'csv')):
        return True
    ruta = request.path.lower()
    return ruta.endswith(('/excel/', '/csv/', '/exportar/', '.csv', '.xlsx', '.txt'))


def restriccion_de_pantalla(request, match):
    """Motivo del bloqueo por el nivel de la pantalla, o None."""
    user = request.user
    if user.is_superuser:
        return None
    vista = match.view_name if match else ''
    if vista in RUTAS_SUELDOS and not ver_sueldos(user):
        return 'Su usuario no ve sueldos ni boletas de pago.'
    if vista in RUTAS_SALDOS and not ver_saldos(user):
        return 'Su usuario no ve saldos de caja y bancos.'
    nivel = nivel_pantalla(user, request.path)
    if nivel == 'OCULTA':
        return 'Esta pantalla no está habilitada para su usuario.'
    if nivel == 'LECTURA' and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return 'Su usuario solo consulta esta pantalla (sin registrar ni modificar).'
    if nivel in ('LECTURA', 'SIN_EXPORTAR') and es_exportacion(request):
        return 'Su usuario no exporta los datos de esta pantalla.'
    return None


# ---------------------------------------------------------------- datos sensibles y filtros por dato
def ver_sueldos(user):
    perfil = perfil_de(user)
    return perfil is None or perfil.ver_sueldos


def ver_saldos(user):
    perfil = perfil_de(user)
    return perfil is None or perfil.ver_saldos


def centros_permitidos(user):
    """Ids de los centros de costo del usuario (con sus subcentros) o None = todos."""
    perfil = perfil_de(user)
    if perfil is None:
        return None
    if not hasattr(perfil, '_centros_ids'):
        ids = set()
        for c in perfil.centros.all():
            ids |= set(c.descendientes_ids())
        perfil._centros_ids = ids or None
    return perfil._centros_ids


def cuentas_permitidas(user):
    perfil = perfil_de(user)
    if perfil is None:
        return None
    if not hasattr(perfil, '_cuentas_ids'):
        perfil._cuentas_ids = set(perfil.cuentas.values_list('pk', flat=True)) or None
    return perfil._cuentas_ids


def vendedor_de(user):
    perfil = perfil_de(user)
    return (perfil.vendedor or '').strip() if perfil is not None else ''


def filtrar_centros(qs, user, campo='centro_costo'):
    ids = centros_permitidos(user)
    return qs if ids is None else qs.filter(**{f'{campo}__in': ids})


def filtrar_cuentas(qs, user, campo='cuenta'):
    ids = cuentas_permitidas(user)
    return qs if ids is None else qs.filter(**{f'{campo}__in': ids})


def filtrar_vendedor(qs, user):
    v = vendedor_de(user)
    if not v or not any(f.name == 'vendedor' for f in qs.model._meta.fields):
        return qs
    return qs.filter(vendedor__iexact=v)


# ---------------------------------------------------------------- perfiles de acceso (roles reutilizables)
def aplicar_perfil(perfil_usuario, perfil_acceso):
    """Copia el perfil de acceso al usuario: módulos, acciones, pantallas, costos, sueldos, saldos y límite."""
    from django.contrib.auth.models import Group

    from .modulos import GRUPO_COSTOS, GRUPOS
    user = perfil_usuario.usuario
    grupos = [Group.objects.get_or_create(name=GRUPOS[c])[0] for c in perfil_acceso.modulos if c in GRUPOS]
    if perfil_acceso.ver_costos:
        grupos.append(Group.objects.get_or_create(name=GRUPO_COSTOS)[0])
    otros = user.groups.exclude(name__in=list(GRUPOS.values()) + [GRUPO_COSTOS])
    user.groups.set(list(otros) + grupos)
    perfil_usuario.perfil_acceso = perfil_acceso
    perfil_usuario.acciones = sorted(perfil_acceso.acciones)
    perfil_usuario.pantallas = dict(perfil_acceso.pantallas)
    perfil_usuario.ver_sueldos, perfil_usuario.ver_saldos = perfil_acceso.ver_sueldos, perfil_acceso.ver_saldos
    perfil_usuario.limite_aprobacion = perfil_acceso.limite_aprobacion
    perfil_usuario.save()
    for attr in ('_modulos_cache', '_perfil_cache', '_ver_costos'):
        if hasattr(user, attr):
            delattr(user, attr)


# ---------------------------------------------------------------- política de contraseñas
def _empresa():
    from .models import Empresa
    return Empresa.actual()


class PoliticaClaveValidator:
    """Largo mínimo, mayúsculas, números y símbolos según Ajustes › Empresa, y sin repetir las últimas N."""

    def validate(self, password, user=None):
        e = _empresa()
        errores = []
        if len(password) < (e.clave_largo_minimo or 8):
            errores.append(f'tener al menos {e.clave_largo_minimo} caracteres')
        if e.clave_mayuscula and not (any(c.isupper() for c in password) and any(c.islower() for c in password)):
            errores.append('combinar mayúsculas y minúsculas')
        if e.clave_numero and not any(c.isdigit() for c in password):
            errores.append('incluir al menos un número')
        if e.clave_simbolo and all(c.isalnum() for c in password):
            errores.append('incluir al menos un símbolo (ej. - . # @)')
        if errores:
            raise ValidationError('La contraseña debe ' + ', '.join(errores) + '.', code='politica_clave')
        if user is not None and user.pk and e.clave_historial:
            from django.contrib.auth.hashers import check_password

            from .models import HistorialClave
            anteriores = HistorialClave.objects.filter(usuario_id=user.pk)[:e.clave_historial]
            if any(check_password(password, h.clave) for h in anteriores):
                raise ValidationError(f'No puede repetir ninguna de sus últimas {e.clave_historial} contraseñas.',
                                      code='clave_repetida')

    def password_changed(self, password, user=None):
        """Guarda el hash en el historial y la fecha del cambio (para el vencimiento)."""
        if user is None or not user.pk or not user.password:
            return
        from .models import HistorialClave, PerfilUsuario
        HistorialClave.objects.create(usuario_id=user.pk, clave=user.password)
        viejos = HistorialClave.objects.filter(usuario_id=user.pk).values_list('pk', flat=True)[12:]
        HistorialClave.objects.filter(pk__in=list(viejos)).delete()
        PerfilUsuario.objects.filter(usuario_id=user.pk).update(clave_cambiada_en=timezone.now())

    def get_help_text(self):
        e = _empresa()
        partes = [f'al menos {e.clave_largo_minimo} caracteres']
        if e.clave_mayuscula:
            partes.append('mayúsculas y minúsculas')
        if e.clave_numero:
            partes.append('un número')
        if e.clave_simbolo:
            partes.append('un símbolo')
        return 'Su contraseña debe tener ' + ', '.join(partes) + '.'


def clave_vencida(user, empresa=None):
    dias = (empresa or _empresa()).clave_vence_dias
    if not dias or not user.is_authenticated:
        return False
    from .models import PerfilUsuario
    perfil = PerfilUsuario.objects.filter(usuario=user).only('pk', 'clave_cambiada_en').first()
    if perfil is None:
        return False
    if perfil.clave_cambiada_en is None:  # usuario anterior a la política: el plazo corre desde hoy
        PerfilUsuario.objects.filter(pk=perfil.pk).update(clave_cambiada_en=timezone.now())
        return False
    return timezone.now() - perfil.clave_cambiada_en >= timedelta(days=dias)


# ---------------------------------------------------------------- verificación en dos pasos obligatoria
SENSIBLES = {f'{m}.{a}' for m, lista in ACCIONES.items() for a, _, s in lista if s}


def exige_doble_factor(user, empresa=None):
    modo = (empresa or _empresa()).exigir_doble_factor
    if not modo or not user.is_authenticated or hasattr(user, 'acceso_proveedor'):
        return False
    if modo == 'TODOS' or user.is_superuser:
        return True
    perfil = perfil_de(user)
    if perfil is None:
        return True  # sin perfil puede las acciones sensibles de sus módulos
    if perfil.limite_aprobacion or SENSIBLES & set(perfil.acciones or []):
        return True
    from .models import ReglaAprobacion
    return ReglaAprobacion.objects.filter(aprobador=user, activo=True).exists()


# ---------------------------------------------------------------- equipo nuevo
def _huella(token):
    return hashlib.sha256(token.encode()).hexdigest()


def revisar_equipo(request, user, response):
    """Reconoce el equipo por una cookie firmada; si es nuevo para el usuario (y ya había ingresado desde otro), le
    avisa por correo. Devuelve True si el equipo era nuevo."""
    from .models import DispositivoConocido
    token = request.get_signed_cookie(COOKIE_EQUIPO, default='', salt='ceiba-equipo')
    if not token:
        token = secrets.token_urlsafe(24)
        response.set_signed_cookie(COOKIE_EQUIPO, token, salt='ceiba-equipo', max_age=400 * 24 * 3600,
                                   httponly=True, samesite='Lax', secure=request.is_secure())
    huella = _huella(token)
    from .acceso import _ip
    navegador = request.META.get('HTTP_USER_AGENT', '')[:200]
    disp = DispositivoConocido.objects.filter(usuario=user, huella=huella).first()
    if disp is not None:
        disp.ip = _ip(request)
        disp.save(update_fields=['ip', 'ultimo'])
        return False
    tenia_otros = DispositivoConocido.objects.filter(usuario=user).exists()
    DispositivoConocido.objects.create(usuario=user, huella=huella, navegador=navegador, ip=_ip(request))
    if tenia_otros and user.email and _empresa().aviso_equipo_nuevo:
        from . import correo
        try:
            correo.enviar([user.email], 'Ingreso desde un equipo nuevo', 'registration/correo_equipo_nuevo.html',
                          {'usuario': user, 'navegador': navegador, 'ip': _ip(request),
                           'fecha': timezone.localtime()})
        except correo.ErrorCorreo:
            pass
    return True


# ---------------------------------------------------------------- middleware
class PoliticasAccesoMiddleware:
    """Cierre de sesión por inactividad, verificación en dos pasos obligatoria y contraseña vencida."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        match = request.resolver_match
        nombre = match.url_name if match else ''
        if match and match.namespace == 'admin':
            return None
        e = _empresa()
        ahora = time.time()
        if e.minutos_inactividad:
            ultima = request.session.get('ultima_actividad')
            if ultima and ahora - ultima > e.minutos_inactividad * 60:
                from django.contrib.auth import logout
                from django.urls import reverse
                logout(request)
                messages.info(request, f'Su sesión se cerró tras {e.minutos_inactividad} minutos sin actividad.')
                return redirect(f'{reverse("login")}?next={request.path}' if request.method == 'GET'
                                else reverse('login'))
            if nombre not in RUTAS_SIN_ACTIVIDAD and (not ultima or ahora - ultima > 15):
                request.session['ultima_actividad'] = ahora
        if nombre in RUTAS_SEGURIDAD:
            return None
        if exige_doble_factor(user, e) and not request.session.get('sso'):  # el proveedor corporativo ya verificó
            from .doble_factor import tiene_doble_factor
            if not tiene_doble_factor(user):
                messages.warning(request, 'La empresa exige la verificación en dos pasos para su usuario: actívela '
                                          'para continuar.')
                return redirect('seguridad')
        if clave_vencida(user, e):
            messages.warning(request, f'Su contraseña venció (se cambia cada {e.clave_vence_dias} días): cree una '
                                      f'nueva para continuar.')
            return redirect('cambiar_clave')
        return None
