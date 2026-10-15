"""Traslada los sustentos guardados en la base de datos al almacenamiento de objetos (S3 compatible).

Requiere las variables OBJETOS_* (core/almacen_objetos.py). Cada archivo se sube, se comprueba que se puede leer y
recién entonces se libera su copia en la base de datos."""
from django.core.management.base import BaseCommand, CommandError

from core import almacen_objetos
from core.models import ArchivoSustento


class Command(BaseCommand):
    help = 'Mueve los sustentos de la base de datos al almacenamiento de objetos.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=0, help='Máximo de archivos (0 = todos)')

    def handle(self, *args, **opts):
        if not almacen_objetos.configurado():
            raise CommandError('Configure OBJETOS_BUCKET, OBJETOS_ACCESS_KEY y OBJETOS_SECRET_KEY.')
        qs = ArchivoSustento.objects.filter(ubicacion='').order_by('pk').values_list('pk', flat=True)
        if opts['limite']:
            qs = qs[:opts['limite']]
        movidos = 0
        for pk in list(qs):
            a = ArchivoSustento.objects.get(pk=pk)
            datos = bytes(a.datos or b'')
            clave = almacen_objetos.guardar(datos, a.nombre, a.tipo)
            if almacen_objetos.leer(clave) != datos:
                raise CommandError(f'El archivo {a.pk} no se leyó igual del almacenamiento: se detiene.')
            ArchivoSustento.objects.filter(pk=pk).update(ubicacion=clave, datos=b'')
            movidos += 1
        self.stdout.write(f'{movidos} archivo(s) movidos al almacenamiento de objetos.')
