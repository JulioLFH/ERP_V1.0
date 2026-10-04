"""v1.6: cuentas de existencias por tipo de producto (21, 23, 24, 25, 284, 285, 602, 603, 612, 613, 692, 702),
tipo de producto de los productos existentes y su juego de cuentas."""
from django.db import migrations


def cuentas_y_productos(apps, schema_editor):
    from contabilidad import pcge
    from core.models import asignar_cuentas
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    pcge.cargar(CuentaContable, apps.get_model('contabilidad', 'CuentaDefecto'))
    for p in apps.get_model('core', 'Producto').objects.all():
        if p.tipo == 'SERVICIO':
            p.clase = 'SERVICIO'
        asignar_cuentas(p, CuentaContable)
        p.precio_compra = p.precio_compra or p.costo_promedio
        p.save()
    # periodos abiertos se recentralizan con las cuentas de cada producto
    apps.get_model('contabilidad', 'PeriodoContable').objects.filter(cerrado=False).update(pendiente=True)


class Migration(migrations.Migration):
    dependencies = [
        ('contabilidad', '0004_datos_v14'),
        ('core', '0008_empresa_fuente_tipo_cambio_empresa_token_tipo_cambio_and_more'),
    ]
    operations = [migrations.RunPython(cuentas_y_productos, migrations.RunPython.noop)]
