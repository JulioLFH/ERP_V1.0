"""v1.99.0: celulares con la app Ceiba ERP aprobados por el administrador entran con el control por IP activo."""
import base64
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core import acceso_ip, app_movil
from core.models import ControlIp, DispositivoApp, IpPermitida

AFUERA = '198.51.100.7'
APP = 'Mozilla/5.0 (Linux; Android 15) CeibaApp/1.1.0'


class Celular:
    """Simula la app: llave EC P-256 propia y pedido firmado como lo hace el Android Keystore."""

    def __init__(self):
        self.llave = ec.generate_private_key(ec.SECP256R1())
        der = self.llave.public_key().public_bytes(serialization.Encoding.DER,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)
        self.clave = base64.b64encode(der).decode()
        self.codigo = app_movil.codigo_de(der)
        self.ultima = 0

    def pedido(self, version='1.1.0', hora=None, firma=None):
        # cada firma con una hora mayor que la anterior (dos pedidos en el mismo milisegundo serían una repetición)
        self.ultima = hora or max(int(time.time() * 1000), self.ultima + 1)
        hora = self.ultima
        firmado = self.llave.sign(app_movil.mensaje_firmado(hora, version, self.clave), ec.ECDSA(hashes.SHA256()))
        return json.dumps({'clave': self.clave, 'version': version, 'modelo': 'Pixel 7', 'hora': hora,
                           'firma': firma or base64.b64encode(firmado).decode()})


class CelularesAppTest(TestCase):
    def setUp(self):
        cache.clear()
        acceso_ip._pendientes.clear()
        acceso_ip.invalidar()
        self.addCleanup(acceso_ip.invalidar)
        self.admin = User.objects.create_superuser('admin.cel', 'a@a.pe', 'x')
        IpPermitida.objects.create(ip='203.0.113.9')
        ControlIp.objects.create(activo=True)
        self.celular = Celular()

    def autorizar(self, celular=None, **kw):
        r = self.client.post(reverse('autorizar_app'), (celular or self.celular).pedido(**kw),
                             content_type='application/json', REMOTE_ADDR=AFUERA, HTTP_USER_AGENT=APP)
        return r.status_code, r.json()

    def abrir(self, pase=None):
        if pase:
            self.client.cookies[app_movil.COOKIE] = pase
        return self.client.get(reverse('login'), REMOTE_ADDR=AFUERA, HTTP_USER_AGENT=APP)

    def aprobar(self):
        self.client.force_login(self.admin)
        c = DispositivoApp.objects.get(codigo=self.celular.codigo)
        self.client.post(reverse('acceso_ip'), {'accion': 'aprobar_celular', 'id': c.pk, 'descripcion': 'Juan'})
        self.client.logout()
        return c

    def test_pendiente_aprobado_y_revocado(self):
        codigo, datos = self.autorizar()
        self.assertEqual((codigo, datos['estado']), (403, 'PENDIENTE'))
        c = DispositivoApp.objects.get(codigo=self.celular.codigo)
        self.assertEqual((datos['codigo'], c.modelo, c.version_app), (c.codigo_corto, 'Pixel 7', '1.1.0'))
        self.assertNotIn('pase', datos)
        r = self.abrir()
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, 'pase vigente', status_code=403)  # mensaje para la app, no para una PC
        self.aprobar()
        codigo, datos = self.autorizar()
        self.assertEqual((codigo, datos['estado']), (200, 'OK'))
        self.assertEqual(self.abrir(datos['pase']).status_code, 200)  # desde una IP que no está en la lista
        c.refresh_from_db()
        self.assertEqual((c.estado, c.descripcion, c.aprobado_por), ('APROBADO', 'Juan', self.admin))
        # revocado: el pase ya entregado deja de valer
        self.client.force_login(self.admin)
        self.client.post(reverse('acceso_ip'), {'accion': 'revocar_celular', 'id': c.pk})
        self.client.logout()
        self.assertEqual(self.abrir(datos['pase']).status_code, 403)
        self.assertEqual(self.autorizar()[1]['estado'], 'REVOCADO')

    def test_version_minima(self):
        self.autorizar()
        self.aprobar()
        pase = self.autorizar()[1]['pase']
        self.client.force_login(self.admin)
        self.client.post(reverse('acceso_ip'), {'accion': 'version_minima', 'version': '1.2.0'})
        self.client.post(reverse('acceso_ip'), {'accion': 'version_minima', 'version': 'abc'})  # no se acepta
        self.client.logout()
        self.assertEqual(ControlIp.actual().version_minima_app, '1.2.0')
        self.assertEqual(self.abrir(pase).status_code, 403)  # el pase de la versión vieja ya no vale
        codigo, datos = self.autorizar(version='1.1.0')
        self.assertEqual((codigo, datos['estado'], datos['minima']), (403, 'ACTUALIZAR', '1.2.0'))
        codigo, datos = self.autorizar(version='1.2.0')
        self.assertEqual(datos['estado'], 'OK')
        self.assertEqual(self.abrir(datos['pase']).status_code, 200)

    def test_firma_hora_y_pase_falsos(self):
        otro = Celular()
        firma_ajena = json.loads(otro.pedido())['firma']
        self.assertEqual(self.autorizar(firma=firma_ajena), (400, {'estado': 'INVALIDO',
                                                                  'mensaje': 'La firma del celular no es válida.'}))
        self.assertEqual(self.autorizar(hora=int(time.time() * 1000) - 3600 * 1000)[1]['estado'], 'HORA')
        self.assertFalse(DispositivoApp.objects.exists())
        # la misma firma no se acepta dos veces
        pedido = self.celular.pedido()
        url = reverse('autorizar_app')
        self.client.post(url, pedido, content_type='application/json', REMOTE_ADDR=AFUERA)
        r = self.client.post(url, pedido, content_type='application/json', REMOTE_ADDR=AFUERA)
        self.assertEqual(r.json()['estado'], 'INVALIDO')
        # un pase inventado o de un celular no aprobado no abre nada
        self.assertEqual(self.abrir('inventado').status_code, 403)
        c = DispositivoApp.objects.get(codigo=self.celular.codigo)
        self.assertEqual(self.abrir(app_movil.emitir_pase(c)).status_code, 403)

    def test_ip_permitida_y_pantalla(self):
        # con la IP en la lista (o el control apagado) la app entra aunque el celular esté pendiente
        r = self.client.post(reverse('autorizar_app'), self.celular.pedido(), content_type='application/json',
                             REMOTE_ADDR='203.0.113.9')
        self.assertEqual(r.json()['estado'], 'OK')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('acceso_ip'))
        self.assertContains(r, DispositivoApp.objects.get().codigo_corto)
        self.assertContains(r, 'Versión mínima de la app')
        # solo POST, y los intentos por IP tienen tope
        self.assertEqual(self.client.get(reverse('autorizar_app')).status_code, 405)
        for _ in range(app_movil.INTENTOS_POR_IP):
            self.client.post(reverse('autorizar_app'), '{}', content_type='application/json', REMOTE_ADDR=AFUERA)
        self.assertEqual(self.autorizar()[0], 429)
