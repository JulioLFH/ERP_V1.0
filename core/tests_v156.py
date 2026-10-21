"""v1.56 (n.º 57): novedades por versión para todos los usuarios (desde el CHANGELOG) y manual 10 en PDF, en el orden
de los manuales."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class NovedadesTest(TestCase):
    def test_novedades_para_todos_y_orden_de_manuales(self):
        from django.conf import settings

        from .manuales import _manuales, versiones
        lista = versiones('1.41')
        self.assertEqual(lista[0]['version'], settings.ERP_VERSION)  # la versión instalada primero
        self.assertTrue(all(tuple(map(int, v['version'].split('.'))) >= (1, 41, 0) for v in lista))
        self.assertTrue(any('<b>' in i for v in lista for i in v['items']))  # negritas del CHANGELOG
        u = User.objects.create_user('vendedor', password='x')  # sin módulos ni permisos especiales
        self.client.force_login(u)
        r = self.client.get(reverse('novedades'), {'desde': '1.41'})
        self.assertContains(r, f'v{settings.ERP_VERSION}')
        self.assertContains(r, 'Motor único de aprobación')  # v1.42
        self.assertNotContains(r, 'v1.39.0')  # se pidió desde la 1.41
        self.assertIn(self.client.get(reverse('manuales')).status_code, (403, 404))  # los PDF siguen siendo de admin
        nombres = [p.name for p in _manuales()]
        self.assertEqual(nombres[-1], '10. Novedades por versión.pdf')  # 1, 2… 9, 10 (no alfabético)
