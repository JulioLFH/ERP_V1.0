"""Segregación de funciones (SoD): pares de permisos que una misma persona no debería tener juntos y la regla de que
quien registra un documento no lo aprueba."""
from django.contrib.auth import get_user_model

from .permisos import ACCIONES, puede

# (acción 1, acción 2, riesgo, por qué)
REGLAS = [
    ('compras.oc', 'compras.aprobar_oc', 'ALTO', 'Crea y aprueba sus propias órdenes de compra'),
    ('compras.registrar', 'finanzas.registrar', 'ALTO', 'Registra facturas de proveedores y también las paga'),
    ('finanzas.cuentas', 'finanzas.registrar', 'MEDIO', 'Crea cuentas bancarias y registra pagos en ellas'),
    ('requerimientos.solicitar', 'requerimientos.aprobar', 'MEDIO', 'Pide materiales y aprueba sus pedidos'),
    ('inventario.operar', 'inventario.ajustar', 'MEDIO', 'Opera el almacén y ajusta sus diferencias'),
    ('ventas.emitir', 'ventas.anular', 'MEDIO', 'Emite comprobantes y los anula'),
    ('ventas.emitir', 'finanzas.registrar', 'BAJO', 'Vende y cobra (aceptable en caja de tienda)'),
    ('planillas.calcular', 'planillas.cerrar', 'ALTO', 'Calcula la planilla y la cierra / paga'),
    ('contabilidad.asientos', 'contabilidad.periodos', 'MEDIO', 'Registra asientos manuales y cierra periodos'),
    ('activos.registrar', 'activos.baja', 'MEDIO', 'Registra activos y los da de baja'),
    ('manufactura.recetas', 'manufactura.aprobar_cambios', 'MEDIO', 'Propone y aprueba cambios de recetas'),
    ('compras.registrar', 'compras.portal', 'BAJO', 'Registra facturas y aprueba las del portal de proveedores'),
]
NOMBRES = {f'{m}.{a}': d for m, lista in ACCIONES.items() for a, d, _ in lista}


def conflictos(user):
    """Reglas que el usuario incumple (tiene ambos permisos)."""
    if user.is_superuser:
        return []
    return [{'a': a, 'b': b, 'riesgo': r, 'motivo': m, 'nombre_a': NOMBRES.get(a, a), 'nombre_b': NOMBRES.get(b, b)}
            for a, b, r, m in REGLAS if puede(user, a) and puede(user, b)]


def reporte():
    """Usuarios activos con sus conflictos; los administradores (acceso total) aparecen aparte."""
    filas, admins = [], []
    for u in get_user_model().objects.filter(is_active=True).order_by('username'):
        if u.is_superuser:
            admins.append(u)
            continue
        c = conflictos(u)
        filas.append({'u': u, 'conflictos': c, 'altos': sum(1 for x in c if x['riesgo'] == 'ALTO')})
    filas.sort(key=lambda f: (-f['altos'], -len(f['conflictos']), f['u'].username))
    return filas, admins


def estricta():
    from .models import Empresa
    return Empresa.actual().segregacion_estricta


def _otro_usuario(user):
    return get_user_model().objects.filter(is_active=True).exclude(pk=user.pk).exists()


def creador(obj):
    """Usuario que registró el documento: su campo propio o la bitácora de auditoría."""
    for campo in ('creado_por_id', 'solicitante_id', 'solicitado_por_id'):
        if getattr(obj, campo, None):
            return getattr(obj, campo)
    from .models import Bitacora
    return (Bitacora.objects.filter(accion='CREAR', modelo=obj._meta.label, objeto_id=str(obj.pk))
            .values_list('usuario_id', flat=True).first())


def error_aprobacion(user, obj):
    """Mensaje si, con la segregación estricta activa, el usuario quiere aprobar lo que él mismo registró."""
    if not estricta() or not _otro_usuario(user):
        return ''
    if creador(obj) == user.pk:
        return (f'Segregación de funciones: usted registró {obj} y no puede aprobarlo; debe hacerlo otro usuario '
                '(Ajustes > Empresa > Segregación de funciones estricta).')
    return ''
