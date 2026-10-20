from django.conf import settings

from .models import Empresa
from .modulos import MODULOS, POR_CLAVE, menu_de, modulos_del_usuario, puede_ver_costos
from .permisos import Permisos
from .politicas import ocultas, ver_saldos, ver_sueldos


def empresa(request):
    if not request.user.is_authenticated:
        return {'erp_version': settings.ERP_VERSION, 'erp_nombre': settings.ERP_NOMBRE,
                'empresa': Empresa.actual()}  # inicio de sesión y enlace de aceptación del proveedor
    mods = modulos_del_usuario(request.user)
    clave = getattr(request, 'modulo_actual', None)
    modulo = POR_CLAVE.get(clave)
    from erp.empresas import actual, empresas, es_multiempresa, razon_social

    from .grupo import corporativo, empresas_de
    es_corporativo = corporativo(request) is not None
    if es_corporativo:  # solo las empresas a las que puede pasar sin volver a ingresar
        otras = [(a, n) for a, n in empresas_de(request) if a != actual()]
    else:
        otras = [(a, razon_social(a)) for a in empresas() if a != actual()] if es_multiempresa() else []
    return {
        'otras_empresas': otras, 'corporativo': es_corporativo,
        'empresa': Empresa.actual(),
        'admin_django': bool(settings.ADMIN_URL) and request.user.is_superuser,
        'erp_version': settings.ERP_VERSION,
        'erp_nombre': settings.ERP_NOMBRE,
        'mods': mods,
        'apps': [m for m in MODULOS if m['clave'] in mods],
        'modulo': modulo,
        'menu_modulo': menu_de(modulo, puede_ver_costos(request.user), request.get_full_path(),
                               request.user.is_superuser, ocultas(request.user)) if modulo else [],
        'ver_costos': puede_ver_costos(request.user),
        'ver_sueldos': ver_sueldos(request.user), 'ver_saldos': ver_saldos(request.user),
        'puede': Permisos(request.user),  # {% if puede.ventas_anular %}
        'url_carga': _url_carga(request, mods),
    }


def _url_carga(request, mods):
    """Lista de un maestro con carga masiva: el enlace a su «Importar desde Excel» (si el usuario tiene el módulo)."""
    from django.urls import reverse

    from .carga_masiva import CARGA_DE_LISTA, DEFINICIONES
    vista = getattr(getattr(request, 'resolver_match', None), 'view_name', '')
    tipo = CARGA_DE_LISTA.get(vista)
    if not tipo or not (request.user.is_superuser or set(DEFINICIONES[tipo]['modulos']) & set(mods)):
        return ''
    return f'{reverse("carga_masiva")}?tipo={tipo}'
