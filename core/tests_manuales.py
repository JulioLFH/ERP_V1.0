from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .manuales import PROCESOS, _manuales


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

    def test_procesos_por_modulo(self):
        pdfs = _manuales(PROCESOS)
        if not pdfs:
            self.skipTest('sin manuales de procesos en la carpeta')
        url = reverse('manual_proceso_pdf', args=[pdfs[0].name])
        self.client.force_login(self.usuario)
        self.assertIn(self.client.get(url).status_code, (403, 404))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('manuales')), url)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        # un PDF de la carpeta principal no se sirve por la ruta de procesos (ni al revés)
        self.assertEqual(self.client.get(reverse('manual_proceso_pdf', args=[_manuales()[0].name])).status_code, 404)
        self.assertEqual(self.client.get(reverse('manual_pdf', args=[pdfs[0].name])).status_code, 404)
