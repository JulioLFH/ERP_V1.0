"""REQ-03: plataforma Ceiba. El operador de la plataforma (el proveedor del ERP, independiente de los usuarios del
cliente) define en la consola /plataforma/ el plan del grupo: los módulos que como máximo puede usar cualquier
empresa del grupo. Un módulo fuera del plan no se muestra ni se abre en ninguna empresa, ni para sus
administradores. Dentro del plan, el administrador de cada empresa los asigna a sus usuarios como siempre."""
import time

# siempre disponibles: sin ellos no se puede entrar ni administrar la empresa
FIJOS = {'tablero', 'ajustes'}
SESION = 'operador_plataforma'

# planes sugeridos (el operador puede marcar o desmarcar cualquier módulo)
PLANES = {
    'basico': ('Básico', {'tablero', 'ajustes', 'ventas', 'compras', 'inventario', 'logistica', 'tesoreria',
                          'contabilidad', 'sunat', 'contactos', 'consulta_ruc', 'conversaciones', 'calendario',
                          'actividades'}),
    'estandar': ('Estándar', {'tablero', 'ajustes', 'ventas', 'compras', 'inventario', 'logistica', 'tesoreria',
                              'contabilidad', 'sunat', 'contactos', 'consulta_ruc', 'conversaciones', 'calendario',
                              'actividades', 'finanzas', 'crm', 'toma_pedido', 'requerimientos', 'rendiciones',
                              'activos', 'presupuestos', 'planillas', 'empleados', 'tiempo_personal',
                              'codigo_barras', 'distribucion', 'portal_proveedores'}),
}

_MEMO = {'hora': 0.0, 'valor': None}
SEGUNDOS_MEMO = 15


def todos_los_modulos():
    from .modulos import POR_CLAVE
    return set(POR_CLAVE)


def plan_actual():
    """El PlanGrupo de la base principal, o None si aún no se definió (= todos los módulos)."""
    try:
        from .models import PlanGrupo
        return PlanGrupo.objects.using('default').first()
    except Exception:  # base sin la tabla todavía (antes de migrar)
        return None


def modulos_permitidos():
    """Claves de los módulos que el plan permite (incluye los FIJOS). Se recalcula cada SEGUNDOS_MEMO segundos."""
    if _MEMO['valor'] is not None and time.monotonic() - _MEMO['hora'] < SEGUNDOS_MEMO:
        return _MEMO['valor']
    plan = plan_actual()
    todos = todos_los_modulos()
    valor = todos if plan is None or plan.todos else (set(plan.modulos or []) & todos) | FIJOS
    _MEMO.update(hora=time.monotonic(), valor=valor)
    return valor


def invalidar():
    _MEMO.update(hora=0.0, valor=None)


def guardar_plan(nombre, modulos, todos, operador):
    """Guarda el plan del grupo (en la base principal) y lo aplica de inmediato en este proceso."""
    from .models import PlanGrupo
    plan = plan_actual() or PlanGrupo()
    plan.nombre = (nombre or '').strip()[:60] or ('Completo' if todos else 'Personalizado')
    plan.todos = bool(todos)
    plan.modulos = sorted((set(modulos) & todos_los_modulos()) | FIJOS)
    plan.modificado_por = operador.usuario
    plan.save(using='default')
    invalidar()
    return plan


def operador_de(request):
    """El operador de la plataforma con sesión abierta (o None)."""
    pk = request.session.get(SESION)
    if not pk:
        return None
    from .models import OperadorPlataforma
    return OperadorPlataforma.objects.using('default').filter(pk=pk, activo=True).first()
