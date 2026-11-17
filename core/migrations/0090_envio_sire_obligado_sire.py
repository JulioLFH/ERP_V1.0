import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """SIR-01 a SIR-03: «Obligado al SIRE» en la empresa (exporta los registros en el formato del SIRE en lugar del
    PLE) y los envíos al SIRE (reemplazo de la propuesta 14.4 / 8.4, no domiciliados 8.5 y ajustes posteriores)."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0089_plan_grupo_apagados'),
    ]

    operations = [
        migrations.AddField(
            model_name='empresa',
            name='obligado_sire',
            field=models.BooleanField(default=False, verbose_name='Obligado al SIRE',
                                      help_text='Lleva el Registro de Ventas (RVIE) y el de Compras (RCE) en el SIRE: '
                                                'los registros se exportan en el formato del SIRE en lugar del PLE'),
        ),
        migrations.CreateModel(
            name='EnvioSIRE',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('libro', models.CharField(choices=[('RVIE', 'Registro de ventas (RVIE)'),
                                                    ('RCE', 'Registro de compras (RCE)')], max_length=4)),
                ('registro', models.CharField(choices=[
                    ('140400', 'Registro de Ventas e Ingresos (RVIE) 14.4'), ('080400', 'Registro de Compras (RCE) 8.4'),
                    ('080500', 'Compras con sujetos no domiciliados (RCE) 8.5')], max_length=6)),
                ('periodo', models.CharField(max_length=6, verbose_name='Periodo (AAAAMM)')),
                ('oportunidad', models.CharField(choices=[
                    ('02', '02 Reemplazo de la propuesta'), ('03', '03 Ajuste posterior'),
                    ('04', '04 Ajuste posterior de periodos anteriores (general)'),
                    ('05', '05 Ajuste posterior de periodos anteriores (simplificado)')], default='02', max_length=2)),
                ('correlativo', models.PositiveSmallIntegerField(blank=True, null=True,
                                                                 verbose_name='Correlativo del ajuste')),
                ('ind_operaciones', models.CharField(default='1', max_length=1,
                                                     verbose_name='Indicador de operaciones')),
                ('ind_moneda', models.CharField(default='1', max_length=1, verbose_name='Moneda')),
                ('nombre_archivo', models.CharField(max_length=80)),
                ('archivo', models.BinaryField(blank=True, editable=False, null=True)),
                ('filas', models.PositiveIntegerField(default=0, verbose_name='Comprobantes')),
                ('estado', models.CharField(choices=[
                    ('GENERADO', 'Generado (sin enviar)'), ('ENVIADO', 'Enviado: SUNAT lo procesa'),
                    ('TERMINADO', 'Recibido por SUNAT'), ('OBSERVADO', 'Con inconsistencias'), ('ERROR', 'Error')],
                    default='GENERADO', max_length=10)),
                ('ticket', models.CharField(blank=True, max_length=20)),
                ('mensaje', models.TextField(blank=True)),
                ('inconsistencias', models.JSONField(blank=True, default=list)),
                ('archivo_respuesta', models.BinaryField(blank=True, editable=False, null=True)),
                ('nombre_respuesta', models.CharField(blank=True, max_length=200)),
                ('ticket_registro', models.CharField(blank=True, max_length=20,
                                                     verbose_name='Ticket del registro del preliminar')),
                ('registrado_en', models.DateTimeField(blank=True, null=True,
                                                       verbose_name='Preliminar registrado en SUNAT')),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('actualizado', models.DateTimeField(auto_now=True)),
                ('usuario', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                                              to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'envío al SIRE', 'verbose_name_plural': 'envíos al SIRE',
                     'ordering': ['-creado', '-id']},
        ),
    ]
