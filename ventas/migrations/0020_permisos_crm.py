"""CRM ampliado (v1.81): quien ya registraba oportunidades sigue viendo todas (antes no había visibilidad por nivel) y
puede registrar postventa. El administrador ajusta luego quién ve solo lo suyo (quitando «ver todas»)."""
from django.db import migrations


def _acciones(lista):
    nuevas = set(lista or [])
    if 'crm.registrar' in nuevas:
        nuevas |= {'crm.ver_todas', 'crm.postventa'}
    return sorted(nuevas)


def dar_permisos(apps, schema_editor):
    alias = schema_editor.connection.alias
    for nombre in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', nombre).objects.using(alias).all():
            acciones = _acciones(perfil.acciones)
            if acciones != sorted(perfil.acciones or []):
                perfil.acciones = acciones
                perfil.save(update_fields=['acciones'])


class Migration(migrations.Migration):
    dependencies = [('ventas', '0019_crm_ampliado'), ('core', '0063_modulo_crm')]
    operations = [migrations.RunPython(dar_permisos, migrations.RunPython.noop)]
