"""v1.117.2: columnas del balance de comprobación como en la hoja de trabajo del PCGE. Las compras y su variación
(60/61) van solo por naturaleza y el costo de ventas (69) solo por función (llega a la naturaleza cancelado contra la
variación de existencias). Solo cambian las cuentas que tenían la columna por defecto anterior («Resultados por
naturaleza y por función»); las que se configuraron de otra forma quedan como están."""
from django.db import migrations


def nuevas_columnas(apps, schema_editor):
    Cuenta = apps.get_model('contabilidad', 'CuentaContable')
    cuentas = Cuenta.objects.using(schema_editor.connection.alias).filter(columna_balance='RESULTADO')
    for prefijo, columna in (('60', 'NATURALEZA'), ('61', 'NATURALEZA'), ('69', 'FUNCION')):
        cuentas.filter(codigo__startswith=prefijo).update(columna_balance=columna)


def columnas_anteriores(apps, schema_editor):
    Cuenta = apps.get_model('contabilidad', 'CuentaContable')
    cuentas = Cuenta.objects.using(schema_editor.connection.alias)
    for prefijo, columna in (('60', 'NATURALEZA'), ('61', 'NATURALEZA'), ('69', 'FUNCION')):
        cuentas.filter(codigo__startswith=prefijo, columna_balance=columna).update(columna_balance='RESULTADO')


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0035_vigencia_centro_costo_v1102'),
    ]

    operations = [
        migrations.RunPython(nuevas_columnas, columnas_anteriores),
    ]
