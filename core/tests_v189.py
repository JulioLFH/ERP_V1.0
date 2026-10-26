"""v1.89: control del ingreso por IP (Ajustes › IP permitidas e intentos de ingreso)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core import acceso_ip
from core.models import AccesoIp, ControlIp, IpPermitida

AFUERA, OFICINA = '198.51.100.7', '203.0.113.9'


class ControlIpTest(TestCase):
    def setUp(self):
        acceso_ip._pendientes.clear()
        acceso_ip.invalidar()
        self.addCleanup(acceso_ip.invalidar)
        self.admin = User.objects.create_superuser('admin.ip', 'a@a.pe', 'x')
        self.client.force_login(self.admin)

    def _post(self, desde='127.0.0.1', **datos):
        return self.client.post(reverse('acceso_ip'), datos, REMOTE_ADDR=desde)

    def test_registra_y_bloquea_solo_con_el_control_activo(self):
        # control apagado: entra cualquiera y queda registrado
        self.assertEqual(self.client.get(reverse('login'), REMOTE_ADDR=AFUERA).status_code, 200)
        self.assertEqual(AccesoIp.objects.get(ip=AFUERA).veces, 1)
        # activar desde afuera sin estar en la lista: no lo deja (se quedaría fuera)
        self._post(AFUERA, accion='activar')
        self.assertFalse(ControlIp.actual().activo)
        self._post(accion='agregar', ip='203.0.113.0/24', descripcion='Oficina')
        self._post(accion='activar')
        self.assertTrue(ControlIp.actual().activo)
        acceso_ip._pendientes.clear()
        r = self.client.get(reverse('login'), REMOTE_ADDR=AFUERA)
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, AFUERA, status_code=403)
        self.assertEqual(AccesoIp.objects.get(ip=AFUERA).bloqueos, 1)
        self.assertEqual(self.client.get(reverse('login'), REMOTE_ADDR=OFICINA).status_code, 200)
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)  # esta PC siempre entra
        # el listado muestra la rechazada y «Permitir» la agrega a la lista
        self.assertContains(self.client.get(reverse('acceso_ip')), AFUERA)
        self._post(accion='permitir', ip=AFUERA)
        self.assertTrue(IpPermitida.objects.filter(ip=AFUERA).exists())
        self.assertEqual(self.client.get(reverse('login'), REMOTE_ADDR=AFUERA).status_code, 200)

    def test_no_se_quita_la_propia_ip_ni_entra_una_invalida(self):
        self._post(accion='agregar', ip='abc')
        self.assertFalse(IpPermitida.objects.exists())
        self._post(OFICINA, accion='agregar', ip=OFICINA)
        self._post(OFICINA, accion='activar')
        fila = IpPermitida.objects.get(ip=OFICINA)
        self._post(OFICINA, accion='eliminar', id=fila.pk)
        self.assertTrue(IpPermitida.objects.filter(pk=fila.pk).exists())  # quedaría fuera del ERP
        self._post(accion='eliminar', id=fila.pk)  # desde esta PC sí
        self.assertFalse(IpPermitida.objects.filter(pk=fila.pk).exists())

    def test_solo_administradores(self):
        normal = User.objects.create_user('normal.ip', password='x')
        self.client.force_login(normal)
        self.assertNotEqual(self.client.get(reverse('acceso_ip')).status_code, 200)
