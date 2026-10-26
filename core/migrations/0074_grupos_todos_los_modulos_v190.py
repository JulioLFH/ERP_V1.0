"""HR-03: los grupos de los módulos se creaban recién al asignar el módulo a un usuario; ahora existen todos (Obras,
Manufactura, Calidad…) para elegirlos también desde los grupos de permisos."""
from django.db import migrations


def crear_grupos(apps, schema_editor):
    from core.modulos import GRUPOS
    Group = apps.get_model('auth', 'Group')
    base = schema_editor.connection.alias
    for nombre in sorted(set(GRUPOS.values())):
        Group.objects.using(base).get_or_create(name=nombre)


class Migration(migrations.Migration):
    dependencies = [('core', '0073_control_ip_v189')]
    operations = [migrations.RunPython(crear_grupos, migrations.RunPython.noop)]
