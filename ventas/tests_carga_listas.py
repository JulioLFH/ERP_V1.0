"""v1.23.2: carga de listas de precios desde Excel (formato propio o exportado de Odoo)."""
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from core import carga_masiva as cm
from core.models import Producto

from .models import ListaPrecios, PrecioLista

D = Decimal


def excel(filas, encabezados=('lista', 'producto', 'precio', 'desde_cantidad', 'descuento')):
    wb = Workbook()
    ws = wb.active
    ws.append(list(encabezados))
    for f in filas:
        ws.append(list(f))
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('listas.xlsx', buf.getvalue(),
                              content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


class CargaListasTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('ventas', 'v@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        self.lista = ListaPrecios.objects.create(codigo='LP02', nombre='Tomapedido Mayorista Lima (PEN)')
        self.p = Producto.objects.get(codigo='P003')

    def test_valida_y_carga_por_codigo_nombre_y_formato_odoo(self):
        filas, errores = cm.validar(excel([
            ('LP02', 'P003', 12.5, None, None),
            ('Tomapedido Mayorista Lima (PEN)', f'[P003] {self.p.nombre}', 11, 10, None),  # tramo desde 10
            ('Horeca Norte', 'P003', None, None, 5),  # lista nueva, solo descuento
            ('LP02', 'NOEXISTE', 3, None, None),
            ('LP02', 'P003', None, None, None)]), 'listas_precios')
        self.assertEqual(errores, 2)
        self.assertIn('no existe', filas[3]['error'])
        self.assertIn('precio o el descuento', filas[4]['error'])
        validas = [f for f in filas if not f.get('error')]
        self.assertEqual(validas[2]['accion'], 'Nuevo (crea la lista)')
        resumen = cm.cargar('listas_precios', validas, self.user)
        self.assertIn('3 precios nuevos', resumen)
        self.assertEqual(self.p.precio_para(self.lista, D('1'))[0], D('12.5'))
        self.assertEqual(self.p.precio_para(self.lista, D('20'))[0], D('11'))
        horeca = ListaPrecios.objects.get(nombre='Horeca Norte')
        self.assertTrue(horeca.codigo.startswith('LP'))
        # volver a cargar lo mismo exige "Actualizar existentes"
        filas, errores = cm.validar(excel([('LP02', 'P003', 13, None, None)]), 'listas_precios')
        self.assertEqual(errores, 1)
        filas, errores = cm.validar(excel([('LP02', 'P003', 13, None, None)]), 'listas_precios', actualizar=True)
        cm.cargar('listas_precios', filas, self.user)
        self.assertEqual(PrecioLista.objects.get(lista=self.lista, producto=self.p, cantidad_minima=1).precio, D('13'))

    def test_pantalla_y_plantilla(self):
        url = reverse('carga_masiva')
        self.assertEqual(self.client.get(url + '?tipo=listas_precios&plantilla=1').status_code, 200)
        r = self.client.post(url, {'tipo': 'listas_precios', 'accion': 'validar',
                                   'archivo': excel([('LP02', 'P003', 9, None, None)])})
        self.assertContains(r, 'Confirmar carga de 1 fila')
        self.client.post(url, {'tipo': 'listas_precios', 'accion': 'confirmar'})
        self.assertEqual(self.lista.precios.get().precio, D('9'))
        self.assertNotContains(self.client.get(reverse('ventas:listas_precios')), 'sin precios: al vender')
