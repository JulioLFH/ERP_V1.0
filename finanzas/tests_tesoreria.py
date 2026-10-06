"""v1.20: anticipos aplicados, letras (canje, cobro, renovación), cheques, entregas a rendir / caja chica y pagos
masivos, con su efecto en saldos y en la contabilidad."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea
from core.models import Tercero
from ventas.models import Venta, VentaItem

from . import operaciones as op
from .models import Aplicacion, Cheque, Cuenta, EntregaRendir, GastoRendicion, Letra, Movimiento

D = Decimal
HOY = date.today()


def saldo_cuenta(codigo, tercero=None):
    qs = AsientoLinea.objects.filter(cuenta__codigo=codigo)
    if tercero is not None:
        qs = qs.filter(tercero=tercero)
    agg = qs.aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or D(0)) - (agg['h'] or D(0))


class TesoreriaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.banco = Cuenta.objects.create(nombre='BCP Tesorería', banco='BCP', tipo='BANCO', saldo_inicial=D('50000'))
        self.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970',
                                              nombre='CLIENTE LETRAS SAC')
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100047218',
                                           nombre='PROVEEDOR PAGOS SAC', banco='BCP', cuenta_bancaria='1931234567012',
                                           cci='00219300123456701212')
        self.trabajador = Tercero.objects.create(tipo='AMBOS', tipo_doc='1', numero_doc='45678912',
                                                 nombre='JUAN PEREZ')

    def venta(self, total='1180', numero='88001'):
        v = Venta.objects.create(tercero=self.cliente, serie='F001', numero=numero, fecha_emision=HOY,
                                 forma_pago='CREDITO', fecha_vencimiento=HOY + timedelta(days=30))
        VentaItem.objects.create(documento=v, descripcion='Producto', cantidad=1, precio_unitario=D(total))
        v.calcular_totales()
        v.save()
        return Venta.objects.con_saldos().get(pk=v.pk)

    def compra(self, total='590', numero='7001', tercero=None):
        c = Compra.objects.create(tercero=tercero or self.prov, serie='F001', numero=numero, fecha_emision=HOY,
                                  forma_pago='CREDITO')
        CompraItem.objects.create(documento=c, descripcion='Servicio', cantidad=1, precio_unitario=D(total))
        c.calcular_totales()
        c.save()
        return Compra.objects.con_saldos().get(pk=c.pk)

    def centralizar(self):
        r = centralizar_periodo(HOY.strftime('%Y%m'))
        self.assertEqual(r['errores'], [])
        tot = AsientoLinea.objects.aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual(tot['d'], tot['h'])

    # ---------------------------------------------------------------- anticipos
    def test_anticipo_de_cliente_se_aplica_a_la_factura(self):
        v = self.venta()
        anticipo = Movimiento.objects.create(cuenta=self.banco, fecha=HOY, tipo='INGRESO', concepto='ANTICIPO',
                                             tercero=self.cliente, monto=D('500'), glosa='Adelanto')
        r = self.client.post(reverse('finanzas:anticipo_aplicar', args=[anticipo.pk]),
                             {f'monto_{v.pk}': '500', 'fecha': HOY.isoformat()})
        self.assertRedirects(r, reverse('finanzas:anticipos'))
        v = Venta.objects.con_saldos().get(pk=v.pk)
        self.assertEqual(v.saldo, v.neto - D('500'))
        self.assertEqual(Venta.objects.get(pk=v.pk).saldo, v.saldo)  # sin anotaciones da lo mismo
        self.assertEqual(anticipo.anticipo_disponible, D('0'))
        with self.assertRaises(op.ErrorTesoreria):  # no se aplica dos veces
            op.aplicar_anticipo(anticipo, [(v, D('1'))], HOY, self.user)
        self.centralizar()
        self.assertEqual(saldo_cuenta('1221', self.cliente), D('0'))  # anticipo recibido y aplicado
        self.assertEqual(saldo_cuenta('1212', self.cliente), v.total_pen - D('500'))

    def test_anticipo_no_se_anula_si_esta_aplicado(self):
        v = self.venta()
        anticipo = Movimiento.objects.create(cuenta=self.banco, fecha=HOY, tipo='INGRESO', concepto='ANTICIPO',
                                             tercero=self.cliente, monto=D('300'))
        ap, = op.aplicar_anticipo(anticipo, [(v, D('300'))], HOY, self.user)
        self.client.post(reverse('finanzas:movimiento_eliminar', args=[anticipo.pk]),
                         {'motivo': 'Error de registro del anticipo'})
        self.assertEqual(Movimiento.todos.get(pk=anticipo.pk).estado, 'VIGENTE')
        op.anular_aplicacion(ap, 'Se aplicó a otra factura por error', self.user)
        self.assertEqual(Venta.objects.get(pk=v.pk).saldo, v.neto)

    # ---------------------------------------------------------------- letras
    def test_canje_por_letras_cobro_y_contabilidad(self):
        v1, v2 = self.venta('1180', '88011'), self.venta('590', '88012')
        r = self.client.post(f'{reverse("finanzas:canje_nuevo")}?tipo=COBRAR&tercero={self.cliente.pk}&moneda=PEN', {
            'tipo': 'COBRAR', 'fecha': HOY.isoformat(), f'monto_{v1.pk}': str(v1.saldo), f'monto_{v2.pk}': str(v2.saldo),
            'l_numero': ['', ''], 'l_vencimiento': [(HOY + timedelta(days=30)).isoformat(),
                                                    (HOY + timedelta(days=60)).isoformat()],
            'l_monto': ['1000', str(v1.saldo + v2.saldo - D('1000'))]})
        self.assertEqual(r.status_code, 302, r.content[:3000])
        self.assertEqual(Venta.objects.get(pk=v1.pk).saldo, 0)
        self.assertEqual(Venta.objects.get(pk=v2.pk).saldo, 0)
        letras = list(Letra.objects.filter(tercero=self.cliente).order_by('fecha_vencimiento'))
        self.assertEqual(len(letras), 2)
        # cobro de la primera letra desde Cobranzas
        r = self.client.post(f'{reverse("finanzas:cobranza")}?tercero={self.cliente.pk}', {
            f'letra_{letras[0].pk}': '1000', 'cuenta': self.banco.pk, 'fecha': HOY.isoformat(),
            'medio_pago': 'TRANSFERENCIA', 'numero_operacion': '123'})
        self.assertEqual(r.status_code, 302)
        letras[0].refresh_from_db()
        self.assertEqual(letras[0].estado, 'CANCELADA')
        self.centralizar()
        self.assertEqual(saldo_cuenta('1212', self.cliente), 0)
        self.assertEqual(saldo_cuenta('1231', self.cliente), letras[1].monto)

    def test_renovacion_de_letra(self):
        v = self.venta('1180', '88021')
        canje = op.canjear('COBRAR', self.cliente, HOY, 'PEN', D('1'), [(v, v.saldo)],
                           [(HOY + timedelta(days=30), v.saldo, '')], self.user)
        letra = canje.letras.get()
        nuevas = op.renovar(letra, [(HOY + timedelta(days=60), D('600')), (HOY + timedelta(days=90), v.saldo - 600)],
                            HOY, self.user)
        letra.refresh_from_db()
        self.assertEqual((letra.estado, letra.saldo), ('RENOVADA', 0))
        self.assertEqual(sum(n.saldo for n in nuevas), v.saldo)
        with self.assertRaises(op.ErrorTesoreria):  # no se anula un canje con letras renovadas
            op.anular_canje(canje, 'Anulación de prueba del canje', self.user)

    # ---------------------------------------------------------------- cheques
    def test_cheque_recibido_rechazado_reabre_la_factura(self):
        v = self.venta('1180', '88031')
        self.client.post(f'{reverse("finanzas:cobranza")}?tercero={self.cliente.pk}', {
            f'monto_{v.pk}': str(v.saldo), 'cuenta': self.banco.pk, 'fecha': HOY.isoformat(),
            'medio_pago': 'CHEQUE', 'numero_operacion': '00012345'})
        ch = Cheque.objects.get(numero='00012345')
        self.assertEqual((ch.tipo, ch.estado, ch.monto), ('RECIBIDO', 'COBRADO', v.saldo))
        self.assertEqual(Venta.objects.get(pk=v.pk).saldo, 0)
        self.client.post(reverse('finanzas:cheque_estado', args=[ch.pk]),
                         {'estado': 'RECHAZADO', 'motivo': 'Sin fondos según banco', 'fecha': HOY.isoformat()})
        ch.refresh_from_db()
        self.assertEqual(ch.estado, 'RECHAZADO')
        self.assertEqual(Venta.objects.get(pk=v.pk).saldo, v.neto)

    def test_cheque_diferido_en_cartera_se_deposita(self):
        v = self.venta('590', '88032')
        r = self.client.post(reverse('finanzas:cheques'), {
            'numero': '777', 'banco_emisor': 'BBVA', 'tercero': self.cliente.pk, 'moneda': 'PEN', 'monto': str(v.saldo),
            'fecha_emision': HOY.isoformat(), 'fecha_pago': HOY.isoformat(), 'cuenta': self.banco.pk})
        self.assertEqual(r.status_code, 302)
        ch = Cheque.objects.get(numero='777')
        self.assertEqual(ch.estado, 'CARTERA')
        self.client.post(f'{reverse("finanzas:cobranza")}?tercero={self.cliente.pk}&cheque={ch.pk}', {
            f'monto_{v.pk}': str(v.saldo), 'cuenta': self.banco.pk, 'fecha': HOY.isoformat(),
            'medio_pago': 'CHEQUE', 'numero_operacion': '777'})
        ch.refresh_from_db()
        self.assertEqual(ch.estado, 'COBRADO')
        self.assertEqual(ch.movimientos.count(), 1)

    # ---------------------------------------------------------------- entregas a rendir
    def test_entrega_a_rendir_completa(self):
        from contabilidad.models import CuentaContable
        r = self.client.post(reverse('finanzas:entrega_nueva'), {
            'tipo': 'ENTREGA', 'responsable': self.trabajador.pk, 'fecha': HOY.isoformat(), 'motivo': 'Viaje a Lima',
            'cuenta': self.banco.pk, 'monto': '1000', 'medio_pago': 'TRANSFERENCIA'})
        e = EntregaRendir.objects.get()
        self.assertRedirects(r, reverse('finanzas:entrega', args=[e.pk]))
        self.assertEqual(e.entregado, D('1000'))
        gasto = CuentaContable.objects.get(codigo='6311')
        self.client.post(reverse('finanzas:entrega', args=[e.pk]), {
            'accion': 'gasto', 'g-fecha': HOY.isoformat(), 'g-tipo_documento': 'PM', 'g-descripcion': 'Taxis',
            'g-cuenta_contable': gasto.pk, 'g-monto': '150'})
        c = self.compra('590', '7101', tercero=self.prov)
        self.client.post(reverse('finanzas:entrega', args=[e.pk]), {
            'accion': 'compra', 'compra': c.pk, 'fecha': HOY.isoformat()})
        self.assertEqual(Compra.objects.get(pk=c.pk).saldo, 0)
        e.refresh_from_db()
        self.assertEqual(e.saldo, D('1000') - D('150') - c.neto)
        with self.assertRaises(op.ErrorTesoreria):
            op.liquidar(e, HOY)
        self.client.post(reverse('finanzas:entrega', args=[e.pk]), {
            'accion': 'devolver', 'cuenta': self.banco.pk, 'monto': str(e.saldo), 'fecha': HOY.isoformat()})
        op.liquidar(e, HOY)
        self.assertEqual(EntregaRendir.objects.get().estado, 'LIQUIDADA')
        self.centralizar()
        self.assertEqual(saldo_cuenta('1413', self.trabajador), 0)
        self.assertEqual(saldo_cuenta('4212', self.prov), 0)

    def test_caja_chica_reposicion(self):
        e = op.crear_entrega(EntregaRendir(tipo='CAJA_CHICA', responsable=self.trabajador, fecha=HOY,
                                           motivo='Caja chica oficina'), self.banco, D('500'), '', 'EFECTIVO', self.user)
        self.assertEqual(e.monto_fondo, D('500'))
        from contabilidad.models import CuentaContable
        op.rendir_gasto(e, GastoRendicion(fecha=HOY, descripcion='Útiles', monto=D('120'),
                                          cuenta_contable=CuentaContable.objects.get(codigo='6561')), self.user)
        self.assertEqual(e.por_reponer, D('120'))
        op.movimiento_fondo(e, 'reponer', self.banco, D('120'), HOY, self.user)
        self.assertEqual(e.saldo, D('500'))
        self.centralizar()
        self.assertEqual(saldo_cuenta('1021'), D('500'))

    # ---------------------------------------------------------------- pagos masivos
    def test_pago_masivo_genera_archivo_y_registra_pagos(self):
        c1, c2 = self.compra('590', '7201'), self.compra('1180', '7202')
        r = self.client.post(f'{reverse("finanzas:pago_masivo_nuevo")}?cuenta={self.banco.pk}&hasta={HOY.isoformat()}',
                             {f'monto_{c1.pk}': str(c1.saldo), f'monto_{c2.pk}': '500', 'fecha': HOY.isoformat()})
        self.assertEqual(r.status_code, 302)
        from .models import PagoMasivo
        pago = PagoMasivo.objects.get()
        archivo = self.client.get(reverse('finanzas:pago_masivo', args=[pago.pk]) + '?formato=excel')
        self.assertEqual(archivo.status_code, 200)
        self.assertEqual(op.datos_banco(self.prov, self.banco), ('PROPIO BANCO', '1931234567012'))
        self.client.post(reverse('finanzas:pago_masivo', args=[pago.pk]), {'accion': 'registrar'})
        pago.refresh_from_db()
        self.assertEqual(pago.estado, 'PAGADO')
        self.assertEqual(Compra.objects.get(pk=c1.pk).saldo, 0)
        self.assertEqual(Compra.objects.get(pk=c2.pk).saldo, c2.neto - 500)

    def test_pantallas_abren(self):
        for nombre in ('anticipos', 'letras', 'canje_nuevo', 'cheques', 'entregas', 'entrega_nueva', 'pagos_masivos',
                       'pago_masivo_nuevo'):
            self.assertEqual(self.client.get(reverse(f'finanzas:{nombre}')).status_code, 200, nombre)
