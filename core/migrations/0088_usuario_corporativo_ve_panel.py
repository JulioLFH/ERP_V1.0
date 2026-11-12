from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0087_usuario_corporativo_modulos'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuariocorporativo',
            name='ve_panel',
            # db_default: la base pone True sola, así el código anterior sigue guardando mientras se actualiza
            field=models.BooleanField(db_default=True, default=True, verbose_name='Ve el Panel del grupo'),
        ),
    ]
