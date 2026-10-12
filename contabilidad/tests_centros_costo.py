"""Gastos por centro de costo (v1.26.4): solo 61 a 68 (la clase 9 aparte), centro obligatorio en 62-65 y 68, sin
centro en cuentas de balance y suministros por el almacén."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from core.models import Producto, Tercero

from .centralizar import centralizar_periodo
from .forms import AsientoLineaForm
from .models import Asiento, AsientoLinea, CentroCosto, CuentaContable
from .pcge import admite_centro, exige_centro

D = Decimal
HOY = date.today()
PERIODO = HOY.strftime('%Y%m')


class CentrosCostoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.mant = CentroCosto.objects.create(codigo='MANT', nombre='Mantenimiento', tipo='PRODUCCION')
        self.prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()

    def compra(self, producto, numero, **kw):
        c = Compra.objects.create(tercero=self.prov, serie='F001', numero=numero, fecha_emision=HOY,
                                  centro_costo=self.mant, ingresar_almacen=False, **kw)
        CompraItem.objects.create(documento=c, producto=producto, descripcion=producto.nombre, cantidad=1,
                                  precio_unitario=D('1000'))
        c.calcular_totales()
        c.save()
        return c

    def test_reglas_de_cuentas(self):
        self.assertTrue(exige_centro('6343') and exige_centro('6814') and not exige_centro('6011'))
        self.assertTrue(admite_centro('6711') and admite_centro('9011') and not admite_centro('3361'))
        cuentas = {c.codigo: c for c in CuentaContable.objects.filter(codigo__in=['6343', '1041'])}
        form = AsientoLineaForm(data={'cuenta': cuentas['6343'].pk, 'debe': '10', 'haber': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('centro_costo', form.errors)
        form = AsientoLineaForm(data={'cuenta': cuentas['1041'].pk, 'debe': '10', 'haber': '',
                                      'centro_costo': self.mant.pk})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['centro_costo'])  # balance: sin centro de costo

    def test_activo_y_suministro_no_son_gasto_del_area(self):
        equipo = Producto.objects.create(nombre='Torno', clase='ACTIVO')
        repuesto = Producto.objects.create(nombre='Rodamiento', clase='SUMINISTRO')
        servicio = Producto.objects.create(nombre='Mantenimiento externo', clase='SERVICIO')
        gasto = CuentaContable.objects.get(codigo='6343')
        self.compra(equipo, '901', clasificacion='ACTIVO_FIJO')
        self.compra(repuesto, '902', clasificacion='GASTO', cuenta_contable=gasto)  # aun con cuenta de gasto
        self.compra(servicio, '903', clasificacion='SERVICIO', cuenta_contable=gasto)
        centralizar_periodo(PERIODO)
        lineas = AsientoLinea.objects.filter(asiento__periodo=PERIODO, asiento__origen='COMPRA', debe__gt=0)
        # el repuesto va a su cuenta de compra (6032 -> 25), no al gasto; el servicio sí al gasto con su centro
        repuesto_linea = lineas.get(asiento__compra__numero='902', cuenta__codigo='6032')
        self.assertIsNone(repuesto_linea.centro_costo)
        self.assertEqual(lineas.get(asiento__compra__numero='903', cuenta=gasto, es_destino=False).centro_costo,
                         self.mant)
        self.assertFalse(lineas.filter(cuenta__codigo__startswith='33', centro_costo__isnull=False).exists())
        # el reporte muestra solo el gasto (61-68); la compra del equipo y del repuesto no son gasto del área
        r = self.client.get(reverse('contabilidad:centros_reporte'))
        grupo = r.context['grupos']['MANT']
        self.assertEqual([f['codigo'] for f in grupo['filas']], ['6343'])
        self.assertEqual(grupo['total'], D('1000.00'))
        r = self.client.get(reverse('contabilidad:centros_reporte') + '?vista=destino')
        self.assertTrue(all(f['codigo'].startswith('9') for f in r.context['grupos']['MANT']['filas']))
        for a in Asiento.objects.filter(periodo=PERIODO):
            self.assertTrue(a.cuadrado, f'{a} no cuadra')
