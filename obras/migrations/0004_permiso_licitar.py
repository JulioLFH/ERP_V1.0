"""Quien crea obras y presupuestos (obras.presupuestar) puede también registrar licitaciones y propuestas
(obras.licitar). La venta de inmuebles (obras.vender) la asigna el administrador."""
from django.db import migrations


def dar_permiso(apps, schema_editor):
    alias = schema_editor.connection.alias
    for nombre in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', nombre).objects.using(alias).all():
            acciones = set(perfil.acciones or [])
            if 'obras.presupuestar' in acciones and 'obras.licitar' not in acciones:
                perfil.acciones = sorted(acciones | {'obras.licitar'})
                perfil.save(update_fields=['acciones'])


class Migration(migrations.Migration):
    dependencies = [
        ('obras', '0003_licitaciones_inmobiliario'),
        ('core', '0059_obras_s10'),
    ]
    operations = [migrations.RunPython(dar_permiso, migrations.RunPython.noop)]
