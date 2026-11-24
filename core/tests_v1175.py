"""v1.117.5: «Nuevo cliente» / «Nuevo proveedor» desde el documento (factura, cotización, factura de compra, OC): la ficha
se abre en otra ventana y, al guardarla, le pasa el contacto nuevo al selector del documento (no se pierde lo llenado)."""
import json

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import PerfilUsuario, Tercero
from core.modulos import GRUPOS


class AltaEnVentanaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('t1175', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_documentos_ofrecen_el_alta_en_otra_ventana(self):
        for ruta, tipo in (('ventas:nuevo', 'CLIENTE'), ('ventas:cot_nuevo', 'CLIENTE'), ('compras:nuevo', 'PROVEEDOR'),
                           ('compras:oc_nuevo', 'PROVEEDOR')):
            html = self.client.get(reverse(ruta)).content.decode()
            self.assertIn(f'data-alta="{tipo}"', html, ruta)
            self.assertIn(f'window.ERP_ALTA_TERCERO_URL = "{reverse("tercero_nuevo")}"', html, ruta)

    def test_sin_permiso_de_contactos_no_se_ofrece(self):
        u = User.objects.create_user('vende1175', password='x')
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=['ventas.emitir'])
        self.client.force_login(u)
        html = self.client.get(reverse('ventas:nuevo')).content.decode()
        self.assertIn('data-alta="CLIENTE"', html)
        self.assertNotIn('ERP_ALTA_TERCERO_URL', html)

    def test_guardar_en_la_ventana_devuelve_el_contacto(self):
        url = reverse('tercero_nuevo') + '?popup=1&tipo=PROVEEDOR&campo=id_tercero'
        r = self.client.get(url)
        self.assertContains(r, 'en-ventana')
        self.assertContains(r, 'value="PROVEEDOR" selected')
        r = self.client.post(url, {'tipo': 'PROVEEDOR', 'tipo_doc': '6', 'numero_doc': '20617171717',
                                   'nombre': 'PROVEEDOR EN VENTANA SAC', 'dias_credito': '0', 'activo': 'on'})
        self.assertEqual(r.status_code, 200, r.context['form'].errors if r.context and 'form' in r.context else '')
        t = Tercero.objects.get(numero_doc='20617171717')
        self.assertEqual((t.tipo, t.nombre), ('PROVEEDOR', 'PROVEEDOR EN VENTANA SAC'))
        datos = json.loads(r.content.decode().split('id="datos-alta" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(datos, {'tipo': 'ceiba:tercero', 'id': t.pk, 'texto': '20617171717 - PROVEEDOR EN VENTANA SAC',
                                 'campo': 'id_tercero'})
        self.assertContains(r, 'postMessage(datos, window.location.origin)')
        self.assertNotIn('_messages', self.client.cookies)  # no deja el aviso «guardado» para la página del documento

    def test_campo_invalido_no_se_devuelve(self):
        r = self.client.post(reverse('tercero_nuevo') + '?popup=1&campo=x"><script>',
                             {'tipo': 'CLIENTE', 'tipo_doc': '6', 'numero_doc': '20617171725', 'nombre': 'CLI VENTANA',
                              'dias_credito': '0', 'activo': 'on'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('"campo": ""', r.content.decode())

    def test_sin_ventana_sigue_igual(self):
        r = self.client.post(reverse('tercero_nuevo'), {'tipo': 'CLIENTE', 'tipo_doc': '6', 'numero_doc': '20617171725',
                                                        'nombre': 'CLI NORMAL', 'dias_credito': '0', 'activo': 'on'})
        self.assertRedirects(r, reverse('terceros'), fetch_redirect_response=False)
