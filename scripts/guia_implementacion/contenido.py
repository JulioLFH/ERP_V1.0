"""Contenido de la Guía de implementación y de su checklist (generar.py). Los menús se escriben como aparecen en el
sistema (core/modulos.py): revíselos cuando cambie el menú. Las columnas de las plantillas no van aquí: salen de
core/carga_masiva.DEFINICIONES.

Cada paso: (código, etapa, paso, dónde, qué hacer / qué debe cumplirse, requisito, responsable, plantilla)."""

PREVIO, BASICA, INTERMEDIA, COMPLETA = 'Paso previo', 'Básica', 'Intermedia', 'Completa'
ADM, IMP, CONT, SOP = 'Administrador del sistema', 'Implementador', 'Contador', 'Soporte técnico'

NIVELES = [
    (BASICA, 'Empezar a vender, comprar, controlar stock y caja',
     'Ventas, Compras, Inventario, Logística, Tesorería, Contactos', '1 a 2 semanas'),
    (INTERMEDIA, 'Contabilidad completa, costos por área, personal y gastos',
     '+ Contabilidad, Planillas, Empleados, Rendición de gastos, Activos fijos, Presupuestos, Requerimientos',
     '3 a 4 semanas'),
    (COMPLETA, 'Producción, costeo, planificación e historial',
     '+ Manufactura, Planificación (MRP), Costos, historial y contabilidad del sistema anterior, integraciones',
     '6 a 8 semanas'),
]

PRINCIPIOS = [
    ('Elija una fecha de corte', 'el último día de un mes (por ejemplo, 31/10/2026). Los saldos iniciales se cargan '
     'a esa fecha y el ERP opera desde el día siguiente. Cierre el mes en el sistema anterior antes de migrar.'),
    ('Respete el orden de carga', 'cada maestro usa a los anteriores (un producto necesita su almacén; un saldo por '
     'cobrar, a su cliente; una receta, a sus productos).'),
    ('Use siempre la plantilla del sistema', 'Ajustes › Configuración › Carga masiva (Excel) › elija el tipo › '
     'Descargar plantilla. Los encabezados pueden ser el título o la clave de la columna (Anexo A).'),
    ('Valide antes de grabar', 'al subir el archivo el sistema revisa fila por fila y no graba nada. Solo sin errores '
     'se pulsa Confirmar carga. Es todo o nada: si una fila falla no se carga ninguna.'),
    ('Actualizar existentes', 'márquelo para corregir datos ya cargados (mismo código o documento). Sin marcarlo, los '
     'repetidos se informan como error y no se duplican.'),
    ('Pruebe primero en una copia', 'cargue en la base de prueba, revise reportes y cuadres, y recién después lleve '
     'la base revisada a producción (comando trasladar_migracion, a cargo de soporte técnico).'),
    ('Cuadre cada saldo con el sistema anterior', 'stock valorizado, cuentas por cobrar y por pagar, caja y bancos '
     'deben coincidir al centavo con los reportes del sistema anterior a la fecha de corte.'),
    ('Respalde antes de cada carga grande', 'Ajustes › Configuración › Respaldo de datos. Si algo sale mal, se vuelve '
     'al respaldo en lugar de corregir a mano.'),
]

USO_CARGA = [
    'Ingrese a Ajustes › Configuración › Carga masiva (Excel) o al botón Importar desde Excel de la lista del maestro.',
    'Elija el tipo de carga (por ejemplo, Productos y servicios) y pulse Descargar plantilla.',
    'Complete el Excel: una fila por registro, sin filas en blanco intermedias. Borre la fila de ejemplo.',
    'Suba el archivo y pulse Validar archivo. La vista previa dice en cada fila Nuevo, Actualizar o el error a corregir.',
    'Corrija los errores en el Excel y vuelva a validar hasta que no quede ninguno.',
    'Pulse Confirmar carga. El sistema informa cuántos registros se crearon o actualizaron.',
    'Revise el resultado en la lista correspondiente y en los reportes de cuadre del paso.',
]

# ---------------------------------------------------------------- pasos
PASOS = {
    PREVIO: {
        'objetivo': 'Configurar la empresa antes de cargar datos. Lo hace el administrador del sistema, en pantalla.',
        'pedir': '',
        'pasos': [
            ('0.1', 'Preparación', 'Elegir la fecha de corte y cerrar el mes en el sistema anterior', 'Fuera del sistema',
             'Último día de un mes. Los saldos iniciales se cargan a esa fecha.', '', 'Gerencia y contador', None),
            ('0.2', 'Configuración', 'Datos de la empresa', 'Ajustes › Empresa › Datos de la empresa',
             'RUC, razón social, dirección, ubigeo, IGV, fuente del tipo de cambio, credenciales API SUNAT y token '
             'Decolecta (tipo de cambio SBS y consulta RUC).', '', ADM, None),
            ('0.3', 'Configuración', 'Giro, régimen y parámetros tributarios',
             'Ajustes › Empresa › Giro y régimen tributario; Parámetros tributarios',
             'Régimen tributario, UIT, tasas y umbrales del año.', '0.2', CONT, None),
            ('0.4', 'Configuración', 'Empresas del grupo (solo multiempresa)', 'Menú de usuario › Empresas del grupo',
             'Crear cada empresa (RUC y razón social) y marcar los usuarios corporativos. Cada empresa tiene su propia '
             'base: repita esta guía en cada una.', '0.2', ADM, None),
            ('0.5', 'Configuración', 'Perfiles de acceso (roles)', 'Ajustes › Usuarios y permisos › Perfiles de acceso (roles)',
             'Un perfil por puesto: módulos, acciones (anular, aprobar…), pantallas y si ve costos, sueldos y saldos.',
             '0.2', ADM, 'perfiles'),
            ('0.6', 'Configuración', 'Usuarios', 'Ajustes › Usuarios y permisos › Usuarios',
             'Un usuario por persona con su perfil, almacenes, series y tope de aprobación.', '0.5', ADM, 'usuarios'),
            ('0.7', 'Configuración', 'Segregación de funciones y reglas de aprobación',
             'Ajustes › Usuarios y permisos › Segregación de funciones; Reglas de aprobación',
             'Quién registra no aprueba ni paga lo suyo. Activar la segregación estricta si la empresa lo exige.',
             '0.6', ADM, None),
            ('0.8', 'Configuración', 'Series y correlativos', 'Ajustes › Configuración › Correlativos / series',
             'Series F001, B001, T001… con el último número usado en el sistema anterior.', '', ADM, None),
            ('0.9', 'Configuración', 'Facturación electrónica', 'Ajustes › Configuración › Facturación electrónica',
             'Conexión con el OSE/PSE. Probar con un comprobante antes de emitir en producción.', '0.8', ADM, None),
            ('0.10', 'Configuración', 'Correo saliente', 'Ajustes › Configuración › Correo saliente',
             'Servidor SMTP para comprobantes, órdenes de compra y avisos.', '', ADM, None),
            ('0.11', 'Configuración', 'Tipo de cambio', 'Ajustes › Configuración › Tipo de cambio',
             'Verificar que se actualiza solo; cargar el del día de corte si falta.', '0.2', ADM, None),
            ('0.12', 'Configuración', 'Respaldo de datos', 'Ajustes › Configuración › Respaldo de datos',
             'Respaldo automático programado y uno manual antes de cada carga grande.', '', SOP, None),
        ],
        'verificar': [],
    },
    BASICA: {
        'objetivo': 'Facturar, comprar, controlar el almacén, emitir guías, cobrar y pagar desde el día siguiente al '
                    'corte.',
        'pedir': 'Lista de almacenes, maestro de productos, maestro de clientes y proveedores, stock valorizado por '
                 'almacén, facturas pendientes de cobro y de pago, y saldos de cada caja y cuenta bancaria (con su '
                 'extracto), todo a la fecha de corte.',
        'pasos': [
            ('3.1', 'Preparación', 'Pedir la información y limpiar los datos en las plantillas', 'Fuera del sistema',
             'Una fila por registro; RUC válidos, fechas dd/mm/aaaa, importes sin símbolo de moneda.', '0.1',
             'Ventas, compras y almacén', None),
            ('3.2', 'Carga', 'Almacenes', 'Inventario › Configuración › Almacenes › Importar desde Excel',
             'Código, nombre, dirección, ubigeo, código de establecimiento SUNAT, uso y operaciones permitidas.',
             'Primero que todo lo de inventario', IMP, 'almacenes'),
            ('3.3', 'Carga', 'Productos y servicios',
             'Inventario › Configuración › Productos y servicios › Importar desde Excel',
             'Tipo, unidad, precios sin IGV, niveles de stock, control por lote o serie y si se compra o se vende. '
             'El tipo define las cuentas y el código: revíselo bien.', '3.2', IMP, 'productos'),
            ('3.4', 'Carga', 'Clientes y proveedores', 'Contactos › Clientes / Proveedores › Importar desde Excel',
             'Tipo, documento, razón social, dirección, ubigeo, correo, teléfono y días de crédito. Se valida el RUC y '
             'el DNI.', '', IMP, 'terceros'),
            ('3.5', 'Carga', 'Cajas y cuentas bancarias con su saldo inicial',
             'Tesorería › Caja y bancos › Cuentas de caja y bancos',
             'Una cuenta por caja y por banco (número, CCI, moneda) con su saldo al corte. Adjuntar extracto o acta de '
             'arqueo: el saldo inicial no se edita después.', '', 'Tesorería', None),
            ('3.6', 'Carga', 'Saldos iniciales de inventario',
             'Inventario › Operaciones › Carga masiva de saldos y productos',
             'Producto, almacén, cantidad y costo unitario sin IGV a la fecha de corte (contrapartida 5911).',
             '3.2 y 3.3', IMP, 'saldos'),
            ('3.7', 'Carga', 'Saldos iniciales por cobrar', 'Ajustes › Configuración › Carga masiva (Excel)',
             'Cada comprobante de venta pendiente con su saldo con IGV. No van al registro de ventas ni a SUNAT.',
             '3.4', IMP, 'saldos_cxc'),
            ('3.8', 'Carga', 'Saldos iniciales por pagar', 'Ajustes › Configuración › Carga masiva (Excel)',
             'Cada comprobante de compra pendiente con su saldo. Se pagan normalmente desde Tesorería.', '3.4', IMP,
             'saldos_cxp'),
            ('3.9', 'Verificación', 'Stock valorizado por almacén', 'Inventario › Reportes › Valorización al cierre',
             'Igual al inventario valorizado del sistema anterior al corte, al centavo.', '3.6',
             'Implementador y almacén', None),
            ('3.10', 'Verificación', 'Cuentas por cobrar', 'Ventas › Cuentas por cobrar',
             'Igual a la antigüedad de saldos de clientes del sistema anterior.', '3.7', 'Implementador y tesorería',
             None),
            ('3.11', 'Verificación', 'Cuentas por pagar', 'Compras › Cuentas por pagar',
             'Igual a la antigüedad de saldos de proveedores.', '3.8', 'Implementador y tesorería', None),
            ('3.12', 'Verificación', 'Caja y bancos', 'Tesorería › Caja y bancos › Cuentas de caja y bancos',
             'Igual al arqueo de caja y a los extractos bancarios al corte.', '3.5', 'Tesorería', None),
            ('3.13', 'Verificación', 'Datos que no pasan las validaciones', 'Comando revisar_datos (soporte técnico)',
             'Sin observaciones: tarifas negativas, vencimientos antes de la emisión, nombres con HTML, datos de prueba '
             'antiguos o migrados que no cuadran.', '3.2 a 3.8', SOP, None),
            ('3.14', 'Verificación', 'Prueba de punta a punta', 'Ventas, Compras, Inventario y Tesorería',
             'Emitir una factura de prueba, registrar una compra, un ingreso y una salida de almacén, un cobro y un pago.',
             '3.9 a 3.13', 'Implementador y jefes de área', None),
            ('3.15', 'Arranque', 'Capacitación de usuarios', 'Ayuda › Manuales de usuario',
             'Ventas, compras, almacén y tesorería capacitados en su operación diaria.', '3.14', IMP, None),
            ('3.16', 'Arranque', 'Arranque en producción (día siguiente al corte)', 'Base de producción',
             'Soporte técnico traslada la base revisada (trasladar_migracion). Desde aquí todo se registra en Ceiba.',
             '3.14 y 3.15', 'Todos', None),
        ],
        'nota': 'Con la migración básica la empresa ya opera. La contabilidad se genera sola (centralización '
                'automática) con las cuentas del plan contable general empresarial; la intermedia la deja a la medida.',
    },
    INTERMEDIA: {
        'objetivo': 'Contabilidad a la medida de la empresa (estados financieros oficiales desde el ERP), gasto por '
                    'centro de costo y línea de negocio, precios por tipo de cliente, planilla, activos y rendiciones.',
        'pedir': 'Plan de cuentas con destinos, balance de comprobación al corte, centros de costo y líneas de negocio, '
                 'listas de precios, la última planilla (trabajadores, sueldos, AFP), registro de activos fijos con su '
                 'depreciación acumulada y políticas de viáticos y caja chica.',
        'pasos': [
            ('4.1', 'Carga', 'Plan de cuentas', 'Contabilidad › Configuración › Plan de cuentas › Importar desde Excel',
             'Solo las subcuentas propias (el PCGE ya viene) o el plan completo con «Actualizar». Naturaleza, '
             'imputable, destinos 90/94/95/97 y 79, moneda, análisis por documento y regla de centro de costo.',
             'Básica completa', CONT, 'cuentas'),
            ('4.2', 'Carga', 'Centros de beneficio (líneas de negocio)',
             'Contabilidad › Configuración › Centros de beneficio (líneas)', 'Código, nombre y responsable.', '',
             CONT, 'centros_beneficio'),
            ('4.3', 'Carga', 'Centros de costo', 'Contabilidad › Configuración › Centros de costo',
             'Tipo, centro del que depende, centro de beneficio y vigencia (vacío = desde hoy hasta 31/12/9999). '
             'Superiores primero.', '4.2', CONT, 'centros_costo'),
            ('4.4', 'Carga', 'Cuentas por operación y por tipo de producto',
             'Contabilidad › Configuración › Cuentas por operación; Cuentas por tipo de producto',
             'Revisar las cuentas sugeridas de cada operación automática y de cada tipo de producto.', '4.1', CONT,
             None),
            ('4.5', 'Carga', 'Asiento de apertura del resto del balance', 'Contabilidad › Asientos › Nuevo asiento manual',
             'Activo fijo, tributos, remuneraciones, préstamos, capital, reservas y resultados contra la 5911. Caja, '
             'inventario, cobrar y pagar ya entraron contra la 5911 en la básica.', '4.1', CONT, None),
            ('4.6', 'Carga', 'Listas de precios', 'Ventas › Ventas › Listas de precios',
             'Lista, producto, precio sin IGV, tramo y descuento (hasta 30,000 filas). Luego asignar la lista a cada '
             'cliente.', 'Productos (3.3)', 'Ventas', 'listas_precios'),
            ('4.7', 'Carga', 'Parámetros de planilla', 'Planillas › Configuración › Parámetros, AFP y conceptos',
             'RMV, UIT, AFP y conceptos con sus cuentas.', '4.1', 'RR. HH.', None),
            ('4.8', 'Carga', 'Trabajadores', 'Empleados › Personal › Cargar trabajadores (Excel)',
             'Documento, nombres, ingreso, cargo, régimen, centro de costo, sueldo, AFP, banco y quinta previa.',
             '4.3 y 4.7', 'RR. HH.', 'trabajadores'),
            ('4.9', 'Carga', 'Categorías de activos fijos', 'Activos fijos › Configuración › Categorías y cuentas',
             'Categorías con sus cuentas, tasas y vida útil.', '4.1', CONT, None),
            ('4.10', 'Carga', 'Activos fijos (saldo inicial)', 'Activos fijos › Activos › Carga masiva de activos (Excel)',
             'Cada activo con valor, fecha, centro de costo y depreciación acumulada al corte. La depreciación sigue '
             'desde el mes siguiente.', '4.3 y 4.9', CONT, 'activos'),
            ('4.11', 'Carga', 'Rendición de gastos',
             'Rendición de gastos › Configuración › Cargar categorías (Excel); Categorías, políticas y aprobación',
             'Categorías con cuenta y topes, políticas por cargo y reglas de aprobación. Entregas a rendir y cajas '
             'chicas abiertas al corte en Tesorería › Caja y bancos › Entregas a rendir y caja chica.', '4.1 y 4.3',
             'Contabilidad y tesorería', 'categorias_gasto'),
            ('4.12', 'Carga', 'Presupuesto (opcional)', 'Presupuestos › Presupuestos y ejecución',
             'Presupuesto anual por cuenta y centro de costo para comparar con lo real.', '4.1 y 4.3', CONT, None),
            ('4.13', 'Verificación', 'Balance de comprobación al corte', 'Contabilidad › Reportes › Balance de comprobación',
             'Igual al del sistema anterior y la 5911 en cero.', '4.5', CONT, None),
            ('4.14', 'Verificación', 'Estado de situación financiera',
             'Contabilidad › Reportes › Estado de situación financiera', 'Activo = pasivo + patrimonio.', '4.13', CONT,
             None),
            ('4.15', 'Verificación', 'Gastos por centro de costo', 'Contabilidad › Reportes › Gastos por centro de costo',
             'Sin gastos 62-65 y 68 sin centro de costo.', '4.3', CONT, None),
            ('4.16', 'Verificación', 'Cuadre de activos fijos', 'Activos fijos › Reportes › Cuadre con contabilidad',
             'Costo y depreciación acumulada iguales a las cuentas 33 y 39.', '4.10', CONT, None),
            ('4.17', 'Verificación', 'Planilla de prueba', 'Planillas › Planillas › Nueva planilla',
             'Neto, AFP/ONP y EsSalud iguales a la última planilla del sistema anterior.', '4.8', 'RR. HH.', None),
            ('4.18', 'Verificación', 'Rendición de prueba', 'Rendición de gastos › Rendir › Nueva rendición',
             'Enviar, aprobar y contabilizar una rendición de prueba.', '4.11', 'Contabilidad', None),
        ],
    },
    COMPLETA: {
        'objetivo': 'Fabricar con recetas y rutas, costear la producción (estándar y real, NIC 2), planificar '
                    'materiales (MRP) y tener a mano el historial del sistema anterior.',
        'pedir': 'Puestos y tarifas, recetas, rutas, órdenes en proceso al corte, plan de demanda y los Excel del '
                 'sistema anterior. Orden obligatorio: centros de beneficio → centros de costo → puestos → listas de '
                 'materiales → hojas de ruta → versiones de fabricación.',
        'pasos': [
            ('5.1', 'Carga', 'Puestos de trabajo', 'Manufactura › Ingeniería › Puestos de trabajo',
             'Tarifas de mano de obra, máquina y CIF por hora (no negativas), centro de costo, turnos, días y eficiencia.',
             'Intermedia completa (4.3)', 'Producción', 'puestos'),
            ('5.2', 'Carga', 'Listas de materiales (recetas)', 'Manufactura › Ingeniería › Listas de materiales (recetas)',
             'Una fila por insumo: producto, receta, rinde, insumo, cantidad, merma y operación.', 'Productos (3.3)',
             'Producción', 'recetas'),
            ('5.3', 'Carga', 'Hojas de ruta', 'Manufactura › Ingeniería › Hojas de ruta',
             'Una fila por operación: secuencia, puesto, preparación, por unidad y espera.', '5.1', 'Producción',
             'hojas_ruta'),
            ('5.4', 'Carga', 'Versiones de fabricación', 'Manufactura › Ingeniería › Versiones de fabricación',
             'Receta + hoja de ruta por rango de lote y vigencia; lote de costeo y plazo. Sin dos versiones activas con '
             'el mismo rango de lote y fechas cruzadas.', '5.2 y 5.3', 'Producción', 'versiones'),
            ('5.5', 'Carga', 'Motivos de parada (opcional, OEE)', 'Manufactura › Ingeniería › Motivos de parada (OEE)',
             'Motivos con la pérdida del OEE, tipo y área responsable.', '5.1', 'Producción', 'motivos_parada'),
            ('5.6', 'Carga', 'Planificación de cada producto', 'Inventario › Configuración › Productos y servicios',
             'Tipo de planificación (MRP, punto de reorden, sin planificar), estrategia MTS o MTO, stock mínimo, lote, '
             'plazo y proveedor (columnas de la carga de productos).', '5.4', 'Producción y compras', None),
            ('5.7', 'Carga', 'Plan de demanda', 'Planificación (MRP) › Demanda › Pronóstico de demanda',
             'Producto, cantidad y fecha en que se necesita (productos para stock).', '5.6', 'Producción y ventas',
             'plan_demanda'),
            ('5.8', 'Carga', 'Costo estándar', 'Costos › Costo estándar del periodo',
             'Calcular y liberar el costo estándar del mes (materiales, mano de obra, máquina y CIF).',
             '5.4 y precios de compra', 'Costos', None),
            ('5.9', 'Carga', 'Órdenes de producción abiertas', 'Manufactura › Producción › Nueva orden de producción',
             'Las órdenes en proceso al corte, por lo que falta producir (o el comando cargar_manufactura).', '5.4',
             'Producción', None),
            ('5.10', 'Historial', 'Migración inicial desde Excel (migrar_excels)', 'Comando (soporte técnico)',
             'Productos, contactos, plan de cuentas, centros, stock (también por lote) y por cobrar a la fecha de corte. '
             'Revisar el Excel de observaciones.', '', SOP, None),
            ('5.11', 'Historial', 'Historial de ventas (importar_historial_ventas)', 'Comando (soporte técnico)',
             'Solo consulta: no va a SUNAT, ni mueve stock ni contabiliza.', '', SOP, None),
            ('5.12', 'Historial', 'Documentos anteriores (importar_anteriores)', 'Comando (soporte técnico)',
             'Órdenes de compra, pedidos, cotizaciones, órdenes de fabricación, kardex, asientos y posiciones '
             'presupuestarias (consulta).', '', SOP, None),
            ('5.13', 'Historial', 'Contabilidad anterior (contabilidad_anterior)', 'Comando (soporte técnico)',
             'Asientos hasta el corte con el ajuste de migración y cierre de esos periodos.', '4.1',
             'Soporte técnico y contador', None),
            ('5.14', 'Integraciones', 'API SUNAT y consulta RUC (opcional)', 'Ajustes › Empresa › Datos de la empresa',
             'Validación de comprobantes y consulta RUC.', '', ADM, None),
            ('5.15', 'Integraciones', 'Rendiciones: app, IA, WhatsApp y correo (opcional)',
             'Rendición de gastos › Configuración › Integraciones (WhatsApp, correo, IA)', '', '', ADM, None),
            ('5.16', 'Integraciones', 'Ingreso con Microsoft / Google y API REST (opcional)',
             'Ajustes › Usuarios y permisos › Ingreso con Microsoft / Google; Menú de usuario › Claves de API', '', '', SOP,
             None),
            ('5.17', 'Verificación', 'Explosión de una orden de prueba', 'Manufactura › Producción › Nueva orden de producción',
             'Insumos y horas iguales a la receta y la ruta.', '5.4', 'Producción', None),
            ('5.18', 'Verificación', 'Costo estándar', 'Costos › Costo estándar del periodo',
             'Cada producto fabricado con costo; sin insumos sin precio.', '5.8', 'Costos', None),
            ('5.19', 'Verificación', 'MRP de prueba', 'Planificación (MRP) › MRP: necesidades y órdenes planificadas',
             'Propone fabricar y comprar lo que falta según pedidos y plan de demanda.', '5.7', 'Producción', None),
            ('5.20', 'Verificación', 'Contabilidad anterior', 'Contabilidad › Sistema anterior › Conciliación de la migración',
             'Sin diferencias abiertas.', '5.13', CONT, None),
            ('5.21', 'Cierre', 'Cierre del primer mes en el ERP', 'Contabilidad › Asientos › Cierre guiado del mes',
             'Centralizar, revisar errores y cerrar el primer mes completo desde Ceiba.', 'Todo lo anterior', CONT,
             None),
        ],
    },
}

CRONOGRAMA = [
    ('1', 'Configuración inicial (paso previo), perfiles, usuarios y series. Elegir la fecha de corte', ADM),
    ('1-2', 'Limpieza de datos en Excel: productos, contactos (RUC válidos), almacenes', 'Ventas, compras, almacén'),
    ('2', 'Carga básica en la copia de prueba y cuadre de saldos', 'Implementador y tesorería'),
    ('2', 'Capacitación de usuarios y prueba de punta a punta', 'Implementador y jefes de área'),
    ('2-3', 'Arranque básico en producción (día siguiente al corte)', 'Todos'),
    ('3-4', 'Plan de cuentas, centros, cuentas por operación y asiento de apertura', CONT),
    ('4', 'Planilla, activos, rendiciones y listas de precios', 'RR. HH., contabilidad, ventas'),
    ('5-7', 'Puestos, recetas, hojas de ruta, versiones, costo estándar y MRP', 'Producción y costos'),
    ('7-8', 'Historial y contabilidad del sistema anterior, integraciones y cierre del primer mes en el ERP',
     'Soporte técnico y contador'),
]

ERRORES = [
    ('«Faltan las columnas…»', 'Encabezados distintos a la plantilla',
     'Descargue la plantilla y copie los datos debajo de sus encabezados'),
    ('«El código … ya existe»', 'Registro cargado antes', 'Marque «Actualizar existentes» o quite la fila'),
    ('RUC inválido', 'Dígito verificador incorrecto o dígitos mal digitados', 'Corrija con la ficha RUC de SUNAT'),
    ('«El producto … no existe»', 'Se cargó el saldo o la receta antes que el producto', 'Respete el orden: productos '
     'primero'),
    ('Fechas o importes mal leídos', 'Celdas con texto o separadores distintos',
     'Fechas dd/mm/aaaa; importes sin símbolo de moneda; punto decimal'),
    ('El stock no cuadra', 'Costos con IGV o en unidades de compra', 'Costo unitario sin IGV y en la unidad de almacén'),
    ('Periodo cerrado', 'Se registra en un mes ya cerrado', 'Use fechas posteriores al corte o pida reabrir el periodo'),
    ('«Se solapa con otra versión activa»', 'Dos versiones con el mismo rango de lote y fechas cruzadas',
     'Cambie el rango de lote o la vigencia, o desactive la anterior'),
]

# plantillas del Anexo A por nivel (claves de core/carga_masiva.DEFINICIONES)
ANEXO = [
    (PREVIO, ['perfiles', 'usuarios']),
    (BASICA, ['almacenes', 'productos', 'terceros', 'saldos', 'saldos_cxc', 'saldos_cxp']),
    (INTERMEDIA, ['cuentas', 'centros_beneficio', 'centros_costo', 'listas_precios', 'trabajadores', 'activos',
                  'categorias_gasto']),
    (COMPLETA, ['puestos', 'recetas', 'hojas_ruta', 'versiones', 'motivos_parada', 'plan_demanda']),
]
