"""Carga masiva de plan de cuentas y almacenes, y el botón «Importar desde Excel» en las listas de maestros (v1.26.8)."""
import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from contabilidad.models import CuentaContable

from .models import Almacen


def excel(filas):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('carga.xlsx', buf.getvalue())


class CargaMaestrosTest(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('t', 't@t.com', 'x'))

    def cargar(self, tipo, filas):
        url = reverse('carga_masiva')
        r = self.client.post(url, {'tipo': tipo, 'accion': 'validar', 'archivo': excel(filas)})
        self.assertNotContains(r, 'con error')
        return self.client.post(url, {'tipo': tipo, 'accion': 'confirmar'})

    def test_plan_de_cuentas_y_almacenes(self):
        self.cargar('cuentas', [['codigo', 'nombre', 'naturaleza', 'imputable', 'destino_debe', 'destino_haber'],
                                ['634399', 'Mantenimiento de montacargas', '', 'Sí', '941', '7911'],
                                ['759901', 'Otros ingresos varios', '', '', '', '']])
        c = CuentaContable.objects.get(codigo='634399')
        self.assertEqual((c.naturaleza, c.imputable, c.destino_debe.codigo, c.destino_haber.codigo),
                         ('DEUDORA', True, '941', '7911'))
        self.assertEqual(CuentaContable.objects.get(codigo='759901').naturaleza, 'ACREEDORA')  # según la clase
        self.cargar('almacenes', [['codigo', 'nombre', 'direccion', 'ubigeo', 'codigo_sunat'],
                                  ['ATE2', 'Almacén Ate 2', 'Av. Ayllón 123', '150103', '2']])
        a = Almacen.objects.get(codigo='ATE2')
        self.assertEqual((a.ubigeo, a.codigo_sunat, a.uso), ('150103', '0002', ''))

    def test_boton_en_las_listas(self):
        for vista, tipo in (('contabilidad:centros', 'centros_costo'), ('almacenes', 'almacenes'),
                            ('contabilidad:plan', 'cuentas'), ('productos', 'productos')):
            self.assertContains(self.client.get(reverse(vista)), f'?tipo={tipo}')
