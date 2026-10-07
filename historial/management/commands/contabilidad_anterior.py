"""Lleva a los libros del ERP la contabilidad del sistema anterior hasta una fecha de corte.

    python manage.py contabilidad_anterior --corte 2026-09-30 [--sin-auxiliares]

1. Importa los asientos del sistema anterior desde su apertura del ejercicio hasta el corte (origen ANTERIOR).
2. Recalcula los saldos por pagar por proveedor y los saldos de caja y bancos desde esa apertura (al corte).
3. Asiento de ajustes de migración al corte (facturas a las cuentas del ERP, existencias al valor del kardex).
4. Cierra los periodos hasta el corte: desde el día siguiente contabiliza el ERP.
Se puede repetir (reemplaza lo importado antes)."""
from datetime import date

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from historial import contabilidad_anterior as ca


class Command(BaseCommand):
    help = 'Importa la contabilidad del sistema anterior hasta la fecha de corte y deja al ERP contabilizando después.'

    def add_arguments(self, parser):
        parser.add_argument('--corte', required=True, help='Último día de un mes, AAAA-MM-DD')
        parser.add_argument('--sin-auxiliares', action='store_true',
                            help='No recalcular por pagar ni caja y bancos (solo los libros)')

    def handle(self, corte, sin_auxiliares, **_):
        try:
            fecha = date.fromisoformat(corte)
        except ValueError:
            raise CommandError('Fecha de corte inválida (use AAAA-MM-DD).')
        try:
            r = ca.importar(fecha, log=self.stdout.write)
        except ca.ErrorMigracion as exc:
            raise CommandError(str(exc))
        if not sin_auxiliares:
            reporte = settings.BASE_DIR / 'migracion_local' / 'anteriores_auxiliares.xlsx'
            reporte.parent.mkdir(exist_ok=True)
            call_command('importar_anteriores', str(settings.BASE_DIR), solo='por_pagar,bancos', rehacer=True,
                         reporte=str(reporte), stdout=self.stdout)
        filas, _ = ca.conciliacion(fecha)
        self.stdout.write(f'Libros del sistema anterior desde {r["desde"]} al {fecha:%d/%m/%Y} '
                          f'(último asiento {r["ultima"]:%d/%m/%Y}): {r["asientos"]:,} asientos, {r["lineas"]:,} líneas.')
        self.stdout.write('Conciliación al corte (auxiliar del ERP / libros / diferencia):')
        for f in filas:
            self.stdout.write(f'  {f["concepto"]:32} {f["auxiliar"]:16,.2f} {f["libros"]:16,.2f} {f["diferencia"]:14,.2f}')
        self.stdout.write(self.style.SUCCESS('Listo. Desde el día siguiente al corte contabiliza el ERP.'))
