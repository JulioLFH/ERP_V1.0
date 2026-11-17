"""v1.115: Ajustes › Credenciales SUNAT y SIRE (cifradas, se editan, nunca se borran solas) y la protección contra el
«borrado solo»: un valor cifrado con otra SECRET_KEY ya no se lee como vacío ni se pierde al volver a guardar."""
import base64
import hashlib

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from .cifrado import PREFIJO, descifrar, ilegible
from .models import Bitacora, Empresa, FacturacionConfig


def cifrado_con_otra_llave(texto, llave='dev-insegura-cambiar-en-produccion-otra'):
    f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(f'ceiba-erp:{llave}'.encode()).digest()))
    return PREFIJO + f.encrypt(texto.encode()).decode()


class Base(TestCase):
    def setUp(self):
        Empresa.objects.create(ruc='20100000001', razon_social='EMPRESA SAC', direccion='Av. 1', email='e@e.pe')
        self.admin = User.objects.create_superuser('admin_cred', 'a@a.pe', 'Clave-segura-9')
        self.client.force_login(self.admin)
        self.url = reverse('credenciales_sunat')

    def enviar(self, **datos):
        return self.client.post(self.url, datos)


class PantallaTest(Base):
    def test_guarda_cifrado_y_no_borra_lo_que_queda_vacio(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'Credenciales SUNAT y SIRE')
        self.assertContains(r, 'No registrada')
        self.enviar(sol_usuario='FACTURA1', sol_clave='Sol-123', sire_usuario_sol='SIRE01', sire_clave_sol='Sire-456',
                    sire_client_id='id-sire', sire_client_secret='sec-sire', sunat_client_id='id-api',
                    sunat_client_secret='sec-api', gre_client_id='id-gre', gre_client_secret='sec-gre')
        e, f = Empresa.actual(), FacturacionConfig.actual()
        self.assertEqual((f.sol_usuario, descifrar(f.sol_clave)), ('FACTURA1', 'Sol-123'))
        self.assertEqual((e.sire_usuario_sol, descifrar(e.sire_clave_sol)), ('SIRE01', 'Sire-456'))
        self.assertEqual((e.sire_client_secret, e.sunat_client_secret), ('sec-sire', 'sec-api'))
        self.assertEqual(descifrar(f.gre_client_secret), 'sec-gre')
        with connection.cursor() as c:  # en la base van cifradas
            c.execute('select sire_client_secret, sire_clave_sol from core_empresa')
            self.assertTrue(all(v.startswith(PREFIJO) for v in c.fetchone()))
        r = self.client.get(self.url)
        self.assertNotContains(r, 'Sol-123')
        self.assertNotContains(r, 'sec-sire')  # los secretos no se vuelven a mostrar
        # volver a guardar con los campos de clave vacíos (como llega el formulario) no borra nada
        self.enviar(sol_usuario='FACTURA1', sire_usuario_sol='SIRE01', sire_client_id='id-sire',
                    sunat_client_id='id-api', gre_client_id='id-gre')
        e, f = Empresa.actual(), FacturacionConfig.actual()
        self.assertEqual((descifrar(f.sol_clave), descifrar(e.sire_clave_sol), e.sire_client_secret),
                         ('Sol-123', 'Sire-456', 'sec-sire'))
        # editar y borrar a propósito
        self.enviar(sol_usuario='FACTURA2', sol_clave='Nueva-789', sire_usuario_sol='SIRE01',
                    sire_client_id='id-sire', sunat_client_id='id-api', gre_client_id='id-gre',
                    gre_client_secret__borrar='on')
        f = FacturacionConfig.actual()
        self.assertEqual((f.sol_usuario, descifrar(f.sol_clave), f.gre_client_secret), ('FACTURA2', 'Nueva-789', ''))
        registro = Bitacora.objects.filter(motivo__startswith='Ajustes › Credenciales').last()
        self.assertNotIn('Nueva-789', str(registro.cambios))  # la auditoría no guarda los valores

    def test_guardar_ajustes_empresa_no_borra_los_secretos(self):
        e = Empresa.actual()
        e.sire_client_secret = 'sec-sire'
        e.save()
        e = Empresa.actual()
        e.razon_social = 'OTRO NOMBRE SAC'
        e.save()
        self.assertEqual(Empresa.actual().sire_client_secret, 'sec-sire')


class OtraLlaveTest(Base):
    def test_valor_de_otra_llave_no_se_pierde_al_guardar(self):
        """La causa del «se borra solo»: un valor cifrado con otra SECRET_KEY se leía vacío y el siguiente guardado
        lo reemplazaba por vacío. Ahora se conserva tal cual y la pantalla pide volver a ingresarlo."""
        valor = cifrado_con_otra_llave('sec-de-otra-llave')
        with connection.cursor() as c:
            c.execute('update core_empresa set sire_client_secret = %s', [valor])
        e = Empresa.actual()
        self.assertTrue(ilegible(e.sire_client_secret))
        e.razon_social = 'CAMBIO SAC'
        e.save()  # antes: guardaba '' y el secreto desaparecía
        with connection.cursor() as c:
            c.execute('select sire_client_secret from core_empresa')
            self.assertEqual(c.fetchone()[0], valor)
        self.assertContains(self.client.get(self.url), 'Vuelva a ingresarla')

    @override_settings(SECRET_KEY='dev-insegura-cambiar-en-produccion-otra')
    def test_con_la_llave_correcta_se_lee(self):
        valor = cifrado_con_otra_llave('sec-ok')
        with connection.cursor() as c:
            c.execute('update core_empresa set sire_client_secret = %s', [valor])
        self.assertEqual(Empresa.actual().sire_client_secret, 'sec-ok')


class LlaveDeLaInstalacionTest(TestCase):
    def test_settings_postgres_lee_la_llave_de_su_archivo(self):
        """Un comando ejecutado sin la variable SECRET_KEY usa la de .env.postgres (antes: la de desarrollo)."""
        import importlib
        import os
        from unittest.mock import patch

        import erp.settings_postgres as sp
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SECRET_KEY', None)
            with patch.object(sp, '_ENV', {'SECRET_KEY': 'llave-de-la-instalacion'}):
                self.assertEqual(sp._llave_instalacion(), 'llave-de-la-instalacion')
        importlib.reload  # noqa: B018 (no se recarga el módulo: solo se prueba la función)
