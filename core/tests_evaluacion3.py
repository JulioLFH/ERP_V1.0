"""Tercera evaluación (v1.16): terminar OP confirmada con fecha y kardex cerrado (37), contactos con RUC antiguo
editables (31/18), guía con saldo pendiente (32), diferencia de precio factura vs recepción (36), reapertura con
permiso (29)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import AjustePrecioCompra, Compra, CompraItem, OrdenCompra, OrdenCompraItem
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea, PeriodoContable
from core.models import Almacen, PerfilUsuario, Producto, Tercero
from core.modulos import GRUPOS
from inventario import servicios as inv
from inventario.models import Operacion, TipoOperacion
from logistica.models import GuiaRemision
from ventas.models import Venta

D = Decimal
HOY = date.today()


class EvaluacionTresTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def test_terminar_op_confirmada_con_fecha_y_kardex_cerrado(self):
        from inventario.models import CierreKardex
        from produccion import servicios
        from produccion.models import CentroTrabajo, ListaMateriales, OrdenProduccion
        kit = Producto.objects.create(nombre='Kit', clase='PRODUCTO_TERMINADO')
        receta = ListaMateriales.objects.create(producto=kit, codigo='V1', cantidad_base=D('1'))
        receta.componentes.create(producto=Producto.objects.get(codigo='P003'), cantidad=D('1'))
        o = OrdenProduccion.objects.create(producto=kit, lista=receta, cantidad=D('2'),
                                           almacen_insumos=Almacen.principal(),
                                           almacen_destino=Almacen.principal())
        servicios.explotar(o)
        servicios.confirmar(o, self.admin)
        CierreKardex.objects.create(periodo='200001', fecha_corte=date(2000, 1, 31), estado='CERRADO')
        c = o.consumos.get()
        r = self.client.post(reverse('manufactura:orden_terminar', args=[o.pk]),
                             {f'consumo_{c.pk}': '2', 'cantidad_producida': '2', 'fecha': HOY.isoformat()})
        self.assertEqual(r.status_code, 302)  # antes: error 500 (fecha como texto con kardex cerrado)
        o.refresh_from_db()
        self.assertEqual((o.estado, o.fecha_inicio), ('TERMINADA', HOY))

    def test_contacto_con_ruc_antiguo_se_puede_editar(self):
        t = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS']).first()
        Tercero.objects.filter(pk=t.pk).update(numero_doc='20601234567', tipo_doc='6')  # dígito incorrecto
        t.refresh_from_db()
        r = self.client.get(reverse('tercero_editar', args=[t.pk]))
        self.assertContains(r, 'SUNAT rechazará')
        from core.forms import TerceroForm
        datos = {**TerceroForm(instance=t).initial, 'limite_credito': '5000'}
        datos = {k: ('' if v is None else v) for k, v in datos.items()}
        form = TerceroForm(datos, instance=t)
        self.assertTrue(form.is_valid(), form.errors)
        datos['numero_doc'] = '20601234568'  # cambiar a otro RUC inválido sí se rechaza
        self.assertFalse(TerceroForm(datos, instance=t).is_valid())

    def test_guia_propone_solo_el_saldo_pendiente(self):
        v = Venta.objects.filter(guias__estado='EMITIDA').first() or Venta.objects.filter(estado='REGISTRADO').first()
        from logistica.views import _initial_desde_venta, _saldos_despacho
        vendido, despachado, _ = _saldos_despacho(v)
        _, items = _initial_desde_venta(v)
        for it in items:
            if it['producto']:
                self.assertLessEqual(it['cantidad'], vendido[it['producto']] - despachado[it['producto']])

    def test_diferencia_de_precio_liquida_la_28(self):
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        mp = Producto.objects.create(nombre='Mantequilla', clase='MATERIA_PRIMA')
        oc = OrdenCompra.objects.create(numero='OC01-00009901', tercero=prov, estado='APROBADO')
        OrdenCompraItem.objects.create(documento=oc, producto=mp, descripcion='Mantequilla', cantidad=10,
                                       precio_unitario=D('10'))
        rec = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), fecha=HOY,
                                       orden_compra=oc, almacen_destino=Almacen.principal())
        rec.items.create(producto=mp, cantidad=10, costo_unitario=D('10'))
        inv.confirmar(rec, self.admin)
        # consumen 4 antes de la factura
        mp.mover_stock(-4, 'consumo de prueba', fecha=HOY, origen='AJUSTE', concepto='CONSUMO')
        compra = Compra.objects.create(tercero=prov, serie='F001', numero='5001', fecha_emision=HOY, orden_compra=oc,
                                       ingresar_almacen=False, clasificacion='MERCADERIA')
        CompraItem.objects.create(documento=compra, producto=mp, descripcion='Mantequilla', cantidad=10,
                                  precio_unitario=D('11'))
        compra.calcular_totales()
        compra.save()
        from compras.precios import liquidar
        liquidar(compra)
        aj = AjustePrecioCompra.objects.get(compra=compra)
        self.assertEqual((aj.diferencia, aj.a_inventario, aj.a_costo), (D('10'), D('6'), D('4')))
        mp.refresh_from_db()
        self.assertEqual(mp.costo_promedio, D('11.0000'))
        liquidar(compra)  # idempotente
        self.assertEqual(AjustePrecioCompra.objects.filter(compra=compra).count(), 1)
        periodo = HOY.strftime('%Y%m')
        centralizar_periodo(periodo)
        saldo_28 = sum((l.debe - l.haber for l in AsientoLinea.objects.filter(
            cuenta=mp.cuenta_compra.destino_debe, asiento__periodo=periodo, asiento__compra=compra) |
            AsientoLinea.objects.filter(cuenta=mp.cuenta_compra.destino_debe, asiento__periodo=periodo,
                                        asiento__origen='INVENTARIO')), D('0'))
        self.assertEqual(saldo_28, D('0'))

    def test_reabrir_periodo_con_permiso(self):
        PeriodoContable.objects.create(periodo='202402', cerrado=True, pendiente=False)
        u = User.objects.create_user('conta', password='x')
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['contabilidad'])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=['contabilidad.periodos', 'contabilidad.reabrir'])
        self.client.force_login(u)
        r = self.client.get(reverse('contabilidad:periodos'))
        self.assertContains(r, 'Motivo de la reapertura')
        self.client.post(reverse('contabilidad:periodos'), {'periodo': '202402', 'accion': 'abrir',
                                                            'motivo': 'Corregir compra mal registrada'})
        self.assertFalse(PeriodoContable.esta_cerrado('202402'))
