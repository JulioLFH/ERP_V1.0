"""v1.16: tipo de operación para atender requerimientos internos (salida al gasto del área)."""
from django.db import migrations


def crear(apps, schema_editor):
    cuenta = apps.get_model('contabilidad', 'CuentaContable').objects.filter(codigo='6561').first()
    apps.get_model('inventario', 'TipoOperacion').objects.get_or_create(codigo='CONS_REQ', defaults={
        'nombre': 'Atención de requerimiento interno', 'clase': 'SALIDA', 'origen': '', 'requiere_costo': False,
        'cuenta_contable': cuenta, 'codigo_sunat': '99', 'icono': 'bi-clipboard-check', 'orden': 85})


class Migration(migrations.Migration):
    dependencies = [('inventario', '0010_operacion_centro_costo_operacion_cuenta_gasto_and_more'),
                    ('contabilidad', '0010_cuenta_costo_produccion')]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
