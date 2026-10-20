"""Chequea el servicio y guarda el resultado para la página /estado/. Programarlo cada 5 minutos (Programador de
tareas de Windows o cron). Con --avisar, si hay una caída se avisa por correo a los administradores."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Chequea bases de datos, disco, respaldo, correo y facturación, y guarda el resultado (página /estado/).'

    def add_arguments(self, parser):
        parser.add_argument('--avisar', action='store_true', help='Avisa por correo a los administradores si hay '
                                                                  'una caída')

    def handle(self, *args, **opciones):
        from core import estado_servicio as es
        chequeo = es.registrar(minimo_minutos=0)
        _, _, estados, detalles = es.chequear()
        for k, v in estados.items():
            self.stdout.write(f'{es.NOMBRES[k]}: {v} ({detalles[k]})')
        if not chequeo.ok and opciones['avisar']:
            from django.contrib.auth import get_user_model

            from core import correo
            destinos = list(get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(email='')
                            .values_list('email', flat=True))
            try:
                correo.enviar(destinos, 'Caída del servicio', 'core/correo_caida.html', {'detalles': detalles})
            except correo.ErrorCorreo as exc:
                self.stderr.write(f'No se pudo avisar por correo: {exc}')
        self.stdout.write(self.style.SUCCESS('Servicio operativo') if chequeo.ok else self.style.ERROR('CAÍDA'))
