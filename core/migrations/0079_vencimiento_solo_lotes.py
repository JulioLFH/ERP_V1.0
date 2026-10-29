"""AUD-10: «Lote con vencimiento obligatorio» solo aplica al control por lote; en los demás productos se apaga."""
from django.db import migrations


def apagar(apps, schema_editor):
    Producto = apps.get_model('core', 'Producto')
    Producto.objects.using(schema_editor.connection.alias).exclude(control='LOTE').filter(
        exige_vencimiento=True).update(exige_vencimiento=False)


class Migration(migrations.Migration):
    dependencies = [('core', '0078_celulares_app_v199')]
    operations = [migrations.RunPython(apagar, migrations.RunPython.noop)]
