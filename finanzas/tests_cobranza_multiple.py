"""Cobranza y pago múltiple (varios terceros, saldo o detracción por comprobante) y constancias de detracción de
clientes desde el reporte de SUNAT (v1.72.0)."""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from core.models import Empresa, Producto, Tercero
from ventas.models import Venta

from .constancias import leer
from .models import Cuenta, Movimiento


class MultipleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.servicio = Producto.objects.get(codigo='P003')
        cls.servicio.detraccion_codigo, cls.servicio.detraccion_pct = '18', D('12')
        cls.servicio.save()
        provs = Tercero.objects.filter(tipo='PROVEEDOR', tipo_doc='6')[:2]
        cls.prov1, cls.prov2 = provs[0], provs[1]
        clis = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6')[:2]
        cls.cli1, cls.cli2 = clis[0], clis[1]
        cls.banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN', es_detracciones=False).first()
        Cuenta.objects.filter(pk=cls.banco.pk).update(saldo_inicial=D('100000'))
        cls.bn = Cuenta.objects.create(nombre='BN detracciones', banco='BN', es_detracciones=True,
                                       numero='00-068-123456')

    def setUp(self):
        Empresa.objects.update(exigir_orden_compra=False, bloquear_deuda_vencida=False)
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def compra(self, tercero, numero, precio):
        r = self.client.post(reverse('compras:nuevo'), {
            'tipo_comprobante': '01', 'serie': 'F800', 'numero': numero, 'tercero': tercero.pk,
            'fecha_emision': '2026-10-01', 'fecha_vencimiento': '2026-10-01', 'periodo': '',
            'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1',
            'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
            'icbper': '0', 'glosa': '', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk,
            'items-0-descripcion': 'Servicio', 'items-0-cantidad': '1', 'items-0-precio_unitario': precio})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Compra.objects.get(serie='F800', numero=numero)

    def venta(self, tercero, precio):
        r = self.client.post(reverse('ventas:nuevo'), {
            'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': tercero.pk, 'fecha_emision': '2026-10-01',
            'fecha_vencimiento': '', 'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1',
            'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
            'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk, 'items-0-descripcion': 'Servicio',
            'items-0-cantidad': '1', 'items-0-precio_unitario': precio})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Venta.objects.latest('id')

    def test_pago_multiple_de_varios_proveedores_saldo_y_detraccion(self):
        c1, c2 = self.compra(self.prov1, '1', '1000'), self.compra(self.prov2, '2', '2000')  # 1180 / 2360
        url = reverse('finanzas:pago_multiple') + '?q=F800'
        r = self.client.get(url)
        self.assertContains(r, 'data-saldo="1038.40" data-detr="141.60"')
        cabecera = {'cuenta': self.banco.pk, 'cuenta_detraccion': self.banco.pk, 'fecha': '2026-10-05',
                    'medio_pago': 'TRANSFERENCIA', 'numero_operacion': 'OP-77'}
        # el saldo completo con la detracción incluida no se le paga al proveedor
        self.client.post(url, {**cabecera, f'monto_{c1.pk}': '1180'})
        self.assertFalse(Movimiento.objects.filter(compra=c1).exists())
        # c1: detracción con su constancia y saldo de c2 en la misma operación (dos proveedores)
        r = self.client.post(url, {**cabecera, f'monto_{c1.pk}': '141.60', f'det_{c1.pk}': '1',
                                   f'constancia_{c1.pk}': '900100200', f'monto_{c2.pk}': '2076.80'})
        self.assertRedirects(r, reverse('finanzas:movimientos'), fetch_redirect_response=False)
        m1, m2 = Movimiento.objects.get(compra=c1), Movimiento.objects.get(compra=c2)
        self.assertEqual((m1.concepto, m1.numero_operacion, m1.tercero), ('DETRACCION', '900100200', self.prov1))
        self.assertEqual((m2.concepto, m2.numero_operacion, m2.tercero, m2.monto), ('PAGO', 'OP-77', self.prov2,
                                                                                    D('2076.80')))
        c1 = Compra.objects.get(pk=c1.pk)
        self.assertEqual((c1.detraccion_estado[0], c1.detraccion_constancia, c1.detraccion_fecha),
                         ('DEPOSITADA', '900100200', date(2026, 10, 5)))
        # ya depositada: ahora se sugiere el saldo completo
        self.assertContains(self.client.get(url), 'data-saldo="1038.40" data-detr="0')

    def test_cobranza_multiple_y_constancias_de_clientes_desde_sunat(self):
        v1, v2, v3 = self.venta(self.cli1, '1000'), self.venta(self.cli2, '2000'), self.venta(self.cli1, '3000')
        # cobranza múltiple: el saldo neto de v1 y v2 (dos clientes) al banco
        r = self.client.post(reverse('finanzas:cobranza_multiple'), {
            'cuenta': self.banco.pk, 'cuenta_detraccion': self.bn.pk, 'fecha': '2026-10-04',
            'medio_pago': 'TRANSFERENCIA', f'monto_{v1.pk}': '1038.40', f'monto_{v2.pk}': '2076.80'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Movimiento.objects.filter(concepto='COBRANZA', venta__in=[v1, v2]).count(), 2)
        # reporte de SUNAT: una constancia por factura (v1 y v2), una repetida y una de otra empresa
        reporte = ('Número de Constancia|Fecha de Pago|Monto de Depósito|Tipo de Comprobante|Serie de Comprobante|'
                   'Número de Comprobante|Número de Documento del Adquiriente\n'
                   f'500000001|05/10/2026|142.00|01|{v1.serie}|{int(v1.numero)}|{self.cli1.numero_doc}\n'
                   f'500000002|06/10/2026|283.00|01|{v2.serie}|{v2.numero}|{self.cli2.numero_doc}\n'
                   '500000003|06/10/2026|99.00|01|F999|123|20111111111\n')
        archivo = SimpleUploadedFile('constancias.txt', reporte.encode('latin-1'))
        self.assertEqual([r['constancia'] for r in leer(SimpleUploadedFile('c.txt', reporte.encode('latin-1')))],
                         ['500000001', '500000002', '500000003'])
        r = self.client.post(reverse('finanzas:constancias_clientes'), {'accion': 'leer', 'archivo': archivo})
        self.assertContains(r, 'Se registrará', count=2)
        self.assertContains(r, 'Factura no encontrada')
        r = self.client.post(reverse('finanzas:constancias_clientes'), {
            'accion': 'registrar', 'cuenta': self.bn.pk, 'constancia': ['500000001', '500000002']})
        self.assertRedirects(r, reverse('ventas:detracciones'), fetch_redirect_response=False)
        v1, v2, v3 = (Venta.objects.get(pk=v.pk) for v in (v1, v2, v3))
        self.assertEqual((v1.detraccion_estado[0], v1.detraccion_constancia, v1.detraccion_fecha, v1.saldo),
                         ('DEPOSITADA', '500000001', date(2026, 10, 5), D('-0.40')))
        self.assertEqual((v2.detraccion_constancia, v2.detraccion_fecha), ('500000002', date(2026, 10, 6)))
        self.assertEqual(Movimiento.objects.get(venta=v2, concepto='DETRACCION').cuenta, self.bn)
        self.assertEqual(v3.detraccion_estado[0], 'PENDIENTE')
        # volver a cargar el mismo reporte no duplica
        archivo = SimpleUploadedFile('constancias.txt', reporte.encode('latin-1'))
        r = self.client.post(reverse('finanzas:constancias_clientes'), {'accion': 'leer', 'archivo': archivo})
        self.assertContains(r, 'Ya registrada', count=2)
