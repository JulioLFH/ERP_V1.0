from django.db import migrations


def cargar(apps, schema_editor):
    """Cuentas de envases y embalajes (26, 2861, 604, 614) y la cuenta por defecto de sus compras."""
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0022_solicitud_pedido'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
