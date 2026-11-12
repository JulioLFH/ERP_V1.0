from django.db import migrations, models


class Migration(migrations.Migration):
    """Módulos que el administrador del grupo apaga dentro del plan de Ceiba (Panel del grupo). Admite nulo para que
    el código anterior siga guardando el plan mientras se actualiza."""

    dependencies = [
        ('core', '0088_usuario_corporativo_ve_panel'),
    ]

    operations = [
        migrations.AddField(
            model_name='plangrupo',
            name='apagados',
            field=models.JSONField(blank=True, default=list, null=True, verbose_name='Apagados por el grupo',
                                   help_text='Módulos del plan que el administrador del grupo desactivó'),
        ),
    ]
