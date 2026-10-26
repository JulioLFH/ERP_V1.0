"""INV-32: productos marcados como EPP; los que ya se entregaron en SST quedan marcados."""
from django.db import migrations, models


def marcar_entregados(apps, schema_editor):
    Producto = apps.get_model('core', 'Producto')
    LineaEPP = apps.get_model('sst', 'LineaEPP')
    Producto.objects.filter(pk__in=LineaEPP.objects.values('producto_id')).update(es_epp=True)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0070_consumo_orden_v187'),
        ('sst', '0002_grupos'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='es_epp',
            field=models.BooleanField(default=False, help_text='INV-32: solo estos productos se entregan en SST › '
                                                               'Entrega de EPP',
                                      verbose_name='Es EPP (equipo de protección personal)'),
        ),
        migrations.RunPython(marcar_entregados, migrations.RunPython.noop),
    ]
