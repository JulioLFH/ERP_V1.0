"""Carga el Plan Contable General Empresarial, las cuentas por defecto y clasifica el kardex existente."""
from django.db import migrations


def cargar_pcge(apps, schema_editor):
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


def clasificar_kardex(apps, schema_editor):
    """El costo de ventas se calcula con las salidas del kardex originadas por ventas."""
    Kardex = apps.get_model('core', 'Kardex')
    if not Kardex.objects.filter(origen='').exists():
        return
    refs_venta = {f'{v.get_tipo_comprobante_display()} {v.serie}-{v.numero}'
                  for v in apps.get_model('ventas', 'Venta').objects.all()}
    refs_compra = {f'{c.get_tipo_comprobante_display()} {c.serie}-{c.numero}'
                   for c in apps.get_model('compras', 'Compra').objects.all()}
    for k in Kardex.objects.filter(origen=''):
        ref = k.referencia.removeprefix('Reversión ')
        if ref.startswith('Ajuste'):
            origen = 'AJUSTE'
        elif ref.startswith('Guía'):
            origen = 'GUIA'
        elif ref in refs_venta and ref not in refs_compra:
            origen = 'VENTA'
        elif ref in refs_compra and ref not in refs_venta:
            origen = 'COMPRA'
        else:
            continue
        k.origen = origen
        k.save(update_fields=['origen'])


class Migration(migrations.Migration):
    dependencies = [
        ('contabilidad', '0001_initial'),
        ('core', '0004_kardex_origen'),
        ('ventas', '0002_venta_almacen_venta_cadena_qr_venta_codigo_hash_and_more'),
        ('compras', '0003_compra_centro_costo_compra_cuenta_contable'),
    ]
    operations = [
        migrations.RunPython(cargar_pcge, migrations.RunPython.noop),
        migrations.RunPython(clasificar_kardex, migrations.RunPython.noop),
    ]
