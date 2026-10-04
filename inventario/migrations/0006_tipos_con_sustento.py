"""v1.10: tipos de operación sin documento de origen exigen sustento (acta, informe) para confirmarse."""
from django.db import migrations, models

CON_SUSTENTO = ('SALDO_INI', 'AJ_ING', 'AJ_SAL', 'CONS_INT', 'CONS_MANT', 'SAL_DESTR', 'TRAS_DESTR')


def marcar(apps, schema_editor):
    apps.get_model('inventario', 'TipoOperacion').objects.filter(codigo__in=CON_SUSTENTO).update(
        requiere_sustento=True)


class Migration(migrations.Migration):
    dependencies = [('inventario', '0005_cierrekardex_saldocierre')]
    operations = [
        migrations.AddField(
            model_name='tipooperacion', name='requiere_sustento',
            field=models.BooleanField(default=False, help_text='No se confirma sin un documento adjunto (acta de '
                                      'inventario, de destrucción, informe, etc.)', verbose_name='Exige sustento')),
        migrations.RunPython(marcar, migrations.RunPython.noop),
    ]
