"""CON-17: los periodos inválidos («202613», «abcdef») que dejó Compras sin validar tumbaban Contabilidad. Se borran los
PeriodoContable sin asientos con un periodo inválido y las compras y ventas con periodo inválido vuelven al mes de su
emisión (las que estaban anuladas siguen anuladas)."""
from django.db import migrations


def valido(p):
    p = str(p or '')
    return len(p) == 6 and p.isdigit() and 1990 <= int(p[:4]) <= 2100 and 1 <= int(p[4:]) <= 12


def limpiar(apps, schema_editor):
    Periodo = apps.get_model('contabilidad', 'PeriodoContable')
    Asiento = apps.get_model('contabilidad', 'Asiento')
    for p in Periodo.objects.all():
        if not valido(p.periodo) and not Asiento.objects.filter(periodo=p.periodo).exists():
            p.delete()
    for modelo in (apps.get_model('compras', 'Compra'), apps.get_model('ventas', 'Venta')):
        for d in modelo.objects.exclude(periodo='').only('id', 'periodo', 'fecha_emision'):
            if not valido(d.periodo):
                modelo.objects.filter(pk=d.pk).update(periodo=d.fecha_emision.strftime('%Y%m'))


class Migration(migrations.Migration):
    dependencies = [
        ('contabilidad', '0032_obras_ejecucion'),
        ('compras', '0030_presupuesto_proyecto_v184'),
        ('ventas', '0023_finanzas_v184'),
    ]

    operations = [migrations.RunPython(limpiar, migrations.RunPython.noop)]
