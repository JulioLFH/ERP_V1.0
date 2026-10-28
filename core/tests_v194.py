"""v1.94: íconos isométricos 3D de los módulos en Inicio (scripts/iconos_modulos.py)."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.modulos import MODULOS


class IconosModulosTest(TestCase):
    def test_cada_modulo_tiene_su_icono_3d(self):
        carpeta = Path(settings.BASE_DIR) / 'static' / 'img' / 'modulos'
        faltan = [m['clave'] for m in MODULOS if not (carpeta / f"{m['clave']}.svg").is_file()]
        self.assertEqual(faltan, [], 'Vuelva a generar los íconos: python scripts/iconos_modulos.py')
        svg = (carpeta / 'inventario.svg').read_text(encoding='utf-8')
        self.assertTrue(svg.startswith('<svg') and 'viewBox' in svg and '<script' not in svg)

    def test_inicio_muestra_los_iconos(self):
        self.client.force_login(User.objects.create_superuser('admin', 'a@a.com', 'x'))
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'app-icon-3d')
        self.assertContains(r, 'img/modulos/ventas.svg')
