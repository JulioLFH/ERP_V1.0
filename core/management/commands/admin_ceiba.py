"""Crea el usuario «administrador» de Ceiba en esta implementación y envía su acceso a CORREO_INSTALACION.

    python manage.py admin_ceiba --url https://dominio-del-erp          crea el usuario (si no existe)
    python manage.py admin_ceiba --url https://dominio-del-erp --reponer  le pone una contraseña nueva y la envía
"""
from django.core.management.base import BaseCommand, CommandError

from core import admin_ceiba


class Command(BaseCommand):
    help = 'Crea el administrador de Ceiba y envía su acceso por correo (CORREO_INSTALACION)'

    def add_arguments(self, parser):
        parser.add_argument('--url', required=True, help='Dirección del ERP, ej. https://empresa.ceiba-tecnologic.com')
        parser.add_argument('--reponer', action='store_true', help='Contraseña nueva si ya existe')

    def handle(self, *args, **o):
        ok, mensaje = admin_ceiba.crear(o['url'], reponer=o['reponer'])
        if not ok:
            raise CommandError(mensaje)
        self.stdout.write(self.style.SUCCESS(mensaje))
