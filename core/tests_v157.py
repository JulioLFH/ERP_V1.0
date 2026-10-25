"""v1.57, checklist v1.56 (correcciones de lo existente): costo de ventas por función = 69 y lo no absorbido aparte
(CON-05), decimales por unidad y costo (PLAT-06), sesión corporativa reconocida sin volver a ingresar (PLAT-07/08),
usuarios del portal fuera de Ajustes › Usuarios (SEG-03), contratos y legajo en el menú (PLA-13), aviso de saldo
negativo en el kardex (MAN-08), almacén con dirección y distrito obligatorios a la vista (LOG-03), trabajadores sin
centro de costo en la campana (PLA-07) y total del comprobante en el portal (PORT-07). Un escenario por clase."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Empresa, Producto, Tercero

D = Decimal


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v157', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)


class CostoDeVentasTest(Base):
    def test_costo_de_ventas_es_la_69_y_lo_no_absorbido_va_aparte(self):
        from contabilidad import reportes
        s = {'701': (D('0'), D('1000')), '691': (D('600'), D('0')), '601': (D('400'), D('0')),
             '611': (D('0'), D('100')), '711': (D('0'), D('250'))}
        with patch('contabilidad.reportes.pcge.funciones', return_value={
                'administracion': ('94',), 'ventas': ('95',), 'financieros': ('97',)}):
            lineas, neta = reportes.estado_resultados('202610', '202610', sumas=s)
        tabla = {n: v for n, v, _ in lineas}
        self.assertEqual(tabla['Costo de ventas'], D('-600'))  # solo la 69
        self.assertEqual(tabla['Producción en proceso y costos de producción no absorbidos'], D('-50'))
        self.assertEqual(tabla['UTILIDAD BRUTA'], D('350'))  # el resultado no cambia: 1000 − 600 − (400 − 100 − 250)


class FormatoCantidadesYCostosTest(Base):
    def test_decimales_por_unidad_y_costo(self):
        from .formato import cantidad, celda, costo
        self.assertEqual((cantidad(D('12.00'), 'NIU'), cantidad(D('12.50'), 'NIU'), cantidad(D('371.6343'), 'KGM')),
                         ('12', '12.50', '371.63'))
        self.assertEqual((costo(D('15.2054'), 'AUTO'), costo(D('0.450000000000000'), 'AUTO'), costo(D('2.908'), '4')),
                         ('15.21', '0.4500', '2.9080'))
        self.assertEqual(celda(D('0.450000000000000'), 'Costo unit.', 'KGM', 'AUTO'), '0.4500')
        self.assertEqual(celda(D('1350'), 'Valor S/', '', 'AUTO'), '1,350.00')
        self.assertEqual(celda(7, 'Días'), 7)
        p = Producto.objects.create(codigo='ZZ-157', nombre='PRUEBA 157', tipo='BIEN', unidad='KGM')
        p.mover_stock(D('3'), 'ingreso', costo=D('0.45'))
        r = self.client.get(reverse('inventario:reporte_masivo', args=['stock_fecha']), {'ver': '1', 'q': 'ZZ-157'})
        self.assertNotContains(r, '0.450000000000000')
        self.assertContains(r, '0.4500')


class SesionCorporativaTest(Base):
    def test_sesion_abierta_antes_se_reconoce_como_corporativa(self):
        from . import grupo
        sesion = self.client.session
        sesion.pop(grupo.SESION, None)  # como una sesión iniciada antes de la v1.49
        sesion.save()
        self.assertContains(self.client.get(reverse('home')), 'Panel del grupo')
        self.assertEqual(self.client.session[grupo.SESION]['username'], 'admin_v157')


class UsuariosDelPortalTest(Base):
    def test_no_aparecen_en_ajustes_usuarios(self):
        from proveedores.models import AccesoProveedor
        portal = User.objects.create_user('prov.insumos.prueba', password='x')
        AccesoProveedor.objects.create(usuario=portal, tercero=Tercero.objects.filter(tipo='PROVEEDOR').first())
        r = self.client.get(reverse('usuarios'))
        self.assertNotContains(r, 'prov.insumos.prueba')
        self.assertEqual(self.client.get(reverse('usuario_editar', args=[portal.pk])).status_code, 404)


class PantallasNuevasTest(Base):
    def test_contratos_kardex_almacen_y_alertas(self):
        from planillas.models import Trabajador
        r = self.client.get(reverse('planillas:contratos'))
        self.assertContains(r, 'Contratos y legajo')
        # v1.83: el menú de contratos y legajo está en el módulo Empleados
        self.assertContains(self.client.get(reverse('planillas:trabajadores')), 'Contratos y legajo (vencimientos)')
        p = Producto.objects.create(codigo='NEG-157', nombre='NEGATIVO', tipo='BIEN', unidad='NIU')
        p.mover_stock(D('5'), 'ingreso', costo=D('1'))
        from core.models import Kardex
        k = Kardex.objects.filter(producto=p).first()
        Kardex.objects.create(producto=p, almacen=k.almacen, fecha=k.fecha, tipo='SALIDA', cantidad=D('9'),
                              costo_unitario=D('1'), saldo=D('-4'), referencia='salida antigua')
        r = self.client.get(reverse('inv_kardex'), {'producto': p.pk, 'desde': '2000-01-01'})
        self.assertContains(r, 'quedó con saldo negativo')
        self.assertContains(r, 'Saldos negativos en el kardex')
        r = self.client.get(reverse('almacen_nuevo'))
        self.assertContains(r, 'Dirección *')
        if Trabajador.objects.filter(fecha_ingreso__isnull=False, centro_costo__isnull=True).exists():
            self.assertIn('Trabajadores sin centro de costo',
                          [a['titulo'] for a in self.client.get(reverse('alertas_json')).json().get('alertas', [])])


class PortalTotalTest(Base):
    def test_total_del_comprobante_y_diferencia_de_redondeo(self):
        from proveedores.models import FacturaProveedor
        f = FacturaProveedor(total=D('21.28'), total_declarado=D('21.30'))
        self.assertEqual((f.total_comprobante, f.diferencia_redondeo), (D('21.30'), D('0.02')))
        self.assertEqual(FacturaProveedor(total=D('10')).total_comprobante, D('10'))
