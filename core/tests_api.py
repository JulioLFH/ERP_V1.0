"""API REST v1 con claves por usuario (v1.17, punto 4)."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from ventas.models import Venta

from .models import ApiToken, Empresa, Producto, Tercero
from .modulos import GRUPOS

D = Decimal


class ApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')
        cls.almacenero = User.objects.create_user('alm', 'a@e.pe', 'x')
        cls.almacenero.groups.add(Group.objects.get_or_create(name=GRUPOS['inventario'])[0])

    def setUp(self):
        cache.clear()
        Empresa.objects.update(bloquear_deuda_vencida=False)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def _get(self, url, clave, **params):
        return self.client.get(url, params, HTTP_AUTHORIZATION=f'Bearer {clave}')

    def _post(self, url, clave, datos):
        return self.client.post(url, json.dumps(datos), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {clave}')

    def test_autenticacion_modulos_y_solo_lectura(self):
        self.assertEqual(self.client.get('/api/v1/productos/').status_code, 401)
        self.assertEqual(self._get('/api/v1/productos/', 'ceiba_falsa').status_code, 401)
        token, clave = ApiToken.crear(self.almacenero, 'BI')
        self.assertNotIn(clave, token.clave_hash)  # solo se guarda el hash
        r = self._get('/api/v1/productos/', clave, q='P00', page_size=2)
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(len(datos['results']), 2)
        self.assertIn('precio_venta', datos['results'][0])
        p = Producto.objects.get(codigo='P002')
        self.assertEqual(self._get(f'/api/v1/productos/{p.pk}/', clave).json()['codigo'], 'P002')
        self.assertEqual(self._get('/api/v1/productos/999999/', clave).status_code, 404)
        self.assertEqual(self._get('/api/v1/stock/', clave).status_code, 200)
        self.assertEqual(self._get('/api/v1/ventas/', clave).status_code, 403)  # no tiene el módulo de ventas
        self.assertEqual(self._post('/api/v1/terceros/', clave, {}).status_code, 403)
        # vencida o revocada
        ApiToken.objects.filter(pk=token.pk).update(expira=date.today() - timedelta(days=1))
        self.assertEqual(self._get('/api/v1/', clave).status_code, 401)
        ApiToken.objects.filter(pk=token.pk).update(expira=None, activo=False)
        self.assertEqual(self._get('/api/v1/', clave).status_code, 401)

    def test_crear_cliente_y_emitir_venta(self):
        _, clave = ApiToken.crear(self.admin, 'Tienda', solo_lectura=False)
        r = self._post('/api/v1/terceros/', clave, {'tipo_doc': '1', 'numero_doc': '4567891', 'nombre': 'Ana'})
        self.assertEqual(r.status_code, 422)
        self.assertIn('numero_doc', r.json()['campos'])
        r = self._post('/api/v1/terceros/', clave, {'tipo_doc': '1', 'numero_doc': '45678912', 'nombre': 'Ana Ruiz'})
        self.assertEqual(r.status_code, 201)
        p = Producto.objects.get(codigo='P002')
        r = self._post('/api/v1/ventas/', clave, {
            'tipo_comprobante': '03', 'tercero_numero_doc': '45678912', 'descontar_stock': False,
            'items': [{'codigo': 'P002', 'cantidad': 2, 'descuento_pct': 10},
                      {'descripcion': 'Delivery', 'cantidad': 1, 'precio_unitario': '10.00'}]})
        self.assertEqual(r.status_code, 201, r.content)
        datos = r.json()
        v = Venta.objects.get(pk=datos['id'])
        esperado = (2 * p.precio_venta * D('0.9')).quantize(D('0.01')) + D('10.00')
        self.assertEqual((v.tercero.nombre, v.serie[0], v.base_imponible), ('Ana Ruiz', 'B', esperado))
        self.assertEqual(len(datos['items']), 2)
        self.assertEqual(self._get(f'/api/v1/ventas/{v.pk}/', clave).json()['numero'], v.numero_completo)
        self.assertEqual(self._get('/api/v1/cuentas-por-cobrar/', clave).status_code, 200)
        self.assertEqual(self._get('/api/v1/compras/', clave).status_code, 200)
        # validaciones de la pantalla: factura a un cliente sin RUC
        r = self._post('/api/v1/ventas/', clave, {'tipo_comprobante': '01', 'tercero_numero_doc': '45678912',
                                                  'items': [{'codigo': 'P002', 'cantidad': 1}]})
        self.assertEqual(r.status_code, 422)
        self.assertIn('tercero', r.json()['campos'])

    def test_solo_lectura_limite_y_pantallas(self):
        _, clave = ApiToken.crear(self.admin, 'BI')
        self.assertEqual(self._post('/api/v1/terceros/', clave, {'nombre': 'x'}).status_code, 403)
        cache.clear()  # el POST anterior también cuenta para el límite
        with patch('core.api.LIMITE_MINUTO', 3):
            codigos = [self._get('/api/v1/', clave).status_code for _ in range(4)]
        self.assertEqual(codigos, [200, 200, 200, 429])
        self.client.force_login(self.almacenero)
        r = self.client.post(reverse('api_claves'), {'nombre': 'Power BI', 'solo_lectura': 'on'})
        self.assertContains(r, 'ceiba_')
        token = ApiToken.objects.get(usuario=self.almacenero)
        self.client.post(reverse('api_claves'), {'revocar': token.pk})
        self.assertFalse(ApiToken.objects.get(pk=token.pk).activo)
        self.assertContains(self.client.get(reverse('api:docs')), '/api/v1/ventas/')
        self.assertEqual(self.client.get(reverse('api:openapi')).json()['openapi'], '3.0.3')
