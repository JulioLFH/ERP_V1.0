"""Comprobantes que quedaron en "Error de envío" porque la facturación electrónica aún no estaba configurada:
no llegaron al OSE, pasan a "No enviado" para poder enviarlos ahora."""
from django.db import migrations


def corregir(apps, schema_editor):
    for app, modelo in (('ventas', 'Venta'), ('logistica', 'GuiaRemision')):
        apps.get_model(app, modelo).objects.filter(
            estado_sunat='ERROR', sunat_descripcion__icontains='no está configurada').update(
            estado_sunat='NO_ENVIADO',
            sunat_descripcion='No enviado: la facturación electrónica no estaba configurada. Envíelo ahora.')


class Migration(migrations.Migration):
    dependencies = [
        ('ventas', '0004_venta_anulado_en_venta_anulado_por_venta_base_pen_and_more'),
        ('logistica', '0002_guiaremision_anulado_en_guiaremision_anulado_por_and_more'),
    ]
    operations = [migrations.RunPython(corregir, migrations.RunPython.noop)]
