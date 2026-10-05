"""v1.16: las recetas existentes pasan al nuevo esquema: estado según "vigente", sus operaciones a una hoja de ruta
(horas por lote -> ejecución por unidad) y una versión de fabricación por receta; las órdenes quedan con su versión
y las horas reales de las órdenes terminadas con su costo absorbido."""
from decimal import Decimal

from django.db import migrations


def migrar(apps, schema_editor):
    Lista = apps.get_model('produccion', 'ListaMateriales')
    Hoja = apps.get_model('produccion', 'HojaRuta')
    OperacionRuta = apps.get_model('produccion', 'OperacionRuta')
    Version = apps.get_model('produccion', 'VersionFabricacion')
    Orden = apps.get_model('produccion', 'OrdenProduccion')
    Hora = apps.get_model('produccion', 'HoraOrden')
    for lista in Lista.objects.all():
        lista.estado = 'APROBADA' if lista.activa else 'OBSOLETA'
        lista.vigente_desde = lista.creado.date()
        lista.save()
        hoja = None
        operaciones = list(apps.get_model('produccion', 'OperacionLista').objects.filter(lista=lista).order_by('id'))
        if operaciones:
            codigo = f'HR-{lista.producto.codigo}-{lista.codigo}'[:20]
            hoja = Hoja.objects.create(codigo=codigo, nombre=f'Ruta de {lista.producto.nombre} ({lista.codigo})'[:120],
                                       estado='APROBADA')
            base = lista.cantidad_base or Decimal('1')
            for n, o in enumerate(operaciones, 1):
                OperacionRuta.objects.create(hoja=hoja, secuencia=n * 10, centro_id=o.centro_id,
                                             descripcion=o.descripcion or f'Operación {n * 10}',
                                             horas_unidad=(o.horas / base).quantize(Decimal('0.0001')))
        version = Version.objects.create(producto_id=lista.producto_id, codigo=lista.codigo[:10], lista=lista,
                                         hoja=hoja, activa=lista.activa, vigente_desde=lista.vigente_desde)
        Orden.objects.filter(lista=lista).update(version=version)
    for h in Hora.objects.filter(orden__estado='TERMINADA').select_related('centro'):
        h.costo_mo = (h.horas_real * h.centro.costo_hora_mo).quantize(Decimal('0.01'))
        h.costo_cif = (h.horas_real * h.centro.costo_hora_cif).quantize(Decimal('0.01'))
        h.save(update_fields=['costo_mo', 'costo_cif'])


class Migration(migrations.Migration):
    dependencies = [('produccion', '0002_hojaruta_alter_centrotrabajo_options_and_more')]
    operations = [migrations.RunPython(migrar, migrations.RunPython.noop)]
