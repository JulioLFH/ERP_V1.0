"""v1.70: en el Registro de compras, la compra sin cuenta contable ni centro de costo (ni reparto por centros) sale
en rojo y se puede filtrar."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, DistribucionCompra
from contabilidad.models import CentroCosto, CuentaContable

from .models import Tercero

D = Decimal
HOY = timezone.localdate()


class ComprasSinImputacionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.436'), D('3.436'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v170', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.436'), D('3.436')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100049008',
                                           nombre='PROVEEDOR IMPUTACION SAC')

    def compra(self, numero, **extra):
        return Compra.objects.create(tercero=self.prov, tipo_comprobante='01', serie='F900', numero=numero,
                                     fecha_emision=HOY, base_imponible=D('100'), igv=D('18'), total=D('118'),
                                     clasificacion='GASTO', ingresar_almacen=False, **extra)

    def test_filas_en_rojo_y_filtro(self):
        centro = CentroCosto.objects.filter(activo=True).first() or CentroCosto.objects.first()
        cuenta = CuentaContable.objects.filter(imputable=True).first()
        sin_nada = self.compra('1')
        con_centro = self.compra('2', centro_costo=centro)
        con_cuenta = self.compra('3', cuenta_contable=cuenta)
        repartida = self.compra('4')
        DistribucionCompra.objects.create(compra=repartida, centro_costo=centro, porcentaje=D('100'))
        anulada = self.compra('5', estado='ANULADO')
        historica = self.compra('6', es_historico=True)  # migrada de otro sistema: no se puede corregir
        r = self.client.get(reverse('compras:lista') + '?q=F900')
        rojas = {d.pk for d in r.context['page_obj'] if d.sin_imputacion}
        self.assertEqual(rojas, {sin_nada.pk})
        self.assertContains(r, 'table-danger', count=1)
        r = self.client.get(reverse('compras:lista') + '?q=F900&sin_imputacion=1')
        self.assertEqual([d.pk for d in r.context['page_obj']], [sin_nada.pk])
        for c in (con_centro, con_cuenta, repartida, anulada, historica):
            self.assertNotContains(r, c.numero_completo)
        # Ventas no se marca
        self.assertNotContains(self.client.get(reverse('ventas:lista')), 'table-danger')
