# Historial de versiones — Ceiba ERP

Cada versión es una "etiqueta" (tag) en GitHub: https://github.com/JulioLFH/ERP_V1.0/tags
El número de versión instalada se ve en el sistema, en el menú del usuario (arriba a la derecha).

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
