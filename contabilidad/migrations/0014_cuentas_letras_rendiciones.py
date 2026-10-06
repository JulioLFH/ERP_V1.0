"""v1.20: letras (1231/4231), entregas a rendir (1413) y fondo de caja chica (1021) para la centralización."""
from django.db import migrations


def cargar(apps, schema_editor):
    from contabilidad import pcge
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    # PCGE: 1231 en cartera, 1232 en descuento (la versión anterior llamaba "en cartera" a la 1232)
    CuentaContable.objects.filter(codigo='1232', nombre='Letras en cartera').update(nombre='Letras en descuento')
    pcge.cargar(CuentaContable, apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0013_alter_asiento_origen')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
