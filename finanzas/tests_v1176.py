"""v1.117.6: los movimientos de caja y bancos se editan mientras no estén conciliados (con motivo y en la bitácora).
El manual se corrige completo; el de una cobranza, pago o transferencia solo en los datos que no cambian su importe."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import CentroCosto
from core.models import Bitacora, PerfilUsuario
from core.modulos import GRUPOS
from finanzas.models import Cuenta, Movimiento
from ventas.models import Venta

D = Decimal
HOY = timezone.localdate()
AYER = HOY - timedelta(days=1)
TC = (D('3.441'), D('3.450'))


class EditarMovimientoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('t1176', 't@t.com', 'x')
        cls.cc, _ = CentroCosto.objects.get_or_create(codigo='ADM-T', defaults={'nombre': 'Administración'})

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=TC)
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)
        self.bcp = Cuenta.objects.get(nombre='BCP Cta. Cte. Soles')

    def manual(self, **extra):
        datos = dict(cuenta=self.bcp, fecha=AYER, tipo='EGRESO', concepto='SERVICIOS', medio_pago='TRANSFERENCIA',
                     monto=D('150'), glosa='Recibo de luz', centro_costo=self.cc)
        datos.update(extra)
        return Movimiento.objects.create(**datos)

    def datos(self, mov, **extra):
        d = {'cuenta': mov.cuenta_id, 'fecha': mov.fecha.isoformat(), 'tipo': mov.tipo, 'concepto': mov.concepto,
             'medio_pago': mov.medio_pago, 'numero_operacion': mov.numero_operacion, 'tercero': mov.tercero_id or '',
             'monto': str(mov.monto), 'cuenta_contable': '', 'centro_costo': mov.centro_costo_id or '',
             'rubro_flujo': '', 'glosa': mov.glosa, 'motivo_correccion': 'El recibo era de otro monto'}
        d.update(extra)
        return d

    def test_manual_se_corrige_completo_con_motivo(self):
        mov = self.manual()
        url = reverse('finanzas:movimiento_editar', args=[mov.pk])
        r = self.client.get(url)
        self.assertContains(r, 'Motivo de la corrección')
        self.assertContains(r, 'name="monto"')
        r = self.client.post(url, self.datos(mov, monto='175.50', motivo_correccion='corto'))
        self.assertIn('motivo_correccion', r.context['form'].errors)
        r = self.client.post(url, self.datos(mov, monto='175.50', glosa='Recibo de luz octubre', fecha=HOY.isoformat()))
        self.assertRedirects(r, reverse('finanzas:movimientos'), fetch_redirect_response=False)
        mov.refresh_from_db()
        self.assertEqual((mov.monto, mov.glosa, mov.fecha), (D('175.50'), 'Recibo de luz octubre', HOY))
        b = Bitacora.objects.get(modelo='finanzas.Movimiento', objeto_id=str(mov.pk), accion='MODIFICAR')
        self.assertEqual(b.motivo, 'Corrección: El recibo era de otro monto')
        self.assertIn(['150.00', '175.50'], [[str(x) for x in v] for v in b.cambios.values()])
        self.assertContains(self.client.get(reverse('finanzas:movimientos')),
                            reverse('finanzas:movimiento_editar', args=[mov.pk]))

    def test_conciliado_no_se_edita(self):
        mov = self.manual(conciliado=True, fecha_conciliacion=HOY)
        url = reverse('finanzas:movimiento_editar', args=[mov.pk])
        r = self.client.get(url, follow=True)
        self.assertContains(r, 'está conciliado')
        self.client.post(url, self.datos(mov, monto='999'))
        mov.refresh_from_db()
        self.assertEqual(mov.monto, D('150'))
        self.assertNotContains(self.client.get(reverse('finanzas:movimientos')), url)
        anulado = self.manual()
        anulado.estado = 'ANULADO'
        anulado.save()
        r = self.client.get(reverse('finanzas:movimiento_editar', args=[anulado.pk]), follow=True)
        self.assertContains(r, 'está anulado')

    def test_cobranza_solo_datos_que_no_cambian_el_importe(self):
        v = Venta.objects.filter(estado='EMITIDA').first() or Venta.objects.first()
        mov = Movimiento.objects.create(cuenta=self.bcp, fecha=AYER, tipo='INGRESO', concepto='COBRANZA', venta=v,
                                        tercero=v.tercero, monto=D('10'), medio_pago='TRANSFERENCIA')
        url = reverse('finanzas:movimiento_editar', args=[mov.pk])
        r = self.client.get(url)
        self.assertContains(r, 'Viene de la cobranza de')
        self.assertNotContains(r, 'name="monto"')
        r = self.client.post(url, self.datos(mov, monto='9999', numero_operacion='OP-778899', glosa='Voucher BCP'))
        self.assertEqual(r.status_code, 302, r.context['form'].errors if r.status_code == 200 else '')
        mov.refresh_from_db()
        self.assertEqual((mov.monto, mov.numero_operacion, mov.glosa, mov.venta_id), (D('10'), 'OP-778899',
                                                                                       'Voucher BCP', v.pk))

    def test_transferencia_mueve_la_fecha_de_ambos_lados(self):
        caja = Cuenta.objects.create(nombre='Caja T1176', tipo='CAJA', moneda='PEN')
        sale = Movimiento.objects.create(cuenta=self.bcp, fecha=AYER, tipo='EGRESO', concepto='TRANSFERENCIA',
                                         monto=D('20'))
        llega = Movimiento.objects.create(cuenta=caja, fecha=AYER, tipo='INGRESO', concepto='TRANSFERENCIA',
                                          monto=D('20'), transferencia_par=sale)
        r = self.client.post(reverse('finanzas:movimiento_editar', args=[sale.pk]),
                             self.datos(sale, fecha=HOY.isoformat()))
        self.assertEqual(r.status_code, 302, r.context['form'].errors if r.status_code == 200 else '')
        llega.refresh_from_db()
        self.assertEqual(llega.fecha, HOY)
        # el lado que llega a caja tampoco se edita si el otro lado está conciliado
        Movimiento.objects.filter(pk=sale.pk).update(conciliado=True)
        r = self.client.get(reverse('finanzas:movimiento_editar', args=[llega.pk]), follow=True)
        self.assertContains(r, 'está conciliado')

    def test_achicar_un_ingreso_no_deja_saldo_negativo(self):
        caja = Cuenta.objects.create(nombre='Caja T1176b', tipo='CAJA', moneda='PEN')
        ingreso = Movimiento.objects.create(cuenta=caja, fecha=AYER, tipo='INGRESO', concepto='OTRO', monto=D('100'),
                                            glosa='Aporte')
        Movimiento.objects.create(cuenta=caja, fecha=HOY, tipo='EGRESO', concepto='OTRO', monto=D('80'), glosa='Gasto')
        r = self.client.post(reverse('finanzas:movimiento_editar', args=[ingreso.pk]),
                             self.datos(ingreso, monto='50'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('No se puede guardar la corrección', str(r.context['form'].non_field_errors()))
        ingreso.refresh_from_db()
        self.assertEqual(ingreso.monto, D('100'))

    def test_sin_permiso_de_anular_no_corrige(self):
        u = User.objects.create_user('caja1176', password='x')
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['tesoreria'])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=['tesoreria.registrar'])
        self.client.force_login(u)
        mov = self.manual()
        self.assertEqual(self.client.get(reverse('finanzas:movimiento_editar', args=[mov.pk])).status_code, 403)
