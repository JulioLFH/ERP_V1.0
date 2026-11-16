"""Usuario «administrador» de Ceiba (v1.112): se crea al terminar la primera instalación y su contraseña se envía solo
por correo a CORREO_INSTALACION; si el correo no sale, no se crea. Comando admin_ceiba para las ya instaladas."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.urls import reverse

from . import admin_ceiba
from .tests_grupo import CLAVE, Base

ENV = {'CORREO_INSTALACION': 'dueno@ceiba.test'}
DATOS = {'razon_social': 'Grupo Andino S.A.C.', 'ruc': '20100066603', 'direccion': 'Av. Lima 100',
         'usuario': 'cliente', 'nombre': 'Julio Flores', 'email': 'julio@grupo.pe', 'clave1': CLAVE, 'clave2': CLAVE}


class AdministradorCeibaTest(Base):
    def test_al_instalar_se_crea_y_su_clave_va_por_correo(self):
        enviados = []
        with patch.dict('os.environ', ENV), patch('core.correo.enviar', side_effect=lambda *a, **k: enviados.append((a, k))):
            r = self.client.post(reverse('instalacion'), DATOS)
        self.assertRedirects(r, reverse('grupo_panel'), fetch_redirect_response=False)
        cliente, ceiba = User.objects.get(username='cliente'), User.objects.get(username='administrador')
        self.assertTrue(cliente.is_superuser and ceiba.is_superuser)
        self.assertEqual(ceiba.email, 'dueno@ceiba.test')
        (destinos, asunto, plantilla, contexto), opciones = enviados[0]
        self.assertEqual(destinos, ['dueno@ceiba.test'])
        self.assertIn('GRUPO ANDINO S.A.C.', asunto)
        self.assertTrue(opciones['solo_ceiba'])
        self.assertTrue(ceiba.check_password(contexto['clave']))  # la contraseña enviada es la del usuario
        self.assertGreaterEqual(len(contexto['clave']), 16)
        self.assertTrue(contexto['enlace'].endswith('/login/'))
        self.assertEqual(self.client.session['corporativo']['username'], 'cliente')  # entra el cliente, no Ceiba

    def test_nombre_reservado_en_el_asistente(self):
        with patch.dict('os.environ', ENV), patch('core.correo.enviar'):
            r = self.client.post(reverse('instalacion'), {**DATOS, 'usuario': 'Administrador'})
        self.assertContains(r, 'está reservado para el administrador de Ceiba')
        self.assertFalse(User.objects.exists())

    def test_sin_correo_no_se_crea_y_el_cliente_igual_instala(self):
        from .correo import ErrorCorreo
        with patch.dict('os.environ', ENV), patch('core.correo.enviar', side_effect=ErrorCorreo('sin SMTP')):
            r = self.client.post(reverse('instalacion'), DATOS)
        self.assertRedirects(r, reverse('grupo_panel'), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(username='cliente').exists())
        self.assertFalse(User.objects.filter(username='administrador').exists())

    def test_sin_correo_de_instalacion_no_hace_nada(self):
        with patch('core.correo.enviar') as enviar:
            self.client.post(reverse('instalacion'), DATOS)
        enviar.assert_not_called()
        self.assertEqual(list(User.objects.values_list('username', flat=True)), ['cliente'])

    def test_comando_para_las_ya_instaladas(self):
        User.objects.create_superuser('cliente', 'c@x.pe', CLAVE)
        claves = []
        with patch.dict('os.environ', ENV), \
                patch('core.correo.enviar', side_effect=lambda d, a, p, c, **k: claves.append(c['clave'])):
            call_command('admin_ceiba', url='https://empresa.ceiba-tecnologic.com')
            with self.assertRaisesMessage(CommandError, 'ya existe'):
                call_command('admin_ceiba', url='https://empresa.ceiba-tecnologic.com')
            call_command('admin_ceiba', url='https://empresa.ceiba-tecnologic.com', reponer=True)
        self.assertEqual(len(claves), 2)
        self.assertNotEqual(claves[0], claves[1])
        self.assertTrue(User.objects.get(username='administrador').check_password(claves[1]))

    def test_clave_segura(self):
        c = admin_ceiba.clave_segura()
        self.assertEqual(len(c), 16)
        self.assertTrue(any(x in admin_ceiba.SIMBOLOS for x in c) and any(x.isdigit() for x in c))


class LogoEmpresaTest(Base):
    PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + bytes(32)

    def validar(self, tamano):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .forms import EmpresaForm
        from .models import Empresa
        form = EmpresaForm(instance=Empresa.objects.get())
        form.cleaned_data = {"logo_archivo": SimpleUploadedFile("logo.png", self.PNG + bytes(tamano - len(self.PNG)),
                                                                content_type="image/png")}
        return form.clean_logo_archivo()

    def test_acepta_hasta_5_mb(self):
        self.assertIsNotNone(self.validar(4 * 1024 * 1024))
        self.assertIsNotNone(self.validar(5 * 1024 * 1024))
        with self.assertRaisesMessage(Exception, "supera 5 MB"):
            self.validar(5 * 1024 * 1024 + 1)
