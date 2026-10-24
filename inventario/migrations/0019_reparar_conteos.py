"""INV-17: conteos con una cantidad imposible (error de digitación, ej. 99999999999999) que ya no se podían abrir: la
cantidad se borra (se vuelve a contar) y el documento vuelve a «En conteo»."""
from django.db import migrations

LIMITE = '999999999.99'


def reparar(apps, schema_editor):
    with schema_editor.connection.cursor() as c:
        c.execute(f'UPDATE inventario_conteoitem SET contado = NULL, contado_por_id = NULL, contado_en = NULL '
                  f'WHERE contado > {LIMITE} OR contado < 0')
        c.execute(f'UPDATE inventario_conteoitem SET reconteo = NULL, reconteo_por_id = NULL, reconteo_en = NULL '
                  f'WHERE reconteo > {LIMITE} OR reconteo < 0')
        c.execute("UPDATE inventario_conteociclico SET estado = 'ABIERTO' WHERE estado = 'CONTADO' AND id IN "
                  "(SELECT conteo_id FROM inventario_conteoitem WHERE contado IS NULL)")


class Migration(migrations.Migration):
    dependencies = [('inventario', '0018_solicitudtraslado')]
    operations = [migrations.RunPython(reparar, migrations.RunPython.noop)]