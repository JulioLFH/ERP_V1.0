"""v1.23: licitaciones con cuadro comparativo y contratos marco."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import CentroCosto
from core.models import Producto, Tercero

from . import abastecimiento as ab
from .models import ContratoMarco, Licitacion, OrdenCompra, OrdenCompraItem

D = Decimal
HOY = date.today()


class AbastecimientoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('compras', 'c@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.cc = CentroCosto.objects.filter(activo=True).first() or CentroCosto.objects.create(codigo='ADM',
                                                                                                nombre='Admin')
        self.prov_a = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100000011', nombre='PROV A')
        self.prov_b = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100000022', nombre='PROV B')
        self.p1 = Producto.objects.get(codigo='P003')
        self.p2 = Producto.objects.filter(tipo='BIEN').exclude(pk=self.p1.pk).first()

    def test_licitacion_cuadro_y_adjudicacion(self):
        r = self.client.post(reverse('compras:licitacion_nueva'), {
            'descripcion': 'Insumos del trimestre', 'fecha': HOY.isoformat(), 'centro_costo': self.cc.pk,
            'lin-TOTAL_FORMS': '2', 'lin-INITIAL_FORMS': '0', 'lin-MIN_NUM_FORMS': '0', 'lin-MAX_NUM_FORMS': '1000',
            'lin-0-producto': self.p1.pk, 'lin-0-descripcion': '', 'lin-0-cantidad': '10',
            'lin-1-producto': self.p2.pk, 'lin-1-descripcion': '', 'lin-1-cantidad': '4'})
        lic = Licitacion.objects.get()
        self.assertRedirects(r, reverse('compras:licitacion', args=[lic.pk]))
        l1, l2 = lic.lineas.all()
        self.client.post(reverse('compras:licitacion', args=[lic.pk]), {
            'accion': 'oferta', 'proveedor': self.prov_a.pk, 'moneda': 'PEN', 'tipo_cambio': '1', 'plazo_entrega': '3',
            f'precio_{l1.pk}': '5', f'precio_{l2.pk}': '20'})
        # B ofrece en dólares: 1.40 x 3.5 = 4.90 (más barato en el ítem 1); no cotiza el ítem 2
        ab.registrar_oferta(lic, self.prov_b, {l1.pk: D('1.40'), l2.pk: None},
                            {'moneda': 'USD', 'tipo_cambio': D('3.5'), 'plazo_entrega': 10})
        ofertas, filas, totales = ab.cuadro(lic)
        self.assertEqual(filas[0]['mejor'], D('4.900'))
        self.assertTrue(filas[0]['celdas'][1]['mejor'])
        self.assertEqual(totales[0]['total'], D('130.00'))
        oa, ob = ofertas
        with self.assertRaises(ab.ErrorAbastecimiento):  # A no es el más barato del ítem 1: hay que justificar
            ab.adjudicar(lic, {l1.pk: oa.pk, l2.pk: oa.pk}, self.user)
        r = self.client.post(reverse('compras:licitacion', args=[lic.pk]), {
            'accion': 'adjudicar', f'gana_{l1.pk}': ob.pk, f'gana_{l2.pk}': oa.pk})
        lic.refresh_from_db()
        self.assertEqual(lic.estado, 'ADJUDICADA')
        ocs = OrdenCompra.objects.filter(glosa__contains=lic.numero)
        self.assertEqual(ocs.count(), 2)
        oc_b = ocs.get(tercero=self.prov_b)
        self.assertEqual((oc_b.moneda, oc_b.estado, oc_b.items.get().precio_unitario), ('USD', 'PENDIENTE', D('1.4')))
        self.assertEqual(self.client.get(reverse('compras:licitacion', args=[lic.pk]) + '?formato=excel').status_code,
                         200)

    def test_contrato_marco_controla_precios_y_saldo(self):
        r = self.client.post(reverse('compras:contrato_nuevo'), {
            'proveedor': self.prov_a.pk, 'descripcion': 'Suministro anual', 'fecha_inicio': HOY.isoformat(),
            'fecha_fin': (HOY + timedelta(days=365)).isoformat(), 'moneda': 'PEN', 'monto_maximo': '500',
            'lin-TOTAL_FORMS': '1', 'lin-INITIAL_FORMS': '0', 'lin-MIN_NUM_FORMS': '0', 'lin-MAX_NUM_FORMS': '1000',
            'lin-0-producto': self.p1.pk, 'lin-0-precio_unitario': '10', 'lin-0-cantidad_maxima': '50'})
        c = ContratoMarco.objects.get()
        self.assertRedirects(r, reverse('compras:contrato', args=[c.pk]))
        # la orden nueva desde el contrato trae el proveedor y el precio pactado
        r = self.client.get(reverse('compras:oc_nuevo') + f'?contrato={c.pk}')
        self.assertContains(r, 'Pedido contra el contrato marco')
        oc = OrdenCompra.objects.create(numero='OC01-T1', tercero=self.prov_a, contrato=c, centro_costo=self.cc)
        OrdenCompraItem.objects.create(documento=oc, producto=self.p1, descripcion='x', cantidad=D('60'),
                                       precio_unitario=D('12'))
        oc.calcular_totales()
        oc.save()
        errores = ab.validar_orden(oc)
        self.assertEqual(len(errores), 3)  # precio mayor, supera la cantidad y el monto
        r = self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'APROBADO'})
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'PENDIENTE')
        oc.items.update(cantidad=D('20'), precio_unitario=D('10'), subtotal=D('200'))
        oc.calcular_totales()
        oc.save()
        self.assertEqual(ab.validar_orden(oc), [])
        self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'APROBADO'})
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'APROBADO')
        filas, usado = ab.consumo(c)
        self.assertEqual((filas[0]['saldo'], usado), (D('30'), D('200')))

    def test_pantallas(self):
        for nombre in ('licitaciones', 'licitacion_nueva', 'contratos', 'contrato_nuevo'):
            self.assertEqual(self.client.get(reverse(f'compras:{nombre}')).status_code, 200, nombre)
