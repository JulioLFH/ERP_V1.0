"""PLE 12.1 (120100) y 13.1 (130100): TXT del inventario permanente (nombre, campos por fila y valores)."""
from decimal import Decimal
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import libros_inventario as li
from .models import Almacen, Empresa, Kardex, Producto

D = Decimal
RUC = '20123456789'


class PleInventarioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Empresa.objects.create(ruc=RUC, razon_social='EMPRESA PLE S.A.C.')
        cls.user = User.objects.create_superuser('ple', 'ple@t.com', 'x')
        cls.almacen = Almacen.objects.create(codigo='A1', nombre='Principal', codigo_sunat='0001')
        cls.p = Producto.objects.create(codigo='P001', nombre='Arroz | 50 kg', unidad='NIU')

        def k(fecha, tipo, cant, cu, saldo, cp, ref, sunat):
            Kardex.objects.create(producto=cls.p, almacen=cls.almacen, fecha=fecha, tipo=tipo, cantidad=D(cant),
                                  costo_unitario=D(cu), saldo=D(saldo), costo_promedio=D(cp), referencia=ref,
                                  codigo_sunat=sunat)
        k(date(2025, 4, 20), 'ENTRADA', '10', '5', '10', '5', 'Inventario inicial', '16')
        k(date(2025, 5, 6), 'ENTRADA', '5', '8', '15', '6', 'Factura F001-00000081', '02')
        k(date(2025, 5, 9), 'SALIDA', '3', '6', '12', '6', 'Boleta de venta B001-00000003', '01')

    def setUp(self):
        self.client.force_login(self.user)

    def descargar(self, formato, mes='5'):
        r = self.client.get(reverse('inv_libro_sunat'), {'formato_sunat': formato, 'mes': mes, 'anio': 2025,
                                                         'formato': 'txt'})
        self.assertEqual(r.status_code, 200)
        return r, [l for l in r.content.decode('utf-8').split('\r\n') if l]

    def test_12_1_unidades_fisicas(self):
        r, lineas = self.descargar('12.1')
        self.assertIn(f'filename="LE{RUC}20250500120100001111.txt"', r['Content-Disposition'])
        self.assertEqual(len(li.CAMPOS_12_1), 19)
        self.assertEqual(len(lineas), 3)  # saldo inicial + entrada + salida
        for l in lineas:
            self.assertEqual(len(l.split('|')) - 1, 19)
        ini, ent, sal = [dict(zip(li.CAMPOS_12_1, l.split('|'))) for l in lineas]
        self.assertEqual(ini['periodo'], '20250500')
        self.assertEqual((ini['cuo'], ini['correlativo'], ini['establecimiento']), (f'SI{self.p.pk}', 'A1', '0000'))
        self.assertEqual((ini['fecha'], ini['tipo_doc'], ini['operacion'], ini['ent_cant'], ini['sal_cant']),
                         ('01/05/2025', '00', '16', '10.00', '0.00'))
        self.assertEqual((ent['catalogo'], ent['tipo_existencia'], ent['codigo'], ent['establecimiento']),
                         ('9', '01', 'P001', '0001'))
        self.assertEqual((ent['fecha'], ent['tipo_doc'], ent['serie'], ent['numero'], ent['operacion']),
                         ('06/05/2025', '01', 'F001', '00000081', '02'))
        self.assertEqual((ent['descripcion'], ent['unidad'], ent['ent_cant'], ent['sal_cant'], ent['estado']),
                         ('Arroz 50 kg', 'NIU', '5.00', '0.00', '1'))
        self.assertEqual((sal['tipo_doc'], sal['serie'], sal['numero'], sal['operacion']),
                         ('03', 'B001', '00000003', '01'))
        self.assertEqual((sal['ent_cant'], sal['sal_cant']), ('0.00', '3.00'))

    def test_13_1_valorizado(self):
        r, lineas = self.descargar('13.1')
        self.assertIn(f'filename="LE{RUC}20250500130100001111.txt"', r['Content-Disposition'])
        self.assertEqual(len(li.CAMPOS_13_1), 27)
        for l in lineas:
            self.assertEqual(len(l.split('|')) - 1, 27)
        ini, ent, sal = [dict(zip(li.CAMPOS_13_1, l.split('|'))) for l in lineas]
        self.assertEqual(ent['metodo'], '1')
        self.assertEqual([ini[c] for c in ('ent_cant', 'ent_costo', 'ent_total', 'saldo_cant', 'saldo_total')],
                         ['10.00', '5.00', '50.00', '10.00', '50.00'])
        self.assertEqual([ent[c] for c in ('ent_cant', 'ent_costo', 'ent_total', 'sal_cant', 'sal_total',
                                            'saldo_cant', 'saldo_costo', 'saldo_total')],
                         ['5.00', '8.00', '40.00', '0.00', '0.00', '15.00', '6.00', '90.00'])
        self.assertEqual([sal[c] for c in ('ent_cant', 'ent_total', 'sal_cant', 'sal_costo', 'sal_total',
                                            'saldo_cant', 'saldo_total')],
                         ['0.00', '0.00', '3.00', '6.00', '18.00', '12.00', '72.00'])

    def test_mes_sin_datos_y_anual(self):
        r, lineas = self.descargar('12.1', mes='1')
        self.assertEqual(lineas, [])
        self.assertIn(f'filename="LE{RUC}20250100120100001011.txt"', r['Content-Disposition'])
        r = self.client.get(reverse('inv_libro_sunat'), {'formato_sunat': '12.1', 'mes': 'anual', 'anio': 2025,
                                                         'formato': 'txt'})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('formato=txt', r['Location'])

    def test_boton_txt(self):
        r = self.client.get(reverse('inv_libro_sunat'), {'formato_sunat': '13.1', 'mes': '5', 'anio': 2025})
        self.assertContains(r, 'formato_sunat=13.1&mes=5&anio=2025&formato=txt')
