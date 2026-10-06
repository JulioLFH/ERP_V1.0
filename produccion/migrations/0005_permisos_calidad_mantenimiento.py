"""v1.20: quienes ya registran órdenes de producción reciben las nuevas acciones de calidad y mantenimiento."""
from django.db import migrations


def asignar(apps, schema_editor):
    Perfil = apps.get_model('core', 'PerfilUsuario')
    for p in Perfil.objects.all():
        acciones = list(p.acciones or [])
        if 'manufactura.ordenes' in acciones:
            nuevas = [a for a in ('manufactura.calidad', 'manufactura.mantenimiento') if a not in acciones]
            if nuevas:
                p.acciones = acciones + nuevas
                p.save(update_fields=['acciones'])


class Migration(migrations.Migration):
    dependencies = [
        ('produccion', '0004_ordenproduccion_compra_servicio_and_more'),
        ('core', '0027_almacen_tercero_alter_almacen_uso'),
    ]
    operations = [migrations.RunPython(asignar, migrations.RunPython.noop)]
