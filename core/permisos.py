"""Permisos por acción, almacén y serie (además del acceso por módulo).

Cada ruta sensible exige una acción (RUTAS). El middleware la valida antes de abrir la vista, así el control
no depende de que cada pantalla oculte sus botones. Superusuario y usuarios sin perfil: todas las acciones de
sus módulos."""
from decimal import Decimal

# módulo -> [(acción, descripción, sensible)]; las sensibles vienen desmarcadas para usuarios nuevos
ACCIONES = {
    'crm': [('registrar', 'Registrar y editar oportunidades, sus etapas, actividades, visitas y contactos', False),
            ('ver_todas', 'Ver y reasignar las oportunidades de todos los vendedores (gerencia)', True),
            ('postventa', 'Registrar casos de postventa, encuestas y contratos', False),
            ('configurar', 'Equipos, metas, campañas, formulario web y configuración del CRM', True),
            ('datos_personales', 'Dar de baja, exportar o suprimir datos personales (Ley 29733)', True)],
    'toma_pedido': [('registrar', 'Tomar pedidos de clientes (en el celular) y enviarlos a aprobación', False)],
    'ventas': [('emitir', 'Emitir comprobantes y cotizaciones', False),
               ('notas', 'Emitir notas de crédito y débito', True),
               ('anular', 'Anular comprobantes', True),
               ('precios', 'Mantener listas de precios y descuentos', True),
               ('bajo_tarifa', 'Vender bajo el precio de la lista o con más descuento', True),
               ('aprobar_pedidos', 'Aprobar pedidos de venta (incluye bajo la tarifa y exceso de crédito)', True),
               ('devoluciones', 'Emitir notas de crédito por devolución', True),
               ('reactivar', 'Reactivar una venta anulada que no se envió a SUNAT', True),
               ('vendedor', 'Cambiar el vendedor de un documento', True)],
    'compras': [('registrar', 'Registrar facturas de compra', False),
                ('oc', 'Crear y editar órdenes de compra', False),
                ('aprobar_oc', 'Aprobar órdenes de compra', True),
                ('anular', 'Anular compras y órdenes de compra', True),
                ('convertir_solped', 'Convertir solicitudes de pedido en órdenes de compra o licitaciones', False),
                ('hes', 'Registrar conformidades de servicio (HES) con sus adjuntos', False),
                ('liberar_hes', 'Liberar conformidades de servicio (sin reglas de aprobación)', True),
                ('hes_todas', 'Ver todas las conformidades de servicio', True),
                ('grupos', 'Configurar grupos de compras y compradores', True)],
    'portal_proveedores': [('aprobar', 'Aprobar o rechazar facturas del portal, liberar pagos y dar accesos a '
                                       'proveedores', True)],
    'inventario': [('operar', 'Registrar y confirmar operaciones de almacén', False),
                   ('ajustar', 'Ajustes de inventario', True),
                   ('aprobar_mayor', 'Aprobar diferencias de inventario físico sobre el tope', True),
                   ('anular', 'Anular operaciones', True),
                   ('cerrar', 'Cerrar el kardex', True),
                   ('fecha_anterior', 'Fecha contable de un mes anterior en operaciones de almacén (con motivo)', True),
                   ('estado_stock', 'Cambiar el estado del stock: poner en calidad, bloquear o desbloquear (con motivo)',
                    True)],
    'codigo_barras': [('asignar', 'Asignar o cambiar el código de barras de los productos', False)],
    'logistica': [('emitir', 'Emitir guías de remisión', False),
                  ('anular', 'Anular guías', True),
                  ('garita', 'Registrar ingresos y salidas en garita', False),
                  ('ver_montos', 'Ver montos de la mercadería en garita (OC, guías)', True)],
    'distribucion': [('programar', 'Programar hojas de reparto (vehículo, conductor y paradas) y cerrarlas', False),
                     ('entregar', 'Registrar las entregas en ruta (conductor)', False)],
    'flota': [('registrar', 'Registrar equipos, documentos, lecturas, combustible y viajes de la flota', False),
              ('aprobar_lecturas', 'Aprobar partes de odómetro / horómetro', True)],
    'tesoreria': [('registrar', 'Registrar cobros, pagos, depósitos de detracción y movimientos', False),
                  ('transferir', 'Transferencias entre cuentas', False),
                  ('anular', 'Anular movimientos, cheques y entregas a rendir', True),
                  ('cuentas', 'Crear cuentas de caja y bancos y sus saldos iniciales', True),
                  ('aprobar_pagos', 'Aprobar la propuesta de pagos (gerencia)', True)],
    'finanzas': [('registrar', 'Aplicar anticipos, registrar letras, canjes y cuotas de préstamos', False),
                 ('anular', 'Anular aplicaciones, canjes y préstamos', True),
                 ('cuentas', 'Registrar préstamos y leasing', True),
                 ('cobranza', 'Gestión de cobranza: gestiones, promesas, recordatorios y provisión', False),
                 ('financiamiento', 'Líneas de crédito, depósitos, factoring, cartas fianza, arrendamientos y '
                                    'forwards', True),
                 ('planeamiento', 'Presupuesto de caja: partidas manuales y escenarios', False)],
    'contabilidad': [('asientos', 'Asientos manuales', False),
                     ('extornar', 'Extornar asientos', True),
                     ('periodos', 'Centralizar y cerrar periodos', True),
                     ('reabrir', 'Reabrir periodos cerrados (con motivo)', True),
                     ('configurar', 'Plan de cuentas y configuración', True)],
    'presupuestos': [('elaborar', 'Elaborar y modificar presupuestos', True)],
    'manufactura': [('ordenes', 'Órdenes de producción, reporte de planta y paradas', False),
                    ('anular', 'Anular órdenes de producción', True),
                    ('recetas', 'Recetas, hojas de ruta, versiones, puestos y costo estándar', True),
                    ('aprobar_cambios', 'Aprobar o rechazar cambios de ingeniería', True),
                    ('consumo_adicional', 'Agregar material fuera de la receta a una orden en proceso', True),
                    ('aprobar_consumo', 'Aprobar consumos fuera de la tolerancia y material agregado a las órdenes',
                     True)],
    # v1.82: módulos separados de Manufactura
    'planificacion': [('planificar', 'Correr el MRP, pronóstico de demanda y programación de planta (genera órdenes '
                                     'y solicitudes)', False)],
    'control_calidad': [('inspecciones', 'Inspecciones, planes de calidad y certificados de análisis', False),
                        ('liberar_lotes', 'Aprobar certificados de análisis y liberar lotes en cuarentena', True),
                        ('reabrir', 'Reabrir inspecciones decididas y anular certificados aprobados (QA, con motivo)',
                         True),
                        ('aprobar_oos', 'Cerrar investigaciones de resultados fuera de especificación (OOS)', True),
                        ('especificaciones', 'Aprobar especificaciones de calidad (versiones)', True),
                        ('registros_gmp', 'Registrar desviaciones, quejas, cambios, retiros, contramuestras y '
                                          'estudios de estabilidad', False),
                        ('aprobar_qa', 'Aprobación de QA: cerrar desviaciones, aprobar y cerrar cambios y retiros',
                         True)],
    'mantenimiento': [('registrar', 'Equipos, planes preventivos, órdenes de mantenimiento y lecturas', False),
                      ('calibrar', 'Calibraciones y calificación (IQ/OQ/PQ) de equipos e instrumentos', False),
                      ('avisos', 'Aprobar o rechazar avisos de mantenimiento del operador', False)],
    'costos': [('liberar', 'Calcular y liberar el costo estándar', True),
               ('abc', 'Configurar actividades del costeo ABC', True),
               ('liquidar', 'Liquidar el costo real y registrar el valor neto realizable (cierre NIC 2)', True)],
    'planillas': [('calcular', 'Calcular planillas, gratificaciones, CTS y liquidaciones', False),
                  ('cerrar', 'Cerrar, reabrir y pagar planillas', True),
                  ('configurar', 'Parámetros, AFP y conceptos de planilla', True),
                  ('ver_sueldos', 'Ver sueldos, cuentas bancarias y netos de la planilla (RR. HH.)', True)],
    'empleados': [('registrar', 'Registrar trabajadores, contratos, legajo, asistencia y movimientos de personal', False),
                  ('aprobar_movimientos', 'Aprobar movimientos de personal (sin reglas de aprobación; también los aprueba '
                                          'el jefe del trabajador)', True),
                  ('configurar', 'Organigrama (unidades y puestos) y turnos de trabajo', True)],
    'tiempo_personal': [('registrar', 'Registrar vacaciones, permisos y licencias y ver las solicitudes de todos',
                         False)],
    'requerimientos': [('solicitar', 'Pedir materiales al almacén', False),
                       ('aprobar', 'Aprobar o rechazar requerimientos', True),
                       ('solped', 'Registrar y enviar solicitudes de pedido (compras)', False),
                       ('liberar_solped', 'Liberar, observar o rechazar solicitudes de pedido', True)],
    'rendiciones': [('rendir', 'Registrar y enviar sus rendiciones de gastos', False),
                    ('aprobar', 'Aprobar, observar o rechazar rendiciones', True),
                    ('contabilizar', 'Revisar y contabilizar rendiciones (revisor contable)', True),
                    ('tesoreria', 'Reembolsos, devoluciones y reposición de caja chica', True),
                    ('configurar', 'Categorías, políticas de gasto y reglas de aprobación', True)],
    'activos': [('registrar', 'Registrar activos', False),
                ('baja', 'Dar de baja o anular activos', True),
                ('depreciar', 'Calcular y revertir depreciación', True)],
    'obras': [('presupuestar', 'Crear obras y elaborar presupuestos', False),
              ('aprobar', 'Aprobar presupuestos de obra', True),
              ('requerir', 'Requerir materiales y servicios de la obra desde la Meta (solicitudes de pedido)', False),
              ('licitar', 'Licitaciones y propuestas (CRM de obras): datos, cronograma, requisitos y resultado', False),
              ('vender', 'Proyectos inmobiliarios: unidades, separaciones, ventas, cobros y entregas', True),
              ('reabrir', 'Reabrir obras liquidadas o cerradas (almacén y centro de costo)', True),
              ('ejecutar', 'Ejecución de obra: avance, cronograma, valorizaciones, tareo, planilla civil, partes de equipo y '
                           'subcontratos', False),
              ('facturar', 'Facturar adelantos y valorizaciones de obra, reclasificar facturas a anticipo y cobrar el fondo '
                           'de garantía', True)],
    'proyectos': [('gestionar', 'Crear proyectos y elegir a sus miembros (jefe de proyecto)', False),
                  ('ver_todos', 'Ver y editar todos los proyectos (gerencia / oficina de proyectos)', True)],
    'sst': [('registrar', 'Registrar exámenes, inspecciones, capacitaciones, IPERC, EPP, permisos y accidentes', False),
            ('validar', 'Validar registros de SST (especialista: jefe o supervisor de SST, médico ocupacional, comité)',
             True),
            ('configurar', 'Reglas de validación por especialista', True)],
    'calidad': [('registrar', 'Registrar no conformidades, acciones, auditorías, documentos, indicadores y sugerencias',
                 False),
                ('validar', 'Validar o aprobar registros de calidad (auditor, responsable del sistema de gestión)',
                 True)],
}
TODAS = {f'{m}.{a}' for m, lista in ACCIONES.items() for a, _, _ in lista}
# acciones sensibles nuevas: nunca se dan por defecto (ni a usuarios sin perfil); el administrador las asigna
EXPLICITAS = {'requerimientos.aprobar', 'contabilidad.reabrir', 'costos.liberar', 'manufactura.aprobar_cambios',
              'costos.liquidar', 'requerimientos.liberar_solped', 'inventario.aprobar_mayor', 'obras.aprobar',
              'obras.reabrir', 'sst.validar', 'calidad.validar', 'obras.facturar',
              'inventario.fecha_anterior', 'proyectos.ver_todos',
              # v1.84: aprobaciones de QA (BPM): solo a quien se le asignan
              'control_calidad.reabrir', 'control_calidad.aprobar_oos', 'control_calidad.especificaciones',
              'control_calidad.aprobar_qa', 'tesoreria.aprobar_pagos',
              # v1.88 (SEG-08): sueldos, cuentas y netos de planilla solo a RR. HH.
              'planillas.ver_sueldos'}

# ruta -> acción exigida. Valor str = siempre; dict = según el método o un dato de la petición (función)
RUTAS = {
    'ventas:nuevo': lambda r: 'ventas.notas' if _tipo(r) in ('07', '08') else 'ventas.emitir',
    'ventas:editar': 'ventas.emitir', 'ventas:anular': 'ventas.anular', 'ventas:reactivar': 'ventas.reactivar', 'ventas:eliminar': 'ventas.anular',
    'ventas:importar': 'ventas.emitir', 'ventas:notas': 'ventas.notas', 'ventas:trasladar': 'ventas.emitir',
    'ventas:lista_precios_nueva': 'ventas.precios', 'ventas:lista_precios_editar': 'ventas.precios',
    'ventas:pos': {'POST': 'ventas.emitir'},
    'crm:nueva': 'crm.registrar', 'crm:oportunidad': {'POST': 'crm.registrar'}, 'crm:etapa': 'crm.registrar',
    'crm:convertir': {'POST': 'crm.registrar'}, 'crm:whatsapp': 'crm.registrar',
    'crm:actividad_hecha': 'crm.registrar', 'crm:visitas': {'POST': 'crm.registrar'},
    'crm:movil_sincronizar': 'crm.registrar', 'crm:cliente': {'POST': 'crm.registrar'},
    'crm:contratos': {'POST': 'crm.postventa'}, 'crm:contrato': {'POST': 'crm.postventa'},
    'crm:contrato_nuevo': {'POST': 'crm.postventa'}, 'crm:caso': {'POST': 'crm.postventa'},
    'crm:caso_nuevo': {'POST': 'crm.postventa'}, 'crm:encuesta_nueva': {'POST': 'crm.postventa'},
    'crm:campana': {'POST': 'crm.configurar'}, 'crm:campana_nueva': {'POST': 'crm.configurar'},
    'crm:meta': {'POST': 'crm.configurar'}, 'crm:meta_nueva': {'POST': 'crm.configurar'},
    'crm:equipo': {'POST': 'crm.configurar'}, 'crm:equipo_nuevo': {'POST': 'crm.configurar'},
    'crm:configuracion': 'crm.configurar', 'crm:reasignaciones': 'crm.ver_todas',
    'crm:datos_personales': {'POST': 'crm.datos_personales'},
    'ventas:cot_nuevo': 'ventas.emitir','ventas:cot_editar': 'ventas.emitir', 'ventas:cot_estado': 'ventas.emitir',
    'compras:nuevo': 'compras.registrar', 'compras:editar': 'compras.registrar', 'compras:importar': 'compras.registrar',
    'compras:importar_xml': 'compras.registrar', 'compras:importar_xml_revisar': 'compras.registrar',
    'compras:trasladar': 'compras.registrar', 'compras:notas': 'compras.registrar',
    'compras:ingresar_almacen': 'compras.registrar',
    'compras:importacion_nueva': 'compras.registrar', 'compras:importacion': {'POST': 'compras.registrar'},
    'compras:licitacion_nueva': 'compras.oc',
    'compras:licitacion': lambda r: (None if r.method != 'POST' else 'compras.aprobar_oc'
                                     if r.POST.get('accion') == 'adjudicar' else 'compras.oc'),
    'compras:contrato_nuevo': 'compras.aprobar_oc', 'compras:contrato_editar': 'compras.aprobar_oc',
    'compras:distribuir': {'POST': 'compras.registrar'},
    'compras:registro_info_nuevo': {'POST': 'compras.oc'}, 'compras:registro_info': {'POST': 'compras.oc'},
    'compras:registros_info': {'POST': 'compras.oc'},
    'compras:anular': 'compras.anular', 'compras:eliminar': 'compras.anular',
    'compras:oc_nuevo': 'compras.oc', 'compras:oc_editar': 'compras.oc', 'compras:oc_enviar': 'compras.oc',
    'compras:oc_estado': lambda r: {'APROBADO': _aprobar_oc(), 'ANULADO': 'compras.anular'}.get(
        r.POST.get('estado'), 'compras.oc'),
    'compras:portal_factura_aprobar': 'portal_proveedores.aprobar',
    'compras:portal_factura_rechazar': 'portal_proveedores.aprobar', 'compras:liberar_pago': 'portal_proveedores.aprobar',
    'compras:portal_acceso_nuevo': 'portal_proveedores.aprobar',
    'compras:portal_acceso_editar': 'portal_proveedores.aprobar',
    'inventario:nueva': 'inventario.operar', 'inventario:editar': 'inventario.operar',
    'inventario:confirmar': 'inventario.operar', 'inventario:pendientes': 'inventario.operar',
    'inventario:eliminar': 'inventario.operar', 'inventario:anular': 'inventario.anular',
    'inventario:cierre_reabrir': 'inventario.cerrar', 'inventario:tipo_nuevo': 'inventario.cerrar',
    'inventario:tipo_editar': 'inventario.cerrar', 'inv_ajuste': 'inventario.ajustar',
    'inventario:cierres': {'POST': 'inventario.cerrar'}, 'ubicaciones': {'POST': 'inventario.operar'},
    'inventario:ola_nueva': {'POST': 'inventario.operar'}, 'inventario:ola': {'POST': 'inventario.operar'},
    'inventario:ciclico': {'POST': 'inventario.operar'}, 'inventario:conteo': {'POST': 'inventario.ajustar'},
    'inventario:fisico_nuevo': {'POST': 'inventario.operar'},
    'logistica:nueva': 'logistica.emitir', 'logistica:editar': 'logistica.emitir',
    'logistica:enviar_sunat': 'logistica.emitir', 'logistica:anular': 'logistica.anular',
    'logistica:garita_nuevo': 'logistica.garita', 'logistica:garita': {'POST': 'logistica.garita'},
    # tesorería (pantallas de la app finanzas que son del módulo Tesorería: core/modulos.py RUTAS_FINANZAS)
    'finanzas:movimiento_nuevo': 'tesoreria.registrar', 'finanzas:cobranza': 'tesoreria.registrar',
    'finanzas:pago': 'tesoreria.registrar', 'finanzas:importar': 'tesoreria.registrar',
    'finanzas:cobranza_multiple': {'POST': 'tesoreria.registrar'}, 'finanzas:pago_multiple': {'POST': 'tesoreria.registrar'},
    'finanzas:extractos': {'POST': 'tesoreria.registrar'}, 'finanzas:extracto_accion': 'tesoreria.registrar',
    'finanzas:transferencia': 'tesoreria.transferir', 'finanzas:movimiento_eliminar': 'tesoreria.anular',
    'finanzas:cuenta_nueva': 'tesoreria.cuentas', 'finanzas:cuenta_editar': 'tesoreria.cuentas',
    'finanzas:cheques': {'POST': 'tesoreria.registrar'},
    'finanzas:cheque_estado': lambda r: 'tesoreria.registrar' if r.POST.get('estado') == 'COBRADO' else 'tesoreria.anular',
    'finanzas:entrega_nueva': 'tesoreria.registrar', 'finanzas:entrega': {'POST': 'tesoreria.registrar'},
    'finanzas:pago_masivo_nuevo': {'POST': 'tesoreria.registrar'}, 'finanzas:pago_masivo': {'POST': 'tesoreria.registrar'},
    'finanzas:detracciones_masivas_nuevo': {'POST': 'tesoreria.registrar'},
    'finanzas:detraccion_masiva': {'POST': 'tesoreria.registrar'},
    'finanzas:constancias_clientes': {'POST': 'tesoreria.registrar'},
    'ventas:deposito_detraccion': {'POST': 'tesoreria.registrar'},
    'finanzas:saldo_inicial': {'POST': 'tesoreria.cuentas'}, 'finanzas:saldo_inicial_sustento': 'tesoreria.cuentas',
    # finanzas: anticipos, letras, canjes, préstamos y leasing
    'finanzas:anticipo_aplicar': {'POST': 'finanzas.registrar'},
    'finanzas:aplicacion_anular': lambda r: _aplicacion(r),
    'finanzas:canje_nuevo': {'POST': 'finanzas.registrar'},
    'finanzas:canje': {'POST': 'finanzas.anular'}, 'finanzas:letra': {'POST': 'finanzas.registrar'},
    'finanzas:prestamo_nuevo': {'POST': 'finanzas.cuentas'},
    'finanzas:prestamo': lambda r: (None if r.method != 'POST' else 'finanzas.anular'
                                    if r.POST.get('accion') == 'anular' else 'finanzas.cuentas'
                                    if r.POST.get('accion') in ('prepagar', 'reprogramar') else 'finanzas.registrar'),
    # v1.84: planeamiento, cobranza, propuesta de pagos, financiamiento e indicadores (FIN-01 a FIN-08)
    'finanzas:presupuesto_caja': {'POST': 'finanzas.planeamiento'},
    'finanzas:cobranza_gestion': {'POST': 'finanzas.cobranza'}, 'finanzas:cobranza_cliente': {'POST': 'finanzas.cobranza'},
    'finanzas:recordatorios': {'POST': 'finanzas.cobranza'}, 'finanzas:provision': {'POST': 'contabilidad.asientos'},
    'finanzas:propuesta_nueva': {'POST': 'tesoreria.registrar'},
    'finanzas:propuesta': lambda r: (None if r.method != 'POST' else 'tesoreria.aprobar_pagos'
                                     if r.POST.get('accion') in ('aprobar', 'rechazar') else 'tesoreria.registrar'),
    'finanzas:lineas_credito': {'POST': 'finanzas.financiamiento'},
    'finanzas:depositos': {'POST': 'finanzas.financiamiento'}, 'finanzas:cesiones': {'POST': 'finanzas.financiamiento'},
    'finanzas:cartas_fianza': {'POST': 'finanzas.financiamiento'},
    'finanzas:arrendamientos': {'POST': 'finanzas.financiamiento'},
    'finanzas:prestamos_cierre': {'POST': 'finanzas.cuentas'}, 'finanzas:forwards': {'POST': 'finanzas.financiamiento'},
    'contabilidad:asiento_nuevo': 'contabilidad.asientos', 'contabilidad:asiento_editar': 'contabilidad.asientos',
    'contabilidad:asiento_eliminar': 'contabilidad.extornar', 'contabilidad:asiento_extornar': 'contabilidad.extornar',
    'contabilidad:periodos': lambda r: (None if r.method != 'POST' else 'contabilidad.reabrir'
                                        if r.POST.get('accion') == 'abrir' else 'contabilidad.periodos'),
    'contabilidad:cuenta_nueva': 'contabilidad.configurar', 'contabilidad:cuenta_editar': 'contabilidad.configurar',
    'contabilidad:cierre': {'POST': 'contabilidad.periodos'},
    'contabilidad:configuracion': {'POST': 'contabilidad.configurar'},
    'contabilidad:cuentas_tipo': {'POST': 'contabilidad.configurar'},
    'contabilidad:cc_nuevo': 'contabilidad.configurar', 'contabilidad:cc_editar': 'contabilidad.configurar',
    'planillas:nueva': 'planillas.calcular', 'planillas:trabajador_nuevo': 'empleados.registrar',
    'planillas:trabajador_editar': {'POST': 'empleados.registrar'},
    'planillas:detalle': lambda r: (None if r.method != 'POST' else 'planillas.cerrar'
                                    if r.POST.get('accion') in ('cerrar', 'reabrir', 'pagar') else 'planillas.calcular'),
    'planillas:configuracion': {'POST': 'planillas.configurar'},
    'planillas:movimiento_nuevo': 'empleados.registrar',
    'planillas:vacaciones': {'POST': 'tiempo_personal.registrar'},
    # la asistencia la registra Empleados; pasarla a la planilla del mes es de Planillas
    'planillas:asistencia': lambda r: (None if r.method != 'POST' else 'planillas.calcular'
                                       if r.POST.get('accion') == 'planilla' else 'empleados.registrar'),
    'planillas:turnos': {'POST': 'empleados.configurar'}, 'planillas:turno': {'POST': 'empleados.configurar'},
    'contabilidad:presupuesto_nuevo': 'presupuestos.elaborar',
    'contabilidad:presupuesto_editar': 'presupuestos.elaborar',
    'contabilidad:presupuesto_generar': 'presupuestos.elaborar',
    'contabilidad:cb_nuevo': 'contabilidad.configurar', 'contabilidad:cb_editar': 'contabilidad.configurar',
    'contabilidad:reparto': {'POST': 'contabilidad.periodos'},
    'contabilidad:reparto_regla_nueva': 'contabilidad.configurar', 'contabilidad:reparto_regla': 'contabilidad.configurar',
    'contabilidad:reparto_cifras': {'POST': 'contabilidad.configurar'},
    'manufactura:orden_nueva': 'manufactura.ordenes', 'manufactura:orden_editar': 'manufactura.ordenes',
    'manufactura:orden_confirmar': 'manufactura.ordenes', 'manufactura:orden_iniciar': 'manufactura.ordenes',
    'manufactura:orden_terminar': 'manufactura.ordenes', 'manufactura:orden_maquila': 'manufactura.ordenes',
    'manufactura:orden_declarar': 'manufactura.ordenes', 'manufactura:declaracion_anular': 'manufactura.ordenes',
    'manufactura:orden_cierre_tecnico': 'manufactura.ordenes', 'manufactura:orden_liquidar': 'manufactura.ordenes',
    'manufactura:orden_costo_tardio': 'manufactura.ordenes',
    'manufactura:orden_material': 'manufactura.consumo_adicional',
    'manufactura:orden_consumo_resolver': 'manufactura.aprobar_consumo',
    'manufactura:inspeccion_nueva': 'control_calidad.inspecciones',
    # v1.84: calidad farmacéutica (las aprobaciones de QA las valida cada servicio con su firma)
    **{f'manufactura:{n}': 'control_calidad.registros_gmp' for n in (
        'desviacion_nueva', 'cambio_cc_nuevo', 'queja_nueva', 'retiro_nuevo', 'contramuestra_nueva', 'estudio_nuevo')},
    **{f'manufactura:{n}': {'POST': 'control_calidad.registros_gmp'} for n in (
        'desviacion', 'cambio_cc', 'queja', 'retiro', 'contramuestra', 'estudio')},
    'manufactura:oos': {'POST': 'control_calidad.inspecciones'},
    'manufactura:registro_lote': {'POST': 'manufactura.ordenes'},
    # calibraciones: las registra Mantenimiento («calibrar») o Calidad («inspecciones»)
    'manufactura:calibracion_nueva': lambda r: _calibrar(r), 'manufactura:calificacion_nueva': lambda r: _calibrar(r),
    'manufactura:inspeccion': {'POST': 'control_calidad.inspecciones'},
    'manufactura:plan_calidad': {'POST': 'control_calidad.inspecciones'},
    'manufactura:certificado_nuevo': 'control_calidad.inspecciones',
    'manufactura:certificado': {'POST': 'control_calidad.liberar_lotes'},
    'manufactura:equipo_nuevo': {'POST': 'mantenimiento.registrar'},
    'manufactura:equipo': {'POST': 'mantenimiento.registrar'},
    'flota:equipo_nuevo': lambda r: _flota(r), 'flota:equipo': lambda r: _flota(r),
    'flota:documentos': lambda r: _flota(r), 'flota:parte': lambda r: _flota(r),
    'flota:lecturas': {'POST': 'flota.aprobar_lecturas'},
    'flota:combustible': lambda r: _flota(r), 'flota:viajes': lambda r: _flota(r),
    'flota:prestamos': lambda r: _flota(r), 'flota:prestamo': lambda r: _flota(r),
    'manufactura:ordenes_mant': {'POST': 'mantenimiento.registrar'},
    'manufactura:orden_mant_nueva': 'mantenimiento.registrar',
    'manufactura:orden_mant': {'POST': 'mantenimiento.registrar'},
    # v1.84: avisar una falla lo puede cualquiera del módulo; aprobarla, quien tenga «avisos»
    'manufactura:avisos_mant': {'POST': 'mantenimiento.avisos'},
    'manufactura:lecturas_mant': {'POST': 'mantenimiento.registrar'},
    'manufactura:plan_mant': {'POST': 'mantenimiento.registrar'},
    'manufactura:ubicaciones_mant': {'POST': 'mantenimiento.registrar'},
    'manufactura:codigos_falla': {'POST': 'mantenimiento.registrar'},
    'manufactura:repuestos_criticos': {'POST': 'mantenimiento.registrar'},
    'manufactura:calendario_mant': {'POST': 'mantenimiento.registrar'},
'manufactura:orden_eliminar': 'manufactura.ordenes',
    'manufactura:orden_anular': 'manufactura.anular',
    'manufactura:lista_nueva': 'manufactura.recetas', 'manufactura:lista_editar': 'manufactura.recetas',
    'manufactura:lista_version': 'manufactura.recetas', 'manufactura:centro_nuevo': 'manufactura.recetas',
    'manufactura:centro_editar': 'manufactura.recetas', 'manufactura:hoja_nueva': 'manufactura.recetas',
    'manufactura:hoja_editar': 'manufactura.recetas', 'manufactura:hoja_obsoleta': 'manufactura.recetas',
    'manufactura:lista_obsoleta': 'manufactura.recetas', 'manufactura:version_nueva': 'manufactura.recetas',
    'manufactura:version_editar': 'manufactura.recetas', 'manufactura:mrp': {'POST': 'planificacion.planificar'},
    'manufactura:pronostico': {'POST': 'planificacion.planificar'},
    'costos:estandar': {'POST': 'costos.liberar'},
    'costos:liquidacion': {'POST': 'costos.liquidar'}, 'costos:vnr': {'POST': 'costos.liquidar'},
    'costos:comportamiento': {'POST': 'costos.liquidar'},
    'costos:actividad_nueva': {'POST': 'costos.abc'}, 'costos:actividad': {'POST': 'costos.abc'},
    'manufactura:programacion': {'POST': 'planificacion.planificar'},
    'manufactura:planta_orden': {'POST': 'manufactura.ordenes'},
    'manufactura:paradas': {'POST': 'manufactura.ordenes'},
    'manufactura:motivos_parada': {'POST': 'manufactura.recetas'},  # maestro de Ingeniería
    'manufactura:motivo_parada_nuevo': 'manufactura.recetas', 'manufactura:motivo_parada_editar': 'manufactura.recetas',
    'manufactura:cambio_nuevo': 'manufactura.recetas',
    'manufactura:cambio': lambda r: (None if r.method != 'POST' else 'manufactura.aprobar_cambios'
                                     if r.POST.get('accion') in ('aprobar', 'rechazar') else 'manufactura.recetas'),
    'requerimientos:nuevo': 'requerimientos.solicitar', 'requerimientos:editar': 'requerimientos.solicitar',
    'requerimientos:enviar': 'requerimientos.solicitar',
    'solicitudes:nueva': 'requerimientos.solped', 'solicitudes:editar': 'requerimientos.solped',
    'solicitudes:enviar': 'requerimientos.solped',
    'compras:solped_bandeja': {'POST': 'compras.convertir_solped'},
    'compras:solped_reglas': {'POST': 'compras.aprobar_oc'}, 'compras:conformidad': {'POST': 'compras.hes'},
    'compras:portal_tablero': 'portal_proveedores.aprobar',
    # v1.83: módulos nuevos
    'toma_pedido:nuevo': 'toma_pedido.registrar',
    'codigo_barras:asignar': {'POST': 'codigo_barras.asignar'},
    'distribucion:nueva': 'distribucion.programar',
    'distribucion:hoja': {'POST': 'distribucion.programar'},
    'distribucion:movil': {'POST': 'distribucion.entregar'},
    'proyectos:nuevo': 'proyectos.gestionar', 'proyectos:editar': 'proyectos.gestionar',
    'compras:grupo_nuevo': 'compras.grupos', 'compras:grupo': 'compras.grupos',
    'compras:hes': lambda r: (None if r.method != 'POST' else 'compras.hes'
                              if r.POST.get('accion') in ('adjuntar', 'enviar', 'anular') else None),
    'obras:obra_nueva': 'obras.presupuestar', 'obras:obra_editar': 'obras.presupuestar',
    'obras:presupuesto_nuevo': 'obras.presupuestar', 'obras:importar': {'POST': 'obras.presupuestar'},
    'obras:presupuesto': lambda r: (None if r.method != 'POST' else 'obras.aprobar'
                                    if r.POST.get('accion') == 'aprobar' else 'obras.presupuestar'),
    'obras:apu': {'POST': 'obras.presupuestar'}, 'obras:precios': {'POST': 'obras.presupuestar'},
    'obras:recurso_nuevo': 'obras.presupuestar', 'obras:recurso_editar': 'obras.presupuestar',
    'obras:biblioteca': {'POST': 'obras.presupuestar'},
    'obras:subanalisis': {'POST': 'obras.presupuestar'}, 'obras:vincular': {'POST': 'obras.presupuestar'},
    'obras:control': {'POST': 'obras.requerir'},
    'obras:licitacion_nueva': 'obras.licitar', 'obras:desde_oportunidad': 'obras.licitar',
    'obras:licitacion': {'POST': 'obras.licitar'},
    'obras:unidades': {'POST': 'obras.vender'}, 'obras:separar': 'obras.vender',
    'obras:venta_inmueble': {'POST': 'obras.vender'}, 'obras:inm_cobrar': 'obras.vender',
    'obras:adelanto': 'obras.facturar', 'obras:reclasificar': {'POST': 'obras.facturar'},
    'obras:garantia_cobrar': 'obras.facturar', 'obras:valorizacion_nueva': 'obras.ejecutar',
    'obras:valorizacion': {'POST': 'obras.ejecutar'}, 'obras:formula': {'POST': 'obras.ejecutar'},
    'obras:indices': {'POST': 'obras.ejecutar'}, 'obras:cronograma': {'POST': 'obras.ejecutar'},
    'obras:avance': {'POST': 'obras.ejecutar'}, 'obras:avance_anular': 'obras.ejecutar',
    'obras:entregables': {'POST': 'obras.ejecutar'}, 'obras:partes_horas': {'POST': 'obras.ejecutar'},
    'obras:garantia': {'POST': 'obras.ejecutar'}, 'obras:garantia_nueva': {'POST': 'obras.ejecutar'},
    'obras:servicios_proceso': {'POST': 'obras.facturar'}, 'obras:versiones': {'POST': 'obras.presupuestar'},
    'obras:presupuesto_proyecto': {'POST': 'obras.presupuestar'},
    'obras:tareo': {'POST': 'obras.ejecutar'}, 'obras:planillas_civil': {'POST': 'obras.ejecutar'},
    'obras:planilla_civil': {'POST': 'obras.ejecutar'}, 'obras:partes': {'POST': 'obras.ejecutar'},
    'obras:subcontratos': {'POST': 'obras.ejecutar'}, 'obras:subcontrato': {'POST': 'obras.ejecutar'},
    # v1.86: la adenda la registra la ejecución y la aprueba (o anula) quien aprueba presupuestos
    'obras:adendas': lambda r: (None if r.method != 'POST' else 'obras.aprobar'
                                if r.POST.get('accion') in ('aprobar', 'anular') else 'obras.ejecutar'),
    'obras:eventos': {'POST': 'obras.ejecutar'}, 'obras:escenarios': {'POST': 'obras.ejecutar'},
    'obras:subcontrato_evaluar': 'obras.ejecutar', 'obras:consorcio': {'POST': 'obras.licitar'},
    'sst:registro_nuevo': 'sst.registrar', 'sst:registro': {'POST': 'sst.registrar'},
    'sst:indices': {'POST': 'sst.registrar'},
    'calidad:registro_nuevo': 'calidad.registrar', 'calidad:registro': {'POST': 'calidad.registrar'},
    'activos:nuevo': 'activos.registrar', 'activos:editar': 'activos.registrar', 'activos:baja': 'activos.baja',
    'activos:anular': 'activos.baja', 'activos:depreciacion': {'POST': 'activos.depreciar'},
    'activos:proceso_revertir': 'activos.depreciar', 'activos:categoria_nueva': 'activos.depreciar',
    'activos:categoria_editar': 'activos.depreciar',
    'rendiciones:nueva': 'rendiciones.rendir',
    'rendiciones:pagos': {'POST': 'rendiciones.tesoreria'}, 'rendiciones:cajas': {'POST': 'rendiciones.tesoreria'},
    'rendiciones:config_nueva': 'rendiciones.configurar', 'rendiciones:config_editar': 'rendiciones.configurar',
    'rendiciones:configuracion': 'rendiciones.configurar', 'rendiciones:integraciones': 'rendiciones.configurar',
    'rendiciones:tarjeta_nueva': 'rendiciones.configurar', 'rendiciones:tarjeta_editar': 'rendiciones.configurar',
    'rendiciones:tarjeta': {'POST': 'rendiciones.tesoreria'},
    'rendiciones:proyecto_nuevo': 'rendiciones.configurar', 'rendiciones:proyecto_editar': 'rendiciones.configurar',
    'rendiciones:sitio_nuevo': 'rendiciones.configurar', 'rendiciones:sitio_editar': 'rendiciones.configurar',
    'rendiciones:movil_gasto': 'rendiciones.rendir', 'rendiciones:movil_nueva': 'rendiciones.rendir',
    'rendiciones:integracion': {'POST': 'rendiciones.contabilizar'},
}
CAMPOS_ALMACEN = ('almacen', 'almacen_origen', 'almacen_destino', 'almacen_insumos')


def _aprobar_oc():
    """Con reglas de aprobación de OC, la vista valida el nivel (el aprobador puede no tener el permiso general)."""
    from .aprobaciones import hay_reglas
    return None if hay_reglas('OC') else 'compras.aprobar_oc'


def _tipo(request):
    return request.POST.get('tipo_comprobante') or request.GET.get('tipo') or ''


def _calibrar(request):
    return None if puede(request.user, 'control_calidad.inspecciones') else 'mantenimiento.calibrar'


def _flota(request):
    from flota.views import accion_flota
    return accion_flota(request)


def _aplicacion(request):
    """Anular la aplicación de una entrega a rendir es de Tesorería; la de un anticipo o un canje, de Finanzas."""
    from finanzas.models import Aplicacion
    pk = request.resolver_match.kwargs.get('pk')
    entrega = Aplicacion.objects.filter(pk=pk, entrega__isnull=False).exists()
    return 'tesoreria.anular' if entrega else 'finanzas.anular'


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
    if hasattr(user, 'acceso_proveedor'):  # usuario del portal de proveedores: ninguna acción del ERP
        return False
    from .modulos import modulos_del_usuario
    if accion.split('.')[0] not in modulos_del_usuario(user):
        return False
    perfil = perfil_de(user)
    if perfil is None:
        return accion not in EXPLICITAS
    return accion in perfil.acciones


def rutas_sin_permiso(user):
    """Pantallas que siempre exigen una acción que el usuario no tiene (SEG-07): el menú no las muestra (el servidor
    igual las rechaza)."""
    if not user.is_authenticated or user.is_superuser:
        return set()
    cache = getattr(user, '_rutas_sin_permiso', None)
    if cache is None:
        cache = user._rutas_sin_permiso = {r for r, regla in RUTAS.items()
                                          if isinstance(regla, str) and not puede(user, regla)}
    return cache


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


def almacenes_visibles(user, elegidos=None):
    """Almacenes que el usuario consulta en reportes: los elegidos que tiene permitidos o, si no eligió (o eligió
    solo ajenos), todos los suyos. None = sin restricción y sin elección (todos los almacenes)."""
    permitidos = almacenes_permitidos(user)
    elegidos = [int(a) for a in (elegidos or [])]
    if permitidos is None:
        return elegidos or None
    return [a for a in elegidos if a in permitidos] or sorted(permitidos)


def series_permitidas(user, tipo=None):
    """Series (texto) que emite el usuario, opcionalmente de un tipo; None = todas."""
    perfil = perfil_de(user) if user is not None else None
    todas = list(perfil.series.all()) if perfil is not None else []
    if not todas:
        return None
    return [s.serie for s in todas if tipo is None or s.tipo == tipo]


# documentos existentes cuyas acciones (confirmar, anular, editar) se limitan a los almacenes del usuario:
# rutas cuyo pk es el documento del módulo (las demás rutas con pk son de otros modelos: olas, conteos, CRM…)
_COMPROBANTE = ('detalle', 'editar', 'imprimir', 'anular', 'eliminar', 'trasladar')
RUTAS_DOCUMENTO = {
    'inventario': ('detalle', 'editar', 'pendientes', 'confirmar', 'anular', 'eliminar', 'imprimir', 'conformidad'),
    'manufactura': ('orden', 'orden_editar', 'orden_confirmar', 'orden_iniciar', 'orden_maquila', 'orden_terminar',
                    'orden_anular', 'orden_eliminar', 'planta_orden', 'orden_declarar', 'orden_cierre_tecnico',
                    'orden_liquidar', 'orden_costo_tardio'),
    'logistica': ('detalle', 'editar', 'imprimir', 'anular', 'enviar_sunat'),
    'ventas': _COMPROBANTE + ('enviar_sunat', 'enviar_correo', 'whatsapp'),
    'compras': _COMPROBANTE + ('ingresar_almacen',),
}


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
    # las rutas de órdenes de compra, cotizaciones, portal, olas, conteos y demás usan otros modelos
    if modelo is None or match.url_name not in RUTAS_DOCUMENTO.get(match.namespace, ()):
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
        # el módulo puede llevar guion bajo (control_calidad_inspecciones): el nombre de módulo más largo que calce
        modulo = next((m for m in _MODULOS_POR_LARGO if nombre.startswith(m + '_')), None)
        if modulo is None:
            raise AttributeError(nombre)
        return puede(self.user, f'{modulo}.{nombre[len(modulo) + 1:]}')


_MODULOS_POR_LARGO = sorted(ACCIONES, key=len, reverse=True)
