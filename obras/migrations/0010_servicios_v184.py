"""v1.84: entregables anulables y sin monto obligatorio (SRV-08, SRV-14), costo de horas distribuido al proyecto
(SRV-10) y centro de costo de los proyectos de servicios con nombre «Proyecto …» (SRV-12)."""
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


def renombrar_centros(apps, schema_editor):
    alias = schema_editor.connection.alias
    for obra in (apps.get_model('obras', 'Obra').objects.using(alias)
                 .filter(tipo='SERVICIO', centro_costo__isnull=False).select_related('centro_costo')):
        cc = obra.centro_costo
        if cc.nombre.startswith('Obra '):
            cc.nombre = ('Proyecto ' + cc.nombre[5:])[:100]
            cc.save(update_fields=['nombre'])


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0032_obras_ejecucion'),
        ('obras', '0009_servicios_por_proyecto'),
    ]

    operations = [
        migrations.AddField(
            model_name='partehoras',
            name='asiento_distribucion',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='contabilidad.asiento'),
        ),
        migrations.AlterField(
            model_name='entregableproyecto',
            name='estado',
            field=models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('ENTREGADO', 'Entregado al cliente'), ('OBSERVADO', 'Observado por el cliente'), ('CONFORME', 'Conforme (aprobado por el cliente)'), ('FACTURADO', 'Facturado'), ('ANULADO', 'Anulado')], default='PENDIENTE', editable=False, max_length=9),
        ),
        migrations.AlterField(
            model_name='entregableproyecto',
            name='monto',
            field=models.DecimalField(blank=True, decimal_places=2, default=Decimal('0'), help_text='Vacío o 0 = el % del monto contratado', max_digits=14, verbose_name='Monto sin IGV'),
        ),
        migrations.RunPython(renombrar_centros, migrations.RunPython.noop),
    ]
