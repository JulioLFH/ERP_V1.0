"""Conceptos de planilla (código PLAME tabla 22 y cuenta contable), AFP y parámetros iniciales.

Las tasas de las AFP y los parámetros son referenciales: revíselos en Planillas › Configuración (las tasas las
publica la SBS; la RMV y la UIT cambian por decreto)."""
from decimal import Decimal

from django.db import migrations

CONCEPTOS = [
    # clave, nombre, tipo, código PLAME, cuenta, cuenta por pagar (aportes)
    ('BASICO', 'Remuneración básica', 'INGRESO', '0121', '6211', ''),
    ('ASIG_FAMILIAR', 'Asignación familiar', 'INGRESO', '0201', '6211', ''),
    ('HORAS_EXTRA_25', 'Horas extra 25%', 'INGRESO', '0105', '6211', ''),
    ('HORAS_EXTRA_35', 'Horas extra 35%', 'INGRESO', '0106', '6211', ''),
    ('VACACIONES', 'Remuneración vacacional', 'INGRESO', '0118', '6215', ''),
    ('OTROS_AFECTOS', 'Otros ingresos afectos', 'INGRESO', '', '6211', ''),
    ('NO_AFECTOS', 'Movilidad / ingresos no afectos', 'INGRESO', '0909', '6211', ''),
    ('GRATIFICACION', 'Gratificación (Ley 27735)', 'INGRESO', '0406', '6214', ''),
    ('BONIF_EXTRA', 'Bonificación extraordinaria (Ley 30334)', 'INGRESO', '0312', '6214', ''),
    ('CTS', 'Compensación por tiempo de servicios', 'INGRESO', '0904', '6291', ''),
    ('ONP', 'ONP - Sistema Nacional de Pensiones', 'DESCUENTO', '0607', '4032', ''),
    ('AFP_APORTE', 'AFP - aporte obligatorio', 'DESCUENTO', '0608', '4071', ''),
    ('AFP_PRIMA', 'AFP - prima de seguro', 'DESCUENTO', '0606', '4071', ''),
    ('AFP_COMISION', 'AFP - comisión', 'DESCUENTO', '0601', '4071', ''),
    ('QUINTA', 'Renta de quinta categoría', 'DESCUENTO', '0605', '40173', ''),
    ('ADELANTO', 'Adelantos', 'DESCUENTO', '0701', '1411', ''),
    ('OTROS_DESCUENTOS', 'Otros descuentos', 'DESCUENTO', '0706', '4699', ''),
    ('ESSALUD', 'EsSalud (empleador)', 'APORTE', '0804', '6271', '4031'),
]
AFPS = [  # código, nombre, prima %, comisión flujo %  (referenciales)
    ('HABITAT', 'AFP Habitat', '1.37', '1.47'),
    ('INTEGRA', 'AFP Integra', '1.37', '1.55'),
    ('PRIMA', 'Prima AFP', '1.37', '1.60'),
    ('PROFUTURO', 'Profuturo AFP', '1.37', '1.69'),
]


def cargar(apps, schema_editor):
    Concepto = apps.get_model('planillas', 'ConceptoPlanilla')
    Cuenta = apps.get_model('contabilidad', 'CuentaContable')
    AFP = apps.get_model('planillas', 'AFP')
    Parametro = apps.get_model('planillas', 'Parametro')
    for orden, (clave, nombre, tipo, plame, cuenta, pasivo) in enumerate(CONCEPTOS, 1):
        Concepto.objects.update_or_create(clave=clave, defaults={
            'nombre': nombre, 'tipo': tipo, 'codigo_plame': plame, 'orden': orden,
            'cuenta': Cuenta.objects.filter(codigo=cuenta).first(),
            'cuenta_pasivo': Cuenta.objects.filter(codigo=pasivo).first() if pasivo else None})
    for codigo, nombre, prima, flujo in AFPS:
        AFP.objects.get_or_create(codigo=codigo, defaults={'nombre': nombre, 'prima_pct': Decimal(prima),
                                                           'comision_flujo_pct': Decimal(flujo)})
    for anio in (2025, 2026):
        Parametro.objects.get_or_create(anio=anio, defaults={'rmv': Decimal('1130'), 'uit': Decimal('5350')})


class Migration(migrations.Migration):
    dependencies = [
        ('planillas', '0001_initial'),
        ('contabilidad', '0011_presupuesto_presupuestolinea'),
    ]

    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
