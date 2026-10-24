"""Cuentas de proyectos inmobiliarios (fase 2): inmuebles terminados (2141; PCGE 2026 2112), arras de retractación
(4699), penalidades retenidas (7599) y provisión de garantías postventa (6866 / 486; PCGE 2026 6896 / 486)."""
from django.db import migrations


def cargar(apps, schema_editor):
    from contabilidad import pcge
    pcge.cargar(apps.get_model('contabilidad', 'CuentaContable'), apps.get_model('contabilidad', 'CuentaDefecto'))


class Migration(migrations.Migration):
    dependencies = [('contabilidad', '0029_licitaciones_inmobiliario')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
