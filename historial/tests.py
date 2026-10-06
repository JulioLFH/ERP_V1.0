"""Importación del resto del sistema anterior (órdenes de compra, pedidos, fabricación, kardex, asientos y
posiciones) y pantallas de consulta del historial."""
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from compras.models import OrdenCompra
from core.forms import digito_ruc
from core.models import Producto, StockAlmacen, Tercero
from ventas.models import Cotizacion

from .management.commands.importar_anteriores import nombre_clave
from .models import AsientoAnterior, MovimientoAnterior, OrdenFabricacionAnterior, PosicionPresupuestaria

D = Decimal
HOY = date.today()


def guardar(carpeta, nombre, filas):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    wb.save(os.path.join(carpeta, nombre))


class HistorialTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('h', 'h@e.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.c = c = tmp.name
        self.prod = Producto.objects.create(codigo='HX01', nombre='Harina', clase='MATERIA_PRIMA')
        self.ruc_nuevo = f'2060000003{digito_ruc("2060000003")}'
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20111111111',
                                           nombre='MOLINOS DEL SUR S.A.C.')
        self.cli = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS']).first()
        reciente = (HOY - timedelta(days=10)).strftime('%Y-%m-%d 10:00:00')
        antigua = (HOY - timedelta(days=300)).strftime('%Y-%m-%d 10:00:00')
        enc_oc = ['Referencia de la Orden', 'Fecha de Confirmación/Fecha Creación', 'Proveedor', 'Comprador',
                  'Tipo de Compra', 'Documento Origen', 'Codigo Material', 'Producto', 'Cantidad', 'Condición de Pago',
                  'Distribución Analítica', 'Moneda', 'Precio Unitario', 'Sub Total sin igv', 'Total',
                  'Almacen de Entrega', 'Cantidad Recibida', 'Pendiente por recibir', 'Estado de entrega',
                  'Estado de Facturación', 'Entrega esperada', 'Estado']
        guardar(c, 'Data_OrdenCompra_Scraping_2026.xlsx', [
            enc_oc,
            # nombre largo: coincide con "MOLINOS DEL SUR S.A.C."; reciente con saldo por recibir -> abierta
            ['P00001', reciente, 'MOLINOS DEL SUR SOCIEDAD ANONIMA CERRADA', 'Ana', 'nacional', 'BO1', 'HX01',
             '[HX01] Harina', 100, '30 días', None, 'PEN', 2, 200, 236, 'Insumos', 60, 40, 'Recibido parcialmente',
             'Facturas en Espera', reciente, 'Orden de compra'],
            # proveedor nuevo: se crea con el RUC del Excel de contactos; antigua con saldo -> cerrada
            ['P00002', antigua, 'NUEVO PROVEEDOR S.A.C.', 'Ana', 'servicio', '', '', 'Servicio de limpieza', 1,
             '15 días', '[922003] MANTENIMIENTO', 'PEN', 500, 500, 500, '', 0, 1, 'No recibido', 'Nada por facturar',
             antigua, 'Orden de compra'],
            ['P00003', antigua, 'DESCONOCIDO SRL', 'Ana', 'nacional', '', 'HX01', '[HX01] Harina', 1, '', None,
             'PEN', 1, 1, 1.18, '', 1, 0, 'Recibido Totalmente', 'Totalmente Facturado', antigua, 'Bloqueado']])
        guardar(c, 'Data_Contactos_API.xlsx', [
            ['nombre_completo', 'nombre', 'tipo_documento', 'numero_documento', 'direccion_completa'],
            ['NUEVO PROVEEDOR S.A.C.', 'NUEVO PROVEEDOR', 'RUC', self.ruc_nuevo, 'AV. LIMA 1']])
        guardar(c, 'Data_OrdenVenta_API_2026.xlsx', [
            ['referencia', 'compañia', 'fecha_orden', 'estado', 'nro_documento_cliente', 'vendedor', 'tipo_de_cambio',
             'moneda', 'cod_producto', 'producto', 'cantidad', 'impuestos', 'precio_unitario', 'descuento',
             'lista_de_precio', 'termino_de_pago', 'direccion_de_entrega'],
            ['S00010', 'PRODUCTORA DE ALIMENTOS UNO S.A.C.', '2026-05-02 00:00:00', 'Orden de venta',
             self.cli.numero_doc, 'Juan', 3.5, 'PEN', 'HX01', 'Harina', 10, 'IGV-VEN', 10, 2, 'Base', '30 días', 'Av'],
            ['S00010', 'PRODUCTORA DE ALIMENTOS UNO S.A.C.', '2026-05-02 00:00:00', 'Orden de venta',
             self.cli.numero_doc, 'Juan', 3.5, 'PEN', 'HX01', 'Harina', 1, 'IGV-TRG', 10, 0, 'Base', '30 días', 'Av'],
            ['S00011', 'ITS SOLUCIONES DE EMBALAJE S.A.C.', '2026-05-02 00:00:00', 'Orden de venta',
             self.cli.numero_doc, 'X', 3.5, 'USD', 'HX01', 'Harina', 1, 'IGV-VEN', 1, 0, '', '', ''],
            ['S00012', 'PRODUCTORA DE ALIMENTOS UNO S.A.C.', '2026-05-03 00:00:00', 'Cancelada',
             self.cli.numero_doc, 'Juan', 3.5, 'PEN', 'HX01', 'Harina', 1, 'IGV-VEN', 5, 0, '', '', '']])
        guardar(c, 'Data_Orden_de_Fabricación_Scraping_2026.xlsx', [
            ['Referencia', 'Producto', 'Lista de materiales', 'Número de serie/lote', 'Fecha de Inicio de Fabricación',
             'Fecha de Final de Fabricación', 'Fecha Kardex', 'Cantidad a producir', 'Cantidad en producción',
             'Unidad de medida del producto', 'Estado', 'Responsable'],
            ['LPROD/MO/1', '[HX01] Harina', 'V1', 'L1', '2026-05-01 08:00:00', '2026-05-01 16:00:00',
             '2026-05-01 16:00:00', 100, 100, 'kg', 'Listo', 'Pedro']])
        guardar(c, 'Reporte de Producción (report.simple.mrp).xlsx', [
            ['Orden de Fabricación', 'Fecha Kardex', 'Fecha Programada', 'Responsable', 'Producto Terminado',
             'Cantidad Requerida', 'Cantidad Producida', 'Lote/Serie Producida', 'Almacén', 'Estado',
             'Monto Mano Obra', 'Monto Gasto Indirecto', 'Monto Materia Prima', 'Componente', 'Cant. Requerida',
             'Cant. Reservada', 'Lotes reservados'],
            ['LPROD/MO/1', None, None, 'Pedro', '[HX01] Harina', 100, 100, 'L1', 'Producción', 'Listo', 50, 20,
             300.456, '[HX01] Harina', 110, 110, 'A1']])
        enc_k = ['id_movimiento_detalle', 'fecha_movimiento', 'almacen', 'transaccion', 'documento_origen',
                 'orden_fabricacion', 'orden_compra', 'guia_de_venta', 'guia_de_compra_o_traslado', 'factura/boleta',
                 'sku', 'sku_nombre', 'udme', 'Lote', 'fecha_de_vencimiento_de_lote', 'ingreso', 'salida', 'costo',
                 'nro_documento', 'contacto', 'usuario']
        guardar(c, 'Data_Kardex_API_2025.xlsx', [enc_k, [
            1, 'martes, 4 de noviembre de 2025', 'Producción', 'Manufactura', 'LPROD/MO/1', 'LPROD/MO/1', None, None,
            None, None, 'HX01', 'Harina', 'kg', 'L1', None, 0, 5, 2.6, None, None, 'Pedro']])
        guardar(c, 'Data_Kardex_API_2026.xlsx', [enc_k, [
            1, '04/11/2025 10:00:00', 'Producción', 'Manufactura', 'X', '', '', '', '', '', 'HX01', 'Harina', 'kg',
            '', None, 0, 5, 2.6, '', '', ''],  # repetido: se omite
            [2, '31/03/2026 23:42:22', 'Insumos', 'Recepcion Compras', 'P00001', '', 'P00001', '', 'G1', 'F1',
             'HX01', 'Harina', 'kg', '', None, 60, 0, 2, self.prov.numero_doc, 'MOLINOS', 'Ana']])
        guardar(c, 'Data_Contable_2026.xlsx', [
            ['diario', 'voucher', 'fecha_contable', 'nro_documento', 'contacto', 'tipo_de_documento',
             'numero_comprobante', 'referencia', 'glosa', 'etiqueta', 'CtaCont', 'DescCtaCont', 'debito_soles',
             'credito_soles', 'Centro_costo', 'usuario', 'PeriodoMes'],
            ['Ventas', '000001', '02/07/2025', '20100070970', 'SPSA', '(01) Factura', 'FF01-1', 'R', 'VENTA', 'X',
             '12120001', 'Facturas', 118, 0, 'SIN_CC', 'Juan', '2025-07-01'],
            ['Ventas', '000001', '02/07/2025', '20100070970', 'SPSA', '(01) Factura', 'FF01-1', 'R', 'VENTA', 'X',
             '70211100', 'Ventas', 0, 118, '[951003] CANAL MODERNO', 'Juan', '2025-07-01'],
            # factura de proveedor: base en 60, IGV en 4011 y total en 42 (el periodo sale de la fecha, no del
            # trimestre de PeriodoMes)
            ['Facturas de proveedores', '000002', '15/08/2025', self.prov.numero_doc, 'MOLINOS', '(01) Factura',
             'F001-00000077', '', 'HARINA', '', '60210100', 'Compra materias primas', 100, 0, 'SIN_CC', 'Ana',
             '2025-07-01'],
            ['Facturas de proveedores', '000002', '15/08/2025', self.prov.numero_doc, 'MOLINOS', '(01) Factura',
             'F001-00000077', '', 'IGV', '', '40111002', 'IGV - Compras', 18, 0, 'SIN_CC', 'Ana', '2025-07-01'],
            ['Facturas de proveedores', '000002', '15/08/2025', self.prov.numero_doc, 'MOLINOS', '(01) Factura',
             'F001-00000077', '', 'TOTAL', '', '42120001', 'Facturas', 0, 118, 'SIN_CC', 'Ana', '2025-07-01']])
        guardar(c, 'Data_Posiciones_Presupuestarias.xlsx', [
            ['Nombre de la Posición Presupuestaria', 'Código', 'Nombre de la Cuenta'],
            ['Marketing', '95210000', 'Publicidad'], ['Marketing', '95220000', 'Promociones']])

    def test_nombre_clave(self):
        self.assertEqual(nombre_clave('Molinos del Sur Sociedad Anónima Cerrada'), nombre_clave('MOLINOS DEL SUR S.A.C.'))
        self.assertEqual(nombre_clave('PERULAB S.A.C., perulab leche'), 'PERULAB SAC')

    def test_importacion_completa_e_idempotente(self):
        stock_antes = list(StockAlmacen.objects.values_list('pk', 'cantidad'))
        salida = StringIO()
        call_command('importar_anteriores', self.c, '--reporte', os.path.join(self.c, 'r.xlsx'), stdout=salida)
        # órdenes de compra
        abierta = OrdenCompra.objects.get(numero='P00001')
        self.assertEqual((abierta.tercero, abierta.estado, abierta.items.get().cantidad), (self.prov, 'APROBADO', D('40')))
        self.assertEqual((abierta.base_imponible, abierta.igv, abierta.total), (D('80.00'), D('14.40'), D('94.40')))
        servicio = OrdenCompra.objects.get(numero='P00002')
        self.assertEqual((servicio.estado, servicio.tercero.numero_doc, servicio.igv, servicio.total),
                         ('ATENDIDO', self.ruc_nuevo, D('0'), D('500.00')))
        self.assertFalse(OrdenCompra.objects.filter(numero='P00003').exists())  # proveedor desconocido
        # pedidos: sin la otra empresa; bonificación a precio cero; descuento
        pedido = Cotizacion.objects.get(numero='S00010')
        self.assertEqual((pedido.tipo, pedido.estado, pedido.base_imponible, pedido.items.count()),
                         ('PED', 'ATENDIDO', D('98.00'), 2))
        self.assertFalse(Cotizacion.objects.filter(numero='S00011').exists())
        self.assertEqual(Cotizacion.objects.get(numero='S00012').estado, 'ANULADO')
        # fabricación, kardex, asientos y posiciones
        of = OrdenFabricacionAnterior.objects.get(referencia='LPROD/MO/1')
        self.assertEqual((of.producto, of.costo_materiales, of.costo_total, of.consumos.count()),
                         (self.prod, D('300.46'), D('370.46'), 1))
        self.assertEqual(MovimientoAnterior.objects.count(), 2)
        self.assertEqual(MovimientoAnterior.objects.get(id_origen=1).fecha, date(2025, 11, 4))
        self.assertEqual(AsientoAnterior.objects.filter(periodo='202507').count(), 2)
        self.assertEqual(AsientoAnterior.objects.filter(periodo='202508').count(), 3)
        # factura de compra histórica armada desde los asientos
        from compras.models import Compra
        compra = Compra.objects.get(tercero=self.prov, serie='F001', numero='77')
        self.assertEqual((compra.es_historico, compra.base_imponible, compra.igv, compra.total, compra.periodo,
                          compra.clasificacion, compra.saldo, compra.stock_aplicado),
                         (True, D('100'), D('18'), D('118'), '202508', 'MERCADERIA', D('0'), False))
        self.assertEqual(AsientoAnterior.objects.get(cuenta='70211100').centro_costo, '[951003] CANAL MODERNO')
        self.assertEqual(PosicionPresupuestaria.objects.count(), 2)
        # no toca el stock de Ceiba
        self.assertEqual(list(StockAlmacen.objects.values_list('pk', 'cantidad')), stock_antes)
        # idempotente
        salida = StringIO()
        call_command('importar_anteriores', self.c, '--reporte', os.path.join(self.c, 'r.xlsx'), stdout=salida)
        self.assertEqual((OrdenCompra.objects.filter(numero__startswith='P0000').count(),
                          Cotizacion.objects.filter(numero__startswith='S000').count(), MovimientoAnterior.objects.count(),
                          AsientoAnterior.objects.count()), (2, 2, 2, 5))
        self.assertEqual(Compra.objects.filter(es_historico=True).count(), 1)
        # pantallas
        for nombre in ('hist_kardex', 'hist_asientos', 'hist_balance', 'hist_fabricacion', 'hist_posiciones'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        self.assertContains(self.client.get(reverse('hist_kardex'), {'q': 'HX01'}), 'Recepcion Compras')
        self.assertContains(self.client.get(reverse('hist_balance'), {'nivel': '2'}), '118.00')
        self.assertContains(self.client.get(reverse('hist_orden', args=[of.pk])), 'LPROD/MO/1')
        self.assertIn('spreadsheetml', self.client.get(reverse('hist_asientos'), {'formato': 'excel'})['Content-Type'])
