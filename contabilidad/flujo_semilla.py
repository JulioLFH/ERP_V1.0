"""Semilla del Estado de Flujos de Efectivo (NIC 7): rubros del método directo y reglas por cuenta de contrapartida
(docs/EFE_NIC7.md, tablas 1.1 y 2.2). La carga la migración de datos; se editan en Contabilidad › Configuración ›
Estado de flujos de efectivo."""

# (código, nombre, actividad, sentido, orden)
RUBROS = [
    ('OP-E01', 'Cobranza de ventas de bienes y prestación de servicios', 'OPERACION', 'ENTRADA', 10),
    ('OP-E02', 'Otros cobros de efectivo de operación', 'OPERACION', 'ENTRADA', 20),
    ('OP-E03', 'Intereses y rendimientos cobrados', 'OPERACION', 'ENTRADA', 30),
    ('OP-S01', 'Pagos a proveedores de bienes y servicios', 'OPERACION', 'SALIDA', 40),
    ('OP-S02', 'Pagos de gastos operativos y administrativos', 'OPERACION', 'SALIDA', 50),
    ('OP-S03', 'Pagos a y por cuenta de los empleados', 'OPERACION', 'SALIDA', 60),
    ('OP-S04', 'Pago de tributos', 'OPERACION', 'SALIDA', 70),
    ('OP-S05', 'Impuesto a las ganancias pagado', 'OPERACION', 'AMBOS', 80),
    ('OP-S06', 'Otros pagos de operación', 'OPERACION', 'SALIDA', 90),
    ('OP-S07', 'Intereses pagados', 'OPERACION', 'SALIDA', 100),
    ('OP-S08', 'Dividendos pagados', 'OPERACION', 'SALIDA', 110),
    ('IN-E01', 'Venta de propiedad, planta y equipo e intangibles', 'INVERSION', 'ENTRADA', 210),
    ('IN-E02', 'Cobranza de préstamos otorgados', 'INVERSION', 'ENTRADA', 220),
    ('IN-E03', 'Venta de inversiones financieras', 'INVERSION', 'ENTRADA', 230),
    ('IN-E04', 'Intereses y dividendos recibidos', 'INVERSION', 'ENTRADA', 240),
    ('IN-S01', 'Compra de propiedad, planta y equipo', 'INVERSION', 'SALIDA', 250),
    ('IN-S02', 'Compra de intangibles', 'INVERSION', 'SALIDA', 260),
    ('IN-S03', 'Préstamos otorgados a terceros y vinculadas', 'INVERSION', 'SALIDA', 270),
    ('IN-S04', 'Compra de inversiones financieras', 'INVERSION', 'SALIDA', 280),
    ('FI-E01', 'Aportes de capital en efectivo', 'FINANCIAMIENTO', 'ENTRADA', 310),
    ('FI-E02', 'Préstamos bancarios recibidos y obligaciones emitidas', 'FINANCIAMIENTO', 'ENTRADA', 320),
    ('FI-E03', 'Préstamos recibidos de accionistas y relacionadas', 'FINANCIAMIENTO', 'ENTRADA', 330),
    ('FI-S01', 'Amortización de préstamos', 'FINANCIAMIENTO', 'SALIDA', 340),
    ('FI-S02', 'Pago de cuotas de arrendamiento financiero (leasing)', 'FINANCIAMIENTO', 'SALIDA', 350),
    ('FI-S03', 'Dividendos pagados', 'FINANCIAMIENTO', 'SALIDA', 360),
    ('FI-S04', 'Intereses pagados', 'FINANCIAMIENTO', 'SALIDA', 370),
    ('FI-S05', 'Devolución de capital y recompra de acciones', 'FINANCIAMIENTO', 'SALIDA', 380),
]

# (prefijo PCGE, rubro si entra efectivo, rubro si sale); gana el prefijo más largo
REGLAS = [
    ('10', 'OP-E02', 'OP-S06'),  # cajas y bancos fuera del efectivo (detracciones, si la política las excluye)
    ('11', 'IN-E03', 'IN-S04'),
    ('12', 'OP-E01', 'OP-E01'),
    ('13', 'OP-E01', 'OP-E01'),
    ('14', 'OP-E02', 'OP-S03'),
    ('1421', 'FI-E01', 'FI-S05'),
    ('1422', 'IN-E02', 'IN-S03'),
    ('16', 'OP-E02', 'OP-S06'),
    ('165', 'IN-E01', 'IN-S01'),
    ('168', 'IN-E02', 'IN-S03'),
    ('17', 'IN-E02', 'IN-S03'),
    ('18', 'OP-E02', 'OP-S02'),
    ('19', 'OP-E01', 'OP-E01'),
    ('2', 'OP-E02', 'OP-S01'),
    ('30', 'IN-E03', 'IN-S04'),
    ('31', 'IN-E01', 'IN-S01'),
    ('32', 'IN-E01', 'IN-S01'),
    ('33', 'IN-E01', 'IN-S01'),
    ('34', 'IN-E01', 'IN-S02'),
    ('35', 'IN-E01', 'IN-S01'),
    ('36', 'IN-E01', 'IN-S01'),
    ('37', 'OP-E02', 'OP-S06'),
    ('38', 'OP-E02', 'OP-S06'),
    ('39', 'IN-E01', 'IN-S01'),
    ('40', 'OP-E02', 'OP-S04'),
    ('4017', 'OP-S05', 'OP-S05'),
    ('41', 'OP-E02', 'OP-S03'),
    ('407', 'OP-E02', 'OP-S03'),
    ('42', 'OP-S01', 'OP-S01'),
    ('43', 'OP-S01', 'OP-S01'),
    ('44', 'FI-E03', 'FI-S01'),
    ('4412', 'FI-E03', 'FI-S03'),
    ('45', 'FI-E02', 'FI-S01'),
    ('452', 'FI-E02', 'FI-S02'),
    ('455', 'FI-E02', 'FI-S04'),
    ('46', 'OP-E02', 'OP-S02'),
    ('465', 'IN-E01', 'IN-S01'),
    ('47', 'FI-E03', 'FI-S01'),
    ('48', 'OP-E02', 'OP-S06'),
    ('49', 'OP-E02', 'OP-S06'),
    ('5', 'FI-E01', 'FI-S05'),
    ('59', 'FI-E01', 'FI-S03'),
    ('6', 'OP-E02', 'OP-S02'),
    ('62', 'OP-E02', 'OP-S03'),
    ('639', 'OP-E02', 'OP-S06'),
    ('641', 'OP-E02', 'OP-S06'),
    ('66', 'OP-E02', 'OP-S06'),
    ('67', 'OP-E02', 'OP-S06'),
    ('673', 'OP-E02', 'FI-S04'),
    ('7', 'OP-E02', 'OP-S06'),
    ('70', 'OP-E01', 'OP-E01'),
    ('756', 'IN-E01', 'IN-S01'),
    ('772', 'IN-E04', 'OP-S06'),
    ('773', 'IN-E04', 'OP-S06'),
    ('88', 'OP-S05', 'OP-S05'),
    ('9', 'OP-E02', 'OP-S02'),
]

# respaldo cuando ninguna cuenta tiene regla: por el concepto del movimiento de Finanzas (entrada, salida)
POR_CONCEPTO = {
    'COBRANZA': ('OP-E01', 'OP-E01'), 'PAGO': ('OP-S01', 'OP-S01'), 'DETRACCION': ('OP-E01', 'OP-S01'),
    'ANTICIPO': ('OP-E01', 'OP-S01'), 'PLANILLA': ('OP-E02', 'OP-S03'), 'TRIBUTOS': ('OP-E02', 'OP-S04'),
    'SERVICIOS': ('OP-E02', 'OP-S02'), 'GASTO_BANCARIO': ('OP-E02', 'OP-S06'), 'CAJA_CHICA': ('OP-E02', 'OP-S02'),
    'PRESTAMO': ('FI-E02', 'FI-S01'), 'DEPOSITO': ('OP-E02', 'OP-S06'), 'OTRO': ('OP-E02', 'OP-S06'),
}


def cargar(RubroFlujo, ReglaFlujoCuenta, PoliticaFlujo):
    """Crea lo que falte (no pisa lo que el usuario cambió)."""
    rubros = {}
    for codigo, nombre, actividad, sentido, orden in RUBROS:
        rubros[codigo], _ = RubroFlujo.objects.get_or_create(
            codigo=codigo, defaults={'nombre': nombre, 'actividad': actividad, 'sentido': sentido, 'orden': orden})
    for prefijo, entrada, salida in REGLAS:
        ReglaFlujoCuenta.objects.get_or_create(prefijo=prefijo, defaults={
            'rubro_entrada': rubros.get(entrada), 'rubro_salida': rubros.get(salida)})
    if not PoliticaFlujo.objects.exists():
        PoliticaFlujo.objects.create()
