"""MAN-16: las órdenes en borrador o confirmadas con una cantidad imposible (más de 99,999,999) que se grabaron antes del
tope no se podían abrir (error 500): quedan anuladas con una nota, sin mover almacén (aún no producían)."""
from django.db import migrations

MAXIMO_ORDEN = 99_999_999


def anular(apps, schema_editor):
    Orden = apps.get_model('produccion', 'OrdenProduccion')
    for o in Orden.objects.filter(cantidad__gt=MAXIMO_ORDEN, estado__in=['BORRADOR', 'CONFIRMADA']):
        o.estado = 'ANULADA'
        o.glosa = (f'{o.glosa} · Anulada por v1.88.0: cantidad {o.cantidad:,.0f} mayor al máximo por orden'
                   .strip(' ·'))[:500]
        o.save(update_fields=['estado', 'glosa'])


class Migration(migrations.Migration):
    dependencies = [('produccion', '0024_consumo_orden_v187')]

    operations = [migrations.RunPython(anular, migrations.RunPython.noop)]
