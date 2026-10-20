from django.db import migrations


def corregir(apps, schema_editor):
    """Los botones XML y CDR de los comprobantes enviados directo a SUNAT apuntaban al id del archivo en vez del id
    del sustento: se rehacen con el vínculo correcto y como descarga."""
    alias = schema_editor.connection.alias
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Sustento = apps.get_model('core', 'Sustento')
    Venta = apps.get_model('ventas', 'Venta')
    ct = ContentType.objects.using(alias).filter(app_label='ventas', model='venta').first()
    if ct is None:
        return
    for s in Sustento.objects.using(alias).filter(content_type=ct, descripcion__startswith='XML firmado enviado a SUNAT'):
        Venta.objects.using(alias).filter(pk=s.object_id).update(enlace_xml=f'/sustentos/{s.pk}/?descargar=1')
    for s in Sustento.objects.using(alias).filter(content_type=ct, descripcion__startswith='CDR de SUNAT'):
        Venta.objects.using(alias).filter(pk=s.object_id).update(enlace_cdr=f'/sustentos/{s.pk}/?descargar=1')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0043_sunat_directo'),
        ('ventas', '0014_orden_de_lineas'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(corregir, migrations.RunPython.noop),
    ]
