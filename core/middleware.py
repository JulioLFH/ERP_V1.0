from django.shortcuts import redirect, render

from .modulos import POR_CLAVE, modulos_de_ruta, modulos_del_usuario

# Lo único que puede abrir un usuario del portal de proveedores fuera del portal
RUTAS_PROVEEDOR = {'logout', 'login', 'cambiar_clave', 'cambiar_clave_ok'}


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
        return None
