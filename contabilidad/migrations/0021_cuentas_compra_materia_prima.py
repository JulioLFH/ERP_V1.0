from django.db import migrations


def cargar(apps, schema_editor):
    """Cuentas por defecto de las compras de materia prima (602) y de suministros (603)."""
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0020_determinacion_cuenta'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
