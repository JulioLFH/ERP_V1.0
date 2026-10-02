"""Crea el almacén principal y le asigna el stock y kardex existentes."""
from django.db import migrations


def asignar_almacen(apps, schema_editor):
    Almacen = apps.get_model('core', 'Almacen')
    Producto = apps.get_model('core', 'Producto')
    StockAlmacen = apps.get_model('core', 'StockAlmacen')
    Kardex = apps.get_model('core', 'Kardex')
    Empresa = apps.get_model('core', 'Empresa')
    if not Producto.objects.exists() and not Kardex.objects.exists():
        return
    empresa = Empresa.objects.first()
    principal = Almacen.objects.filter(es_principal=True).first() or Almacen.objects.create(
        codigo='ALM01', nombre='Almacén principal', es_principal=True,
        direccion=empresa.direccion if empresa else '')
    for p in Producto.objects.filter(tipo='BIEN'):
        StockAlmacen.objects.get_or_create(producto=p, almacen=principal, defaults={'cantidad': p.stock})
    for k in Kardex.objects.filter(almacen__isnull=True).select_related('producto'):
        k.almacen = principal
        k.costo_promedio = k.producto.costo_promedio
        k.save(update_fields=['almacen', 'costo_promedio'])
    for modelo in (('compras', 'Compra'), ('ventas', 'Venta')):
        apps.get_model(*modelo).objects.filter(stock_aplicado=True, almacen__isnull=True).update(almacen=principal)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_almacen_facturacionconfig_tipocambio_and_more'),
        ('compras', '0002_compra_almacen'),
        ('ventas', '0002_venta_almacen_venta_cadena_qr_venta_codigo_hash_and_more'),
    ]
    operations = [migrations.RunPython(asignar_almacen, migrations.RunPython.noop)]
