"""v1.117.6: franja ERP_AVISO arriba de todas las pantallas (p. ej. «Base de datos de prueba» en el ERP de pruebas)."""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse


class AvisoEntornoTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('t1176a', 't@t.com', 'x')

    @override_settings(ERP_AVISO='Base de datos de prueba')
    def test_franja_en_el_ingreso_y_en_las_pantallas(self):
        r = self.client.get(reverse('login'))
        self.assertContains(r, 'class="erp-aviso d-print-none"')
        self.assertContains(r, 'Base de datos de prueba')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('terceros'))
        self.assertContains(r, 'Base de datos de prueba')

    def test_sin_aviso_no_hay_franja(self):
        self.assertNotContains(self.client.get(reverse('login')), 'erp-aviso')
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('terceros')), 'erp-aviso')


class CertificadoPruebaSinGuardarTest(TestCase):
    """Con «Ninguno» guardado, elegir SUNAT · Pruebas y pulsar «Generar certificado de prueba» lo deja listo."""

    def setUp(self):
        from core.models import FacturacionConfig
        self.client.force_login(User.objects.create_superuser('t1176c', 't@t.com', 'x'))
        self.cfg = FacturacionConfig.actual()
        self.cfg.proveedor, self.cfg.entorno = 'NINGUNO', 'BETA'
        self.cfg.save()

    def test_genera_y_guarda_sunat_beta(self):
        r = self.client.get(reverse('facturacion'))
        self.assertContains(r, 'value="certificado_prueba"')  # ya está en la página (se muestra al elegir SUNAT)
        r = self.client.post(reverse('facturacion'), {'proveedor': 'SUNAT', 'entorno': 'BETA',
                                                      'accion': 'certificado_prueba'})
        self.assertRedirects(r, reverse('facturacion'), fetch_redirect_response=False)
        self.cfg.refresh_from_db()
        self.assertEqual((self.cfg.proveedor, self.cfg.entorno, self.cfg.certificado_prueba, bool(self.cfg.certificado)),
                         ('SUNAT', 'BETA', True, True))

    def test_en_produccion_no_genera(self):
        r = self.client.post(reverse('facturacion'), {'proveedor': 'SUNAT', 'entorno': 'PRODUCCION',
                                                      'accion': 'certificado_prueba'}, follow=True)
        self.assertContains(r, 'solo sirve con SUNAT directo en el entorno de pruebas')
        self.cfg.refresh_from_db()
        self.assertEqual((self.cfg.proveedor, bool(self.cfg.certificado)), ('NINGUNO', False))
