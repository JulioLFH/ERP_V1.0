"""Plan Contable General Empresarial (PCGE 2019) — selección de cuentas de uso frecuente
y cuentas por defecto que usa la centralización automática.

Las cuentas sin subcuentas en esta lista quedan como imputables (aceptan movimientos).
El usuario puede agregar o editar cuentas desde Contabilidad > Plan de cuentas.
"""

PCGE = [
    ('10', 'EFECTIVO Y EQUIVALENTES DE EFECTIVO'),
    ('101', 'Caja'), ('1011', 'Caja'),
    ('102', 'Fondos fijos'), ('1021', 'Caja chica'),
    ('103', 'Efectivo en tránsito'), ('1031', 'Efectivo en tránsito'),
    ('104', 'Cuentas corrientes en instituciones financieras'),
    ('1041', 'Cuentas corrientes operativas'), ('1042', 'Cuentas corrientes para fines específicos (detracciones)'),
    ('106', 'Depósitos en instituciones financieras'), ('1061', 'Depósitos de ahorro'), ('1062', 'Depósitos a plazo'),
    ('12', 'CUENTAS POR COBRAR COMERCIALES - TERCEROS'),
    ('121', 'Facturas, boletas y otros comprobantes por cobrar'), ('1212', 'Emitidas en cartera'),
    ('1213', 'En cobranza'),
    ('122', 'Anticipos de clientes'), ('1221', 'Anticipos de clientes'),
    ('123', 'Letras por cobrar'), ('1232', 'Letras en cartera'),
    ('14', 'CUENTAS POR COBRAR AL PERSONAL, ACCIONISTAS, DIRECTORES Y GERENTES'),
    ('141', 'Personal'), ('1411', 'Préstamos al personal'),
    ('142', 'Accionistas'), ('1421', 'Préstamos a accionistas'),
    ('16', 'CUENTAS POR COBRAR DIVERSAS - TERCEROS'),
    ('162', 'Reclamaciones a terceros'), ('1629', 'Otras reclamaciones'),
    ('18', 'SERVICIOS Y OTROS CONTRATADOS POR ANTICIPADO'), ('182', 'Seguros'), ('1821', 'Seguros pagados por anticipado'),
    ('19', 'ESTIMACIÓN DE CUENTAS DE COBRANZA DUDOSA'), ('191', 'Cuentas por cobrar comerciales - terceros'),
    ('1911', 'Estimación de cobranza dudosa'),
    ('20', 'MERCADERÍAS'), ('201', 'Mercaderías'), ('2011', 'Mercaderías'), ('20111', 'Mercaderías - costo'),
    ('21', 'PRODUCTOS TERMINADOS'), ('211', 'Productos manufacturados'), ('2111', 'Productos manufacturados'),
    ('23', 'PRODUCTOS EN PROCESO'), ('231', 'Productos en proceso de manufactura'),
    ('2311', 'Productos en proceso (semi elaborados)'),
    ('24', 'MATERIAS PRIMAS'), ('241', 'Materias primas para productos manufacturados'),
    ('2411', 'Materias primas para productos manufacturados'),
    ('25', 'MATERIALES AUXILIARES, SUMINISTROS Y REPUESTOS'), ('252', 'Suministros'), ('2521', 'Suministros'),
    ('28', 'INVENTARIOS POR RECIBIR'), ('281', 'Mercaderías'), ('2811', 'Mercaderías por recibir'),
    ('284', 'Materias primas'), ('2841', 'Materias primas por recibir'),
    ('285', 'Materiales auxiliares, suministros y repuestos'), ('2851', 'Suministros por recibir'),
    ('33', 'PROPIEDAD, PLANTA Y EQUIPO'),
    ('331', 'Terrenos'), ('3311', 'Terrenos'),
    ('332', 'Edificaciones'), ('3321', 'Edificaciones'),
    ('334', 'Unidades de transporte'), ('3341', 'Vehículos motorizados'),
    ('335', 'Muebles y enseres'), ('3351', 'Muebles'),
    ('336', 'Equipos diversos'), ('3361', 'Equipo para procesamiento de información'), ('3369', 'Otros equipos'),
    ('34', 'INTANGIBLES'), ('343', 'Programas de computadora (software)'), ('3431', 'Software'),
    ('39', 'DEPRECIACIÓN, AMORTIZACIÓN Y AGOTAMIENTO ACUMULADOS'),
    ('391', 'Depreciación acumulada'), ('3913', 'Propiedad, planta y equipo - costo'),
    ('40', 'TRIBUTOS, CONTRAPRESTACIONES Y APORTES AL SISTEMA DE PENSIONES Y DE SALUD POR PAGAR'),
    ('401', 'Gobierno central'), ('4011', 'Impuesto general a las ventas'),
    ('40111', 'IGV - Cuenta propia'), ('40113', 'IGV - Régimen de percepciones'),
    ('40114', 'IGV - Régimen de retenciones'),
    ('4017', 'Impuesto a la renta'), ('40171', 'Renta de tercera categoría'), ('40172', 'Renta de cuarta categoría'),
    ('40173', 'Renta de quinta categoría'),
    ('4018', 'Otros impuestos y contraprestaciones'), ('40189', 'Otros impuestos (ICBPER)'),
    ('403', 'Instituciones públicas'), ('4031', 'ESSALUD'), ('4032', 'ONP'),
    ('407', 'Administradoras de fondos de pensiones'), ('4071', 'AFP'),
    ('41', 'REMUNERACIONES Y PARTICIPACIONES POR PAGAR'),
    ('411', 'Remuneraciones por pagar'), ('4111', 'Sueldos y salarios por pagar'), ('4114', 'Gratificaciones por pagar'),
    ('4115', 'Vacaciones por pagar'),
    ('415', 'Beneficios sociales de los trabajadores por pagar'), ('4151', 'Compensación por tiempo de servicios'),
    ('42', 'CUENTAS POR PAGAR COMERCIALES - TERCEROS'),
    ('421', 'Facturas, boletas y otros comprobantes por pagar'), ('4212', 'Emitidas'),
    ('422', 'Anticipos a proveedores'), ('4221', 'Anticipos a proveedores'),
    ('423', 'Letras por pagar'), ('4231', 'Letras por pagar'),
    ('424', 'Honorarios por pagar'), ('4241', 'Honorarios por pagar'),
    ('44', 'CUENTAS POR PAGAR A LOS ACCIONISTAS, DIRECTORES Y GERENTES'),
    ('441', 'Accionistas'), ('4411', 'Préstamos de accionistas'),
    ('45', 'OBLIGACIONES FINANCIERAS'), ('451', 'Préstamos de instituciones financieras'),
    ('4511', 'Instituciones financieras'),
    ('46', 'CUENTAS POR PAGAR DIVERSAS - TERCEROS'), ('469', 'Otras cuentas por pagar diversas'),
    ('4699', 'Otras cuentas por pagar'),
    ('50', 'CAPITAL'), ('501', 'Capital social'), ('5011', 'Acciones'),
    ('58', 'RESERVAS'), ('582', 'Legal'), ('5821', 'Reserva legal'),
    ('59', 'RESULTADOS ACUMULADOS'), ('591', 'Utilidades no distribuidas'), ('5911', 'Utilidades acumuladas'),
    ('592', 'Pérdidas acumuladas'), ('5921', 'Pérdidas acumuladas'),
    ('60', 'COMPRAS'), ('601', 'Mercaderías'), ('6011', 'Mercaderías'),
    ('602', 'Materias primas'), ('6021', 'Materias primas para productos manufacturados'),
    ('603', 'Materiales auxiliares, suministros y repuestos'), ('6032', 'Suministros'),
    ('609', 'Costos vinculados con las compras'), ('6091', 'Costos vinculados con compras de mercaderías'),
    ('61', 'VARIACIÓN DE INVENTARIOS'), ('611', 'Mercaderías'), ('6111', 'Mercaderías'),
    ('612', 'Materias primas'), ('6121', 'Materias primas'),
    ('613', 'Materiales auxiliares, suministros y repuestos'), ('6132', 'Suministros'),
    ('62', 'GASTOS DE PERSONAL Y DIRECTORES'), ('621', 'Remuneraciones'), ('6211', 'Sueldos y salarios'),
    ('6214', 'Gratificaciones'), ('6215', 'Vacaciones'),
    ('627', 'Seguridad, previsión social y otras contribuciones'), ('6271', 'Régimen de prestaciones de salud (ESSALUD)'),
    ('629', 'Beneficios sociales de los trabajadores'), ('6291', 'Compensación por tiempo de servicios'),
    ('63', 'GASTOS DE SERVICIOS PRESTADOS POR TERCEROS'),
    ('631', 'Transporte, correos y gastos de viaje'), ('6311', 'Transporte'),
    ('632', 'Asesoría y consultoría'), ('6321', 'Administrativa'), ('6322', 'Legal y tributaria'),
    ('6323', 'Auditoría y contable'),
    ('634', 'Mantenimiento y reparaciones'), ('6343', 'Propiedad, planta y equipo'),
    ('635', 'Alquileres'), ('6352', 'Edificaciones'),
    ('636', 'Servicios básicos'), ('6361', 'Energía eléctrica'), ('6363', 'Agua'), ('6364', 'Teléfono'),
    ('6365', 'Internet'),
    ('637', 'Publicidad, publicaciones y relaciones públicas'), ('6371', 'Publicidad'),
    ('639', 'Otros servicios prestados por terceros'), ('6391', 'Gastos bancarios'), ('6399', 'Otros servicios'),
    ('64', 'GASTOS POR TRIBUTOS'), ('641', 'Gobierno central'), ('6412', 'Impuesto a las transacciones financieras'),
    ('643', 'Gobierno local'), ('6431', 'Impuesto predial'),
    ('65', 'OTROS GASTOS DE GESTIÓN'), ('651', 'Seguros'), ('6511', 'Seguros'),
    ('656', 'Suministros'), ('6561', 'Suministros (consumo interno)'),
    ('659', 'Otros gastos de gestión'), ('6591', 'Donaciones'), ('6599', 'Otros gastos de gestión'),
    ('67', 'GASTOS FINANCIEROS'), ('673', 'Intereses por préstamos y otras obligaciones'),
    ('6731', 'Préstamos de instituciones financieras'),
    ('676', 'Diferencia de cambio'), ('6761', 'Pérdida por diferencia de cambio'),
    ('68', 'VALUACIÓN Y DETERIORO DE ACTIVOS Y PROVISIONES'), ('681', 'Depreciación'),
    ('6814', 'Depreciación de propiedad, planta y equipo - costo'),
    ('69', 'COSTO DE VENTAS'), ('691', 'Mercaderías'), ('6911', 'Mercaderías'), ('69111', 'Mercaderías - terceros'),
    ('692', 'Productos terminados'), ('6921', 'Productos manufacturados'),
    ('70', 'VENTAS'), ('701', 'Mercaderías'), ('7011', 'Mercaderías'), ('70111', 'Mercaderías - terceros'),
    ('702', 'Productos terminados'), ('7021', 'Productos manufacturados'),
    ('704', 'Prestación de servicios'), ('7041', 'Prestación de servicios - terceros'),
    ('709', 'Devoluciones sobre ventas'), ('7091', 'Devoluciones sobre ventas'),
    ('75', 'OTROS INGRESOS DE GESTIÓN'), ('759', 'Otros ingresos de gestión'), ('7599', 'Otros ingresos de gestión'),
    ('77', 'INGRESOS FINANCIEROS'), ('772', 'Rendimientos ganados'), ('7721', 'Depósitos en instituciones financieras'),
    ('776', 'Diferencia de cambio'), ('7761', 'Ganancia por diferencia de cambio'),
    ('79', 'CARGAS IMPUTABLES A CUENTAS DE COSTOS Y GASTOS'),
    ('791', 'Cargas imputables a cuentas de costos y gastos'), ('7911', 'Cargas imputables a cuentas de costos y gastos'),
    ('88', 'IMPUESTO A LA RENTA'), ('881', 'Impuesto a la renta - corriente'), ('8811', 'Impuesto a la renta corriente'),
    ('89', 'DETERMINACIÓN DEL RESULTADO DEL EJERCICIO'), ('891', 'Utilidad'), ('8911', 'Utilidad del ejercicio'),
    ('892', 'Pérdida'), ('8921', 'Pérdida del ejercicio'),
    ('94', 'GASTOS ADMINISTRATIVOS'), ('941', 'Gastos administrativos'),
    ('95', 'GASTOS DE VENTAS'), ('951', 'Gastos de ventas'),
    ('97', 'GASTOS FINANCIEROS (DESTINO)'), ('971', 'Gastos financieros'),
]

# Cuentas de saldo acreedor dentro de clases normalmente deudoras (y viceversa)
ACREEDORAS = ('19', '39', '122', '4', '5', '7')
DEUDORAS_EXCEPCION = ('422', '709')


def naturaleza(codigo):
    if codigo.startswith(DEUDORAS_EXCEPCION):
        return 'DEUDORA'
    return 'ACREEDORA' if codigo.startswith(ACREEDORAS) else 'DEUDORA'


def destinos(codigo):
    """(destino_debe, destino_haber) para cuentas de gasto por naturaleza."""
    if codigo.startswith('6011'):
        # la compra queda "por recibir"; el ingreso al almacén (kardex) la pasa a 20111
        return '2811', '6111'
    if codigo.startswith('6021'):
        return '2841', '6121'
    if codigo.startswith('6032'):
        return '2851', '6132'
    if codigo.startswith(('62', '63', '64', '65', '68')):
        return '941', '7911'
    if codigo.startswith('67'):
        return '971', '7911'
    return None, None


# (clave, descripción, código por defecto)
DEFECTOS = [
    ('cliente', 'Clientes - facturas por cobrar', '1212'),
    ('ventas_bienes', 'Ventas de mercaderías', '70111'),
    ('ventas_servicios', 'Ventas de servicios', '7041'),
    ('igv', 'IGV cuenta propia (débito y crédito fiscal)', '40111'),
    ('icbper', 'Impuesto a las bolsas plásticas (ICBPER)', '40189'),
    ('igv_retencion', 'IGV - retenciones', '40114'),
    ('igv_percepcion', 'IGV - percepciones', '40113'),
    ('proveedor', 'Proveedores - facturas por pagar', '4212'),
    ('honorarios_por_pagar', 'Honorarios por pagar (recibos por honorarios)', '4241'),
    ('retencion_cuarta', 'Retención de renta de cuarta categoría', '40172'),
    ('compra_MERCADERIA', 'Compras: mercadería', '6011'),
    ('compra_GASTO', 'Compras: gasto', '6599'),
    ('compra_SERVICIO', 'Compras: servicio', '6399'),
    ('compra_HONORARIOS', 'Compras: honorarios', '6321'),
    ('compra_ACTIVO_FIJO', 'Compras: activo fijo', '3361'),
    ('costo_ventas', 'Costo de ventas (desde el kardex)', '69111'),
    ('mercaderias', 'Inventario de mercaderías', '20111'),
    ('caja', 'Caja (cuentas de caja sin cuenta asignada)', '1011'),
    ('bancos', 'Bancos (cuentas bancarias sin cuenta asignada)', '1041'),
    ('detracciones', 'Banco de la Nación - detracciones', '1042'),
    ('ingreso_otro', 'Tesorería: otros ingresos', '7599'),
    ('egreso_otro', 'Tesorería: otros egresos', '6599'),
    ('mov_GASTO_BANCARIO', 'Tesorería: gastos bancarios / ITF', '6391'),
    ('mov_SERVICIOS', 'Tesorería: servicios públicos', '6361'),
    ('mov_PLANILLA', 'Tesorería: planillas', '4111'),
    ('mov_TRIBUTOS', 'Tesorería: pago de tributos', '40111'),
    ('mov_PRESTAMO', 'Tesorería: préstamos', '4511'),
    ('mov_ANTICIPO_INGRESO', 'Tesorería: anticipos de clientes', '1221'),
    ('mov_ANTICIPO_EGRESO', 'Tesorería: anticipos a proveedores', '4221'),
    ('mov_CAJA_CHICA', 'Tesorería: caja chica / entregas a rendir', '1021'),
    ('mov_DEPOSITO', 'Tesorería: efectivo en tránsito', '1031'),
    ('mercaderia_por_recibir', 'Mercaderías compradas aún no ingresadas al almacén', '2811'),
    ('inventario_inicial', 'Inventario inicial (contrapartida patrimonial)', '5911'),
    ('inventario_sobrante', 'Sobrantes de inventario', '7599'),
    ('inventario_merma', 'Mermas, faltantes y consumo interno', '6599'),
    ('dif_cambio_perdida', 'Pérdida por diferencia de cambio', '6761'),
    ('dif_cambio_ganancia', 'Ganancia por diferencia de cambio', '7761'),
    ('apertura_patrimonio', 'Asiento de apertura: contrapartida de saldos iniciales', '5911'),
]


def cargar(CuentaContable, CuentaDefecto):
    """Crea las cuentas que falten y las cuentas por defecto. Acepta modelos reales o históricos."""
    codigos = [c for c, _ in PCGE]
    existentes = set(CuentaContable.objects.values_list('codigo', flat=True))
    for codigo, nombre in PCGE:
        if codigo in existentes:
            continue
        hoja = not any(o != codigo and o.startswith(codigo) for o in codigos)
        CuentaContable.objects.create(codigo=codigo, nombre=nombre, naturaleza=naturaleza(codigo), imputable=hoja)
    por_codigo = {c.codigo: c for c in CuentaContable.objects.all()}
    for codigo, _ in PCGE:
        debe, haber = destinos(codigo)
        cuenta = por_codigo[codigo]
        if debe and cuenta.imputable and not cuenta.destino_debe_id:
            cuenta.destino_debe, cuenta.destino_haber = por_codigo[debe], por_codigo[haber]
            cuenta.save(update_fields=['destino_debe', 'destino_haber'])
    for clave, descripcion, codigo in DEFECTOS:
        CuentaDefecto.objects.get_or_create(clave=clave, defaults={'descripcion': descripcion,
                                                                   'cuenta': por_codigo[codigo]})
