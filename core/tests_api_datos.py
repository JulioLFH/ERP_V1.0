"""API v1 de datos para exportar (v1.26): contabilidad, kardex, tesorería, cuentas por pagar, producción, filtros de
fecha y formato CSV."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento

from .models import ApiToken
from .modulos import GRUPOS

D = Decimal
HOY = date.today()


class ApiDatosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
            centralizar_periodo(HOY.strftime('%Y%m'))
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')
        cls.almacenero = User.objects.create_user('alm', 'a@e.pe', 'x')
        cls.almacenero.groups.add(Group.objects.get_or_create(name=GRUPOS['inventario'])[0])

    def setUp(self):
        cache.clear()
        _, self.clave = ApiToken.crear(self.admin, 'BI')
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def get(self, url, clave=None, **params):
        return self.client.get(url, params, HTTP_AUTHORIZATION=f'Bearer {clave or self.clave}')

    def test_contabilidad(self):
        a = Asiento.objects.filter(periodo=HOY.strftime('%Y%m')).first()
        self.assertIsNotNone(a)
        r = self.get('/api/v1/contabilidad/asientos/', desde=HOY.replace(day=1).isoformat(), hasta=HOY.isoformat(),
                     detalle='true')
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()['count'], 0)
        self.assertIn('lineas', r.json()['results'][0])
        detalle = self.get(f'/api/v1/contabilidad/asientos/{a.pk}/').json()
        self.assertEqual(detalle['total_debe'], detalle['total_haber'])
        diario = self.get('/api/v1/contabilidad/libro-diario/', periodo_desde=HOY.strftime('%Y-%m')).json()
        debe = sum(D(l['debe']) for l in diario['results'])
        haber = sum(D(l['haber']) for l in diario['results'])
        if diario['pages'] == 1:
            self.assertEqual(debe, haber)
        self.assertEqual(self.get('/api/v1/contabilidad/cuentas/', q='70').status_code, 200)
        balance = self.get('/api/v1/contabilidad/balance-comprobacion/', nivel='2').json()
        self.assertEqual(balance['totales']['debe'], balance['totales']['haber'])
        er = self.get('/api/v1/contabilidad/estado-resultados/', desde=f'{HOY.year}-01', hasta=HOY.strftime('%Y-%m'),
                      mensual='true').json()
        self.assertEqual(len(er['meses']), HOY.month)
        total = self.get('/api/v1/contabilidad/estado-resultados/', vista='naturaleza').json()
        self.assertEqual(D(total['resultado']), D(next(f for f in er['filas']
                                                       if f['concepto'] == 'RESULTADO DEL EJERCICIO')['total']))
        sf = self.get('/api/v1/contabilidad/situacion-financiera/', libro='TRIBUTARIO').json()
        self.assertEqual(sf['libro'], 'TRIBUTARIO')

    def test_fechas_invalidas_y_csv(self):
        r = self.get('/api/v1/ventas/', desde='31-12-2026')
        self.assertEqual(r.status_code, 400)
        self.assertIn('AAAA-MM-DD', r.json()['error'])
        self.assertEqual(self.get('/api/v1/contabilidad/estado-resultados/', hasta='2026-13').status_code, 400)
        r = self.get('/api/v1/contabilidad/libro-diario/', formato='csv')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'text/csv; charset=utf-8')
        texto = r.content.decode('utf-8-sig')
        self.assertTrue(texto.startswith('id;asiento_id;numero;fecha'))
        r = self.get('/api/v1/ventas/', formato='csv')
        self.assertIn('tercero.nombre', r.content.decode('utf-8-sig').splitlines()[0])

    def test_inventario_tesoreria_compras_y_produccion(self):
        k = self.get('/api/v1/kardex/', hasta=HOY.isoformat(), page_size='1000').json()
        self.assertGreater(k['count'], 0)
        self.assertIn('costo_unitario', k['results'][0])
        al_corte = self.get('/api/v1/stock/', fecha=HOY.isoformat()).json()
        self.assertIn('valor_total', al_corte)
        self.assertEqual(self.get('/api/v1/tesoreria/movimientos/', desde=f'{HOY.year}-01-01').status_code, 200)
        self.assertEqual(self.get('/api/v1/cuentas-por-pagar/').status_code, 200)
        self.assertEqual(self.get('/api/v1/produccion/ordenes/', por='termino').status_code, 200)
        # sin permiso de costos el kardex no muestra costos; sin el módulo, 403
        _, clave = ApiToken.crear(self.almacenero, 'Almacén')
        fila = self.get('/api/v1/kardex/', clave)
        if fila.json()['results']:
            self.assertNotIn('costo_unitario', fila.json()['results'][0])
        self.assertEqual(self.get('/api/v1/contabilidad/asientos/', clave).status_code, 403)

    def test_documentacion(self):
        ruta = self.get('/api/v1/openapi.json').json()['paths']['/api/v1/contabilidad/libro-diario/']['get']
        nombres = {p['name'] for p in ruta['parameters']}
        self.assertTrue({'desde', 'hasta', 'cuenta', 'formato', 'page_size'} <= nombres)
        self.assertEqual(len(self.get('/api/v1/').json()['endpoints']), 24)
