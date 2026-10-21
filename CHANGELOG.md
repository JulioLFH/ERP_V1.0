# Historial de versiones — Ceiba ERP

Cada versión es una "etiqueta" (tag) en GitHub: https://github.com/JulioLFH/ERP_V1.0/tags
El número de versión instalada se ve en el sistema, en el menú del usuario (arriba a la derecha).

## v1.52.0 — 21/10/2026 · Datos de la empresa desde SUNAT
- **Ajustes › Empresa**: al escribir el RUC (o con el botón «Traer datos de SUNAT») el sistema consulta el padrón RUC de SUNAT y completa la **razón social**, la **dirección** y el **departamento, provincia y distrito** (ubigeo). Además muestra la ficha completa de SUNAT: estado y condición (avisa si no está activo o no habido), tipo de contribuyente, actividad económica, domicilio fiscal, sistema de emisión y de contabilidad, comercio exterior, número de trabajadores, si es agente de retención o buen contribuyente y sus locales anexos (si el plan de Decolecta no incluye la consulta completa, se usa la básica). Usa el token de Decolecta de la misma pantalla (el del tipo de cambio SBS; cuenta gratuita en decolecta.com); el token viaja en el cuerpo de la consulta, no en la dirección. SUNAT no publica el correo ni el teléfono: esos se ingresan a mano.
- **Clientes y proveedores** (Contactos › Nuevo / Editar): el mismo botón «Traer datos de SUNAT» junto al número de documento (con tipo RUC): llena nombre o razón social, dirección y distrito, muestra la ficha de SUNAT y avisa si el RUC no está activo o no habido y si **ya está registrado** como cliente o proveedor (con el enlace a su ficha). Usa el token de Decolecta de Ajustes › Empresa.
- **Dirección y correo obligatorios** en los datos de la empresa, y el RUC se valida (11 dígitos y dígito verificador).

## v1.51.0 — 21/10/2026 · Detracciones y guías de remisión directo con SUNAT
- **Detracciones en SUNAT directo**: las facturas con detracción ya van directo (antes solo por Nubefact). El XML lleva la operación sujeta a detracción (1001), la leyenda, la **cuenta de detracciones del Banco de la Nación** (Finanzas › Cuentas, la marcada como «Cuenta de detracciones»), el código del bien o servicio (catálogo 54), el porcentaje y el monto **en soles** aunque la factura sea en dólares; al crédito, las cuotas informan el neto (total − detracción − retención). Si falta la cuenta del Banco de la Nación, el sistema avisa antes de enviar.
- **Retención del IGV** (el cliente es agente de retención) y **operaciones gratuitas** (bonificaciones: valor referencial, leyenda «transferencia gratuita», total cero) también van directo. Probado contra el beta real de SUNAT: detracción al contado, al crédito, en dólares y de transporte de carga, retención al crédito, factura y boleta gratuitas, todas aceptadas.
- **Guías de remisión directo con SUNAT**: remitente (serie T) y transportista (serie V) por la API REST de SUNAT: XML UBL 2.1 (versión 2022) firmado con el certificado de la empresa, token con el usuario SOL secundario y las credenciales de API (client_id y client_secret de «GRE Emisión de Comprobantes», nuevos campos en Ajustes › Facturación electrónica, el secreto cifrado), envío con **ticket** y constancia (CDR) con el **enlace del QR**, que sale en la guía impresa. Si SUNAT demora, «Consultar estado» usa el ticket. Incluye transporte público (empresa de transporte con su registro MTC) y privado (vehículo y conductor), traslado entre establecimientos (código del anexo), documento relacionado y, en la guía transportista, el registro MTC de la empresa, el remitente y la TUCE del vehículo.
- SUNAT **no tiene entorno de pruebas para guías**: en beta el sistema genera y firma el XML (queda adjunto para revisarlo) sin enviarlo; el envío real se verifica en producción.
- **Lista «Para emitir en producción»** en Ajustes › Facturación electrónica: RUC, dirección y ubigeo, certificado real y vigente (avisa 30 días antes de vencer), usuario SOL, series F/B/T, credenciales de guías, cuenta de detracciones y registro MTC, cada uno con su estado. «Probar conexión» también prueba las credenciales de guías en producción.
- Aún no van directo: percepciones e ICBPER (bolsas plásticas).

## v1.50.0 — 20/10/2026 · Primera instalación y Panel del grupo
- **Primera instalación**: si el sistema no tiene ningún usuario, la pantalla de ingreso lleva a un asistente para registrar la empresa principal (razón social, RUC validado y dirección) y crear el administrador del sistema (con la política de contraseñas), que entra directo al Panel del grupo. Por seguridad solo se abre desde el mismo equipo del servidor o con la clave de instalación (variable de entorno `CLAVE_INSTALACION`, en `/instalacion/?clave=…`), y deja de existir apenas hay un usuario.
- **Panel del grupo** (menú de usuario › Panel del grupo): una tarjeta por empresa con RUC, usuarios activos, ventas del mes, última venta y lo que le falta configurar (series, correo, facturación electrónica, dirección), con el botón para entrar a cada una sin volver a iniciar sesión; la disponibilidad del servicio; **crear empresa** y **crear usuarios corporativos** ahí mismo (con sus empresas y si pueden crear empresas; esto último solo el administrador de la principal).
- Instalación sin segunda empresa (`PG_BASE_EMPRESA2=-` en `.env.postgres`) y cookie de sesión propia por instalación (`PG_COOKIE`): varias copias del sistema funcionan a la vez en el mismo equipo sin cruzar sesiones.

## v1.49.0 — 20/10/2026 · Usuario corporativo y empresas del grupo
- **Usuario corporativo (n.º 54)**: un usuario de la empresa principal maneja varias empresas del grupo con **un solo ingreso**: desde el menú de usuario pasa a otra empresa **sin cerrar sesión** (antes cambiar de empresa obligaba a volver a ingresar). Los administradores de la principal son corporativos de todas las empresas; a otros usuarios se les asigna en Empresas del grupo: todas o algunas empresas, y si pueden crear empresas. En cada empresa actúa con su mismo nombre de usuario: si allí no existe se crea como administrador de esa empresa sin contraseña propia (solo se entra por el grupo); si ya existe, usa sus permisos de esa empresa. Quien no es corporativo sigue ingresando a cada empresa con su usuario.
- **Crear empresas desde el sistema** (menú de usuario › Empresas del grupo): con razón social y RUC (validado y sin repetir en el grupo) el sistema crea la base de datos en el mismo servidor PostgreSQL, sus tablas y el plan contable, la ficha de la empresa y deja al usuario corporativo como administrador. Corre en segundo plano (alrededor de un minuto; la página muestra el avance) y, si falla, se puede reintentar. La empresa queda registrada en la base principal: el sistema la reconoce solo, sin editar la configuración ni reiniciar.
- El ingreso corporativo cuenta como verificado: al pasar a otra empresa no se repite la verificación en dos pasos.

## v1.48.0 — 20/10/2026 · Ingreso con Microsoft / Google y estado del servicio
- **Ingreso con Microsoft / Google** (inicio de sesión único con la cuenta de la empresa) con Microsoft (Office 365 / Entra ID), Google Workspace u otro proveedor OpenID Connect, en Ajustes › Usuarios y permisos (solo administradores). En la pantalla de ingreso aparece el botón «Ingresar con Microsoft / Google». El usuario se reconoce por su **correo**, que debe ser el de un usuario activo del sistema; los módulos y permisos siguen siendo los del sistema. Se puede limitar a los dominios de la empresa. Seguridad: código de autorización con PKCE, firma del token verificada con las llaves públicas del proveedor, emisor, audiencia, vigencia y nonce; el secreto se guarda cifrado. Quien entra así no repite la verificación en dos pasos del sistema (la exige su proveedor). Cada empresa del grupo tiene su propio proveedor.
- **Estado del servicio (n.º 57)**: página pública **/estado/** (sin datos de la empresa) con el estado de la base de datos, el espacio en disco, el respaldo diario, el correo y la facturación electrónica; la **disponibilidad de los últimos 30 y 90 días** frente a la meta de 99.9%, una barra por día y las caídas con su duración. Se alimenta con el nuevo comando `chequear_servicio` (con `--avisar` manda un correo a los administradores si hay caída) y con el monitor externo que consulta /salud/. `scripts/programar_chequeo.ps1` lo programa cada 5 minutos en Windows.

## v1.47.0 — 20/10/2026 · Flujo proyectado, pronóstico de demanda, consolidación y formularios más rápidos
- **Formularios de venta, compra y almacén más rápidos (n.º 83)**: ya no descargan el catálogo completo de productos (más de 1 MB) al abrirse; solo viajan los productos del documento y el resto se pide uno a uno al elegirlo (precio, unidad e IGV; el costo solo para quien puede verlo).
- **Flujo de caja proyectado (n.º 61)** en Finanzas › Reportes: saldo de hoy de caja y bancos y lo que se espera cobrar y pagar por semana (4 a 26 semanas): cobranzas vencidas y por vencer, letras, pedidos aprobados por facturar, pagos a proveedores, órdenes de compra aprobadas, cuotas de préstamos y leasing, y la planilla estimada. Avisa la semana en que el saldo quedaría en negativo, con el detalle de documentos de cada concepto y exportación a Excel. Respeta los permisos de saldos y de cuentas.
- **Pronóstico de demanda (n.º 65)** en Manufactura › Planificación: con las ventas de los últimos 24 meses prueba promedio móvil, suavizamiento exponencial, Holt (con tendencia) y estacional, y elige por producto el que menos se equivocó en los últimos 3 meses (con su error %). Lo marcado pasa al plan de demanda que usa el MRP; un nuevo pase reemplaza el anterior.
- **Consolidación del grupo (n.º 53)**: ahora elimina la **utilidad no realizada** en inventarios: el margen de la empresa que vendió mercadería que la compradora del grupo aún tiene al cierre (por código de producto), con su detalle.
- **Verificado con pruebas**: el MRP propone comprar la mercadería de reventa (n.º 77) con el proveedor y plazo del registro info, y las órdenes planificadas se convierten en bloque en una solicitud de pedido (n.º 82). Las planillas (n.º 50 a 52) ya tenían sus pruebas; falta cargar la fecha de ingreso y el sueldo de los trabajadores.

## v1.46.0 — 20/10/2026 · Facturación electrónica directa con SUNAT (sin intermediario)
- **SUNAT directo** en Ajustes › Facturación electrónica (nueva opción junto a Nubefact): el sistema arma el XML UBL 2.1 de **facturas, boletas, notas de crédito y notas de débito**, lo **firma con el certificado digital** de la empresa y lo envía al servicio de SUNAT, que responde al momento con la **CDR** (aceptado, rechazado u observado). El XML firmado y la CDR quedan como sustento del comprobante, con su código hash y la cadena del QR.
- **Anulaciones directas**: comunicación de baja para facturas y sus notas, resumen diario (estado anulado) para boletas y sus notas, con ticket que se consulta solo (y con «Consultar estado» si SUNAT demora).
- **Entorno de pruebas (beta) de SUNAT**: sin clave SOL (RUC + MODDATOS) y con un **certificado de prueba** que se genera con un botón. Lo enviado no tiene validez tributaria. Se probó contra el beta real: factura, boleta, notas de crédito y débito de ambas, factura al crédito en dólares, baja de factura y resumen de boleta, todos aceptados.
- **Producción**: certificado digital de la empresa (.pfx) con su clave, usuario SOL secundario y su clave. **Las claves se guardan cifradas.** No deja pasar a producción con el certificado de prueba.
- Aún van por Nubefact (o se emiten en SUNAT): guías de remisión electrónicas (SUNAT usa otra API para ellas), detracciones, percepciones, ICBPER y operaciones gratuitas; el sistema avisa antes de enviar.
- Nuevas librerías: signxml, lxml y cryptography (requirements.txt).

## v1.45.0 — 20/10/2026 · Proveedores por producto, redondeo de facturas, reparto por centro de costo y fecha contable
- **Proveedores por producto (registro info, n.º 103)** en Compras › Abastecimiento: precio acordado y su vigencia, plazo de entrega, lote mínimo, código del proveedor y **proveedor preferido** (uno por producto). El último precio, la fecha y el número de compras se actualizan solos con cada compra registrada. El **MRP** (también para la mercadería de reventa), el punto de reorden y la solicitud de pedido toman de aquí el proveedor, el plazo y el lote; sin registro, los de la ficha del producto.
- **Diferencia pequeña en la factura del proveedor (n.º 104)**: al registrar desde el XML o aprobar la factura del portal, se registra **el total del proveedor** (el de SUNAT) cuando la diferencia con el calculado por líneas está dentro de la tolerancia, y esa diferencia va a su propia cuenta en el asiento (pérdida 6599 / ganancia 7599, configurables) en vez de mezclarse con el gasto. Se muestra en el comprobante.
- **Una factura en varios centros de costo (n.º 97)**: botón «Centros de costo» en la compra para repartir el gasto por porcentajes (deben sumar 100%); el asiento lleva una línea por centro. No se cambia en un periodo cerrado.
- **Fecha contable y fecha del documento (n.º 90)**: las operaciones de almacén tienen «Fecha contable» (kardex y contabilidad) y «Fecha del documento» (la de la guía o el acta, si es distinta). Al terminar una orden de producción se puede indicar una fecha contable distinta a la de término: kardex, costos y asientos usan la contable y la fecha y hora reales quedan registradas.

## v1.44.0 — 20/10/2026 · SIRE conectado con SUNAT: propuestas de ventas y compras
- **Propuestas del SIRE por la API de SUNAT** (Ventas › Reportes y Compras › Reportes › «SIRE: propuesta de SUNAT»): se elige el registro (ventas RVIE o compras RCE) y el periodo, se ingresa la clave SOL y el sistema pide la propuesta a SUNAT, espera su ticket y descarga el archivo. Si SUNAT aún lo está preparando, queda «Solicitada» con el botón «Consultar a SUNAT de nuevo». El archivo original queda guardado como sustento.
- **Cruce y creación de lo que falta**: coincidencias, diferencias de importe, lo que está solo en SUNAT y lo que está solo en el sistema, exportable a Excel. Los comprobantes que faltan se marcan y se **crean de un golpe** con los datos de SUNAT (proveedor o cliente nuevo incluido). Las ventas así creadas quedan como ya informadas a SUNAT (emitidas fuera del sistema): no se reenvían al OSE ni mueven stock.
- **Aceptar la propuesta en SUNAT** desde el sistema (solo con el permiso de cerrar periodos contables), con su ticket y registro en la auditoría.
- **Sin credenciales**, la misma pantalla acepta el archivo descargado del portal SIRE (también para ventas, que antes no tenía cruce).
- Credenciales en Ajustes › Empresa: client_id, client_secret y usuario SOL del SIRE. **La clave SOL no se guarda**: se pide al traer o aceptar la propuesta y la sesión con SUNAT dura lo que SUNAT indique (alrededor de una hora).
- Implementado según los manuales oficiales de la API del SIRE (Ventas v31 y Compras v28).

## v1.43.0 — 20/10/2026 · Seguridad de acceso: permisos por pantalla, perfiles, contraseñas y doble factor
- **Permisos por pantalla (n.º 24)**: en Ajustes › Usuarios cada opción del menú puede quedar **oculta** (no aparece y no se abre), **solo lectura** (consulta sin registrar, modificar ni exportar) o **sin exportar** (trabaja normal pero no descarga Excel, CSV ni PDF). Aplica también a las páginas de detalle de cada pantalla y se valida en el servidor.
- **Datos sensibles**: «Ocultar sueldos» (no abre planillas, boletas, PLAME, AFP, costo por centro ni movimientos de personal; en la lista y la ficha del trabajador no ve el sueldo ni lo exporta) y «Ocultar saldos de caja y bancos» (ve sus cuentas sin el saldo; no abre flujo de caja, conciliación ni extractos).
- **Filtros por dato**: un usuario puede limitarse a ciertos **centros de costo** (trabajadores y filas de planilla, con sus subcentros), a ciertas **cuentas de caja y bancos** (cuentas y movimientos) y a las ventas de **un vendedor** (comprobantes y pedidos).
- **Perfiles de acceso (roles)** en Ajustes › Perfiles de acceso: módulos, acciones, pantallas, costos, sueldos, saldos y límite de aprobación definidos una vez («Asistente de compras», «Jefe de almacén»…); se eligen en el usuario y, al cambiar el perfil, se actualizan todos los usuarios que lo tienen.
- **Política de contraseñas (n.º 28)** en Ajustes › Empresa: largo mínimo (8), mayúsculas y minúsculas, número, símbolo, **no repetir las últimas N** y **vencimiento** cada N días (al vencer, el sistema pide cambiarla antes de seguir). Los intentos fallidos antes del bloqueo también se configuran (5 por defecto).
- **Verificación en dos pasos obligatoria (n.º 27)**: opcional, obligatoria para administradores y aprobadores (quien tenga acciones sensibles, límite de aprobación o reglas de aprobación) u obligatoria para todos; quien deba tenerla es llevado a activarla antes de usar el sistema.
- **Cierre de sesión por inactividad** (30 minutos por defecto, configurable) y **aviso por correo del ingreso desde un equipo o navegador nuevo**.
- Los administradores ven una alerta si la segregación de funciones no está en modo estricto o si la verificación en dos pasos es opcional.

## v1.42.0 — 20/10/2026 · Motor único de aprobación, estructura organizativa y personal
- **Motor único de aprobación (n.º 33)** en Ajustes › Reglas de aprobación: para órdenes de compra, solicitudes de pedido, requerimientos, rendiciones, vacaciones y movimientos de personal, por nivel (1 → 2 → 3), con aprobador = jefe del solicitante, responsable del centro de costo, usuario, ocupante de un puesto o jefe N niveles arriba; por monto, centro de costo y tipo (urgente, activo, servicio, material…). Nadie aprueba lo suyo. Historial de cada aprobación, rechazo y reasignación. Sin reglas, todo funciona como antes. Las reglas de liberación de la SolPed pasaron a este motor.
- **Órdenes de compra por niveles (n.º 25)**: aprobación en cadena con historial; **rechazo con motivo** (vuelve al comprador); si se edita, la aprobación empieza de nuevo. Sin reglas sigue el límite por usuario.
- **Requerimientos (n.º 26)**: los aprueba el jefe del solicitante o el responsable del centro, por monto valorizado, aunque no tenga el permiso general; el aprobador los ve en su lista.
- **«Mis aprobaciones»** (menú del usuario): todo lo que le toca aprobar hoy, de cualquier módulo, aprobando o rechazando ahí mismo.
- **Suplencias y escalamiento (n.º 34)**: cada usuario delega sus aprobaciones por un periodo; si un nivel pasa los días de escalamiento de la empresa (3 por defecto) sin respuesta, también lo aprueba el jefe del aprobador. Aplica también a las rendiciones.
- **Estructura organizativa (n.º 32)**: unidades (gerencia → área → sección) y puestos con su centro de costo y su puesto superior; **organigrama** con ocupantes y vacantes. El jefe de un trabajador es quien ocupa el puesto superior (si está vacante, el siguiente).
- **Usuario ↔ trabajador (n.º 31)**: relación 1 a 1 desde la ficha del trabajador o desde Ajustes › Usuarios («Trabajador de la planilla»). Al registrar el **cese** se desactiva el usuario, se le quitan módulos y permisos y sus aprobaciones pendientes pasan a su jefe.
- **Movimientos de personal (n.º 35)**: alta, cambio de puesto, de remuneración o de centro de costo y cese, con fecha efectiva, motivo, aprobación e historial (valores anteriores). Se aplican a la ficha al aprobarse o en su fecha; la planilla de cada periodo usa la **remuneración vigente** en ese periodo. En la ficha, sueldo, puesto, centro de costo, cargo y cese ya no se editan directamente.
- **Contratos y legajo (n.º 36)**: contratos por modalidad con vencimiento y renovaciones (alerta 30 días antes), legajo digital (DNI, contrato, antecedentes, EMO…) con archivo y vencimiento, y **Excel con los datos para el T-Registro** (altas, bajas y vigentes).
- **Portal del trabajador (n.º 37)**: «Mi portal» con sus boletas, solicitud de vacaciones y permisos (las aprueba su jefe según la jerarquía; solo las aprobadas entran a la planilla), días pendientes y sus datos de contacto.
- **Sustentos**: ahora se pueden adjuntar en el documento de inventario físico (faltaba), en la solicitud de pedido, en los movimientos de personal y en el legajo.

## v1.41.0 — 20/10/2026 · Correcciones del piloto (hoja de 42 puntos): pendientes y completar lo parcial
- **Saldos negativos en el kardex (n.º 12)**: nuevo reporte en Inventario › Reportes masivos con el producto, el almacén, desde cuándo quedó en negativo, el movimiento que lo dejó así, el peor saldo y el saldo de hoy, para regularizar antes del cierre.
- **Aviso general en los formularios de comprobantes (n.º 13)**: arriba aparece «Revise los campos marcados» con la lista de los campos y su error (por ejemplo, la orden de compra sin aprobar).
- **Absorción de costos de planta (n.º 15 y 17)**: separa la mano de obra (planilla 62 real frente a la absorbida en las órdenes) de la máquina y el CIF (63 a 68 real frente a lo aplicado), agrega el detalle por puesto de trabajo (horas máquina, horas hombre, mano de obra, máquina, CIF y tarifas) y explica la contabilización: la cuenta 71 hace de «costos aplicados» y la diferencia se cierra con la liquidación de costo real (enlace e indicador de si el mes ya se liquidó).
- **Trabajadores sin centro de costo (n.º 16)**: filtro «Sin centro de costo», aviso con el número de trabajadores y **asignación masiva** (marcar y asignar el centro).
- **Personal desde la tablet (n.º 18)**: el avance de la planta registra quién trabajó (trabajador de planilla, rol y horas) con el botón «Agregar trabajador»; al terminar la orden se precarga como la cuadrilla real.
- **Envases y embalajes (n.º 22)**: nueva clasificación de compras con su cuenta 604 (destino 2861 por recibir y variación 614); se agregaron al plan las cuentas 26, 2861, 604 y 614.
- **Almacenes (n.º 23)**: un almacén físico exige dirección y ubigeo (punto de partida de las guías); los de tránsito, cuarentena o terceros no.
- **Sugerencia de compra (n.º 30)**: ya no propone comprar productos que se fabrican (terminados, semielaborados o con versión de fabricación): los repone el MRP.
- **Kardex masivo (n.º 39)**: columna de saldo por producto y almacén (con el saldo al inicio del rango) y opción **«Enviarme por correo»**: se genera en segundo plano y llega como CSV comprimido. El Excel acepta hasta 200,000 movimientos; para más, el CSV.
- **Modelo de control del almacén (n.º 41)**: inventario general anual, cíclico ABC o ambos; se muestra en los indicadores de exactitud.
- **Stock por almacén (n.º 42)**: cuenta todos los almacenes activos, lista los que están sin stock y la opción «Incluir almacenes sin stock» muestra sus columnas en cero.
- **Condición de pago (n.º 20)**: un pedido sin condición escrita hereda la del cliente («Crédito 45 días» o «Contado») y la factura la del pedido.

## v1.40.0 — 20/10/2026 · Inventario físico y reportes masivos de inventario (al estilo SAP)
- **Inventario físico** (Inventario › Operaciones › Inventario físico): documento con número, almacén, tipo, contador asignado y **foto del stock** (cantidad y costo de cada producto, una línea por lote en los productos con lote). Tipos: **general** (todo el almacén), **cíclico ABC** (A cada 30 días, B 90, C 180), **aleatorio** (N productos al azar; la semilla y el usuario quedan registrados y la misma semilla reproduce la selección), **por ubicación** (todo un pasillo o rack) y **stock en cero** (confirmar que no queda saldo).
- **Bloqueo de movimientos** opcional: mientras se cuenta, los productos del documento no se mueven en ese almacén (ventas, compras, producción, operaciones), con un aviso claro. Sin bloqueo, lo que se mueve después de la foto se descuenta al comparar.
- **Conteo**: hoja de conteo para imprimir y **conteo en el celular** con lector de código de barras; **conteo ciego** (el contador no ve el stock del sistema); queda la fecha, hora y usuario de cada conteo.
- **Lista de diferencias**: cantidad, valor y %, con **reconteo obligatorio por otro contador** cuando supera la tolerancia del documento (2 % o S/ 500 por defecto) y **motivo** de cada diferencia (error de registro, merma, daño, vencimiento, robo, mal ubicado, sobrante, otro).
- **Aprobación**: quien contó no aprueba; el jefe de almacén (permiso de ajustes) aprueba hasta el **tope de la empresa** (S/ 5,000 por defecto) y por encima la contraloría o la gerencia (nuevo permiso «Aprobar diferencias de inventario físico sobre el tope»).
- **Contabilización**: ajustes de ingreso (sobrantes) y salida (faltantes), tabla 12 SUNAT 28, con el **acta en Excel como sustento** (foto, conteos, reconteos, diferencias, motivos y quiénes intervinieron); el asiento sale en la centralización. Historial y adjuntos en el documento.
- **Indicadores**: exactitud del inventario (IRA) por almacén, clase ABC y contador (meta ≥ 95 %), cumplimiento del programa cíclico, valor de faltantes y sobrantes por motivo y por mes, y productos con stock sin conteo en 12 meses (con Excel).
- **Reportes masivos** (Inventario › Reportes): **stock a una fecha** (MB5B) con filtros de almacén, producto, familia, lote y ubicación, cantidad, costo y valor; **stock por almacén, lote y ubicación** (MB52/MMBE) con vencimiento, **comprometido** (requerimientos aprobados e insumos de órdenes de producción abiertas), **bloqueado** (cuarentena, conteo con bloqueo, lotes vencidos) y disponible; **kardex masivo** (MB51) de varios productos y almacenes, un movimiento por fila, en Excel o **CSV generado por partes sin límite de filas**; **valorización a una fecha** con **cuadre automático contra las cuentas 20 a 26**; **rotación**, días de inventario y **stock sin movimiento** en 90, 180 o 365 días. Todos con Excel; sin permiso de costos no se muestran costos ni valores.

## v1.39.0 — 20/10/2026 · Solicitud de pedido (SolPed, al estilo SAP ME51N)
- **Solicitud de pedido**: la necesidad de compra que el área pide y Compras convierte en orden de compra. Productos o servicios en texto libre (unidad servicio, hora, día o mes), cantidad, fecha requerida, centro de costo, cuenta de gasto o activo, proveedor sugerido, urgente y sustento. Borrador → por liberar → liberada → pedida en parte / pedida → cerrada (u observada, rechazada, anulada). La registra cualquier área desde **Requerimientos › Solicitudes de pedido** (permiso «Registrar y enviar solicitudes de pedido»), sin necesitar el módulo Compras.
- **Orígenes**: del área; del **MRP** («Comprar» ahora crea una solicitud, liberada si ninguna regla pide liberarla); del **punto de reorden** (Sugerencia de compra › «Generar solicitudes de pedido», una por proveedor habitual); de un **requerimiento sin stock** (al aprobarlo, lo que el almacén no tiene pasa solo a una solicitud vinculada y el área ve el avance); de una **orden de mantenimiento** (repuestos sin stock y el servicio externo, cargados a la orden y al centro de costo del equipo). Opción de la empresa: con proveedor habitual y **contrato marco vigente**, el MRP y la reposición crean la orden de compra directa.
- **Liberación por niveles** (Compras › Reglas de liberación): secuencial, por monto valorizado (cantidad × precio estimado o último precio de compra), por centro de costo (su **usuario responsable**, nuevo campo del centro) o por tipo (nivel adicional para activo fijo, servicios o urgentes). Nadie libera su propia solicitud; observar devuelve al solicitante para corregir; rechazar y anular exigen motivo. Aviso por correo y por WhatsApp (si está activo), y **liberación desde el celular**.
- **Presupuesto**: al enviar, avisa o bloquea (opción de la empresa) si supera lo disponible del centro de costo y la cuenta: presupuesto del año menos lo ejecutado y lo comprometido (solicitudes por liberar o liberadas y órdenes de compra sin facturar).
- **Bandeja de compras**: las líneas liberadas por pedir, agrupables por proveedor, producto o fecha. Se convierten en **orden de compra** (una por proveedor y centro de costo, pendiente de aprobar), en **pedido contra contrato marco** (precios pactados) o en **licitación** (al adjudicarla, sus órdenes quedan vinculadas). **Parcialidades**: una línea puede pedirse en varias órdenes; anular una orden devuelve lo pedido a la bandeja; Compras puede cerrar lo que ya no se pedirá. El MRP y la sugerencia de compra cuentan lo ya solicitado.
- **Servicios**: la factura de una orden de servicios que viene de una solicitud exige la **conformidad del servicio** (hoja de entrada) registrada en la orden de compra.
- **Trazabilidad**: solicitud → orden de compra → recepción → conformidad → factura → pago, en la solicitud; la orden de compra, el requerimiento, la propuesta del MRP y la orden de mantenimiento muestran su solicitud.
- **Reportes**: solicitudes por estado, antigüedad de las abiertas, tiempo promedio de liberación, días de solicitud liberada a orden de compra y por centro de costo (con Excel).
- **Permisos nuevos**: registrar solicitudes, liberarlas (se asigna a propósito) y convertirlas; reglas de segregación «solicita y libera» y «convierte y aprueba la OC».

## v1.38.0 — 20/10/2026 · Correcciones del flujo integral de punta a punta
- **Recepción con decimales (COM-03)**: la recepción de una OC (ej. 160.56 L) se prellenaba con 160.560000 y el formulario la rechazaba; ahora se propone con 2 decimales.
- **Clasificación de compras (COM-04)**: nuevas clasificaciones **Materia prima e insumos** (602) y **Suministros y repuestos** (603). La factura que se registra desde una OC propone la clasificación según los productos que más pesan; también la del portal de proveedores. «Exigir orden de compra» aplica a los tres tipos de bienes de almacén.
- **Venta desde un pedido al crédito (VEN-09)**: toma la condición de pago del pedido («Crédito 30 días», «45 días», «Contado»; sin condición, los días de crédito del cliente) y propone el vencimiento.
- **Pedido aprobado modificado (VEN-10)**: al guardar cambios vuelve a «Pendiente» y debe aprobarse otra vez (se avisa antes de editar); con un comprobante emitido ya no se edita.
- **Cuadrilla de la orden (MAN-10)**: botón **Agregar persona** en cada puesto para registrar todas las horas hombre que hagan falta (antes solo la cuadrilla prevista y una fila libre).
- **Paradas (MAN-11)**: no se aceptan horas futuras, ni anteriores al inicio o posteriores al término de la orden, ni de un puesto por el que la orden no pasa, ni cruzadas con otra parada del mismo puesto o equipo. El operario se elige de los trabajadores de planilla.
- **Guía de remisión (LOG-03)**: si el almacén no tiene dirección, el punto de partida se toma del almacén principal (y luego de la empresa) y se avisa; un almacén nuevo propone la dirección del principal.
- **Planilla por centro de costo (PLA-06/07)**: el gasto de un trabajador sin centro de costo se reparte según las horas hombre que registró en las órdenes de producción del mes (centro del puesto o de la orden), también en las provisiones de beneficios sociales. La planilla no se cierra si algún trabajador queda sin centro: el mensaje los lista.
- **Resultados por línea de negocio (CON-05)**: en cada línea el costo de ventas es el **costo de lo vendido (69)**, así el margen por línea es el real; lo comprado, consumido y producido en el periodo que aún no se vende (60/61, destinos de producción, 71/72) va a la columna «Sin línea / producción en curso». El total sigue siendo el del estado de resultados por función.

## v1.37.0 — 20/10/2026 · Maestro de motivos de parada y OEE por tipo de pérdida
- **Motivos de parada** en Manufactura › Ingeniería (y carga masiva desde Excel): código, nombre, **pérdida OEE** (disponibilidad, rendimiento, calidad o no programado), **tipo** (planificada, avería, proceso, externa, microparada, velocidad reducida, calidad), área responsable, si cuenta para MTBF/MTTR, si exige equipo, si propone orden de mantenimiento y en qué puestos aplica. Los motivos con paradas no se borran: se desactivan.
- Los 9 motivos anteriores quedaron clasificados (sus paradas se conservan) y se agregaron los de la lista sugerida que faltaban (sin pedidos, refrigerio, arranque, averías eléctrica y neumática, ajustes, atasco, microparada, velocidad reducida, rechazo, reproceso, merma de arranque).
- **Paradas de línea y tablet**: solo se ofrecen los motivos del puesto; si el motivo exige equipo y el puesto tiene varios, se elige; las averías **proponen una orden de mantenimiento correctiva** para el equipo.
- **OEE**: el tiempo no programado queda fuera; las planificadas restan disponibilidad solo si la empresa lo configura; las microparadas (paradas cortas no planificadas, límite configurable, 5 min por defecto) y la velocidad reducida bajan el rendimiento; averías, proceso y externas bajan la disponibilidad. Columnas por cada tipo de pérdida.
- **Pareto de paradas** (nuevo reporte): horas, número de paradas y % acumulado por motivo, tipo, área, equipo, línea o pérdida OEE. El MTBF y el MTTR usan solo los motivos marcados.

## v1.36.0 — 19/10/2026 · Correcciones del piloto (checklist v1.34.0)
- **Kardex sin negativos por fechas atrasadas (MAN-08, crítica)**: una salida (consumo, venta, traslado) con fecha pasada ya no puede usar un ingreso posterior: se valida el saldo en su fecha y en cada movimiento siguiente, con un mensaje que indica cuánto puede salir y desde cuándo.
- **Órdenes de compra sin aprobar (COM-01)**: no se recibe mercadería ni se registra la factura de una OC pendiente; mensaje claro para aprobarla primero.
- **Vencimiento de ventas al crédito (VEN-01)**: vacío = emisión + días de crédito del cliente (o los días entre cuotas); un vencimiento igual o anterior a la emisión se rechaza. Las compras al crédito toman los días de la orden o del proveedor.
- **Producto sin versión de fabricación (MAN-02)**: la orden de producción avisa por qué el producto no aparece, con enlace a crear su versión (producto y receta ya elegidos); la carga masiva de recetas lista los productos que quedaron sin versión.
- **Carga masiva de puestos (PLAT-04)**: días laborables como 123456, 1,2,3,4,5,6, 1-6 o el número que deja Excel; vacío = lunes a sábado.
- **OEE y productividad (MAN-04)**: incluyen las horas reales de las órdenes terminadas desde su pantalla (sin tablet); también las horas de operación para el MTBF.
- **Merma (diagnóstico n.º 16)**: se compara con la receta para lo **producido**, no para lo planificado (una orden de 1 200 kg que produjo 500 ya no figura con 58 % de «ahorro»). Aplica a merma, consumo de materias primas y reporte diario.
- **Carga masiva de activos fijos (checklist n.º 86)**: activos existentes con su depreciación acumulada a una fecha, sin asiento de compra (ya están en el balance); el Excel queda como sustento. Botón «Importar desde Excel» en Activos fijos.
- **UIT 2026 = S/ 5,500** (diagnóstico n.º 9; la carga inicial tenía la de 2025). Solo se corrige si conservaba el valor inicial.
- **Kardex del sistema anterior** (diagnóstico n.º 18): las listas de filtros se calculan en una sola pasada y quedan en memoria (primera carga de 2,6 s a 1,3 s; luego 0,17 s).
- Verificado con pruebas: límite de crédito, MRP de mercadería de reventa como propuesta de compra y lanzamiento masivo de propuestas a órdenes de compra.

## v1.35.0 — 19/10/2026 · Determinación de la cuenta contable
- Al crear o editar una cuenta contable se configura su **determinación** (lo que se deja en «automático» toma el valor que corresponde a su código):
  - **Nivel**: según los dígitos (1 cuenta, 2 subcuenta, 3 divisionaria, 4 subdivisionaria, más dígitos = auxiliar). Una cuenta nueva debe colgar de una cuenta superior existente.
  - **Columna del balance**: inventario (balance general), resultados por naturaleza, por función, por ambos, o solo saldo. El **balance de comprobación (hoja de trabajo)** ubica cada cuenta en la columna configurada; con los valores por defecto el resultado es idéntico al anterior (verificado con las 508 cuentas con movimiento del 2026).
  - **Moneda** (soles o dólares): en el asiento manual, las líneas de cuentas en dólares exigen el tipo de cambio del asiento y guardan también el importe en US$. Las subcuentas de bancos en dólares quedaron marcadas en dólares.
  - **Tipo de cambio** (compra o venta): el ajuste por diferencia de cambio al cierre usa el de cada cuenta (activos a compra, pasivos a venta).
  - **Análisis por número de documento**: las líneas de esas cuentas exigen el N° de documento (por defecto, cuentas por cobrar y por pagar 12-14, 16, 17, 42-47).
  - **Cuenta analítica (centro de costo)**: no lleva, opcional u obligatorio. Reemplaza la regla fija por código en asientos manuales, compras, movimientos de caja, rendiciones y centralización.
- El plan de cuentas muestra nivel, columna, moneda y tipo de cambio, análisis y centro de costo; la carga masiva desde Excel acepta esas columnas.
- **Corrección**: el asiento manual no mostraba el motivo cuando faltaba el centro de costo (ahora se ve el error bajo el campo, igual que el del N° de documento).

## v1.34.0 — 19/10/2026 · Plan contable PCGE 2026 por empresa
- **Cada empresa elige su plan contable**: PCGE modificado 2019 (por defecto) o **PCGE 2026**. El catálogo 2026 viene completo (1 634 cuentas a cinco dígitos) con las divisionarias propias del ERP para la clase 9 (911 producción, 912 administración, 913 ventas, 921, 931) y la 6399.
- El ERP genera los asientos automáticos en el plan de cada empresa: los códigos que cambian de significado en 2026 se traducen solos (ventas de servicios 7041 → 70321, mercaderías 70111 → 70121, caja 1011 → 101, gastos por función 90/94/95/97 → 911/912/913/92-93, depreciación en la 39/68 nuevas, entre otros). Aplica a centralización, cuentas por defecto de productos, reportes, importaciones y destinos de la clase 6.
- Comando **`adoptar_pcge2026 --empresa <alias> --confirmar`**: cambia una empresa al PCGE 2026; se niega si ya tiene asientos manuales, de apertura o del sistema anterior, o periodos cerrados. La empresa de prueba ya usa el PCGE 2026; la principal sigue en el 2019.
- El plan de cuentas muestra qué plan usa la empresa, y «Completar PCGE» completa con el catálogo de ese plan.
- **Corrección**: las líneas de comprobantes (cotizaciones, ventas, órdenes de compra, compras) y de guías de remisión ahora salen siempre en el orden en que se registraron. En PostgreSQL podían salir desordenadas (en el PDF o en el envío a SUNAT). Lo detectaron las pruebas sobre PostgreSQL.

## v1.33.1 — 19/10/2026 · Fecha de Lima en todo el sistema
- **Corrección importante para el servidor**: el sistema tomaba "hoy" de la hora del sistema operativo. En un servidor en hora UTC (lo habitual en un VPS), entre las 7 p. m. y la medianoche de Lima usaba la fecha del día siguiente: comprobantes, movimientos, planillas y reportes con fecha futura (SUNAT rechaza comprobantes con fecha futura). Ahora todo usa la fecha de Lima según la configuración del ERP (124 lugares en 63 archivos). Lo detectaron las pruebas automáticas de GitHub, que corren en hora UTC.
- Si una prueba falla en GitHub, el resumen de la ejecución muestra cuál y por qué. Acciones de GitHub actualizadas.
- **`iniciar.bat` arranca con PostgreSQL**, la base de trabajo (antes seguía abriendo la base SQLite anterior, que quedó sin las últimas migraciones y hacía fallar todas las pantallas). Avisa si el servicio de PostgreSQL no está iniciado. `iniciar.bat sqlite` abre la base anterior solo para consultar.

## v1.33.0 — 19/10/2026 · Monitoreo, velocidad y control
- **Incidencias y errores** (Ajustes): cada error del sistema se registra solo, agrupado si se repite, con el detalle técnico, y se avisa por correo a los administradores (máximo una vez por hora por error); si un error resuelto vuelve a ocurrir, se reabre.
- **Reportar un problema** en el menú de cada usuario: queda registrado con la página en la que estaba y llega al administrador.
- **/salud/** para un monitor de caídas: estado de cada base de datos y migraciones pendientes. Errores también en `logs/errores.log`.
- **Log de modificaciones en todos los módulos** (solo administradores): quién creó, cambió, anuló o eliminó qué, con el valor anterior y el nuevo; la bitácora registra además cotizaciones, listas de precios, oportunidades, turnos y conceptos de planilla.
- **Más rápido** con los datos reales en PostgreSQL: emitir comprobante 5,9 s → 1,1 s, nueva oportunidad 5 s → 0,04 s (ya no carga las 18 600 cotizaciones atendidas), requerimiento de materiales 6,6 s → 0,4 s, sugerencia de compra 4,4 s → 0,5 s, campana de alertas 7,2 s → 0,8 s.
- **Pruebas automáticas en GitHub** sobre PostgreSQL 17 en cada subida (pestaña Actions).
- **Respaldo diario programado** con `scripts\programar_respaldo.ps1`.
- Documento **docs/INTEGRACION.md**: diagramas del flujo entre módulos, de las integraciones externas y de la plataforma.

## v1.32.0 — 19/10/2026 · PostgreSQL como base de trabajo
- **PostgreSQL 17 local** con una base por empresa (`ceiba_principal`, `ceiba_empresa2`) y su propio usuario; ajustes en `erp/settings_postgres.py` (claves en el archivo local `.env.postgres`, que no se sube al repositorio).
- Nuevo comando **`copiar_a_postgres`**: copia una base SQLite completa a PostgreSQL con los mismos ids (usuarios, permisos, configuración, bitácora, historial y todos los módulos), en una sola transacción, con revisión previa de datos que PostgreSQL no acepta y verificación final de cantidades e importes tabla por tabla. La base de la empresa (2 447 945 registros) se copió en 3 minutos sin diferencias.
- Scripts de **respaldo y restauración** (`scripts\respaldar_postgres.ps1`, `scripts\restaurar_postgres.ps1`) para guardar las bases y llevarlas completas (tablas y datos) al servidor.
- Guía **docs/POSTGRESQL.md**: instalación, uso diario, migración, respaldos, traslado al VPS y problemas frecuentes.
- Las 349 pruebas automáticas pasan también sobre PostgreSQL. El despliegue en Render queda en desuso: el código se publica solo en GitHub.

## v1.31.0 — 16/10/2026 · Reporte diario de producción y log de modificaciones de Manufactura
- **Reporte diario de producción** (Manufactura › Reportes): resumen del día y seis secciones — producción terminada (planificado, producido, cumplimiento, kg y TM), avance de planta por turno y operario, insumos consumidos contra la receta, paradas de línea, inspecciones de calidad y órdenes del día que siguen abiertas. Filtros por planta, línea, turno, familia y producto; botones de día anterior y siguiente; Excel con una hoja por sección; impresión.
- **Log de modificaciones** en Manufactura (solo administradores): quién creó, modificó, anuló o eliminó cada registro, con el valor anterior y el nuevo, fecha, hora e IP; filtros por usuario, acción, tipo de registro y fechas; Excel. Botón **Historial** en cada orden de producción.
- La bitácora ahora también registra en Manufactura: insumos y subproductos de las recetas, operaciones de las hojas de ruta, plan de demanda, avances de planta, paradas y sus motivos, inspecciones y parámetros de calidad, equipos, planes y órdenes de mantenimiento y cambios de ingeniería.

## v1.30.0 — 16/10/2026 · Reportes de producción en kg y toneladas
- **Producto**: peso neto por unidad, familia y unidad de reporte (unidad base, kg o TM); también en la carga masiva. Con el peso neto, todo se convierte a kg y TM.
- **Manufactura › Reportes** (todos con filtros, Excel, impresión y **filtros guardados** por usuario):
  1. Producción por día, semana o mes y por planta, línea, familia o producto, en unidad base, kg y TM; detalle por orden con **fecha real de producción y fecha contable**.
  2. Plan contra real con cumplimiento y alerta de desvío mayor a ±5 % (por orden, línea o semana; unidad elegible).
  3. Rendimiento y merma por orden (receta contra real, kg, %, S/) con el top 10.
  4. Consumo de materias primas: TM consumidas contra el estándar de la receta, variación en kg, % y S/.
  5. Productividad: kg por hora hombre y por hora máquina, por línea, turno u operario.
  6. OEE por línea: disponibilidad × rendimiento × calidad.
  7. Órdenes abiertas y atrasadas: días de atraso y pendiente.
  8. Costo de producción por kg y por TM (material, mano de obra, máquina, CIF), con la liquidación de costo real.
  9. Producción por turno y operario.
  10. Trazabilidad de lote: orden, lotes de insumos y clientes; y al revés, desde un lote de insumo o un cliente.
  11. Calidad por lote: % aprobado, reprocesado y rechazado, y las causas de rechazo.
  12. Paradas y mantenimiento: horas de parada, MTBF y MTTR por equipo.
  13. Producción, ventas y stock en TM con días de cobertura.
  14. **Tablero diario de producción** (TM del día y del mes, cumplimiento, merma, productividad, atrasos y gráfico).
- **Paradas de línea** con motivo (avería, falta de material, cambio de formato, limpieza…; planificadas o de falla) y duración, desde Manufactura › Paradas o desde la tablet de planta. La tablet registra también el **turno**.
- Inspección de calidad con resultado **Reprocesado**.
- Los reportes leen solo los campos necesarios: responden en 1 a 3 segundos aun con decenas de miles de órdenes. En pantalla se muestran hasta 2 000 filas; el Excel lleva todas.

## v1.29.0 — 16/10/2026 · Rendición de gastos: lecciones de una implementación corporativa
- **Indicador de impuesto obligatorio** en cada gasto (gravado con o sin crédito fiscal, tasa reducida de restaurantes y hoteles MYPE, exonerado, inafecto, no gravado; parametrizables). No se envía sin él; valida base × tasa = IGV (tolerancia S/ 0.10) y define si va al registro de compras con crédito fiscal o al gasto con el IGV incluido. Se sugiere según el comprobante.
- **Motivos de observación estándar** (ilegible, indicador, fuera de política, imputación errada, fuera de plazo, duplicado…) al observar o rechazar un gasto o la rendición, y **reporte de observaciones y errores por usuario y por área**.
- **Historial de cambios de imputación**: al cambiar la cuenta de una categoría se indica desde qué fecha rige (los gastos anteriores conservan su cuenta) y se avisa a contabilidad por correo y en la campana.
- **Corte de cierre del mes**: día de corte configurable, aviso a rendidores y aprobadores (campana y recordatorio diario) y lo aprobado después del corte se contabiliza el mes siguiente.
- **Distribución en varios centros de costo**: un gasto o toda la rendición (fondo fijo) repartida por porcentajes que suman 100%, con plantillas reutilizables; el asiento lleva una línea por centro (también en las facturas del registro de compras).
- **Trazabilidad de aprobación**: reporte con cada aprobador, nivel, fecha y hora, horas de cada paso y último aprobador; exportable a Excel.
- **Sustento enlazado al asiento**: la foto, PDF o XML del gasto queda en el registro de compras y en el gasto rendido, y el asiento muestra los sustentos de su documento de origen.
- **Exportación a un ERP externo (SAP)**: lote con cuenta (con equivalencias), centro de costo, indicador de impuesto y enlace al sustento; carga de la respuesta del ERP por documento, reintento de los errores y conciliación Ceiba frente al ERP externo.
- **App del celular**: aviso inmediato de comprobante repetido (y bloqueo al registrar desde cualquier canal), saldo en poder del rendidor, indicador de impuesto, gastos subidos con lo que salió de la política y **aprobación desde el celular** gasto por gasto viendo la foto.
- Comando `demo_rendiciones` para recorrer el circuito completo en una empresa de prueba.

## v1.28.1 — 14/10/2026 · Inicio: nombres de módulo centrados
- En la pantalla de inicio, los nombres de módulo que ocupan dos líneas (p. ej. «Rendición de gastos») quedan centrados bajo su ícono.

## v1.28.0 — 14/10/2026 · Rendición de gastos: app del celular, fases 2 y 3
- **App del celular instalable** (Rendición de gastos › App del celular): se instala desde el navegador en Android y iPhone, entra con el usuario de Ceiba (con verificación en dos pasos) y **funciona sin internet**: guarda los gastos y sus fotos en el teléfono y los sube solos al recuperar la señal, sin duplicarlos.
- **Foto del comprobante**: cámara o galería, varias fotos por gasto, recorte con el dedo, giro y compresión a menos de 1 MB antes de subir.
- **Lectura del QR SUNAT**: llena RUC, tipo, serie, número, IGV, total y fecha, y avisa si la factura no está a nombre del RUC de la empresa (sin crédito fiscal).
- **Consulta RUC**: razón social, estado y condición (con el token de Decolecta o lo registrado en el sistema); alerta si no está activo o habido.
- **Validación del comprobante en SUNAT** (API de consulta de validez): se consulta al enviar o con el botón de cada gasto; guarda el resultado y la fecha; un comprobante anulado o inexistente no se puede enviar.
- **Planilla de movilidad** imprimible por día (fecha, motivo, destino, monto) con el tope diario de 4% de la RMV, declaración y firma del trabajador.
- **Lectura con IA de tickets y boletas sin QR** (Claude, de Anthropic): sugiere RUC, fecha, número y total desde la foto; el rendidor confirma antes de enviar. Se activa en Integraciones.
- **Kilometraje** en vehículo propio: km × tarifa por km de la categoría.
- **Proyectos y sitios**: el gasto de los técnicos en campo se carga al proyecto y sitio (obligatorio según la categoría); sin centro de costo usa el del proyecto. Tablero por proyecto.
- **Tarjetas corporativas**: se importa el estado de cuenta (Excel o CSV), cada cargo es un gasto que el titular concilia con su comprobante; contabilidad contra la cuenta por pagar de la tarjeta y registro del pago al banco. **Cargos sin comprobante** con antigüedad y recordatorio al titular (y a su jefe pasados 30 días).
- **Gastos por WhatsApp** (WhatsApp Business Cloud API): el trabajador envía la foto al número de la empresa, se identifica por su celular, se lee el comprobante y se crea el borrador; recibe la confirmación por WhatsApp.
- **Gastos por correo**: un buzón recibe los PDF/XML reenviados y crea borradores en la rendición abierta del remitente (el XML llena los datos exactos).
- **Cruce con SIRE**: marca las facturas que llegaron por rendición y avisa de las facturas a nombre de la empresa que nadie rindió ni registró.
- **API REST**: `/api/v1/rendiciones/` (lista y detalle), categorías y `POST /api/v1/rendiciones/{id}/gastos/` con foto en base64.
- **Fotos y PDF en almacenamiento de objetos** (S3 compatible: AWS, Cloudflare R2, Spaces, Backblaze) con las variables `OBJETOS_*`, y el comando `mover_sustentos` para trasladar lo que hoy está en la base de datos; conservación y respaldo se configuran en el bucket.

## v1.27.0 — 14/10/2026 · Módulo de rendición de gastos (fase 1)
- **Nuevo módulo «Rendición de gastos»** sobre las entregas a rendir y cajas chicas de Finanzas: cada rendición muestra lo entregado, lo rendido, lo que está en trámite y el saldo en poder del responsable.
- **Estados con bitácora**: borrador → enviada → en revisión → aprobada / observada / rechazada → contabilizada → reembolsada o devuelta; cada cambio guarda usuario, fecha y comentario (y la auditoría de Ceiba registra altas, cambios y eliminaciones).
- **Línea de gasto completa**: fecha, RUC y razón social, tipo de comprobante (factura, boleta, recibo por honorarios, ticket, movilidad, sin comprobante), serie-número, moneda y tipo de cambio, base, IGV, total, categoría, centro de costo, proyecto o sitio, glosa y varias fotos por gasto (desde el celular abre la cámara). Bloquea comprobantes ya rendidos o ya registrados en compras.
- **Categorías de gasto** con cuenta contable, si exigen comprobante o centro de costo, tope por gasto y tope diario (movilidad: 4% de la RMV por día); carga masiva por Excel.
- **Políticas de gasto** por empresa y por cargo: días máximos para rendir, tope por rendición y por día, comprobante obligatorio y categorías no permitidas. El rendidor ve qué gastos salen de la política y debe justificarlos antes de enviar.
- **Aprobación configurable**: por jefe directo, por usuario, por monto y por centro de costo, en uno o varios niveles; se aprueba, observa o rechaza la rendición completa o gasto por gasto, con comentario. Nadie aprueba su propia rendición (regla fija en Segregación de funciones).
- **Roles** con los permisos por acción: rendir, aprobar, contabilizar (revisor contable), tesorería y configurar. En Ajustes › Usuarios se indica qué trabajador es cada usuario y su jefe directo.
- **Contabilidad automática**: al contabilizar, las facturas válidas a nombre de la empresa pasan al registro de compras 8.1 con crédito fiscal y se cancelan con el fondo; boletas, tickets y movilidad van como gasto sin crédito fiscal contra la entrega a rendir o la caja chica (cuenta de la categoría y centro de costo).
- **Reembolso o devolución**: reembolso individual o en lote, devolución del saldo o descuento por planilla (pasa a cuenta por cobrar al trabajador). **Caja chica**: contabiliza las rendiciones aprobadas y repone el fondo fijo en un paso.
- **Avisos**: correo al siguiente aprobador y al rendidor; alertas en la campana (por aprobar, observadas, por contabilizar, entregas vencidas) y el comando `recordar_rendiciones` para el recordatorio diario al rendidor y su jefe.
- **Reportes**: tablero de gastos (por categoría, centro de costo, persona y mes; pendientes, vencidas, días promedio de aprobación y gastos fuera de política), saldos por rendir con antigüedad (0-15, 16-30, más de 30 días) y la rendición en PDF (con fotos, aprobaciones y firmas) y Excel.

## v1.26.9 — 12/10/2026 · Planificación por producto: MRP, punto de reorden, MTS y MTO
- **Tipo de planificación por producto** (pestaña Planificación): *MRP* (según pedidos, plan de demanda y recetas), *Punto de reorden* o *Sin planificar*. Los suministros indirectos pasan a punto de reorden: quedan fuera del MRP y la corrida los propone aparte cuando el stock más lo que viene en camino llega al punto de reorden (hasta el stock máximo, por lote de compra). Los sin planificar no se proponen.
- **Estrategia MTS / MTO por producto**: *para stock* usa el plan de demanda y el stock libre (como hasta ahora); *contra pedido* se fabrica o compra solo por los pedidos de venta, sin usar el stock libre ni el de seguridad, con una orden por pedido.
- **Carga masiva del plan de demanda desde Excel** (producto, cantidad, fecha, nota), con botón «Importar desde Excel» en la pantalla del MRP; si ya hay plan para el producto en esa fecha se reemplaza con «Actualizar existentes». La carga de productos acepta las columnas tipo de planificación y estrategia.

## v1.26.8 — 12/10/2026 · Importar maestros y estado de resultados por cuenta
- **Importar desde Excel en cada maestro**: botón en las listas de productos, clientes y proveedores, centros de costo y de beneficio, plan de cuentas, almacenes, puestos de trabajo, recetas, hojas de ruta, versiones de fabricación, trabajadores y listas de precios. Nuevas cargas masivas: **plan de cuentas** (naturaleza, cuenta de agrupación y destinos) y **almacenes**.
- **Estado de resultados, un nivel más**: cada línea (ventas, costo de ventas, gastos…) se despliega con las cuentas que la forman y su monto, también mes a mes y en el Excel; botones «Ver cuentas» y «Ocultar cuentas». La suma de las cuentas es siempre el importe de la línea.

## v1.26.7 — 12/10/2026 · Gastos y destinos juntos
- Gastos por centro de costo: nueva vista «Todos» con los gastos (61 a 68) y los destinos (clase 9) de cada centro en columnas separadas, con su propio y acumulado (no se suman: un gasto y su destino son el mismo importe).

## v1.26.6 — 12/10/2026 · Actualización sin recargar a mano
- El navegador descarga otra vez el JavaScript y los estilos del ERP en cada versión nueva (antes podía quedarse con la versión anterior en memoria y había que recargar con Ctrl + F5).

## v1.26.5 — 12/10/2026 · Reportes que se comprimen y despliegan
- Gastos por centro de costo: cada centro se comprime o despliega con un clic (abre comprimido, con el total de cada centro y cuántas cuentas tiene) y botones «Desplegar todo» y «Comprimir todo». El mecanismo queda listo para cualquier reporte agrupado.

## v1.26.4 — 12/10/2026 · Gastos por centro de costo
- **Reporte**: solo los gastos por naturaleza (61 a 68); los destinos (clase 9) en una vista aparte. Ya no aparecen como gasto del área las compras (60), el costo de ventas (69) ni las cuentas de balance (activos 33, depreciación acumulada 39, existencias).
- **Centro de costo obligatorio** en los gastos 62 a 65 y 68: asientos manuales, compras de gasto o servicio, movimientos de caja y bancos, trabajadores (planilla), activos fijos (su depreciación) y requerimientos al almacén. Las cuentas de balance no llevan centro de costo.
- **Activos**: la depreciación de cada mes se carga al centro de costo del activo (el centro ya no aparece en la compra del activo).
- **Suministros por el almacén**: los productos de almacén van siempre a su cuenta de compra (ingresan a la 25 aunque la factura tenga una cuenta de gasto); el gasto con su centro nace en la salida por requerimiento.

## v1.26.3 — 12/10/2026 · Revisión de reportes
- **Balance de comprobación**: la columna de resultado por función ahora incluye compras y variación de existencias (60/61) y una fila con los gastos 62-68 sin destino a la clase 9; su resultado es igual al del estado de resultados y al de la columna por naturaleza (antes difería con lo importado del sistema anterior).
- **Rentabilidad** (por producto, cliente, vendedor o mes): incluye las ventas del sistema anterior con su costo tomado del kardex de ese sistema (por comprobante y producto); avisa cuántas líneas no tienen salida en ese kardex. Índice en el comprobante del kardex anterior (consulta 4 veces más rápida).
- Revisión completa: 179 pantallas abren sin error; estado de resultados (por función, naturaleza, mes a mes y por línea), balance de comprobación, situación financiera, libros y centros de costo cuadran entre sí.

## v1.26.2 — 12/10/2026 · Correcciones: resultados por línea y comando de traslado
- **Corrección**: el estado de resultados por línea de negocio usaba un cálculo simplificado (ventas solo 70, costo solo 69, todos los gastos 62-68 como administración y ventas) y no coincidía con el estado de resultados por función cuando hay compras, variación de existencias, producción (60/61, 9x, 71) o descuentos (74), como en lo importado del sistema anterior. Ahora cada línea se calcula con la misma fórmula del estado oficial y el total es idéntico.
- **Corrección**: el comando `trasladar_migracion` (pasa la base revisada en local a la base de la nube) había quedado dañado en la v1.23.4 al quitar el nombre de la empresa del código y no se podía ejecutar. Se restauró tal como estaba, con el nombre genérico de la base local.

## v1.26.1 — 08/10/2026 · Buscadores por palabra
- Los selectores de productos, clientes, proveedores, cuentas y comprobantes (y cualquier lista de más de 50 opciones) tienen buscador: al escribir una palabra (ej. FRESA) muestran todo lo que la contiene en cualquier parte del código o el nombre, sin importar mayúsculas ni tildes; con varias palabras (FRESA GRANOLA) deben estar todas. Los campos opcionales tienen una × para limpiarlos.

## v1.26.0 — 07/10/2026 · API de datos para exportar
- **Contabilidad en la API**: plan de cuentas, asientos (con o sin líneas), libro diario plano (una fila por línea, ideal para Power BI o Excel), balance de comprobación, estado de resultados por función o naturaleza (total o mes a mes) y estado de situación financiera; libro NIIF o tributario.
- **Más datos**: kardex, stock a una fecha con su valor, cuentas por pagar, movimientos de caja y bancos y órdenes de producción con sus costos.
- **Filtros de fecha** `desde` / `hasta` (AAAA-MM-DD) en todas las listas de documentos y movimientos, y `periodo` en ventas y compras; fechas inválidas responden 400 con el formato esperado.
- **Exportación**: `page_size` hasta 1000 y `formato=csv` para descargar todo el resultado en un archivo (separador `;`, abre directo en Excel). Los costos solo salen si el usuario de la clave puede ver costos. OpenAPI con todos los parámetros.

## v1.25.0 — 07/10/2026 · Costeo de planta y estado de resultados mes a mes
- **Costo real unitario = todo lo consumido ÷ todo lo producido.** La merma anormal ya no se resta sola: al terminar la orden hay una casilla para llevar a gasto el consumo sobre la receta cuando corresponda (NIC 2 párr. 16).
- **Tarifas separadas por puesto**: mano de obra (por hora hombre), máquina y CIF (ambas por hora máquina). Las horas de la orden son horas máquina; la hoja de ruta indica la cuadrilla (maquinistas y ayudantes) de cada operación.
- **Horas hombre por maquinista y ayudante**: al terminar, cada operación lista su cuadrilla con el rol, el trabajador de planillas y sus horas. La hora se valoriza al **costo real del trabajador** (sueldo, asignación familiar, EsSalud, gratificaciones con bonificación, CTS y vacaciones según su régimen); sin trabajador, a la tarifa referencial del puesto. Variaciones de eficiencia de mano de obra por horas hombre y de máquina/CIF por horas máquina.
- **Subproductos y coproductos** en la receta (NIC 2 párr. 14): el subproducto entra a su valor por unidad y se resta del costo del principal; el coproducto recibe su % del costo conjunto. Se declaran al terminar la orden y entran al almacén con su costo.
- **Fecha y hora de término**: por defecto la fecha y hora actual.
- **Estado de resultados mes a mes**: una columna por mes y el total, por función o por naturaleza (nuevo), con exportación a Excel.

## v1.24.2 — 07/10/2026 · Todas las órdenes de producción del sistema anterior
- `cargar_manufactura` carga también las órdenes **terminadas y canceladas** del sistema anterior como órdenes del ERP marcadas «Del sistema anterior»: con su número, fechas, cantidad producida, costos y consumo real (cuando el reporte lo trae), sin operación de almacén ni asiento, porque su producción ya está en el inventario y la contabilidad migrados.
- Esas órdenes son de consulta: no se anulan en el ERP y no entran al costo real vs estándar ni a la liquidación del costo real. Los productos sin detalle de componentes reciben una receta de referencia obsoleta (no la usan el MRP ni las órdenes nuevas).
- **Corrección** en el kardex y las órdenes del sistema anterior: la columna «Costo unit.» mostraba el costo total de la línea (así lo exporta el sistema anterior). Ahora se ven el costo unitario y el total, y cada orden muestra su costo según el kardex (insumos consumidos ÷ cantidad producida) aunque el reporte de órdenes lo traiga en cero.

## v1.24.1 — 07/10/2026 · Carga de manufactura del sistema anterior
- Comando `cargar_manufactura` que lee el reporte de producción del sistema anterior (Excel) y crea las **recetas** (lista de materiales aprobada y versión de fabricación) de cada producto fabricado, con la proporción de componentes que más se repite entre sus órdenes (las órdenes divididas -001/-002 conservan lo planificado de la original y no se usan como referencia).
- Las **órdenes abiertas** del sistema anterior (en progreso, borrador o por cerrar) pasan a órdenes de producción confirmadas por lo que falta producir; no mueven stock hasta terminarlas en el ERP. Se puede volver a correr sin duplicar.

## v1.24.0 — 07/10/2026 · Cierre de costos según la NIC 2
- **Liquidación de costo real** (Costos › Cierre de costos): al cierre del mes, el gasto real de cada centro de costo de planta (planillas, depreciación, energía…) se lleva a las órdenes terminadas por sus horas y, por producto, al inventario (revaloriza el costo promedio) o al costo de ventas, por línea de negocio. Multinivel: lo de los semielaborados pasa a los productos que los consumieron. Se puede volver a liquidar o anular.
- **Capacidad normal (NIC 2 párr. 13)**: cada puesto de trabajo tiene sus horas normales al mes; el CIF fijo de la capacidad ociosa queda como gasto del periodo y no se inventaría. Pantalla para clasificar los gastos de planta en mano de obra, CIF variable y CIF fijo por cuenta.
- **Merma anormal**: lo consumido por encima de la receta (con su merma normal) va a gasto y no al costo del producto; se muestra en la orden y en sus variaciones.
- **Valor neto realizable**: prueba al cierre con el precio promedio de las ventas recientes menos los gastos de venta; registra la desvalorización (695 / 29) y su reversión, por defecto solo en el libro NIIF. Nuevas cuentas 29 y 695.
- Permiso nuevo «Liquidar el costo real y registrar el VNR», que se asigna expresamente.

## v1.23.4 — 07/10/2026 · Sin datos de clientes en el código
- Los comandos de migración ya no traen el RUC ni la razón social de ninguna empresa: los toman de Ajustes › Empresa o de los parámetros `--ruc`, `--razon-social` (migrar_excels) y `--compania` (importar_anteriores).
- Ajustes locales de revisión renombrados a `erp.settings_cliente` (base `cliente.sqlite3`); pruebas con datos genéricos.
- «Diseño del ERP» regenerado sin el nombre de la empresa.
- `desplegar.bat`: publica en un clic (revisa que no vayan datos reales, corre las pruebas, sube a GitHub con su etiqueta y Render despliega).

## v1.23.3 — 07/10/2026 · Contabilidad del sistema anterior hasta la fecha de corte
- **Libros iguales al sistema anterior:** comando `contabilidad_anterior --corte AAAA-MM-DD` que importa a los libros del ERP cada asiento del sistema anterior desde su apertura del ejercicio hasta el corte (mismas cuentas, terceros y centros de costo), cierra esos periodos y deja al ERP contabilizando desde el día siguiente. Un asiento de ajustes de migración al corte pasa las facturas por cobrar y por pagar a las cuentas del ERP y lleva las existencias al valor del kardex.
- **Corrección:** los saldos de por pagar, caja y bancos migrados (y el «Balance del sistema anterior») sumaban también los meses previos a la apertura del ejercicio, que ya estaban resumidos en ella: se duplicaban saldos.
- **Estados financieros:** el resultado incluye compras, variación de existencias y producción almacenada (60/61/71/72), igual por naturaleza y por función; la clase 9 que no cancela con la 79 se muestra como «Costos por asignar» y el balance cuadra.
- Contabilidad › Sistema anterior › **Conciliación de la migración**: documentos por cobrar y por pagar, caja y bancos e inventario frente a sus cuentas.

## v1.23.2 — 07/10/2026 · Carga de listas de precios desde Excel
- **Carga masiva › Listas de precios**: precio, tramo por cantidad y descuento de cada producto en cada lista, hasta 30,000 filas. Acepta la lista por código o nombre (si no existe se crea) y el producto por código o como lo exporta Odoo («[A2820] Nombre»). Validación previa, «Actualizar existentes» y grabación en bloque.
- Ventas › Listas de precios: botón «Cargar precios desde Excel» y aviso de las listas sin precios.

## v1.23.1 — 07/10/2026 · Avisos de carga y de permisos
- **Aviso de carga**: barra superior y «Cargando…» / «Procesando…» mientras el servidor responde al abrir pantallas o guardar; el botón pulsado se bloquea para no registrar dos veces lo mismo (las descargas de Excel y PDF no lo muestran).
- **Acción sin permiso desde un botón**: se vuelve a la misma pantalla con el aviso «Acción no permitida» y el motivo, en vez de salir a otra página. La página de acceso denegado tiene botón «Volver».

## v1.23.0 — 07/10/2026 · Funciones de gran empresa
**Plataforma**
- **Segregación de funciones**: reporte de usuarios con permisos incompatibles (crear y aprobar órdenes, registrar facturas y pagarlas…), aviso al guardar un usuario y opción «estricta» en Ajustes › Empresa: nadie aprueba lo que registró (órdenes de compra, requerimientos, planillas).
- **Estados financieros consolidados** del grupo (solo administradores): suma las empresas y elimina las operaciones entre ellas por RUC.

**Finanzas y contabilidad**
- **Libros paralelos NIIF y tributario**: asientos manuales «solo NIIF» o «solo tributario»; libros y reportes con selector de libro; conciliación NIIF - tributaria (base de adiciones y deducciones).
- **Cierre guiado del mes**: lista de verificación (tipo de cambio, conciliación, kardex, depreciación, planilla, provisiones, centralización, cuadre y cierre) y **provisión mensual de gratificaciones, CTS y vacaciones** por régimen laboral.
- **Préstamos y leasing**: cronograma de cuota fija (TEA), desembolso, pago de cuotas y contabilidad (45 / 673 / 32; IGV en el leasing).

**Manufactura y costos**
- **Programación detallada de planta** con capacidad finita por puesto, prioridades y diagrama por días.
- **Reporte de planta en tablets**: producción buena, merma y horas por operación; alimenta el término de la orden.
- **Cambios de ingeniería** (ECO): receta propuesta, diferencias, aprobación por otra persona y fecha efectiva.
- **Costeo por actividades (ABC)**: actividades, centros de costo e inductores; costo ABC por producto frente al absorbido.

**Inventario y compras**
- **Picking por olas**: varios pedidos en un recorrido ordenado por ubicación y separación por pedido.
- **Inventario cíclico ABC**: clasificación por valor, programa de conteo, conteo a ciegas y ajuste con acta.
- **Licitaciones** con cuadro comparativo y adjudicación que genera las órdenes de compra; **contratos marco** con precios pactados y saldo.

**Planillas**
- **Asistencia y turnos**: marcaciones (Excel del reloj o manual), faltas, tardanzas y horas extra 25 % / 35 % que pasan a la planilla.

## v1.22.1 — 06/10/2026 · EMPRESAS_EXTRA más simple
- `EMPRESAS_EXTRA` acepta solo el nombre de la empresa (varias separadas por `;`), tolera comillas tipográficas y, si el valor no se entiende, el despliegue sigue con la empresa principal en vez de fallar.

## v1.22.0 — 06/10/2026 · Otra empresa sin otro servidor
- **Multiempresa en el mismo servidor**: una empresa nueva puede ir como otra base dentro del mismo PostgreSQL de la principal (`EMPRESAS_EXTRA = {"alias": {"nombre": "…", "base": "erp_alias"}}`), sin costo extra ni otra clave. El despliegue (`migrar_empresas`) crea la base si falta, con sus tablas, plan de cuentas y administrador.

## v1.21.0 — 06/10/2026 · Manuales de usuario
- **Manuales de usuario** en Ajustes → Ayuda: un PDF por módulo con diagramas de flujo, pantallas paso a paso y el logo en cada página. Solo los administradores los ven y descargan.
- Ventas: se quita "Trasladar a periodo" del comprobante de venta (queda solo en compras, para el crédito fiscal).
- Historial: el detalle de una orden de fabricación del sistema anterior abre más rápido (de 4 s a 0.6 s).

## v1.20.0 — 06/10/2026 · Funciones faltantes del alcance Base y multiempresa
**Plataforma**
- **Multiempresa**: cada empresa en su propia base de datos (usuarios, datos y configuración separados). Se elige la empresa al iniciar sesión y se cambia desde el menú del usuario. Nuevas empresas con la variable `EMPRESAS_EXTRA` y `python manage.py migrar_empresas`. La API usa la cabecera `X-Empresa` y los enlaces del portal de proveedores llevan su empresa.
- **Menú lateral** con todas las opciones del módulo (v1.19).

**Finanzas**
- **Anticipos** de clientes y a proveedores aplicados a sus comprobantes (sin mover caja), con anulación y contabilidad 122/422 contra 12/42.
- **Letras por cobrar y por pagar**: canje de facturas por letras (123/423), cobro y pago desde Cobranzas/Pagos, estados (cartera, cobranza, descuento, protesto), renovación y anulación del canje.
- **Cheques** emitidos y recibidos: cartera de diferidos, depósito, cobrado, rechazado o anulado (anula sus movimientos y el comprobante vuelve a deber).
- **Entregas a rendir y caja chica**: entrega, rendición con tickets o facturas registradas, devolución, reembolso, reposición del fondo y liquidación (1413 / 1021).
- **Pagos masivos a bancos**: lote de facturas de proveedores, archivo Excel con banco, cuenta o CCI de cada proveedor y registro de los pagos. Las fichas de proveedores tienen banco, cuenta, CCI y cuenta de detracciones.

**Ventas**
- **CRM**: embudo de oportunidades por etapa (tablero para arrastrar), pronóstico ponderado y actividades de seguimiento.
- **Punto de venta**: búsqueda o lector de código de barras, boleta, factura o nota de venta, cobro en caja con vuelto y resumen del día.
- **Ventas al crédito en cuotas** (cronograma que exige SUNAT) y boletas a "clientes varios". El límite de crédito considera las letras pendientes.

**Compras, inventario y manufactura**
- **Importaciones**: gastos vinculados (flete, seguro, ad valorem, agente, almacenaje) prorrateados por valor, cantidad o peso al costo de cada producto (6091 → 2811 → inventario).
- **Ubicaciones dentro del almacén** (zona-rack-nivel), ubicación habitual por producto, importación desde Excel, hoja de conteo y ubicación en las notas impresas (picking).
- **Maquila** (tercerización): envío de materiales al almacén del maquilador y costo del servicio sumado al producto.
- **Calidad**: plan de calidad por producto, inspecciones con resultado conforme/no conforme y cuarentena de lo rechazado.
- **Mantenimiento**: equipos, planes preventivos, órdenes de trabajo preventivas y correctivas, horas de parada y repuestos descargados del almacén (6343 al centro de costo).

**Contabilidad y planillas**
- **Libro de Inventarios y Balances** (formatos 3.x) con saldos por cuenta, tercero o documento, en Excel.
- **Vacaciones** (récord vacacional, goce y venta), **liquidación de beneficios sociales** (CTS, gratificación y vacaciones truncas, no gozadas, indemnizaciones) y **costo de planilla por centro de costo**.

## v1.18.0 — 05/10/2026 · Planillas y migración desde el sistema anterior
**Planillas** (módulo nuevo)
- **Trabajadores** con datos personales y laborales: régimen (general, pequeña o microempresa), centro de costo, sueldo, asignación familiar, ONP o AFP (comisión flujo o mixta, CUSPP) y cuentas de sueldo y CTS. Carga masiva desde Excel.
- **Planilla mensual**: básico por días (ingresos y ceses en el mes, faltas, vacaciones, descanso médico), asignación familiar, horas extra 25% y 35%, otros ingresos afectos y no afectos, ONP o AFP (aporte, prima con tope y comisión), **renta de quinta categoría** por proyección anual (con lo percibido y retenido antes de usar el sistema), adelantos y otros descuentos, y **EsSalud** del empleador (base mínima la RMV).
- **Gratificación** de julio y diciembre con la **bonificación extraordinaria del 9%** y **CTS** de mayo y noviembre (con 1/6 de la gratificación); pequeña empresa al 50% y microempresa sin estos beneficios.
- **Boletas de pago** imprimibles, Excel de la planilla, **archivos PLAME** (.rem, .jor y .snl) y aportes por AFP.
- Al cerrar, la planilla se **contabiliza** al centralizar el periodo (gasto 62 por centro de costo con su destino 90/94/95, ONP, AFP, quinta, EsSalud y neto a 4111); el **pago** se registra en tesorería. Permisos: calcular, cerrar/pagar y configurar.
- Configuración editable: RMV, UIT y tasas por año, tasas de cada AFP y código PLAME y cuenta de cada concepto.

**Migración desde el sistema anterior** (`python manage.py migrar_excels <carpeta>`): lee los Excel tal como están y carga almacenes, plan de cuentas, centros de costo, productos, clientes y proveedores (con su lista de precios), trabajadores, stock inicial valorizado por almacén y lote, comprobantes por cobrar y los correlativos de las series; con simulación previa y un Excel de observaciones.

## v1.17.0 — 05/10/2026 · Precios y descuentos, presupuesto, conciliación automática, XML/SIRE, API y variantes
**Ventas y compras**
- **Descuento por línea** (%) en ventas, cotizaciones, compras y órdenes de compra; se muestra en el comprobante impreso y viaja a SUNAT como descuento del ítem. El costo de lo comprado es el precio neto del descuento.
- **Listas de precios** (Ventas > Listas de precios) por cliente o canal, con vigencia y **precios por volumen** (tramos de cantidad). El cliente tiene su lista; la venta la toma sola (o se elige otra) y propone precio y descuento al elegir el producto o cambiar la cantidad.
- **Factor de conversión de compra**: se compra en cajas (ej. ×12) y se almacena por unidad; órdenes, facturas, recepciones y sugerencias de compra lo respetan.
- **Registrar compra desde el XML** del proveedor (facturas y notas de crédito/débito): valida que sea para la empresa y que no esté duplicada, crea al proveedor si no existe, sugiere el producto de cada línea (código, compras anteriores o nombre), comprueba el total y guarda el XML como sustento.
- **Cruce con la propuesta SIRE** (Compras > Reportes): compara la propuesta RCE de SUNAT con el Registro de Compras del periodo: coinciden, diferencias de importe, faltan registrar (o registradas en otro periodo) y solo en el ERP. Exportable a Excel.

**Finanzas y contabilidad**
- **Conciliación bancaria automática** (Finanzas > Bancos): se carga el estado de cuenta de **BCP, BBVA o Interbank** (Excel o CSV, formato reconocido solo) y cada línea se empareja con tesorería por N° de operación o por importe y fecha cercana. Comisiones e ITF se registran con un clic; enlace manual, ignorar y deshacer; partidas en tránsito y diferencia de saldo con el banco.
- **Presupuestos** (Contabilidad > Reportes): por cuenta, centro de costo y mes; se generan desde el real del año anterior con un % de variación; **ejecución** (presupuesto vs real acumulado, % de ejecución) exportable a Excel.
- **Estados financieros comparativos**: Estado de resultados vs mismo periodo del año anterior, periodo anterior o **presupuesto**; Situación financiera vs cierre del año anterior o mes anterior; con variación y %, exportación a **Excel** e impresión / PDF.

**Productos**
- **Variantes** (talla, color, presentación…): desde un producto plantilla se generan las combinaciones, cada una con su código, stock, kardex y precio. La plantilla no se usa en documentos.
- **Imagen del producto** (PNG, JPG, GIF o WEBP hasta 2 MB, validada por su contenido).

**Integraciones**
- **API REST v1** (`/api/v1/`) para tienda online, BI u otros sistemas: productos (con variantes e imagen), stock por almacén, clientes/proveedores (consulta y alta), ventas (consulta y **emisión** con las mismas validaciones de la pantalla), cuentas por cobrar y compras. **Claves de API** por usuario (menú del usuario): actúan con sus módulos y permisos, solo lectura o con registro, vencimiento, revocación y límite de 120 solicitudes por minuto. Documentación en `/api/v1/docs/` y descripción OpenAPI.

**Vista móvil**: revisadas las pantallas principales a 375 px; los comparativos y filtros ya no desbordan.

## v1.16.0 — 05/10/2026 · Manufactura y costos tipo SAP, estructura organizacional y requerimientos
**Correcciones**
- Terminar una orden de producción confirmada ya no da error (fecha como texto con kardex cerrado); se inicia sola.
- Contactos con RUC antiguo (datos de demostración) se pueden editar; el RUC se valida al crear o al cambiarlo.
- La guía generada desde una factura propone solo el saldo pendiente de despacho.
- **Diferencia de precio** entre la recepción (costo de la orden de compra) y la factura: se liquida sola; lo que sigue en stock revaloriza el costo promedio y lo ya vendido va al costo de ventas. La cuenta 28 queda en cero.
- Reapertura de periodos con el permiso "Reabrir periodos cerrados" (además del administrador).

**Estructura organizacional**
- **Centros de beneficio** (líneas de negocio) y **centros de costo con tipo** (producción, servicio, administración, ventas, finanzas), **jerarquía** y responsable. Los gastos de centros de producción van al **costo de producción (90)**, no a la 94.
- La venta toma la **línea del producto**; ingresos, costo de ventas e inventario se registran por línea. Nuevo **Estado de resultados por línea de negocio** con cuentas por cobrar e inventario por línea. Gastos por centro de costo con acumulado por jerarquía.

**Manufactura y costos (estilo SAP)**
- **Puestos de trabajo** con tipo, tarifas de mano de obra y máquina/CIF, turnos, calendario y eficiencia; **carga vs capacidad** por puesto.
- **Hojas de ruta** con operaciones 10, 20, 30… (preparación, ejecución por unidad y espera), reutilizables.
- **Listas de materiales** con estado (borrador / aprobada / obsoleta), vigencia, rango de lote, operación y almacén de consumo por insumo.
- **Versiones de fabricación** (receta + hoja de ruta + rango de lote + vigencia): la orden y el MRP eligen la versión según cantidad y fecha.
- **Costo estándar por periodo**: se calcula de abajo hacia arriba y se **libera**; las órdenes del periodo se comparan contra él (ya no se recalcula al confirmar).
- **Variaciones por tipo** en cada orden: precio y cantidad de materiales, eficiencia de mano de obra y de máquina/CIF, y tarifa.
- **Absorción de costos de planta**: gasto real de cada centro de costo de planta frente a lo absorbido por las órdenes (sub o sobreaplicación).
- **MRP multinivel**: desde pedidos de venta y plan de demanda, explota todas las recetas y propone órdenes de fabricar (semielaborados incluidos) y de comprar con fechas; se convierten en órdenes de producción y de compra.

**Requerimientos internos al almacén** (módulo Requerimientos)
- Las áreas piden materiales contra su centro de costo; se aprueban (quien pide no aprueba), el almacén entrega total o parcialmente, los faltantes pasan a órdenes de compra y el consumo va a la cuenta de gasto y al centro de costo del área.

**Carga masiva** de centros de beneficio, centros de costo, puestos, recetas, hojas de ruta y versiones, en ese orden.

## v1.15.0 — 04/10/2026 · Alertas y lotes, series y vencimientos
- **Alertas** (campana en la barra superior y página Alertas), según los módulos y permisos de cada usuario: cobranzas y pagos vencidos o por vencer en 7 días, comprobantes no aceptados por SUNAT, órdenes de compra por aprobar, facturas del portal por revisar, stock bajo, lotes por vencer, insumos que no alcanzan para producir, depreciación pendiente y periodos sin centralizar.
- **Lotes y números de serie**: cada producto puede controlarse por lote (con vencimiento) o por número de serie. Al ingresar mercadería por Inventario se indica el lote y su vencimiento (o las series); las salidas sin lote indicado toman primero lo que vence primero (FEFO). Anulaciones, notas de crédito y traslados devuelven o mueven la mercadería en los mismos lotes. En manufactura, el lote del producto es el número de la orden.
- **Lotes, series y vencimientos** (Inventario > Reportes): stock por lote y almacén, filtro de lotes por vencer (Excel) y **trazabilidad** de cada lote: de qué documento entró y a qué documento salió.

## v1.14.0 — 04/10/2026 · Permisos por acción y afectación del IGV por línea
- **Permisos por acción** (Ajustes > Usuarios y permisos): además de los módulos, qué puede hacer cada usuario: emitir, emitir notas de crédito, anular, aprobar órdenes de compra, ajustar o cerrar el kardex, extornar asientos, cerrar periodos, dar de baja activos, etc. Las acciones sensibles se marcan en rojo y vienen desmarcadas para usuarios nuevos. El control es del servidor (no basta con ocultar botones) y los botones "Anular" se ocultan a quien no puede.
- **Por almacén**: el usuario solo ve y opera en los almacenes asignados (operaciones, ventas, compras, guías y órdenes de producción).
- **Por serie**: el usuario solo emite con sus series (comprobantes y guías); si deja la serie vacía se usa la suya.
- **Límite de aprobación** de órdenes de compra por usuario (en soles): una orden mayor la aprueba quien tenga más límite.
- Los usuarios existentes conservan todo lo de sus módulos hasta que el administrador les asigne permisos. Los cambios de permisos quedan en la auditoría.
- **Afectación del IGV por línea**: una factura puede mezclar ítems gravados, exonerados e inafectos (columna "IGV" en el detalle). El producto puede tener su afectación (ej. libros exonerados) y se completa sola. Totales, envío a SUNAT (tipo de IGV por ítem), impresión y registro de ventas 14.1 separan gravado, exonerado, inafecto y exportación.

## v1.13.0 — 04/10/2026 · Correcciones de la segunda evaluación
- **La sesión ya no se cierra al publicar una versión**: el despliegue volvía a fijar la clave del administrador y eso invalidaba sus sesiones. Ahora la clave del entorno solo se usa al crear el usuario (DJANGO_SUPERUSER_RESET=1 la repone si se pierde el acceso).
- **Seguridad**: cabeceras HSTS (1 año) y CSP; redirección a HTTPS. El panel de Django ya no existe en /admin/ en producción: solo con una ruta secreta (ADMIN_URL), solo superusuarios y opcionalmente solo desde ciertas IP (ADMIN_IPS).
- **Recuperación de contraseña** por correo ("¿Olvidó su contraseña?"), con enlace de un solo uso y límite de solicitudes.
- **Verificación en dos pasos** (Google/Microsoft Authenticator) opcional por usuario, con 8 códigos de respaldo; el administrador puede quitarla con motivo si el usuario pierde el teléfono.
- **Comprobante emitido inmutable**: ya no se edita una factura/boleta emitida; se corrige con nota de crédito o anulación (solo se edita la que falló al enviarse).
- **Enviar comprobante** al cliente por correo (con PDF, XML y CDR del OSE) o por WhatsApp, desde el detalle; queda en la auditoría.
- **Exportar a Excel** las listas de ventas, compras, clientes, productos y demás listados, con los filtros aplicados (sin costos para quien no tiene ese permiso).
- **Inventario permanente SUNAT**: formatos **12.1** (unidades físicas) y **13.1** (valorizado) por mes o año, en pantalla y Excel con la estructura oficial (Inventario > Reportes y Contabilidad > Libros).
- **Guías vinculadas** en la factura muestran su estado (las anuladas, tachadas).
- **Reapertura de periodos** contables solo por el administrador, con motivo y registro en la auditoría.
- **Peso por unidad** en el producto: la guía de remisión calcula el peso bruto automáticamente.

## v1.12.0 — 04/10/2026 · Módulo de Activos fijos
- **Registro de activos**: código automático (AF000001), categoría, marca, modelo, serie/placa, ubicación, responsable y centro de costo. Se registran **desde la factura de compra** (botón "Registrar activo fijo", una o varias unidades) o como **saldo inicial** con su depreciación acumulada a una fecha; sin compra en el sistema se exige el documento de sustento.
- **Categorías** con sus cuentas (33/34, 39 y 68) y la tasa máxima de SUNAT: edificaciones 5 %, maquinaria 10 %, vehículos 20 %, muebles 10 %, cómputo 25 %, otros equipos 10 %, software 25 %; los terrenos no se deprecian.
- **Depreciación mensual** por el método lineal, mes a mes y en orden; si un activo tiene meses pendientes se acumulan en el mes que se calcula. Solo un administrador revierte el último mes, con motivo. Proyección de la depreciación en cada activo.
- **Bajas** (venta, siniestro, obsolescencia, donación) con motivo y sustento obligatorio: se deprecia hasta el mes de la baja. Un activo registrado por error se anula con motivo si aún no tiene depreciación. Con depreciación registrada ya no cambian su valor, vida útil ni fechas.
- **Contabilidad** (al centralizar): depreciación 68 / 39 por centro de costo; baja: 39 + costo neto 655 contra la cuenta del activo; reclasificación si la compra usó otra cuenta. Una compra con activos registrados no se anula.
- **Reportes**: Registro de activos fijos **Formato 7.1** (Excel con todas las columnas) y **cuadre** del registro con el libro mayor por cuenta.

## v1.11.0 — 04/10/2026 · Módulos de Manufactura y Costos
- **Manufactura** (módulo nuevo):
  - **Centros de trabajo** con costo por hora de mano de obra y de costos indirectos (CIF).
  - **Listas de materiales (recetas)**: insumos por lote con % de merma y horas por centro de trabajo. Una receta vigente por producto; si ya se usó en órdenes no se modifica, se crea una **nueva versión**.
  - **Órdenes de producción**: Borrador → Confirmada → En proceso → Terminada, o Anulada con motivo. Los insumos y horas se calculan de la receta según la cantidad; al terminar se registran los consumos y horas reales y se mueve el almacén (Tabla 12: 10 salida a producción, 19 entrada de producción). El producto entra al **costo por absorción**: materiales + mano de obra + CIF. Anular una orden terminada revierte el almacén.
  - **Requerimiento de materiales**: lo que piden las órdenes abiertas frente al stock y a lo pedido en compras (exportable a Excel).
- **Costos** (módulo nuevo, requiere el permiso "Ver costos de inventario"):
  - **Costo estándar** por producto según su receta, comparado con el costo promedio y el precio de venta (margen), con hoja de costos y simulación por cantidad.
  - **Costo real vs estándar** por orden terminada: variaciones de materiales, mano de obra y CIF, y rendimiento.
  - **Rentabilidad** por producto, cliente, vendedor o mes: ventas netas, costo de ventas del kardex y margen (las notas de crédito restan).
- **Contabilidad**: consumo de insumos en la cuenta 61 (variación de existencias) y producción en la 71 (variación de la producción almacenada) contra las cuentas de existencias de cada producto.

## v1.10.0 — 03/10/2026 · Todo con sustento y auditoría
- **Documentos de sustento** (PDF, imagen, Excel, Word, XML o ZIP; máx. 5 MB) adjuntos a asientos, movimientos de caja y bancos, operaciones de inventario, comprobantes, órdenes de compra y guías. Se guardan en la base de datos y no se pueden borrar.
- **Sustento obligatorio** en: asientos manuales; ingresos y egresos de caja y bancos sin comprobante (gastos, caja chica, tributos, préstamos…); saldo inicial, ajustes, consumos, destrucción y traslado a destrucción de inventario (no se confirman sin el documento). Las transferencias piden N° de operación o voucher. Los extractos y cargas masivas de saldos quedan adjuntos como sustento.
- **Asientos manuales**: ya no se editan ni eliminan; se corrigen con **extorno** (asiento inverso con motivo, enlazado al original).
- **Movimientos de caja y bancos**: ya no se eliminan; se **anulan con motivo** (quedan visibles con el filtro "Anulados" y fuera de saldos, cobros y contabilidad). Una transferencia se anula completa.
- **Comprobantes**: se quitó "eliminar"; solo se anulan con motivo.
- **Ajuste rápido de inventario**: ahora registra una operación numerada (saldo inicial, ajuste o consumo) con su sustento, anulable con motivo.
- **Bitácora de auditoría** (Ajustes > Auditoría, exportable a Excel): quién creó, modificó (campo, valor anterior y nuevo), anuló, extornó o adjuntó, cuándo y desde qué IP; también los inicios de sesión. Cada comprobante, asiento, operación, movimiento, orden y guía muestra su historial.

## v1.9.1 — 03/10/2026 · Saldo inicial con sustento
- El saldo inicial de caja y bancos ya no se edita libremente: al crear la cuenta se exige el motivo y el documento de sustento (extracto, acta de arqueo; PDF, imagen o Excel).
- Después solo un administrador puede **regularizarlo** con motivo y sustento; no se permite si el periodo contable de apertura está cerrado ni si deja la cuenta en negativo.
- Historial de cada cambio (anterior, nuevo, motivo, documento, usuario y fecha) en Caja y bancos > Saldo inicial. Los saldos registrados antes se muestran "sin sustento" para documentarlos.

## v1.9.0 — 03/10/2026 · Correcciones de la evaluación funcional
- **Bancos y caja nunca en negativo**: el saldo se valida día por día (un egreso con fecha anterior a un ingreso ya no usa ese dinero), al eliminar ingresos o transferencias, al importar extractos y al cambiar el saldo inicial. La caja no admite sobregiro aunque se marque; un banco solo con sobregiro autorizado. Caja y bancos avisa si quedaron saldos negativos de antes para corregirlos.
- **Facturación electrónica (63)**: los comprobantes que se intentaron enviar antes de configurar Nubefact quedan "No enviado" (no "Error de envío") y se pueden enviar; se corrigieron los existentes.
- **Despachos parciales (40)**: una guía de una venta no puede despachar más de lo facturado (sumando las guías emitidas y descontando devoluciones) ni productos que no están en la factura.
- **Control de crédito (37)**: límite de crédito por cliente y bloqueo de ventas al crédito con deuda vencida (con días de gracia, en Ajustes > Empresa).
- **Libro Mayor PLE 6.1 (67)**: exportación TXT del mayor del mes.
- **Sugerencia de compra (34)**: Inventario > Reportes > Sugerencia de compra según punto de reorden, stock máximo, lote y lo que ya está en camino; crea la orden de compra por proveedor.
- **Saldos iniciales por cobrar y por pagar (77)**: carga masiva de comprobantes pendientes de antes de usar el sistema; se cobran/pagan normalmente, no van a los registros de ventas/compras ni a SUNAT y se contabilizan contra la apertura (5911).
- **Seguridad (74)**: bloqueo temporal de 15 minutos tras 5 intentos fallidos de inicio de sesión (el administrador lo desbloquea al editar el usuario).
- **Respaldos (80)**: Ajustes > Respaldo de datos descarga una copia completa (solo administradores).

## v1.8.0 — 03/10/2026 · Fase 3: control de inventario y seguridad
- **Costos ocultos según permiso**: nuevo permiso "Puede ver costos de inventario" en Ajustes > Usuarios y permisos (los administradores siempre los ven). Sin él, en Inventario no se ve el costo promedio, el valorizado, el kardex valorizado (se muestra en unidades), los costos de las operaciones ni la valorización al cierre; tampoco en los Excel ni en los datos que se envían al navegador.
- **Carga masiva desde Excel** (Ajustes > Configuración > Carga masiva, también desde Inventario): productos, clientes y proveedores, y saldos iniciales de inventario. Plantilla descargable, validación fila por fila sin grabar nada (tipo de producto, unidad, RUC con dígito verificador, DNI, ubigeo, almacén, cantidades y costos), opción de actualizar los existentes y carga "todo o nada". Los saldos iniciales se registran como operaciones "Saldo inicial" confirmadas (una por almacén y fecha).
- **Cierre de kardex** (Inventario > Reportes > Cierre de kardex): cierra el mes y guarda la valorización por producto y almacén (exportable a Excel). Con el kardex cerrado no se pueden registrar, editar ni anular compras, ventas, guías, ajustes u operaciones que muevan el almacén con fecha de ese periodo. No se cierra un mes que no terminó ni con operaciones en borrador. Solo un administrador puede reabrir el último cierre (con motivo).

## v1.7.3 — 03/10/2026 · Factura solo con mercadería ingresada al almacén
- El proveedor solo puede cargar la factura de una orden de compra cuando la mercadería **ya ingresó al almacén** (recepción confirmada), y solo por las cantidades ingresadas pendientes de facturar. Mientras tanto la orden figura como "Pendiente de ingreso" en el portal.
- Al aprobar la factura en el ERP se vuelve a verificar que la recepción siga vigente.
- Las líneas de servicios (no inventariables) se facturan por lo pedido.

## v1.7.2 — 03/10/2026 · Carga de facturas en el portal de proveedores
- En el portal, cada orden de compra tiene el botón **Cargar factura**: el proveedor sube el **PDF** (obligatorio) y el **XML** de su factura electrónica (o el .zip de SUNAT).
- Con el XML se completan solos la serie, número, fecha, monto total y las cantidades y precios de cada línea.
- **La factura debe cuadrar**: cantidad contra lo recibido (±1), precio contra la orden (±1), monto total contra cantidades × precios + IGV (±1, configurable en Ajustes > Empresa) y, si hay XML, el RUC del emisor y del receptor, la moneda, la cantidad total y el valor de venta del XML. El formulario muestra en vivo si la factura cuadra.
- Los archivos se guardan en la base de datos (no se pierden al desplegar) y se pueden ver desde el portal y desde Compras > Portal de proveedores, junto con el cuadre (total de la factura vs. calculado).
- Las órdenes aprobadas también aparecen en el portal.

## v1.7.1 — 03/10/2026 · Nueva identidad: ceiba ERP
- Logotipo de ceiba (isotipo en SVG: completo, reducido para favicon, negativo para fondos oscuros e icono de app).
- Paleta de la marca en todo el sistema: Selva #0F3D2E (barra superior y botones), Hoja #1F7A52, Sol #E3A72F y Bruma #F4F6F2 (fondo); tipografía Sora.
- Nueva pantalla de inicio de sesión, barra superior con el logotipo (el color de cada módulo queda como línea inferior) y portal de proveedores con la marca.
- El sistema se llama **Ceiba ERP** (antes "CEIVA ERP").

## v1.7.0 — 03/10/2026 · Fase 2: compras, proveedores y logística
- **Orden de compra con centro de costo obligatorio** y días de crédito (por defecto los del proveedor). Opción "Exigir orden de compra" (Ajustes > Empresa, activa por defecto): las compras nuevas de mercadería solo se registran desde una orden; la compra hereda el centro de costo de la orden.
- **Envío de la orden al proveedor por correo** con un enlace para aceptarla o rechazarla (sin necesidad de usuario). El detalle de la orden muestra la respuesta y un enlace para copiar y enviarlo por otro medio.
- **Conformidad de recepción**: al confirmar la recepción de una compra en Inventario se envía al proveedor el correo de aceptación de la mercadería (también se puede reenviar).
- **Portal de proveedores** (/portal/): cada proveedor ingresa con su usuario, ve sus órdenes y recepciones, acepta las órdenes y registra sus facturas. Se valida: cantidad contra lo recibido (o lo pedido) y precio contra la orden, con tolerancia ±1 configurable; facturas duplicadas; y la validez en SUNAT (API de consulta de comprobantes, con las credenciales en Ajustes > Empresa). Los usuarios del portal no pueden entrar al ERP.
- **Compras > Portal de proveedores**: revisión de las facturas (diferencias de cantidad y precio, validación SUNAT), aprobación que registra la compra automáticamente, rechazo con motivo y administración de los accesos de los proveedores.
- **Vencimiento desde el ingreso de la mercadería**: las facturas de una orden vencen a los días de crédito contados desde la fecha de ingreso al almacén (se recalcula con cada recepción).
- **Ajustes > Correo saliente** (SMTP: Gmail, Outlook u otro) con botón de prueba.

## v1.6.0 — 03/10/2026 · Fase 1: configuración base y maestros
- **Productos por tipo y código**: Mercadería, Materia prima, Semi elaborado, Producto terminado, Suministros, Activo fijo y Servicio. Si el código se deja vacío se genera solo con el prefijo del tipo (ME, MP, SE, PT, SU, AF, SV + 6 dígitos). El tipo no se cambia después de creado.
- **Ficha del producto con pestañas** (General, Compras, Ventas, Contabilidad, Planificación): marca, código de barras, descripción, precio de compra, proveedor habitual, unidad de compra, precio de venta, stock mínimo y máximo, punto de reorden, lote mínimo de compra, tiempo de entrega y almacén por defecto. Filtro por tipo en la lista de productos.
- **Juego de cuentas por producto** (existencias, compra, ventas y costo), completado automáticamente según el tipo. La contabilidad automática lo usa en compras, ventas, costo de ventas y en la valorización de cada cuenta de existencias (20, 21, 23, 24, 25). Se agregaron al plan de cuentas las cuentas de materias primas, productos terminados, productos en proceso y suministros (21, 23, 24, 25, 284, 285, 602, 603, 612, 613, 692, 702).
- **Activo fijo**: es solo de compra; la ficha muestra "Precio" (de compra) y no el precio de venta; no es inventariable.
- **Ubigeo en cascada** (Departamento → Provincia → Distrito) con la tabla oficial del INEI (1874 distritos) en almacenes, empresa y clientes/proveedores; el ubigeo se completa solo y se valida.
- **Tipo de cambio SBS**: en Ajustes > Empresa se elige la fuente (SUNAT o SBS promedio ponderado vía Decolecta, con token). Si la SBS no responde se usa SUNAT y se avisa.
- En compras y órdenes de compra el precio sugerido es el precio de compra del producto.

## v1.5.0 — 03/10/2026 · Operaciones de inventario
- Nuevo menú Inventario > Operaciones con 14 tipos de operación: Saldo inicial, Recepción de compras, Devolución de clientes, Ajuste ingreso, Salida por ventas, Devolución a proveedor, Ajuste salida, Consumo interno, Consumo mantenimiento, Salida a destrucción, Traslado a destrucción, Traslado a tránsito, Recepción de tránsito y Manufactura.
- Cada operación se guarda en borrador y mueve el almacén al confirmarse; se anula con motivo (revierte el movimiento). Numeración propia: NI (ingresos), NS (salidas), NT (traslados), MF (manufactura). Impresión de la nota con firmas.
- Recepción de compras parcial desde la orden de compra o la factura: el sistema muestra lo pendiente y no deja recibir de más. La factura de una orden ya recibida no vuelve a ingresar la mercadería.
- Devoluciones de clientes y a proveedores limitadas a lo vendido/recibido; la devolución de cliente se valoriza al costo con que salió. Botón para emitir la nota de crédito sin volver a mover el almacén.
- Mercadería en tránsito: el traslado la deja en el almacén virtual "Mercadería en tránsito" hasta que se recibe (también parcialmente) en el destino.
- Destrucción en dos pasos: traslado al almacén "Cuarentena / por destruir" y salida a destrucción.
- Manufactura: consume insumos y el costo del producto terminado es el valor de los insumos.
- Tipos de operación configurables (Inventario > Configuración): cuenta contable de contrapartida y código de la tabla 12 de SUNAT. El kardex muestra ese código.
- Botón "Almacén" en órdenes de compra, compras y ventas para recibir, despachar o devolver.
- Contabilidad: cada operación usa la cuenta de su tipo (ej. consumo interno 6561, mantenimiento 6343, saldo inicial 5911); traslados y manufactura no afectan resultados.

## v1.4.1 — 03/10/2026 · Nuevo nombre: CEIVA ERP
- El sistema se llama **CEIVA ERP**: inicio de sesión, barra superior, pestaña del navegador, pie de página, administración, impresiones e iniciar.bat. El nombre se define en un solo lugar (ERP_NOMBRE en erp/settings.py).

## v1.4.0 — 03/10/2026 · Corrección de los hallazgos del informe de QA
**Inventario**
- BUG-01: no se puede vender, devolver a proveedor ni despachar con guía más de lo que hay en el almacén (opción "Permitir vender sin stock" en Ajustes > Empresa, desactivada por defecto). Tampoco se puede anular una compra cuya mercadería ya salió.
- Corregido: dos líneas del mismo producto en un comprobante ya no se pisan al mover el stock.
- BUG-02: una guía con salida de almacén no puede volver a descontar la mercadería de una factura que ya la descontó.
- BUG-03: en la guía de traslado entre establecimientos (motivo 04/18) el destinatario es automáticamente la propia empresa.
- Ajustes de inventario con concepto (inventario inicial, sobrante, merma, consumo) que define su cuenta contable.

**Contabilidad**
- NEW-07: contabilidad automática. Cada cambio marca su mes y al abrir cualquier pantalla de Contabilidad se centraliza solo.
- NEW-01: asiento de apertura automático con los saldos iniciales de caja y bancos. Las cuentas 12 y 42 cuadran al céntimo con cuentas por cobrar y por pagar.
- NEW-02: la compra de mercadería pasa por 2811 (por recibir) y el kardex mueve la 20111. La 20111 queda igual a la valorización del inventario al cierre de cada mes.
- NEW-03: subcuenta propia por cada caja y banco (10111, 10411, ...), diferencia de cambio en cobros y pagos de documentos en dólares (6761/7761) y ajuste de las cuentas en dólares al tipo de cambio de cierre.
- NEW-08: importes en soles calculados una sola vez por documento y por pago; registro de ventas/compras, cuentas por cobrar/pagar y contabilidad usan los mismos.
- Pagos en una moneda distinta a la del documento (por ejemplo, detracción de una factura en dólares depositada en soles): se distingue el monto del banco y el monto aplicado al documento.
- Percepciones de ventas contabilizadas.

**Control y rendimiento**
- BUG-04: la anulación pide motivo y registra quién y cuándo (comprobantes y guías).
- NEW-05: no se aceptan egresos que dejen una cuenta en negativo (opción "Permite sobregiro" por cuenta).
- NEW-04: Tablero y cuentas por cobrar calculan saldos en una sola consulta (de ~5 s a menos de 0,1 s con 150 documentos).
- NEW-06: libro diario paginado.
- BUG-05: estados SUNAT coherentes. "No enviado" con lo que hay que corregir cuando faltan datos; "Error de envío" solo si responde el OSE con error; si el OSE ya tenía el documento, se trae su estado real.
- El tipo de cambio ya no se consulta para fechas futuras ni insiste cuando la API limita las consultas.

## v1.3.2 — 03/10/2026 · Facturación electrónica según el manual oficial de Nubefact
- Envío ajustado al manual oficial de Nubefact (comprobantes y Guías de Remisión Electrónica v1.7) y a sus ejemplos JSON.
- Guía transportista: el remitente va como "cliente" y el destinatario en sus propios campos; ya no se envían campos que solo existen en la guía remitente.
- Guía remitente en transporte público: fecha de entrega al transportista y registro MTC. Las guías se envían en dos pasos (generar y consultar).
- Detracción: el código de bien o servicio (catálogo 54) se elige en cada venta; antes estaba fijo y era incorrecto.
- Exportaciones, retenciones, percepciones y ventas al crédito con los campos oficiales.
- Notas de crédito y débito de boletas usan series que empiezan con B (BC01, BD01). Se valida la letra de la serie.
- Validaciones antes de enviar (serie, dirección, ubigeo, placa, licencia, peso) con mensajes claros.
- Botón "Probar conexión con Nubefact" en Ajustes > Facturación electrónica.
- Validación del dígito verificador del RUC (módulo 11 de SUNAT) al registrar clientes/proveedores y antes de enviar.
- Probado contra la cuenta DEMO de Nubefact: factura, nota de crédito, nota de débito, guía remitente y guía transportista ACEPTADAS por SUNAT; boletas registradas a la espera del resumen diario.

## v1.3.1 — 03/10/2026 · Versión en los títulos
- El título del sistema (inicio de sesión, barra superior, pestaña del navegador, administración e impresiones) muestra "ERP" con la versión instalada.
- Pie de página "Creado por J. Flores" en todas las pantallas y en el inicio de sesión.

## v1.3 — 03/10/2026 · Módulos estilo Odoo
- Pantalla de aplicaciones y barra de menú propia para cada módulo.
- Usuarios y permisos por módulo (Ajustes > Usuarios y permisos).
- Cambio de contraseña para cada usuario.
- Corrección: la valorización de inventario al cierre ahora es correcta aunque haya movimientos con fecha anterior.

## v1.2 — 02/10/2026 · Contabilidad
- Plan Contable General Empresarial (PCGE) y configuración de cuentas por operación.
- Centralización automática de compras, ventas, caja/bancos y costo de ventas.
- Asientos manuales, centros de costo y cierre de periodos.
- Libro diario (PLE 5.1), libro mayor, balance de comprobación, situación financiera y estado de resultados.

## v1.1 — 01/10/2026 · Logística, inventario y SUNAT
- Guías de remisión remitente y transportista, vehículos y conductores.
- Almacenes múltiples, kardex valorizado, ajustes y valorización al cierre.
- Tipo de cambio SUNAT automático y caja/bancos consolidado en soles.
- Facturación electrónica vía OSE (Nubefact) y notas de crédito/débito con asistente.

## v1.0.1 — 01/10/2026 · Despliegue en Render
- El usuario administrador se crea en cada despliegue y los datos de demostración son opcionales.

## v1.0 — 01/10/2026 · Primera versión
- Compras, ventas y finanzas (caja y bancos), registros 8.1 / 14.1 con TXT PLE, cuentas por cobrar y pagar, conciliación bancaria.
