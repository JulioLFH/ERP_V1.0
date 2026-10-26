"""SEG-08: «Ver sueldos» ya no se deduce de «calcular» ni de «cerrar planillas» (el primero es implícito para todo el
grupo Planillas). Quien tenía «calcular» o «cerrar» marcado a mano en su perfil (o en un perfil de acceso) es de RR. HH.:
recibe «Ver sueldos» para no perder lo que veía. Los usuarios sin perfil lo reciben del administrador."""
from django.db import migrations

VER, CALCULAR, CERRAR = 'planillas.ver_sueldos', 'planillas.calcular', 'planillas.cerrar'


def otorgar(apps, schema_editor):
    for nombre in ('PerfilUsuario', 'PerfilAcceso'):
        for p in apps.get_model('core', nombre).objects.all():
            acciones = list(p.acciones or [])
            if (CALCULAR in acciones or CERRAR in acciones) and VER not in acciones:
                p.acciones = acciones + [VER]
                p.save(update_fields=['acciones'])


class Migration(migrations.Migration):
    dependencies = [('core', '0071_producto_es_epp_v188')]

    operations = [migrations.RunPython(otorgar, migrations.RunPython.noop)]
