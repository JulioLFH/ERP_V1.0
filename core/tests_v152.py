"""v1.52: la ficha de la empresa trae de SUNAT (padrón RUC vía Decolecta) la razón social, la dirección y el ubigeo
al escribir el RUC; dirección y correo son obligatorios y el RUC se valida. Decolecta se simula. Un escenario por
clase."""
import json
import urllib.error
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Empresa

D = Decimal
RUC = '20100070970'


def respuesta_decolecta(**extra):
    datos = {'razon_social': 'SUPERMERCADOS PERUANOS SOCIEDAD ANONIMA', 'numero_documento': RUC, 'estado': 'ACTIVO',
             'condicion': 'HABIDO', 'direccion': 'CAL.  MORELLI   NRO. 181  INT. P-2', 'ubigeo': '150131',
             'distrito': 'SAN BORJA', 'provincia': 'LIMA', 'departamento': 'LIMA', 'es_agente_retencion': True,
             'es_buen_contribuyente': False, 'tipo': 'SOCIEDAD ANONIMA', 'actividad_economica':
             'VENTA AL POR MENOR EN COMERCIOS NO ESPECIALIZADOS', 'numero_trabajadores': '15000',
             'tipo_facturacion': 'COMPUTARIZADO', 'tipo_contabilidad': 'COMPUTARIZADO',
             'comercio_exterior': 'IMPORTADOR/EXPORTADOR', 'locales_anexos': [
                 {'direccion': 'AV. AREQUIPA 123', 'distrito': 'LINCE'}], **extra}
    resp = MagicMock()
    resp.read.return_value = json.dumps(datos).encode()
    resp.__enter__.return_value = resp
    return resp


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v152', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin)


class ConsultaRucTest(Base):
    def test_trae_razon_social_direccion_y_ubigeo(self):
        with patch('core.padron_ruc.urllib.request.urlopen', return_value=respuesta_decolecta()) as abrir:
            r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'tok-escrito'}).json()
        self.assertTrue(r['ok'])
        self.assertEqual(r['razon_social'], 'SUPERMERCADOS PERUANOS SOCIEDAD ANONIMA')
        self.assertEqual(r['direccion'], 'CAL. MORELLI NRO. 181 INT. P-2')
        self.assertEqual((r['ubigeo'], r['alerta']), ('150131', ''))
        self.assertEqual((r['distrito'], r['tipo'], r['tipo_facturacion']), ('SAN BORJA', 'SOCIEDAD ANONIMA',
                                                                           'COMPUTARIZADO'))
        self.assertEqual(r['actividad_economica'], 'VENTA AL POR MENOR EN COMERCIOS NO ESPECIALIZADOS')
        self.assertEqual((r['agente_retencion'], r['buen_contribuyente']), (True, False))
        self.assertEqual((r['locales_anexos'], r['n_locales']), (['AV. AREQUIPA 123 LINCE'], 1))
        pedido = abrir.call_args[0][0]
        self.assertIn(f'/sunat/ruc/full?numero={RUC}', pedido.full_url)
        self.assertEqual(pedido.get_header('Authorization'), 'Bearer tok-escrito')  # el token no va en la URL
        self.assertNotIn('tok-escrito', pedido.full_url)
        # se guarda en caché: no se repite la consulta en el día
        with patch('core.padron_ruc.urllib.request.urlopen') as abrir:
            self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'tok-escrito'})
        abrir.assert_not_called()
        self.assertEqual(self.client.get(reverse('consulta_ruc')).status_code, 405)  # solo POST


class AvisosRucTest(Base):
    def test_sin_token_ruc_invalido_no_habido_y_token_malo(self):
        Empresa.objects.update(token_tipo_cambio='')
        r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC}).json()
        self.assertFalse(r['ok'])
        self.assertIn('token de Decolecta', r['error'])
        r = self.client.post(reverse('consulta_ruc'), {'ruc': '20100070971', 'token': 'x'}).json()
        self.assertIn('dígito verificador', r['error'])
        with patch('core.padron_ruc.urllib.request.urlopen',
                   return_value=respuesta_decolecta(estado='BAJA DE OFICIO', condicion='NO HABIDO')):
            r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'x'}).json()
        self.assertEqual(r['alerta'], 'RUC BAJA DE OFICIO · NO HABIDO en SUNAT')
        cache.clear()
        with patch('core.padron_ruc.urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError('u', 401, 'no', {}, None)):
            r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'malo'}).json()
        self.assertIn('token de Decolecta no es válido', r['error'])
        # el plan no incluye la consulta completa: se usa la básica
        cache.clear()
        with patch('core.padron_ruc.urllib.request.urlopen', side_effect=[
                urllib.error.HTTPError('u', 403, 'plan', {}, None), respuesta_decolecta()]) as abrir:
            r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'x'}).json()
        self.assertTrue(r['ok'])
        self.assertIn('/sunat/ruc?numero=', abrir.call_args_list[1][0][0].full_url)


class ClienteProveedorTest(Base):
    def test_boton_en_contactos_y_aviso_de_ruc_ya_registrado(self):
        from .models import Tercero
        r = self.client.get(reverse('tercero_nuevo') + '?tipo=PROVEEDOR')
        self.assertContains(r, 'consulta_ruc.js')
        self.assertContains(r, "tipoDoc: 'id_tipo_doc'")
        existente = Tercero.objects.create(numero_doc=RUC, nombre='YA REGISTRADO SAC', tipo='PROVEEDOR', tipo_doc='6')
        with patch('core.padron_ruc.urllib.request.urlopen', return_value=respuesta_decolecta()):
            r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'x'}).json()
        self.assertEqual(r['registrado']['nombre'], 'YA REGISTRADO SAC')
        self.assertEqual(r['registrado']['url'], reverse('tercero_editar', args=[existente.pk]))
        # al editar ese mismo registro no se avisa; desde la ficha de la empresa tampoco
        r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'x', 'excluir': existente.pk}).json()
        self.assertNotIn('registrado', r)
        r = self.client.post(reverse('consulta_ruc'), {'ruc': RUC, 'token': 'x', 'contexto': 'empresa'}).json()
        self.assertNotIn('registrado', r)
        self.assertContains(self.client.get(reverse('tercero_editar', args=[existente.pk])), 'consulta_ruc.js')


class FichaEmpresaTest(Base):
    def test_direccion_y_correo_obligatorios_y_ruc_validado(self):
        r = self.client.get(reverse('empresa'))
        self.assertContains(r, 'consulta_ruc.js')
        self.assertContains(r, "token: 'id_token_tipo_cambio'")
        e = Empresa.actual()
        datos = {'ruc': '20100070971', 'razon_social': 'MI EMPRESA SAC', 'direccion': '', 'email': '',
                 'igv_tasa': '18.00', 'fuente_tipo_cambio': 'SUNAT', 'dias_gracia': 0,
                 'presupuesto_solped': '', 'tope_diferencias_inventario': '5000', 'tolerancia_cantidad': '1',
                 'tolerancia_precio': '1', 'tolerancia_total': '1', 'plan_contable': e.plan_contable}
        r = self.client.post(reverse('empresa'), datos)
        form = r.context['form']
        self.assertIn('direccion', form.errors)
        self.assertIn('email', form.errors)
        self.assertIn('dígito verificador', form.errors['ruc'][0])
