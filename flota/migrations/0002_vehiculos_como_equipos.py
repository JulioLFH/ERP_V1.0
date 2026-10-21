from django.db import migrations


def vehiculos_como_equipos(apps, schema_editor):
    """Maestro único (FLO-01): cada vehículo de Logística pasa a ser un equipo de tipo vehículo, con su placa."""
    alias = schema_editor.connection.alias
    Equipo = apps.get_model('produccion', 'Equipo')
    for v in apps.get_model('logistica', 'Vehiculo').objects.using(alias).filter(equipo__isnull=True):
        eq = Equipo.objects.using(alias).filter(tipo='VEHICULO', placa=v.placa).first()
        if eq is None:
            base = f'VH-{v.placa.replace("-", "")}'[:20]
            codigo, n = base, 1
            while Equipo.objects.using(alias).filter(codigo=codigo).exists():
                n += 1
                codigo = f'{base}-{n}'[:20]
            eq = Equipo.objects.using(alias).create(
                codigo=codigo, nombre=f'{v.marca} {v.modelo} {v.placa}'.strip()[:120], tipo='VEHICULO',
                placa=v.placa, marca_modelo=f'{v.marca} {v.modelo}'.strip(), medidor='KM', activo=v.activo)
        v.equipo_id = eq.pk
        v.save(update_fields=['equipo'])


class Migration(migrations.Migration):

    dependencies = [
        ('flota', '0001_initial'),
        ('logistica', '0007_conductor_licencia_categoria_and_more'),
        ('produccion', '0018_equipo_anio_equipo_aplica_prestamo_and_more'),
    ]

    operations = [migrations.RunPython(vehiculos_como_equipos, migrations.RunPython.noop)]
