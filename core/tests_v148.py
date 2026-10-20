"""Inicio de sesión corporativo (OpenID Connect) y estado del servicio (v1.48). El proveedor se simula con una llave
RSA propia: los tokens se firman y se verifican de verdad. Un escenario por clase."""
import base64
import json
import time
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .cifrado import descifrar
from .models import ChequeoServicio, Empresa, PerfilUsuario, ProveedorSSO
from .modulos import GRUPOS

EMISOR = 'https://idp.prueba.pe'
LLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(datos):
    return base64.urlsafe_b64encode(datos).decode().rstrip('=')


def entero(n):
    return b64(n.to_bytes((n.bit_length() + 7) // 8, 'big'))


def id_token(claims, llave=LLAVE, kid='k1'):
    cabecera = b64(json.dumps({'alg': 'RS256', 'kid': kid}).encode())
    cuerpo = b64(json.dumps(claims).encode())
    firma = llave.sign(f'{cabecera}.{cuerpo}'.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f'{cabecera}.{cuerpo}.{b64(firma)}'


class ProveedorSimulado:
    def __init__(self):
        self.token = None

    def __call__(self, url, datos=None):
        if url.endswith('/.well-known/openid-configuration'):
            return {'issuer': EMISOR, 'authorization_endpoint': f'{EMISOR}/authorize',
                    'token_endpoint': f'{EMISOR}/token', 'jwks_uri': f'{EMISOR}/keys'}
        if url.endswith('/keys'):
            n = LLAVE.public_key().public_numbers()
            return {'keys': [{'kty': 'RSA', 'kid': 'k1', 'n': entero(n.n), 'e': entero(n.e)}]}
        if url.endswith('/token'):
            self.recibido = datos
            return {'id_token': self.token}
        raise AssertionError(url)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        Empresa.objects.create(ruc='20100000001', razon_social='EMPRESA SSO S.A.C.')
        self.proveedor = ProveedorSSO.objects.create(tipo='OIDC', nombre='Ingresar con la cuenta de la empresa',
                                                     client_id='app-ceiba', emisor=EMISOR, dominios='empresa.pe')
        self.usuario = User.objects.create_user('ana', email='Ana@Empresa.pe', password='Clave-segura-9')
        self.usuario.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        PerfilUsuario.objects.create(usuario=self.usuario, acciones=['ventas.emitir'])
        self.idp = ProveedorSimulado()
        p = patch('core.sso._http_json', self.idp)
        p.start()
        self.addCleanup(p.stop)

    def ingresar(self, correo='ana@empresa.pe', **cambios):
        r = self.client.get(reverse('sso_iniciar', args=[self.proveedor.pk]))
        destino = urlparse(r['Location'])
        params = {k: v[0] for k, v in parse_qs(destino.query).items()}
        claims = {'iss': EMISOR, 'aud': 'app-ceiba', 'exp': time.time() + 300, 'nonce': params['nonce'],
                  'email': correo, **cambios}
        self.idp.token = cambios.pop('_token', None) or id_token(claims)
        return params, self.client.get(reverse('sso_callback'), {'state': params['state'], 'code': 'codigo-1'},
                                       follow=True)


class SSOIngresoTest(Base):
    def test_ingresa_por_su_correo_con_pkce(self):
        self.assertContains(self.client.get(reverse('login')), 'Ingresar con la cuenta de la empresa')
        params, r = self.ingresar()
        self.assertEqual((params['code_challenge_method'], params['client_id']), ('S256', 'app-ceiba'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.usuario.pk)
        self.assertEqual(self.idp.recibido['code'], 'codigo-1')
        self.assertTrue(self.idp.recibido['code_verifier'])
        # con verificación en dos pasos obligatoria, quien entró por su proveedor corporativo no la repite
        Empresa.objects.update(exigir_doble_factor='TODOS')
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)


class SSORechazosTest(Base):
    def test_rechaza_dominio_usuario_firma_y_estado(self):
        _, r = self.ingresar(correo='ana@gmail.com')
        self.assertContains(r, 'no pertenece a un dominio permitido')
        _, r = self.ingresar(correo='pedro@empresa.pe')
        self.assertContains(r, 'No hay un usuario activo del sistema con el correo pedro@empresa.pe')
        otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        r = self.client.get(reverse('sso_iniciar', args=[self.proveedor.pk]))
        params = {k: v[0] for k, v in parse_qs(urlparse(r['Location']).query).items()}
        self.idp.token = id_token({'iss': EMISOR, 'aud': 'app-ceiba', 'exp': time.time() + 300,
                                   'nonce': params['nonce'], 'email': 'ana@empresa.pe'}, llave=otra)
        r = self.client.get(reverse('sso_callback'), {'state': params['state'], 'code': 'x'}, follow=True)
        self.assertContains(r, 'La firma del token no es válida')
        _, r = self.ingresar(aud='otra-app')
        self.assertContains(r, 'no es para esta aplicación')
        r = self.client.get(reverse('sso_callback'), {'state': 'inventado', 'code': 'x'}, follow=True)
        self.assertContains(r, 'no es válido o venció')
        self.assertNotIn('_auth_user_id', self.client.session)


class SSOConfiguracionTest(Base):
    def test_solo_administrador_y_secreto_cifrado(self):
        self.client.force_login(self.usuario)
        self.assertIn(self.client.get(reverse('sso_lista')).status_code, (403, 404))  # sin Ajustes ni ser admin
        admin = User.objects.create_superuser('admin_sso', 'a@a.pe', 'Clave-segura-9')
        self.client.force_login(admin)
        self.client.post(reverse('sso_nuevo'), {'tipo': 'MICROSOFT', 'nombre': 'Ingresar con Microsoft',
                                                'client_id': 'app-ms', 'client_secret_nuevo': 'secreto-123',
                                                'tenant': 'empresa.onmicrosoft.com', 'activo': 'on'})
        p = ProveedorSSO.objects.get(tipo='MICROSOFT')
        self.assertNotIn('secreto-123', p.client_secret)
        self.assertEqual(descifrar(p.client_secret), 'secreto-123')
        self.assertEqual(p.url_emisor, 'https://login.microsoftonline.com/empresa.onmicrosoft.com/v2.0')
        self.assertContains(self.client.get(reverse('sso_lista')), reverse('sso_callback'))


class EstadoServicioTest(TestCase):
    databases = {'default', 'empresa2'}

    def test_pagina_publica_disponibilidad_y_caidas(self):
        self.client.get(reverse('salud'))
        self.assertEqual(ChequeoServicio.objects.count(), 1)
        self.client.get(reverse('salud'))  # no más de un registro cada 4 minutos
        self.assertEqual(ChequeoServicio.objects.count(), 1)
        ahora = timezone.now()
        for minutos, ok in ((50, True), (40, False), (35, False), (30, True)):
            c = ChequeoServicio.objects.create(ok=ok, componentes={})
            ChequeoServicio.objects.filter(pk=c.pk).update(fecha=ahora - timedelta(minutes=minutos))
        r = self.client.get(reverse('estado_servicio'))  # sin sesión
        self.assertContains(r, 'Base de datos')
        self.assertEqual(r.context['disp_30'], 60.0)  # 3 de 5 chequeos correctos
        self.assertEqual(r.context['caidas'][0]['minutos'], 10)
        self.assertNotContains(r, 'C:\\')  # no muestra rutas del servidor
        import io
        call_command('chequear_servicio', stdout=io.StringIO())
        self.assertEqual(ChequeoServicio.objects.count(), 6)
