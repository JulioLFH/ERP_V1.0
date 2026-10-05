"""Requerimientos internos (v1.16, punto 38): pedido del área, aprobación, entrega parcial, faltantes a compras y
asiento al gasto y centro de costo del área."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea, CentroCosto, CuentaContable
from core.models import Almacen, PerfilUsuario, Producto, Tercero
from core.modulos import GRUPOS

from . import requerimientos as srv
from .models import RequerimientoInterno

D = Decimal
HOY = date.today()


class RequerimientosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.almacen = Almacen.principal()
        self.mant = CentroCosto.objects.create(codigo='MNT', nombre='Mantenimiento', tipo='PRODUCCION')
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        self.guantes = Producto.objects.create(nombre='Guantes', clase='SUMINISTRO', proveedor=prov,
                                               precio_compra=D('5'))
        self.guantes.mover_stock(D('6'), 'Saldo de prueba', costo=D('5'), almacen=self.almacen)
        self.area = User.objects.create_user('area', password='x')
        self.area.groups.add(Group.objects.get_or_create(name=GRUPOS['requerimientos'])[0])
        self.jefe_area = User.objects.create_user('jefearea', password='x')
        self.jefe_area.groups.add(Group.objects.get_or_create(name=GRUPOS['requerimientos'])[0])
        PerfilUsuario.objects.create(usuario=self.jefe_area, acciones=['requerimientos.solicitar',
                                                                       'requerimientos.aprobar'])
        self.almacenero = User.objects.create_user('almacen', password='x')
        self.almacenero.groups.add(Group.objects.get_or_create(name=GRUPOS['inventario'])[0])

    def crear(self, cantidad='10'):
        self.client.force_login(self.area)
        r = self.client.post(reverse('requerimientos:nuevo'), {
            'fecha': HOY.isoformat(), 'fecha_requerida': '', 'centro_costo': self.mant.pk, 'almacen': self.almacen.pk,
            'cuenta_gasto': CuentaContable.objects.get(codigo='6343').pk, 'motivo': 'Mantenimiento de hornos',
            'it-TOTAL_FORMS': '1', 'it-INITIAL_FORMS': '0', 'it-MIN_NUM_FORMS': '1', 'it-MAX_NUM_FORMS': '1000',
            'it-0-producto': self.guantes.pk, 'it-0-cantidad': cantidad, 'it-0-observacion': '', 'accion': 'enviar'})
        req = RequerimientoInterno.objects.latest('id')
        self.assertRedirects(r, reverse('requerimientos:detalle', args=[req.pk]))
        return req

    def test_flujo_completo_con_entrega_parcial_y_compra_de_faltantes(self):
        req = self.crear('10')
        self.assertEqual(req.estado, 'ENVIADO')
        self.assertTrue(req.numero.startswith('RQ01-'))
        # quien pide no aprueba; el área sin permiso tampoco
        self.client.post(reverse('requerimientos:aprobar', args=[req.pk]))
        self.assertEqual(RequerimientoInterno.objects.get(pk=req.pk).estado, 'ENVIADO')
        self.client.force_login(self.jefe_area)
        self.client.post(reverse('requerimientos:aprobar', args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual((req.estado, req.aprobado_por), ('APROBADO', self.jefe_area))
        # el almacén entrega lo que hay (6 de 10)
        self.client.force_login(self.almacenero)
        item = req.items.get()
        r = self.client.post(reverse('requerimientos:atender', args=[req.pk]), {f'entregar_{item.pk}': '6'})
        req.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((req.estado, item.cantidad_atendida, item.pendiente), ('PARCIAL', D('6'), D('4')))
        op = req.atenciones.get()
        self.assertEqual((op.centro_costo, op.cuenta_gasto.codigo), (self.mant, '6343'))
        # lo que falta va a compras (el almacenero no crea OC: lo hace el administrador)
        ordenes = srv.pasar_a_compras(req, self.admin)
        self.assertEqual(ordenes[0].items.get().cantidad, D('4'))
        self.assertEqual(ordenes[0].centro_costo, self.mant)
        # asiento: el consumo va a 6343 con el centro de mantenimiento (planta -> 901), no a la 6561
        periodo = HOY.strftime('%Y%m')
        centralizar_periodo(periodo)
        gasto = AsientoLinea.objects.get(asiento__periodo=periodo, asiento__origen='INVENTARIO',
                                         cuenta__codigo='6343', es_destino=False)
        self.assertEqual((gasto.debe, gasto.centro_costo), (D('30'), self.mant))
        self.assertTrue(AsientoLinea.objects.filter(asiento=gasto.asiento, cuenta__codigo='901', debe=D('30')).exists())

    def test_no_entrega_mas_de_lo_pendiente_ni_sin_aprobar(self):
        req = self.crear('2')
        with self.assertRaises(srv.ErrorRequerimiento):
            srv.atender(req, self.admin, {req.items.get().pk: D('2')})  # aún no aprobado
        srv.aprobar(req, self.jefe_area)
        with self.assertRaises(srv.ErrorRequerimiento):
            srv.atender(req, self.admin, {req.items.get().pk: D('3')})
        srv.atender(req, self.admin, {req.items.get().pk: D('2')})
        self.assertEqual(RequerimientoInterno.objects.get(pk=req.pk).estado, 'ATENDIDO')

    def test_area_solo_ve_lo_suyo_y_pantallas(self):
        req = self.crear('1')
        otro = User.objects.create_user('otra_area', password='x')
        otro.groups.add(Group.objects.get_or_create(name=GRUPOS['requerimientos'])[0])
        self.client.force_login(otro)
        self.assertRedirects(self.client.get(reverse('requerimientos:detalle', args=[req.pk])),
                             reverse('requerimientos:lista'))
        self.client.force_login(self.almacenero)  # el almacén entra por ser de Inventario
        for url in [reverse('requerimientos:lista'), reverse('requerimientos:detalle', args=[req.pk]),
                    reverse('requerimientos:lista') + '?estado=APROBADO']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
