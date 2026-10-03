# Historial de versiones — CEIVA ERP

Cada versión es una "etiqueta" (tag) en GitHub: https://github.com/JulioLFH/ERP_V1.0/tags
El número de versión instalada se ve en el sistema, en el menú del usuario (arriba a la derecha).

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
