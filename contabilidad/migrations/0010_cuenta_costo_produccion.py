"""v1.16: cuenta 90 Costo de producción (destino de los gastos de centros de producción) y recentralización."""
from django.db import migrations


def cargar(apps, schema_editor):
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))
    apps.get_model('contabilidad', 'PeriodoContable').objects.filter(cerrado=False).update(pendiente=True)


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0009_centrobeneficio_centrocosto_padre_and_more')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
