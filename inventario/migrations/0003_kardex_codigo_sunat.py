"""Código de la tabla 12 SUNAT para los movimientos de kardex registrados antes de la v1.5."""
from django.db import migrations


def completar(apps, schema_editor):
    from core.models import _codigo_sunat
    Kardex = apps.get_model('core', 'Kardex')
    for k in Kardex.objects.filter(codigo_sunat=''):
        cantidad = k.cantidad if k.tipo == 'ENTRADA' else -k.cantidad
        if k.referencia.startswith('Reversión') or k.referencia.startswith('Anulación'):
            cantidad = -cantidad  # la reversión lleva el código de la operación original
        k.codigo_sunat = _codigo_sunat(k.origen, k.concepto, cantidad)
        k.save(update_fields=['codigo_sunat'])


class Migration(migrations.Migration):
    dependencies = [
        ('inventario', '0002_tipos_operacion'),
        ('core', '0007_almacen_uso_kardex_codigo_sunat_and_more'),
    ]
    operations = [migrations.RunPython(completar, migrations.RunPython.noop)]
