"""v1.20: multiempresa: cada empresa en su propia base (usuarios, datos y configuración separados)."""
import io
from contextlib import redirect_stderr

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from erp.empresas import activar, actual, config_base, leer_empresas_extra, restaurar

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


class ConfigBaseTest(SimpleTestCase):
    """EMPRESAS_EXTRA con "base": otra base en el mismo servidor de la principal (sin URL ni clave)."""
    principal = {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'erp_db', 'USER': 'erp', 'PASSWORD': 'x',
                 'HOST': 'dpg-1', 'PORT': 5432}

    def test_misma_conexion_con_otro_nombre(self):
        conf = config_base({'nombre': 'OTRA S.A.C.', 'base': 'erp_empresa2'}, self.principal)
        self.assertEqual(conf['NAME'], 'erp_empresa2')
        self.assertEqual((conf['HOST'], conf['USER'], conf['PASSWORD']), ('dpg-1', 'erp', 'x'))
        self.assertEqual(self.principal['NAME'], 'erp_db')  # no toca la principal

    def test_url_propia_y_nombre_invalido(self):
        self.assertEqual(config_base({'url': 'postgres://u:c@otro:5432/b2'}, self.principal)['HOST'], 'otro')
        with self.assertRaises(ValueError):
            config_base({'base': 'x"; DROP'}, self.principal)

    def test_variable_simple_o_con_errores_de_pegado(self):
        self.assertEqual(leer_empresas_extra('SEGUNDA S.A.C.'),
                         {'empresa2': {'nombre': 'SEGUNDA S.A.C.', 'base': 'erp_empresa2'}})
        self.assertEqual(list(leer_empresas_extra(' A S.A.C. ; B S.A.C. ')), ['empresa2', 'empresa3'])
        json_tipografico = '“{“e2”: {“nombre”: “X”, “base”: “erp_e2”}}”'
        self.assertEqual(leer_empresas_extra(json_tipografico), {'e2': {'nombre': 'X', 'base': 'erp_e2'}})
        with redirect_stderr(io.StringIO()):
            self.assertEqual(leer_empresas_extra('{roto'), {})
        self.assertEqual(leer_empresas_extra(''), {})

    def test_sqlite_junto_a_la_principal(self):
        conf = config_base({'base': 'empresa3'}, {'ENGINE': 'django.db.backends.sqlite3', 'NAME': '/datos/db.sqlite3'})
        self.assertEqual(conf['NAME'].name, 'empresa3.sqlite3')
