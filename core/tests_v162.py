"""v1.62, checklist v1.56 (LOG-04): control de garita con placa, conductor, documento y hora, contra la orden de
compra o la guía, enlazado a la recepción del almacén; los montos solo con «Ver montos en garita»."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Almacen, PerfilUsuario, Tercero
from .modulos import GRUPOS

D = Decimal


class GaritaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v162', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def test_ingreso_enlace_a_la_recepcion_salida_y_montos(self):
        from compras.models import OrdenCompra
        from inventario.models import Operacion, TipoOperacion
        from logistica.models import RegistroGarita
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        oc = OrdenCompra.objects.create(numero='OC01-00000991', tercero=prov, estado='APROBADO', total=D('1180'))
        vigilante = User.objects.create_user('garita_v162', password='x')
        vigilante.groups.add(Group.objects.get_or_create(name=GRUPOS['logistica'])[0])
        PerfilUsuario.objects.create(usuario=vigilante, acciones=['logistica.garita'])
        self.client.force_login(vigilante)
        base = {'ingreso': '2026-10-21T08:15', 'placa': 'abc-123', 'conductor': 'Juan Pérez', 'conductor_doc': '40123456',
                'transportista': '', 'tercero': '', 'documento': '09 EG01-555', 'orden_compra': '', 'guia': '',
                'almacen': Almacen.principal().pk, 'bultos': '4', 'peso': '120', 'observaciones': ''}
        r = self.client.post(reverse('logistica:garita_nuevo'), {**base, 'tipo': 'DESPACHO'})
        self.assertContains(r, 'guía de remisión con la que sale')
        self.client.post(reverse('logistica:garita_nuevo'), {**base, 'tipo': 'RECEPCION', 'orden_compra': oc.pk})
        reg = RegistroGarita.objects.get()
        self.assertEqual((reg.placa, reg.tercero_id, reg.registrado_por), ('ABC123', prov.pk, vigilante))
        r = self.client.get(reverse('logistica:garita', args=[reg.pk]))
        self.assertNotContains(r, 'Valor de la mercadería')
        # la recepción de la OC aparece para enlazar
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), orden_compra=oc,
                                      tercero=prov, almacen_destino=Almacen.principal())
        self.client.post(reverse('logistica:garita', args=[reg.pk]), {'accion': 'enlazar', 'operacion': op.pk})
        self.client.post(reverse('logistica:garita', args=[reg.pk]), {'accion': 'salida'})
        reg.refresh_from_db()
        self.assertEqual(reg.operacion, op)
        self.assertIsNotNone(reg.salida)
        self.client.force_login(self.admin)
        r = self.client.get(reverse('logistica:garita', args=[reg.pk]))
        self.assertContains(r, 'Valor de la mercadería')
        self.assertContains(self.client.get(reverse('inventario:detalle', args=[op.pk])), reg.numero)
        self.assertContains(self.client.get(reverse('logistica:garita_lista')), 'ABC123')
