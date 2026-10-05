"""Pruebas de las operaciones de inventario (v1.5): recepciones, devoluciones, salidas, traslados,
tránsito, destrucción, manufactura, anulación y contabilidad."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.forms import CompraForm
from compras.models import OrdenCompra, OrdenCompraItem
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Almacen, Kardex, Producto, Tercero
from core.sustentos import adjuntar
from ventas.models import Venta

from . import servicios
from .models import Operacion, TipoOperacion

D = Decimal
HOY = date.today()


def acta():
    return SimpleUploadedFile('acta.pdf', b'%PDF-1.4 acta de inventario', content_type='application/pdf')


class OperacionesTest(TestCase):
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
        self.principal = Almacen.principal()
        self.callao = Almacen.objects.get(codigo='ALM02')
        self.p1, self.p3 = Producto.objects.get(codigo='P001'), Producto.objects.get(codigo='P003')

    def crear(self, codigo, items, **campos):
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo=codigo), fecha=HOY, **campos)
        for fila in items:
            producto, cantidad = fila[0], fila[1]
            op.items.create(producto=producto, cantidad=D(cantidad),
                            costo_unitario=D(fila[2]) if len(fila) > 2 and fila[2] is not None else None,
                            rol=fila[3] if len(fila) > 3 else '')
        if op.tipo.requiere_sustento:
            adjuntar(op, acta())
        return op

    def stock(self, producto, almacen):
        return Producto.objects.get(pk=producto.pk).stock_en(almacen)

    # ---------------------------------------------------------------- configuración
    def test_tipos_de_operacion_cargados(self):
        codigos = set(TipoOperacion.objects.values_list('codigo', flat=True))
        self.assertEqual(codigos, {'SALDO_INI', 'REC_COMPRA', 'DEV_CLI', 'AJ_ING', 'SAL_VENTA', 'DEV_PROV', 'AJ_SAL',
                                   'CONS_INT', 'CONS_MANT', 'SAL_DESTR', 'TRAS_DESTR', 'TRAS_TRANS', 'REC_TRANS',
                                   'MANUF', 'TRAS_ALM'})
        self.assertEqual(TipoOperacion.objects.get(codigo='CONS_INT').cuenta_contable.codigo, '6561')
        self.assertIsNone(TipoOperacion.objects.get(codigo='TRAS_TRANS').cuenta_contable)

    # ---------------------------------------------------------------- ingresos
    def test_saldo_inicial_con_costo_y_tabla_12(self):
        nuevo = Producto.objects.create(codigo='P900', nombre='Cable HDMI')
        op = self.crear('SALDO_INI', [(nuevo, '100', '12.50')], almacen_destino=self.principal)
        servicios.confirmar(op, self.user)
        nuevo.refresh_from_db()
        self.assertEqual((nuevo.stock, nuevo.costo_promedio), (D('100'), D('12.5000')))
        k = Kardex.objects.get(producto=nuevo)
        self.assertEqual((k.origen, k.concepto, k.codigo_sunat), ('OPERACION', 'SALDO_INI', '16'))
        self.assertTrue(op.numero.startswith('NI01-'))

    def test_recepcion_parcial_de_orden_de_compra(self):
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        oc = OrdenCompra.objects.create(numero='OC01-00000099', tercero=prov)
        OrdenCompraItem.objects.create(documento=oc, producto=self.p1, descripcion='Laptop', cantidad=10,
                                       precio_unitario=D('2000'))
        antes = self.stock(self.p1, self.principal)
        op = self.crear('REC_COMPRA', [(self.p1, '4', '2000')], orden_compra=oc, almacen_destino=self.principal)
        servicios.confirmar(op, self.user)
        self.assertEqual(self.stock(self.p1, self.principal), antes + 4)
        self.assertEqual(servicios.pendientes(Operacion(tipo=op.tipo, orden_compra=oc))[self.p1.pk][0], D('6'))
        exceso = self.crear('REC_COMPRA', [(self.p1, '7', '2000')], orden_compra=oc, almacen_destino=self.principal)
        with self.assertRaises(servicios.ErrorOperacion):
            servicios.confirmar(exceso)
        # la factura de esa orden no puede volver a ingresar la mercadería
        form = CompraForm(data={'tipo_comprobante': '01', 'serie': 'F001', 'numero': '999', 'tercero': prov.pk,
                                'fecha_emision': HOY, 'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO',
                                'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
                                'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0',
                                'orden_compra': oc.pk, 'ingresar_almacen': 'on', 'almacen': self.principal.pk})
        self.assertFalse(form.is_valid())
        self.assertIn('ingresar_almacen', form.errors)
        # el detalle de la orden ofrece seguir recibiendo
        self.assertTrue(servicios.acciones_para(oc))

    def test_devolucion_a_proveedor_limitada_a_lo_recibido(self):
        compra = self.p3.compraitem_set.first().documento
        recibido = sum(i.cantidad for i in compra.items.filter(producto=self.p3))
        mucho = self.crear('DEV_PROV', [(self.p3, recibido + 1)], compra=compra, almacen_origen=self.principal)
        with self.assertRaises(servicios.ErrorOperacion):
            servicios.confirmar(mucho)
        antes = self.stock(self.p3, self.principal)
        op = self.crear('DEV_PROV', [(self.p3, '5')], compra=compra, almacen_origen=self.principal)
        servicios.confirmar(op)
        self.assertEqual(self.stock(self.p3, self.principal), antes - 5)
        self.assertEqual(Kardex.objects.filter(concepto='DEV_PROV').get().codigo_sunat, '06')
        r = self.client.get(reverse('inventario:detalle', args=[op.pk]))
        self.assertContains(r, 'Emitir nota de crédito')

    def test_devolucion_de_cliente_al_costo_de_la_venta(self):
        venta = Venta.objects.filter(tipo_comprobante='01', items__producto=self.p3).first()
        vendido = venta.items.get(producto=self.p3).cantidad
        costo_salida = Kardex.objects.get(producto=self.p3, referencia=str(venta)).costo_unitario
        with self.assertRaises(servicios.ErrorOperacion):
            servicios.confirmar(self.crear('DEV_CLI', [(self.p3, vendido + 1)], venta=venta,
                                           almacen_destino=self.principal))
        filas = servicios.items_desde_origen(Operacion(tipo=TipoOperacion.objects.get(codigo='DEV_CLI'), venta=venta))
        fila = next(f for f in filas if f['producto'] == self.p3.pk)
        self.assertEqual(fila['costo_unitario'], costo_salida)
        op = self.crear('DEV_CLI', [(self.p3, '2', fila['costo_unitario'])], venta=venta,
                        almacen_destino=self.principal)
        servicios.confirmar(op)
        # nota de crédito sugerida sin volver a mover el almacén
        r = self.client.get(reverse('ventas:nuevo') + f'?ref={venta.pk}&tipo=07&motivo=07&op={op.pk}')
        self.assertEqual(r.context['form'].initial.get('descontar_stock'), False)
        self.assertEqual(r.context['formset'].forms[0].initial['cantidad'], D('2'))

    # ---------------------------------------------------------------- salidas
    def test_salida_sin_stock_no_se_confirma(self):
        op = self.crear('CONS_INT', [(self.p1, '9999')], almacen_origen=self.principal)
        self.assertTrue(servicios.errores_confirmacion(op))
        with self.assertRaises(servicios.ErrorOperacion):
            servicios.confirmar(op)
        op.refresh_from_db()
        self.assertEqual(op.estado, 'BORRADOR')

    def test_destruccion_en_dos_pasos(self):
        destruccion = Almacen.especial('DESTRUCCION')
        total = Producto.objects.get(pk=self.p3.pk).stock
        tras = self.crear('TRAS_DESTR', [(self.p3, '3')], almacen_origen=self.principal, almacen_destino=destruccion)
        servicios.confirmar(tras)
        self.assertEqual(self.stock(self.p3, destruccion), 3)
        self.assertEqual(Producto.objects.get(pk=self.p3.pk).stock, total)  # traslado: no cambia el total
        sal = self.crear('SAL_DESTR', [(self.p3, '3')], almacen_origen=destruccion)
        servicios.confirmar(sal)
        self.assertEqual(self.stock(self.p3, destruccion), 0)
        self.assertEqual(Producto.objects.get(pk=self.p3.pk).stock, total - 3)
        self.assertEqual(Kardex.objects.filter(concepto='SAL_DESTR').get().codigo_sunat, '15')

    # ---------------------------------------------------------------- tránsito
    def test_traslado_a_transito_y_recepcion_parcial(self):
        transito = Almacen.especial('TRANSITO')
        antes = self.stock(self.p3, self.principal)
        envio = self.crear('TRAS_TRANS', [(self.p3, '5')], almacen_origen=self.principal, almacen_destino=self.callao)
        servicios.confirmar(envio)
        self.assertEqual(self.stock(self.p3, transito), 5)
        self.assertEqual(self.stock(self.p3, self.principal), antes - 5)
        rec = self.crear('REC_TRANS', [(self.p3, '2')], envio=envio, almacen_destino=self.callao)
        servicios.confirmar(rec)
        self.assertEqual(self.stock(self.p3, transito), 3)
        self.assertEqual(self.stock(self.p3, self.callao), 2)
        tipo_rec = TipoOperacion.objects.get(codigo='REC_TRANS')
        self.assertEqual(servicios.pendientes(Operacion(tipo=tipo_rec, envio=envio))[self.p3.pk][0], D('3'))
        # el envío no se anula mientras tenga recepciones
        with self.assertRaises(servicios.ErrorOperacion):
            servicios.anular(envio, self.user, 'Prueba de anulación')
        r = self.client.get(reverse('inventario:lista'))
        self.assertContains(r, 'Mercadería en tránsito por recibir')

    # ---------------------------------------------------------------- manufactura
    def test_manufactura_costea_el_producto_con_los_insumos(self):
        kit = Producto.objects.create(codigo='KIT1', nombre='Kit oficina')
        costo_p3 = Producto.objects.get(pk=self.p3.pk).costo_promedio
        op = self.crear('MANUF', [(self.p3, '4', None, 'INSUMO'), (kit, '2', None, 'PRODUCTO')],
                        almacen_origen=self.principal, almacen_destino=self.principal)
        servicios.confirmar(op)
        kit.refresh_from_db()
        self.assertEqual(kit.stock, 2)
        self.assertEqual(kit.costo_promedio, (costo_p3 * 4 / 2).quantize(D('0.0001')))
        self.assertEqual(set(Kardex.objects.filter(concepto='MANUF').values_list('codigo_sunat', flat=True)),
                         {'10', '19'})

    # ---------------------------------------------------------------- anulación
    def test_anular_revierte_y_bloquea_si_ya_salio(self):
        nuevo = Producto.objects.create(codigo='P901', nombre='Mouse pad')
        ing = self.crear('AJ_ING', [(nuevo, '10', '5')], almacen_destino=self.principal)
        servicios.confirmar(ing)
        sal = self.crear('AJ_SAL', [(nuevo, '8')], almacen_origen=self.principal)
        servicios.confirmar(sal)
        with self.assertRaises(servicios.ErrorOperacion):  # anular el ingreso dejaría stock negativo
            servicios.anular(ing, self.user, 'Ingreso duplicado')
        servicios.anular(sal, self.user, 'Salida registrada por error')
        servicios.anular(ing, self.user, 'Ingreso duplicado')
        self.assertEqual(Producto.objects.get(pk=nuevo.pk).stock, 0)
        ing.refresh_from_db()
        self.assertEqual((ing.estado, ing.anulado_por), ('ANULADO', self.user))

    # ---------------------------------------------------------------- contabilidad
    def test_contrapartida_contable_del_tipo(self):
        op = self.crear('CONS_INT', [(self.p3, '2')], almacen_origen=self.principal)
        servicios.confirmar(op)
        valor = op.items.get().valor
        centralizar_periodo(HOY.strftime('%Y%m'))
        a = Asiento.objects.get(periodo=HOY.strftime('%Y%m'), origen='INVENTARIO')
        self.assertEqual(a.lineas.get(cuenta__codigo='6561').debe, valor)
        for asiento in Asiento.objects.all():
            self.assertTrue(asiento.cuadrado, f'{asiento} no cuadra')

    # ---------------------------------------------------------------- pantallas
    def test_flujo_por_pantallas(self):
        self.assertEqual(self.client.get(reverse('inventario:nueva')).status_code, 200)
        r = self.client.get(reverse('inventario:nueva') + '?tipo=CONS_MANT')
        self.assertEqual(r.status_code, 200)
        datos = {'fecha': HOY.isoformat(), 'almacen_origen': self.principal.pk, 'referencia': 'OT-15', 'glosa': '',
                 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
                 'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.p3.pk, 'items-0-cantidad': '1',
                 'items-0-observacion': 'Reposición', 'accion': 'confirmar'}
        # sin sustento queda en borrador: el consumo de mantenimiento lo exige
        self.client.post(reverse('inventario:nueva') + '?tipo=CONS_MANT', {**datos, 'referencia': 'OT-14'})
        self.assertEqual(Operacion.objects.get(referencia='OT-14').estado, 'BORRADOR')
        r = self.client.post(reverse('inventario:nueva') + '?tipo=CONS_MANT', {**datos, 'sustento': acta()})
        op = Operacion.objects.get(referencia='OT-15')
        self.assertRedirects(r, reverse('inventario:detalle', args=[op.pk]))
        self.assertEqual(op.estado, 'CONFIRMADO')
        for nombre in ('inventario:detalle', 'inventario:imprimir'):
            self.assertEqual(self.client.get(reverse(nombre, args=[op.pk])).status_code, 200)
        for url in (reverse('inventario:lista') + '?grupo=salidas', reverse('inventario:tipos'), reverse('inv_kardex')
                    + f'?producto={self.p3.pk}'):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse('ventas:detalle', args=[Venta.objects.first().pk])).status_code,
                         200)
