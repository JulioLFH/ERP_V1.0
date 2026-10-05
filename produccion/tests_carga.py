"""Carga masiva de maestros de costos y manufactura (v1.16, punto 46): centros de beneficio, centros de costo con
jerarquía, puestos, recetas, hojas de ruta y versiones, en ese orden."""
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from contabilidad.models import CentroBeneficio, CentroCosto
from core.models import Producto

from .models import CentroTrabajo, HojaRuta, ListaMateriales, VersionFabricacion
from .servicios import version_para

D = Decimal


def excel(filas):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('carga.xlsx', buf.getvalue())


class CargaMaestrosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def cargar(self, tipo, filas, errores=0):
        url = reverse('carga_masiva')
        r = self.client.post(url, {'tipo': tipo, 'accion': 'validar', 'archivo': excel(filas)})
        if errores:
            self.assertContains(r, f'{errores} con error')
            return r
        self.assertNotContains(r, 'con error')
        return self.client.post(url, {'tipo': tipo, 'accion': 'confirmar'})

    def test_maestros_en_orden(self):
        self.assertEqual(self.client.get(reverse('carga_masiva') + '?tipo=versiones&plantilla=1').status_code, 200)
        self.cargar('centros_beneficio', [['codigo', 'nombre'], ['GAL', 'Galletas']])
        self.cargar('centros_costo', [['codigo', 'nombre', 'tipo', 'padre', 'centro_beneficio'],
                                      ['PL', 'Planta', 'Producción', '', 'GAL'],
                                      ['PL-HOR', 'Hornos', 'Producción', 'PL', '']])
        hornos = CentroCosto.objects.get(codigo='PL-HOR')
        self.assertEqual((hornos.padre.codigo, hornos.beneficio, hornos.tipo),
                         ('PL', CentroBeneficio.objects.get(codigo='GAL'), 'PRODUCCION'))
        self.cargar('puestos', [['codigo', 'nombre', 'costo_hora_mo', 'costo_hora_cif', 'centro_costo', 'turnos'],
                                ['HOR1', 'Horno 1', 20, 30, 'PL-HOR', 2]])
        self.assertEqual(CentroTrabajo.objects.get(codigo='HOR1').turnos, 2)
        pt = Producto.objects.create(nombre='Galleta', clase='PRODUCTO_TERMINADO')
        insumo = Producto.objects.get(codigo='P003')
        self.cargar('recetas', [['producto', 'receta', 'cantidad_base', 'insumo', 'cantidad', 'merma'],
                                [pt.codigo, 'G1', 100, insumo.codigo, 12.5, 2]])
        lista = ListaMateriales.objects.get(producto=pt, codigo='G1')
        self.assertEqual((lista.estado, lista.componentes.get().cantidad), ('APROBADA', D('12.5000')))
        self.cargar('hojas_ruta', [['hoja', 'nombre', 'secuencia', 'puesto', 'descripcion', 'preparacion', 'por_unidad'],
                                   ['R-GAL', 'Horneado', 10, 'HOR1', 'Hornear', 0.5, 0.01],
                                   ['R-GAL', 'Horneado', 20, 'HOR1', 'Empacar', 0, 0.005]])
        self.assertEqual(HojaRuta.objects.get(codigo='R-GAL').operaciones.count(), 2)
        self.cargar('versiones', [['producto', 'version', 'receta', 'hoja', 'lote_desde', 'lote_costeo'],
                                  [pt.codigo, '1', 'G1', 'R-GAL', 0, 100]])
        v = VersionFabricacion.objects.get(producto=pt)
        self.assertEqual(version_para(pt, D('500')), v)

    def test_errores_de_referencia(self):
        self.cargar('centros_costo', [['codigo', 'nombre', 'tipo', 'padre'], ['X1', 'Huérfano', 'Producción', 'NOEXISTE']],
                    errores=1)
        self.cargar('versiones', [['producto', 'version', 'receta'], ['P003', '1', 'NADA']], errores=1)
