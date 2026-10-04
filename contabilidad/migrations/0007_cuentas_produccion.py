"""v1.11: cuentas 71 Variación de la producción almacenada (manufactura) y recentralización de periodos abiertos."""
from django.db import migrations


def cuentas_produccion(apps, schema_editor):
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))
    apps.get_model('contabilidad', 'PeriodoContable').objects.filter(cerrado=False).update(pendiente=True)


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0006_asiento_creado_por_asiento_extorna')]
    operations = [migrations.RunPython(cuentas_produccion, migrations.RunPython.noop)]
