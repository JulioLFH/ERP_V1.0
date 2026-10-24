"""PLA-01: el permiso que aprueba los movimientos de personal sin reglas tiene nombre propio («Aprobar movimientos de
personal»); quien cerraba planillas lo conserva."""
from django.db import migrations


def dar_permiso(apps, schema_editor):
    alias = schema_editor.connection.alias
    for nombre in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', nombre).objects.using(alias).all():
            acciones = set(perfil.acciones or [])
            if 'planillas.cerrar' in acciones and 'planillas.aprobar_movimientos' not in acciones:
                perfil.acciones = sorted(acciones | {'planillas.aprobar_movimientos'})
                perfil.save(update_fields=['acciones'])


class Migration(migrations.Migration):
    dependencies = [
        ('planillas', '0009_trabajador_categoria_civil_alter_trabajador_regimen'),
        ('core', '0061_preferencialista'),
    ]
    operations = [migrations.RunPython(dar_permiso, migrations.RunPython.noop)]