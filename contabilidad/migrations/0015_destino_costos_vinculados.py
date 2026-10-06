"""v1.20: la 6091 (costos vinculados: gastos de importación) va por destino a la 2811, como la 6011."""
from django.db import migrations


def cargar(apps, schema_editor):
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0014_cuentas_letras_rendiciones')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
