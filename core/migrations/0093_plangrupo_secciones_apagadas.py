from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0092_v1117'),
    ]

    operations = [
        migrations.AddField(
            model_name='plangrupo',
            name='secciones_apagadas',
            field=models.JSONField(blank=True, default=list, help_text='Pantallas del menú (como en permisos por pantalla) desactivadas en todas las empresas', null=True, verbose_name='Secciones apagadas por el grupo'),
        ),
    ]
