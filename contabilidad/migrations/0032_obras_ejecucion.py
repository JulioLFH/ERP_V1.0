"""Cuentas de la ejecución de obras: fondo de garantía que retiene el cliente (1683; PCGE 2026 1699), fondo retenido
a los subcontratistas (467) y planilla de construcción civil (SCTR 6273, EsSalud, ONP, AFP y CONAFOVICER)."""
from django.db import migrations


def cargar(apps, schema_editor):
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0031_alter_reglareparto_base')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]