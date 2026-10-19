"""PCGE 2026: una empresa adopta el plan 2026 (adoptar_pcge2026) y la centralización usa sus cuentas."""
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db.models import Sum
from django.test import TestCase

from compras.models import Compra
from core.models import Empresa, Producto
from finanzas.models import Movimiento
from ventas.models import Venta

from . import pcge, reportes
from .centralizar import centralizar_periodo
from .models import Asiento, AsientoLinea, CentroCosto, CuentaContable, CuentaDefecto

TC = (Decimal('3.441'), Decimal('3.450'))


def lineas(asiento, codigo):
    return asiento.lineas.filter(cuenta__codigo=codigo)


class AdopcionPCGE2026Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
            call_command('adoptar_pcge2026', '--confirmar', stdout=StringIO())

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=TC)
        p.start()
        self.addCleanup(p.stop)
        self.periodos = sorted(set(Compra.objects.values_list('periodo', flat=True)) |
                               set(Venta.objects.values_list('periodo', flat=True)) |
                               {f.strftime('%Y%m') for f in Movimiento.objects.dates('fecha', 'month')})
        for p in self.periodos:
            r = centralizar_periodo(p)
            self.assertFalse([e for e in r['errores'] if 'no cuadra' in e], r['errores'])

    def test_plan_y_catalogo(self):
        self.assertEqual(Empresa.actual().plan_contable, pcge.PCGE2026)
        self.assertEqual(pcge.plan(), pcge.PCGE2026)
        c = CuentaContable.objects.get(codigo='7041')
        self.assertEqual(c.nombre, 'Subproductos')  # en el 2026 la 7041 ya no es prestación de servicios
        self.assertFalse(c.imputable)
        self.assertTrue(CuentaContable.objects.filter(codigo='655141', imputable=True).exists())
        self.assertFalse(CuentaContable.objects.filter(codigo__in=['941', '7911', '1231', '2811']).exists())
        self.assertEqual(CuentaContable.objects.get(codigo='36').naturaleza, 'ACREEDORA')
        for d in CuentaDefecto.objects.select_related('cuenta'):
            self.assertTrue(d.cuenta.imputable, d.clave)
        mapa = CuentaDefecto.mapa()
        self.assertEqual((mapa['ventas_bienes'].codigo, mapa['ventas_servicios'].codigo, mapa['costo_ventas'].codigo,
                          mapa['letras_cobrar'].codigo, mapa['baja_activo'].codigo),
                         ('70121', '70321', '69121', '1232', '655141'))

    def test_productos_y_activos(self):
        p = Producto.objects.get(codigo='S001')
        self.assertEqual(p.cuenta_venta.codigo, '70321')
        nuevo = Producto.objects.create(nombre='Producto 2026', clase='PRODUCTO_TERMINADO')
        self.assertEqual((nuevo.cuenta_existencias.codigo, nuevo.cuenta_venta.codigo, nuevo.cuenta_costo.codigo),
                         ('21111', '70221', '69221'))
        from activos.models import CategoriaActivo
        edi = CategoriaActivo.objects.get(codigo='EDI')
        self.assertEqual((edi.cuenta_activo.codigo, edi.cuenta_depreciacion.codigo, edi.cuenta_gasto.codigo),
                         ('33211', '39512', '68312'))
        sof = CategoriaActivo.objects.get(codigo='SOF')
        self.assertEqual((sof.cuenta_activo.codigo, sof.cuenta_depreciacion.codigo, sof.cuenta_gasto.codigo),
                         ('34311', '39613', '68613'))

    def test_asientos_con_cuentas_2026(self):
        self.assertTrue(Asiento.objects.exists())
        for a in Asiento.objects.all():
            self.assertTrue(a.cuadrado, f'{a} no cuadra')
        v = Venta.objects.filter(tipo_comprobante='01').first()
        a = v.asientos.get()
        self.assertEqual(lineas(a, '1212').get().debe, v.total)
        self.assertEqual(a.lineas.filter(cuenta__codigo__startswith='70').aggregate(h=Sum('haber'))['h'],
                         v.base_imponible)
        if v.items.filter(producto__tipo='SERVICIO').exists():
            self.assertTrue(lineas(a, '70321').exists())
        c = Compra.objects.filter(clasificacion='MERCADERIA').first()
        a = c.asientos.get()
        self.assertEqual(lineas(a, '6011').get().debe, c.base_imponible)
        self.assertEqual(lineas(a, '281').get().debe, c.base_imponible)
        self.assertEqual(lineas(a, '6111').get().haber, c.base_imponible)
        s = Compra.objects.get(clasificacion='SERVICIO').asientos.get()
        self.assertTrue(lineas(s, '6399').exists())
        self.assertTrue(lineas(s, '912').exists() and lineas(s, '791').exists())
        self.assertFalse(AsientoLinea.objects.filter(cuenta__imputable=False).exists())
        cobro = Movimiento.objects.filter(concepto='COBRANZA', cuenta__tipo='CAJA').first()
        if cobro:
            self.assertTrue(cobro.cuenta.cuenta_contable.codigo.startswith('101'))

    def test_estados_cuadran_y_gastos_por_funcion(self):
        desde, hasta = self.periodos[0], self.periodos[-1]
        filas, tot = reportes.balance_comprobacion(desde, hasta)
        self.assertEqual(tot['d'], tot['h'])
        self.assertEqual(tot['res_bal'], tot['res_nat'])
        self.assertEqual(tot['res_bal'], tot['res_fun'])
        lineas_er, neta = reportes.estado_resultados(desde, hasta)
        self.assertEqual(neta, tot['res_fun'])
        er = {n: v for n, v, _ in lineas_er}
        self.assertLess(er['Gastos de administración'], 0)  # los gastos con destino 912
        self.assertNotIn('Gastos sin destino asignado', er)
        self.assertEqual(reportes.situacion_financiera(hasta, desde)['t']['diferencia'], 0)

    def test_centro_de_ventas_va_a_913(self):
        centro = CentroCosto.objects.create(codigo='VTA26', nombre='Ventas', tipo='VENTAS')
        from .centralizar import _destino_por_centro
        adm = CuentaContable.objects.get(codigo='912')
        self.assertEqual(_destino_por_centro(adm, centro, pcge.PCGE2026).codigo, '913')
        fin = CuentaContable.objects.get(codigo='931')
        self.assertEqual(_destino_por_centro(fin, centro, pcge.PCGE2026).codigo, '931')


class AdopcionRestriccionesTest(TestCase):
    def test_simulacion_no_graba(self):
        salida = StringIO()
        call_command('adoptar_pcge2026', stdout=salida)
        self.assertIn('Simulación', salida.getvalue())
        self.assertEqual(Empresa.actual().plan_contable, pcge.PCGE2019)
        self.assertTrue(CuentaContable.objects.filter(codigo='941').exists())
        self.assertEqual(CuentaDefecto.mapa()['ventas_bienes'].codigo, '70111')

    def test_no_adopta_con_asientos_manuales(self):
        from datetime import date
        a = Asiento.objects.create(fecha=date(2026, 1, 31), libro='05', origen='MANUAL', glosa='Manual')
        AsientoLinea.objects.create(asiento=a, cuenta=CuentaContable.objects.get(codigo='5911'), haber=Decimal('1'))
        with self.assertRaises(CommandError):
            call_command('adoptar_pcge2026', '--confirmar', stdout=StringIO())
        self.assertEqual(Empresa.actual().plan_contable, pcge.PCGE2019)
