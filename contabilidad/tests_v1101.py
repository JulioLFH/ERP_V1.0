"""v1.101: los asientos usan las cuentas de 6 dígitos del plan cargado (4212 -> 421211 MN / 421212 ME)."""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from core.models import Tercero
from finanzas.models import Cuenta, Movimiento

from . import pcge
from .centralizar import centralizar_periodo
from .models import AsientoLinea, CuentaContable, CuentaDefecto

P = '203105'  # un periodo sin otros datos
F = date(2031, 5, 12)


class CuentasSeisDigitosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000022',
                                          nombre='PROVEEDOR SAC')
        cls.bcp = Cuenta.objects.create(nombre='BCP soles', banco='BCP', moneda='PEN', saldo_inicial=D('100000'))

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(pcge.limpiar_detalle)  # el índice de subcuentas no sobrevive a la base de la prueba
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.700'), D('3.750')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.user)
        cta = CuentaDefecto.mapa()
        self.igv, self.prov_cta, self.gasto = cta['igv'], cta['proveedor'], cta['compra_SERVICIO']
        # el plan cargado por la empresa: subcuentas de 6 dígitos debajo de las generales del ERP
        self.igv6 = self.sub(self.igv, '1', 'IGV - CUENTA PROPIA')
        self.mn = self.sub(self.prov_cta, '1', 'EMITIDAS MN')
        self.me = self.sub(self.prov_cta, '2', 'EMITIDAS ME', moneda='USD')

    def sub(self, padre, final, nombre, moneda='PEN'):
        codigo = padre.codigo.ljust(5, '1')[:5] + final
        return CuentaContable.objects.create(codigo=codigo, nombre=nombre, naturaleza=padre.naturaleza,
                                             imputable=True, moneda=moneda)

    def compra(self, numero, base, moneda='PEN', tc='1'):
        c = Compra.objects.create(tipo_comprobante='01', serie='F001', numero=numero, fecha_emision=F,
                                  tercero=self.prov, clasificacion='SERVICIO', ingresar_almacen=False,
                                  moneda=moneda, tipo_cambio=D(tc))
        CompraItem.objects.create(documento=c, descripcion='Servicio', cantidad=1, precio_unitario=D(base))
        c.calcular_totales()
        c.save()
        return c

    def lineas(self, **filtro):
        return {l.cuenta.codigo: l for l in AsientoLinea.objects.filter(**filtro).select_related('cuenta')}

    def test_compra_en_soles_va_a_las_cuentas_de_6_digitos(self):
        c = self.compra('100', '500')
        r = centralizar_periodo(P)
        self.assertEqual(r['errores'], [])
        lineas = self.lineas(asiento__compra=c)
        self.assertEqual(lineas[self.igv6.codigo].debe, D('90.00'))
        self.assertEqual(lineas[self.mn.codigo].haber, D('590.00'))
        self.assertNotIn(self.igv.codigo, lineas)
        self.assertNotIn(self.prov_cta.codigo, lineas)

    def test_compra_en_dolares_al_proveedor_me_y_su_pago_desde_banco_en_soles(self):
        c = self.compra('101', '100', moneda='USD', tc='3.75')  # 118 dólares
        Movimiento.objects.create(cuenta=self.bcp, fecha=F, tipo='EGRESO', concepto='PAGO', monto=D('442.50'),
                                  compra=c)
        r = centralizar_periodo(P)
        self.assertEqual(r['errores'], [])
        self.assertEqual(self.lineas(asiento__compra=c)[self.me.codigo].haber, D('442.50'))
        pago = self.lineas(asiento__movimiento__compra=c)
        self.assertEqual(pago[self.me.codigo].debe, D('442.50'))  # cancela la misma 421212, no la de soles
        self.assertNotIn(self.mn.codigo, pago)

    def test_sin_subcuentas_queda_la_cuenta_configurada(self):
        sola = CuentaContable.objects.create(codigo='96991', nombre='Sin subcuentas', imputable=True)
        self.assertEqual(pcge.al_detalle(sola), sola)
        self.assertEqual(pcge.al_detalle(self.prov_cta), self.mn)  # varias: la de la moneda y menor código
        self.assertEqual(pcge.al_detalle(self.prov_cta, 'USD'), self.me)
        self.assertEqual(pcge.al_detalle(self.mn, 'USD'), self.mn)  # sin emparejar: la elegida se respeta
        self.assertEqual(pcge.al_detalle(self.mn, 'USD', emparejar=True), self.me)

    def test_configuracion_muestra_donde_se_registra(self):
        r = self.client.get(reverse('contabilidad:configuracion'))
        self.assertContains(r, f'<b>{self.mn.codigo}</b> EMITIDAS MN')
        self.assertContains(r, f'<b>{self.me.codigo}</b> EMITIDAS ME (dólares)')
