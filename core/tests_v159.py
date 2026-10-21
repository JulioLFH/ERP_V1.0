"""v1.59, checklist v1.56 (carga masiva): perfiles de acceso y usuarios desde Excel (CM-01), productos con la ficha
completa y avisos (CM-02) y operaciones permitidas por almacén validadas en cada movimiento (CM-03). Un escenario
por clase."""
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from . import carga_masiva as cm
from .models import Almacen, PerfilAcceso, PerfilUsuario, Producto

D = Decimal


def excel(filas):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('carga.xlsx', buf.getvalue())


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v159', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def cargar(self, tipo, filas, actualizar=False):
        resultado, errores = cm.validar(excel(filas), tipo, actualizar)
        self.assertEqual(errores, 0, [r.get('error') for r in resultado if r.get('error')])
        return cm.cargar(tipo, resultado, self.admin), resultado


class PerfilesYUsuariosTest(Base):
    def test_matriz_de_perfiles_y_usuarios(self):
        texto, _ = self.cargar('perfiles', [
            ['Perfil', 'Módulos', 'Acciones', 'Pantalla', 'Nivel', 'Ve costos'],
            ['Asistente de compras', 'compras, Inventario', 'compras.registrar, compras.oc', 'compras:oc_lista',
             'Solo lectura', 'No'],
            ['Asistente de compras', '', '', 'compras:importar_xml', 'Oculta', ''],
        ])
        self.assertIn('1 perfil', texto)
        perfil = PerfilAcceso.objects.get(nombre='Asistente de compras')
        self.assertEqual(sorted(perfil.modulos), ['compras', 'inventario'])
        self.assertEqual(perfil.pantallas, {'compras:oc_lista': 'LECTURA', 'compras:importar_xml': 'OCULTA'})
        self.assertFalse(perfil.ver_costos)
        alm = Almacen.principal()
        texto, _ = self.cargar('usuarios', [
            ['Usuario', 'Nombres y apellidos', 'Correo', 'Perfil de acceso', 'Almacenes (códigos)',
             'Contraseña inicial'],
            ['jperez', 'Juan Pérez Ruiz', 'jperez@empresa.pe', 'Asistente de compras', alm.codigo,
             'Clave-segura-9'],
            ['mlopez', 'María López', '', 'asistente de compras', '', ''],
        ])
        self.assertIn('2 nuevos', texto)
        u = User.objects.get(username='jperez')
        self.assertTrue(u.check_password('Clave-segura-9'))
        self.assertEqual((u.first_name, u.last_name), ('Juan', 'Pérez Ruiz'))
        pu = PerfilUsuario.objects.get(usuario=u)
        self.assertEqual(pu.perfil_acceso, perfil)
        self.assertEqual(list(pu.almacenes.values_list('pk', flat=True)), [alm.pk])
        self.assertIn('Compras', set(u.groups.values_list('name', flat=True)))
        self.assertFalse(User.objects.get(username='mlopez').has_usable_password())  # entra con «olvidé»
        # errores claros: perfil inexistente, usuario repetido sin «actualizar», contraseña débil
        resultado, errores = cm.validar(excel([['Usuario', 'Perfil de acceso', 'Contraseña inicial'],
                                               ['otro', 'No existe', ''], ['jperez', 'Asistente de compras', ''],
                                               ['nuevo2', 'Asistente de compras', '123']]), 'usuarios')
        self.assertEqual(errores, 3)
        textos = ' '.join(r['error'] for r in resultado)
        self.assertIn('no existe', textos)
        self.assertIn('ya existe', textos)
        self.assertIn('Contraseña inicial', textos)


class ProductosFichaCompletaTest(Base):
    def test_columnas_nuevas_y_avisos(self):
        from contabilidad.models import CuentaContable
        venta = CuentaContable.objects.filter(codigo__startswith='70', imputable=True).first()
        texto, resultado = self.cargar('productos', [
            ['Código', 'Nombre', 'Tipo de producto', 'Unidad', 'Unidad de compra', 'Unidades por unidad de compra',
             'Control', 'Se vende', 'Afectación del IGV', 'Cuenta de venta (excepción)', 'Peso bruto por unidad (kg)',
             'Precio de compra sin IGV'],
            ['GA-159', 'Gaseosa 500 ml', 'Mercadería', 'NIU', 'BX', '12', 'Lote', 'Sí', 'Exonerada', venta.codigo,
             '0.55', '2.40'],
            ['GA-160', 'Agua 625 ml', 'Mercadería', 'NIU', '', '', '', 'No', '', '', '', ''],
        ])
        p = Producto.objects.get(codigo='GA-159')
        self.assertEqual((p.unidad_compra, p.factor_compra, p.control, p.afectacion_igv, p.cuenta_venta_id, p.peso),
                         ('BX', D('12.0000'), 'LOTE', 'EXONERADA', venta.pk, D('0.550')))
        self.assertFalse(Producto.objects.get(codigo='GA-160').puede_venderse)
        aviso = next(r['resumen'] for r in resultado if r['codigo'] == 'GA-160')
        self.assertIn('sin precio de compra', aviso)
        resultado, errores = cm.validar(excel([['Nombre', 'Tipo de producto', 'Cuenta de existencias (excepción)'],
                                               ['Mal', 'Mercadería', '1011']]), 'productos')
        self.assertEqual(errores, 1)  # «101 Caja» no es una cuenta de existencias


class OperacionesPorAlmacenTest(Base):
    def test_almacen_que_no_entrega_ventas(self):
        from inventario.cierre import KardexCerrado
        self.cargar('almacenes', [
            ['Código', 'Nombre', 'Dirección', 'Ubigeo', 'Operaciones permitidas', 'Responsables (usuarios)'],
            ['MP9', 'Materias primas 9', 'Av. 1', '150101', 'Recibe compras, Traslados, Consumo interno',
             'admin_v159'],
        ])
        a = Almacen.objects.get(codigo='MP9')
        self.assertEqual(sorted(a.operaciones), ['COMPRAS', 'CONSUMO', 'TRASLADOS'])
        self.assertEqual(list(a.responsables.values_list('username', flat=True)), ['admin_v159'])
        p = Producto.objects.create(codigo='ZZ-159', nombre='Insumo 159', clase='MATERIA_PRIMA')
        p.mover_stock(D('10'), 'F001-1', costo=D('1'), almacen=a, origen='COMPRA')  # permitido
        with self.assertRaisesMessage(KardexCerrado, 'no permite esta operación (entrega ventas)'):
            p.mover_stock(D('-2'), 'F001-9', almacen=a, origen='VENTA')
        p.mover_stock(D('-2'), 'Reversión F001-1', almacen=a, origen='COMPRA')  # las reversiones siempre pasan
