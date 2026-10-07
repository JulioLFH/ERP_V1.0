from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render

from .modulos import POR_CLAVE, modulos_de_ruta, modulos_del_usuario


class SeguridadMiddleware:
    """Cabecera CSP en todas las respuestas y panel de administración de Django restringido: solo desde las IP
    permitidas (ADMIN_IPS) y, ya autenticado, solo para superusuarios; para los demás la ruta no existe."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        prefijo = f'/{settings.ADMIN_URL}/' if settings.ADMIN_URL else None
        if prefijo and request.path.startswith(prefijo):
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR', ''))
            usuario = getattr(request, 'user', None)
            if (settings.ADMIN_IPS and ip not in settings.ADMIN_IPS) or (
                    usuario is not None and usuario.is_authenticated and not usuario.is_superuser):
                raise Http404
        response = self.get_response(request)
        if settings.CONTENT_SECURITY_POLICY and 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = settings.CONTENT_SECURITY_POLICY
        return response

# Lo único que puede abrir un usuario del portal de proveedores fuera del portal
RUTAS_PROVEEDOR = {'logout', 'login', 'cambiar_clave', 'cambiar_clave_ok', 'seguridad', 'login_2fa'}


class AccesoModulosMiddleware:
    """Bloquea las pantallas de módulos no asignados al usuario y recuerda el módulo activo.

    Las pantallas compartidas (ej. Productos, que se abre desde Ventas, Compras o Inventario)
    se muestran dentro del último módulo que el usuario estaba usando.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Movimiento de almacén en un periodo con el kardex cerrado: aviso claro en vez de un error 500."""
        from inventario.cierre import KardexCerrado
        if isinstance(exception, KardexCerrado):
            from django.contrib import messages
            messages.error(request, str(exception))
            return redirect(request.META.get('HTTP_REFERER') or 'home')
        return None

    def process_view(self, request, view_func, view_args, view_kwargs):
        request.modulo_actual = None
        if not request.user.is_authenticated:
            return None
        match = request.resolver_match
        if hasattr(request.user, 'acceso_proveedor') and not request.user.is_superuser:
            # usuario del portal de proveedores: no entra al ERP
            if match and (match.namespace == 'portal' or match.url_name in RUTAS_PROVEEDOR):
                return None
            return redirect('portal:inicio')
        if match and match.namespace == 'portal':
            return None  # el portal valida su propio acceso
        permitidos = modulos_de_ruta(request.resolver_match)
        if permitidos is None:
            return None
        mods = modulos_del_usuario(request.user)
        accesibles = [m for m in permitidos if m in mods]
        if not accesibles:
            return render(request, 'core/sin_acceso.html',
                          {'modulo_bloqueado': POR_CLAVE.get(permitidos[0])}, status=403)
        ultimo = request.session.get('modulo')
        actual = ultimo if ultimo in accesibles else accesibles[0]
        if ultimo != actual:
            request.session['modulo'] = actual
        request.modulo_actual = actual
        # permisos por acción, almacén (y monto para aprobar órdenes de compra)
        from .permisos import ACCIONES, accion_de_ruta, almacen_no_permitido, puede
        accion = accion_de_ruta(match, request)
        if accion and not puede(request.user, accion):
            modulo, _, clave = accion.partition('.')
            texto = next((d for a, d, _ in ACCIONES.get(modulo, []) if a == clave), accion)
            return _denegar(request, f'Su usuario no tiene permiso para: {texto.lower()}. Pida al administrador que '
                                     f'se lo asigne en Ajustes > Usuarios y permisos.')
        almacen = almacen_no_permitido(request, match)
        if almacen is not None:
            return _denegar(request, f'Su usuario no opera en el almacén "{almacen}".')
        return None


def _denegar(request, motivo):
    """Acción sin permiso. Si vino de un botón de otra pantalla del sistema, vuelve a ella con el aviso (no se pierde
    lo que se estaba viendo); si no, muestra la página de acceso denegado."""
    from django.contrib import messages
    from django.utils.http import url_has_allowed_host_and_scheme
    origen = request.META.get('HTTP_REFERER', '')
    if request.method == 'POST' and origen and url_has_allowed_host_and_scheme(
            origen, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        from urllib.parse import urlsplit
        partes = urlsplit(origen)
        messages.error(request, f'Acción no permitida. {motivo}')
        return redirect(partes.path + (f'?{partes.query}' if partes.query else ''))
    return render(request, 'core/sin_acceso.html', {'motivo': motivo}, status=403)
