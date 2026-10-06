"""v1.20: multiempresa: cada empresa en su propia base (usuarios, datos y configuración separados)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from erp.empresas import activar, actual, restaurar

from .models import Empresa, Tercero


class MultiempresaTest(TestCase):
    databases = {'default', 'empresa2'}

    @classmethod
    def setUpTestData(cls):
        User.objects.create_superuser('admin1', 'a@a.pe', 'clave-uno')
        Empresa.objects.create(ruc='20100000001', razon_social='PRIMERA S.A.C.')
        token = activar('empresa2')
        try:
            User.objects.create_superuser('ana', 'ana@b.pe', 'clave-dos')
            Empresa.objects.create(ruc='20600000002', razon_social='SEGUNDA S.A.C.')
        finally:
            restaurar(token)

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_usuarios_y_datos_separados(self):
        self.assertFalse(User.objects.filter(username='ana').exists())  # el usuario es de la otra empresa
        r = self.client.get(reverse('login'))
        self.assertContains(r, 'SEGUNDA S.A.C.')  # el inicio de sesión ofrece las empresas
        # en la empresa principal "ana" no existe
        r = self.client.post(reverse('login'), {'username': 'ana', 'password': 'clave-dos', 'empresa': 'default'})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse('login'), {'username': 'ana', 'password': 'clave-dos', 'empresa': 'empresa2'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.session['empresa'], 'empresa2')
        self.assertContains(self.client.get(reverse('home')), 'SEGUNDA S.A.C.')
        self.client.post(reverse('tercero_nuevo'), {
            'tipo': 'CLIENTE', 'tipo_doc': '1', 'numero_doc': '45678912', 'nombre': 'CLIENTE DE LA SEGUNDA',
            'dias_credito': '0', 'limite_credito': '0', 'activo': 'on'})
        self.assertTrue(Tercero.objects.using('empresa2').filter(nombre='CLIENTE DE LA SEGUNDA').exists())
        self.assertFalse(Tercero.objects.using('default').filter(nombre='CLIENTE DE LA SEGUNDA').exists())
        self.assertEqual(actual(), 'default')  # al terminar la petición se restaura la principal
        # cambiar de empresa cierra la sesión y lleva al inicio de sesión de la otra
        r = self.client.post(reverse('cambiar_empresa'), {'empresa': 'default'})
        self.assertRedirects(r, f'{reverse("login")}?empresa=default', fetch_redirect_response=False)
        self.client.post(reverse('login'), {'username': 'admin1', 'password': 'clave-uno', 'empresa': 'default'})
        self.assertContains(self.client.get(reverse('home')), 'PRIMERA S.A.C.')
