"""v1.117.3: las reglas de aprobación de pagos funcionan por centro de costo. La propuesta de pagos junta facturas de
varios centros (el de la factura o el de su orden de compra): una regla de un centro aplica cuando la propuesta trae
pagos de ese centro (o de sus subcentros) y se evalúa con lo que suman esos pagos; una regla sin centro, con el total.
Con «Responsable del centro de costo» aprueba el responsable de cada centro, uno tras otro."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.urls import reverse

from contabilidad.models import CentroCosto
from core import aprobaciones as motor
from core.models import ReglaAprobacion
from core.views_aprobaciones import ReglaForm

from . import propuesta_pagos as pp
from .tests_v1117 import HOY, Base


class PagosPorCentroTest(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.jefe = User.objects.create_user('jefe1173', password='Clave-segura-9', first_name='Jefa')
        cls.gerente = User.objects.create_user('gerente1173', password='Clave-segura-9', first_name='Gerente')
        cls.ra = User.objects.create_user('resp_a1173', password='Clave-segura-9', first_name='Resp A')
        cls.rb = User.objects.create_user('resp_b1173', password='Clave-segura-9', first_name='Resp B')
        cls.cc_a = CentroCosto.objects.create(codigo='T1173A', nombre='Proyecto Lima', responsable_usuario=cls.ra)
        cls.cc_a1 = CentroCosto.objects.create(codigo='T1173A1', nombre='Lima obra 1', padre=cls.cc_a)
        cls.cc_b = CentroCosto.objects.create(codigo='T1173B', nombre='Proyecto Sur', responsable_usuario=cls.rb)

    def compra_cc(self, numero, total, centro):
        c = self.compra(numero, total=total)
        c.centro_costo = centro
        c.save(update_fields=['centro_costo'])
        return c

    def propuesta(self, *compras):
        p = pp.armar(self.banco, HOY, HOY + timedelta(days=10), D('0'), self.user)
        pp.cambiar(p, set(p.lineas.filter(compra__in=compras).values_list('pk', flat=True)))
        pp.enviar(p, self.user)
        p.refresh_from_db()
        return p

    def test_regla_del_centro_se_evalua_con_lo_que_suman_sus_pagos(self):
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='USUARIO', aprobador=self.jefe)
        ReglaAprobacion.objects.create(documento='PAGO', nivel=2, tipo='USUARIO', aprobador=self.gerente,
                                       monto_desde=D('1000'), centro_costo=self.cc_a)
        a = self.compra_cc('11', D('1180'), self.cc_a)
        b = self.compra_cc('12', D('5900'), self.cc_b)
        p = self.propuesta(a, b)
        self.assertEqual([n['usuario'] for n in p.niveles], [self.jefe.pk, self.gerente.pk])
        self.assertEqual(p.niveles[1]['centro'], str(self.cc_a))
        self.assertNotIn('centro', p.niveles[0])  # la regla general no es de un centro
        r = self.client.get(reverse('finanzas:propuesta', args=[p.pk]))
        self.assertContains(r, f'· {self.cc_a}')
        # el total pasa de S/ 1,000 pero lo de Proyecto Lima no: su regla no aplica
        a2 = self.compra_cc('13', D('590'), self.cc_a)
        b2 = self.compra_cc('14', D('5900'), self.cc_b)
        p = self.propuesta(a2, b2)
        self.assertEqual([n['usuario'] for n in p.niveles], [self.jefe.pk])

    def test_regla_del_centro_alcanza_a_sus_subcentros_y_no_a_otros(self):
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='USUARIO', aprobador=self.gerente,
                                       centro_costo=self.cc_a)
        p = self.propuesta(self.compra_cc('21', D('1180'), self.cc_a1))
        self.assertEqual([n['usuario'] for n in p.niveles], [self.gerente.pk])
        p = self.propuesta(self.compra_cc('22', D('1180'), self.cc_b))
        self.assertEqual(p.niveles, [])
        p = self.propuesta(self.compra_cc('23', D('1180'), None))
        self.assertEqual(p.niveles, [])

    def test_responsable_de_cada_centro_aprueba_uno_tras_otro(self):
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='RESPONSABLE_CC')
        ReglaAprobacion.objects.create(documento='PAGO', nivel=2, tipo='USUARIO', aprobador=self.gerente)
        p = self.propuesta(self.compra_cc('31', D('1180'), self.cc_a), self.compra_cc('32', D('2360'), self.cc_b))
        self.assertEqual([(n['usuario'], n.get('centro')) for n in p.niveles],
                         [(self.ra.pk, str(self.cc_a)), (self.rb.pk, str(self.cc_b)), (self.gerente.pk, None)])
        self.assertFalse(pp.puede_aprobar(self.rb, p))  # primero Proyecto Lima
        pp.aprobar(p, self.ra)
        self.assertTrue(pp.puede_aprobar(self.rb, p))
        pp.aprobar(p, self.rb)
        pp.aprobar(p, self.gerente)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'APROBADA')
        self.assertEqual([r['estado'] for r in pp.ruta(p)], ['APROBADO'] * 3)

    def test_mismo_responsable_de_dos_centros_aprueba_una_vez(self):
        CentroCosto.objects.filter(pk=self.cc_b.pk).update(responsable_usuario=self.ra)
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='RESPONSABLE_CC')
        p = self.propuesta(self.compra_cc('41', D('1180'), self.cc_a), self.compra_cc('42', D('1180'), self.cc_b))
        self.assertEqual(len(p.niveles), 1)
        self.assertEqual(p.niveles[0]['centro'], f'{self.cc_a} · {self.cc_b}')

    def test_responsable_sin_centro_usa_el_reemplazo(self):
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='RESPONSABLE_CC', reemplazo=self.jefe)
        p = self.propuesta(self.compra_cc('51', D('1180'), None))
        self.assertEqual([n['usuario'] for n in p.niveles], [self.jefe.pk])

    def test_sin_reglas_de_centro_da_lo_mismo_que_antes(self):
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='USUARIO', aprobador=self.jefe)
        ReglaAprobacion.objects.create(documento='PAGO', nivel=2, tipo='USUARIO', aprobador=self.gerente,
                                       monto_desde=D('3000'))
        p = self.propuesta(self.compra_cc('61', D('1180'), self.cc_a), self.compra_cc('62', D('2360'), self.cc_b))
        antes = motor.calcular_niveles('PAGO', self.user, pp.total_en_soles(p))
        self.assertEqual([(n['nivel'], n['usuario']) for n in p.niveles], [(n['nivel'], n['usuario']) for n in antes])
        self.assertEqual(sum(pp.montos_por_centro(p).values()), pp.total_en_soles(p))

    def test_centro_de_la_orden_de_compra_si_la_factura_no_tiene(self):
        from compras.models import OrdenCompra
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='RESPONSABLE_CC')
        oc = OrdenCompra.objects.create(numero='OC01-T1173', tercero=self.prov, estado='APROBADO', centro_costo=self.cc_b)
        c = self.compra_cc('71', D('1180'), None)
        c.orden_compra = oc
        c.save(update_fields=['orden_compra'])
        self.assertEqual(pp.centro_de(c), self.cc_b)
        p = self.propuesta(c)
        self.assertEqual([(n['usuario'], n['centro']) for n in p.niveles], [(self.rb.pk, str(self.cc_b))])

    def test_tipo_de_documento_no_se_usa_en_pagos(self):
        datos = {'documento': 'PAGO', 'nivel': 1, 'tipo': 'USUARIO', 'aprobador': self.jefe.pk, 'monto_desde': '0',
                 'niveles_arriba': 2, 'activo': True}
        self.assertFalse(ReglaForm({**datos, 'aplica_a': 'SERVICIO'}).is_valid())
        self.assertTrue(ReglaForm({**datos, 'aplica_a': '', 'centro_costo': self.cc_a.pk}).is_valid())
        r = self.client.get(reverse('aprobaciones_reglas') + '?documento=PAGO')
        self.assertContains(r, 'una regla <b>de un centro</b> aplica')
