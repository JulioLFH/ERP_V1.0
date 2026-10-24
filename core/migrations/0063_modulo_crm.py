"""El CRM se separa de Ventas: quien tenía Ventas conserva el CRM (recibe también el módulo CRM) y quien podía emitir
(el permiso que antes registraba oportunidades) recibe «crm.registrar»."""
from django.db import migrations


def _acciones(lista):
    nuevas = set(lista or [])
    if 'ventas.emitir' in nuevas:
        nuevas.add('crm.registrar')
    return sorted(nuevas)


def separar(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    crm, _ = Group.objects.using(alias).get_or_create(name='CRM')
    ventas = Group.objects.using(alias).filter(name='Ventas').first()
    if ventas is not None:
        crm.user_set.add(*ventas.user_set.all())
    for nombre in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', nombre).objects.using(alias).all():
            campos = []
            acciones = _acciones(perfil.acciones)
            if acciones != sorted(perfil.acciones or []):
                perfil.acciones = acciones
                campos.append('acciones')
            modulos = getattr(perfil, 'modulos', None)
            if modulos is not None and 'ventas' in modulos and 'crm' not in modulos:
                perfil.modulos = list(modulos) + ['crm']
                campos.append('modulos')
            if campos:
                perfil.save(update_fields=campos)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0062_reglaaprobacion_reemplazo'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [migrations.RunPython(separar, migrations.RunPython.noop)]
