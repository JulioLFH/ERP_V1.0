"""UIT 2026 = S/ 5,500 (la carga inicial dejó S/ 5,350, que es la de 2025). Solo se corrige si aún tiene el valor de
la carga inicial: si alguien ya la editó en Planillas › Configuración, se respeta."""
from decimal import Decimal

from django.db import migrations


def corregir(apps, schema_editor):
    Parametro = apps.get_model('planillas', 'Parametro')
    Parametro.objects.using(schema_editor.connection.alias).filter(anio=2026, uit=Decimal('5350')).update(
        uit=Decimal('5500'))


def revertir(apps, schema_editor):
    Parametro = apps.get_model('planillas', 'Parametro')
    Parametro.objects.using(schema_editor.connection.alias).filter(anio=2026, uit=Decimal('5500')).update(
        uit=Decimal('5350'))


class Migration(migrations.Migration):

    dependencies = [('planillas', '0006_asistencia_turnos')]

    operations = [migrations.RunPython(corregir, revertir)]
