from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0034_incidencias'),
    ]

    operations = [
        migrations.AddField(
            model_name='empresa',
            name='plan_contable',
            field=models.CharField(choices=[('PCGE2019', 'PCGE modificado 2019'), ('PCGE2026', 'PCGE 2026')],
                                   default='PCGE2019', editable=False, max_length=8, verbose_name='Plan contable'),
        ),
    ]
