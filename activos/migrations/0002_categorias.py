"""v1.12: cuentas de activos (3331, 392, 655, 682, 756) y categorías con las tasas máximas de SUNAT."""
from django.db import migrations

# código, nombre, cuenta activo, depreciación acumulada, gasto, deprecia, tasa %, vida útil en meses
CATEGORIAS = [
    ('TER', 'Terrenos', '3311', None, None, False, 0, 0),
    ('EDI', 'Edificaciones', '3321', '3913', '6814', True, 5, 240),
    ('MAQ', 'Maquinaria y equipo', '3331', '3913', '6814', True, 10, 120),
    ('VEH', 'Vehículos', '3341', '3913', '6814', True, 20, 60),
    ('MUE', 'Muebles y enseres', '3351', '3913', '6814', True, 10, 120),
    ('CPU', 'Equipos de cómputo', '3361', '3913', '6814', True, 25, 48),
    ('OTR', 'Otros equipos', '3369', '3913', '6814', True, 10, 120),
    ('SOF', 'Software (intangible)', '3431', '3921', '6821', True, 25, 48),
]


def cargar(apps, schema_editor):
    from contabilidad import pcge
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    pcge.cargar(CuentaContable, apps.get_model('contabilidad', 'CuentaDefecto'))
    cuentas = {c.codigo: c for c in CuentaContable.objects.all()}
    Categoria = apps.get_model('activos', 'CategoriaActivo')
    for codigo, nombre, activo, dep, gasto, deprecia, tasa, vida in CATEGORIAS:
        Categoria.objects.get_or_create(codigo=codigo, defaults={
            'nombre': nombre, 'cuenta_activo': cuentas[activo], 'cuenta_depreciacion': cuentas.get(dep),
            'cuenta_gasto': cuentas.get(gasto), 'deprecia': deprecia, 'tasa_anual': tasa, 'vida_util_meses': vida})


class Migration(migrations.Migration):
    dependencies = [('activos', '0001_initial'), ('contabilidad', '0008_alter_asiento_origen')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
