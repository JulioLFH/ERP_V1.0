"""REQ-04: perfiles de acceso predefinidos para cada jefatura de área (y la gerencia en solo lectura).

Se crean con «Crear perfiles predefinidos» (Ajustes › Perfiles de acceso, solo el administrador) y en cada empresa
nueva del grupo. Si un perfil con ese nombre ya existe no se toca: el administrador puede ajustarlos libremente.
Las acciones más delicadas (reabrir periodos, liberar costos, aprobar pagos, ver sueldos fuera de RR. HH.) quedan
para que el administrador las asigne a mano."""

# (nombre, descripción, módulos, acciones, {ver_costos, ver_sueldos, ver_saldos})
PERFILES = [
    ('Jefe de Ventas', 'Comprobantes, notas, precios, aprobación de pedidos y CRM de todo el equipo',
     ['tablero', 'ventas', 'crm', 'toma_pedido'],
     ['ventas.emitir', 'ventas.notas', 'ventas.anular', 'ventas.precios', 'ventas.bajo_tarifa',
      'ventas.aprobar_pedidos', 'ventas.devoluciones', 'ventas.vendedor', 'crm.registrar', 'crm.ver_todas',
      'crm.postventa', 'crm.configurar', 'toma_pedido.registrar', 'contactos.registrar'],
     {'ver_costos': False, 'ver_sueldos': False, 'ver_saldos': False}),
    ('Jefe de Compras', 'Órdenes de compra y su aprobación, facturas de proveedores, solicitudes y portal',
     ['tablero', 'compras', 'requerimientos', 'portal_proveedores'],
     ['compras.registrar', 'compras.oc', 'compras.aprobar_oc', 'compras.anular', 'compras.anular_oc',
      'compras.convertir_solped', 'compras.hes', 'compras.liberar_hes', 'compras.hes_todas', 'compras.grupos',
      'requerimientos.solped', 'requerimientos.liberar_solped', 'portal_proveedores.aprobar', 'contactos.registrar'],
     {'ver_costos': True, 'ver_sueldos': False, 'ver_saldos': False}),
    ('Jefe de Almacén y Logística', 'Operaciones y ajustes de almacén, cierre del kardex, guías y reparto',
     ['tablero', 'inventario', 'logistica', 'distribucion', 'codigo_barras', 'requerimientos'],
     ['inventario.operar', 'inventario.ajustar', 'inventario.aprobar_mayor', 'inventario.anular', 'inventario.cerrar',
      'inventario.estado_stock', 'logistica.emitir', 'logistica.anular', 'logistica.garita', 'logistica.ver_montos',
      'distribucion.programar', 'distribucion.entregar', 'codigo_barras.asignar', 'requerimientos.solicitar',
      'requerimientos.aprobar'],
     {'ver_costos': True, 'ver_sueldos': False, 'ver_saldos': False}),
    ('Jefe de Tesorería y Finanzas', 'Cobros, pagos, transferencias, cuentas, financiamiento y cobranza',
     ['tablero', 'tesoreria', 'finanzas', 'rendiciones'],
     ['tesoreria.registrar', 'tesoreria.transferir', 'tesoreria.anular', 'tesoreria.cuentas', 'finanzas.registrar',
      'finanzas.anular', 'finanzas.cuentas', 'finanzas.cobranza', 'finanzas.financiamiento', 'finanzas.planeamiento',
      'finanzas.credito', 'rendiciones.rendir', 'rendiciones.aprobar', 'rendiciones.tesoreria'],
     {'ver_costos': False, 'ver_sueldos': False, 'ver_saldos': True}),
    ('Contador General', 'Asientos, centralización y cierre, plan de cuentas, SUNAT (SIRE y libros) y activos',
     ['tablero', 'contabilidad', 'sunat', 'activos', 'presupuestos', 'rendiciones'],
     ['contabilidad.asientos', 'contabilidad.bancos', 'contabilidad.extornar', 'contabilidad.periodos',
      'contabilidad.configurar', 'sunat.sire', 'sunat.detracciones', 'activos.registrar', 'activos.baja',
      'activos.depreciar', 'rendiciones.contabilizar'],
     {'ver_costos': True, 'ver_sueldos': False, 'ver_saldos': True}),
    ('Jefe de RR. HH.', 'Trabajadores, planillas, sueldos, organigrama, vacaciones y permisos',
     ['tablero', 'planillas', 'empleados', 'tiempo_personal'],
     ['planillas.calcular', 'planillas.cerrar', 'planillas.configurar', 'planillas.ver_sueldos', 'empleados.registrar',
      'empleados.aprobar_movimientos', 'empleados.configurar', 'tiempo_personal.registrar'],
     {'ver_costos': False, 'ver_sueldos': True, 'ver_saldos': False}),
    ('Jefe de Producción', 'Órdenes de producción, recetas, planificación, calidad en planta y mantenimiento',
     ['tablero', 'manufactura', 'planificacion', 'control_calidad', 'mantenimiento', 'costos'],
     ['manufactura.ordenes', 'manufactura.anular', 'manufactura.recetas', 'manufactura.aprobar_cambios',
      'manufactura.consumo_adicional', 'manufactura.aprobar_consumo', 'planificacion.planificar',
      'control_calidad.inspecciones', 'control_calidad.registros_gmp', 'mantenimiento.registrar',
      'mantenimiento.calibrar', 'mantenimiento.avisos'],
     {'ver_costos': True, 'ver_sueldos': False, 'ver_saldos': False}),
    ('Jefe de Obras y Proyectos', 'Obras, presupuestos, ejecución, valorizaciones, licitaciones y proyectos',
     ['tablero', 'obras', 'proyectos'],
     ['obras.presupuestar', 'obras.aprobar', 'obras.requerir', 'obras.licitar', 'obras.ejecutar', 'obras.facturar',
      'proyectos.gestionar', 'proyectos.ver_todos'],
     {'ver_costos': True, 'ver_sueldos': False, 'ver_saldos': False}),
    ('Jefe de SST y Calidad', 'Seguridad y salud en el trabajo y sistema de gestión de calidad',
     ['tablero', 'sst', 'calidad'],
     ['sst.registrar', 'sst.validar', 'sst.configurar', 'calidad.registrar', 'calidad.validar'],
     {'ver_costos': False, 'ver_sueldos': False, 'ver_saldos': False}),
]
# la gerencia ve todos los módulos de la empresa, sin registrar ni modificar (perfil sin acciones = solo lectura)
SIN_GERENCIA = {'ajustes', 'cliente', 'asistente'}


def perfil_gerencia():
    from .modulos import GRUPOS
    return ('Gerencia (solo lectura)', 'Ve todos los módulos y reportes, sin registrar ni modificar',
            sorted(m for m in GRUPOS if m not in SIN_GERENCIA), [],
            {'ver_costos': True, 'ver_sueldos': False, 'ver_saldos': True})


def crear(using=None):
    """Crea los perfiles que falten (por nombre). Devuelve los nombres creados."""
    from .models import PerfilAcceso
    from .modulos import GRUPOS
    from .permisos import TODAS
    manager = PerfilAcceso.objects.using(using) if using else PerfilAcceso.objects
    existentes = set(manager.values_list('nombre', flat=True))
    creados = []
    for nombre, descripcion, modulos, acciones, datos in PERFILES + [perfil_gerencia()]:
        if nombre in existentes:
            continue
        manager.create(nombre=nombre, descripcion=descripcion, modulos=[m for m in modulos if m in GRUPOS],
                       acciones=sorted(a for a in acciones if a in TODAS), **datos)
        creados.append(nombre)
    return creados
