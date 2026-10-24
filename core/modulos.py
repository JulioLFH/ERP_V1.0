"""Registro de módulos (aplicaciones) al estilo Odoo: menú propio y permiso por módulo.

Cada módulo corresponde a un grupo de Django con el mismo nombre. El superusuario ve todo.
Para un usuario normal, el middleware AccesoModulosMiddleware bloquea las pantallas de módulos
que no tiene asignados.
"""
from django.urls import reverse

# Menú: lista de (etiqueta, url) para enlace directo o (etiqueta, [(etiqueta, url, icono), ...]) para desplegable.
# url = nombre de ruta, opcionalmente con querystring: 'logistica:lista?tipo=09'
MODULOS = [
    {
        'clave': 'tablero', 'nombre': 'Tablero', 'icono': 'bi-speedometer2', 'color': '#4c6ef5',
        'inicio': 'dashboard', 'descripcion': 'Indicadores de la empresa',
        'menu': [],
    },
    {
        'clave': 'crm', 'nombre': 'CRM', 'icono': 'bi-kanban', 'color': '#c2255c',
        'inicio': 'crm:embudo', 'descripcion': 'Oportunidades, embudo comercial y seguimiento de clientes',
        'menu': [
            ('Embudo de oportunidades', 'crm:embudo'),
            ('Oportunidades', [('Todas las oportunidades', 'crm:lista', 'bi-list-stars'),
                               ('Mis oportunidades', 'crm:lista?mias=1', 'bi-person-check'),
                               ('Nueva oportunidad', 'crm:nueva', 'bi-plus-circle')]),
            ('Clientes', 'terceros?tipo=CLIENTE'),
        ],
    },
    {
        'clave': 'ventas', 'nombre': 'Ventas', 'icono': 'bi-receipt', 'color': '#2f9e44',
        'inicio': 'ventas:lista', 'descripcion': 'Cotizaciones, comprobantes y cobranzas',
        'menu': [
            ('Punto de venta', 'ventas:pos'),
            ('Ventas', [('Cotizaciones y pedidos', 'ventas:cot_lista', 'bi-file-earmark-text'),
                        ('Comprobantes', 'ventas:lista', 'bi-receipt'),
                        ('Notas de crédito / débito', 'ventas:notas', 'bi-file-earmark-diff'),
                        ('Emitir comprobante', 'ventas:nuevo', 'bi-plus-circle'),
                        ('Listas de precios', 'ventas:listas_precios', 'bi-tags')]),
            ('Cuentas por cobrar', 'ventas:pendientes'),
            ('Clientes', 'terceros?tipo=CLIENTE'),
            ('Productos', 'productos'),
            ('Reportes', [('Registro de ventas 14.1 / PLE', 'ventas:registro', 'bi-journal-text'),
                          ('Detracciones de ventas', 'ventas:detracciones', 'bi-bank'),
                          ('SIRE: propuesta de SUNAT (RVIE)', 'sire_propuestas?libro=RVIE', 'bi-cloud-download'),
                          ('Reportes de ventas', 'ventas:reportes', 'bi-graph-up'),
                          ('Importar desde Excel', 'ventas:importar', 'bi-file-earmark-arrow-up'),
                          ('Cargar listas de precios (Excel)', 'carga_masiva?tipo=listas_precios',
                           'bi-tags')]),
        ],
    },
    {
        'clave': 'compras', 'nombre': 'Compras', 'icono': 'bi-bag', 'color': '#e8590c',
        'inicio': 'compras:lista', 'descripcion': 'Órdenes de compra, facturas y pagos',
        'menu': [
            ('Solicitudes de pedido', [('Bandeja de compras', 'compras:solped_bandeja', 'bi-inbox'),
                                       ('Mis líneas por pedir', 'compras:solped_bandeja?comprador=mias',
                                        'bi-person-check'),
                                       ('Traslados entre almacenes', 'compras:traslados', 'bi-arrow-left-right'),
                                       ('Grupos de compras y compradores', 'compras:grupos', 'bi-people'),
                                       ('Todas las solicitudes', 'solicitudes:lista?vista=todas', 'bi-cart'),
                                       ('Nueva solicitud', 'solicitudes:nueva', 'bi-plus-circle'),
                                       ('Por liberar', 'solicitudes:lista?vista=liberar', 'bi-check2-circle'),
                                       ('Reglas de aprobación (OC y solicitudes)', 'aprobaciones_reglas?documento=OC',
                                        'bi-diagram-3')]),
            ('Compras', [('Órdenes de compra', 'compras:oc_lista', 'bi-clipboard-check'),
                         ('Conformidades de servicio (HES)', 'compras:hes_lista', 'bi-patch-check'),
                         ('Registro de compras', 'compras:lista', 'bi-bag'),
                         ('Notas de crédito / débito', 'compras:notas', 'bi-file-earmark-diff'),
                         ('Registrar compra', 'compras:nuevo', 'bi-plus-circle'),
                         ('Registrar desde XML', 'compras:importar_xml', 'bi-filetype-xml'),
                         ('Importaciones y costeo', 'compras:importaciones', 'bi-globe-americas')]),
            ('Abastecimiento', [('Licitaciones y cuadro comparativo', 'compras:licitaciones', 'bi-trophy'),
                                ('Nueva licitación', 'compras:licitacion_nueva', 'bi-plus-circle'),
                                ('Contratos marco', 'compras:contratos', 'bi-file-earmark-text'),
                                ('Proveedores por producto (registro info)', 'compras:registros_info',
                                 'bi-person-lines-fill')]),
            ('Portal de proveedores', [('Tablero del portal', 'compras:portal_tablero', 'bi-speedometer2'),
                                       ('Facturas por revisar', 'compras:portal_facturas', 'bi-inbox'),
                                       ('Accesos de proveedores', 'compras:portal_accesos', 'bi-person-lock')]),
            ('Cuentas por pagar', 'compras:pendientes'),
            ('Sugerencia de compra', 'inv_reposicion'),
            ('Proveedores', 'terceros?tipo=PROVEEDOR'),
            ('Productos', 'productos'),
            ('Reportes', [('Registro de compras 8.1 / PLE', 'compras:registro', 'bi-journal-text'),
                          ('Detracciones de compras', 'compras:detracciones', 'bi-bank'),
                          ('Reportes de compras', 'compras:reportes', 'bi-bar-chart'),
                          ('Historial de precios de compra', 'compras:historial_precios', 'bi-graph-up'),
                          ('Solicitudes de pedido: estado y tiempos', 'compras:solped_reportes', 'bi-hourglass-split'),
                          ('Desempeño de compradores', 'compras:desempeno', 'bi-speedometer2'),
                          ('SIRE: propuesta de SUNAT (RCE)', 'sire_propuestas?libro=RCE', 'bi-cloud-download'),
                          ('Cruce con propuesta SIRE', 'compras:sire', 'bi-arrow-left-right'),
                          ('Importar desde Excel', 'compras:importar', 'bi-file-earmark-arrow-up')]),
        ],
    },
    {
        'clave': 'inventario', 'nombre': 'Inventario', 'icono': 'bi-boxes', 'color': '#ae3ec9',
        'inicio': 'inv_stock', 'descripcion': 'Stock, almacenes, kardex y valorización',
        'menu': [
            ('Stock por almacén', 'inv_stock'),
            ('Operaciones', [('Nueva operación', 'inventario:nueva', 'bi-plus-circle'),
                             ('Recepciones e ingresos', 'inventario:lista?grupo=recepciones', 'bi-box-arrow-in-down'),
                             ('Despachos y salidas', 'inventario:lista?grupo=salidas', 'bi-box-arrow-up'),
                             ('Traslados y tránsito', 'inventario:lista?grupo=traslados', 'bi-truck'),
                             ('Manufactura', 'inventario:lista?grupo=manufactura', 'bi-gear-wide-connected'),
                             ('Todas las operaciones', 'inventario:lista', 'bi-list-ul'),
                             ('Ajuste rápido', 'inv_ajuste', 'bi-sliders'),
                             ('Picking por olas', 'inventario:olas', 'bi-water'),
                             ('Traslados por aprobar o recibir', 'compras:traslados', 'bi-arrow-left-right'),
                             ('Inventario físico (general, cíclico, aleatorio…)', 'inventario:fisico_lista',
                              'bi-clipboard2-check'),
                             ('Programa cíclico ABC', 'inventario:ciclico', 'bi-arrow-repeat'),
                             ('Requerimientos de las áreas', 'requerimientos:lista?estado=APROBADO',
                              'bi-clipboard-plus'),
                             ('Carga masiva de saldos y productos', 'carga_masiva?tipo=saldos',
                              'bi-file-earmark-arrow-up')]),
            ('Reportes', [('Reportes masivos (stock a fecha, kardex masivo, rotación…)', 'inventario:reportes_masivos',
                           'bi-table'),
                          ('Indicadores de inventario físico', 'inventario:fisico_indicadores', 'bi-speedometer2'),
                          ('Kardex', 'inv_kardex', 'bi-list-columns'),
                          ('Kardex del sistema anterior', 'hist_kardex', 'bi-clock-history'),
                          ('Lotes, series y vencimientos', 'inv_lotes', 'bi-upc-scan'),
                          ('Inventario permanente SUNAT 12.1 / 13.1', 'inv_libro_sunat', 'bi-journal-text'),
                          ('Sugerencia de compra', 'inv_reposicion', 'bi-cart-plus'),
                          ('Valorización al cierre', 'inv_valorizacion', 'bi-calculator'),
                          ('Cierre de kardex', 'inventario:cierres', 'bi-lock')]),
            ('Configuración', [('Productos y servicios', 'productos', 'bi-box-seam'),
                               ('Almacenes', 'almacenes', 'bi-house-gear'),
                               ('Ubicaciones en el almacén', 'ubicaciones', 'bi-grid-3x3'),
                               ('Tipos de operación', 'inventario:tipos', 'bi-ui-checks')]),
        ],
    },
    {
        'clave': 'manufactura', 'nombre': 'Manufactura', 'icono': 'bi-gear-wide-connected', 'color': '#c2410c',
        'inicio': 'manufactura:ordenes', 'descripcion': 'Recetas, órdenes de producción y requerimiento de materiales',
        'menu': [
            ('Producción', [('Órdenes de producción', 'manufactura:ordenes', 'bi-clipboard-data'),
                            ('Nueva orden de producción', 'manufactura:orden_nueva', 'bi-plus-circle'),
                            ('Requerimiento de materiales', 'manufactura:requerimiento', 'bi-basket'),
                            ('Órdenes del sistema anterior', 'hist_fabricacion', 'bi-clock-history')]),
            ('Planta', [('Reporte de planta (tablets)', 'manufactura:planta', 'bi-tablet'),
                        ('Paradas de línea', 'manufactura:paradas', 'bi-pause-circle')]),
            ('Reportes', [('Reporte diario de producción', 'manufactura:diario', 'bi-calendar-day'),
                          ('Tablero diario de producción', 'manufactura:tablero', 'bi-speedometer2'),
                          ('Todos los reportes de producción', 'manufactura:reportes', 'bi-bar-chart-line'),
                          ('Log de modificaciones', 'manufactura:modificaciones', 'bi-journal-text')]),
            ('Planificación', [('MRP: necesidades y órdenes planificadas', 'manufactura:mrp', 'bi-diagram-2'),
                               ('Pronóstico de demanda', 'manufactura:pronostico', 'bi-graph-up'),
                               ('Programación detallada de planta', 'manufactura:programacion', 'bi-calendar-range'),
                               ('Carga y capacidad de puestos', 'manufactura:capacidad', 'bi-speedometer')]),
            ('Calidad', [('Inspecciones de calidad', 'manufactura:inspecciones', 'bi-clipboard2-check'),
                         ('Nueva inspección', 'manufactura:inspeccion_nueva', 'bi-plus-circle'),
                         ('Planes de calidad por producto', 'manufactura:planes_calidad', 'bi-list-check'),
                         ('Certificados de análisis y liberación de lotes', 'manufactura:certificados',
                          'bi-patch-check')]),
            ('Mantenimiento', [('Órdenes de mantenimiento', 'manufactura:ordenes_mant', 'bi-wrench-adjustable'),
                               ('Nueva orden de mantenimiento', 'manufactura:orden_mant_nueva', 'bi-plus-circle'),
                               ('Equipos y planes preventivos', 'manufactura:equipos', 'bi-gear'),
                               ('Parte diario de horómetros / odómetros', 'flota:parte', 'bi-speedometer'),
                               ('Documentos de equipos por vencer', 'flota:documentos', 'bi-calendar-x')]),
            ('Ingeniería', [('Listas de materiales (recetas)', 'manufactura:listas', 'bi-diagram-3'),
                            ('Cambios de ingeniería', 'manufactura:cambios', 'bi-arrow-left-right'),
                            ('Hojas de ruta', 'manufactura:hojas', 'bi-signpost-split'),
                            ('Versiones de fabricación', 'manufactura:versiones', 'bi-layers'),
                            ('Puestos de trabajo', 'manufactura:centros', 'bi-tools'),
                            ('Motivos de parada (OEE)', 'manufactura:motivos_parada', 'bi-pause-btn'),
                            ('Carga masiva de maestros (Excel)', 'carga_masiva?tipo=centros_beneficio',
                             'bi-file-earmark-arrow-up')]),
            ('Productos', 'productos'),
        ],
    },
    {
        'clave': 'costos', 'nombre': 'Costos', 'icono': 'bi-calculator', 'color': '#0f766e',
        'inicio': 'costos:estandar', 'descripcion': 'Costo estándar, real vs estándar y rentabilidad',
        'menu': [
            ('Costo estándar del periodo', 'costos:estandar'),
            ('Control de costos', [('Real vs estándar y variaciones', 'costos:real_vs_estandar', 'bi-sliders'),
                                   ('Absorción de costos de planta', 'costos:absorcion', 'bi-funnel'),
                                   ('Costeo por actividades (ABC)', 'costos:abc', 'bi-diagram-2'),
                                   ('Actividades e inductores', 'costos:actividades', 'bi-gear')]),
            ('Cierre de costos (NIC 2)', [('Liquidación de costo real', 'costos:liquidacion', 'bi-calculator'),
                                          ('Valor neto realizable (VNR)', 'costos:vnr', 'bi-graph-down-arrow'),
                                          ('Gastos de planta: fijos y variables', 'costos:comportamiento',
                                           'bi-sliders2')]),
            ('Rentabilidad', [('Por producto', 'costos:rentabilidad?agrupar=producto', 'bi-box-seam'),
                              ('Por cliente', 'costos:rentabilidad?agrupar=cliente', 'bi-person'),
                              ('Por vendedor', 'costos:rentabilidad?agrupar=vendedor', 'bi-person-badge'),
                              ('Por mes', 'costos:rentabilidad?agrupar=mes', 'bi-calendar3')]),
            ('Valorización de inventario', 'inv_valorizacion'),
        ],
    },
    {
        'clave': 'activos', 'nombre': 'Activos fijos', 'icono': 'bi-building-gear', 'color': '#7c5c2e',
        'inicio': 'activos:lista', 'descripcion': 'Registro de activos, depreciación y bajas',
        'menu': [
            ('Activos', [('Activos fijos', 'activos:lista', 'bi-building-gear'),
                         ('Nuevo activo', 'activos:nuevo', 'bi-plus-circle'),
                         ('Carga masiva de activos (Excel)', 'carga_masiva?tipo=activos', 'bi-file-earmark-arrow-up')]),
            ('Depreciación', 'activos:depreciacion'),
            ('Reportes', [('Registro de activos fijos 7.1', 'activos:registro', 'bi-journal-text'),
                          ('Cuadre con contabilidad', 'activos:cuadre', 'bi-check2-square')]),
            ('Configuración', [('Categorías y cuentas', 'activos:categorias', 'bi-tags')]),
        ],
    },
    {
        'clave': 'obras', 'nombre': 'Obras', 'icono': 'bi-cone-striped', 'color': '#d97706',
        'inicio': 'obras:lista', 'descripcion': 'Presupuestos de obra con análisis de precios unitarios',
        'menu': [
            ('Obras', [('Obras', 'obras:lista', 'bi-cone-striped'),
                       ('Nueva obra', 'obras:obra_nueva', 'bi-plus-circle'),
                       ('Sobrantes en almacenes de obra', 'obras:sobrantes', 'bi-box-seam')]),
            ('Comercial', [('Licitaciones y propuestas (CRM)', 'obras:licitaciones', 'bi-trophy'),
                           ('Nueva licitación / propuesta', 'obras:licitacion_nueva', 'bi-plus-circle'),
                           ('Cobranza inmobiliaria', 'obras:inm_cobranza', 'bi-house-check'),
                           ('Reportes inmobiliarios', 'obras:inm_reportes', 'bi-graph-up')]),
            ('Presupuestos', [('Recursos (insumos)', 'obras:recursos', 'bi-boxes'),
                              ('Vincular recursos con productos', 'obras:vincular', 'bi-link-45deg'),
                              ('Biblioteca de partidas tipo', 'obras:biblioteca', 'bi-collection'),
                              ('Índices unificados (INEI)', 'obras:indices', 'bi-table'),
                              ('Plantilla de importación (Excel)', 'obras:plantilla', 'bi-file-earmark-excel')]),
            ('Reportes', [('Resultado por obra (con gastos generales asignados)', 'contabilidad:reparto_resultado',
                           'bi-bar-chart')]),
        ],
    },
    {
        'clave': 'sst', 'nombre': 'Seguridad y salud', 'icono': 'bi-heart-pulse', 'color': '#e11d48',
        'inicio': 'sst:tablero', 'descripcion': 'Registros obligatorios de SST, IPERC, EPP, accidentes e índices',
        'menu': [
            ('SST', [('Tablero de SST', 'sst:tablero', 'bi-speedometer2'),
                     ('Accidentes, incidentes y enfermedades', 'sst:registros?tipo=eventos', 'bi-exclamation-octagon'),
                     ('Índices de accidentabilidad', 'sst:indices', 'bi-graph-up')]),
            ('Registros', [('Exámenes médicos ocupacionales', 'sst:registros?tipo=emo', 'bi-clipboard2-pulse'),
                           ('Monitoreo de agentes', 'sst:registros?tipo=monitoreos', 'bi-activity'),
                           ('Inspecciones', 'sst:registros?tipo=inspecciones', 'bi-search'),
                           ('Inducción, capacitación y simulacros', 'sst:registros?tipo=capacitaciones',
                            'bi-mortarboard'),
                           ('Equipos de seguridad y emergencia', 'sst:registros?tipo=equipos', 'bi-fire'),
                           ('IPERC', 'sst:registros?tipo=iperc', 'bi-grid-3x3'),
                           ('Entrega de EPP', 'sst:registros?tipo=epp', 'bi-shield-check'),
                           ('SCTR', 'sst:registros?tipo=sctr', 'bi-file-medical'),
                           ('Permisos de trabajo de alto riesgo', 'sst:registros?tipo=permisos', 'bi-cone')]),
            ('Configuración', [('Reglas de validación por especialista', 'validacion_reglas', 'bi-patch-check')]),
        ],
    },
    {
        'clave': 'calidad', 'nombre': 'Excelencia organizacional', 'icono': 'bi-award', 'color': '#0d9488',
        'inicio': 'calidad:tablero', 'descripcion': 'Indicadores por área, no conformidades, auditorías y mejora',
        'menu': [
            ('Gestión', [('Objetivos e indicadores (BSC)', 'calidad:tablero', 'bi-bullseye'),
                         ('Definir indicadores', 'calidad:registros?tipo=indicadores', 'bi-sliders'),
                         ('Acciones correctivas y preventivas', 'calidad:acciones', 'bi-check2-square')]),
            ('Registros', [('No conformidades', 'calidad:registros?tipo=no-conformidades', 'bi-x-octagon'),
                           ('Auditorías internas', 'calidad:registros?tipo=auditorias', 'bi-clipboard-check'),
                           ('Documentos controlados', 'calidad:registros?tipo=documentos', 'bi-file-earmark-text'),
                           ('Sugerencias de mejora (Kaizen)', 'calidad:registros?tipo=sugerencias', 'bi-lightbulb')]),
            ('Configuración', [('Reglas de validación por especialista', 'validacion_reglas', 'bi-patch-check')]),
        ],
    },
    {
        'clave': 'planillas', 'nombre': 'Planillas', 'icono': 'bi-person-badge', 'color': '#9333ea',
        'inicio': 'planillas:lista', 'descripcion': 'Trabajadores, remuneraciones, gratificaciones, CTS y PLAME',
        'menu': [
            ('Planillas', [('Planillas', 'planillas:lista', 'bi-cash-stack'),
                           ('Nueva planilla', 'planillas:nueva', 'bi-plus-circle'),
                           ('Liquidación de beneficios (ceses)', 'planillas:nueva?tipo=LIQUIDACION',
                            'bi-box-arrow-right')]),
            ('Organización', [('Organigrama, unidades y puestos', 'planillas:organigrama', 'bi-diagram-3'),
                              ('Movimientos de personal', 'planillas:movimientos', 'bi-arrow-left-right'),
                              ('Solicitudes de vacaciones y permisos', 'planillas:solicitudes_personal',
                               'bi-calendar-check')]),
            ('Personal', [('Trabajadores', 'planillas:trabajadores', 'bi-people'),
                          ('Contratos y legajo (vencimientos)', 'planillas:contratos', 'bi-file-earmark-person'),
                          ('Vacaciones y récord vacacional', 'planillas:vacaciones', 'bi-sun'),
                          ('Asistencia y marcaciones', 'planillas:asistencia', 'bi-fingerprint'),
                          ('Turnos de trabajo', 'planillas:turnos', 'bi-clock')]),
            ('Reportes', [('Costo por centro de costo', 'planillas:costo_centros', 'bi-diagram-3'),
                          ('Datos para el T-Registro (Excel)', 'planillas:t_registro', 'bi-file-earmark-excel')]),
            ('Configuración', [('Parámetros, AFP y conceptos', 'planillas:configuracion', 'bi-gear')]),
        ],
    },
    {
        'clave': 'requerimientos', 'nombre': 'Requerimientos', 'icono': 'bi-clipboard-plus', 'color': '#0f766e',
        'inicio': 'requerimientos:lista', 'descripcion': 'Pedidos de materiales de las áreas al almacén',
        'menu': [
            ('Mis requerimientos', 'requerimientos:lista'),
            ('Nuevo requerimiento', 'requerimientos:nuevo'),
            ('Por aprobar', 'requerimientos:lista?estado=ENVIADO'),
            ('Por atender', 'requerimientos:lista?estado=APROBADO'),
            ('Solicitudes de pedido', [('Mis solicitudes de pedido', 'solicitudes:lista', 'bi-cart'),
                                       ('Nueva solicitud de pedido', 'solicitudes:nueva', 'bi-plus-circle'),
                                       ('Por liberar', 'solicitudes:lista?vista=liberar', 'bi-check2-circle'),
                                       ('Liberar desde el celular', 'solicitudes:movil', 'bi-phone')]),
        ],
    },
    {
        'clave': 'rendiciones', 'nombre': 'Rendición de gastos', 'icono': 'bi-receipt-cutoff', 'color': '#0b7285',
        'inicio': 'rendiciones:lista', 'descripcion': 'Gastos de entregas a rendir y caja chica, con aprobación',
        'menu': [
            ('Mis rendiciones', 'rendiciones:lista?vista=mias'),
            ('Rendir', [('Nueva rendición', 'rendiciones:nueva', 'bi-plus-circle'),
                        ('App del celular (foto, QR, sin internet)', 'rendiciones:movil', 'bi-phone'),
                        ('Aprobar desde el celular', 'rendiciones:movil_aprobar', 'bi-phone-vibrate')]),
            ('Revisión', [('Por aprobar', 'rendiciones:lista?vista=aprobar', 'bi-check2-circle'),
                          ('Por contabilizar', 'rendiciones:lista?vista=contabilizar', 'bi-journal-check'),
                          ('Reembolsos y devoluciones', 'rendiciones:pagos', 'bi-cash-coin'),
                          ('Reposición de caja chica', 'rendiciones:cajas', 'bi-safe'),
                          ('Tarjetas corporativas', 'rendiciones:tarjetas', 'bi-credit-card'),
                          ('Todas las rendiciones', 'rendiciones:lista?vista=todas', 'bi-list-ul')]),
            ('Reportes', [('Tablero de gastos', 'rendiciones:tablero', 'bi-speedometer2'),
                          ('Saldos por rendir con antigüedad', 'rendiciones:antiguedad', 'bi-hourglass-split'),
                          ('Cargos de tarjeta sin comprobante', 'rendiciones:sin_comprobante', 'bi-receipt'),
                          ('Trazabilidad de aprobación', 'rendiciones:trazabilidad', 'bi-diagram-3'),
                          ('Observaciones y errores por usuario y área', 'rendiciones:errores', 'bi-bug')]),
            ('Configuración', [('Categorías, políticas y aprobación', 'rendiciones:configuracion', 'bi-gear'),
                               ('Proyectos y sitios', 'rendiciones:proyectos', 'bi-geo-alt'),
                               ('Integraciones (WhatsApp, correo, IA)', 'rendiciones:integraciones', 'bi-plug'),
                               ('Exportación a un ERP externo (SAP)', 'rendiciones:integracion', 'bi-box-arrow-up-right'),
                               ('Cargar categorías (Excel)', 'carga_masiva?tipo=categorias_gasto',
                                'bi-file-earmark-arrow-up')]),
        ],
    },
    {
        'clave': 'logistica', 'nombre': 'Logística', 'icono': 'bi-truck', 'color': '#1098ad',
        'inicio': 'logistica:lista', 'descripcion': 'Guías de remisión remitente y transportista',
        'menu': [
            ('Guías de remisión', [('Guías remitente', 'logistica:lista?tipo=09', 'bi-truck'),
                                   ('Guías transportista', 'logistica:lista?tipo=31', 'bi-truck-front'),
                                   ('Nueva guía remitente', 'logistica:nueva?tipo=09', 'bi-plus-circle'),
                                   ('Nueva guía transportista', 'logistica:nueva?tipo=31', 'bi-plus-circle')]),
            ('Garita', [('Registro de garita', 'logistica:garita_lista', 'bi-sign-stop'),
                        ('Ingreso de vehículo', 'logistica:garita_nuevo', 'bi-plus-circle')]),
            ('Flota', [('Equipos y vehículos', 'flota:equipos', 'bi-truck-flatbed'),
                       ('Parte diario de odómetros / horómetros', 'flota:parte', 'bi-speedometer'),
                       ('Lecturas por aprobar', 'flota:lecturas', 'bi-check2-square'),
                       ('Combustible (vales y rendimiento)', 'flota:combustible', 'bi-fuel-pump'),
                       ('Viajes', 'flota:viajes', 'bi-signpost-2'),
                       ('Préstamos y comodato', 'flota:prestamos', 'bi-box-arrow-up-right'),
                       ('Costo por equipo (km u hora)', 'flota:costos', 'bi-cash-stack'),
                       ('Cargar equipos, componentes y lecturas (Excel)', 'carga_masiva?tipo=equipos',
                        'bi-file-earmark-arrow-up'),
                       ('Documentos por vencer (SOAT, revisión, licencias)', 'flota:documentos', 'bi-calendar-x')]),
            ('Configuración', [('Vehículos', 'logistica:vehiculos', 'bi-car-front'),
                               ('Conductores', 'logistica:conductores', 'bi-person-badge')]),
        ],
    },
    {
        'clave': 'tesoreria', 'nombre': 'Tesorería', 'icono': 'bi-bank', 'color': '#1971c2',
        'inicio': 'finanzas:cuentas', 'descripcion': 'Caja, bancos, cobranzas, pagos, detracciones y conciliación',
        'menu': [
            ('Caja y bancos', [('Cuentas de caja y bancos', 'finanzas:cuentas', 'bi-bank'),
                               ('Movimientos', 'finanzas:movimientos', 'bi-arrow-left-right'),
                               ('Transferencias', 'finanzas:transferencia', 'bi-shuffle'),
                               ('Cheques', 'finanzas:cheques', 'bi-credit-card-2-front'),
                               ('Entregas a rendir y caja chica', 'finanzas:entregas', 'bi-wallet2')]),
            ('Cobranzas y pagos', [('Cobranzas', 'finanzas:cobranza', 'bi-cash-coin'),
                                   ('Cobranza múltiple (varios clientes)', 'finanzas:cobranza_multiple',
                                    'bi-cash-stack'),
                                   ('Pagos', 'finanzas:pago', 'bi-credit-card'),
                                   ('Pago múltiple (varios proveedores)', 'finanzas:pago_multiple',
                                    'bi-credit-card-2-back'),
                                   ('Pagos masivos a bancos', 'finanzas:pagos_masivos', 'bi-send')]),
            ('Detracciones', [('Depósito masivo de detracciones', 'finanzas:detracciones_masivas', 'bi-bank'),
                              ('Constancias de detracción de clientes (SUNAT)', 'finanzas:constancias_clientes',
                               'bi-cloud-upload')]),
            ('Bancos', [('Conciliación automática (extracto)', 'finanzas:extractos', 'bi-magic'),
                        ('Conciliación bancaria', 'finanzas:conciliacion', 'bi-check2-square'),
                        ('Importar estado de cuenta', 'finanzas:importar', 'bi-upload')]),
            ('Reportes', [('Flujo de caja', 'finanzas:flujo', 'bi-water'),
                          ('Flujo de caja proyectado', 'finanzas:flujo_proyectado', 'bi-graph-up-arrow')]),
            ('Configuración', [('Tipo de cambio', 'tipos_cambio', 'bi-currency-exchange')]),
        ],
    },
    {
        'clave': 'finanzas', 'nombre': 'Finanzas', 'icono': 'bi-piggy-bank', 'color': '#3b5bdb',
        'inicio': 'finanzas:anticipos', 'descripcion': 'Anticipos, letras, canjes, préstamos y leasing',
        'menu': [
            ('Documentos', [('Anticipos', 'finanzas:anticipos', 'bi-hourglass-split'),
                            ('Letras por cobrar y pagar', 'finanzas:letras', 'bi-file-earmark-ruled'),
                            ('Canje de facturas por letras', 'finanzas:canje_nuevo', 'bi-arrow-repeat')]),
            ('Financiamiento', [('Préstamos y leasing', 'finanzas:prestamos', 'bi-piggy-bank')]),
            ('Reportes', [('Flujo de caja proyectado', 'finanzas:flujo_proyectado', 'bi-graph-up-arrow')]),
        ],
    },
    {
        'clave': 'contabilidad', 'nombre': 'Contabilidad', 'icono': 'bi-journal-bookmark', 'color': '#5f3dc4',
        'inicio': 'contabilidad:asientos', 'descripcion': 'Asientos, libros y estados financieros',
        'menu': [
            ('Asientos', [('Asientos contables', 'contabilidad:asientos', 'bi-journal-plus'),
                          ('Nuevo asiento manual', 'contabilidad:asiento_nuevo', 'bi-plus-circle'),
                          ('Centralización y periodos', 'contabilidad:periodos', 'bi-arrow-repeat'),
                          ('Reparto de gastos entre centros (obras)', 'contabilidad:reparto', 'bi-diagram-3'),
                          ('Cierre guiado del mes', 'contabilidad:cierre', 'bi-list-check')]),
            ('Libros', [('Libro diario', 'contabilidad:diario', 'bi-journal-text'),
                        ('Libro mayor', 'contabilidad:mayor', 'bi-journal-bookmark'),
                        ('Inventarios y Balances (3.x)', 'contabilidad:libro_inventarios', 'bi-journal-richtext'),
                        ('Inventario permanente 12.1 / 13.1', 'inv_libro_sunat', 'bi-boxes')]),
            ('Reportes', [('Balance de comprobación', 'contabilidad:balance', 'bi-table'),
                          ('Estado de situación financiera', 'contabilidad:situacion', 'bi-bank2'),
                          ('Estado de resultados', 'contabilidad:resultados', 'bi-graph-up-arrow'),
                          ('Estado de flujos de efectivo (NIC 7)', 'contabilidad:flujo_efectivo', 'bi-water'),
                          ('Resultados por línea de negocio', 'contabilidad:resultados_linea', 'bi-bar-chart-steps'),
                          ('Gastos por centro de costo', 'contabilidad:centros_reporte', 'bi-diagram-3'),
                          ('Resultado por centro y obra (con gastos asignados)', 'contabilidad:reparto_resultado',
                           'bi-bar-chart'),
                          ('Presupuestos y ejecución', 'contabilidad:presupuestos', 'bi-clipboard-data'),
                          ('Prorrata del crédito fiscal (IGV)', 'contabilidad:prorrata', 'bi-percent'),
                          ('Conciliación NIIF - tributaria', 'contabilidad:conciliacion_normas', 'bi-intersect'),
                          ('Estados consolidados del grupo', 'contabilidad:consolidacion', 'bi-buildings'),
                          ('Documentos del cliente (estudio)', 'cliente_documentos', 'bi-inbox')]),
            ('Sistema anterior', [('Conciliación de la migración', 'contabilidad:conciliacion_migracion',
                                   'bi-check2-square'),
                                  ('Asientos del sistema anterior', 'hist_asientos', 'bi-clock-history'),
                                  ('Balance del sistema anterior', 'hist_balance', 'bi-table'),
                                  ('Posiciones presupuestarias', 'hist_posiciones', 'bi-collection')]),
            ('Configuración', [('Plan de cuentas', 'contabilidad:plan', 'bi-list-ol'),
                               ('Centros de beneficio (líneas)', 'contabilidad:beneficios', 'bi-briefcase'),
                               ('Centros de costo', 'contabilidad:centros', 'bi-bullseye'),
                               ('Cifras de reparto (m², trabajadores…)', 'contabilidad:reparto_cifras', 'bi-rulers'),
                               ('Cuentas por operación', 'contabilidad:configuracion', 'bi-gear'),
                               ('Estado de flujos de efectivo (rubros y reglas)', 'contabilidad:flujo_configuracion',
                                'bi-sliders'),
                               ('Cuentas por tipo de producto', 'contabilidad:cuentas_tipo', 'bi-box-seam')]),
        ],
    },
    {
        'clave': 'cliente', 'nombre': 'Mi empresa', 'icono': 'bi-person-workspace', 'color': '#1098ad',
        'inicio': 'cliente_documentos',
        'descripcion': 'Cliente del estudio contable: sube sus documentos y consulta sus reportes',
        'menu': [('Subir documentos', 'cliente_subir'), ('Mis documentos', 'cliente_documentos'),
                 ('Mis reportes', 'cliente_reportes')],
    },
    {
        'clave': 'contactos', 'nombre': 'Contactos', 'icono': 'bi-people', 'color': '#f08c00',
        'inicio': 'terceros', 'descripcion': 'Clientes y proveedores',
        'menu': [('Clientes', 'terceros?tipo=CLIENTE'), ('Proveedores', 'terceros?tipo=PROVEEDOR'),
                 ('Todos', 'terceros')],
    },
    {
        'clave': 'ajustes', 'nombre': 'Ajustes', 'icono': 'bi-gear', 'color': '#495057',
        'inicio': 'empresa', 'descripcion': 'Empresa, usuarios y configuración general',
        'menu': [
            ('Empresa', [('Datos de la empresa', 'empresa', 'bi-building'),
                         ('Giro y régimen tributario', 'empresa_giro', 'bi-sliders'),
                         ('Parámetros tributarios (UIT, tasas, umbrales)', 'parametros', 'bi-percent')]),
            ('Usuarios y permisos', [('Usuarios', 'usuarios', 'bi-people'),
                                     ('Perfiles de acceso (roles)', 'perfiles_acceso', 'bi-person-badge'),
                                     ('Ingreso con Microsoft / Google', 'sso_lista', 'bi-microsoft'),
                                     ('Segregación de funciones', 'segregacion', 'bi-shield-exclamation'),
                                     ('Reglas de aprobación (todos los documentos)', 'aprobaciones_reglas',
                                      'bi-diagram-3'),
                                     ('Suplencias de aprobación', 'aprobaciones_delegaciones', 'bi-person-check')]),
            ('Control', [('Auditoría de todo el sistema', 'auditoria', 'bi-journal-text'),
                         ('Incidencias y errores', 'incidencias', 'bi-bug')]),
            ('Configuración', [('Correlativos / series', 'series', 'bi-123'),
                               ('Tipo de cambio', 'tipos_cambio', 'bi-currency-exchange'),
                               ('Facturación electrónica', 'facturacion', 'bi-cloud-upload'),
                               ('Correo saliente', 'correo', 'bi-envelope'),
                               ('Carga masiva (Excel)', 'carga_masiva', 'bi-file-earmark-arrow-up'),
                               ('Respaldo de datos', 'respaldo', 'bi-shield-check')]),
            ('Ayuda', [('Manuales de usuario', 'manuales', 'bi-book'),
                       ('Novedades por versión', 'novedades', 'bi-stars')]),
        ],
    },
]


def _agregar_log_de_modificaciones():
    """«Log de modificaciones» (solo administradores) en cada módulo: en su grupo Reportes o en un grupo Control.
    Manufactura tiene el suyo propio y Ajustes la auditoría de todo el sistema."""
    for m in MODULOS:
        if m['clave'] in ('manufactura', 'ajustes'):
            continue
        item = ('Log de modificaciones', f'modificaciones?modulo={m["clave"]}', 'bi-journal-text')
        grupo = next((d for e, d in m['menu'] if e == 'Reportes' and isinstance(d, list)), None)
        if grupo is not None:
            grupo.append(item)
        else:
            m['menu'].append(('Control', [item]))


_agregar_log_de_modificaciones()
POR_CLAVE = {m['clave']: m for m in MODULOS}

# Nombre de grupo de Django por módulo (lo que se asigna a cada usuario)
GRUPOS = {m['clave']: m['nombre'] for m in MODULOS if m['clave'] != 'contactos'}

# Contactos no tiene grupo propio: lo usan quienes venden, compran, cobran o despachan
DERIVADOS = {'contactos': {'crm', 'ventas', 'compras', 'tesoreria', 'finanzas', 'logistica', 'contabilidad'},
             # el almacén atiende los requerimientos de las áreas; Compras ve y libera sus solicitudes de pedido
             'requerimientos': {'inventario', 'compras', 'obras'},  # la obra pide materiales y servicios
             'rendiciones': {'tesoreria', 'finanzas', 'contabilidad'}}  # tesorería y contabilidad revisan las rendiciones

# Rutas sin espacio de nombres (app core) -> módulos que pueden abrirlas (el primero es el principal)
RUTAS_CORE = {
    'dashboard': ['tablero'],
    'cliente_subir': ['cliente'], 'cliente_reportes': ['cliente'],
    'cliente_documentos': ['cliente', 'contabilidad'], 'cliente_archivo': ['cliente', 'contabilidad'],
    'empresa': ['ajustes'], 'empresa_giro': ['ajustes'], 'parametros': ['ajustes', 'contabilidad'],
    'validacion_reglas': ['sst', 'calidad', 'ajustes'], 'consulta_ruc': ['ajustes', 'contactos', 'crm', 'ventas', 'compras'], 'facturacion': ['ajustes'], 'correo': ['ajustes'], 'respaldo': ['ajustes'],
    'auditoria': ['ajustes'], 'manuales': ['ajustes'], 'manual_pdf': ['ajustes'], 'manual_proceso_pdf': ['ajustes'],
    'incidencias': ['ajustes'], 'incidencia': ['ajustes'],
    'modificaciones': [m['clave'] for m in MODULOS],  # se ve dentro del módulo; la vista exige ser administrador
    'carga_masiva': ['ajustes', 'inventario', 'compras', 'ventas', 'tesoreria', 'finanzas', 'contactos', 'manufactura',
                     'logistica', 'contabilidad', 'planillas', 'rendiciones', 'activos'], 'usuarios': ['ajustes'],
    'usuario_nuevo': ['ajustes'],
    'usuario_editar': ['ajustes'], 'segregacion': ['ajustes'],
    'sire_propuestas': ['compras', 'ventas', 'contabilidad'], 'sire_propuesta': ['compras', 'ventas', 'contabilidad'],
    'perfiles_acceso': ['ajustes'], 'sso_lista': ['ajustes'], 'sso_nuevo': ['ajustes'], 'sso_editar': ['ajustes'], 'perfil_acceso_nuevo': ['ajustes'], 'perfil_acceso_editar': ['ajustes'],
    'aprobaciones_reglas': ['ajustes', 'compras', 'rendiciones', 'planillas'],
    'series': ['ajustes'], 'serie_nueva': ['ajustes'], 'serie_editar': ['ajustes'],
    'tipos_cambio': ['ajustes', 'tesoreria', 'finanzas'],
    'terceros': ['contactos', 'crm', 'ventas', 'compras'], 'tercero_nuevo': ['contactos', 'crm', 'ventas', 'compras'],
    'tercero_editar': ['contactos', 'crm', 'ventas', 'compras'],
    'productos': ['inventario', 'ventas', 'compras', 'manufactura'],
    'producto_nuevo': ['inventario', 'ventas', 'compras', 'manufactura'],
    'producto_editar': ['inventario', 'ventas', 'compras', 'manufactura'], 'kardex': ['inventario'],
    'producto_imagen': ['inventario', 'ventas', 'compras', 'manufactura'],
    # historial del sistema anterior (solo consulta)
    'hist_kardex': ['inventario', 'costos'], 'hist_asientos': ['contabilidad'], 'hist_balance': ['contabilidad'],
    'hist_posiciones': ['contabilidad'], 'hist_fabricacion': ['manufactura', 'costos'],
    'hist_orden': ['manufactura', 'costos'],
    'producto_variantes': ['inventario', 'ventas', 'compras', 'manufactura'],
    'inv_stock': ['inventario'], 'inv_kardex': ['inventario'], 'inv_libro_sunat': ['inventario', 'contabilidad'],
    'inv_lotes': ['inventario', 'ventas', 'compras'], 'inv_lote': ['inventario', 'ventas', 'compras'], 'inv_ajuste': ['inventario'],
    'inv_valorizacion': ['inventario', 'costos'], 'inv_reposicion': ['inventario', 'compras'], 'almacenes': ['inventario'], 'almacen_nuevo': ['inventario'],
    'almacen_editar': ['inventario'], 'ubicaciones': ['inventario'],
}
# Rutas abiertas a cualquier usuario autenticado
RUTAS_LIBRES = {'home', 'tipo_cambio_api', 'ubigeos_json', 'login', 'logout', 'cambiar_clave', 'cambiar_clave_ok',
                'cambiar_empresa', 'novedades',  # novedades por versión: para todos los usuarios
                'alertas', 'alertas_json',  # cada alerta ya se filtra por los módulos del usuario
                'login_2fa', 'recuperar', 'recuperar_confirmar', 'recuperar_listo', 'seguridad',
                'sustento_subir', 'sustento_ver',  # los sustentos validan el módulo del documento
                'validacion_accion',  # validación por especialista: valida el módulo y el permiso del registro
                'lista_preferencias',  # columnas y filtros de las listas: preferencia de cada usuario
                'api_claves',  # cada usuario administra sus claves de API (actúan con sus propios permisos)
                'opciones',  # listas de los selectores (códigos y nombres, sin importes)
                'producto_datos',  # precio y unidad del producto elegido (valida los módulos; el costo según permiso)
                'reportar', 'salud',  # «Reportar problema» lo usa cualquiera; /salud/ es para el monitor de caídas
                'estado_servicio', 'sso_iniciar', 'sso_callback',  # estado público e ingreso Microsoft / Google
                'grupo_empresas', 'grupo_entrar', 'grupo_estado', 'grupo_panel',  # exigen ser usuario corporativo
                'instalacion',  # solo existe mientras el sistema no tiene usuarios
                # cada usuario ve y resuelve lo que le toca aprobar y delega sus aprobaciones (cada acción valida)
                'aprobaciones_bandeja', 'aprobaciones_delegaciones',
                'mi_portal', 'mi_boleta'}  # autoservicio: cada trabajador ve solo lo suyo


# Permiso especial: ver costos de inventario (costo promedio, valorizado, kardex valorizado)
GRUPO_COSTOS = 'Ver costos de inventario'


def puede_ver_costos(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    cache = getattr(user, '_ver_costos', None)
    if cache is None:
        cache = user._ver_costos = user.groups.filter(name=GRUPO_COSTOS).exists()
    return cache


def _ocultos_empresa():
    """Módulos que la empresa no usa (Ajustes › Giro y régimen): no se muestran ni se abren. Ajustes nunca."""
    from .models import Empresa
    return set(Empresa.objects.values_list('modulos_ocultos', flat=True).first() or []) - {'ajustes', 'tablero'}


def modulos_del_usuario(user):
    """Conjunto de claves de módulos a los que el usuario tiene acceso."""
    if not user.is_authenticated:
        return set()
    cache = getattr(user, '_modulos_cache', None)
    if cache is None:
        if user.is_superuser:
            cache = set(POR_CLAVE)
        else:
            nombres = set(user.groups.values_list('name', flat=True))
            cache = {clave for clave, nombre in GRUPOS.items() if nombre in nombres}
            for derivado, origen in DERIVADOS.items():
                if cache & origen:
                    cache.add(derivado)
        cache -= _ocultos_empresa()
        user._modulos_cache = cache
    return cache


# espacios de nombres que no son un módulo pero se usan desde varios (el primero es el principal)
NAMESPACES_COMPARTIDOS = {'solicitudes': ['requerimientos', 'compras', 'manufactura'],
                          'flota': ['logistica', 'manufactura']}  # maestro único de equipos y flota

# Tesorería y Finanzas comparten la app finanzas (URL /finanzas/…): módulos de cada pantalla (el primero es el
# principal). Una pantalla nueva de la app que no esté aquí queda en Finanzas.
RUTAS_FINANZAS = {
    **{nombre: ['tesoreria'] for nombre in (
        'cuentas', 'cuenta_nueva', 'cuenta_editar', 'saldo_inicial', 'saldo_inicial_sustento', 'movimientos',
        'movimiento_nuevo', 'movimiento_eliminar', 'transferencia', 'cheques', 'cheque_estado', 'entregas', 'entrega',
        'entrega_nueva', 'cobranza', 'cobranza_multiple', 'pago', 'pago_multiple', 'pagos_masivos', 'pago_masivo',
        'pago_masivo_nuevo', 'detracciones_masivas', 'detracciones_masivas_nuevo', 'detraccion_masiva',
        'constancias_clientes', 'extractos', 'extracto', 'extracto_accion', 'conciliacion', 'importar', 'flujo')},
    **{nombre: ['finanzas'] for nombre in (
        'anticipos', 'anticipo_aplicar', 'letras', 'letra', 'canje', 'canje_nuevo', 'prestamos', 'prestamo',
        'prestamo_nuevo')},
    # el voucher se abre desde ambos; las aplicaciones se anulan desde anticipos y desde las entregas a rendir
    'voucher': ['tesoreria', 'finanzas'], 'aplicacion_anular': ['finanzas', 'tesoreria'],
    'flujo_proyectado': ['tesoreria', 'finanzas'],  # incluye las cuotas de préstamos y letras por vencer
}
# pantallas de un módulo que también abre otro (vista: módulos; el primero es el principal)
RUTAS_COMPARTIDAS = {'contabilidad:reparto_resultado': ['contabilidad', 'obras'],
                     # COM-13: el traslado lo piden, aprueban y reciben compras, almacén y las obras
                     'compras:traslados': ['compras', 'inventario', 'obras', 'requerimientos'],
                     'compras:traslado_nuevo': ['compras', 'inventario', 'obras', 'requerimientos'],
                     'ventas:crm_antiguo': ['crm', 'ventas']}  # enlaces guardados de /ventas/crm/ (van a /crm/)


def modulos_de_ruta(match):
    """Módulos que pueden abrir la vista resuelta; None = libre (cualquier usuario autenticado)."""
    if match is None:
        return None
    if match.namespace:
        if match.namespace == 'admin':
            return None
        if match.view_name in RUTAS_COMPARTIDAS:
            return RUTAS_COMPARTIDAS[match.view_name]
        if match.namespace in NAMESPACES_COMPARTIDOS:
            return NAMESPACES_COMPARTIDOS[match.namespace]
        if match.namespace == 'finanzas':
            return RUTAS_FINANZAS.get(match.url_name, ['finanzas'])
        return [match.namespace] if match.namespace in POR_CLAVE else None
    if match.url_name in RUTAS_LIBRES:
        return None
    return RUTAS_CORE.get(match.url_name)


def resolver_url(destino):
    nombre, _, query = destino.partition('?')
    url = reverse(nombre)
    return f'{url}?{query}' if query else url


RUTAS_COSTOS = {'inv_valorizacion', 'inventario:cierres'}  # solo con el permiso de ver costos
RUTAS_ADMIN = {'manuales', 'contabilidad:consolidacion', 'manufactura:modificaciones', 'modificaciones', 'sso_lista'}  # solo los administradores (superusuarios) las ven


def marcar_activo(items, ruta_completa):
    """Marca la opción del menú de la página actual: coincidencia exacta o, si no hay, la URL más
    larga que sea prefijo de la ruta (p. ej. el detalle /ventas/5/ marca «Comprobantes» /ventas/)."""
    enlaces = [e for i in items for e in (i.get('hijos') or [i])]
    activo = next((e for e in enlaces if e['url'] == ruta_completa), None)
    if activo is None:
        ruta = ruta_completa.partition('?')[0]
        largos = {}
        for e in enlaces:
            base = e['url'].partition('?')[0]
            if ruta.startswith(base):
                largos.setdefault(len(base), []).append(e)
        if largos:
            mejores = largos[max(largos)]
            activo = mejores[0] if len(mejores) == 1 else next(
                (e for e in mejores if '?' not in e['url']), None)
    if activo is not None:
        activo['activo'] = True
    return items


def menu_de(modulo, ver_costos=True, ruta=None, es_admin=True, ocultas=()):
    """Menú del módulo con las URLs ya resueltas (y la opción actual marcada si se da la ruta). `ocultas`: pantallas
    que el usuario no ve (permisos por pantalla)."""
    items = []
    for etiqueta, destino in modulo['menu']:
        if isinstance(destino, list):
            hijos = [{'etiqueta': e, 'url': resolver_url(d), 'icono': i} for e, d, i in destino
                     if (ver_costos or d not in RUTAS_COSTOS) and (es_admin or d.partition('?')[0] not in RUTAS_ADMIN)
                     and d.partition('?')[0] not in ocultas]
            if hijos:
                items.append({'etiqueta': etiqueta, 'hijos': hijos})
        elif destino.partition('?')[0] not in ocultas:
            items.append({'etiqueta': etiqueta, 'url': resolver_url(destino)})
    return marcar_activo(items, ruta) if ruta else items
