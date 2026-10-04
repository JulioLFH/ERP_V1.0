"""v1.7: centro de costo para las órdenes existentes y días de crédito desde el proveedor."""
from django.db import migrations


def completar(apps, schema_editor):
    CentroCosto = apps.get_model('contabilidad', 'CentroCosto')
    OrdenCompra = apps.get_model('compras', 'OrdenCompra')
    sin_centro = OrdenCompra.objects.filter(centro_costo__isnull=True)
    if sin_centro.exists() or not CentroCosto.objects.exists():
        centro = CentroCosto.objects.filter(activo=True).first() or CentroCosto.objects.create(
            codigo='CC01', nombre='General')
        sin_centro.update(centro_costo=centro)
    for oc in OrdenCompra.objects.filter(dias_credito__isnull=True).select_related('tercero'):
        oc.dias_credito = oc.tercero.dias_credito
        oc.save(update_fields=['dias_credito'])


class Migration(migrations.Migration):
    dependencies = [
        ('compras', '0005_compra_fecha_ingreso_ordencompra_centro_costo_and_more'),
        ('contabilidad', '0005_cuentas_productos'),
    ]
    operations = [migrations.RunPython(completar, migrations.RunPython.noop)]
