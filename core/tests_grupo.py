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
        self.assertContains(r, 'Panel del grupo')
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
        self.en_empresa2(lambda: local.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0]))
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


class PrimeraInstalacionTest(Base):
    def test_asistente_sin_usuarios(self):
        self.assertFalse(User.objects.exists())
        self.assertRedirects(self.client.get(reverse('login')), reverse('instalacion'), fetch_redirect_response=False)
        r = self.client.get(reverse('instalacion'), REMOTE_ADDR='200.48.10.5')  # desde fuera del servidor
        self.assertEqual(r.status_code, 403)
        with patch.dict('os.environ', {'CLAVE_INSTALACION': 'clave-del-instalador'}):
            self.assertEqual(self.client.get(reverse('instalacion') + '?clave=clave-del-instalador',
                                             REMOTE_ADDR='200.48.10.5').status_code, 200)
        r = self.client.post(reverse('instalacion'), {
            'razon_social': 'Grupo Andino S.A.C.', 'ruc': '20100066603', 'direccion': 'Av. Lima 100',
            'usuario': 'admin', 'nombre': 'Julio Flores', 'email': 'julio@grupo.pe', 'clave1': CLAVE,
            'clave2': CLAVE})
        self.assertRedirects(r, reverse('grupo_panel'), fetch_redirect_response=False)
        admin = User.objects.get()
        self.assertTrue(admin.is_superuser)
        self.assertEqual((admin.first_name, admin.last_name), ('Julio', 'Flores'))
        e = Empresa.actual()
        self.assertEqual((e.ruc, e.razon_social, e.direccion), ('20100066603', 'GRUPO ANDINO S.A.C.', 'Av. Lima 100'))
        self.assertEqual(self.client.session['corporativo']['username'], 'admin')
        self.assertContains(self.client.get(reverse('grupo_panel')), 'FILIAL NORTE S.A.C.')
        self.assertEqual(self.client.get(reverse('instalacion')).status_code, 404)  # ya no existe


class PanelDelGrupoTest(Base):
    def test_panel_y_usuario_corporativo_que_crea_empresas(self):
        User.objects.create_superuser('jefe', 'j@holding.pe', CLAVE)
        self.ingresar('jefe')
        r = self.client.get(reverse('grupo_panel'))
        self.assertContains(r, 'HOLDING PRINCIPAL S.A.C.')
        self.assertContains(r, 'FILIAL NORTE S.A.C.')
        self.assertContains(r, 'Falta configurar')
        self.client.post(reverse('grupo_panel'), {'accion': 'nuevo_corporativo', 'usuario': 'socia',
                                                  'nombre': 'Rosa Pérez', 'email': 'rosa@holding.pe', 'clave': CLAVE,
                                                  'todas': '1', 'puede_crear': '1', 'admin': '1', 've_panel': '1'})
        socia = User.objects.get(username='socia')
        c = UsuarioCorporativo.objects.get(usuario=socia)
        self.assertTrue(c.todas and c.puede_crear)
        self.assertFalse(socia.is_superuser)
        self.client.logout()
        self.ingresar('socia')
        r = self.client.get(reverse('grupo_panel'))
        self.assertContains(r, 'Crear empresa')
        self.assertNotContains(r, 'Nuevo usuario corporativo')  # eso es solo del administrador
        self.client.post(reverse('grupo_panel'), {'accion': 'nuevo_corporativo', 'usuario': 'otro', 'clave': CLAVE})
        self.assertFalse(User.objects.filter(username='otro').exists())


class IngresoDirectoCorporativoTest(Base):
    """El corporativo entra a otra empresa desde el login con su mismo usuario y clave de la principal."""

    def ingresar_en(self, usuario, clave=CLAVE, empresa='empresa2'):
        return self.client.post(reverse('login'), {'username': usuario, 'password': clave, 'empresa': empresa})

    def test_misma_clave_en_todas_sus_empresas(self):
        User.objects.create_superuser('gerente', 'gerente@holding.pe', CLAVE)
        r = self.ingresar_en('gerente')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.session['empresa'], 'empresa2')
        self.assertEqual(self.client.session['corporativo']['username'], 'gerente')
        espejo = self.en_empresa2(lambda: User.objects.get(username='gerente'))
        self.assertEqual(int(self.client.session['_auth_user_id']), espejo.pk)
        self.assertFalse(espejo.has_usable_password())  # la clave sigue siendo solo la de la principal
        self.assertContains(self.client.get(reverse('home')), 'FILIAL NORTE S.A.C.')
        self.client.post(reverse('cambiar_empresa'), {'empresa': 'default'})  # y pasa a la principal sin clave
        self.assertEqual(self.client.session['empresa'], 'default')

    def test_clave_incorrecta_sin_acceso_o_no_corporativo(self):
        User.objects.create_superuser('gerente', 'gerente@holding.pe', CLAVE)
        self.assertEqual(self.ingresar_en('gerente', 'otra-Clave-9').status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        limitado = User.objects.create_user('contador', 'c@holding.pe', CLAVE)
        UsuarioCorporativo.objects.create(usuario=limitado, todas=False, empresas=[])
        self.assertEqual(self.ingresar_en('contador').status_code, 200)  # corporativo sin esa empresa
        User.objects.create_user('vendedor', 'v@holding.pe', CLAVE)
        self.assertEqual(self.ingresar_en('vendedor').status_code, 200)  # usuario común de la principal
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(self.en_empresa2(lambda: User.objects.filter(username__in=['contador', 'vendedor'])
                                          .exists()))
        UsuarioCorporativo.objects.filter(usuario=limitado).update(empresas=['empresa2'])
        self.assertEqual(self.ingresar_en('contador').status_code, 302)  # con acceso asignado, entra

    def test_usuario_local_con_clave_propia_y_doble_factor(self):
        from .models import SegundoFactor
        local = self.en_empresa2(lambda: User.objects.create_user('jflores', 'j@filial.pe', 'Local-Clave-7'))
        self.assertEqual(self.ingresar_en('jflores', 'Local-Clave-7').status_code, 302)  # sin cambios
        self.assertEqual(int(self.client.session['_auth_user_id']), local.pk)
        self.assertNotIn('corporativo', self.client.session)
        self.client.logout()
        jefe = User.objects.create_superuser('jefe', 'jefe@holding.pe', CLAVE)
        SegundoFactor.objects.create(usuario=jefe, secreto='JBSWY3DPEHPK3PXP', activo=True)
        r = self.ingresar_en('jefe')
        self.assertContains(r, 'ingrese por la empresa principal')  # no se salta el doble factor
        self.assertNotIn('_auth_user_id', self.client.session)


class EmpresaSinModulosTest(Base):
    """Un usuario se guarda sin módulos; el corporativo no ve ni entra a la empresa donde no tiene ninguno."""

    def test_guardar_sin_modulos_y_empresa_oculta(self):
        from .usuarios import UsuarioForm
        admin = User.objects.create_superuser('jefe', 'jefe@holding.pe', CLAVE)
        form = UsuarioForm({'username': 'sinmod', 'is_active': 'on', 'clave1': CLAVE, 'clave2': CLAVE},
                           editor=admin)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertFalse(User.objects.get(username='sinmod').groups.exists())
        u = User.objects.create_user('corp', 'corp@holding.pe', CLAVE)
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        UsuarioCorporativo.objects.create(usuario=u, todas=True, empresas=[])
        local = self.en_empresa2(lambda: User.objects.create_user('corp', 'corp@filial.pe', None))
        self.ingresar('corp')
        self.assertNotContains(self.client.get(reverse('home')), 'Ir a FILIAL NORTE')
        self.client.post(reverse('cambiar_empresa'), {'empresa': 'empresa2'})
        self.assertEqual(self.client.session['empresa'], 'default')  # no puede pasar
        self.client.logout()
        r = self.client.post(reverse('login'), {'username': 'corp', 'password': CLAVE, 'empresa': 'empresa2'})
        self.assertContains(r, 'no tiene módulos asignados')  # ni entrar directo
        self.en_empresa2(lambda: local.groups.add(Group.objects.get_or_create(name=GRUPOS['compras'])[0]))
        self.ingresar('corp')
        self.assertContains(self.client.get(reverse('home')), 'Ir a FILIAL NORTE')  # con un módulo, la ve


class CorporativoConModulosTest(Base):
    """Al crear el corporativo se eligen sus módulos y empresas; en cada empresa entra con esos módulos y luego se
    ajustan allí."""

    def test_alta_con_modulos_y_empresas(self):
        User.objects.create_superuser('jefe', 'jefe@holding.pe', CLAVE)
        self.ingresar('jefe')
        self.client.post(reverse('grupo_panel'), {'accion': 'nuevo_corporativo', 'usuario': 'sinnada', 'clave': CLAVE,
                                                  'todas': '1'})
        self.assertFalse(User.objects.filter(username='sinnada').exists())  # sin módulos no se crea
        self.client.post(reverse('grupo_panel'), {'accion': 'nuevo_corporativo', 'usuario': 'ana', 'clave': CLAVE,
                                                  'modulos': ['ventas', 'compras'], 'empresas': ['empresa2']})
        ana = User.objects.get(username='ana')
        c = UsuarioCorporativo.objects.get(usuario=ana)
        self.assertEqual((c.todas, c.empresas, sorted(c.modulos)), (False, ['empresa2'], ['compras', 'ventas']))
        self.assertEqual(set(ana.groups.values_list('name', flat=True)), {GRUPOS['ventas'], GRUPOS['compras']})
        self.client.logout()
        self.ingresar('ana')
        self.assertContains(self.client.get(reverse('home')), 'Ir a FILIAL NORTE')
        self.client.post(reverse('cambiar_empresa'), {'empresa': 'empresa2'})
        self.assertEqual(self.client.session['empresa'], 'empresa2')
        espejo = self.en_empresa2(lambda: User.objects.get(username='ana'))
        self.assertFalse(espejo.is_superuser)  # no es administrador: solo sus módulos
        self.assertEqual(set(self.en_empresa2(lambda: list(espejo.groups.values_list('name', flat=True)))),
                         {GRUPOS['ventas'], GRUPOS['compras']})


class EditarCorporativoTest(Base):
    """El administrador de la principal edita datos, clave, empresas y módulos de un usuario corporativo."""

    def test_editar(self):
        User.objects.create_superuser('jefe', 'jefe@holding.pe', CLAVE)
        ana = User.objects.create_user('ana', 'ana@holding.pe', CLAVE)
        ana.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        UsuarioCorporativo.objects.create(usuario=ana, todas=True, empresas=[], modulos=['ventas'])
        self.ingresar('jefe')
        url = reverse('grupo_corporativo', args=[ana.pk])
        self.assertContains(self.client.get(reverse('grupo_empresas')), url)
        r = self.client.get(url)
        self.assertContains(r, 'FILIAL NORTE S.A.C.')
        self.assertContains(r, 'Aún no entró')
        r = self.client.post(url, {'nombre': 'Ana Ruiz', 'email': 'ana@holding.pe', 'activo': '1',
                                   'empresas': ['empresa2']})
        self.assertContains(r, 'Elija los módulos')  # sin módulos no se guarda
        r = self.client.post(url, {'nombre': 'Ana Ruiz', 'email': 'ana2@holding.pe', 'activo': '1',
                                   'empresas': ['empresa2'], 'modulos': ['compras'], 'puede_crear': '1',
                                   'clave': 'Nueva-Clave-8', 'clave2': 'Nueva-Clave-8'})
        self.assertRedirects(r, reverse('grupo_empresas'), fetch_redirect_response=False)
        ana.refresh_from_db()
        c = UsuarioCorporativo.objects.get(usuario=ana)
        self.assertEqual((ana.first_name, ana.last_name, ana.email), ('Ana', 'Ruiz', 'ana2@holding.pe'))
        self.assertTrue(ana.check_password('Nueva-Clave-8'))
        self.assertEqual((c.todas, c.empresas, c.modulos, c.puede_crear), (False, ['empresa2'], ['compras'], True))
        self.assertEqual(list(ana.groups.values_list('name', flat=True)), [GRUPOS['compras']])
        self.client.post(url, {'activo': '1', 'todas': '1', 'admin': '1'})
        self.assertIsNone(UsuarioCorporativo.objects.get(usuario=ana).modulos)  # administrador en cada empresa
        # un corporativo que no es administrador no edita a otros
        self.client.logout()
        self.client.post(reverse('login'), {'username': 'ana', 'password': 'Nueva-Clave-8', 'empresa': 'default'})
        self.assertEqual(self.client.get(url).status_code, 403)


class PermisoPanelTest(Base):
    """«Ve el Panel del grupo» se marca al crear y al editar el corporativo; sin él no ve ni abre el panel, pero sigue
    pasando entre sus empresas."""

    def test_ve_panel(self):
        User.objects.create_superuser('jefe', 'jefe@holding.pe', CLAVE)
        self.ingresar('jefe')
        self.client.post(reverse('grupo_panel'), {'accion': 'nuevo_corporativo', 'usuario': 'ana', 'clave': CLAVE,
                                                  'todas': '1', 'modulos': ['ventas']})  # sin marcar el panel
        ana = User.objects.get(username='ana')
        self.assertFalse(UsuarioCorporativo.objects.get(usuario=ana).ve_panel)
        self.client.logout()
        self.ingresar('ana')
        r = self.client.get(reverse('home'))
        self.assertNotContains(r, 'Panel del grupo')
        self.assertContains(r, 'Ir a FILIAL NORTE')  # igual pasa entre sus empresas
        self.assertEqual(self.client.get(reverse('grupo_panel')).status_code, 403)
        self.client.logout()
        self.ingresar('jefe')
        self.client.post(reverse('grupo_corporativo', args=[ana.pk]),
                         {'activo': '1', 'todas': '1', 'modulos': ['ventas'], 've_panel': '1'})
        self.assertTrue(UsuarioCorporativo.objects.get(usuario=ana).ve_panel)
        self.client.logout()
        self.ingresar('ana')
        self.assertContains(self.client.get(reverse('home')), 'Panel del grupo')
        self.assertEqual(self.client.get(reverse('grupo_panel')).status_code, 200)
