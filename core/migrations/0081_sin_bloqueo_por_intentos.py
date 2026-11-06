"""v1.105: sin bloqueo temporal por contraseñas fallidas (0 intentos = sin bloqueo). Las empresas existentes pasan a 0;
quien quiera el bloqueo lo vuelve a poner en Ajustes › Empresa › Seguridad."""
from django.db import migrations, models


def quitar_bloqueo(apps, schema_editor):
    alias = schema_editor.connection.alias
    apps.get_model('core', 'Empresa').objects.using(alias).update(intentos_bloqueo=0)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0080_producto_precio_compra'),
    ]

    operations = [
        migrations.AlterField(
            model_name='empresa',
            name='intentos_bloqueo',
            field=models.PositiveSmallIntegerField(default=0, help_text='0 = sin bloqueo temporal: una contraseña mal escrita no bloquea al usuario (los intentos igual quedan registrados)', verbose_name='Bloquear tras intentos fallidos'),
        ),
        migrations.RunPython(quitar_bloqueo, migrations.RunPython.noop),
    ]
