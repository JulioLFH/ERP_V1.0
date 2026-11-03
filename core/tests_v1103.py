"""Auditoría funcional v1.102 (02/11/2026): segregación en pedidos (VEN-01), versión automática (MAN-01), costo de las
devoluciones (VEN-02, COM-02), diferencia de cambio de cuentas por cobrar y pagar (FIN-04), calidad sin lote (CAL-01,
CAL-02), merma anormal (FIN-01), reparto de la variación (COS-01), tipo de cambio (FIN-05) y producto nuevo (MAE-01).
Un escenario por clase."""
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, CompraItem
from contabilidad.centralizar import _doc_texto, centralizar_periodo
from contabilidad.models import AsientoLinea
from core.models import Almacen, Bitacora, Kardex, Producto, Tercero, TipoCambio
from finanzas.models import Cuenta, Movimiento
from ventas.models import Cotizacion, CotizacionItem, Venta, VentaItem

HOY = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('admin_v1103', 'a@a.com', 'x')
        cls.otro = User.objects.create_superuser('otro_v1103', 'o@o.com', 'x')
        cls.almacen = Almacen.objects.create(codigo='PRI', nombre='Almacén principal', es_principal=True)
        cls.cli = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20600000011', nombre='CLIENTE SAC')
        cls.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000022',
                                          nombre='PROVEEDOR SAC')

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)


# ---------------------------------------------------------------- VEN-01
class PedidoSegregacionTest(Base):
    def test_quien_registra_el_pedido_no_lo_aprueba(self):
        from ventas.aprobacion_pedido import ErrorAprobacionPedido, aprobar, enviar
        ped = Cotizacion.objects.create(tipo='PED', numero='PD01-00000014', tercero=self.cli)
        CotizacionItem.objects.create(documento=ped, descripcion='Servicio', cantidad=1, precio_unitario=D('100'))
        Bitacora.objects.create(usuario=self.user, accion='CREAR', modelo=ped._meta.label, modelo_nombre='pedido',
                                objeto_id=str(ped.pk), objeto=str(ped))
        with self.assertRaisesMessage(ErrorAprobacionPedido, 'Segregación de funciones'):
            aprobar(ped, self.user)
        self.assertIn('enviado a aprobación', enviar(ped, self.user))  # sin motivos ya no se aprueba solo
        ped.refresh_from_db()
        self.assertEqual(ped.estado, 'PENDIENTE')
        aprobar(ped, self.otro)
        ped.refresh_from_db()
        self.assertEqual(ped.estado, 'APROBADO')


# ---------------------------------------------------------------- MAN-01
class VersionAutomaticaTest(Base):
    def test_la_version_con_hoja_reemplaza_a_la_automatica(self):
        from produccion.forms import VersionForm
        from produccion.models import (DESCRIPCION_AUTOMATICA, CentroTrabajo, HojaRuta, ListaMateriales,
                                       VersionFabricacion)
        from produccion.servicios import version_vigente
        silla = Producto.objects.create(nombre='Silla', clase='PRODUCTO_TERMINADO', precio_venta=D('100'))
        madera = Producto.objects.create(nombre='Madera', clase='MATERIA_PRIMA')
        receta = ListaMateriales.objects.create(producto=silla, codigo='AUD-SILLA-V1', cantidad_base=D('1'),
                                                estado='APROBADA', vigente_desde=date(2025, 1, 1))
        receta.componentes.create(producto=madera, cantidad=D('2'))
        auto = VersionFabricacion.objects.create(producto=silla, codigo=receta.codigo, lista=receta,
                                                 vigente_desde=date(2025, 1, 1),
                                                 descripcion=f'{DESCRIPCION_AUTOMATICA} (sin hoja de ruta)')
        self.assertEqual(auto.codigo, 'AUD-SILLA-V1')  # sin truncar a 10 caracteres
        puesto = CentroTrabajo.objects.create(codigo='CAR', nombre='Carpintería', costo_hora_mo=D('10'))
        ruta = HojaRuta.objects.create(codigo='R-SILLA', nombre='Silla', estado='APROBADA')
        ruta.operaciones.create(secuencia=10, centro=puesto, descripcion='Armado', horas_unidad=D('1'))
        datos = {'producto': silla.pk, 'codigo': 'AUD-SILLA-RUTA', 'lista': receta.pk, 'hoja': ruta.pk,
                 'lote_min': '0', 'vigente_desde': '2025-01-01', 'dias_fabricacion': '1', 'activa': 'on'}
        form = VersionForm(datos)
        self.assertTrue(form.is_valid(), form.errors)
        real = form.save()
        auto.refresh_from_db()
        self.assertFalse(auto.activa)
        self.assertEqual(version_vigente(silla, HOY), real)
        # otra versión real que se solapa: no se guarda
        form = VersionForm({**datos, 'codigo': 'OTRA'})
        self.assertFalse(form.is_valid())
        self.assertIn('Se solapa', str(form.errors))


# ---------------------------------------------------------------- VEN-02 y COM-02
class DevolucionesAlCostoTest(Base):
    def test_nc_de_venta_y_de_compra(self):
        lamp = Producto.objects.create(nombre='Lámpara', clase='MERCADERIA', precio_venta=D('100'))
        compra = Compra.objects.create(tipo_comprobante='01', serie='E001', numero='301', fecha_emision=HOY,
                                       tercero=self.prov, almacen=self.almacen)
        CompraItem.objects.create(documento=compra, producto=lamp, descripcion='Lámpara', cantidad=10,
                                  precio_unitario=D('40'))
        compra.calcular_totales()
        compra.save()
        compra.aplicar_stock()
        f = Venta.objects.create(tipo_comprobante='01', serie='F001', numero='39', fecha_emision=HOY, tercero=self.cli,
                                 almacen=self.almacen)
        VentaItem.objects.create(documento=f, producto=lamp, descripcion='Lámpara', cantidad=5,
                                 precio_unitario=D('100'))
        f.aplicar_stock()
        lamp.mover_stock(D('5'), 'Compra a 52', costo=D('52'), almacen=self.almacen)  # promedio 46
        lamp.refresh_from_db()
        self.assertEqual(lamp.costo_promedio, D('46'))
        nc = Venta.objects.create(tipo_comprobante='07', serie='FC01', numero='4', fecha_emision=HOY, tercero=self.cli,
                                  almacen=self.almacen, doc_referencia=f)
        VentaItem.objects.create(documento=nc, producto=lamp, descripcion='Lámpara', cantidad=2,
                                 precio_unitario=D('100'))
        nc.aplicar_stock()
        self.assertEqual(Kardex.objects.get(referencia=str(nc)).costo_unitario, D('40'))  # VEN-02: no 46
        ncp = Compra.objects.create(tipo_comprobante='07', serie='E001', numero='901', fecha_emision=HOY,
                                    tercero=self.prov, almacen=self.almacen, doc_referencia=compra)
        CompraItem.objects.create(documento=ncp, producto=lamp, descripcion='Lámpara', cantidad=1,
                                  precio_unitario=D('40'))
        ncp.aplicar_stock()
        self.assertEqual(Kardex.objects.get(referencia=str(ncp)).costo_unitario, D('40'))  # COM-02: no el promedio


# ---------------------------------------------------------------- FIN-04
class DiferenciaCambioDocumentosTest(Base):
    P1, F1, P2, F2 = '203104', date(2031, 4, 10), '203105', date(2031, 5, 10)

    def test_cuentas_por_cobrar_y_pagar_en_dolares_al_cierre(self):
        usd = Cuenta.objects.create(nombre='BCP dólares', banco='BCP', moneda='USD')
        v = Venta.objects.create(tipo_comprobante='01', serie='F001', numero='45', fecha_emision=self.F1,
                                 tercero=self.cli, moneda='USD', tipo_cambio=D('3.410'), descontar_stock=False)
        VentaItem.objects.create(documento=v, descripcion='Servicio', cantidad=1, precio_unitario=D('1000'))
        v.calcular_totales()
        v.save()
        c = Compra.objects.create(tipo_comprobante='01', serie='E001', numero='501', fecha_emision=self.F1,
                                  tercero=self.prov, moneda='USD', tipo_cambio=D('3.410'), ingresar_almacen=False,
                                  clasificacion='SERVICIO')
        CompraItem.objects.create(documento=c, descripcion='Servicio', cantidad=1, precio_unitario=D('500'))
        c.calcular_totales()
        c.save()
        Movimiento.objects.create(cuenta=usd, fecha=self.F1, tipo='INGRESO', concepto='COBRANZA', monto=D('590'),
                                  venta=v)
        Movimiento.objects.create(cuenta=usd, fecha=self.F1, tipo='EGRESO', concepto='PAGO', monto=D('295'), compra=c)
        r = centralizar_periodo(self.P1)
        self.assertEqual([e for e in r['errores'] if 'cuadra' in e], [])

        def lineas(doc):
            return AsientoLinea.objects.filter(asiento__origen='CAMBIO', documento=_doc_texto(doc),
                                               tercero=doc.tercero)
        cobrar = lineas(v).get()
        self.assertEqual(cobrar.debe, D('18.29'))   # 590 × 3.441 − 2,011.90
        pagar = lineas(c).get()
        self.assertEqual(pagar.haber, D('11.80'))   # 295 × 3.450 − 1,005.95
        # el cobro del mes siguiente parte del saldo ajustado: la cuenta del documento queda en cero
        Movimiento.objects.create(cuenta=usd, fecha=self.F2, tipo='INGRESO', concepto='COBRANZA', monto=D('590'),
                                  venta=v)
        centralizar_periodo(self.P2)
        cliente = AsientoLinea.objects.filter(documento=_doc_texto(v), tercero=self.cli,
                                              cuenta__codigo__startswith='12')
        self.assertEqual(sum((l.debe - l.haber for l in cliente), D('0')), D('0'))


# ---------------------------------------------------------------- CAL-02
class LiberacionCalidadTest(Base):
    def test_no_se_libera_sin_inspeccion_aprobada(self):
        from inventario.estados import ErrorLote, trasladar_estado
        resina = Producto.objects.create(nombre='Resina', clase='MATERIA_PRIMA')
        calidad = Almacen.especial('CALIDAD')
        resina.mover_stock(D('10'), 'Recepción', costo=D('5'), almacen=calidad)
        with self.assertRaisesMessage(ErrorLote, 'inspección de calidad aprobada'):
            trasladar_estado(resina, D('10'), calidad, self.almacen, 'Liberación sin inspección', self.user)
        from produccion.models import InspeccionCalidad
        InspeccionCalidad.objects.create(numero='QC-1', producto=resina, cantidad=D('4'), resultado='APROBADO')
        with self.assertRaisesMessage(ErrorLote, 'cubren 4'):
            trasladar_estado(resina, D('10'), calidad, self.almacen, 'Liberación de más', self.user)
        trasladar_estado(resina, D('4'), calidad, self.almacen, 'Liberación aprobada', self.user)
        self.assertEqual(resina.stock_en(self.almacen), D('4'))


# ---------------------------------------------------------------- COS-01
class RepartoVariacionTest(Base):
    def test_con_stock_previo_se_reparte_en_proporcion(self):
        from produccion.parciales import _en_stock_de_la_orden
        bandeja = Producto.objects.create(nombre='Bandeja', clase='PRODUCTO_TERMINADO', precio_venta=D('10'))
        bandeja.mover_stock(D('4'), 'OP-A', costo=D('5'), almacen=self.almacen, fecha=date(2026, 1, 5))
        bandeja.mover_stock(D('10'), 'OP-B', costo=D('5'), almacen=self.almacen, fecha=date(2026, 1, 10))
        bandeja.mover_stock(D('-6'), 'Venta', almacen=self.almacen, fecha=date(2026, 1, 12))
        bandeja.refresh_from_db()
        decl = [SimpleNamespace(lote='', fecha=date(2026, 1, 10))]
        # de 14 bandejas (4 previas + 10 de la orden) quedan 8: de la orden siguen en stock 10 × 8 ÷ 14
        self.assertEqual(_en_stock_de_la_orden(bandeja, decl, D('10')), D('5.7143'))


# ---------------------------------------------------------------- FIN-01
class MermaAnormalTest(Base):
    def test_linea_propia_en_el_estado_de_resultados(self):
        from contabilidad import reportes
        from produccion.models import ListaMateriales, OrdenProduccion
        pt = Producto.objects.create(nombre='Kit', clase='PRODUCTO_TERMINADO', precio_venta=D('10'))
        receta = ListaMateriales.objects.create(producto=pt, codigo='K1', cantidad_base=D('1'))
        OrdenProduccion.objects.create(producto=pt, lista=receta, cantidad=D('1'), almacen_insumos=self.almacen,
                                       almacen_destino=self.almacen, estado='TERMINADA', fecha_fin=HOY,
                                       merma_anormal=D('25.50'))
        lineas, _ = reportes.estado_resultados(HOY.replace(day=1), HOY)
        valores = {n: v for n, v, _ in lineas}
        self.assertEqual(valores[reportes.LINEA_MERMA], D('-25.50'))


# ---------------------------------------------------------------- FIN-05 y MAE-01
class MaestrosTest(Base):
    def test_tipo_de_cambio_se_corrige_y_producto_por_clase(self):
        url = reverse('tipos_cambio')
        self.client.post(url, {'fecha': '2026-11-02', 'compra': '3.400', 'venta': '3.410'})
        r = self.client.post(url, {'fecha': '2026-11-02', 'compra': '3.420', 'venta': '3.430'})
        self.assertEqual(r.status_code, 302)
        tc = TipoCambio.objects.get(fecha=date(2026, 11, 2))
        self.assertEqual((tc.compra, tc.venta), (D('3.420'), D('3.430')))
        self.assertTrue(Bitacora.objects.filter(modelo=tc._meta.label, objeto_id=str(tc.pk), accion='MODIFICAR')
                        .exists())
        r = self.client.get(reverse('producto_nuevo') + '?clase=MATERIA_PRIMA')
        self.assertFalse(r.context['form'].initial['puede_venderse'])
        r = self.client.get(reverse('producto_nuevo') + '?clase=PRODUCTO_TERMINADO')
        self.assertFalse(r.context['form'].initial['puede_comprarse'])
