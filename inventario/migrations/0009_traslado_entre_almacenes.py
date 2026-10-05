"""v1.16: tipo de operación "Traslado entre almacenes" (también abastece a manufactura desde otros almacenes)."""
from django.db import migrations


def crear(apps, schema_editor):
    apps.get_model('inventario', 'TipoOperacion').objects.get_or_create(codigo='TRAS_ALM', defaults={
        'nombre': 'Traslado entre almacenes', 'clase': 'TRASLADO', 'origen': '', 'requiere_costo': False,
        'codigo_sunat': '11', 'codigo_sunat_ingreso': '21', 'icono': 'bi-arrow-left-right', 'orden': 115})


class Migration(migrations.Migration):
    dependencies = [('inventario', '0008_operacionitem_lote_operacionitem_vencimiento')]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
