"""Usuario corporativo y empresas del grupo (v1.49): un solo ingreso para varias empresas, cambio de empresa sin
cerrar sesión, acceso limitado, usuario espejo en cada empresa y alta de empresas. Un escenario por clase."""
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from erp.empresas import activar, restaurar

from .models import Empresa, EmpresaGrupo, PerfilUsuario, UsuarioCorporativo
from .modulos import GRUPOS

CLAVE = 'Clave-segura-9'


class Base(TestCase):
    databases = {'default', 'empresa2'}

    def setUp(self):
        cache.clear()
        Empresa.objects.create(ruc='20100000001', razon_social='HOLDING PRINCIPAL S.A.C.')
        token = activar('empresa2')
        try:
            Empresa.objects.create(ruc='20600000005', razon_social='FILIAL NORTE S.A.C.')
        finally:
            restaurar(token)

    def en_empresa2(self, funcion):
        token = activar('empresa2')
        try:
            return funcion()
        finally:
            restaurar(token)

    def ingresar(self, usuario):
        return self.client.post(reverse('login'), {'username': usuario, 'password': CLAVE, 'empresa': 'default'})


class CambioSinCerrarSesionTest(Base):
    def test_administrador_pasa_entre_empresas(self):
        User.objects.create_superuser('gerente', 'gerente@holding.pe', CLAVE, first_name='Gerente')
        self.ingresar('gerente')
        self.assertEqual(self.client.session['corporativo']['username'], 'gerente')
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'Ir a FILIAL NORTE S.A.C.')
        self.assertContains(r, 'Empresas del grupo')
        r = self.client.post(reverse('cambiar_empresa'), {'empresa': 'empresa2'})
        self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)  # no lo manda a iniciar sesión
        self.assertEqual(self.client.session['empresa'], 'empresa2')
        self.assertContains(self.client.get(reverse('home')), 'FILIAL NORTE S.A.C.')
        espejo = self.en_empresa2(lambda: User.objects.get(username='gerente'))
        self.assertTrue(espejo.is_superuser)
        self.assertFalse(espejo.has_usable_password())  # en la filial solo se entra por el grupo
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)
        self.client.post(reverse('cambiar_empresa'), {'empresa': 'default'})  # y vuelve
        self.assertEqual(self.client.session['empresa'], 'default')
        self.assertContains(self.client.get(reverse('home')), 'HOLDING PRINCIPAL S.A.C.')


class AccesoLimitadoTest(Base):
    def test_corporativo_sin_acceso_y_usuario_comun(self):
        u = User.objects.create_user('contador', 'c@holding.pe', CLAVE)
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['contabilidad'])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=[])
        UsuarioCorporativo.objects.create(usuario=u, todas=False, empresas=[])
        self.ingresar('contador')
        self.assertNotContains(self.client.get(reverse('home')), 'Ir a FILIAL NORTE')
        self.client.post(reverse('grupo_entrar'), {'empresa': 'empresa2'})
        self.assertEqual(self.client.session.get('empresa'), 'default')  # sin acceso: se queda
        UsuarioCorporativo.objects.filter(usuario=u).update(empresas=['empresa2'])
        # ya existe en la filial con otros permisos: usa los de allí (no se vuelve administrador)
        local = self.en_empresa2(lambda: User.objects.create_user('contador', 'c@filial.pe', 'otra-Clave-1'))
        self.client.post(reverse('grupo_entrar'), {'empresa': 'empresa2'})
        self.assertEqual(self.client.session['empresa'], 'empresa2')
        self.assertEqual(int(self.client.session['_auth_user_id']), local.pk)
        self.assertFalse(self.en_empresa2(lambda: User.objects.get(pk=local.pk).is_superuser))
        # un usuario que no es corporativo sigue saliendo e ingresando a la otra empresa
        self.client.logout()
        User.objects.create_user('vendedor', 'v@holding.pe', CLAVE)
        self.ingresar('vendedor')
        self.assertNotIn('corporativo', self.client.session)
        r = self.client.post(reverse('cambiar_empresa'), {'empresa': 'empresa2'})
        self.assertIn(reverse('login'), r['Location'])  # cerró la sesión para ingresar a la otra empresa
        self.ingresar('vendedor')
        self.assertEqual(self.client.get(reverse('grupo_empresas')).status_code, 404)


class CrearEmpresaTest(Base):
    def test_validaciones_registro_y_pantalla(self):
        from . import grupo
        admin = User.objects.create_superuser('dueno', 'd@holding.pe', CLAVE)
        self.ingresar('dueno')
        for razon, ruc, error in (('', '20100066603', 'razón social'), ('NUEVA', '123', '11 dígitos'),
                                  ('OTRA', '20600000005', 'ya es de FILIAL NORTE')):
            with self.assertRaisesMessage(grupo.ErrorGrupo, error):
                grupo.crear_empresa(razon, ruc, admin)
        with patch('core.grupo.threading.Thread') as hilo:
            r = self.client.post(reverse('grupo_empresas'), {'accion': 'crear', 'razon_social': 'Nueva Sur S.A.C.',
                                                              'ruc': '20100066603'}, follow=True)
        hilo.return_value.start.assert_called_once()
        e = EmpresaGrupo.objects.get()
        self.assertEqual((e.alias, e.razon_social, e.estado, e.creado_por), ('empresa3', 'NUEVA SUR S.A.C.',
                                                                            'CREANDO', 'dueno'))
        self.assertTrue(e.base.endswith('_empresa3'))
        self.assertContains(r, 'Creando NUEVA SUR S.A.C.')
        self.assertEqual(self.client.get(reverse('grupo_estado')).json()['empresa3']['estado'], 'CREANDO')
        with self.assertRaisesMessage(grupo.ErrorGrupo, 'ya se está creando'):
            grupo.crear_empresa('REPETIDA', '20100066603', admin)
