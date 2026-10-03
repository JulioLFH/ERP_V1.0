from django.shortcuts import render

from .modulos import POR_CLAVE, modulos_de_ruta, modulos_del_usuario


class AccesoModulosMiddleware:
    """Bloquea las pantallas de módulos no asignados al usuario y recuerda el módulo activo.

    Las pantallas compartidas (ej. Productos, que se abre desde Ventas, Compras o Inventario)
    se muestran dentro del último módulo que el usuario estaba usando.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        request.modulo_actual = None
        if not request.user.is_authenticated:
            return None
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
