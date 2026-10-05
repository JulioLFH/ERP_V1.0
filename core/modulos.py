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
        'clave': 'ventas', 'nombre': 'Ventas', 'icono': 'bi-receipt', 'color': '#2f9e44',
        'inicio': 'ventas:lista', 'descripcion': 'Cotizaciones, comprobantes y cobranzas',
        'menu': [
            ('Ventas', [('Cotizaciones y pedidos', 'ventas:cot_lista', 'bi-file-earmark-text'),
                        ('Comprobantes', 'ventas:lista', 'bi-receipt'),
                        ('Notas de crédito / débito', 'ventas:notas', 'bi-file-earmark-diff'),
                        ('Emitir comprobante', 'ventas:nuevo', 'bi-plus-circle'),
                        ('Listas de precios', 'ventas:listas_precios', 'bi-tags')]),
            ('Cuentas por cobrar', 'ventas:pendientes'),
            ('Clientes', 'terceros?tipo=CLIENTE'),
            ('Productos', 'productos'),
            ('Reportes', [('Registro de ventas 14.1 / PLE', 'ventas:registro', 'bi-journal-text'),
                          ('Reportes de ventas', 'ventas:reportes', 'bi-graph-up'),
                          ('Importar desde Excel', 'ventas:importar', 'bi-file-earmark-arrow-up')]),
        ],
    },
    {
        'clave': 'compras', 'nombre': 'Compras', 'icono': 'bi-bag', 'color': '#e8590c',
        'inicio': 'compras:lista', 'descripcion': 'Órdenes de compra, facturas y pagos',
        'menu': [
            ('Compras', [('Órdenes de compra', 'compras:oc_lista', 'bi-clipboard-check'),
                         ('Registro de compras', 'compras:lista', 'bi-bag'),
                         ('Notas de crédito / débito', 'compras:notas', 'bi-file-earmark-diff'),
                         ('Registrar compra', 'compras:nuevo', 'bi-plus-circle')]),
            ('Portal de proveedores', [('Facturas por revisar', 'compras:portal_facturas', 'bi-inbox'),
                                       ('Accesos de proveedores', 'compras:portal_accesos', 'bi-person-lock')]),
            ('Cuentas por pagar', 'compras:pendientes'),
            ('Sugerencia de compra', 'inv_reposicion'),
            ('Proveedores', 'terceros?tipo=PROVEEDOR'),
            ('Productos', 'productos'),
            ('Reportes', [('Registro de compras 8.1 / PLE', 'compras:registro', 'bi-journal-text'),
                          ('Reportes de compras', 'compras:reportes', 'bi-bar-chart'),
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
                             ('Requerimientos de las áreas', 'requerimientos:lista?estado=APROBADO',
                              'bi-clipboard-plus'),
                             ('Carga masiva de saldos y productos', 'carga_masiva?tipo=saldos',
                              'bi-file-earmark-arrow-up')]),
            ('Reportes', [('Kardex', 'inv_kardex', 'bi-list-columns'),
                          ('Lotes, series y vencimientos', 'inv_lotes', 'bi-upc-scan'),
                          ('Inventario permanente SUNAT 12.1 / 13.1', 'inv_libro_sunat', 'bi-journal-text'),
                          ('Sugerencia de compra', 'inv_reposicion', 'bi-cart-plus'),
                          ('Valorización al cierre', 'inv_valorizacion', 'bi-calculator'),
                          ('Cierre de kardex', 'inventario:cierres', 'bi-lock')]),
            ('Configuración', [('Productos y servicios', 'productos', 'bi-box-seam'),
                               ('Almacenes', 'almacenes', 'bi-house-gear'),
                               ('Tipos de operación', 'inventario:tipos', 'bi-ui-checks')]),
        ],
    },
    {
        'clave': 'manufactura', 'nombre': 'Manufactura', 'icono': 'bi-gear-wide-connected', 'color': '#c2410c',
        'inicio': 'manufactura:ordenes', 'descripcion': 'Recetas, órdenes de producción y requerimiento de materiales',
        'menu': [
            ('Producción', [('Órdenes de producción', 'manufactura:ordenes', 'bi-clipboard-data'),
                            ('Nueva orden de producción', 'manufactura:orden_nueva', 'bi-plus-circle'),
                            ('Requerimiento de materiales', 'manufactura:requerimiento', 'bi-basket')]),
            ('Planificación', [('MRP: necesidades y órdenes planificadas', 'manufactura:mrp', 'bi-diagram-2'),
                               ('Carga y capacidad de puestos', 'manufactura:capacidad', 'bi-speedometer')]),
            ('Ingeniería', [('Listas de materiales (recetas)', 'manufactura:listas', 'bi-diagram-3'),
                            ('Hojas de ruta', 'manufactura:hojas', 'bi-signpost-split'),
                            ('Versiones de fabricación', 'manufactura:versiones', 'bi-layers'),
                            ('Puestos de trabajo', 'manufactura:centros', 'bi-tools'),
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
                                   ('Absorción de costos de planta', 'costos:absorcion', 'bi-funnel')]),
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
                         ('Nuevo activo', 'activos:nuevo', 'bi-plus-circle')]),
            ('Depreciación', 'activos:depreciacion'),
            ('Reportes', [('Registro de activos fijos 7.1', 'activos:registro', 'bi-journal-text'),
                          ('Cuadre con contabilidad', 'activos:cuadre', 'bi-check2-square')]),
            ('Configuración', [('Categorías y cuentas', 'activos:categorias', 'bi-tags')]),
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
            ('Configuración', [('Vehículos', 'logistica:vehiculos', 'bi-car-front'),
                               ('Conductores', 'logistica:conductores', 'bi-person-badge')]),
        ],
    },
    {
        'clave': 'finanzas', 'nombre': 'Finanzas', 'icono': 'bi-bank', 'color': '#1971c2',
        'inicio': 'finanzas:cuentas', 'descripcion': 'Caja, bancos, cobranzas y pagos',
        'menu': [
            ('Tesorería', [('Caja y bancos', 'finanzas:cuentas', 'bi-bank'),
                           ('Movimientos', 'finanzas:movimientos', 'bi-arrow-left-right'),
                           ('Cobranzas', 'finanzas:cobranza', 'bi-cash-coin'),
                           ('Pagos', 'finanzas:pago', 'bi-credit-card'),
                           ('Transferencias', 'finanzas:transferencia', 'bi-shuffle')]),
            ('Bancos', [('Conciliación bancaria', 'finanzas:conciliacion', 'bi-check2-square'),
                        ('Importar estado de cuenta', 'finanzas:importar', 'bi-upload')]),
            ('Reportes', [('Flujo de caja', 'finanzas:flujo', 'bi-water')]),
            ('Configuración', [('Tipo de cambio', 'tipos_cambio', 'bi-currency-exchange')]),
        ],
    },
    {
        'clave': 'contabilidad', 'nombre': 'Contabilidad', 'icono': 'bi-journal-bookmark', 'color': '#5f3dc4',
        'inicio': 'contabilidad:asientos', 'descripcion': 'Asientos, libros y estados financieros',
        'menu': [
            ('Asientos', [('Asientos contables', 'contabilidad:asientos', 'bi-journal-plus'),
                          ('Nuevo asiento manual', 'contabilidad:asiento_nuevo', 'bi-plus-circle'),
                          ('Centralización y periodos', 'contabilidad:periodos', 'bi-arrow-repeat')]),
            ('Libros', [('Libro diario', 'contabilidad:diario', 'bi-journal-text'),
                        ('Libro mayor', 'contabilidad:mayor', 'bi-journal-bookmark'),
                        ('Inventario permanente 12.1 / 13.1', 'inv_libro_sunat', 'bi-boxes')]),
            ('Reportes', [('Balance de comprobación', 'contabilidad:balance', 'bi-table'),
                          ('Estado de situación financiera', 'contabilidad:situacion', 'bi-bank2'),
                          ('Estado de resultados', 'contabilidad:resultados', 'bi-graph-up-arrow'),
                          ('Resultados por línea de negocio', 'contabilidad:resultados_linea', 'bi-bar-chart-steps'),
                          ('Gastos por centro de costo', 'contabilidad:centros_reporte', 'bi-diagram-3')]),
            ('Configuración', [('Plan de cuentas', 'contabilidad:plan', 'bi-list-ol'),
                               ('Centros de beneficio (líneas)', 'contabilidad:beneficios', 'bi-briefcase'),
                               ('Centros de costo', 'contabilidad:centros', 'bi-bullseye'),
                               ('Cuentas por operación', 'contabilidad:configuracion', 'bi-gear')]),
        ],
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
            ('Empresa', 'empresa'),
            ('Usuarios y permisos', 'usuarios'),
            ('Auditoría', 'auditoria'),
            ('Configuración', [('Correlativos / series', 'series', 'bi-123'),
                               ('Tipo de cambio', 'tipos_cambio', 'bi-currency-exchange'),
                               ('Facturación electrónica', 'facturacion', 'bi-cloud-upload'),
                               ('Correo saliente', 'correo', 'bi-envelope'),
                               ('Carga masiva (Excel)', 'carga_masiva', 'bi-file-earmark-arrow-up'),
                               ('Respaldo de datos', 'respaldo', 'bi-shield-check')]),
        ],
    },
]
POR_CLAVE = {m['clave']: m for m in MODULOS}

# Nombre de grupo de Django por módulo (lo que se asigna a cada usuario)
GRUPOS = {m['clave']: m['nombre'] for m in MODULOS if m['clave'] != 'contactos'}

# Contactos no tiene grupo propio: lo usan quienes venden, compran, cobran o despachan
DERIVADOS = {'contactos': {'ventas', 'compras', 'finanzas', 'logistica', 'contabilidad'},
             'requerimientos': {'inventario'}}  # el almacén atiende los requerimientos de las áreas

# Rutas sin espacio de nombres (app core) -> módulos que pueden abrirlas (el primero es el principal)
RUTAS_CORE = {
    'dashboard': ['tablero'],
    'empresa': ['ajustes'], 'facturacion': ['ajustes'], 'correo': ['ajustes'], 'respaldo': ['ajustes'],
    'auditoria': ['ajustes'],
    'carga_masiva': ['ajustes', 'inventario', 'compras', 'ventas', 'finanzas', 'contactos', 'manufactura',
                     'contabilidad'], 'usuarios': ['ajustes'], 'usuario_nuevo': ['ajustes'],
    'usuario_editar': ['ajustes'],
    'series': ['ajustes'], 'serie_nueva': ['ajustes'], 'serie_editar': ['ajustes'],
    'tipos_cambio': ['ajustes', 'finanzas'],
    'terceros': ['contactos', 'ventas', 'compras'], 'tercero_nuevo': ['contactos', 'ventas', 'compras'],
    'tercero_editar': ['contactos', 'ventas', 'compras'],
    'productos': ['inventario', 'ventas', 'compras', 'manufactura'],
    'producto_nuevo': ['inventario', 'ventas', 'compras', 'manufactura'],
    'producto_editar': ['inventario', 'ventas', 'compras', 'manufactura'], 'kardex': ['inventario'],
    'inv_stock': ['inventario'], 'inv_kardex': ['inventario'], 'inv_libro_sunat': ['inventario', 'contabilidad'],
    'inv_lotes': ['inventario', 'ventas', 'compras'], 'inv_lote': ['inventario', 'ventas', 'compras'], 'inv_ajuste': ['inventario'],
    'inv_valorizacion': ['inventario', 'costos'], 'inv_reposicion': ['inventario', 'compras'], 'almacenes': ['inventario'], 'almacen_nuevo': ['inventario'],
    'almacen_editar': ['inventario'],
}
# Rutas abiertas a cualquier usuario autenticado
RUTAS_LIBRES = {'home', 'tipo_cambio_api', 'ubigeos_json', 'login', 'logout', 'cambiar_clave', 'cambiar_clave_ok',
                'alertas', 'alertas_json',  # cada alerta ya se filtra por los módulos del usuario
                'login_2fa', 'recuperar', 'recuperar_confirmar', 'recuperar_listo', 'seguridad',
                'sustento_subir', 'sustento_ver'}  # los sustentos validan el módulo del documento


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


def modulos_del_usuario(user):
    """Conjunto de claves de módulos a los que el usuario tiene acceso."""
    if not user.is_authenticated:
        return set()
    if user.is_superuser:
        return set(POR_CLAVE)
    cache = getattr(user, '_modulos_cache', None)
    if cache is None:
        nombres = set(user.groups.values_list('name', flat=True))
        cache = {clave for clave, nombre in GRUPOS.items() if nombre in nombres}
        for derivado, origen in DERIVADOS.items():
            if cache & origen:
                cache.add(derivado)
        user._modulos_cache = cache
    return cache


def modulos_de_ruta(match):
    """Módulos que pueden abrir la vista resuelta; None = libre (cualquier usuario autenticado)."""
    if match is None:
        return None
    if match.namespace:
        if match.namespace == 'admin':
            return None
        return [match.namespace] if match.namespace in POR_CLAVE else None
    if match.url_name in RUTAS_LIBRES:
        return None
    return RUTAS_CORE.get(match.url_name)


def resolver_url(destino):
    nombre, _, query = destino.partition('?')
    url = reverse(nombre)
    return f'{url}?{query}' if query else url


RUTAS_COSTOS = {'inv_valorizacion', 'inventario:cierres'}  # solo con el permiso de ver costos


def menu_de(modulo, ver_costos=True):
    """Menú del módulo con las URLs ya resueltas."""
    items = []
    for etiqueta, destino in modulo['menu']:
        if isinstance(destino, list):
            items.append({'etiqueta': etiqueta, 'hijos': [
                {'etiqueta': e, 'url': resolver_url(d), 'icono': i} for e, d, i in destino
                if ver_costos or d not in RUTAS_COSTOS]})
        else:
            items.append({'etiqueta': etiqueta, 'url': resolver_url(destino)})
    return items
