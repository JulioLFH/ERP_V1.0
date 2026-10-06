from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .manuales import _manuales


class ManualesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('jefe', password='x')
        self.usuario = User.objects.create_user('operador', password='x')

    def test_solo_el_administrador_ve_los_manuales(self):
        self.client.force_login(self.usuario)
        self.assertIn(self.client.get(reverse('manuales')).status_code, (403, 404))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('manuales'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Manuales de usuario')

    def test_menu_de_ajustes_oculta_la_opcion_al_resto(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('respaldo')), reverse('manuales'))

    def test_descarga_del_pdf(self):
        pdfs = _manuales()
        if not pdfs:
            self.skipTest('sin manuales en la carpeta')
        self.client.force_login(self.usuario)
        self.assertIn(self.client.get(reverse('manual_pdf', args=[pdfs[0].name])).status_code, (403, 404))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('manual_pdf', args=[pdfs[0].name]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(self.client.get(reverse('manual_pdf', args=['..%2Fsettings.py'])).status_code, 404)
