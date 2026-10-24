"""Grupos de los módulos nuevos (Seguridad y salud, Excelencia organizacional) y el tipo de operación de inventario
para la entrega de EPP."""
from django.db import migrations


def crear(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    for nombre in ('Seguridad y salud', 'Excelencia organizacional'):
        Group.objects.using(alias).get_or_create(name=nombre)
    TipoOperacion = apps.get_model('inventario', 'TipoOperacion')
    base = TipoOperacion.objects.using(alias).filter(codigo__in=['CONS_REQ', 'CONS_INT']).order_by('codigo').first()
    TipoOperacion.objects.using(alias).get_or_create(codigo='EPP', defaults={
        'nombre': 'Entrega de EPP al trabajador', 'clase': 'SALIDA', 'origen': '', 'requiere_costo': False,
        'cuenta_contable_id': base.cuenta_contable_id if base else None, 'codigo_sunat': '99',
        'icono': 'bi-shield-check', 'orden': 86})


class Migration(migrations.Migration):
    dependencies = [('sst', '0001_initial'), ('auth', '0012_alter_user_first_name_max_length'),
                    ('inventario', '0017_foto_por_kardex')]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
