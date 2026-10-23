"""El libro (NIIF / tributario / ambos) de cada línea es copia del de su asiento: se corrigen las líneas que quedaron
con otro libro (en un libro el asiento salía descuadrado aunque cuadrara: validación V6 del estado de flujos)."""
from django.db import migrations


def alinear(apps, schema_editor):
    alias = schema_editor.connection.alias
    AsientoLinea = apps.get_model('contabilidad', 'AsientoLinea')
    for norma in ('AMBOS', 'NIIF', 'TRIBUTARIO'):
        AsientoLinea._base_manager.using(alias).filter(asiento__norma=norma).exclude(norma=norma).update(norma=norma)


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0027_reparto_gastos')]
    operations = [migrations.RunPython(alinear, migrations.RunPython.noop)]
