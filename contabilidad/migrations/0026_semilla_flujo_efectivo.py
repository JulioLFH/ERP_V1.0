# Semilla del Estado de Flujos de Efectivo (NIC 7): rubros, reglas por cuenta y políticas por defecto.

from django.db import migrations


def cargar(apps, schema_editor):
    from contabilidad.flujo_semilla import cargar as cargar_semilla
    db = schema_editor.connection.alias
    modelos = [apps.get_model('contabilidad', n) for n in ('RubroFlujo', 'ReglaFlujoCuenta', 'PoliticaFlujo')]

    class EnBase:  # los modelos históricos, en la base de la empresa que se migra
        def __init__(self, modelo):
            self.modelo, self.objects = modelo, modelo.objects.using(db)

    cargar_semilla(*(EnBase(m) for m in modelos))


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0025_flujo_efectivo'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
