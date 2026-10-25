"""v1.84: Finanzas (FIN-01 a FIN-08): flujo proyectado corregido, presupuesto de caja con escenarios, cobranza,
propuesta de pagos, financiamiento, préstamos y NIIF 16, riesgo cambiario e indicadores."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, CompraItem
from contabilidad.models import Asiento, AsientoLinea, CuentaContable
from core.models import Producto, Tercero
from finanzas.models import Cuenta, Movimiento
from ventas.models import Cotizacion, CotizacionItem, Venta, VentaItem

from .tests_v181 import usuario

D = Decimal
HOY = timezone.localdate()
PERIODO = f'{HOY:%Y%m}'


class FinanzasBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('fin', 'f@f.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.banco = Cuenta.objects.create(nombre='BCP Finanzas', banco='BCP', tipo='BANCO', saldo_inicial=D('50000'))
        self.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070971',
                                              nombre='CLIENTE FINANZAS SAC', dias_credito=30)
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100047219',
                                           nombre='PROVEEDOR FINANZAS SAC', banco='BCP',
                                           cuenta_bancaria='1931234567013', cci='00219300123456701313')
        self.bbva = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100130204',
                                           nombre='BANCO BBVA PERU')

    def venta(self, total='1180', numero='88001', emision=None, vence=None, moneda='PEN'):
        emision = emision or HOY
        v = Venta.objects.create(tercero=self.cliente, serie='F001', numero=numero, fecha_emision=emision,
                                 forma_pago='CREDITO', fecha_vencimiento=vence or emision + timedelta(days=30),
                                 moneda=moneda, tipo_cambio=D('3.45') if moneda == 'USD' else D('1'))
        VentaItem.objects.create(documento=v, descripcion='Producto', cantidad=1, precio_unitario=D(total))
        v.calcular_totales()
        v.save()
        return Venta.objects.con_saldos().get(pk=v.pk)

    def compra(self, total='590', numero='7001', tercero=None, vence=None, moneda='PEN'):
        c = Compra.objects.create(tercero=tercero or self.prov, serie='F001', numero=numero, fecha_emision=HOY,
                                  forma_pago='CREDITO', fecha_vencimiento=vence or HOY, moneda=moneda,
                                  tipo_cambio=D('3.45') if moneda == 'USD' else D('1'))
        CompraItem.objects.create(documento=c, descripcion='Servicio', cantidad=1, precio_unitario=D(total))
        c.calcular_totales()
        c.save()
        return Compra.objects.con_saldos().get(pk=c.pk)


class FlujoYPresupuestoTest(FinanzasBase):
    def test_pedido_neto_de_detraccion_y_anticipo_y_cotizacion_aparte(self):
        """FIN-01."""
        from finanzas.proyeccion import proyectar
        prod = Producto.objects.create(codigo='SRV-FIN', nombre='Servicio de ingeniería', tipo='SERVICIO',
                                       detraccion_codigo='022', detraccion_pct=D('12'))
        ped = Cotizacion.objects.create(tipo='PED', tercero=self.cliente, estado='APROBADO', numero='PD01-1',
                                        condicion_pago='Crédito 30 días', fecha_entrega=HOY + timedelta(days=10))
        CotizacionItem.objects.create(documento=ped, producto=prod, descripcion='Servicio', cantidad=1,
                                      precio_unitario=D('10000'))
        ped.calcular_totales()
        ped.save()
        Movimiento.objects.create(cuenta=self.banco, tipo='INGRESO', concepto='ANTICIPO', tercero=self.cliente,
                                  monto=D('2000'), glosa='Anticipo del pedido')
        cot = Cotizacion.objects.create(tipo='COT', tercero=self.cliente, estado='PENDIENTE', numero='CT01-1',
                                        validez_dias=30)
        CotizacionItem.objects.create(documento=cot, descripcion='Otro', cantidad=1, precio_unitario=D('5000'))
        cot.calcular_totales()
        cot.save()
        p = proyectar(12)
        fila = next(f for f in p['filas'] if f['concepto'].startswith('Pedidos'))
        self.assertEqual(fila['total'], D('11800') - D('1416') - D('2000'))
        self.assertEqual(fila['valores'][40 // 7], fila['total'])  # entrega + 30 días
        ref = next(f for f in p['referenciales'] if f['concepto'].startswith('Cotizaciones'))
        self.assertEqual(ref['total'], D('1770.00'))  # 30 % de 5,900
        self.assertNotIn(D('5900'), p['ingresos'])
        self.assertEqual(p['saldos_iniciales'][0], p['saldo_inicial'])
        r = self.client.get(reverse('finanzas:flujo_proyectado'))
        self.assertContains(r, 'referencial')

    def test_presupuesto_mensual_escenarios_partidas_y_real(self):
        """FIN-02."""
        from finanzas import proyeccion
        from finanzas.models import PartidaCaja, ProyeccionGuardada
        from planillas.models import Trabajador
        self.venta('11800', vence=HOY + timedelta(days=5))
        Trabajador.objects.create(nombres='Ana', apellido_paterno='Ruiz', numero_doc='41414142', sueldo=D('3000'),
                                  fecha_ingreso=HOY - timedelta(days=400))
        siguiente = (HOY.replace(day=1) + timedelta(days=32)).replace(day=10)
        PartidaCaja.objects.create(fecha=siguiente, tipo='INGRESO', concepto='Aporte de capital', monto=D('50000'))
        base = proyeccion.presupuesto_caja(12)
        pes = proyeccion.presupuesto_caja(12, escenario='PESIMISTA')
        cob = lambda d: next(f for f in d['filas'] if f['concepto'] == 'Cobranzas por vencer')['total']
        self.assertEqual(cob(pes), (cob(base) * D('0.85')).quantize(D('0.01')))
        aporte = next(f for f in base['filas'] if f['concepto'].startswith('Aporte de capital'))
        self.assertEqual(aporte['valores'][1], D('50000'))
        self.assertTrue(any(f['concepto'].startswith('Gratificaciones') for f in base['filas']))
        self.assertTrue(any(f['concepto'].startswith('CTS') for f in base['filas']))
        r = self.client.post(reverse('finanzas:presupuesto_caja'), {'accion': 'guardar'}, follow=True)
        self.assertContains(r, 'Proyección guardada')
        g = ProyeccionGuardada.objects.get()
        self.assertEqual(self.client.get(reverse('finanzas:real_vs_proyectado', args=[g.pk])).status_code, 200)
        r = self.client.post(reverse('finanzas:presupuesto_caja'), {
            'accion': 'partida', 'fecha': siguiente.isoformat(), 'tipo': 'EGRESO', 'concepto': 'Compra de camión',
            'moneda': 'PEN', 'monto': '80000', 'escenario': '', 'repetir_meses': '1'}, follow=True)
        self.assertContains(r, 'Compra de camión')


class CobranzaYPagosTest(FinanzasBase):
    def test_bloqueo_promesas_recordatorios_y_provision(self):
        """FIN-03."""
        from finanzas import cobranza as cob
        from finanzas.models import RecordatorioCobranza
        from ventas.aprobacion_pedido import exceso_credito
        vieja = self.venta('1000', '88010', emision=HOY - timedelta(days=70), vence=HOY - timedelta(days=40))
        self.venta('500', '88011', emision=HOY - timedelta(days=37), vence=HOY - timedelta(days=7))
        self.assertEqual(cob.actualizar_bloqueos(), (1, 0))
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.bloqueado_mora)
        self.assertIn('bloqueado por mora', exceso_credito(self.cliente, D('100')))
        with self.assertRaisesMessage(cob.ErrorCobranza, 'fecha futura'):
            cob.registrar_gestion(self.cliente, 'LLAMADA', 'Promete pagar', self.user, vieja,
                                  HOY - timedelta(days=1), D('1180'))
        g = cob.registrar_gestion(self.cliente, 'LLAMADA', 'Promete pagar el lunes', self.user, vieja,
                                  HOY + timedelta(days=1), D('1180'))
        self.assertEqual([x.pk for x in cob.revisar_promesas(HOY + timedelta(days=3))], [g.pk])
        self.assertEqual([(v.numero, d) for v, d in cob.recordatorios_de_hoy() if v.tercero_id == self.cliente.pk],
                         [('88011', 7)])
        enviados, errores = cob.enviar_recordatorios()
        self.assertEqual(enviados, 0)  # sin correo saliente configurado
        self.assertGreaterEqual(errores, 1)
        self.assertFalse(RecordatorioCobranza.objects.get(venta__numero='88011').enviado)
        calc = cob.calcular_provision()
        tramo = next(f for f in calc['filas'] if f['tramo'] == '31-60')
        self.assertEqual(tramo['provision'], (tramo['saldo'] * D('0.05')).quantize(D('0.01')))
        prov = cob.registrar_provision(PERIODO, self.user)
        self.assertEqual(prov.calculada, calc['total'])
        self.assertTrue(AsientoLinea.objects.filter(asiento=prov.asiento, cuenta__codigo__startswith='684').exists())
        with self.assertRaisesMessage(cob.ErrorCobranza, 'ya se registró'):
            cob.registrar_provision(PERIODO, self.user)
        r = self.client.get(reverse('finanzas:cobranza_gestion'))
        self.assertContains(r, 'CLIENTE FINANZAS SAC')
        self.assertContains(self.client.get(reverse('finanzas:cobranza_cliente', args=[self.cliente.pk])),
                            'Promete pagar el lunes')
        self.assertEqual(self.client.get(reverse('finanzas:recordatorios')).status_code, 200)
        self.assertEqual(self.client.get(reverse('finanzas:provision')).status_code, 200)

    def test_propuesta_por_prioridad_y_caja_con_aprobacion_de_gerencia(self):
        """FIN-04."""
        from finanzas import propuesta_pagos as pp
        critico = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100047220',
                                         nombre='INSUMO CRITICO SAC', proveedor_critico=True,
                                         cci='00219300123456701414')
        grande = self.compra('50000', '7101', vence=HOY - timedelta(days=2))
        chica = self.compra('5000', '7102', tercero=critico, vence=HOY + timedelta(days=3))
        tesorero = usuario('tesorero', ['tesoreria'], ['tesoreria.registrar'])
        gerente = usuario('gerente', ['tesoreria'], ['tesoreria.aprobar_pagos'])
        p = pp.armar(self.banco, HOY, HOY + timedelta(days=7), D('1000'), tesorero)
        lineas = {l.compra_id: l for l in p.lineas.all()}
        self.assertEqual((lineas[chica.pk].prioridad, lineas[chica.pk].incluir), (1, True))
        self.assertFalse(lineas[grande.pk].incluir)
        self.assertIn('sin caja', lineas[grande.pk].motivo)
        pp.enviar(p, tesorero)
        with self.assertRaisesMessage(pp.ErrorPropuesta, 'no quien la armó'):
            pp.aprobar(p, tesorero)
        pp.aprobar(p, gerente)
        pago = pp.generar(p, tesorero)
        incluidas = [l.compra_id for l in pago.lineas.all()]
        self.assertIn(chica.pk, incluidas)
        self.assertNotIn(grande.pk, incluidas)
        self.assertEqual(incluidas[0], chica.pk)  # la del proveedor crítico va primero
        self.assertEqual(self.client.get(reverse('finanzas:propuesta', args=[p.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('finanzas:propuestas')).status_code, 200)
        self.assertEqual(self.client.get(reverse('finanzas:descuentos_pp')).status_code, 200)


class FinanciamientoTest(FinanzasBase):
    def prestamo(self, monto, linea=None, **extra):
        from finanzas import prestamos as srv
        from finanzas.models import Prestamo
        datos = {'entidad': self.bbva, 'descripcion': 'Capital de trabajo', 'monto': D(monto), 'tasa_anual': D('12'),
                 'plazo': 12, 'fecha_desembolso': HOY.replace(day=1), 'primera_cuota': HOY.replace(day=1) + timedelta(
                     days=45), 'cuenta': self.banco, 'linea': linea}
        datos.update(extra)
        return srv.crear(Prestamo(**datos), self.user)

    def test_linea_deposito_y_factoring(self):
        """FIN-05."""
        from finanzas import financiamiento as fin
        from finanzas.forms_gestion import CartaFianzaForm
        from finanzas.models import CesionFactura, DepositoPlazo, LineaCredito
        from finanzas.operaciones import ErrorTesoreria
        linea = LineaCredito.objects.create(banco=self.bbva, tipo='GLOBAL', limite=D('100000'))
        with self.assertRaisesMessage(ErrorTesoreria, 'no alcanza'):
            self.prestamo('120000', linea)
        self.prestamo('60000', linea)
        self.assertEqual(fin.uso(linea)['utilizado'], D('60000'))
        f = CartaFianzaForm({'banco': self.bbva.pk, 'beneficiario': self.cliente.pk, 'linea': linea.pk,
                             'tipo': 'FIEL_CUMPLIMIENTO', 'moneda': 'PEN', 'monto': '50000', 'comision_pct': '0.5',
                             'emision': HOY.isoformat(), 'vencimiento': (HOY + timedelta(days=90)).isoformat(),
                             'estado': 'VIGENTE'})
        self.assertIn('linea', f.errors)
        dpf = fin.crear_deposito(DepositoPlazo(banco=self.bbva, cuenta=self.banco, monto=D('10000'), tasa=D('6'),
                                               inicio=HOY - timedelta(days=60), vencimiento=HOY + timedelta(days=30)),
                                 self.user)
        asiento = fin.devengar_depositos(PERIODO, self.user)
        dpf.refresh_from_db()
        self.assertGreater(dpf.devengado, 0)
        self.assertTrue(asiento.lineas.filter(cuenta__codigo__startswith='772', haber=dpf.devengado).exists())
        v = self.venta('10000', '88020')
        c = fin.ceder(CesionFactura(venta=v, tipo='FACTORING', entidad=self.bbva, monto=v.saldo, interes=D('200'),
                                    comision=D('50')), self.banco, self.user)
        self.assertEqual(Venta.objects.con_saldos().get(pk=v.pk).saldo, 0)
        self.assertEqual(c.neto, v.saldo - D('250'))
        self.assertTrue(Movimiento.objects.filter(concepto='GASTO_BANCARIO', monto=D('250')).exists())
        for nombre in ('lineas_credito', 'depositos', 'cesiones', 'cartas_fianza'):
            self.assertEqual(self.client.get(reverse(f'finanzas:{nombre}')).status_code, 200, nombre)

    def test_cronogramas_gracia_dobles_prepago_tasa_y_devengo(self):
        """FIN-06."""
        from finanzas import prestamos as srv
        primera = date(2027, 1, 15)
        frances = srv.cronograma(D('100000'), D('12'), 12, 1, primera)
        self.assertEqual(frances[0]['capital'] + frances[0]['interes'], D('8856.21'))
        aleman = srv.cronograma(D('120000'), D('12'), 12, 1, primera, sistema='ALEMAN')
        self.assertEqual({f['capital'] for f in aleman}, {D('10000.00')})
        self.assertGreater(aleman[0]['interes'], aleman[-1]['interes'])
        parcial = srv.cronograma(D('100000'), D('12'), 12, 1, primera, gracia=2, tipo_gracia='PARCIAL')
        self.assertEqual([f['capital'] for f in parcial[:2]], [D('0'), D('0')])
        self.assertEqual(parcial[-1]['saldo'], 0)
        total = srv.cronograma(D('100000'), D('12'), 12, 1, primera, gracia=2, tipo_gracia='TOTAL')
        self.assertEqual(len(total), 10)
        self.assertGreater(total[0]['saldo'] + total[0]['capital'], D('100000'))
        dobles = srv.cronograma(D('100000'), D('12'), 12, 1, primera, dobles=True)
        julio = next(f for f in dobles if f['fecha'].month == 7)
        enero = dobles[0]
        self.assertAlmostEqual(julio['capital'] + julio['interes'], 2 * (enero['capital'] + enero['interes']),
                               delta=D('0.02'))
        p = self.prestamo('12000', plazo=12)
        srv.prepagar(p, D('6000'), HOY, self.user, 'PLAZO')
        p.refresh_from_db()
        self.assertEqual(p.saldo_capital, D('6000'))
        self.assertLess(p.cuotas.count(), 12)
        srv.reprogramar(p, D('20'), 'Reajuste de la tasa variable del contrato', self.user)
        p.refresh_from_db()
        self.assertEqual(p.tasa_anual, D('20'))
        self.assertEqual(p.saldo_capital, D('6000'))
        asiento = srv.devengar(PERIODO, self.user)
        self.assertTrue(asiento.lineas.filter(cuenta__codigo__startswith='673').exists())
        self.assertTrue(Asiento.objects.filter(extorna=asiento).exists())
        fila = next(f for f in srv.deuda_corriente() if f['prestamo'].pk == p.pk)
        self.assertEqual(fila['corriente'] + fila['no_corriente'], D('6000'))
        r = self.client.get(reverse('finanzas:prestamos_cierre'))
        self.assertContains(r, 'corriente')
        r = self.client.get(reverse('finanzas:prestamo', args=[p.pk]))
        self.assertContains(r, 'Prepago de capital')

    def test_arrendamiento_niif16(self):
        """FIN-06: derecho de uso, pasivo y asiento mensual solo en el libro NIIF."""
        from finanzas import niif16
        from finanzas.models import Arrendamiento
        a = niif16.crear(Arrendamiento(arrendador=self.prov, descripcion='Local de Miraflores', cuota=D('1000'),
                                       plazo=12, tasa=D('10'), inicio=HOY.replace(day=1)), self.user)
        i = niif16.tasa_mensual(D('10'))
        esperado = sum((D('1000') / (1 + i) ** k for k in range(12)), D('0')).quantize(D('0.01'))
        self.assertEqual(a.pasivo_inicial, esperado)
        self.assertEqual(a.cuotas.order_by('-numero').first().saldo, 0)
        self.assertEqual(a.asiento_inicial.norma, 'NIIF')
        asiento = niif16.registrar_mes(PERIODO, self.user)
        self.assertEqual(asiento.norma, 'NIIF')
        codigos = {l.cuenta.codigo[:3] for l in asiento.lineas.select_related('cuenta')}
        self.assertTrue({'452', '681', '392', '635'} <= codigos, codigos)
        self.assertEqual(self.client.get(reverse('finanzas:arrendamientos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('finanzas:arrendamiento', args=[a.pk])).status_code, 200)


class RiesgoEIndicadoresTest(FinanzasBase):
    def test_posicion_en_dolares_y_sensibilidad(self):
        """FIN-07."""
        from finanzas import riesgo_cambiario as rc
        from finanzas.models import Forward
        antes = rc.posicion()
        Cuenta.objects.create(nombre='BCP US$', banco='BCP', tipo='BANCO', moneda='USD', saldo_inicial=D('1000'))
        self.compra('500', '7201', moneda='USD')  # 590 con IGV
        Forward.objects.create(banco=self.bbva, tipo='VENTA', monto_usd=D('300'), tc_pactado=D('3.5'),
                               vencimiento=HOY + timedelta(days=60))
        p = rc.posicion()
        self.assertEqual((p['activos'] - antes['activos'], p['pasivos'] - antes['pasivos'],
                          p['neta'] - antes['neta'], p['cubierta'] - antes['cubierta']),
                         (D('1000'), D('590'), D('410'), D('110')))
        s = next(x for x in p['sensibilidad'] if x['variacion'] == 10)
        self.assertEqual(s['sin_cobertura'], (p['neta'] * p['tc'] * D('0.1')).quantize(D('0.01')))
        self.assertContains(self.client.get(reverse('finanzas:posicion_moneda')), 'Sensibilidad')
        self.assertEqual(self.client.get(reverse('finanzas:forwards')).status_code, 200)

    def test_tablero_financiero_y_analisis(self):
        """FIN-08."""
        from finanzas import indicadores as ind
        caja, _ = CuentaContable.objects.get_or_create(codigo='104199', defaults={'nombre': 'Banco prueba',
                                                                                  'imputable': True})
        prov, _ = CuentaContable.objects.get_or_create(codigo='421299', defaults={'nombre': 'Facturas prueba',
                                                                                  'imputable': True})
        capital, _ = CuentaContable.objects.get_or_create(codigo='501199', defaults={'nombre': 'Capital prueba',
                                                                                    'imputable': True})
        antes = ind.calcular(ind.datos(PERIODO))
        a = Asiento.objects.create(fecha=HOY, glosa='Prueba del tablero')
        AsientoLinea.objects.create(asiento=a, cuenta=caja, debe=D('5000'))
        AsientoLinea.objects.create(asiento=a, cuenta=prov, haber=D('2000'))
        AsientoLinea.objects.create(asiento=a, cuenta=capital, haber=D('3000'))
        d = ind.datos(PERIODO)
        self.assertGreaterEqual(d['activo_corriente'], D('5000'))
        despues = ind.calcular(d)
        self.assertEqual(despues['capital_trabajo'] - antes['capital_trabajo'], D('3000'))
        r = self.client.get(reverse('finanzas:tablero_financiero'))
        self.assertContains(r, 'Liquidez corriente')
        self.assertIn('spreadsheetml', self.client.get(reverse('finanzas:tablero_financiero') + '?formato=excel')[
            'Content-Type'])
        self.assertContains(self.client.get(reverse('finanzas:analisis_estados')), 'Análisis vertical')
