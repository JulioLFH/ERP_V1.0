"""Casos de prueba de la auditoría del proceso de venta (de la cotización a la cobranza y las correcciones).

Cada caso: (código, etapa, caso, pasos, datos de prueba, resultado esperado, control / riesgo, prioridad, tipo).
tipo: CONTROL = control que el sistema ya aplica (se confirma); REVISAR = punto detectado al leer el código, que la
prueba debe confirmar o descartar.
"""
ALTA, MEDIA, BAJA = 'Alta', 'Media', 'Baja'
CONTROL, REVISAR = 'Control', 'Punto a revisar'

ETAPAS = ['1. Datos maestros', '2. Cotización y pedido', '3. Aprobación del pedido', '4. Emisión del comprobante',
          '5. Almacén y costo', '6. SUNAT', '7. Contabilidad', '8. Cobranza y crédito', '9. Notas y anulación',
          '10. Permisos y segregación', '11. Registro y reportes']

CASOS = [
    # ------------------------------------------------------------ 1. datos maestros
    ('V-01', ETAPAS[0], 'Factura a cliente sin RUC',
     'Emitir comprobante › Tipo 01 Factura › elegir un cliente con DNI › Guardar.',
     'Cliente con DNI (tipo doc 1).', 'No guarda: «Las facturas requieren un cliente con RUC».',
     'Comprobante mal emitido ante SUNAT.', ALTA, CONTROL),
    ('V-02', ETAPAS[0], 'Serie que no corresponde al tipo',
     'Emitir factura con serie B001; luego boleta con serie F001.', 'Series B001 / F001.',
     'No guarda: la factura y sus notas empiezan con F, la boleta y sus notas con B (4 caracteres).',
     'Numeración inválida ante SUNAT.', ALTA, CONTROL),
    ('V-03', ETAPAS[0], 'Numeración correlativa automática',
     'Emitir dos facturas seguidas dejando el número vacío.', 'Serie F001.',
     'Números consecutivos sin saltos ni repetidos; el número no se puede cambiar después de emitir.',
     'Huecos o duplicados en el registro de ventas.', ALTA, CONTROL),
    ('V-04', ETAPAS[0], 'Precio desde la lista del cliente',
     'Cliente con lista de precios asignada › agregar un producto con tramo por cantidad (ej. desde 10 u).',
     'Lista con precio especial desde 10 u.', 'Con 9 u toma el precio normal; con 10 u el de la lista.',
     'Precio mal aplicado.', MEDIA, CONTROL),
    ('V-05', ETAPAS[0], 'Lista de precios vencida',
     'Lista con «vigente hasta» ayer › emitir venta a un cliente con esa lista.', 'Lista vencida.',
     'No aplica el precio de la lista vencida (usa el precio de venta del producto).',
     'Descuentos fuera de vigencia.', MEDIA, CONTROL),

    # ------------------------------------------------------------ 2. cotización y pedido
    ('V-06', ETAPAS[1], 'Cotización crea su oportunidad en el CRM',
     'Ventas › Cotizaciones y pedidos › Nueva (tipo Cotización) sin oportunidad › Guardar.', 'Monto sobre el '
     'parámetro del CRM.', 'Mensaje «Se creó la oportunidad…» y la oportunidad queda enlazada.',
     'Embudo comercial incompleto.', BAJA, CONTROL),
    ('V-07', ETAPAS[1], 'Pedido aprobado que se modifica',
     'Pedido en «Aprobado» › Editar › cambiar una cantidad › Guardar.', 'Pedido aprobado sin comprobante.',
     'Vuelve a «Pendiente» y debe aprobarse otra vez (aviso en pantalla y en el historial).',
     'Cambios después de la aprobación.', ALTA, CONTROL),
    ('V-08', ETAPAS[1], 'Pedido facturado por partes',
     'Pedido de 10 u aprobado › emitir factura por 6 u › volver al pedido › emitir otra factura.',
     'Pedido 10 u.', 'Tras la 1.ª factura el pedido sigue «Aprobado» (Facturado parcial); la 2.ª propone 4 u; con '
     'todo facturado pasa a «Atendido».', 'Pedido cerrado antes de tiempo o saldo perdido.', ALTA, CONTROL),
    ('V-09', ETAPAS[1], 'Facturar más de lo pedido',
     'Pedido de 10 u aprobado › emitir factura por 12 u.', 'Pedido 10 u.',
     'No guarda: «No se factura más que el pedido…».', 'Facturación en exceso.', ALTA, CONTROL),
    ('V-10', ETAPAS[1], 'Producto que no está en el pedido',
     'Pedido aprobado con producto A › emitir factura desde el pedido y agregar una línea con producto B.',
     'Producto B no pedido.', 'Debería bloquear o pedir autorización. En el código el tope solo revisa los '
     'productos del pedido: B pasaría sin control. Confirmar.',
     'Se factura y despacha lo que no se aprobó.', ALTA, REVISAR),
    ('V-11', ETAPAS[1], 'Cotización (no pedido) sin tope de cantidades',
     'Cotización aceptada de 5 u › emitir comprobante desde ella con 50 u y otro precio.', 'Cotización 5 u.',
     'El tope de cantidades aplica solo a pedidos (PED); con una cotización (COT) se puede facturar cualquier '
     'cantidad. Los precios sí se validan contra la lista. Decidir si es aceptable.',
     'Venta distinta a lo cotizado sin aprobación.', MEDIA, REVISAR),
    ('V-12', ETAPAS[1], 'Anular un pedido con facturas vigentes',
     'Pedido con una factura registrada › Anular pedido.', 'Pedido con factura.',
     'No anula: «tiene comprobantes vigentes…: cierre su saldo».', 'Pedido anulado con venta viva.', MEDIA,
     CONTROL),
    ('V-13', ETAPAS[1], 'Cerrar saldo del pedido',
     'Pedido facturado parcial › Cerrar saldo con motivo de menos de 5 letras; luego con motivo válido.',
     'Pedido parcial.', 'Sin motivo no deja; con motivo pasa a «Atendido» y el historial muestra lo pendiente '
     'que ya no se facturará.', 'Saldos cerrados sin sustento.', MEDIA, CONTROL),

    # ------------------------------------------------------------ 3. aprobación
    ('V-14', ETAPAS[2], 'Pedido bajo la tarifa requiere aprobación',
     'Pedido con precio menor al de la lista › Enviar a aprobación.', 'Precio 10 % bajo la lista.',
     'Queda a la espera con el motivo «Bajo la tarifa…»; no se aprueba solo.', 'Descuentos no autorizados.',
     ALTA, CONTROL),
    ('V-15', ETAPAS[2], 'Pedido que excede el crédito',
     'Cliente con límite S/ 1,000 y deuda S/ 800 › pedido a crédito por S/ 500 › Enviar.', 'Límite 1,000.',
     'Requiere aprobación con motivo «Crédito: deuda … supera el límite…».', 'Riesgo de incobrable.', ALTA,
     CONTROL),
    ('V-16', ETAPAS[2], 'Quien registra no aprueba su pedido',
     'Usuario A registra el pedido › el mismo usuario A intenta aprobar.', 'Dos usuarios.',
     'No deja aprobar (segregación); lo aprueba otro usuario.', 'Autoaprobación.', ALTA, CONTROL),
    ('V-17', ETAPAS[2], 'Aprobación por niveles en orden',
     'Configurar reglas de 2 niveles para «Pedidos de venta» › el nivel 2 intenta aprobar primero.',
     'Reglas de 2 niveles.', 'No deja: «El pedido lo aprueba ahora el nivel 1…»; luego nivel 1 y nivel 2.',
     'Saltarse niveles.', ALTA, CONTROL),
    ('V-18', ETAPAS[2], 'Emitir comprobante desde pedido sin aprobar',
     'Pedido pendiente que requiere aprobación › abrir /ventas/nuevo/?cot=N directamente.', 'Pedido pendiente.',
     'Mensaje de bloqueo y al guardar no emite el comprobante.', 'Saltarse la aprobación por la URL.', ALTA,
     CONTROL),
    ('V-19', ETAPAS[2], 'Pedido aprobado autoriza el precio en la factura',
     'Pedido bajo la tarifa aprobado › emitir factura con usuario SIN «Vender bajo el precio de la lista».',
     'Pedido aprobado.', 'La factura se emite (el precio ya se aprobó en el pedido).',
     'Control duplicado o bloqueo indebido.', MEDIA, CONTROL),

    # ------------------------------------------------------------ 4. emisión
    ('V-20', ETAPAS[3], 'Venta bajo la tarifa sin pedido',
     'Usuario sin «Vender bajo el precio de la lista» › factura directa con precio menor al de la lista.',
     'Precio bajo lista.', 'No guarda: «Precio bajo la tarifa…».', 'Descuentos no autorizados.', ALTA, CONTROL),
    ('V-21', ETAPAS[3], 'Venta sin stock',
     'Factura de un producto con stock 2 u por 5 u, con «Mover almacén» marcado.', 'Stock 2 u.',
     'No guarda: indica el faltante.', 'Stock negativo.', ALTA, CONTROL),
    ('V-22', ETAPAS[3], 'Venta en periodo contable cerrado',
     'Cerrar el periodo › emitir factura con fecha de ese periodo.', 'Periodo cerrado.',
     'No guarda: periodo cerrado.', 'Alterar meses ya declarados.', ALTA, CONTROL),
    ('V-23', ETAPAS[3], 'Crédito: vencimiento y cuotas',
     'Factura a crédito con cuotas = 3 y vencimiento vacío; luego cuotas = 2 al contado; luego vencimiento '
     'igual a la emisión.', 'Cliente con 30 días de crédito.',
     '1) Vence a los 30 días y la impresión muestra 3 cuotas que suman el neto. 2) Error «solo al crédito». '
     '3) Error «vencimiento posterior a la emisión».', 'Cronograma mal informado a SUNAT.', MEDIA, CONTROL),
    ('V-24', ETAPAS[3], 'Detracción automática sobre S/ 700',
     'Factura de un servicio con % de detracción por S/ 800 dejando % detracción en 0.', 'Producto con 12 %.',
     'Toma el 12 % y el código del producto; el monto se descuenta del cobro esperado.',
     'Detracción omitida.', MEDIA, CONTROL),
    ('V-25', ETAPAS[3], 'Cambiar el vendedor',
     'Usuario sin «Cambiar el vendedor de un documento» › emitir venta poniendo a otro vendedor.',
     'Dos vendedores.', 'No guarda: «Su usuario no cambia el vendedor…».', 'Comisiones mal asignadas.', MEDIA,
     CONTROL),
    ('V-26', ETAPAS[3], 'Editar un comprobante emitido',
     'Factura registrada (aceptada o no enviada) › Editar.', 'Factura emitida.',
     'Solo se edita la que está en «Error de envío». En el código una venta «No enviado» (sin facturación '
     'electrónica activa) tampoco se edita: se corrige anulando y reactivando o con nota. Confirmar si es lo '
     'deseado.', 'Correcciones sin rastro / operación bloqueada.', MEDIA, REVISAR),
    ('V-27', ETAPAS[3], 'Ticket (12) y nota de venta (00)',
     'Emitir un ticket y una nota de venta con productos.', 'Tipos 12 y 00.',
     'Descuentan stock. Revisar si deben ir al Registro de ventas 14.1 / PLE y a la contabilidad (la nota de '
     'venta 00 no es comprobante SUNAT).', 'Ingresos sin declarar o declarados de más.', ALTA, REVISAR),

    # ------------------------------------------------------------ 5. almacén y costo
    ('V-28', ETAPAS[4], 'Salida de almacén al emitir',
     'Factura con «Mover almacén» › revisar kardex del producto.', 'Producto inventariable.',
     'Salida con la referencia de la factura, la fecha de emisión y el almacén elegido.',
     'Inventario descuadrado.', ALTA, CONTROL),
    ('V-29', ETAPAS[4], 'Doble salida con despacho de Inventario',
     'Factura sin mover almacén › despacho (salida por ventas) en Inventario › editar la factura y marcar '
     '«Mover almacén».', 'Factura con despacho.', 'No deja: «ya tiene despachos… no mover el almacén dos veces».',
     'Doble descuento de stock.', ALTA, CONTROL),
    ('V-30', ETAPAS[4], 'Venta con cierre de inventario',
     'Cerrar el inventario del mes › emitir factura con «Mover almacén» en ese mes.', 'Mes cerrado.',
     'No guarda: mensaje del cierre de inventario.', 'Costo de un mes cerrado alterado.', MEDIA, CONTROL),

    # ------------------------------------------------------------ 6. SUNAT
    ('V-31', ETAPAS[5], 'Envío automático que falla',
     'Con envío automático activo, emitir factura con un dato que el OSE rechace (o sin conexión).',
     'Facturación activa.', 'Queda en «Error de envío» con el detalle; en pantalla no avisa al guardar (envío '
     'silencioso). Verificar que la alerta de Inicio la muestre y que se reenvíe desde el comprobante.',
     'Comprobantes no informados a SUNAT.', ALTA, REVISAR),
    ('V-32', ETAPAS[5], 'Anular comprobante aceptado',
     'Factura «Aceptado» sin cobros › Anular con motivo.', 'Factura aceptada.',
     'Envía la comunicación de baja y queda anulada; si SUNAT responde error, no se anula.',
     'Anulación local sin baja en SUNAT.', ALTA, CONTROL),
    ('V-33', ETAPAS[5], 'Reactivar comprobante ya informado',
     'Factura anulada que llegó a SUNAT › Reactivar.', 'Factura con baja.',
     'No deja: «Ya se informó a SUNAT: emita un comprobante nuevo».', 'Reusar un número dado de baja.', ALTA,
     CONTROL),

    # ------------------------------------------------------------ 7. contabilidad
    ('V-34', ETAPAS[6], 'Asiento de la venta',
     'Emitir factura gravada › Contabilidad › Centralización y periodos › Centralizar el mes › Libro diario.',
     'Factura S/ 118.', 'Debe 12 Clientes 118; Haber 40 IGV 18; Haber 70 (cuenta del producto) 100, con centro '
     'de costo y línea de negocio.', 'Estados financieros errados.', ALTA, CONTROL),
    ('V-35', ETAPAS[6], 'Asiento pendiente sin centralizar',
     'Emitir ventas y NO centralizar › comparar Registro de ventas con el Libro diario / Balance.', 'Mes abierto.',
     'El periodo figura como «pendiente» de centralizar. Ventas y contabilidad no cuadran hasta centralizar: '
     'verificar que el cierre del mes lo exija.', 'Estados financieros sin las ventas del mes.', ALTA, REVISAR),
    ('V-36', ETAPAS[6], 'Venta en dólares',
     'Factura en USD con tipo de cambio 3.75 › centralizar.', 'USD 100.',
     'Asiento en soles al TC del comprobante y con el importe en moneda extranjera.',
     'Diferencias de cambio mal calculadas.', MEDIA, CONTROL),
    ('V-37', ETAPAS[6], 'Costo de ventas',
     'Venta de producto inventariable › centralizar › revisar asiento de inventario.', 'Producto con costo.',
     'Debe 69 Costo de ventas / Haber 20 Mercaderías por el costo del kardex.', 'Margen errado.', ALTA, CONTROL),
    ('V-38', ETAPAS[6], 'Retención y percepción',
     'Factura con 3 % de retención; otra con percepción › centralizar.', 'Cliente agente de retención.',
     'Retención: Debe 40 (IGV retenido) / Haber 12. Percepción: Debe 12 / Haber 40 percepción.',
     'Saldo del cliente errado.', MEDIA, CONTROL),

    # ------------------------------------------------------------ 8. cobranza y crédito
    ('V-39', ETAPAS[7], 'Cliente con deuda vencida',
     'Empresa con «bloquear deuda vencida» › cliente con factura vencida › nueva venta a crédito.',
     'Factura vencida + días de gracia.', 'No guarda la venta a crédito; al contado sí.', 'Riesgo de incobrable.',
     ALTA, CONTROL),
    ('V-40', ETAPAS[7], 'Cliente bloqueado por mora',
     'Marcar el cliente bloqueado por mora (Gestión de cobranza) › venta a crédito.', 'Cliente bloqueado.',
     'No guarda: «está bloqueado por mora…».', 'Venta a moroso.', ALTA, CONTROL),
    ('V-41', ETAPAS[7], 'Límite de crédito con letras e ICBPER',
     'Cliente con límite S/ 1,000, deuda S/ 500 y una letra pendiente S/ 300 › venta a crédito S/ 250 con bolsas '
     '(ICBPER) y percepción.', 'Límite 1,000.',
     'Debe bloquear (500 + 300 + 250 > 1,000). Revisar: el cálculo de la venta nueva suma base + IGV, sin ICBPER '
     'ni percepción.', 'Límite superado por montos pequeños.', BAJA, REVISAR),
    ('V-42', ETAPAS[7], 'Saldo después de cobro parcial',
     'Factura S/ 1,000 › cobrar S/ 400 en Finanzas › Cobranzas › ver Cuentas por cobrar.', 'Factura 1,000.',
     'Saldo S/ 600 (total − NC + ND − retención + percepción − cobrado).', 'Cuentas por cobrar erradas.', ALTA,
     CONTROL),

    # ------------------------------------------------------------ 9. notas y anulación
    ('V-43', ETAPAS[8], 'Nota de crédito por devolución',
     'Factura con producto › Emitir NC motivo 07 «Devolución por ítem».', 'Factura con stock aplicado.',
     'La mercadería reingresa al costo con que salió en la factura; el saldo de la factura baja.',
     'Inventario valorizado mal.', ALTA, CONTROL),
    ('V-44', ETAPAS[8], 'NC por devolución sin permiso',
     'Usuario sin «Emitir notas de crédito por devolución» › NC motivo 06 o 07.', 'Usuario restringido.',
     'No guarda: pide a quien tenga el permiso.', 'Devoluciones ficticias.', ALTA, CONTROL),
    ('V-45', ETAPAS[8], 'Anular factura con cobros',
     'Factura con un cobro registrado › Anular.', 'Factura cobrada.',
     'No deja: «Tiene cobros/pagos registrados en Tesorería…».', 'Anular ventas ya cobradas.', ALTA, CONTROL),
    ('V-46', ETAPAS[8], 'Anular factura con nota vigente',
     'Factura con NC registrada › Anular la factura.', 'Factura con NC.', 'No deja: «Anúlelas primero».',
     'Notas huérfanas.', MEDIA, CONTROL),
    ('V-47', ETAPAS[8], 'Anular la factura de un pedido',
     'Pedido facturado completo (Atendido) › anular su factura.', 'Pedido atendido.',
     'El pedido vuelve a «Aprobado» (o «Pendiente» si su ruta no estaba completa) y la mercadería regresa al '
     'almacén.', 'Pedido cerrado sin venta.', MEDIA, CONTROL),
    ('V-48', ETAPAS[8], 'Los comprobantes no se eliminan',
     'Intentar eliminar una factura.', '—', 'Mensaje: «Los comprobantes no se eliminan: use Anular…».',
     'Pérdida de rastro.', ALTA, CONTROL),

    # ------------------------------------------------------------ 10. permisos
    ('V-49', ETAPAS[9], 'Vendedor ve solo lo suyo',
     'Usuario con política «solo sus documentos» › ver Cotizaciones y Comprobantes.', 'Dos vendedores.',
     'Solo ve los documentos donde es el vendedor.', 'Fuga de información comercial.', MEDIA, CONTROL),
    ('V-50', ETAPAS[9], 'Perfil sin permiso de anular',
     'Usuario sin «Anular» › intentar anular un comprobante.', 'Usuario restringido.',
     'No ve el botón o recibe acceso denegado.', 'Anulaciones no autorizadas.', ALTA, CONTROL),
    ('V-51', ETAPAS[9], 'Auditoría de cambios',
     'Hacer V-07, V-13 y una anulación › revisar el historial del documento.', '—',
     'Quedan usuario, fecha, estado anterior y nuevo y el motivo.', 'Sin trazabilidad.', MEDIA, CONTROL),

    # ------------------------------------------------------------ 11. registro y reportes
    ('V-52', ETAPAS[10], 'Registro de ventas 14.1 / PLE',
     'Ventas › Reportes › Registro de ventas › generar el TXT del mes.', 'Mes con factura, boleta, NC y anulada.',
     'NC con importes negativos y su documento de referencia; la anulada en 0 con estado 2; totales iguales a la '
     'contabilidad (cuenta 70 y 40).', 'Declaración errada a SUNAT.', ALTA, CONTROL),
    ('V-53', ETAPAS[10], 'Cuadre ventas vs. contabilidad',
     'Comparar el total del Registro de ventas del mes con el Haber de 70 + 40 del Libro diario.', 'Mes '
     'centralizado.', 'Cuadran al céntimo.', 'Diferencias no explicadas.', ALTA, CONTROL),
]
