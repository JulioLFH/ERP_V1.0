"""PLE 7.1 (070100): TXT del registro de activos fijos (nombre del archivo, 37 campos por fila y valores)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import CuentaContable
from core.models import Empresa

from . import ple
from .models import ActivoFijo, CategoriaActivo

D = Decimal
RUC = '20123456789'


class Ple71Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        Empresa.objects.create(ruc=RUC, razon_social='EMPRESA PLE S.A.C.')
        cls.user = User.objects.create_superuser('ple', 'ple@t.com', 'x')
        cuenta = CuentaContable.objects.get_or_create(codigo='336199', defaults={'nombre': 'Equipo PLE'})[0]
        cls.cat = CategoriaActivo.objects.create(codigo='PLETEST', nombre='Cómputo', cuenta_activo=cuenta,
                                                 tasa_anual=D('25'), vida_util_meses=48)

    def setUp(self):
        self.client.force_login(self.user)

    def descargar(self, anio=2025):
        r = self.client.get(reverse('activos:registro'), {'anio': anio, 'formato': 'txt'})
        self.assertEqual(r.status_code, 200)
        return r, [l for l in r.content.decode('utf-8').split('\r\n') if l]

    def test_sin_activos_indicador_de_contenido_cero(self):
        r, lineas = self.descargar()
        self.assertEqual(lineas, [])
        self.assertIn(f'filename="LE{RUC}20250000070100001011.txt"', r['Content-Disposition'])

    def test_activo_simple_y_baja(self):
        a = ActivoFijo.objects.create(nombre='Servidor | rack', categoria=self.cat, marca='Dell', modelo='R740',
                                      serie='SN123', fecha_adquisicion=date(2024, 1, 15),
                                      fecha_uso=date(2024, 1, 15), valor=D('12000'), vida_util_meses=48,
                                      origen='SALDO_INICIAL', dep_inicial=D('3000'),
                                      dep_inicial_hasta=date(2024, 12, 31))
        b = ActivoFijo.objects.create(nombre='Laptop', categoria=self.cat, fecha_adquisicion=date(2025, 3, 10),
                                      fecha_uso=date(2025, 3, 10), valor=D('1200'), vida_util_meses=12,
                                      origen='OTRO', estado='BAJA', fecha_baja=date(2025, 10, 1))
        r, lineas = self.descargar()
        self.assertIn(f'filename="LE{RUC}20250000070100001111.txt"', r['Content-Disposition'])
        self.assertEqual(len(lineas), 2)
        for l in lineas:
            self.assertTrue(l.endswith('|'))
            self.assertEqual(len(l.split('|')) - 1, len(ple.CAMPOS_7_1))
        self.assertEqual(len(ple.CAMPOS_7_1), 37)
        c = dict(zip(ple.CAMPOS_7_1, lineas[0].split('|')))
        self.assertEqual(c['periodo'], '20250000')
        self.assertEqual((c['cuo'], c['correlativo'], c['codigo_activo']), (a.codigo, 'M1', a.codigo))
        self.assertEqual((c['catalogo'], c['tipo_activo'], c['estado_activo'], c['cuenta']), ('9', '1', '9', '336199'))
        self.assertEqual((c['descripcion'], c['marca'], c['modelo'], c['serie']), ('Servidor rack', 'Dell', 'R740',
                                                                                    'SN123'))
        self.assertEqual((c['saldo_inicial'], c['adquisiciones'], c['retiros']), ('12000.00', '0.00', '0.00'))
        self.assertEqual((c['fecha_adquisicion'], c['fecha_uso'], c['metodo']), ('15/01/2024', '15/01/2024', '1'))
        self.assertEqual((c['porcentaje'], c['dep_anterior'], c['dep_ejercicio']), ('25.00', '3000.00', '0.00'))
        self.assertEqual(c['estado'], '1')
        c = dict(zip(ple.CAMPOS_7_1, lineas[1].split('|')))
        self.assertEqual((c['cuo'], c['marca'], c['modelo'], c['serie']), (b.codigo, '-', '-', '-'))
        self.assertEqual((c['saldo_inicial'], c['adquisiciones'], c['retiros']), ('0.00', '1200.00', '-1200.00'))
        self.assertEqual(c['porcentaje'], '100.00')

    def test_boton_txt_en_el_registro(self):
        r = self.client.get(reverse('activos:registro'), {'anio': 2025})
        self.assertContains(r, 'formato=txt')
