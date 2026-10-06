"""v1.20: punto de venta, CRM (embudo), ventas en cuotas, Libro de Inventarios y Balances y crédito con letras."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import sunat
from core.models import Producto, Tercero
from finanzas.models import Cuenta, Movimiento

from .models import Oportunidad, Venta, VentaItem

D = Decimal
HOY = date.today()


class PosCrmTest(TestCase):
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
        self.caja = Cuenta.objects.create(nombre='Caja tienda', tipo='CAJA')
        self.producto = Producto.objects.get(codigo='P003')

    def test_pos_emite_boleta_y_cobra(self):
        r = self.client.get(reverse('ventas:pos_productos'), {'q': 'P003'})
        self.assertEqual(json.loads(r.content)[0]['codigo'], 'P003')
        stock = self.producto.stock
        r = self.client.post(reverse('ventas:pos'), json.dumps({
            'tipo_comprobante': '03', 'caja': self.caja.pk, 'medio': 'EFECTIVO', 'recibido': '200',
            'items': [{'id': self.producto.pk, 'cantidad': 2, 'precio': '50'}]}), content_type='application/json')
        d = json.loads(r.content)
        self.assertTrue(d['ok'], d)
        v = Venta.objects.get(pk=d['venta'])
        self.assertEqual((v.tipo_comprobante, v.tercero.nombre, v.total), ('03', 'CLIENTES VARIOS', D('118.00')))
        self.assertEqual(D(d['vuelto']), D('82.00'))
        self.assertEqual(Venta.objects.get(pk=v.pk).saldo, 0)
        self.assertEqual(Movimiento.objects.get(venta=v).cuenta, self.caja)
        self.assertEqual(Producto.objects.get(pk=self.producto.pk).stock, stock - 2)
        self.assertEqual(sunat.payload_comprobante(v)['cliente_tipo_de_documento'], '-')  # boleta a clientes varios
        # sin stock suficiente no emite
        r = self.client.post(reverse('ventas:pos'), json.dumps({
            'tipo_comprobante': '03', 'caja': self.caja.pk, 'items': [{'id': self.producto.pk, 'cantidad': 999999,
                                                                      'precio': '50'}]}),
            content_type='application/json')
        self.assertFalse(json.loads(r.content)['ok'])

    def test_crm_embudo_y_etapas(self):
        cliente = Tercero.objects.filter(tipo='CLIENTE').first()
        r = self.client.post(reverse('ventas:crm_nueva'), {
            'nombre': 'Contrato anual', 'tercero': cliente.pk, 'etapa': 'PROPUESTA', 'monto': '10000'})
        o = Oportunidad.objects.get()
        self.assertRedirects(r, reverse('ventas:crm_oportunidad', args=[o.pk]))
        self.assertEqual(o.ponderado, D('5000'))
        self.assertContains(self.client.get(reverse('ventas:crm')), 'Contrato anual')
        r = self.client.post(reverse('ventas:crm_etapa', args=[o.pk]), {'etapa': 'PERDIDA'})
        self.assertEqual(r.status_code, 422)  # perdida exige motivo
        self.client.post(reverse('ventas:crm_etapa', args=[o.pk]), {'etapa': 'GANADA'})
        o.refresh_from_db()
        self.assertEqual((o.etapa, o.prob), ('GANADA', 100))
        self.client.post(reverse('ventas:crm_oportunidad', args=[o.pk]), {
            'accion': 'actividad', 'tipo': 'LLAMADA', 'fecha': HOY.isoformat(), 'descripcion': 'Coordinar entrega'})
        self.assertEqual(o.actividades.count(), 1)

    def test_venta_en_cuotas(self):
        cliente = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        v = Venta.objects.create(tercero=cliente, serie='F001', numero='77001', forma_pago='CREDITO', cuotas=3,
                                 dias_entre_cuotas=30, fecha_vencimiento=HOY + timedelta(days=30))
        VentaItem.objects.create(documento=v, descripcion='Servicio', cantidad=1, precio_unitario=D('1000'))
        v.calcular_totales()
        v.save()
        cuotas = v.cronograma_cuotas
        self.assertEqual([c[2] for c in cuotas], [D('393.33'), D('393.33'), D('393.34')])
        self.assertEqual(cuotas[2][1], HOY + timedelta(days=90))
        payload = sunat.payload_comprobante(v)
        self.assertEqual(len(payload['venta_al_credito']), 3)

    def test_libro_inventarios_y_balances(self):
        r = self.client.get(reverse('contabilidad:libro_inventarios'), {'formato': '3.3',
                                                                        'periodo': HOY.strftime('%Y-%m')})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse('contabilidad:libro_inventarios'), {
            'formato': '3.12', 'periodo': HOY.strftime('%Y-%m'), 'formato_salida': 'excel'})
        self.assertIn('spreadsheetml', r['Content-Type'])
        self.assertContains(self.client.get(reverse('contabilidad:libro_inventarios'), {'formato': '3.1'}),
                            'estado financiero')

    def test_limite_de_credito_incluye_letras(self):
        from finanzas.models import Letra
        cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100100102', nombre='CON LETRAS',
                                         direccion='Av. 1', limite_credito=D('1000'))
        Letra.objects.create(numero='LC-1', tipo='COBRAR', tercero=cliente, monto=D('900'),
                             fecha_vencimiento=HOY + timedelta(days=30))
        datos = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': cliente.pk,
                 'fecha_emision': HOY.isoformat(), 'fecha_vencimiento': (HOY + timedelta(days=30)).isoformat(),
                 'forma_pago': 'CREDITO', 'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
                 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0',
                 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
                 'items-MAX_NUM_FORMS': '1000', 'items-0-descripcion': 'Servicio', 'items-0-cantidad': '1',
                 'items-0-precio_unitario': '200', 'items-0-descuento_pct': '0'}
        r = self.client.post(reverse('ventas:nuevo'), datos)
        self.assertContains(r, 'límite de crédito')
