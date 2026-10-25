"""MNT-09: las órdenes de mantenimiento cerradas antes de la validación con horas o costos negativos quedan en cero, con
la nota del valor original en el trabajo realizado."""
from decimal import Decimal

from django.db import migrations
from django.db.models import F, Q
from django.utils import timezone


def corregir(apps, schema_editor):
    Orden = apps.get_model('produccion', 'OrdenMantenimiento')
    for o in Orden.objects.filter(Q(horas_parada__lt=0) | Q(costo_mano_obra__lt=0) | Q(costo_servicios__lt=0) |
                                  Q(fin_real__lt=F('inicio_real'))):
        cambios = []
        for campo, nombre in (('horas_parada', 'Horas de parada'), ('costo_mano_obra', 'Mano de obra S/'),
                              ('costo_servicios', 'Servicios externos S/')):
            valor = getattr(o, campo)
            if valor < 0:
                cambios.append(f'{nombre} {valor} → 0.00')
                setattr(o, campo, Decimal('0'))
        if o.inicio_real and o.fin_real and o.fin_real < o.inicio_real:
            cambios.append(f'Fin real {timezone.localtime(o.fin_real):%d/%m/%Y %H:%M} → vacío (era anterior al inicio)')
            o.fin_real = None
        if cambios:
            nota = f'[Corrección de datos v1.85.0: {"; ".join(cambios)}. Revise y complete la orden.]'
            o.trabajo_realizado = f'{o.trabajo_realizado}\n{nota}'.strip()
            o.save(update_fields=['horas_parada', 'costo_mano_obra', 'costo_servicios', 'fin_real',
                                  'trabajo_realizado'])


class Migration(migrations.Migration):
    dependencies = [('produccion', '0021_calidad_farma_v184')]
    operations = [migrations.RunPython(corregir, migrations.RunPython.noop)]
