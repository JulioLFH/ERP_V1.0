"""Tesorería se separa de Finanzas: quien tenía Finanzas conserva todo (recibe también el módulo Tesorería) y sus
permisos de cobros, pagos, transferencias, anulaciones y cuentas pasan también a Tesorería."""
from django.db import migrations

ACCIONES = ('registrar', 'transferir', 'anular', 'cuentas')


def _acciones(lista):
    nuevas = set(lista or [])
    for accion in ACCIONES:
        if f'finanzas.{accion}' in nuevas:
            nuevas.add(f'tesoreria.{accion}')
    nuevas.discard('finanzas.transferir')  # las transferencias son solo de Tesorería
    return sorted(nuevas)


def separar(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    tesoreria, _ = Group.objects.using(alias).get_or_create(name='Tesorería')
    finanzas = Group.objects.using(alias).filter(name='Finanzas').first()
    if finanzas is not None:
        tesoreria.user_set.add(*finanzas.user_set.all())
    for nombre in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', nombre).objects.using(alias).all():
            campos = []
            acciones = _acciones(perfil.acciones)
            if acciones != sorted(perfil.acciones or []):
                perfil.acciones = acciones
                campos.append('acciones')
            modulos = getattr(perfil, 'modulos', None)
            if modulos is not None and 'finanzas' in modulos and 'tesoreria' not in modulos:
                perfil.modulos = list(modulos) + ['tesoreria']
                campos.append('modulos')
            if campos:
                perfil.save(update_fields=campos)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0057_detraccion_producto_constancia'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [migrations.RunPython(separar, migrations.RunPython.noop)]
