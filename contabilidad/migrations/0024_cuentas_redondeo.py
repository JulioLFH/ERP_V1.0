from django.db import migrations


def cargar(apps, schema_editor):
    """Cuentas por defecto de la diferencia de redondeo en facturas de proveedores (pérdida 6599 / ganancia 7599)."""
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0023_cuentas_envases'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
