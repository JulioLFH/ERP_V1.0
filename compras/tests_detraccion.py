"""Detracción: el % (y el código) se toma del producto y la constancia del depósito pasa a la compra y al PLE 8.1
(v1.71.0)."""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.forms import ProductoForm
from core.models import Empresa, Producto, Tercero
from finanzas.models import Cuenta, Movimiento
from ventas.models import Venta

from .models import Compra


class DetraccionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.servicio = Producto.objects.get(codigo='P003')
        cls.servicio.detraccion_codigo, cls.servicio.detraccion_pct = '18', D('12')
        cls.servicio.save()
        cls.prov = Tercero.objects.filter(tipo='PROVEEDOR', tipo_doc='6').first()
        # una sola cuenta de detracciones activa: la impresión muestra la de la empresa (el demo trae otra)
        Cuenta.objects.filter(es_detracciones=True).update(activo=False)
        cls.bn = Cuenta.objects.create(nombre='BN detracciones', banco='BN', es_detracciones=True,
                                       numero='00-068-123456', saldo_inicial=D('10000'))

    def setUp(self):
        Empresa.objects.update(exigir_orden_compra=False, bloquear_deuda_vencida=False)
        self.client.force_login(self.user)
        plazo = patch('core.utils.DIAS_ENVIO_SUNAT', 3650)  # ventas con fechas fijas de octubre (VEN-20)
        plazo.start()
        self.addCleanup(plazo.stop)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def compra(self, numero, precio, pct='0'):
        data = {
            'tipo_comprobante': '01', 'serie': 'F700', 'numero': numero, 'tercero': self.prov.pk,
            'fecha_emision': '2026-10-01', 'fecha_vencimiento': '', 'periodo': '', 'clasificacion': 'MERCADERIA',
            'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
            'detraccion_pct': pct, 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk, 'items-0-descripcion': 'Servicio',
            'items-0-cantidad': '1', 'items-0-precio_unitario': precio,
        }
        r = self.client.post(reverse('compras:nuevo'), data)
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Compra.objects.get(serie='F700', numero=numero), data

    def test_el_porcentaje_sale_del_producto(self):
        c, data = self.compra('1', '1000')  # 1180 > 700
        self.assertEqual((c.detraccion_pct, c.detraccion_monto), (D('12'), D('141.60')))
        chica, _ = self.compra('2', '500')  # 590: no llega al mínimo del SPOT
        self.assertEqual(chica.detraccion_pct, 0)
        propio, _ = self.compra('3', '1000', pct='10')  # el % escrito en la factura manda
        self.assertEqual(propio.detraccion_pct, D('10'))
        # al editar se respeta lo que diga la factura, aunque sea 0
        item = c.items.first()
        edit = dict(data, detraccion_pct='0', **{'items-INITIAL_FORMS': '1', 'items-0-id': item.pk,
                                                 'items-0-documento': c.pk})
        self.assertEqual(self.client.post(reverse('compras:editar', args=[c.pk]), edit).status_code, 302)
        c.refresh_from_db()
        self.assertEqual((c.detraccion_pct, c.detraccion_monto), (0, 0))

    def test_constancia_del_deposito_en_la_compra_y_el_ple(self):
        c, _ = self.compra('10', '1000')
        r = self.client.post(reverse('finanzas:pago') + f'?doc={c.pk}&detraccion=1', {
            'cuenta': self.bn.pk, 'fecha': '2026-10-05', 'medio_pago': 'DEPOSITO', 'numero_operacion': '78451236',
            'es_detraccion': 'on', 'detraccion': '1', f'monto_{c.pk}': '141.60'})
        self.assertEqual(r.status_code, 302)
        mov = Movimiento.objects.get(compra=c)
        self.assertEqual(mov.concepto, 'DETRACCION')
        c.refresh_from_db()
        self.assertEqual((c.detraccion_constancia, c.detraccion_fecha), ('78451236', date(2026, 10, 5)))
        self.assertContains(self.client.get(reverse('compras:detalle', args=[c.pk])), 'N° 78451236')
        # Registro de Compras 8.1: campos 32 (fecha) y 33 (número) de la constancia
        txt = self.client.get(reverse('compras:registro') + '?formato=ple&periodo=202610').content.decode()
        campos = next(l for l in txt.splitlines() if '|F700|' in l and '|10|' in l).split('|')
        self.assertEqual((campos[31], campos[32]), ('05/10/2026', '78451236'))
        # lo corregido a mano no se pisa con otro depósito
        Compra.objects.filter(pk=c.pk).update(detraccion_constancia='MANUAL1')
        Movimiento.objects.create(cuenta=self.bn, fecha=date(2026, 10, 6), tipo='EGRESO', concepto='DETRACCION',
                                  numero_operacion='999', compra=c, monto=D('1'))
        c.refresh_from_db()
        self.assertEqual(c.detraccion_constancia, 'MANUAL1')
        # al anular el depósito, la constancia que vino de él se quita
        Compra.objects.filter(pk=c.pk).update(detraccion_constancia='78451236')
        self.client.post(reverse('finanzas:movimiento_eliminar', args=[mov.pk]), {'motivo': 'Depósito mal registrado'})
        c.refresh_from_db()
        self.assertEqual((c.detraccion_constancia, c.detraccion_fecha), ('999', date(2026, 10, 6)))

    def test_venta_toma_porcentaje_y_codigo_del_producto(self):
        cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        r = self.client.post(reverse('ventas:nuevo'), {
            'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': cli.pk, 'fecha_emision': '2026-10-01',
            'fecha_vencimiento': '2026-10-31', 'forma_pago': 'CREDITO', 'cuotas': '2', 'dias_entre_cuotas': '30',
            'moneda': 'PEN', 'tipo_cambio': '1',
            'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
            'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk, 'items-0-descripcion': 'Servicio',
            'items-0-cantidad': '1', 'items-0-precio_unitario': '2000'})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        v = Venta.objects.latest('id')
        self.assertEqual((v.detraccion_pct, v.detraccion_codigo, v.detraccion_monto), (D('12'), '18', D('283.20')))
        # representación impresa: leyenda SPOT, bien o servicio, monto en soles, cuenta del BN y cuotas netas
        r = self.client.get(reverse('ventas:imprimir', args=[v.pk]))
        for texto in ('Sistema de Pago de Obligaciones Tributarias', '020 Mantenimiento y reparación',
                      'S/ 283.20', '00-068-123456', 'Monto neto pendiente de pago: S/ 2,076.80', '30/11/2026'):
            self.assertContains(r, texto)
        self.assertEqual([c[2] for c in v.cronograma_cuotas], [D('1038.40'), D('1038.40')])

    def venta(self):
        cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        r = self.client.post(reverse('ventas:nuevo'), {
            'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': cli.pk, 'fecha_emision': '2026-10-01',
            'fecha_vencimiento': '', 'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1',
            'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
            'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk, 'items-0-descripcion': 'Servicio',
            'items-0-cantidad': '1', 'items-0-precio_unitario': '2000'})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Venta.objects.latest('id')

    def test_cliente_paga_el_total_y_la_empresa_deposita(self):
        v = self.venta()  # 2360, detracción 283.20
        self.assertEqual(v.detraccion_estado, ('PENDIENTE', None))
        banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN', es_detracciones=False).first()
        self.client.post(reverse('finanzas:cobranza') + f'?doc={v.pk}', {
            'cuenta': banco.pk, 'fecha': '2026-10-01', 'medio_pago': 'TRANSFERENCIA', f'monto_{v.pk}': '2360'})
        v = Venta.objects.get(pk=v.pk)
        self.assertEqual(v.saldo, 0)
        self.assertEqual(v.detraccion_estado, ('SIN_DEPOSITO', date(2026, 10, 1)))
        self.assertEqual(v.detraccion_plazo, date(2026, 10, 8))  # 5 días hábiles (jue 01 → jue 08)
        r = self.client.get(reverse('ventas:detalle', args=[v.pk]))
        self.assertContains(r, 'hasta el 08/10/2026')
        self.assertContains(self.client.get(reverse('ventas:detracciones')), 'la empresa debe depositarla')
        r = self.client.post(reverse('ventas:deposito_detraccion', args=[v.pk]), {
            'origen': banco.pk, 'destino': self.bn.pk, 'fecha': '2026-10-06', 'numero': '55500011'})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        v = Venta.objects.get(pk=v.pk)
        self.assertEqual((v.detraccion_estado, v.detraccion_constancia, v.detraccion_fecha),
                         (('DEPOSITADA', None), '55500011', date(2026, 10, 6)))
        entrada = v.detraccion_deposito
        self.assertEqual((entrada.cuenta, entrada.monto, entrada.transferencia_par.cuenta),
                         (self.bn, D('283.20'), banco))
        self.assertEqual(v.saldo, 0)  # la transferencia no cuenta como cobro de la venta
        # anulada la transferencia, la venta vuelve a «sin depósito»
        self.client.post(reverse('finanzas:movimiento_eliminar', args=[entrada.pk]), {'motivo': 'Depósito duplicado'})
        v = Venta.objects.get(pk=v.pk)
        self.assertEqual((v.detraccion_estado[0], v.detraccion_constancia, v.detraccion_deposito), ('SIN_DEPOSITO', '', None))

    def test_cliente_deposita_la_detraccion(self):
        v = self.venta()
        self.client.post(reverse('finanzas:cobranza') + f'?doc={v.pk}&detraccion=1', {
            'cuenta': self.bn.pk, 'fecha': '2026-10-03', 'medio_pago': 'DEPOSITO', 'numero_operacion': '44400022',
            'es_detraccion': 'on', 'detraccion': '1', f'monto_{v.pk}': '283.20'})
        v = Venta.objects.get(pk=v.pk)
        self.assertEqual((v.detraccion_estado[0], v.detraccion_constancia, v.detraccion_fecha),
                         ('DEPOSITADA', '44400022', date(2026, 10, 3)))

    def test_compra_pagada_por_el_total_pide_constancia_del_proveedor(self):
        c, _ = self.compra('20', '1000')  # 1180, detracción 141.60
        banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN', es_detracciones=False).first()
        Cuenta.objects.filter(pk=banco.pk).update(saldo_inicial=D('100000'))
        r = self.client.post(reverse('finanzas:pago') + f'?doc={c.pk}', {
            'cuenta': banco.pk, 'fecha': '2026-10-02', 'medio_pago': 'TRANSFERENCIA', f'monto_{c.pk}': '1180'})
        self.assertEqual(r.status_code, 302)
        c = Compra.objects.get(pk=c.pk)
        self.assertEqual(c.detraccion_estado, ('SIN_DEPOSITO', date(2026, 10, 2)))
        self.assertContains(self.client.get(reverse('compras:detalle', args=[c.pk])), 'pídale la constancia')
        self.client.post(reverse('compras:constancia_detraccion', args=[c.pk]),
                         {'numero': '33300044', 'fecha': '2026-10-07'})
        c = Compra.objects.get(pk=c.pk)
        self.assertEqual((c.detraccion_estado[0], c.detraccion_constancia, c.detraccion_fecha),
                         ('DEPOSITADA', '33300044', date(2026, 10, 7)))
        txt = self.client.get(reverse('compras:registro') + '?formato=ple&periodo=202610').content.decode()
        campos = next(l for l in txt.splitlines() if '|F700|' in l and '|20|' in l).split('|')
        self.assertEqual((campos[31], campos[32]), ('07/10/2026', '33300044'))
        r = self.client.get(reverse('compras:detracciones'), {'formato': 'excel'})
        self.assertIn('spreadsheetml', r['Content-Type'])

    def test_producto_exige_codigo_con_porcentaje(self):
        p = self.servicio
        datos = {campo: getattr(p, campo) for campo in ('clase', 'codigo', 'nombre', 'unidad', 'precio_venta')}
        form = ProductoForm(dict(datos, puede_venderse='on', detraccion_pct='12', detraccion_codigo=''), instance=p)
        self.assertIn('detraccion_codigo', form.errors)
        form = ProductoForm(dict(datos, puede_venderse='on', detraccion_pct='', detraccion_codigo='18'), instance=p)
        self.assertIn('detraccion_pct', form.errors)
