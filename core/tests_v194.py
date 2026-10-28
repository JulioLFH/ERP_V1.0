"""v1.94: íconos 3D de los módulos en Inicio (scripts/iconos_modulos.py). v1.95: título CEIBA ERP y selector de
empresa en la barra superior."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.modulos import MODULOS
from core.tests_grupo import CLAVE, Base as BaseGrupo


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
        self.assertContains(r, '<h1 class="apps-titulo" aria-label="Ceiba ERP">')  # v1.95: la marca, no «Hola, …»
        self.assertNotContains(r, 'Hola,')


class SelectorEmpresaTest(BaseGrupo):
    def test_cambiar_de_empresa_desde_la_barra(self):
        """v1.95: el nombre de la empresa en la barra abre la lista de empresas con acceso (como en Odoo)."""
        User.objects.create_superuser('gerente', 'gerente@holding.pe', CLAVE)
        self.ingresar('gerente')
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'empresas-menu')
        self.assertContains(r, 'HOLDING PRINCIPAL S.A.C.</div>')  # la actual, marcada
        self.assertContains(r, 'name="empresa" value="empresa2"')
        self.assertContains(r, 'FILIAL NORTE S.A.C.</button>')
        self.assertNotContains(r, 'Cada empresa tiene sus usuarios')  # corporativo: cambia sin volver a ingresar
        r = self.client.post(reverse('cambiar_empresa'), {'empresa': 'empresa2'})
        self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('home')), 'FILIAL NORTE S.A.C.</div>')
