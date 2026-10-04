from django.conf import settings

from .models import Empresa
from .modulos import MODULOS, POR_CLAVE, menu_de, modulos_del_usuario


def empresa(request):
    if not request.user.is_authenticated:
        return {'erp_version': settings.ERP_VERSION, 'erp_nombre': settings.ERP_NOMBRE,
                'empresa': Empresa.actual()}  # inicio de sesión y enlace de aceptación del proveedor
    mods = modulos_del_usuario(request.user)
    clave = getattr(request, 'modulo_actual', None)
    modulo = POR_CLAVE.get(clave)
    return {
        'empresa': Empresa.actual(),
        'erp_version': settings.ERP_VERSION,
        'erp_nombre': settings.ERP_NOMBRE,
        'mods': mods,
        'apps': [m for m in MODULOS if m['clave'] in mods],
        'modulo': modulo,
        'menu_modulo': menu_de(modulo) if modulo else [],
    }
