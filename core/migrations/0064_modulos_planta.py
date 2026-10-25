"""Planificación (MRP), Calidad y Mantenimiento se separan de Manufactura: quien tenía Manufactura conserva todo (recibe
los tres módulos) y sus permisos pasan a los módulos nuevos."""
from django.db import migrations

CAMBIOS = {'manufactura.calidad': 'control_calidad.inspecciones',
           'manufactura.liberar_lotes': 'control_calidad.liberar_lotes',
           'manufactura.mantenimiento': 'mantenimiento.registrar'}
GRUPOS = ('Planificación (MRP)', 'Calidad', 'Mantenimiento')
MODULOS = ('planificacion', 'control_calidad', 'mantenimiento')


def _acciones(lista):
    nuevas = set(lista or [])
    for antigua, nueva in CAMBIOS.items():
        if antigua in nuevas:
            nuevas.discard(antigua)
            nuevas.add(nueva)
    if 'manufactura.ordenes' in nuevas:  # quien corría el MRP lo sigue haciendo
        nuevas.add('planificacion.planificar')
    return sorted(nuevas)


def separar(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    manufactura = Group.objects.using(alias).filter(name='Manufactura').first()
    for nombre in GRUPOS:
        g, _ = Group.objects.using(alias).get_or_create(name=nombre)
        if manufactura is not None:
            g.user_set.add(*manufactura.user_set.all())
    for modelo in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', modelo).objects.using(alias).all():
            campos = []
            acciones = _acciones(perfil.acciones)
            if acciones != sorted(perfil.acciones or []):
                perfil.acciones = acciones
                campos.append('acciones')
            modulos = getattr(perfil, 'modulos', None)
            if modulos is not None and 'manufactura' in modulos:
                faltan = [m for m in MODULOS if m not in modulos]
                if faltan:
                    perfil.modulos = list(modulos) + faltan
                    campos.append('modulos')
            if campos:
                perfil.save(update_fields=campos)


class Migration(migrations.Migration):
    dependencies = [('core', '0063_modulo_crm'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(separar, migrations.RunPython.noop)]
