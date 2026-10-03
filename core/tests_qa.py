"""Pruebas de regresión de los hallazgos del informe de QA (v1.4)."""
import io
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.db.models import Sum
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from compras.models import Compra
from contabilidad.automatico import actualizar_pendientes, periodos_pendientes
from contabilidad.models import Asiento, AsientoLinea, CuentaContable, PeriodoContable
from core import sunat
from core.inventario import valor_inventario
from core.models import Almacen, Empresa, FacturacionConfig, Producto, Tercero
from finanzas.models import Cuenta, Movimiento
from logistica.models import Conductor, GuiaRemision, Vehiculo
from ventas.models import Venta

ITEMS = {'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000'}
TC = (Decimal('3.441'), Decimal('3.450'))


def saldo_cuenta(prefijo):
    agg = AsientoLinea.objects.filter(cuenta__codigo__startswith=prefijo).aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or 0) - (agg['h'] or 0)


class HallazgosQATest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('qa', 'qa@x.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=TC)
        p.start()
        self.addCleanup(p.stop)
        self.cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()

    def _venta(self, producto, cantidad, precio='100', **extra):
        data = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': self.cli.pk,
                'fecha_emision': '2026-10-03', 'fecha_vencimiento': '', 'forma_pago': 'CONTADO', 'moneda': 'PEN',
                'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0',
                'percepcion_pct': '0', 'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '',
                'descontar_stock': 'on', 'almacen': Almacen.principal().pk, 'glosa': '', 'items-TOTAL_FORMS': '1',
                'items-0-producto': producto.pk, 'items-0-descripcion': producto.nombre,
                'items-0-cantidad': str(cantidad), 'items-0-precio_unitario': precio, **ITEMS}
        data.update(extra)
        return self.client.post(reverse('ventas:nuevo'), data)

    # BUG-01 ------------------------------------------------------------------------------------------------
    def test_bug01_no_permite_vender_sin_stock(self):
        p = Producto.objects.get(codigo='P004')
        stock0 = p.stock
        r = self._venta(p, stock0 + 50)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Stock insuficiente', ' '.join(r.context['form'].non_field_errors()))
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0)
        # dos líneas del mismo producto que juntas superan el stock también se rechazan
        r = self._venta(p, stock0, **{'items-TOTAL_FORMS': '2', 'items-1-producto': p.pk, 'items-1-descripcion': 'x',
                                       'items-1-cantidad': '1', 'items-1-precio_unitario': '10'})
        self.assertEqual(r.status_code, 200)
        # con la opción de la empresa sí se permite
        Empresa.objects.update(permitir_stock_negativo=True)
        self.assertEqual(self._venta(p, stock0 + 1).status_code, 302)

    def test_bug01_dos_lineas_mismo_producto_descuentan_bien(self):
        p = Producto.objects.get(codigo='P003')
        stock0 = p.stock
        r = self._venta(p, 2, **{'items-TOTAL_FORMS': '2', 'items-1-producto': p.pk, 'items-1-descripcion': 'x',
                                  'items-1-cantidad': '3', 'items-1-precio_unitario': '10'})
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0 - 5)

    # BUG-02 / BUG-03 ---------------------------------------------------------------------------------------
    def _guia(self, **extra):
        p = Producto.objects.get(codigo='P003')
        data = {'serie': '', 'motivo_traslado': '01', 'descripcion_motivo': '', 'modalidad': '02', 'venta': '',
                'compra': '', 'transportista': '', 'efecto_stock': 'SALIDA', 'almacen_origen': Almacen.principal().pk,
                'almacen_destino': '', 'fecha_emision': '2026-10-03', 'fecha_traslado': '2026-10-03',
                'destinatario': self.cli.pk, 'partida_ubigeo': '150131', 'partida_direccion': 'Lima',
                'llegada_ubigeo': '150101', 'llegada_direccion': 'Lima', 'vehiculo': Vehiculo.objects.first().pk,
                'conductor': Conductor.objects.first().pk, 'peso_bruto': '5', 'unidad_peso': 'KGM',
                'numero_bultos': '1', 'doc_relacionado': '', 'observaciones': '', 'items-TOTAL_FORMS': '1',
                'items-0-producto': p.pk, 'items-0-descripcion': p.nombre, 'items-0-unidad': 'NIU',
                'items-0-cantidad': '1', **ITEMS}
        data.update(extra)
        return self.client.post(reverse('logistica:nueva') + '?tipo=09', data)

    def test_bug02_guia_no_descuenta_dos_veces(self):
        venta = Venta.objects.filter(tipo_comprobante='01', stock_aplicado=True).first()
        r = self._guia(venta=venta.pk, destinatario=venta.tercero_id)
        self.assertIn('efecto_stock', r.context['form'].errors)
        r = self._guia(venta=venta.pk, destinatario=venta.tercero_id, efecto_stock='NINGUNO')
        self.assertEqual(r.status_code, 302)

    def test_bug01_guia_salida_sin_stock(self):
        r = self._guia(**{'items-0-cantidad': '9999'})
        self.assertIn('Stock insuficiente', ' '.join(r.context['form'].non_field_errors()))

    def test_bug03_motivo04_usa_la_propia_empresa(self):
        r = self._guia(motivo_traslado='04', destinatario='', efecto_stock='TRASLADO',
                       almacen_destino=Almacen.objects.get(codigo='ALM02').pk)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(GuiaRemision.objects.latest('id').destinatario.numero_doc, Empresa.actual().ruc)
        r = self._guia(motivo_traslado='04', destinatario=self.cli.pk, efecto_stock='TRASLADO',
                       almacen_destino=Almacen.objects.get(codigo='ALM02').pk)
        self.assertIn('destinatario', r.context['form'].errors)

    # BUG-04 ------------------------------------------------------------------------------------------------
    def test_bug04_anular_exige_motivo_y_registra_usuario(self):
        v = Venta.objects.filter(tipo_comprobante='01').exclude(movimientos__isnull=False).first()
        self.client.post(reverse('ventas:anular', args=[v.pk]), {'motivo': ''})
        v.refresh_from_db()
        self.assertEqual(v.estado, 'REGISTRADO')
        self.client.post(reverse('ventas:anular', args=[v.pk]), {'motivo': 'Cliente desistió de la compra'})
        v.refresh_from_db()
        self.assertEqual((v.estado, v.anulado_por, v.motivo_anulacion), ('ANULADO', self.user,
                                                                         'Cliente desistió de la compra'))
        self.assertIsNotNone(v.anulado_en)
        self.assertContains(self.client.get(reverse('ventas:detalle', args=[v.pk])), 'Cliente desistió')

    def test_anular_compra_cuya_mercaderia_ya_se_vendio(self):
        c = Compra.objects.filter(stock_aplicado=True, clasificacion='MERCADERIA').exclude(
            movimientos__isnull=False).first()
        p = c.items.first().producto
        Producto.objects.filter(pk=p.pk).update(stock=0)
        from core.models import StockAlmacen
        StockAlmacen.objects.filter(producto=p).update(cantidad=0)
        self.client.post(reverse('compras:anular', args=[c.pk]), {'motivo': 'Prueba de anulación'})
        c.refresh_from_db()
        self.assertEqual(c.estado, 'REGISTRADO')

    # NEW-05 ------------------------------------------------------------------------------------------------
    def test_new05_no_permite_saldo_bancario_negativo(self):
        banco = Cuenta.objects.get(nombre='BCP Cta. Cte. Soles')
        datos = {'cuenta': banco.pk, 'fecha': '2026-10-03', 'tipo': 'EGRESO', 'concepto': 'OTRO',
                 'medio_pago': 'TRANSFERENCIA', 'monto': str(banco.saldo + 1)}
        r = self.client.post(reverse('finanzas:movimiento_nuevo'), datos)
        self.assertIn('monto', r.context['form'].errors)
        Cuenta.objects.filter(pk=banco.pk).update(permite_sobregiro=True)
        self.assertEqual(self.client.post(reverse('finanzas:movimiento_nuevo'), datos).status_code, 302)

    # NEW-01 / NEW-07 / NEW-02 / NEW-03 / NEW-08 -------------------------------------------------------------
    def test_new07_contabilidad_automatica_y_cuadre_con_auxiliares(self):
        self.assertTrue(periodos_pendientes())
        self.client.get(reverse('contabilidad:asientos'))  # abrir Contabilidad centraliza lo pendiente
        self.assertFalse(periodos_pendientes())
        self.assertTrue(Asiento.objects.filter(origen='APERTURA').exists())  # NEW-01: apertura automática
        p = Producto.objects.get(codigo='P002')
        self._venta(p, 1, precio='520')
        self.assertTrue(periodos_pendientes())  # la venta nueva marca el mes
        self.client.get(reverse('contabilidad:balance'))
        venta = Venta.objects.latest('id')
        self.assertTrue(venta.asientos.exists())
        # NEW-01 / NEW-08: cuentas 12 y 42 iguales a cuentas por cobrar y por pagar (al céntimo)
        cxc = sum(v.saldo_pen for v in Venta.objects.con_saldos().filter(estado='REGISTRADO'))
        cxp = sum(c.saldo_pen for c in Compra.objects.con_saldos().filter(estado='REGISTRADO'))
        self.assertEqual(saldo_cuenta('121'), cxc)
        self.assertEqual(-saldo_cuenta('42'), cxp)
        # NEW-02: la 20111 igual a la valorización del kardex
        hasta = max(PeriodoContable.objects.values_list('periodo', flat=True))
        from contabilidad.centralizar import _fin_mes
        self.assertEqual(saldo_cuenta('20111'), valor_inventario(_fin_mes(hasta))[1])
        # NEW-03: cada cuenta en soles cuadra exactamente con su auxiliar de caja/bancos
        for c in Cuenta.objects.filter(moneda='PEN'):
            self.assertEqual(saldo_cuenta(c.cuenta_contable.codigo), c.saldo, c.nombre)
        for a in Asiento.objects.all():
            self.assertTrue(a.cuadrado, a)

    def test_new03_subcuentas_y_diferencia_de_cambio(self):
        codigos = list(Cuenta.objects.values_list('cuenta_contable__codigo', flat=True))
        self.assertEqual(len(codigos), len(set(codigos)))  # una subcuenta distinta por caja/banco
        nueva = Cuenta.objects.create(tipo='BANCO', nombre='Interbank USD', moneda='USD', banco='INTERBANK')
        self.assertTrue(nueva.cuenta_contable.codigo.startswith('1041'))
        # factura en dólares (T.C. 3.300) cobrada cuando el T.C. está en 3.450 -> ganancia por diferencia de cambio
        p = Producto.objects.get(codigo='P002')
        self._venta(p, 1, precio='100', moneda='USD', tipo_cambio='3.300')
        v = Venta.objects.latest('id')
        usd = Cuenta.objects.get(nombre='BCP Cta. Cte. Dólares')
        self.client.post(reverse('finanzas:cobranza') + f'?doc={v.pk}', {
            'cuenta': usd.pk, 'fecha': '2026-10-03', 'medio_pago': 'TRANSFERENCIA', f'monto_{v.pk}': str(v.total)})
        actualizar_pendientes()
        a = Movimiento.objects.get(venta=v).asientos.get()
        self.assertEqual(a.lineas.get(cuenta__codigo='1212').haber, v.total_pen)
        self.assertEqual(a.lineas.get(cuenta__codigo='7761').haber, Decimal('17.70'))  # 118 x (3.45 - 3.30)
        self.assertTrue(Asiento.objects.filter(origen='CAMBIO').exists())  # ajuste al cierre de dólares

    def test_new03_detraccion_de_factura_en_dolares_se_deposita_en_soles(self):
        p = Producto.objects.get(codigo='P002')
        self._venta(p, 1, precio='1000', moneda='USD', tipo_cambio='3.400', detraccion_pct='12',
                    descontar_stock='')
        v = Venta.objects.latest('id')
        bn = Cuenta.objects.get(es_detracciones=True)
        saldo_bn = bn.saldo
        self.client.post(reverse('finanzas:cobranza') + f'?doc={v.pk}&detraccion=1', {
            'cuenta': bn.pk, 'fecha': '2026-10-03', 'medio_pago': 'DEPOSITO', 'es_detraccion': 'on',
            f'monto_{v.pk}': str(v.detraccion_monto)})
        m = Movimiento.objects.get(venta=v)
        self.assertEqual(m.monto_doc, v.detraccion_monto)  # US$ 141.60 aplicados a la factura
        self.assertEqual(m.monto, Decimal('481.44'))  # S/ en la cuenta del BN (141.60 x 3.400)
        self.assertEqual(bn.saldo, saldo_bn + Decimal('481.44'))
        self.assertEqual(v.saldo, v.total - v.detraccion_monto)
        actualizar_pendientes()
        agg = AsientoLinea.objects.filter(cuenta=bn.cuenta_contable).aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual((agg['d'] or 0) - (agg['h'] or 0), bn.saldo)

    # NEW-04 / NEW-06 ---------------------------------------------------------------------------------------
    def test_new04_consultas_constantes(self):
        url = reverse('ventas:pendientes')
        with CaptureQueriesContext(connection) as antes:
            self.client.get(url)
        for _ in range(8):
            Venta.objects.create(tercero=self.cli, serie='F009', numero=str(Venta.objects.count() + 100),
                                 forma_pago='CREDITO', total=Decimal('118'), igv=Decimal('18'),
                                 base_imponible=Decimal('100'))
        with CaptureQueriesContext(connection) as despues:
            self.client.get(url)
        self.assertLessEqual(len(despues), len(antes) + 2)  # no crece con la cantidad de documentos
        with CaptureQueriesContext(connection) as tablero:
            self.client.get(reverse('dashboard'))
        self.assertLess(len(tablero), 60)

    def test_new06_libro_diario_paginado(self):
        r = self.client.get(reverse('contabilidad:diario'))
        self.assertIn('page_obj', r.context)
        self.assertLessEqual(len(r.context['asientos'].object_list), 60)

    # BUG-05 ------------------------------------------------------------------------------------------------
    def test_bug05_estados_sunat_coherentes(self):
        cfg = FacturacionConfig.actual()
        cfg.proveedor, cfg.ruta, cfg.token = 'NUBEFACT', 'https://api.nubefact.com/api/v1/x', 't'
        cfg.save()
        v = Venta.objects.filter(tipo_comprobante='01').first()
        Tercero.objects.filter(pk=v.tercero_id).update(direccion='')
        v.refresh_from_db()
        self.client.post(reverse('ventas:enviar_sunat', args=[v.pk]))
        v.refresh_from_db()
        self.assertEqual(v.estado_sunat, 'NO_ENVIADO')  # no llegó al OSE: no es "Error de envío"
        self.assertIn('Corrija', v.sunat_descripcion)
        Tercero.objects.filter(pk=v.tercero_id).update(direccion='Av. Lima 1')
        ya_existe = sunat.ErrorFacturacion('Este documento ya existe en NubeFacT', 23)
        with patch('core.sunat._post', side_effect=[ya_existe, {'aceptada_por_sunat': True,
                                                                'sunat_description': 'Aceptada'}]):
            self.client.post(reverse('ventas:enviar_sunat', args=[v.pk]))
        v.refresh_from_db()
        self.assertEqual(v.estado_sunat, 'ACEPTADO')  # se sincroniza con el estado real del OSE
