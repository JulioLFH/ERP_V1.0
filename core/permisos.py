"""Permisos por acción, almacén y serie (además del acceso por módulo).

Cada ruta sensible exige una acción (RUTAS). El middleware la valida antes de abrir la vista, así el control
no depende de que cada pantalla oculte sus botones. Superusuario y usuarios sin perfil: todas las acciones de
sus módulos."""
from decimal import Decimal

# módulo -> [(acción, descripción, sensible)]; las sensibles vienen desmarcadas para usuarios nuevos
ACCIONES = {
    'ventas': [('emitir', 'Emitir comprobantes y cotizaciones', False),
               ('notas', 'Emitir notas de crédito y débito', True),
               ('anular', 'Anular comprobantes', True),
               ('precios', 'Mantener listas de precios y descuentos', True)],
    'compras': [('registrar', 'Registrar facturas de compra', False),
                ('oc', 'Crear y editar órdenes de compra', False),
                ('aprobar_oc', 'Aprobar órdenes de compra', True),
                ('anular', 'Anular compras y órdenes de compra', True),
                ('portal', 'Aprobar o rechazar facturas del portal', True)],
    'inventario': [('operar', 'Registrar y confirmar operaciones de almacén', False),
                   ('ajustar', 'Ajustes de inventario', True),
                   ('anular', 'Anular operaciones', True),
                   ('cerrar', 'Cerrar el kardex', True)],
    'logistica': [('emitir', 'Emitir guías de remisión', False),
                  ('anular', 'Anular guías', True)],
    'finanzas': [('registrar', 'Registrar cobros, pagos y movimientos', False),
                 ('transferir', 'Transferencias entre cuentas', False),
                 ('anular', 'Anular movimientos', True),
                 ('cuentas', 'Crear cuentas y saldos iniciales', True)],
    'contabilidad': [('asientos', 'Asientos manuales', False),
                     ('extornar', 'Extornar asientos', True),
                     ('periodos', 'Centralizar y cerrar periodos', True),
                     ('reabrir', 'Reabrir periodos cerrados (con motivo)', True),
                     ('configurar', 'Plan de cuentas y configuración', True),
                     ('presupuesto', 'Elaborar y modificar presupuestos', True)],
    'manufactura': [('ordenes', 'Órdenes de producción y planificación (MRP)', False),
                    ('calidad', 'Inspecciones de calidad y planes de calidad', False),
                    ('mantenimiento', 'Equipos y órdenes de mantenimiento', False),
                    ('anular', 'Anular órdenes de producción', True),
                    ('recetas', 'Recetas, hojas de ruta, versiones, puestos y costo estándar', True)],
    'costos': [('liberar', 'Calcular y liberar el costo estándar', True)],
    'planillas': [('calcular', 'Registrar trabajadores y calcular planillas', False),
                  ('cerrar', 'Cerrar, reabrir y pagar planillas', True),
                  ('configurar', 'Parámetros, AFP y conceptos de planilla', True)],
    'requerimientos': [('solicitar', 'Pedir materiales al almacén', False),
                       ('aprobar', 'Aprobar o rechazar requerimientos', True)],
    'activos': [('registrar', 'Registrar activos', False),
                ('baja', 'Dar de baja o anular activos', True),
                ('depreciar', 'Calcular y revertir depreciación', True)],
}
TODAS = {f'{m}.{a}' for m, lista in ACCIONES.items() for a, _, _ in lista}
# acciones sensibles nuevas: nunca se dan por defecto (ni a usuarios sin perfil); el administrador las asigna
EXPLICITAS = {'requerimientos.aprobar', 'contabilidad.reabrir', 'costos.liberar'}

# ruta -> acción exigida. Valor str = siempre; dict = según el método o un dato de la petición (función)
RUTAS = {
    'ventas:nuevo': lambda r: 'ventas.notas' if _tipo(r) in ('07', '08') else 'ventas.emitir',
    'ventas:editar': 'ventas.emitir', 'ventas:anular': 'ventas.anular', 'ventas:eliminar': 'ventas.anular',
    'ventas:importar': 'ventas.emitir', 'ventas:notas': 'ventas.notas', 'ventas:trasladar': 'ventas.emitir',
    'ventas:lista_precios_nueva': 'ventas.precios', 'ventas:lista_precios_editar': 'ventas.precios',
    'ventas:cot_nuevo': 'ventas.emitir', 'ventas:cot_editar': 'ventas.emitir', 'ventas:cot_estado': 'ventas.emitir',
    'compras:nuevo': 'compras.registrar', 'compras:editar': 'compras.registrar', 'compras:importar': 'compras.registrar',
    'compras:importar_xml': 'compras.registrar', 'compras:importar_xml_revisar': 'compras.registrar',
    'compras:trasladar': 'compras.registrar', 'compras:notas': 'compras.registrar',
    'compras:ingresar_almacen': 'compras.registrar',
    'compras:importacion_nueva': 'compras.registrar', 'compras:importacion': {'POST': 'compras.registrar'},
    'compras:anular': 'compras.anular', 'compras:eliminar': 'compras.anular',
    'compras:oc_nuevo': 'compras.oc', 'compras:oc_editar': 'compras.oc', 'compras:oc_enviar': 'compras.oc',
    'compras:oc_estado': lambda r: {'APROBADO': 'compras.aprobar_oc', 'ANULADO': 'compras.anular'}.get(
        r.POST.get('estado'), 'compras.oc'),
    'compras:portal_factura_aprobar': 'compras.portal', 'compras:portal_factura_rechazar': 'compras.portal',
    'compras:portal_acceso_nuevo': 'compras.portal', 'compras:portal_acceso_editar': 'compras.portal',
    'inventario:nueva': 'inventario.operar', 'inventario:editar': 'inventario.operar',
    'inventario:confirmar': 'inventario.operar', 'inventario:pendientes': 'inventario.operar',
    'inventario:eliminar': 'inventario.operar', 'inventario:anular': 'inventario.anular',
    'inventario:cierre_reabrir': 'inventario.cerrar', 'inventario:tipo_nuevo': 'inventario.cerrar',
    'inventario:tipo_editar': 'inventario.cerrar', 'inv_ajuste': 'inventario.ajustar',
    'inventario:cierres': {'POST': 'inventario.cerrar'}, 'ubicaciones': {'POST': 'inventario.operar'},
    'logistica:nueva': 'logistica.emitir', 'logistica:editar': 'logistica.emitir',
    'logistica:enviar_sunat': 'logistica.emitir', 'logistica:anular': 'logistica.anular',
    'finanzas:movimiento_nuevo': 'finanzas.registrar', 'finanzas:cobranza': 'finanzas.registrar',
    'finanzas:pago': 'finanzas.registrar', 'finanzas:importar': 'finanzas.registrar',
    'finanzas:extractos': {'POST': 'finanzas.registrar'}, 'finanzas:extracto_accion': 'finanzas.registrar',
    'finanzas:transferencia': 'finanzas.transferir', 'finanzas:movimiento_eliminar': 'finanzas.anular',
    'finanzas:cuenta_nueva': 'finanzas.cuentas', 'finanzas:cuenta_editar': 'finanzas.cuentas',
    'finanzas:anticipo_aplicar': {'POST': 'finanzas.registrar'}, 'finanzas:aplicacion_anular': 'finanzas.anular',
    'finanzas:canje_nuevo': {'POST': 'finanzas.registrar'},
    'finanzas:canje': {'POST': 'finanzas.anular'}, 'finanzas:letra': {'POST': 'finanzas.registrar'},
    'finanzas:cheques': {'POST': 'finanzas.registrar'},
    'finanzas:cheque_estado': lambda r: 'finanzas.registrar' if r.POST.get('estado') == 'COBRADO' else 'finanzas.anular',
    'finanzas:entrega_nueva': 'finanzas.registrar', 'finanzas:entrega': {'POST': 'finanzas.registrar'},
    'finanzas:pago_masivo_nuevo': {'POST': 'finanzas.registrar'}, 'finanzas:pago_masivo': {'POST': 'finanzas.registrar'},
    'finanzas:saldo_inicial': {'POST': 'finanzas.cuentas'}, 'finanzas:saldo_inicial_sustento': 'finanzas.cuentas',
    'contabilidad:asiento_nuevo': 'contabilidad.asientos', 'contabilidad:asiento_editar': 'contabilidad.asientos',
    'contabilidad:asiento_eliminar': 'contabilidad.extornar', 'contabilidad:asiento_extornar': 'contabilidad.extornar',
    'contabilidad:periodos': lambda r: (None if r.method != 'POST' else 'contabilidad.reabrir'
                                        if r.POST.get('accion') == 'abrir' else 'contabilidad.periodos'),
    'contabilidad:cuenta_nueva': 'contabilidad.configurar', 'contabilidad:cuenta_editar': 'contabilidad.configurar',
    'contabilidad:configuracion': {'POST': 'contabilidad.configurar'},
    'contabilidad:cc_nuevo': 'contabilidad.configurar', 'contabilidad:cc_editar': 'contabilidad.configurar',
    'planillas:nueva': 'planillas.calcular', 'planillas:trabajador_nuevo': 'planillas.calcular',
    'planillas:trabajador_editar': {'POST': 'planillas.calcular'},
    'planillas:detalle': lambda r: (None if r.method != 'POST' else 'planillas.cerrar'
                                    if r.POST.get('accion') in ('cerrar', 'reabrir', 'pagar') else 'planillas.calcular'),
    'planillas:configuracion': {'POST': 'planillas.configurar'},
    'planillas:vacaciones': {'POST': 'planillas.calcular'},
    'contabilidad:presupuesto_nuevo': 'contabilidad.presupuesto',
    'contabilidad:presupuesto_editar': 'contabilidad.presupuesto',
    'contabilidad:presupuesto_generar': 'contabilidad.presupuesto',
    'contabilidad:cb_nuevo': 'contabilidad.configurar', 'contabilidad:cb_editar': 'contabilidad.configurar',
    'manufactura:orden_nueva': 'manufactura.ordenes', 'manufactura:orden_editar': 'manufactura.ordenes',
    'manufactura:orden_confirmar': 'manufactura.ordenes', 'manufactura:orden_iniciar': 'manufactura.ordenes',
    'manufactura:orden_terminar': 'manufactura.ordenes', 'manufactura:orden_maquila': 'manufactura.ordenes',
    'manufactura:inspeccion_nueva': 'manufactura.calidad', 'manufactura:inspeccion': {'POST': 'manufactura.calidad'},
    'manufactura:plan_calidad': {'POST': 'manufactura.calidad'},
    'manufactura:equipo_nuevo': {'POST': 'manufactura.mantenimiento'},
    'manufactura:equipo': {'POST': 'manufactura.mantenimiento'},
    'manufactura:ordenes_mant': {'POST': 'manufactura.mantenimiento'},
    'manufactura:orden_mant_nueva': 'manufactura.mantenimiento',
    'manufactura:orden_mant': {'POST': 'manufactura.mantenimiento'},
'manufactura:orden_eliminar': 'manufactura.ordenes',
    'manufactura:orden_anular': 'manufactura.anular',
    'manufactura:lista_nueva': 'manufactura.recetas', 'manufactura:lista_editar': 'manufactura.recetas',
    'manufactura:lista_version': 'manufactura.recetas', 'manufactura:centro_nuevo': 'manufactura.recetas',
    'manufactura:centro_editar': 'manufactura.recetas', 'manufactura:hoja_nueva': 'manufactura.recetas',
    'manufactura:hoja_editar': 'manufactura.recetas', 'manufactura:hoja_obsoleta': 'manufactura.recetas',
    'manufactura:lista_obsoleta': 'manufactura.recetas', 'manufactura:version_nueva': 'manufactura.recetas',
    'manufactura:version_editar': 'manufactura.recetas', 'manufactura:mrp': {'POST': 'manufactura.ordenes'},
    'costos:estandar': {'POST': 'costos.liberar'},
    'requerimientos:nuevo': 'requerimientos.solicitar', 'requerimientos:editar': 'requerimientos.solicitar',
    'requerimientos:enviar': 'requerimientos.solicitar',
    'activos:nuevo': 'activos.registrar', 'activos:editar': 'activos.registrar', 'activos:baja': 'activos.baja',
    'activos:anular': 'activos.baja', 'activos:depreciacion': {'POST': 'activos.depreciar'},
    'activos:proceso_revertir': 'activos.depreciar', 'activos:categoria_nueva': 'activos.depreciar',
    'activos:categoria_editar': 'activos.depreciar',
}
CAMPOS_ALMACEN = ('almacen', 'almacen_origen', 'almacen_destino', 'almacen_insumos')


def _tipo(request):
    return request.POST.get('tipo_comprobante') or request.GET.get('tipo') or ''


def perfil_de(user):
    if not user.is_authenticated or user.is_superuser:
        return None
    if not hasattr(user, '_perfil_cache'):
        from .models import PerfilUsuario
        user._perfil_cache = PerfilUsuario.objects.filter(usuario=user).prefetch_related(
            'almacenes', 'series').first()
    return user._perfil_cache


def puede(user, accion):
    """¿El usuario puede realizar la acción? Exige además el módulo de la acción."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from .modulos import modulos_del_usuario
    if accion.split('.')[0] not in modulos_del_usuario(user):
        return False
    perfil = perfil_de(user)
    if perfil is None:
        return accion not in EXPLICITAS
    return accion in perfil.acciones


def accion_de_ruta(match, request):
    if match is None:
        return None
    clave = f'{match.namespace}:{match.url_name}' if match.namespace else match.url_name
    regla = RUTAS.get(clave)
    if callable(regla):
        return regla(request)
    if isinstance(regla, dict):
        return regla.get(request.method)
    return regla


def almacenes_permitidos(user):
    """IDs de almacenes en que opera el usuario; None = todos."""
    perfil = perfil_de(user) if user is not None else None
    if perfil is None:
        return None
    ids = {a.pk for a in perfil.almacenes.all()}
    return ids or None


def series_permitidas(user, tipo=None):
    """Series (texto) que emite el usuario, opcionalmente de un tipo; None = todas."""
    perfil = perfil_de(user) if user is not None else None
    todas = list(perfil.series.all()) if perfil is not None else []
    if not todas:
        return None
    return [s.serie for s in todas if tipo is None or s.tipo == tipo]


# documentos existentes cuyas acciones (confirmar, anular, editar) se limitan a los almacenes del usuario
def _documento_almacenes(match):
    pk = match.kwargs.get('pk')
    if not pk:
        return None
    modelo, campos = {
        'inventario': ('inventario.Operacion', ('almacen_origen_id', 'almacen_destino_id')),
        'manufactura': ('produccion.OrdenProduccion', ('almacen_insumos_id', 'almacen_destino_id')),
        'logistica': ('logistica.GuiaRemision', ('almacen_origen_id', 'almacen_destino_id')),
        'ventas': ('ventas.Venta', ('almacen_id',)),
        'compras': ('compras.Compra', ('almacen_id',)),
    }.get(match.namespace, (None, None))
    # las rutas de órdenes de compra, cotizaciones y portal usan otros modelos
    if modelo is None or match.url_name.startswith(('oc_', 'cot_', 'portal_')):
        return None
    from django.apps import apps
    obj = apps.get_model(modelo)._base_manager.filter(pk=pk).first()
    if obj is None:
        return None
    return {getattr(obj, c, None) for c in campos} - {None}


def almacen_no_permitido(request, match=None):
    """Almacén fuera de los permitidos: el elegido en el formulario (POST) o el del documento sobre el que se
    actúa (confirmar, anular...). None si todo está permitido."""
    permitidos = almacenes_permitidos(request.user)
    if permitidos is None or request.method != 'POST':
        return None
    from .models import Almacen
    for campo in CAMPOS_ALMACEN:
        valor = request.POST.get(campo)
        if valor and valor.isdigit() and int(valor) not in permitidos:
            return Almacen.objects.filter(pk=valor).first()
    if match is not None:
        fuera = (_documento_almacenes(match) or set()) - permitidos
        if fuera:
            return Almacen.objects.filter(pk__in=fuera).first()
    return None


def validar_serie(datos, tipo, por_defecto):
    """Serie del usuario para un comprobante o guía nuevo: si la deja vacía toma la primera que tiene asignada
    del tipo. Devuelve un mensaje de error o '' (y fija datos['serie'])."""
    from .auditoria import usuario_actual
    permitidas = series_permitidas(usuario_actual(), tipo)
    if permitidas is None:
        return ''
    serie = (datos.get('serie') or '').upper()
    if not permitidas:
        return 'Su usuario no tiene series asignadas para este tipo de documento.'
    if not serie:
        datos['serie'] = por_defecto if por_defecto in permitidas else permitidas[0]
        return ''
    if serie not in permitidas:
        return f'Su usuario solo emite con las series {", ".join(permitidas)}.'
    return ''


def puede_aprobar(user, monto_pen):
    """Aprobación de órdenes de compra hasta el límite del usuario (0 = sin límite)."""
    if not puede(user, 'compras.aprobar_oc'):
        return False
    perfil = perfil_de(user)
    limite = perfil.limite_aprobacion if perfil else Decimal('0')
    return not limite or monto_pen <= limite


class Permisos:
    """Para las plantillas: {% if puede.ventas_anular %} (módulo_acción)."""

    def __init__(self, user):
        self.user = user

    def __getattr__(self, nombre):
        modulo, _, accion = nombre.partition('_')
        if not accion:
            raise AttributeError(nombre)
        return puede(self.user, f'{modulo}.{accion}')
