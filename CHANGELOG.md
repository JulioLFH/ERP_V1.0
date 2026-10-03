# Historial de versiones — ERP

Cada versión es una "etiqueta" (tag) en GitHub: https://github.com/JulioLFH/ERP_V1.0/tags
El número de versión instalada se ve en el sistema, en el menú del usuario (arriba a la derecha).

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
