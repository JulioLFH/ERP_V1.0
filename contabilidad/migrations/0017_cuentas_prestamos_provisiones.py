"""v1.23: cuentas de leasing (32, 4521, 6732) y cuentas por defecto de préstamos, leasing y provisiones de
beneficios sociales."""
from django.db import migrations


def cargar(apps, schema_editor):
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0016_libros_paralelos_prestamos')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
