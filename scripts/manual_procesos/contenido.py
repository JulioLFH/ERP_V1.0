"""Textos del manual de procesos por módulo (desde v1.79.0); los usa generar.py.

Cada módulo: intro (para qué sirve), procesos [(título, [pasos], resultado)], pantallas {etiqueta: qué se hace},
integra [(módulo, qué recibe o envía)], extra (secciones adicionales opcionales [(título, [(nombre, texto)])]).
Los pasos admiten <b>negrita</b> para las rutas del menú.
"""

COMUNES = {
    'Log de modificaciones': 'Historial de altas, cambios y anulaciones del módulo: quién, cuándo, qué campo y valor anterior.',
    'Productos': 'Maestro de productos y servicios compartido (código, unidad, tipo, cuentas, precios y stock mínimo).',
    'Productos y servicios': 'Maestro de productos y servicios: código, unidad, tipo de existencia, cuentas contables, lotes/series y stock mínimo.',
    'Clientes': 'Maestro de clientes con RUC/DNI (consulta a SUNAT/RENIEC), condiciones de pago, línea de crédito y contactos.',
    'Proveedores': 'Maestro de proveedores con RUC, cuentas bancarias, condiciones de pago, detracción y retención.',
    'Sugerencia de compra': 'Productos bajo el stock mínimo o punto de pedido con la cantidad sugerida a comprar.',
    'Importar desde Excel': 'Carga masiva de documentos desde una plantilla Excel, con validación previa fila por fila.',
    'Por liberar': 'Solicitudes de pedido que esperan su liberación según las reglas de aprobación.',
    'Flujo de caja proyectado': 'Proyección de cobros y pagos por semana o mes según vencimientos, letras y cuotas.',
    'Tipo de cambio': 'Tipo de cambio diario SUNAT/SBS (compra y venta), descargado o registrado a mano.',
    'Reglas de validación por especialista': 'Qué registros requieren validación y quién valida (cargo, especialista); se comparte entre SST y Calidad.',
}

M = {}

M['tablero'] = {
    'titulo': 'Tablero e inicio',
    'intro': ('Es la primera pantalla después de ingresar. Resume la situación de la empresa en indicadores y gráficos, '
              'y reúne las funciones que comparten todos los módulos: elegir la empresa, ver las alertas, '
              'aprobar documentos desde una sola bandeja y trabajar las listas con columnas, filtros y Excel.'),
    'procesos': [
        ('Ingresar y elegir la empresa', [
            'Ingrese con su usuario y clave (o con Microsoft / Google si su empresa lo activó; puede pedir doble factor).',
            'Si trabaja con varias empresas, elija la empresa en el selector de la barra superior: cada empresa tiene su propia base de datos, usuarios y configuración.',
            'En la pantalla de aplicaciones verá solo los módulos que el administrador le asignó.',
        ], 'Trabaja sobre la empresa elegida; todo lo que registre queda en su base.'),
        ('Revisar los indicadores del día', [
            'Abra <b>Tablero</b>: saldos de caja y bancos en soles, dólares y consolidado al tipo de cambio del día.',
            'Revise ventas y compras del mes, total por cobrar y por pagar, y el gráfico de ventas vs compras de los últimos 6 meses.',
            'Atienda las listas de documentos vencidos por cobrar, pagos que vencen en los próximos 7 días, productos con stock bajo el mínimo y los 5 mejores clientes del mes.',
        ], 'Una vista rápida para decidir qué cobrar, qué pagar y qué reponer.'),
        ('Aprobar documentos desde «Mis aprobaciones»', [
            'Abra <b>Mis aprobaciones</b> (menú del usuario): bandeja única con órdenes de compra, solicitudes de pedido, pedidos de venta, conformidades de servicio (HES), rendiciones, movimientos de personal y otros documentos.',
            'Revise la ruta de aprobación: en cada nivel se ve quién aprobó, rechazó u observó, la fecha y el motivo.',
            'Apruebe, observe o rechace (con motivo). Si un nivel no tiene aprobador (sin jefe o puesto vacante), se usa el aprobador de reemplazo o pasa al siguiente nivel con aviso; el administrador ve los documentos sin aprobador.',
            'Durante sus vacaciones, registre una suplencia (menú del usuario › <b>Suplencias</b>); lo que no se responde a tiempo escala al jefe.',
        ], 'El documento avanza al siguiente nivel o queda liberado para continuar su proceso.'),
        ('Trabajar con listas', [
            'En las listas que lo permiten (operaciones de inventario, órdenes de compra y otras), use <b>Columnas</b> para elegir qué ver; la elección se guarda por usuario.',
            'Combine filtros (fechas, almacén, centro de costo, usuario, estado) y guárdelos para reutilizarlos.',
            'Agrupe con subtotales y descargue a Excel exactamente lo que ve.',
        ], 'Reportes a la medida sin pedir desarrollos.'),
    ],
    'pantallas': {
        'Log de modificaciones': 'Historial de cambios de los datos que se ven en el tablero.',
    },
    'extra': [('Funciones comunes a todos los módulos', [
        ('Alertas', 'La campana muestra lo pendiente: aprobaciones, vencimientos, conteos abiertos, documentos por validar, accidentes por notificar, etc.'),
        ('Mis aprobaciones', 'Bandeja única de todo lo que espera su aprobación, con la ruta y el historial de cada nivel.'),
        ('Log de modificaciones', 'Cada módulo tiene su historial de cambios (quién, cuándo, valor anterior y nuevo).'),
        ('Reportar un problema', 'Desde cualquier pantalla se puede reportar un error; queda como incidencia para el administrador.'),
        ('Claves de API', 'Menú del usuario: claves para conectar Power BI, una tienda u otro sistema con la API REST.'),
        ('Celular', 'Las pantallas se adaptan al celular; rendiciones, liberación de solicitudes, conteos y avance de obra tienen vistas móviles.'),
    ])],
    'integra': [('Todos los módulos', 'El tablero lee ventas, compras, tesorería e inventario; no registra nada por sí mismo.')],
}

M['crm'] = {
    'intro': ('Módulo propio para la gestión comercial: oportunidades de negocio con su embudo, actividades de '
              'seguimiento con recordatorio, ficha 360 del cliente con sus contactos y su crédito, cotizaciones unidas '
              'a la oportunidad, equipos y metas, campañas, formulario web, WhatsApp, trabajo de campo desde el '
              'celular, postventa y encuestas, contratos con renovación y protección de datos personales (Ley 29733). '
              'Cada vendedor ve lo suyo, el jefe su equipo y la gerencia todo.'),
    'procesos': [
        ('Captar la oportunidad', [
            'Regístrela en <b>CRM › Oportunidades › Nueva oportunidad</b>: cliente o prospecto (contacto, celular, correo), monto (mayor que cero), cierre estimado (no anterior a hoy), inicio probable, origen, campaña y responsable. Marque si el prospecto autorizó el uso de sus datos.',
            'Ceiba avisa si parece repetida (mismo cliente, prospecto, teléfono, correo o nombre).',
            'Las consultas de la página web llegan solas por el <b>formulario web</b>: crean la oportunidad con origen «Web», la asignan (responsable fijo o rotativo) y agendan «Llamar hoy».',
            'Una cotización desde el monto del parámetro CRM_EXIGIR_OPORTUNIDAD_DESDE que se haga sin oportunidad crea la suya.',
        ], 'Toda venta importante entra al embudo con su origen y su campaña.'),
        ('Seguimiento y siguiente paso', [
            'En la ficha agende actividades (llamada, reunión, visita, correo / WhatsApp, tarea) con fecha, hora, responsable y recordatorio; aparecen en la campana de Inicio y en <b>Mis actividades</b> (vencidas, hoy, próximas).',
            'Al marcar como realizada la última actividad pendiente, Ceiba pide la siguiente: ninguna oportunidad abierta queda sin próximo paso.',
            'El embudo marca en rojo las oportunidades sin movimiento más días que el parámetro CRM_DIAS_ESTANCADA.',
            'Use el botón <b>WhatsApp</b> para escribir al contacto: la conversación queda registrada como actividad.',
        ], 'Cartera comercial al día, con el siguiente paso de cada oportunidad.'),
        ('Del prospecto a la venta', [
            'Con <b>Convertir en cliente</b> ingrese su RUC o DNI: con RUC se traen la razón social y la dirección de SUNAT y el prospecto se reemplaza por el cliente.',
            'Revise el <b>crédito del cliente</b> en la ficha (límite, deuda, vencida y días promedio de pago): si está en mora, coordine con Cobranzas antes de enviar la propuesta.',
            'Use <b>Crear cotización</b>: lleva el cliente, el vendedor y la referencia; la cotización vuelve a la oportunidad, su monto pasa a la oportunidad y la etapa pasa a Propuesta. La ficha muestra la <b>fecha de entrega factible</b> de lo cotizado (stock, receta e insumos con su plazo).',
            'Al aprobarse la cotización, confirmarse el pedido o emitirse el comprobante, la oportunidad pasa sola a <b>Ganada</b> y se avisa a operaciones. Si se pierde, elija el motivo del catálogo (y el competidor).',
        ], 'Venta trazada desde el primer contacto hasta la factura.'),
        ('Clientes, visitas y renovaciones', [
            'La <b>ficha 360</b> reúne oportunidades, cotizaciones, comprobantes, deuda, actividades, contactos (con cargo y rol), casos, encuestas y contratos del cliente, y su vendedor de cartera.',
            'Agende las visitas en el <b>Plan de visitas</b> y regístrelas en campo con <b>En el celular</b> (ubicación y foto; funciona sin señal y se envía al volver).',
            'Revise <b>Clientes sin compras</b> (parámetro CRM_DIAS_CLIENTE_INACTIVO) para visitarlos.',
            'En <b>Contratos y renovaciones</b> registre los contratos con su fecha de término: Ceiba avisa con anticipación y crea la oportunidad de renovación.',
        ], 'Clientes atendidos antes de que se vayan.'),
        ('Postventa y satisfacción', [
            'Registre reclamos, garantías y consultas en <b>Casos</b> con responsable y plazo (vencidos salen en Inicio); al cerrar se exige la solución.',
            'Registre la <b>encuesta de satisfacción</b> al cerrar la obra, la entrega o el caso: Ceiba calcula el índice de recomendación (NPS).',
        ], 'La relación sigue después de la venta.'),
        ('Equipos, metas, comisiones y reportes', [
            'Arme los <b>Equipos de ventas</b> con su jefe (ve y reasigna las oportunidades de su equipo) y registre las <b>Metas</b> mensuales por vendedor o equipo; el avance compara lo ganado y lo facturado.',
            'Revise los <b>Reportes del embudo</b>: conversión por etapa, perdidas por motivo y competidor, por origen y por vendedor, ciclo de venta, pronóstico por mes de cierre y estancadas (cada tabla a Excel).',
            'Calcule las <b>Comisiones</b> sobre las ventas que nacieron en una oportunidad (parámetro CRM_COMISION_PCT).',
            'Mida las <b>Campañas</b>: costo por oportunidad y por venta ganada, y exporte <b>segmentos</b> de contactos que dieron su consentimiento.',
        ], 'Gestión comercial con números.'),
        ('Licitaciones e inmobiliario', [
            'Constructoras: <b>Convertir en obra / licitación</b> crea la obra en «Propuesta / licitación»; su resultado se registra en Obras › Licitaciones y actualiza la oportunidad.',
            'Inmobiliarias: elija la <b>unidad de interés</b>, emita la <b>Proforma</b> con su cronograma de pagos y use <b>Separar unidad</b> para registrar la separación en Obras.',
            'Operaciones (obras, producción, logística) ve <b>Lo que viene</b>: oportunidades en negociación y ganadas recientes con su inicio probable.',
        ], 'Lo comercial conectado con la ejecución.'),
        ('Visibilidad, cartera y datos personales', [
            'El vendedor ve sus oportunidades; el jefe, las de su equipo; con el permiso «ver todas», toda la empresa.',
            'Al desactivar a un vendedor con cartera abierta, Ajustes › Usuarios pide a quién pasarla; también se reasigna en <b>Reasignar cartera</b> (con historial).',
            'En <b>Datos personales</b> se ven los consentimientos (canal, finalidad, fecha) y se registra la baja de comunicaciones, se exportan los datos de una persona o se suprimen (anonimizan).',
        ], 'Cumplimiento de la Ley 29733 y continuidad cuando cambia el equipo.'),
    ],
    'pantallas': {
        'Embudo de oportunidades': 'Tablero por etapa con montos, ponderado, actividades vencidas y oportunidades sin movimiento; se arrastran las tarjetas.',
        'Todas las oportunidades': 'Lista con filtros por etapa, vendedor y campaña, búsqueda y Excel; en rojo las estancadas.',
        'Mis oportunidades': 'Las oportunidades de las que el usuario es responsable.',
        'Nueva oportunidad': 'Registrar una oportunidad con aviso de posibles duplicados.',
        'Lo que viene (operaciones)': 'Oportunidades en negociación y ganadas recientes con su inicio probable, para obras, producción y logística.',
        'Mis actividades': 'Actividades vencidas, de hoy y próximas (propias o del equipo) y oportunidades sin siguiente paso.',
        'Plan de visitas': 'Visitas a clientes por semana con su responsable; agendar una visita.',
        'En el celular (campo)': 'Registrar visitas con ubicación y foto y oportunidades desde el celular, aun sin señal.',
        'Clientes (ficha 360)': 'Clientes con vendedor, oportunidades abiertas y última compra; entrada a la ficha 360.',
        'Contactos': 'Personas de cada cliente con cargo, rol, teléfono y correo.',
        'Clientes sin compras': 'Clientes que no compran hace más de los días del parámetro, para visitarlos.',
        'Contratos y renovaciones': 'Contratos con su término y aviso; crear la oportunidad de renovación.',
        'Casos (reclamos y garantías)': 'Casos de postventa con responsable, plazo y solución.',
        'Encuestas de satisfacción': 'Encuestas con satisfacción e índice de recomendación (NPS).',
        'Campañas': 'Campañas con costo, oportunidades, ganadas, costo por oportunidad y por venta y retorno.',
        'Segmentos de clientes': 'Contactos con consentimiento vigente por rol, zona o días sin compra, para comunicaciones.',
        'Reportes del embudo': 'Conversión, motivos de pérdida, origen, vendedor, ciclo, pronóstico y estancadas; cada tabla a Excel.',
        'Metas y avance': 'Meta mensual por vendedor o equipo frente a lo ganado y lo facturado.',
        'Comisiones': 'Comisión por vendedor sobre las ventas nacidas en una oportunidad.',
        'Equipos de ventas': 'Equipos con su jefe y sus miembros (visibilidad por nivel).',
        'Reasignar cartera': 'Pasar oportunidades, actividades y clientes de un vendedor a otro, con historial.',
        'Datos personales (Ley 29733)': 'Consentimientos; baja de comunicaciones, exportar y suprimir los datos de una persona.',
        'Configuración y formulario web': 'Formulario web (enlace, clave, asignación), avisos a operaciones, nombres de etapas y finalidad de los datos.',
    },
    'integra': [
        ('Ventas', 'Cotizaciones desde la oportunidad; la cotización aprobada o facturada gana la oportunidad; comisiones.'),
        ('Tesorería', 'Crédito y deuda del cliente; pronóstico ponderado en el flujo de caja proyectado.'),
        ('Obras', 'Licitaciones, separación de unidades inmobiliarias y aviso al ganar; casos de garantía de obra.'),
        ('Contactos', 'Mismo maestro de clientes, con contactos y cartera.'),
        ('Ajustes', 'Usuarios (reasignación al desactivar), permisos y parámetros del CRM.'),
    ],
}

M['ventas'] = {
    'intro': ('Gestiona el ciclo de venta: cotizaciones y pedidos, emisión de facturas, boletas y notas electrónicas '
              '(Nubefact / SUNAT), punto de venta, listas de precios, cuentas por cobrar y los registros tributarios '
              'de ventas (PLE 14.1 y SIRE). Las oportunidades comerciales están en el módulo CRM.'),
    'procesos': [
        ('Cotización, pedido y aprobación', [
            'Cree la cotización en <b>Ventas › Cotizaciones y pedidos</b>: cliente, productos, lista de precios, descuentos, moneda y condición de pago.',
            'Envíela al cliente (PDF o correo) y, cuando la acepte, conviértala en pedido.',
            'Si el pedido va bajo el precio de la lista, con más descuento o excede la línea de crédito, pulse <b>Enviar a aprobación</b>: la ruta (niveles y aprobadores) se ve desde antes de enviarlo.',
            'El aprobador lo aprueba o rechaza desde <b>Mis aprobaciones</b> (permiso «Aprobar pedidos de venta»).',
        ], 'Pedido aprobado listo para despachar y facturar.'),
        ('Emisión del comprobante electrónico', [
            'Desde el pedido o en <b>Ventas › Emitir comprobante</b>, elija el tipo (factura 01, boleta 03), la serie y el cliente (RUC o DNI con consulta en línea).',
            'Ingrese o revise el detalle: productos, cantidades, precios, IGV (gravado, exonerado, inafecto), detracción o retención y tipo de cambio si es en dólares.',
            'Guarde y envíe: el comprobante va a SUNAT por Nubefact y se guarda la respuesta (CDR). El PDF y el XML quedan disponibles para el cliente.',
            'Los bienes salen del almacén (kardex valorizado) y se genera la cuenta por cobrar y el asiento contable de la venta.',
        ], 'Venta registrada, aceptada por SUNAT, con su salida de stock y su asiento.'),
        ('Punto de venta (mostrador)', [
            'Abra <b>Ventas › Punto de venta</b>: busque productos por código o nombre y agréguelos.',
            'Emita boleta o factura y cobre en efectivo, tarjeta o transferencia en la caja asignada.',
        ], 'Venta y cobro en un solo paso, con la caja actualizada en Tesorería.'),
        ('Notas de crédito y débito, anulación y reactivación', [
            'Desde el comprobante, emita la nota en <b>Ventas › Notas de crédito / débito</b> con el motivo SUNAT (anulación, descuento, devolución, corrección).',
            'Si es por devolución (permiso «devoluciones»), la mercadería reingresa al almacén.',
            'Para anular un comprobante aceptado (permiso «anular») se envía a SUNAT la comunicación de baja (facturas) o el resumen diario (boletas) con el motivo, o se emite una nota de crédito. Una venta anulada que no se envió a SUNAT puede reactivarse (permiso «reactivar»).',
        ], 'Saldo del cliente, stock y contabilidad corregidos.'),
        ('Precios y descuentos', [
            'Mantenga las tarifas en <b>Ventas › Listas de precios</b> o cárguelas desde Excel (<b>Cargar listas de precios</b>).',
            'Asigne la lista al cliente; vender bajo la lista exige el permiso «bajo_tarifa» o la aprobación del pedido.',
        ], 'Precios controlados y descuentos autorizados.'),
        ('Cobranza y control de cartera', [
            'Revise <b>Ventas › Cuentas por cobrar</b>: saldos por cliente, antigüedad y documentos vencidos.',
            'El cobro se registra en Tesorería (individual o múltiple), con retenciones, detracciones y diferencia de cambio.',
        ], 'Cartera al día y cobranza conciliada con bancos.'),
        ('Obligaciones tributarias de ventas', [
            'Genere el <b>Registro de ventas 14.1 / PLE</b> del mes.',
            'Descargue la propuesta de SUNAT en <b>SIRE: propuesta de SUNAT (RVIE)</b> y compárela con lo registrado.',
            'Controle las detracciones de clientes en <b>Detracciones de ventas</b>.',
        ], 'Libros electrónicos listos para subir a SUNAT.'),
    ],
    'pantallas': {
        'Punto de venta': 'Venta rápida en mostrador con boleta o factura y cobro inmediato.',
        'Cotizaciones y pedidos': 'Crear cotizaciones, convertirlas en pedidos, enviarlas a aprobación y facturarlas.',
        'Comprobantes': 'Lista de facturas y boletas emitidas con su estado SUNAT, saldo, PDF y XML.',
        'Notas de crédito / débito': 'Notas electrónicas relacionadas a un comprobante (devoluciones, descuentos, anulaciones).',
        'Emitir comprobante': 'Emitir una factura o boleta electrónica directamente.',
        'Listas de precios': 'Tarifas por producto, moneda y cliente con descuentos máximos.',
        'Cuentas por cobrar': 'Saldos pendientes por cliente y antigüedad de la deuda.',
        'Registro de ventas 14.1 / PLE': 'Registro de ventas del periodo y archivo PLE para SUNAT.',
        'Detracciones de ventas': 'Ventas sujetas a detracción y el estado del depósito del cliente.',
        'SIRE: propuesta de SUNAT (RVIE)': 'Descargar la propuesta del RVIE y cruzarla con las ventas registradas.',
        'Reportes de ventas': 'Ventas por cliente, producto, vendedor, periodo y línea de negocio.',
        'Cargar listas de precios (Excel)': 'Carga masiva de precios desde una plantilla Excel.',
    },
    'integra': [
        ('CRM', 'Cotizaciones creadas desde una oportunidad.'),
        ('Inventario', 'Salida de mercadería al costo promedio (kardex) y reingreso por devolución.'),
        ('Tesorería', 'Cobranzas, retenciones y detracciones de clientes.'),
        ('Logística', 'Guía de remisión que acompaña el despacho.'),
        ('Contabilidad', 'Asiento de venta (12 / 40 / 70) y de costo de ventas (69 / 20).'),
        ('Obras', 'Facturas de adelanto y de valorización con deducción del anticipo.'),
    ],
}

M['compras'] = {
    'intro': ('Cubre el abastecimiento de punta a punta: solicitudes de pedido, bandeja del comprador, licitaciones y '
              'cuadro comparativo, órdenes de compra y de servicio con aprobación por niveles, conformidades de servicio '
              '(HES), registro de facturas con cruce de tres vías, portal de proveedores, importaciones y los registros '
              'tributarios de compras (PLE 8.1 y SIRE).'),
    'procesos': [
        ('De la solicitud de pedido a la orden de compra', [
            'El área registra la solicitud en <b>Requerimientos › Nueva solicitud de pedido</b> (o <b>Compras › Nueva solicitud</b>) y la envía.',
            'Se libera según las reglas de aprobación (<b>Por liberar</b>; también desde el celular).',
            'La línea llega a la <b>Bandeja de compras</b> del comprador de su grupo de compras. La bandeja muestra el stock propio y el disponible en otros almacenes.',
            'Si hay stock en otro almacén, use <b>Atender por traslado</b>; si compra igual, se pide justificación o se bloquea según el parámetro STOCK_CONTROL_COMPRA.',
            'Convierta las líneas en orden de compra (permiso «convertir_solped») o en una licitación.',
        ], 'Orden de compra en borrador unida a la solicitud de origen.'),
        ('Licitación y cuadro comparativo', [
            'Cree la licitación en <b>Compras › Abastecimiento › Nueva licitación</b> con los productos y proveedores invitados.',
            'Registre las ofertas (precio, plazo, condiciones) y compárelas en el cuadro comparativo.',
            'Adjudique al ganador y genere la orden de compra.',
        ], 'Compra sustentada con la comparación de ofertas.'),
        ('Aprobación y envío de la orden de compra', [
            'Complete la orden: proveedor, condiciones, almacén y centro de costo (si es de una obra, se propone el almacén de la obra). La fecha de registro la pone el sistema.',
            'Envíela a aprobación: la ruta muestra los niveles según el monto y quién aprueba; el aprobador responde en <b>Mis aprobaciones</b> (permiso «aprobar_oc»).',
            'Aprobada, se envía al proveedor por correo o por el portal de proveedores.',
        ], 'Orden aprobada y comunicada al proveedor.'),
        ('Recepción, factura y cruce de tres vías', [
            'El almacén recibe en Inventario contra la orden; el costo es el de la orden (no se edita).',
            'Registre la factura en <b>Registrar compra</b> o <b>Registrar desde XML</b> (lee el XML y valida el comprobante en SUNAT).',
            'El sistema cruza factura, recepción y orden: la cantidad facturada no puede superar la recibida; si el precio sale de la tolerancia, la factura queda «retenida para pago» hasta que Compras la libere con motivo.',
            'La factura genera la cuenta por pagar y el asiento (60 / 40 / 42 y destino 20 / 61). El pago se hace en Tesorería.',
        ], 'Factura conforme, lista para pago.'),
        ('Servicios con conformidad (HES)', [
            'Emita la orden de servicio igual que una orden de compra.',
            'Al recibir el servicio, registre la conformidad en <b>Conformidades de servicio (HES)</b> con sus adjuntos (permiso «hes»).',
            'La HES se aprueba por reglas o la libera quien tiene el permiso «liberar_hes».',
            'La factura del proveedor se registra contra la HES.',
        ], 'Solo se pagan servicios efectivamente conformes.'),
        ('Portal de proveedores', [
            'Dé acceso al proveedor en <b>Portal de proveedores › Accesos de proveedores</b>.',
            'El proveedor acepta sus órdenes y sube la factura (XML y PDF) contra la orden o la HES.',
            'Revise en <b>Facturas por revisar</b> y apruebe o rechace (permiso «portal»); al aprobarla se registra la compra.',
        ], 'Menos digitación y trazabilidad de cada factura recibida.'),
        ('Importaciones y costeo', [
            'Abra un expediente en <b>Importaciones y costeo</b> con la factura del exterior, la DUA, el flete, el seguro y los gastos de aduana.',
            'Prorratee los gastos para obtener el costo unitario puesto en almacén.',
        ], 'Costo real de importación en el kardex.'),
        ('Obligaciones tributarias de compras', [
            'Genere el <b>Registro de compras 8.1 / PLE</b> del mes.',
            'Descargue la propuesta del RCE en <b>SIRE: propuesta de SUNAT (RCE)</b> y use <b>Cruce con propuesta SIRE</b> para ver diferencias y precargar compras faltantes (con orden de compra, clasificación y cuenta del Excel).',
            'Controle las detracciones de proveedores en <b>Detracciones de compras</b>.',
        ], 'Registro de compras cuadrado con SUNAT.'),
    ],
    'pantallas': {
        'Bandeja de compras': 'Líneas liberadas por atender, por comprador, con stock propio y en otros almacenes.',
        'Mis líneas por pedir': 'Líneas asignadas al comprador que aún no tienen orden.',
        'Traslados entre almacenes': 'Atender una necesidad con stock de otro almacén (origen aprueba, tránsito, destino recibe).',
        'Grupos de compras y compradores': 'Qué comprador atiende qué familias de productos o áreas.',
        'Todas las solicitudes': 'Todas las solicitudes de pedido con su estado y tiempos.',
        'Nueva solicitud': 'Registrar una solicitud de pedido.',
        'Reglas de aprobación (OC y solicitudes)': 'Niveles y aprobadores por monto, área o tipo de documento.',
        'Órdenes de compra': 'Lista de órdenes de compra y servicio con columnas, filtros, ruta de aprobación y Excel.',
        'Conformidades de servicio (HES)': 'Conformidades de servicios recibidos con adjuntos y aprobación.',
        'Registro de compras': 'Facturas de compra registradas con su saldo y estado de pago.',
        'Notas de crédito / débito': 'Notas de proveedores relacionadas a sus facturas.',
        'Registrar compra': 'Registrar una factura, boleta o recibo por honorarios (con retención de 4ta).',
        'Registrar desde XML': 'Registrar la factura leyendo su XML y validándola en SUNAT.',
        'Importaciones y costeo': 'Expedientes de importación y prorrateo de gastos al costo.',
        'Licitaciones y cuadro comparativo': 'Procesos de cotización a varios proveedores y su comparación.',
        'Nueva licitación': 'Iniciar una licitación con productos y proveedores invitados.',
        'Contratos marco': 'Acuerdos de precio y volumen con proveedores y su consumo.',
        'Proveedores por producto (registro info)': 'Precios, plazos y proveedores habituales por producto.',
        'Tablero del portal': 'Resumen de la actividad de los proveedores en el portal.',
        'Facturas por revisar': 'Facturas subidas por proveedores pendientes de aprobar o rechazar.',
        'Accesos de proveedores': 'Usuarios del portal de cada proveedor.',
        'Cuentas por pagar': 'Saldos pendientes por proveedor y vencimientos.',
        'Registro de compras 8.1 / PLE': 'Registro de compras del periodo y archivo PLE.',
        'Detracciones de compras': 'Compras sujetas a detracción y su depósito.',
        'Reportes de compras': 'Compras por proveedor, producto, centro de costo y periodo.',
        'Historial de precios de compra': 'Evolución de precios por producto y proveedor.',
        'Solicitudes de pedido: estado y tiempos': 'Tiempos de liberación, compra y atención de cada solicitud.',
        'Desempeño de compradores': 'Líneas atendidas, tiempos y ahorros por comprador.',
        'SIRE: propuesta de SUNAT (RCE)': 'Descargar la propuesta del RCE de SUNAT.',
        'Cruce con propuesta SIRE': 'Diferencias entre la propuesta SIRE y lo registrado; precarga de compras faltantes.',
    },
    'integra': [
        ('Requerimientos', 'Solicitudes de pedido de las áreas.'),
        ('Inventario', 'Recepciones contra la orden y traslados entre almacenes.'),
        ('Tesorería', 'Pagos, detracciones y retenciones.'),
        ('Obras', 'Pedidos desde la Meta de la obra y subcontratos.'),
        ('Contabilidad', 'Asiento de compra y provisión (60 / 40 / 42, destino 20 / 61 / 79).'),
    ],
}

M['inventario'] = {
    'intro': ('Controla el stock por almacén y ubicación con kardex valorizado (costo promedio), lotes, series y '
              'vencimientos: recepciones, despachos, traslados con tránsito, inventario físico a ciegas, ajustes, '
              'cierre del kardex y los libros de inventario permanente de SUNAT (12.1 y 13.1).'),
    'procesos': [
        ('Recepción de mercadería', [
            'En <b>Inventario › Operaciones › Nueva operación</b> (o desde la orden de compra) elija la recepción y la orden; si la orden es de una obra se propone su almacén.',
            'Ingrese las cantidades recibidas, lotes/series, ubicación y la guía del proveedor (avisa si la guía ya se usó con ese proveedor).',
            'Revise las fechas: la de registro la pone el sistema; la contable no puede ser futura y una de un mes anterior exige el permiso «fecha_anterior» y un motivo.',
            'Confirme (permiso «operar»). El costo se toma de la orden o la factura. Si algo falla, no queda un borrador a medias.',
        ], 'Stock y kardex actualizados y asiento de ingreso (20 / 28x contra 60 / 61).'),
        ('Despachos y salidas', [
            'Registre la salida por venta, requerimiento de un área, consumo de obra, entrega de EPP o producción.',
            'Indique el almacén, el centro de costo y las cantidades; confirme.',
        ], 'Salida al costo promedio cargada al gasto o al centro de costo correcto.'),
        ('Traslado entre almacenes con tránsito', [
            'Cree el traslado (también desde la bandeja de compras); el almacén de origen lo aprueba y la mercadería pasa a «en tránsito».',
            'El almacén de destino lo recibe o lo rechaza en <b>Traslados por aprobar o recibir</b>; puede emitirse la guía de remisión.',
        ], 'Stock trasladado sin perder el control de lo que está en camino.'),
        ('Inventario físico seguro', [
            'Cree el conteo en <b>Inventario físico</b> (general, cíclico ABC o aleatorio). No se crea si sus productos ya están en otro conteo abierto del mismo almacén.',
            'Los contadores registran cantidades a ciegas (sin ver el sistema ni la diferencia), en escritorio o celular. Las cantidades se validan: no negativas, máximo 2 decimales, coma decimal aceptada.',
            'Las diferencias se recuentan (también a ciegas) y se registran los motivos.',
            'Aprueba quien no contó; diferencias sobre el tope requieren el permiso «aprobar_mayor». Sin diferencias pasa directo a «Aprobado».',
            'Contabilice: el ajuste recalcula lo movido después de la foto. Si hace falta, use <b>Revertir contabilización</b> (el ajuste no se anula suelto).',
            'Imprima el acta con totales de faltantes y sobrantes y las firmas de quién contó, recontó y aprobó. Hay alerta para conteos abiertos más de 7 días.',
        ], 'Stock físico y contable iguales, con evidencia de cada diferencia.'),
        ('Ajustes y anulaciones', [
            'Para correcciones puntuales use <b>Ajuste rápido</b> (permiso «ajustar»).',
            'Anular una operación (permiso «anular») revierte el kardex. Si la recepción ya tenía factura, la orden vuelve a pendiente de recibir y la factura queda retenida con diferencia.',
        ], 'Correcciones controladas y auditables.'),
        ('Cierre mensual de inventario', [
            'Revise el <b>Kardex</b> y la <b>Valorización al cierre</b>.',
            'Cierre el periodo en <b>Cierre de kardex</b> (permiso «cerrar»): ya no se registran movimientos con esa fecha.',
            'Genere el <b>Inventario permanente SUNAT 12.1 / 13.1</b>.',
        ], 'Inventario valorizado y libros SUNAT del mes.'),
    ],
    'pantallas': {
        'Stock por almacén': 'Stock actual por producto, almacén, ubicación y lote, con su valor.',
        'Nueva operación': 'Registrar una recepción, salida, traslado o ajuste.',
        'Recepciones e ingresos': 'Ingresos de mercadería (compras, devoluciones, producción).',
        'Despachos y salidas': 'Salidas por venta, consumo, requerimiento o EPP.',
        'Traslados y tránsito': 'Traslados entre almacenes y lo que está en tránsito.',
        'Manufactura': 'Consumos de insumos e ingresos de producto terminado de las órdenes de producción.',
        'Todas las operaciones': 'Lista completa con documento de origen, proveedor, guía, centro, usuarios, filtros y Excel.',
        'Ajuste rápido': 'Ajuste puntual de stock con motivo.',
        'Picking por olas': 'Agrupar pedidos para preparar varios despachos a la vez.',
        'Traslados por aprobar o recibir': 'Traslados que esperan la aprobación del origen o la recepción del destino.',
        'Inventario físico (general, cíclico, aleatorio…)': 'Conteos físicos a ciegas con reconteo, aprobación, contabilización y acta.',
        'Programa cíclico ABC': 'Calendario de conteos cíclicos según la clasificación ABC.',
        'Requerimientos de las áreas': 'Requerimientos aprobados por atender desde el almacén.',
        'Carga masiva de saldos y productos': 'Saldos iniciales y productos desde Excel.',
        'Reportes masivos (stock a fecha, kardex masivo, rotación…)': 'Reportes de stock a una fecha, kardex de todos los productos y rotación.',
        'Indicadores de inventario físico': 'Exactitud de inventario, diferencias y conteos por periodo.',
        'Kardex': 'Movimientos valorizados de un producto con saldo y costo promedio.',
        'Kardex del sistema anterior': 'Consulta del kardex migrado del sistema anterior.',
        'Lotes, series y vencimientos': 'Stock por lote o serie y productos por vencer.',
        'Inventario permanente SUNAT 12.1 / 13.1': 'Libros de inventario permanente en unidades y valorizado.',
        'Valorización al cierre': 'Valor del inventario al cierre de un periodo.',
        'Cierre de kardex': 'Cerrar el periodo del kardex.',
        'Almacenes': 'Almacenes de la empresa (principal, de obra, de tránsito) y sus responsables.',
        'Ubicaciones en el almacén': 'Racks, pasillos y ubicaciones dentro de cada almacén.',
        'Tipos de operación': 'Tipos de movimiento (tabla 12 SUNAT), su cuenta y comportamiento.',
    },
    'integra': [
        ('Compras', 'Recepciones contra la orden de compra; el costo es el de la orden o la factura.'),
        ('Ventas y Logística', 'Despachos y guías de remisión.'),
        ('Manufactura y Obras', 'Consumos, producción y almacenes de obra.'),
        ('Contabilidad', 'Asientos de ingreso, salida, ajuste y costo de ventas.'),
    ],
}

M['manufactura'] = {
    'intro': ('Planifica y controla la producción: recetas (listas de materiales), hojas de ruta y puestos de trabajo, '
              'pronóstico y MRP, órdenes de producción con reporte de planta desde tablets, paradas y OEE, '
              'calidad (inspecciones, certificados y liberación de lotes) y mantenimiento de equipos.'),
    'procesos': [
        ('Ingeniería del producto', [
            'Defina la receta en <b>Ingeniería › Listas de materiales (recetas)</b>: insumos, cantidades y mermas.',
            'Defina la <b>Hoja de ruta</b> con las operaciones, los <b>Puestos de trabajo</b> y sus tiempos; combínelas en <b>Versiones de fabricación</b>.',
            'Los cambios se proponen como <b>Cambios de ingeniería</b> y los aprueba quien tiene «aprobar_cambios».',
        ], 'Estructura de producto con la que se calcula el costo estándar y el MRP.'),
        ('Planificación (MRP)', [
            'Registre o calcule el <b>Pronóstico de demanda</b>.',
            'Corra el <b>MRP</b>: compara demanda, stock y órdenes abiertas y propone órdenes de producción y solicitudes de compra.',
            'Ajuste la secuencia en <b>Programación detallada de planta</b> y revise <b>Carga y capacidad de puestos</b>.',
        ], 'Plan de producción y de compras de insumos.'),
        ('Orden de producción', [
            'Cree la orden en <b>Producción › Nueva orden de producción</b> (o desde el MRP).',
            'Genere el <b>Requerimiento de materiales</b>; el almacén despacha los insumos.',
            'En planta se reporta el avance, las mermas y las paradas desde las tablets (<b>Reporte de planta</b>).',
            'Al terminar, ingresa el producto con su lote y se cierra la orden con su costo real.',
        ], 'Producto terminado en stock con su costo.'),
        ('Calidad y liberación de lotes', [
            'Defina los <b>Planes de calidad por producto</b> (características, límites, muestreo).',
            'Registre las <b>Inspecciones de calidad</b> en recepción, proceso o producto terminado.',
            'Emita el <b>Certificado de análisis</b> y libere el lote en cuarentena (permiso «liberar_lotes»).',
        ], 'Solo se despachan lotes liberados.'),
        ('Mantenimiento', [
            'Registre los <b>Equipos y planes preventivos</b> por tiempo u horómetro.',
            'Cree las <b>Órdenes de mantenimiento</b> (preventivas o correctivas) y registre repuestos y horas.',
            'Registre lecturas en el <b>Parte diario de horómetros / odómetros</b> y controle los documentos por vencer.',
        ], 'Equipos disponibles y costo de mantenimiento por equipo.'),
    ],
    'pantallas': {
        'Órdenes de producción': 'Órdenes con su estado, avance y costo.',
        'Nueva orden de producción': 'Crear una orden con producto, cantidad, versión y fechas.',
        'Requerimiento de materiales': 'Insumos que pide cada orden al almacén.',
        'Órdenes del sistema anterior': 'Consulta de órdenes migradas.',
        'Reporte de planta (tablets)': 'Pantalla táctil para reportar avance, mermas y paradas en planta.',
        'Paradas de línea': 'Paradas registradas con motivo y duración.',
        'Reporte diario de producción': 'Producción del día por línea y producto.',
        'Tablero diario de producción': 'Indicadores del día: producción, eficiencia y OEE.',
        'Todos los reportes de producción': 'Historial de reportes de planta.',
        'MRP: necesidades y órdenes planificadas': 'Cálculo de necesidades y órdenes sugeridas.',
        'Pronóstico de demanda': 'Demanda esperada por producto y periodo.',
        'Programación detallada de planta': 'Secuencia de órdenes por puesto y turno.',
        'Carga y capacidad de puestos': 'Horas requeridas vs disponibles por puesto.',
        'Inspecciones de calidad': 'Inspecciones registradas con sus resultados.',
        'Nueva inspección': 'Registrar una inspección.',
        'Planes de calidad por producto': 'Características a controlar y sus límites.',
        'Certificados de análisis y liberación de lotes': 'Certificados y liberación de lotes en cuarentena.',
        'Órdenes de mantenimiento': 'Órdenes preventivas y correctivas con su costo.',
        'Nueva orden de mantenimiento': 'Crear una orden de mantenimiento.',
        'Equipos y planes preventivos': 'Equipos con sus planes por tiempo u horómetro.',
        'Parte diario de horómetros / odómetros': 'Lecturas diarias de los equipos.',
        'Documentos de equipos por vencer': 'Certificados y documentos de equipos por vencer.',
        'Listas de materiales (recetas)': 'Recetas con insumos, cantidades y mermas.',
        'Cambios de ingeniería': 'Propuestas de cambio a recetas y rutas con aprobación.',
        'Hojas de ruta': 'Operaciones, puestos y tiempos de fabricación.',
        'Versiones de fabricación': 'Combinación receta + ruta vigente por producto.',
        'Puestos de trabajo': 'Máquinas o líneas con su capacidad y tarifa.',
        'Motivos de parada (OEE)': 'Catálogo de motivos para medir el OEE.',
        'Carga masiva de maestros (Excel)': 'Recetas, rutas y puestos desde Excel.',
    },
    'integra': [
        ('Inventario', 'Consumo de insumos e ingreso de producto terminado con lote.'),
        ('Compras', 'Solicitudes de compra que propone el MRP.'),
        ('Costos', 'Costo estándar, costo real de las órdenes y variaciones.'),
        ('Excelencia organizacional', 'El OEE alimenta los indicadores por área.'),
    ],
}

M['costos'] = {
    'intro': ('Calcula el costo estándar de los productos, lo compara con el real y analiza las variaciones; '
              'distribuye los costos de planta (absorción y costeo ABC), liquida el costo real al cierre (NIC 2) '
              'con el valor neto realizable y mide la rentabilidad por producto, cliente, vendedor y mes.'),
    'procesos': [
        ('Costo estándar del periodo', [
            'Con recetas, rutas y tarifas de puestos al día, calcule el costo en <b>Costo estándar del periodo</b>.',
            'Revise el desglose (materiales, mano de obra, gastos de fabricación) y libérelo (permiso «liberar»).',
        ], 'Costo estándar vigente para valorizar y presupuestar.'),
        ('Control de costos', [
            'Revise <b>Real vs estándar y variaciones</b>: precio, cantidad y eficiencia por orden y producto.',
            'Distribuya los gastos de planta en <b>Absorción de costos de planta</b> y, si aplica, con <b>Costeo por actividades (ABC)</b> configurando actividades e inductores (permiso «abc»).',
        ], 'Causas de las desviaciones identificadas.'),
        ('Cierre de costos (NIC 2)', [
            'Clasifique los <b>Gastos de planta: fijos y variables</b>.',
            'Ejecute la <b>Liquidación de costo real</b> del periodo (permiso «liquidar»): ajusta el inventario y el costo de ventas.',
            'Registre el <b>Valor neto realizable (VNR)</b> y la estimación por desvalorización si corresponde.',
        ], 'Inventario medido según NIC 2 y asiento de cierre.'),
        ('Rentabilidad', [
            'Analice márgenes por producto, cliente, vendedor y mes en <b>Rentabilidad</b>.',
        ], 'Decisiones de precio y mezcla de ventas con datos.'),
    ],
    'pantallas': {
        'Costo estándar del periodo': 'Cálculo y liberación del costo estándar.',
        'Real vs estándar y variaciones': 'Variaciones de precio, cantidad y eficiencia.',
        'Absorción de costos de planta': 'Distribución de los gastos de fabricación a los productos.',
        'Costeo por actividades (ABC)': 'Costo de productos por actividades consumidas.',
        'Actividades e inductores': 'Configuración de actividades y sus inductores de costo.',
        'Liquidación de costo real': 'Cierre del costo real del periodo con su asiento.',
        'Valor neto realizable (VNR)': 'Comparación costo vs VNR y desvalorización.',
        'Gastos de planta: fijos y variables': 'Clasificación de los gastos de fabricación.',
        'Por producto': 'Margen por producto.',
        'Por cliente': 'Margen por cliente.',
        'Por vendedor': 'Margen por vendedor.',
        'Por mes': 'Evolución mensual del margen.',
        'Valorización de inventario': 'Valor del inventario a costo promedio o estándar.',
    },
    'integra': [
        ('Manufactura', 'Recetas, rutas, órdenes y consumos.'),
        ('Inventario', 'Valorización del stock.'),
        ('Ventas', 'Ingresos para la rentabilidad.'),
        ('Contabilidad', 'Asientos de liquidación, variaciones y desvalorización.'),
    ],
}

M['activos'] = {
    'intro': ('Registra los activos fijos con su categoría, vida útil y centro de costo, calcula la depreciación '
              'mensual con su asiento, controla bajas y ventas, y emite el Registro de activos fijos 7.1 con el '
              'cuadre contra contabilidad.'),
    'procesos': [
        ('Alta de activos', [
            'Configure las <b>Categorías y cuentas</b> (cuenta del activo 33, depreciación acumulada 39, gasto 68, vida útil y tasa).',
            'Registre el activo en <b>Nuevo activo</b> (o desde la factura de compra): descripción, código, fecha de uso, valor, centro de costo y responsable. Para muchos, use la <b>Carga masiva (Excel)</b>.',
        ], 'Activo listo para depreciarse.'),
        ('Depreciación mensual', [
            'En <b>Depreciación</b>, calcule el mes (permiso «depreciar»).',
            'Revise el detalle por activo y centro de costo; si hay un error, revierta y vuelva a calcular.',
        ], 'Asiento de depreciación (68 / 39) por centro de costo.'),
        ('Baja o venta', [
            'Dé de baja el activo (permiso «baja») indicando el motivo y la fecha.',
        ], 'Activo y depreciación acumulada retirados con su asiento.'),
        ('Control', [
            'Emita el <b>Registro de activos fijos 7.1</b> y verifique el <b>Cuadre con contabilidad</b>.',
        ], 'Saldos del módulo iguales a los contables.'),
    ],
    'pantallas': {
        'Activos fijos': 'Lista de activos con su valor, depreciación acumulada y estado.',
        'Nuevo activo': 'Registrar un activo.',
        'Carga masiva de activos (Excel)': 'Alta de muchos activos desde Excel.',
        'Depreciación': 'Cálculo y reversión de la depreciación mensual.',
        'Registro de activos fijos 7.1': 'Formato 7.1 de SUNAT.',
        'Cuadre con contabilidad': 'Diferencias entre el módulo y las cuentas 33 / 39.',
        'Categorías y cuentas': 'Categorías con cuentas, vida útil y tasa de depreciación.',
    },
    'integra': [
        ('Compras', 'Factura de adquisición del activo.'),
        ('Contabilidad', 'Asientos de alta, depreciación y baja.'),
    ],
}

M['obras'] = {
    'intro': ('Gestiona empresas constructoras e inmobiliarias: licitaciones y propuestas, presupuestos con análisis de '
              'precios unitarios (compatibles con S10), versiones y Meta, abastecimiento desde la Meta, ejecución '
              '(cronograma, avance y curva S), valorizaciones con fórmula polinómica, adelantos y fondo de garantía, '
              'tareo y planilla de construcción civil, partes de equipo, subcontratos, control por partida y '
              'proyectos inmobiliarios.'),
    'procesos': [
        ('Licitación o propuesta (CRM de obras)', [
            'Registre la licitación en <b>Obras › Comercial › Nueva licitación / propuesta</b> (o desde una oportunidad del CRM): datos del proceso SEACE, entidad, valor referencial y su oferta.',
            'Cargue el cronograma del proceso y los requisitos del postor con su vencimiento (hay alertas).',
            'Registre el resultado: ganada o perdida con motivo, ganador y precio. Se mide la tasa de éxito y el backlog.',
        ], 'Si se gana, la propuesta pasa a ser la obra.'),
        ('Presupuesto y aprobación', [
            'Cree la obra en <b>Nueva obra</b> y su presupuesto: partidas con análisis de precios unitarios (APU), subpresupuestos, gastos generales detallados, utilidad e IGV.',
            'Importe del S10 (reporte .xls y CSV de APU) o use la <b>Plantilla de importación (Excel)</b> y la <b>Biblioteca de partidas tipo</b>.',
            'Revise la relación de insumos y vincule los recursos con los productos del almacén (<b>Vincular recursos con productos</b>).',
            'Apruebe el presupuesto (permiso «aprobar»): se crea el centro de costo y el almacén de la obra y queda la Meta.',
        ], 'Presupuesto contractual y Meta de la obra.'),
        ('Versiones (adicionales y deductivos)', [
            'Dentro de la obra, en <b>Versiones</b>, registre el adicional, deductivo o nueva Meta con su resolución y motivo.',
            'Compare por partida (altas, bajas, cambios) y por recurso.',
            'Al aprobar la versión nueva, el avance, valorizaciones, cronograma, tareo, partes y subcontratos pasan a ella.',
        ], 'Historial de versiones como en el S10.'),
        ('Abastecimiento desde la Meta', [
            'Desde la Meta, requiera materiales y servicios (permiso «requerir»): se generan solicitudes de pedido.',
            'El control de obra muestra el stock propio y el disponible en otros almacenes (traslado antes que compra).',
            'Revise el control Meta vs real y los <b>Sobrantes en almacenes de obra</b>.',
        ], 'Compras ajustadas a lo presupuestado.'),
        ('Ejecución: cronograma, avance y curva S', [
            'Cargue el <b>Cronograma</b> desde Excel (meses o inicio/fin) o desde MS Project (XML).',
            'Registre el <b>Avance</b> físico por partida con foto y responsable (también desde el celular). No se acepta más que el metrado de la partida (los mayores metrados van por un adicional en Versiones) ni negativos: un registro errado se anula con su motivo en la lista de avances de la partida.',
            'Revise la curva S (programado vs ejecutado vs valorizado) y las partidas atrasadas.',
        ], 'Avance real que alimenta la valorización.'),
        ('Adelantos', [
            'Emita la factura de adelanto directo o de materiales (permiso «facturar»): va a la cuenta 122 con IGV y detracción.',
            'Si una factura ya emitida era un adelanto, use <b>Reclasificar</b>: sus líneas pasan a «Adelanto de obra» con bitácora.',
        ], 'Anticipo registrado que se amortiza en las valorizaciones.'),
        ('Valorización mensual', [
            'En <b>Valorizar el mes</b>, el metrado del periodo viene del avance (editable): anterior, actual, acumulado y saldo por partida.',
            'Se calculan gastos generales y utilidad, la amortización del adelanto, la retención del fondo de garantía y el reintegro con la <b>Fórmula polinómica</b> y los <b>Índices INEI</b>.',
            'El supervisor la aprueba; luego se emite la factura con la deducción del anticipo (Nubefact) y el fondo de garantía (1683) se cobra al final.',
        ], 'Valorización facturada y saldo de la obra al día.'),
        ('Mano de obra de construcción civil', [
            'Registre el <b>Tareo</b> diario por cuadrilla y partida (operario, oficial, peón).',
            'Genere la <b>Planilla semanal</b>: jornal, dominical, BUC, horas extra, movilidad, ONP/AFP, CONAFOVICER, EsSalud, SCTR, CTS, vacaciones y gratificaciones (parámetros con vigencia).',
        ], 'Costo de mano de obra cargado a la obra y a sus partidas; HH Meta vs real.'),
        ('Equipos y subcontratos', [
            'Registre los <b>Partes de equipo</b> diarios: horómetro, horas trabajadas y standby, combustible, operador, partida y tarifa.',
            'Cree el <b>Subcontrato</b> por partidas (precio unitario o suma alzada) con su orden de servicio; valorice cada mes, amortice el adelanto y retenga el fondo de garantía (467). Las HES se generan desde la valorización.',
        ], 'Costo de equipos y subcontratos vs Meta.'),
        ('Control y cierre de la obra', [
            'Revise el <b>Control por partida</b> (orden del S10): contractual, Meta, avance, valorizado, costo real por tipo y margen.',
            'Revise el <b>Resultado por obra</b> con los gastos generales asignados (los anticipos no cuentan como ventas).',
            'Al terminar, liquide la obra: cierra su almacén y su centro de costo. Reabrirla exige el permiso «reabrir».',
        ], 'Resultado real de la obra.'),
        ('Servicios profesionales por proyecto (arquitectura, ingeniería, consultoría, supervisión)', [
            'Cree el proyecto con tipo «Proyecto de servicios»: tiene centro de costo pero no almacén; registre el jefe de proyecto (residente), las horas y los gastos presupuestados.',
            'Defina los <b>Entregables</b> (anteproyecto, expediente, supervisión mensual) con su % o monto, fecha comprometida, horas y penalidad por atraso.',
            'El personal registra sus <b>Partes de horas</b> semanales por proyecto y entregable (los independientes con su tarifa); el jefe de proyecto los aprueba en <b>Partes por aprobar</b>.',
            'Marque el entregable como entregado (se calcula la penalidad si hay atraso), observado o conforme con el acta del cliente; recién conforme se <b>factura</b>, con la deducción proporcional del adelanto y la detracción 022.',
            'Registre las <b>cartas fianza</b> (fiel cumplimiento, adelanto) con su vencimiento.',
            'Al cierre del mes, en <b>Servicios en proceso</b>, revise el costo acumulado, el avance y el ingreso por avance no facturado, y registre el asiento del mes (confirme el criterio NIIF 15 con su contador).',
        ], 'Proyectos de servicios costeados por horas y facturados por entregable.'),
        ('Proyecto inmobiliario', [
            'Registre las <b>Unidades</b> del proyecto (departamentos, estacionamientos) con área y precio.',
            'Registre la separación y la venta con su cronograma de pagos (permiso «vender»), arras y garantías.',
            'Cobre las cuotas con comprobante de anticipo (IGV solo de la construcción, exoneración de vivienda cuando corresponde) y controle la bancarización.',
            'Al entregar, se reconoce el ingreso y el costo por unidad; los costos se activan durante la obra (incluye intereses NIC 23). El desistimiento genera nota de crédito y penalidad.',
        ], 'Cobranza inmobiliaria, tablero de ventas y reportes del proyecto.'),
    ],
    'pantallas': {
        'Obras': 'Lista de obras con su estado, presupuesto, avance y resultado.',
        'Nueva obra': 'Crear una obra o proyecto (cliente, ubicación, tipo, centro de costo).',
        'Sobrantes en almacenes de obra': 'Materiales que sobran en las obras para trasladar o reutilizar.',
        'Licitaciones y propuestas (CRM)': 'Procesos de licitación con cronograma, requisitos y resultado.',
        'Nueva licitación / propuesta': 'Registrar una licitación o propuesta.',
        'Cobranza inmobiliaria': 'Cuotas por cobrar de las ventas de inmuebles.',
        'Reportes inmobiliarios': 'Ventas, separaciones, cobranza y stock de unidades.',
        'Recursos (insumos)': 'Catálogo de recursos (materiales, mano de obra, equipos) con código S10 y sub-análisis.',
        'Vincular recursos con productos': 'Relación entre recursos del presupuesto y productos del almacén.',
        'Biblioteca de partidas tipo': 'Partidas con APU reutilizables.',
        'Índices unificados (INEI)': 'Índices mensuales por área para la fórmula polinómica.',
        'Plantilla de importación (Excel)': 'Plantilla para cargar presupuestos desde Excel.',
        'Resultado por obra (con gastos generales asignados)': 'Ingresos, costos y gastos asignados por obra.',
        'Partes de horas': 'Horas semanales por persona, proyecto y entregable, con su costo hora.',
        'Partes por aprobar': 'Partes de horas que espera el jefe de proyecto (aprobar o rechazar).',
        'Servicios en proceso (cierre del mes)': 'Costo acumulado, avance e ingreso por avance de los proyectos de servicios; asiento del mes.',
        'Garantías y cartas fianza': 'Cartas fianza y pólizas del contrato con su vencimiento.',
    },
    'extra': [('Pantallas dentro de cada obra', [
        ('Presupuesto y APU', 'Partidas, análisis de precios unitarios, precios, relación de insumos y reporte de análisis.'),
        ('Control de obra', 'Meta vs real por recurso con stock y lo que falta pedir.'),
        ('Ejecución', 'Resumen de adelantos, valorizaciones, fondo de garantía, reintegros y facturas.'),
        ('Adelanto / Reclasificar', 'Facturar el adelanto o pasar una factura emitida a anticipo.'),
        ('Valorizar el mes / Valorización', 'Valorización mensual con amortización, garantía, reintegro, aprobación y factura.'),
        ('Fórmula polinómica', 'Monomios, coeficientes e índices de la fórmula.'),
        ('Cronograma / Avance', 'Programación, avance físico con foto y curva S.'),
        ('Control por partida', 'Contractual, Meta, avance, valorizado, costo real y margen por partida.'),
        ('Versiones', 'Adicionales, deductivos y nuevas Metas con comparación.'),
        ('Tareo / Planillas civil', 'Tareo diario y planilla semanal de construcción civil.'),
        ('Partes de equipo', 'Parte diario de equipos con horómetro y combustible.'),
        ('Subcontratos', 'Subcontratos con valorizaciones, adelanto, garantía y HES.'),
        ('Unidades / Separar / Venta', 'Unidades inmobiliarias, separación, venta, cuotas y entrega.'),
        ('Entregables / Horas (proyectos de servicios)', 'Entregables con conformidad y factura, y horas presupuestadas frente a reales.'),
    ])],
    'integra': [
        ('Compras e Inventario', 'Solicitudes desde la Meta, almacén de obra, traslados y consumos.'),
        ('Ventas', 'Facturas de adelanto y de valorización (Nubefact).'),
        ('Planillas', 'Régimen de construcción civil.'),
        ('Contabilidad', 'Centro de costo de la obra, anticipos (122), fondo de garantía (1683 / 467) y resultado por obra.'),
        ('Seguridad y salud', 'Registros de SST por obra y SCTR.'),
    ],
}

M['sst'] = {
    'intro': ('Lleva los registros obligatorios de seguridad y salud en el trabajo (Ley 29783, D.S. 005-2012-TR y '
              'G.050 en construcción) por empresa, área u obra: exámenes médicos, monitoreos, inspecciones, '
              'capacitaciones, equipos de emergencia, IPERC, entrega de EPP, SCTR, permisos de alto riesgo, '
              'accidentes con plazo de notificación e índices de accidentabilidad, con validación por especialista.'),
    'procesos': [
        ('Identificación de peligros (IPERC)', [
            'Registre el IPERC por proceso, puesto, área u obra: tarea, peligro, riesgo, probabilidad × severidad y controles.',
            'Calcule el riesgo residual y envíelo a validar (supervisor o jefe de SST).',
        ], 'Matriz IPERC validada y vigente.'),
        ('Registros obligatorios', [
            'Registre los <b>Exámenes médicos ocupacionales</b> con resultado, restricciones y vencimiento (valida el médico ocupacional).',
            'Registre los <b>Monitoreos de agentes</b>, <b>Inspecciones</b> (con acciones correctivas), <b>Inducción, capacitación y simulacros</b> (con asistentes) y <b>Equipos de seguridad y emergencia</b>.',
        ], 'Registros al día con alertas de vencimiento a 15 días.'),
        ('Entrega de EPP', [
            'Registre la entrega por trabajador: almacén y EPP; se genera la salida de almacén al gasto con el centro de costo del trabajador o de la obra.',
            'Adjunte el cargo firmado; queda la fecha de reposición.',
        ], 'Stock de EPP descontado y cargo de entrega archivado.'),
        ('Permisos de trabajo de alto riesgo', [
            'Emita el permiso (altura, caliente, espacio confinado, eléctrico, excavación, izaje) con responsable, trabajadores y controles.',
            'Valídelo con el supervisor de SST y ciérrelo al terminar.',
        ], 'Trabajos de riesgo autorizados y trazables.'),
        ('Accidentes, incidentes y enfermedades', [
            'Registre el evento con fecha, trabajador, tipo y gravedad; el sistema calcula el plazo de notificación.',
            'Investigue las causas, registre días de descanso, costo y centro de costo, y defina las acciones correctivas.',
            'Valida el jefe o el comité de SST.',
        ], 'Evento notificado a tiempo y alimentando los índices.'),
        ('Índices y validación', [
            'Revise los <b>Índices de accidentabilidad</b> por mes, empresa u obra: IF, IG e IA con las horas-hombre trabajadas.',
            'Cada registro pasa por Borrador → Por validar → Validado u Observado; se adjunta el registro firmado y un cambio crea una versión nueva.',
        ], 'Indicadores de SST confiables.'),
    ],
    'pantallas': {
        'Tablero de SST': 'Resumen de vencimientos, accidentes, permisos abiertos y registros por validar.',
        'Accidentes, incidentes y enfermedades': 'Eventos con investigación, plazo de notificación y acciones.',
        'Índices de accidentabilidad': 'IF, IG e IA por mes, empresa u obra.',
        'Exámenes médicos ocupacionales': 'EMO por trabajador con vencimiento.',
        'Monitoreo de agentes': 'Monitoreos físicos, químicos, biológicos, disergonómicos y psicosociales.',
        'Inspecciones': 'Inspecciones con hallazgos y acciones correctivas.',
        'Inducción, capacitación y simulacros': 'Eventos con tema, horas, expositor y asistentes.',
        'Equipos de seguridad y emergencia': 'Extintores, botiquines y otros con su vencimiento.',
        'IPERC': 'Matriz de identificación de peligros y evaluación de riesgos.',
        'Entrega de EPP': 'Entregas por trabajador con salida de almacén y reposición.',
        'SCTR': 'Pólizas de salud y pensión por trabajador y obra con vigencia.',
        'Permisos de trabajo de alto riesgo': 'Permisos con responsable, trabajadores y controles.',
    },
    'integra': [
        ('Planillas', 'Trabajadores, áreas y horas-hombre.'),
        ('Inventario', 'Salida de EPP del almacén.'),
        ('Obras', 'Registros y SCTR por obra.'),
        ('Excelencia organizacional', 'Acciones correctivas e índices como indicadores.'),
    ],
}

M['calidad'] = {
    'intro': ('Sostiene el sistema de gestión (ISO 9001, 14001, 45001): objetivos e indicadores por área con semáforo, '
              'no conformidades con análisis de causa, acciones correctivas y preventivas con verificación de eficacia, '
              'auditorías internas, documentos controlados y sugerencias de mejora (Kaizen), con validación por especialista.'),
    'procesos': [
        ('Objetivos e indicadores por área', [
            'Defina el indicador en <b>Definir indicadores</b>: área, meta, sentido, tolerancia (ámbar), frecuencia y responsable.',
            'Los automáticos se calculan solos (días de cobro, no conformidades abiertas, acciones vencidas, IF, IG, IA, EMO vencidos, OEE); los manuales se ingresan con su evidencia.',
            'Revise el semáforo en <b>Objetivos e indicadores (BSC)</b>.',
        ], 'Tablero de gestión por área.'),
        ('No conformidad y acción correctiva', [
            'Registre la no conformidad (auditoría, reclamo, inspección de SST, proveedor, producción).',
            'Analice la causa (5 porqués o Ishikawa 6M) y defina la causa raíz.',
            'Cree las <b>Acciones correctivas y preventivas</b> con responsable y plazo; ejecútelas y verifique su eficacia (una no eficaz se reabre).',
            'La no conformidad se cierra cuando todas sus acciones están verificadas.',
        ], 'Problema resuelto en su causa raíz.'),
        ('Auditoría interna', [
            'Planifique la auditoría (norma, alcance, auditor) y registre los hallazgos.',
            'Convierta los hallazgos en no conformidades.',
        ], 'Ciclo de mejora alimentado por la auditoría.'),
        ('Documentos controlados y Kaizen', [
            'Registre el documento con código, versión, aprobación y próxima revisión.',
            'Registre las sugerencias de mejora, evalúelas con el ahorro estimado y controle su implementación.',
        ], 'Documentación vigente y mejora continua medida.'),
    ],
    'pantallas': {
        'Objetivos e indicadores (BSC)': 'Indicadores por área con semáforo y tendencia.',
        'Definir indicadores': 'Configurar metas, tolerancias y responsables.',
        'Acciones correctivas y preventivas': 'Acciones con plazo, ejecución y verificación de eficacia.',
        'No conformidades': 'No conformidades con análisis de causa y cierre.',
        'Auditorías internas': 'Auditorías con hallazgos.',
        'Documentos controlados': 'Documentos con versión, aprobación y revisión.',
        'Sugerencias de mejora (Kaizen)': 'Ideas de mejora con evaluación y ahorro.',
    },
    'integra': [
        ('Seguridad y salud', 'Inspecciones y accidentes generan acciones; los índices son indicadores.'),
        ('Ventas, Manufactura', 'Días de cobro y OEE como indicadores automáticos.'),
    ],
}

M['planillas'] = {
    'intro': ('Administra el personal y sus remuneraciones: organigrama y puestos, trabajadores, contratos y legajo, '
              'asistencia y turnos, vacaciones y permisos, movimientos de personal con aprobación, cálculo de la '
              'planilla mensual con gratificaciones, CTS y provisiones, liquidaciones por cese, PLAME y T-Registro.'),
    'procesos': [
        ('Organización y personal', [
            'Arme el <b>Organigrama, unidades y puestos</b> (define quién es jefe de quién).',
            'Registre los <b>Trabajadores</b>: régimen, sistema de pensiones (AFP/ONP), cuenta de sueldo, centro de costo y puesto.',
            'Controle <b>Contratos y legajo</b> con sus vencimientos.',
        ], 'Base de personal para planillas, aprobaciones y SST.'),
        ('Asistencia, vacaciones y permisos', [
            'Defina los <b>Turnos de trabajo</b> e importe las <b>Marcaciones</b>.',
            'El trabajador pide vacaciones o permisos; el jefe aprueba. El <b>Récord vacacional</b> se actualiza solo.',
        ], 'Asistencia lista para el cálculo.'),
        ('Movimientos de personal', [
            'Registre el alta, cambio de puesto o sueldo, o el cese en <b>Movimientos de personal</b>.',
            'Lo aprueba el jefe del trabajador o quien tiene «aprobar_movimientos» (o según reglas); el movimiento dice quién puede aprobarlo.',
        ], 'Cambios del personal autorizados antes de calcular.'),
        ('Planilla mensual', [
            'Cree la planilla en <b>Nueva planilla</b> y calcule (permiso «calcular»): remuneración, horas extra, asignación familiar, AFP/ONP, renta de 5ta, EsSalud y descuentos.',
            'Revise, cierre la planilla (permiso «cerrar») y pague por Tesorería.',
            'Calcule gratificaciones, CTS y provisiones en su mes; genere PLAME y los datos del T-Registro.',
        ], 'Planilla pagada con su asiento y provisiones.'),
        ('Liquidación por cese', [
            'En <b>Liquidación de beneficios (ceses)</b>, calcule CTS, vacaciones truncas y gratificación trunca.',
        ], 'Liquidación lista para pago y firma.'),
    ],
    'pantallas': {
        'Planillas': 'Planillas por periodo con su estado (calculada, cerrada, pagada).',
        'Nueva planilla': 'Crear la planilla de un periodo.',
        'Liquidación de beneficios (ceses)': 'Liquidación de beneficios sociales al cese.',
        'Organigrama, unidades y puestos': 'Estructura de la empresa y jefaturas.',
        'Movimientos de personal': 'Altas, cambios y ceses con aprobación.',
        'Solicitudes de vacaciones y permisos': 'Pedidos del personal y su aprobación.',
        'Trabajadores': 'Maestro de trabajadores.',
        'Contratos y legajo (vencimientos)': 'Contratos, documentos y vencimientos.',
        'Vacaciones y récord vacacional': 'Días ganados, gozados y pendientes.',
        'Asistencia y marcaciones': 'Marcaciones importadas y asistencia diaria.',
        'Turnos de trabajo': 'Horarios y turnos.',
        'Costo por centro de costo': 'Costo de personal por centro de costo.',
        'Datos para el T-Registro (Excel)': 'Datos de trabajadores en el formato del T-Registro.',
        'Parámetros, AFP y conceptos': 'RMV, UIT, tasas de AFP, conceptos de ingreso y descuento (permiso «configurar»).',
    },
    'integra': [
        ('Tesorería', 'Pago de sueldos y obligaciones.'),
        ('Contabilidad', 'Asiento de planilla y provisiones por centro de costo.'),
        ('Obras', 'Régimen de construcción civil (tareo y planilla semanal).'),
        ('Seguridad y salud', 'Trabajadores, áreas y horas-hombre.'),
    ],
}

M['requerimientos'] = {
    'intro': ('Es el canal de las áreas para pedir: requerimientos de materiales al almacén (con aprobación del jefe) '
              'y solicitudes de pedido a Compras (con liberación por reglas, también desde el celular).'),
    'procesos': [
        ('Requerimiento al almacén', [
            'Registre el pedido en <b>Nuevo requerimiento</b>: productos, cantidades y centro de costo (permiso «solicitar»).',
            'El jefe lo aprueba o rechaza en <b>Por aprobar</b> (permiso «aprobar»).',
            'El almacén lo atiende en <b>Por atender</b>: la salida se carga al centro de costo del área.',
        ], 'Material entregado y cargado al área correcta.'),
        ('Solicitud de pedido a Compras', [
            'Registre la solicitud en <b>Nueva solicitud de pedido</b> y envíela (permiso «solped»).',
            'Se libera, observa o rechaza en <b>Por liberar</b> o <b>Liberar desde el celular</b> (permiso «liberar_solped»).',
            'Liberada, pasa a la bandeja de compras del comprador.',
        ], 'Necesidad de compra aprobada y en manos de Compras.'),
    ],
    'pantallas': {
        'Mis requerimientos': 'Requerimientos del usuario con su estado.',
        'Nuevo requerimiento': 'Pedir materiales al almacén.',
        'Por aprobar': 'Requerimientos que esperan la aprobación del jefe.',
        'Por atender': 'Requerimientos aprobados que el almacén debe despachar.',
        'Mis solicitudes de pedido': 'Solicitudes de compra del usuario con su estado.',
        'Nueva solicitud de pedido': 'Registrar una solicitud de compra.',
        'Liberar desde el celular': 'Vista móvil para liberar solicitudes.',
    },
    'integra': [
        ('Inventario', 'Salida de materiales al centro de costo.'),
        ('Compras', 'Solicitudes liberadas a la bandeja de compras.'),
    ],
}

M['rendiciones'] = {
    'intro': ('Controla los gastos de entregas a rendir, caja chica y tarjetas corporativas: el colaborador registra '
              'sus comprobantes desde el celular (foto, QR, sin internet), se aprueban por reglas, el revisor contable '
              'los contabiliza y Tesorería reembolsa, cobra la devolución o repone la caja chica.'),
    'procesos': [
        ('Rendición de una entrega a rendir', [
            'Tesorería entrega el dinero (<b>Tesorería › Entregas a rendir y caja chica</b>).',
            'El colaborador registra cada gasto en <b>Nueva rendición</b> o en la <b>App del celular</b>: foto del comprobante, lectura del QR o con IA, validación en SUNAT; funciona sin internet y sincroniza después.',
            'Envía la rendición; se aprueba, observa o rechaza según las reglas (también <b>Aprobar desde el celular</b>).',
            'El revisor contable la revisa y contabiliza en <b>Por contabilizar</b>: las facturas pasan al registro de compras.',
            'Tesorería liquida el saldo en <b>Reembolsos y devoluciones</b>.',
        ], 'Gasto sustentado, contabilizado y saldo cerrado.'),
        ('Caja chica', [
            'Registre los gastos de la caja como una rendición.',
            'Al llegar al tope, pida la <b>Reposición de caja chica</b>.',
        ], 'Caja repuesta con sus gastos sustentados.'),
        ('Tarjetas corporativas', [
            'Cargue los movimientos de la tarjeta y asígnelos a su comprobante.',
            'Revise los <b>Cargos de tarjeta sin comprobante</b>.',
        ], 'Cargos de tarjeta conciliados.'),
        ('Configuración', [
            'Defina categorías, topes y políticas de gasto en <b>Categorías, políticas y aprobación</b>; las reglas de aprobación vigentes están en Ajustes › Reglas de aprobación.',
            'Configure proyectos y sitios, integraciones (WhatsApp, correo, IA) y la exportación a un ERP externo.',
        ], 'Reglas claras para todos los colaboradores.'),
    ],
    'pantallas': {
        'Mis rendiciones': 'Rendiciones del usuario con su estado y saldo.',
        'Nueva rendición': 'Registrar una rendición con sus comprobantes.',
        'App del celular (foto, QR, sin internet)': 'Registrar gastos desde el celular.',
        'Aprobar desde el celular': 'Vista móvil para aprobar rendiciones.',
        'Por aprobar': 'Rendiciones que esperan aprobación.',
        'Por contabilizar': 'Rendiciones aprobadas para el revisor contable.',
        'Reembolsos y devoluciones': 'Saldos a reembolsar al colaborador o a devolver a la empresa.',
        'Reposición de caja chica': 'Solicitudes de reposición de caja chica.',
        'Tarjetas corporativas': 'Movimientos de tarjetas y su sustento.',
        'Todas las rendiciones': 'Todas las rendiciones con filtros.',
        'Tablero de gastos': 'Gastos por categoría, área, proyecto y periodo.',
        'Saldos por rendir con antigüedad': 'Entregas pendientes de rendir y su antigüedad.',
        'Cargos de tarjeta sin comprobante': 'Cargos sin sustento.',
        'Trazabilidad de aprobación': 'Quién aprobó cada rendición y cuándo.',
        'Observaciones y errores por usuario y área': 'Calidad de las rendiciones por colaborador.',
        'Categorías, políticas y aprobación': 'Categorías de gasto, topes y políticas.',
        'Proyectos y sitios': 'Proyectos y sitios a los que se cargan los gastos.',
        'Integraciones (WhatsApp, correo, IA)': 'Recepción de comprobantes por WhatsApp o correo y lectura con IA.',
        'Exportación a un ERP externo (SAP)': 'Archivo de exportación para otro ERP.',
        'Cargar categorías (Excel)': 'Categorías de gasto desde Excel.',
    },
    'integra': [
        ('Tesorería', 'Entrega, reembolso, devolución y reposición.'),
        ('Compras', 'Facturas al registro de compras.'),
        ('Contabilidad', 'Asiento de la rendición.'),
    ],
}

M['logistica'] = {
    'intro': ('Emite las guías de remisión electrónicas (remitente y transportista), controla la garita (ingreso y '
              'salida de vehículos) y administra la flota: equipos y vehículos, lecturas de odómetro/horómetro, '
              'combustible, viajes, préstamos, costo por km u hora y documentos por vencer.'),
    'procesos': [
        ('Guía de remisión remitente', [
            'Cree la guía en <b>Nueva guía remitente</b> (o desde la venta o el traslado): motivo, puntos de partida y llegada, vehículo y conductor.',
            'Emítala (permiso «emitir»): se envía a SUNAT por Nubefact.',
        ], 'Mercadería trasladada con guía válida.'),
        ('Guía de remisión transportista', [
            'Si la empresa transporta para terceros, emita la guía en <b>Nueva guía transportista</b>.',
        ], 'Servicio de transporte documentado.'),
        ('Garita', [
            'Registre el <b>Ingreso de vehículo</b>: placa, conductor, guía y orden de compra.',
            'Registre la salida. Los montos de la mercadería solo los ve quien tiene «ver_montos».',
        ], 'Control de lo que entra y sale de la planta.'),
        ('Flota', [
            'Registre los <b>Equipos y vehículos</b> con sus componentes y documentos (SOAT, revisión técnica, licencias).',
            'Registre el <b>Parte diario de odómetros / horómetros</b>; se aprueban en <b>Lecturas por aprobar</b> (permiso «aprobar_lecturas»).',
            'Registre <b>Combustible (vales y rendimiento)</b>, <b>Viajes</b> y <b>Préstamos y comodato</b>.',
            'Revise el <b>Costo por equipo (km u hora)</b> y los <b>Documentos por vencer</b>.',
        ], 'Flota controlada con su costo real.'),
    ],
    'pantallas': {
        'Guías remitente': 'Guías emitidas como remitente con su estado SUNAT.',
        'Guías transportista': 'Guías emitidas como transportista.',
        'Nueva guía remitente': 'Emitir una guía remitente.',
        'Nueva guía transportista': 'Emitir una guía transportista.',
        'Registro de garita': 'Ingresos y salidas registrados.',
        'Ingreso de vehículo': 'Registrar un ingreso en garita.',
        'Equipos y vehículos': 'Maestro de la flota con componentes y documentos.',
        'Parte diario de odómetros / horómetros': 'Lecturas diarias.',
        'Lecturas por aprobar': 'Lecturas pendientes de aprobación.',
        'Combustible (vales y rendimiento)': 'Vales de combustible y rendimiento por equipo.',
        'Viajes': 'Viajes con ruta, conductor y kilometraje.',
        'Préstamos y comodato': 'Equipos prestados o recibidos.',
        'Costo por equipo (km u hora)': 'Costo de operación por km u hora.',
        'Cargar equipos, componentes y lecturas (Excel)': 'Carga masiva de la flota.',
        'Documentos por vencer (SOAT, revisión, licencias)': 'Vencimientos de la flota y conductores.',
        'Vehículos': 'Vehículos para las guías.',
        'Conductores': 'Conductores con licencia.',
    },
    'integra': [
        ('Ventas e Inventario', 'Guías de despachos y traslados.'),
        ('Compras', 'Ingresos en garita contra la orden.'),
        ('Obras y Manufactura', 'Horómetros y costo de equipos.'),
    ],
}

M['tesoreria'] = {
    'intro': ('Administra el dinero: cuentas de caja y bancos, cobranzas y pagos (individuales, múltiples y masivos), '
              'transferencias, cheques, entregas a rendir, detracciones, conciliación bancaria, flujo de caja real y '
              'proyectado y tipo de cambio.'),
    'procesos': [
        ('Configuración inicial', [
            'Cree las <b>Cuentas de caja y bancos</b> con su moneda, cuenta contable y saldo inicial (permiso «cuentas»).',
            'Mantenga el <b>Tipo de cambio</b> del día.',
        ], 'Cuentas listas para operar.'),
        ('Cobranza', [
            'Registre el cobro en <b>Cobranzas</b> (o <b>Cobranza múltiple</b> para varios clientes): documentos, cuenta, fecha y medio de pago.',
            'Registre retenciones y la diferencia de cambio; las constancias de detracción de clientes se cargan desde SUNAT.',
        ], 'Cuenta por cobrar cancelada y asiento de cobranza.'),
        ('Pagos', [
            'Registre el pago en <b>Pagos</b> o <b>Pago múltiple</b>; solo se pagan facturas no retenidas.',
            'Para muchos pagos, genere el archivo del banco en <b>Pagos masivos a bancos</b>.',
            'Deposite las detracciones de proveedores en lote en <b>Depósito masivo de detracciones</b>.',
        ], 'Proveedores pagados y detracciones depositadas.'),
        ('Movimientos de caja y bancos', [
            'Registre <b>Transferencias</b> entre cuentas (permiso «transferir»), <b>Cheques</b> y otros <b>Movimientos</b>.',
            'Entregue fondos en <b>Entregas a rendir y caja chica</b>.',
        ], 'Saldos de caja y bancos al día.'),
        ('Conciliación bancaria', [
            'Importe el estado de cuenta en <b>Importar estado de cuenta</b>.',
            'Use la <b>Conciliación automática (extracto)</b> y termine en <b>Conciliación bancaria</b>.',
        ], 'Saldo del banco igual al del libro.'),
        ('Flujo de caja', [
            'Revise el <b>Flujo de caja</b> real y el <b>Flujo de caja proyectado</b>.',
        ], 'Liquidez prevista para decidir pagos y financiamiento.'),
    ],
    'pantallas': {
        'Cuentas de caja y bancos': 'Cuentas con su saldo y movimientos.',
        'Movimientos': 'Ingresos y egresos de caja y bancos.',
        'Transferencias': 'Transferencias entre cuentas propias.',
        'Cheques': 'Cheques emitidos y recibidos con su estado.',
        'Entregas a rendir y caja chica': 'Fondos entregados al personal.',
        'Cobranzas': 'Cobros de clientes.',
        'Cobranza múltiple (varios clientes)': 'Un depósito aplicado a facturas de varios clientes.',
        'Pagos': 'Pagos a proveedores.',
        'Pago múltiple (varios proveedores)': 'Un pago que cancela facturas de varios proveedores.',
        'Pagos masivos a bancos': 'Archivo de pagos para el banco.',
        'Depósito masivo de detracciones': 'Lote de detracciones para SUNAT con sus constancias.',
        'Constancias de detracción de clientes (SUNAT)': 'Constancias de los depósitos de los clientes.',
        'Conciliación automática (extracto)': 'Cruce automático del extracto con los movimientos.',
        'Conciliación bancaria': 'Conciliación mensual por cuenta.',
        'Importar estado de cuenta': 'Carga del extracto bancario.',
        'Flujo de caja': 'Ingresos y egresos reales por periodo.',
    },
    'integra': [
        ('Ventas y Compras', 'Cuentas por cobrar y por pagar.'),
        ('Finanzas', 'Cobro y pago de letras, anticipos y cuotas.'),
        ('Planillas y Rendiciones', 'Pago de sueldos, entregas a rendir y reembolsos.'),
        ('Contabilidad', 'Asientos de cobranza, pago y diferencia de cambio.'),
    ],
}

M['finanzas'] = {
    'intro': ('Gestiona los instrumentos financieros: anticipos de clientes y a proveedores con su aplicación, '
              'letras por cobrar y por pagar, canje de facturas por letras, préstamos y leasing con su cronograma, '
              'y el flujo de caja proyectado.'),
    'procesos': [
        ('Anticipos', [
            'Registre el anticipo recibido o entregado en <b>Anticipos</b>.',
            'Aplíquelo a la factura cuando se emita o se reciba (permiso «registrar»).',
        ], 'Saldo del cliente o proveedor neto del anticipo.'),
        ('Letras', [
            'Canjee facturas por letras en <b>Canje de facturas por letras</b>.',
            'Controle las <b>Letras por cobrar y pagar</b>; su cobro o pago se registra en Tesorería.',
        ], 'Deuda documentada en letras.'),
        ('Préstamos y leasing', [
            'Registre el préstamo o leasing en <b>Préstamos y leasing</b> (permiso «cuentas»): monto, tasa, plazo y cronograma.',
            'Registre las cuotas (capital e intereses) al pagarlas.',
        ], 'Deuda financiera con sus intereses contabilizados.'),
    ],
    'pantallas': {
        'Anticipos': 'Anticipos de clientes y a proveedores con su saldo.',
        'Letras por cobrar y pagar': 'Letras con vencimiento y estado.',
        'Canje de facturas por letras': 'Convertir facturas en letras.',
        'Préstamos y leasing': 'Préstamos con cronograma de cuotas.',
    },
    'integra': [
        ('Tesorería', 'Cobro y pago de letras y cuotas.'),
        ('Ventas y Compras', 'Facturas canjeadas o con anticipo aplicado.'),
        ('Contabilidad', 'Asientos de anticipos, letras e intereses.'),
    ],
}

M['contabilidad'] = {
    'intro': ('Recibe automáticamente los asientos de todos los módulos y produce los libros y estados financieros: '
              'asientos manuales y extornos, centralización y cierre de periodos, reparto de gastos entre centros, '
              'libros diario y mayor, estados financieros (incluido el EFE NIC 7), prorrata del IGV, presupuestos, '
              'consolidación del grupo y atención a clientes de un estudio contable.'),
    'procesos': [
        ('Contabilidad automática y asientos manuales', [
            'Cada venta, compra, movimiento de almacén, cobro, pago, planilla y depreciación genera su asiento.',
            'Registre lo que no viene de otro módulo en <b>Nuevo asiento manual</b> (permiso «asientos»); corrija con un extorno (permiso «extornar»).',
        ], 'Libro diario completo sin digitar dos veces.'),
        ('Cierre del mes', [
            'Siga el <b>Cierre guiado del mes</b>: revisa pendientes de cada módulo.',
            'Centralice y cierre el periodo en <b>Centralización y periodos</b> (permiso «periodos»). Reabrir exige permiso y motivo.',
        ], 'Periodo cerrado; nadie registra con esa fecha.'),
        ('Reparto de gastos y resultados por centro u obra', [
            'Defina las <b>Cifras de reparto</b> (m², trabajadores, horas de equipo…).',
            'Ejecute el <b>Reparto de gastos entre centros (obras)</b>.',
            'Revise <b>Resultado por centro y obra</b> y <b>Gastos por centro de costo</b>.',
        ], 'Resultado de cada obra o línea con sus gastos generales.'),
        ('Libros y estados financieros', [
            'Emita el <b>Libro diario</b>, el <b>Libro mayor</b>, <b>Inventarios y Balances</b> y los archivos PLE.',
            'Revise el <b>Balance de comprobación</b>, <b>Estado de situación financiera</b>, <b>Estado de resultados</b> y <b>Estado de flujos de efectivo (NIC 7)</b>.',
            'Calcule la <b>Prorrata del crédito fiscal (IGV)</b> y la <b>Conciliación NIIF - tributaria</b>.',
        ], 'Estados financieros y libros electrónicos del periodo.'),
        ('Presupuestos y grupo', [
            'Elabore el presupuesto (permiso «presupuesto») y compárelo en <b>Presupuestos y ejecución</b>.',
            'Consolide las empresas del grupo en <b>Estados consolidados del grupo</b>.',
        ], 'Control presupuestal y visión del grupo.'),
        ('Estudio contable', [
            'El cliente sube sus documentos en «Mi empresa»; el contador los revisa en <b>Documentos del cliente (estudio)</b>.',
        ], 'Documentos del cliente ordenados por periodo.'),
    ],
    'pantallas': {
        'Asientos contables': 'Todos los asientos con su origen, filtros y detalle.',
        'Nuevo asiento manual': 'Registrar un asiento manual.',
        'Centralización y periodos': 'Centralizar, cerrar y reabrir periodos.',
        'Reparto de gastos entre centros (obras)': 'Distribuir gastos comunes entre centros de costo y obras.',
        'Cierre guiado del mes': 'Lista de verificación del cierre mensual.',
        'Libro diario': 'Libro diario y PLE 5.1 / 5.2.',
        'Libro mayor': 'Mayor por cuenta y PLE 6.1.',
        'Inventarios y Balances (3.x)': 'Formatos 3.x de SUNAT.',
        'Inventario permanente 12.1 / 13.1': 'Libros de inventario permanente.',
        'Balance de comprobación': 'Saldos y movimientos por cuenta.',
        'Estado de situación financiera': 'Activo, pasivo y patrimonio.',
        'Estado de resultados': 'Ingresos, costos y gastos.',
        'Estado de flujos de efectivo (NIC 7)': 'EFE por método directo e indirecto.',
        'Resultados por línea de negocio': 'Resultado por centro de beneficio.',
        'Gastos por centro de costo': 'Gastos por centro y cuenta.',
        'Resultado por centro y obra (con gastos asignados)': 'Resultado con gastos generales asignados.',
        'Presupuestos y ejecución': 'Presupuesto vs real por cuenta y centro.',
        'Prorrata del crédito fiscal (IGV)': 'Cálculo de la prorrata del IGV.',
        'Conciliación NIIF - tributaria': 'Diferencias entre la base contable y la tributaria.',
        'Estados consolidados del grupo': 'Estados financieros consolidados.',
        'Documentos del cliente (estudio)': 'Documentos subidos por los clientes del estudio.',
        'Conciliación de la migración': 'Cuadre de saldos migrados del sistema anterior.',
        'Asientos del sistema anterior': 'Consulta de asientos migrados.',
        'Balance del sistema anterior': 'Balance migrado.',
        'Posiciones presupuestarias': 'Posiciones del presupuesto migradas.',
        'Plan de cuentas': 'Plan contable (PCGE) con niveles y naturaleza.',
        'Centros de beneficio (líneas)': 'Líneas de negocio.',
        'Centros de costo': 'Centros de costo (áreas, obras).',
        'Cifras de reparto (m², trabajadores…)': 'Bases para repartir gastos.',
        'Cuentas por operación': 'Cuentas que usa cada tipo de operación.',
        'Estado de flujos de efectivo (rubros y reglas)': 'Configuración del EFE.',
        'Cuentas por tipo de producto': 'Cuentas de compra, venta y existencia por tipo de producto.',
    },
    'integra': [
        ('Todos los módulos', 'Asientos automáticos de cada operación.'),
        ('SUNAT', 'Libros electrónicos PLE.'),
    ],
}

M['cliente'] = {
    'intro': ('Portal para el cliente de un estudio contable: sube sus comprobantes y documentos del mes y '
              'consulta los reportes que el estudio le prepara.'),
    'procesos': [
        ('Enviar documentos al estudio', [
            'En <b>Subir documentos</b>, elija el periodo y el tipo y adjunte los archivos.',
            'Revise el estado en <b>Mis documentos</b>.',
        ], 'El estudio recibe los documentos ordenados por periodo.'),
        ('Consultar reportes', [
            'Abra <b>Mis reportes</b> para ver los estados y reportes que el estudio publicó.',
        ], 'Información contable sin pedirla por correo.'),
    ],
    'pantallas': {
        'Subir documentos': 'Adjuntar comprobantes y documentos del periodo.',
        'Mis documentos': 'Documentos enviados con su estado.',
        'Mis reportes': 'Reportes publicados por el estudio.',
    },
    'integra': [('Contabilidad', 'El estudio ve los documentos en «Documentos del cliente».')],
}

M['contactos'] = {
    'intro': ('Directorio único de clientes y proveedores (terceros) que usan todos los módulos, con consulta de RUC y '
              'DNI en línea.'),
    'procesos': [
        ('Registrar un tercero', [
            'Ingrese el RUC o DNI: los datos se completan con la consulta en línea.',
            'Complete condiciones de pago, cuentas bancarias, contactos, línea de crédito, detracción y retención.',
        ], 'Cliente o proveedor disponible en ventas, compras y tesorería.'),
    ],
    'pantallas': {
        'Todos': 'Todos los terceros (clientes y proveedores).',
    },
    'integra': [('Ventas, Compras, Tesorería', 'Usan el mismo maestro de terceros.')],
}

M['ajustes'] = {
    'intro': ('Configuración general de la empresa y del sistema: datos y régimen tributario, parámetros, usuarios, '
              'perfiles y permisos, ingreso con Microsoft/Google, segregación de funciones, reglas de aprobación y '
              'suplencias, auditoría, incidencias, series, facturación electrónica, correo, carga masiva y respaldo. '
              'Solo para administradores.'),
    'procesos': [
        ('Configurar la empresa', [
            'Complete los <b>Datos de la empresa</b> (RUC, razón social, dirección, logo).',
            'Defina el <b>Giro y régimen tributario</b> y los <b>Parámetros tributarios</b> (UIT, tasas, umbrales) con su vigencia.',
            'Configure <b>Correlativos / series</b>, <b>Facturación electrónica</b> (Nubefact) y <b>Correo saliente</b>.',
        ], 'Empresa lista para emitir y operar.'),
        ('Usuarios y permisos', [
            'Cree el usuario en <b>Usuarios</b>: módulos asignados y acciones permitidas (un usuario nuevo empieza sin acciones; o use un perfil).',
            'Defina <b>Perfiles de acceso (roles)</b> para asignar módulos y acciones en bloque. Al guardar, el sistema avisa acciones de módulos no asignados.',
            'Revise la <b>Segregación de funciones</b> (combinaciones de permisos incompatibles).',
            'Active el <b>Ingreso con Microsoft / Google</b> si corresponde.',
        ], 'Cada persona ve y hace solo lo que le corresponde.'),
        ('Aprobaciones', [
            'Configure en <b>Reglas de aprobación (todos los documentos)</b> los niveles por documento, monto y área, con aprobador de reemplazo.',
            'Registre las <b>Suplencias de aprobación</b> por vacaciones o ausencias.',
        ], 'Flujos de aprobación sin documentos atascados.'),
        ('Control y soporte', [
            'Revise la <b>Auditoría de todo el sistema</b> y las <b>Incidencias y errores</b> reportados.',
            'Descargue un <b>Respaldo de datos</b> y use la <b>Carga masiva (Excel)</b> para maestros.',
            'Consulte los <b>Manuales de usuario</b> y las <b>Novedades por versión</b>.',
        ], 'Sistema auditado y respaldado.'),
    ],
    'pantallas': {
        'Datos de la empresa': 'RUC, razón social, dirección, logo y módulos visibles.',
        'Giro y régimen tributario': 'Giros (comercializadora, manufactura, construcción, inmobiliaria, servicios y servicios profesionales por proyecto), régimen, costeo y módulos que usa la empresa.',
        'Parámetros tributarios (UIT, tasas, umbrales)': 'Valores con vigencia que usan los cálculos.',
        'Usuarios': 'Usuarios con módulos, acciones, perfiles y estado.',
        'Perfiles de acceso (roles)': 'Plantillas de módulos y acciones.',
        'Ingreso con Microsoft / Google': 'Configuración del ingreso con cuenta corporativa.',
        'Segregación de funciones': 'Conflictos de permisos por usuario.',
        'Reglas de aprobación (todos los documentos)': 'Niveles y aprobadores de cada documento.',
        'Suplencias de aprobación': 'Quién aprueba en reemplazo y por cuánto tiempo.',
        'Auditoría de todo el sistema': 'Bitácora de cambios de todos los módulos.',
        'Incidencias y errores': 'Errores del sistema y reportes de usuarios.',
        'Correlativos / series': 'Series y números de cada tipo de documento.',
        'Facturación electrónica': 'Conexión con Nubefact y la API de SUNAT.',
        'Correo saliente': 'Servidor SMTP para avisos y envío de documentos.',
        'Carga masiva (Excel)': 'Carga de maestros desde Excel.',
        'Respaldo de datos': 'Descarga de una copia de los datos.',
        'Manuales de usuario': 'Manuales en PDF del sistema, incluido este manual de procesos por módulo.',
        'Novedades por versión': 'Cambios de cada versión.',
    },
    'integra': [('Todos los módulos', 'Usuarios, permisos, reglas, parámetros y series.')],
}
