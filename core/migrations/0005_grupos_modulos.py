"""Crea un grupo de permisos por módulo (Ventas, Compras, Inventario, ...)."""
from django.db import migrations

GRUPOS = ['Tablero', 'Ventas', 'Compras', 'Inventario', 'Logística', 'Finanzas', 'Contabilidad', 'Ajustes']


def crear_grupos(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for nombre in GRUPOS:
        Group.objects.get_or_create(name=nombre)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0004_kardex_origen'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [migrations.RunPython(crear_grupos, migrations.RunPython.noop)]
