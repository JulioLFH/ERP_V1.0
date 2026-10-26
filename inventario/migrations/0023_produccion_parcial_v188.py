"""MAN-18: la declaración de producción ingresa el producto al costo estándar (Tabla 12: 19) y descuenta los materiales
de la receta como consumo de la orden (Tabla 12: 10)."""
from django.db import migrations

TIPOS = [('ENT_OP', 'Ingreso de producción (declaración de la orden)', 'INGRESO', '19', 'bi-box-arrow-in-down'),
         ('CONS_OP', 'Consumo de materiales de la orden (declaración)', 'SALIDA', '10', 'bi-box-arrow-up-right')]


def crear_tipos(apps, schema_editor):
    Tipo = apps.get_model('inventario', 'TipoOperacion')
    for n, (codigo, nombre, clase, sunat, icono) in enumerate(TIPOS):
        Tipo.objects.get_or_create(codigo=codigo, defaults={
            'nombre': nombre, 'clase': clase, 'origen': '', 'requiere_costo': False, 'cuenta_contable': None,
            'codigo_sunat': sunat, 'codigo_sunat_ingreso': '', 'icono': icono, 'orden': 910 + n})


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0022_consumo_orden_v187b'),
        ('produccion', '0026_produccion_parcial_v188'),
    ]

    operations = [migrations.RunPython(crear_tipos, migrations.RunPython.noop)]
