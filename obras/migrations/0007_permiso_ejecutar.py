"""Quien presupuesta o requiere materiales de la obra (obras.presupuestar / obras.requerir) registra también su
ejecución (obras.ejecutar: avance, valorizaciones, tareo, partes y subcontratos). Facturar adelantos y valorizaciones
(obras.facturar) lo asigna el administrador."""
from django.db import migrations


def dar_permiso(apps, schema_editor):
    alias = schema_editor.connection.alias
    for nombre in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', nombre).objects.using(alias).all():
            acciones = set(perfil.acciones or [])
            if acciones & {'obras.presupuestar', 'obras.requerir'} and 'obras.ejecutar' not in acciones:
                perfil.acciones = sorted(acciones | {'obras.ejecutar'})
                perfil.save(update_fields=['acciones'])


class Migration(migrations.Migration):
    dependencies = [
        ('obras', '0006_ejecucion_obra'),
        ('core', '0060_reglavalidacion_almacen_cerrado_and_more'),
    ]
    operations = [migrations.RunPython(dar_permiso, migrations.RunPython.noop)]