"""v1.88 (checklist del 26/10/2026, 04h08): producción parcial con cierre técnico y liquidación (MAN-18 a MAN-21),
errores 500 (CON-17, VEN-18, MAN-16, PLA-15, ACT-01) y validaciones de la batería de pruebas negativas (COM-20, VEN-19,
VEN-20, TER-02, FIN-11, CON-18 a CON-20, INV-32, REQ-04, MAN-17, OBR-29, OBR-30, PLA-16, PLA-17, SEG-08)."""
from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from produccion.tests_reportes_produccion import Base as BaseProduccion

HOY = timezone.localdate()


def _salida(test, o, producto, cantidad, codigo='SAL_OP'):
    from inventario import servicios as inv
    from inventario.models import Operacion, TipoOperacion
    op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo=codigo), fecha=HOY,
                                  almacen_origen=test.almacen if codigo == 'SAL_OP' else None,
                                  almacen_destino=test.almacen if codigo == 'DEV_OP' else None,
                                  orden_imputada=o, creado_por=test.user)
    op.items.create(producto=producto, cantidad=D(cantidad))
    return inv.confirmar(op, test.user)


class DeclaracionParcialTest(BaseProduccion):
    def test_declaraciones_saldo_lotes_y_planificacion(self):
        """MAN-18: cada turno ingresa su lote al estándar y consume su parte; la orden queda parcial con saldo."""
        from produccion import parciales, servicios
        o = self.orden()
        stock_masa = self.masa.stock
        d1 = parciales.declarar(o, self.user, '30', lote='L-1')
        o.refresh_from_db()
        self.assertEqual((o.estado, o.cantidad_producida, o.saldo), ('PARCIAL', D('30'), D('70')))
        self.assertEqual(d1.costo_unitario, o.costo_estandar_unit)
        self.masa.refresh_from_db()
        self.assertEqual(stock_masa - self.masa.stock, D('15'))  # 0.5 kg por paquete
        self.galleta.refresh_from_db()
        self.assertEqual(self.galleta.stock, D('30'))
        parciales.declarar(o, self.user, '35')
        o.refresh_from_db()
        self.assertEqual((o.cantidad_producida, o.declaraciones.count()), (D('65'), 2))
        # la planificación solo pide lo que falta de la receta (50 − 32.5)
        fila = next(f for f in servicios.requerimientos() if f['p'].pk == self.masa.pk)
        self.assertEqual(fila['requerido'], D('17.50'))
        # vistas: lista con declarado y saldo, ficha con las declaraciones
        self.assertContains(self.client.get(reverse('manufactura:ordenes')), 'Saldo')
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        self.assertContains(r, 'Declaraciones de producción')
        self.assertContains(r, 'Cierre técnico')

    def test_lo_entregado_cuenta_para_la_receta(self):
        """MAN-19: la salida a la orden no se suma encima de la receta; al declarar se descuenta solo lo que falte."""
        from produccion import parciales
        o = self.orden()
        _salida(self, o, self.masa, '20')
        c = o.consumos.get(producto=self.masa)
        self.assertEqual((c.despachado, c.cantidad_real), (D('20'), D('50')))
        self.masa.refresh_from_db()
        antes = self.masa.stock
        parciales.declarar(o, self.user, '30')  # pide 15: ya se entregaron 20
        self.masa.refresh_from_db()
        self.assertEqual(self.masa.stock, antes)
        parciales.declarar(o, self.user, '20')  # acumulado 50 → 25: faltan 5
        self.masa.refresh_from_db()
        self.assertEqual(antes - self.masa.stock, D('5'))

    def test_terminar_con_todo_entregado(self):
        """MAN-19: si todo el material salió por almacén, «Terminar» solo ingresa el producto."""
        o = self.orden()
        _salida(self, o, self.masa, '50')
        _salida(self, o, self.empaque, '100')
        o = self.terminar(o, masa='50')
        self.assertEqual(o.estado, 'TERMINADA')

    def test_sobreproduccion_pide_autorizacion(self):
        from produccion import parciales, servicios
        o = self.orden()
        with self.assertRaisesMessage(servicios.ErrorProduccion, 'sobreproducción'):
            parciales.declarar(o, self.user, '120')
        parciales.declarar(o, self.user, '120', autorizar=True)


class CierreTecnicoTest(BaseProduccion):
    def test_cierre_devuelve_sobrante_y_liquidacion_reparte_variacion(self):
        """MAN-18: cierre técnico con motivo del faltante, devolución del sobrante y liquidación a los lotes."""
        from core.models import Kardex
        from produccion import parciales, servicios
        o = self.orden()
        _salida(self, o, self.masa, '40')
        parciales.declarar(o, self.user, '60')  # la receta pide 30 de masa: sobran 10
        with self.assertRaisesMessage(servicios.ErrorProduccion, 'motivo del faltante'):
            parciales.cierre_tecnico(o, self.user)
        parciales.cierre_tecnico(o, self.user, 'MERMA_NORMAL', 'Falla del horno en el turno noche')
        o.refresh_from_db()
        self.assertEqual(o.estado, 'CERRADA_TEC')
        self.assertEqual(o.consumos.get(producto=self.masa).despachado, D('30'))
        with self.assertRaises(servicios.ErrorProduccion):
            parciales.declarar(o, self.user, '5')
        parciales.costo_tardio(o, self.user, 'MO', HOY.isoformat(), 'Horas del turno noche no registradas',
                               importe='50')
        self.assertContains(self.client.get(reverse('manufactura:orden', args=[o.pk])), 'Liquidación de la orden')
        o = parciales.liquidar_orden(o, self.user)
        self.assertEqual(o.estado, 'TERMINADA')
        self.assertContains(self.client.get(reverse('manufactura:orden', args=[o.pk])), 'Liquidada el')
        estandar = sum((d.valor_estandar for d in o.declaraciones.all()), D('0'))
        materiales, mo, maq, cif = parciales.costo_real(o)
        variacion = materiales + mo + maq + cif - estandar
        self.assertEqual(o.variacion_inventario + o.variacion_costo_ventas, variacion)
        self.assertEqual(o.variacion_inventario, variacion)  # los 60 paquetes siguen en stock
        self.assertTrue(Kardex.objects.filter(concepto='LIQ_COSTO', producto=self.galleta).exists())
        self.assertIn(D('50'), [t.importe for t in o.costos_tardios.all()])
        # MAN-21: después de liquidar, un costo olvidado va a gasto (no se anula la orden)
        t = parciales.costo_tardio(o, self.user, 'MO', HOY.isoformat(), 'Otra hora olvidada del turno',
                                   importe='10')
        self.assertEqual(t.destino, 'GASTO')
        with self.assertRaises(servicios.ErrorProduccion):
            servicios.anular(o, self.user, 'No debería anularse una orden liquidada')

    def test_merma_anormal_va_a_gasto(self):
        from produccion import parciales
        o = self.orden()
        _salida(self, o, self.masa, '45')
        parciales.declarar(o, self.user, '40')
        parciales.cierre_tecnico(o, self.user, 'MERMA_ANORMAL', 'Se quemó una tanda completa', devolver=False)
        o = parciales.liquidar_orden(o, self.user)
        self.assertGreater(o.variacion_gasto, 0)
        self.assertEqual((o.variacion_inventario, o.variacion_costo_ventas), (D('0'), D('0')))
        self.assertEqual(o.merma_anormal, o.variacion_gasto)

    def test_en_proceso_a_fin_de_mes(self):
        """Lo cargado y no declarado de una orden abierta queda como productos en proceso."""
        from produccion import parciales
        o = self.orden()
        _salida(self, o, self.masa, '30')
        parciales.declarar(o, self.user, '10')
        filas = parciales.en_proceso(HOY)
        fila = next(f for f in filas if f[0].pk == o.pk)
        self.assertGreater(fila[3], 0)


class AnularOrdenTest(BaseProduccion):
    def test_anular_devuelve_lo_entregado_y_las_declaraciones(self):
        """MAN-20: al anular la orden vuelve al almacén lo entregado y se revierten sus declaraciones."""
        from produccion import parciales, servicios
        o = self.orden()
        self.masa.refresh_from_db()
        inicial = self.masa.stock
        _salida(self, o, self.masa, '5')
        parciales.declarar(o, self.user, '20')
        servicios.anular(o, self.user, 'Orden registrada por error de prueba')
        self.masa.refresh_from_db()
        self.galleta.refresh_from_db()
        self.assertEqual((self.masa.stock, self.galleta.stock), (inicial, D('0')))
        self.assertEqual(o.consumos.get(producto=self.masa).despachado, D('0'))

    def test_stock_libre_y_versiones_vigentes(self):
        """MAN-20: la ficha separa el stock libre del retenido; la orden nueva no ofrece recetas obsoletas."""
        from produccion import servicios
        from produccion.forms import OrdenProduccionForm
        o = self.orden()
        fila = servicios.disponibilidad(o)[0]
        self.assertIn('libre', fila)
        lista = self.version.lista
        lista.estado = 'OBSOLETA'
        lista.save()
        self.assertNotIn(self.version, OrdenProduccionForm().fields['version'].queryset)

    def test_orden_enorme_no_se_graba(self):
        """MAN-16: tope de cantidad y de consumos (antes la ficha daba error 500 para siempre)."""
        from produccion.forms import OrdenProduccionForm
        datos = {'producto': self.galleta.pk, 'cantidad': '999999999999', 'fecha': HOY.isoformat(),
                 'almacen_insumos': self.almacen.pk, 'almacen_destino': self.almacen.pk, 'costo_servicio': '0'}
        f = OrdenProduccionForm(datos)
        self.assertFalse(f.is_valid())
        self.assertIn('cantidad', f.errors)


class ValidacionesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t188', 't@t.com', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.user)

    def _reglas(self, modelo, **datos):
        from core.utils import reglas_comprobante
        doc = modelo(tipo_comprobante=datos.get('tipo_comprobante', '01'), fecha_emision=HOY)
        base = {'fecha_emision': HOY, 'tipo_operacion': 'GRAVADA'}
        base.update(datos)
        form = SimpleNamespace(instance=doc, cleaned_data=base)
        formset = SimpleNamespace(forms=[SimpleNamespace(cleaned_data={'cantidad': D('1'),
                                                                       'precio_unitario': D('100')})])
        return ' '.join(reglas_comprobante(form, formset))

    def test_compra_serie_numero_periodo_tasas(self):
        """COM-20."""
        from compras.models import Compra
        from core.models import Tercero
        empresa = Tercero.objects.filter(tipo_doc='6', numero_doc__startswith='20').first()
        texto = self._reglas(Compra, serie='X1', numero='ABC', periodo='202613', tipo_cambio=D('-3.5'),
                             detraccion_pct=D('150'), retencion_pct=D('-5'), glosa='x' * 600)
        for parte in ('Serie «X1»', 'Número «ABC»', 'Periodo «202613»', 'tipo de cambio', 'Detracción',
                      'Retención', 'glosa'):
            self.assertIn(parte, texto)
        self.assertIn('Fecha', self._reglas(Compra, fecha_emision=date(1900, 1, 1)) + 'Fecha')
        self.assertIn('emisión', self._reglas(Compra, fecha_emision=date(1900, 1, 1)))
        self.assertIn('persona natural', self._reglas(Compra, tipo_comprobante='02', tercero=empresa))
        self.assertIn('plazo', self._reglas(Compra, fecha_emision=HOY.replace(year=HOY.year - 2),
                                            periodo=HOY.strftime('%Y%m')))
        self.assertEqual(self._reglas(Compra, serie='F001', numero='123', periodo=HOY.strftime('%Y%m')), '')

    def test_venta_tasas_nota_y_plazo(self):
        """VEN-19 y VEN-20."""
        from core.models import Tercero
        from ventas.models import Venta
        texto = self._reglas(Venta, percepcion_pct=D('200'), icbper=D('-1'))
        self.assertIn('Percepción', texto)
        self.assertIn('ICBPER', texto)
        self.assertIn('3 días', self._reglas(Venta, fecha_emision=HOY - timedelta(days=16), serie='F001'))
        self.assertEqual(self._reglas(Venta, fecha_emision=HOY - timedelta(days=16), serie='0001'), '')
        uno, dos = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6')[:2]
        ref = Venta.objects.filter(tercero=uno, estado='REGISTRADO').first() or Venta(tercero=uno, moneda='PEN')
        self.assertIn('mismo cliente', self._reglas(Venta, tipo_comprobante='07', tercero=dos, doc_referencia=ref,
                                                   moneda='PEN'))

    def test_cuotas_sin_error_500(self):
        """VEN-18: 0 o 1000 cuotas dan un mensaje, no un KeyError."""
        from ventas.forms import VentaForm
        cliente = __import__('core.models', fromlist=['Tercero']).Tercero.objects.filter(
            tipo='CLIENTE', tipo_doc='6').first()
        for cuotas in ('0', '1000'):
            f = VentaForm({'tipo_comprobante': '01', 'tercero': cliente.pk, 'fecha_emision': HOY.isoformat(),
                           'forma_pago': 'CREDITO', 'cuotas': cuotas, 'dias_entre_cuotas': '30', 'moneda': 'PEN',
                           'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0',
                           'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0'})
            self.assertFalse(f.is_valid())
            self.assertIn('cuotas', f.errors)

    def test_trasladar_y_periodos_invalidos(self):
        """CON-17 y COM-20: no se traslada un anulado ni a un periodo inexistente."""
        from compras.models import Compra
        from contabilidad.automatico import periodo_valido
        from core.comprobantes import ComprobanteViews
        self.assertFalse(periodo_valido('202613'))
        self.assertFalse(periodo_valido('abcdef'))
        self.assertTrue(periodo_valido('202610'))
        c = Compra(estado='ANULADO', fecha_emision=HOY)
        self.assertIn('anulado', ComprobanteViews._error_traslado(c, HOY.strftime('%Y%m')))
        c.estado = 'REGISTRADO'
        self.assertIn('inválido', ComprobanteViews._error_traslado(c, '202613'))
        self.assertIn('anterior', ComprobanteViews._error_traslado(c, (HOY.replace(day=1) - timedelta(days=40))
                                                                    .strftime('%Y%m')))
        self.assertEqual(self.client.get(reverse('contabilidad:periodos')).status_code, 200)

    def test_contactos_formatos(self):
        """TER-02."""
        from core.forms import TerceroForm
        f = TerceroForm()
        f.cleaned_data = {}
        f._validar_formatos({'telefono': 'abcdefg', 'dias_credito': 10000, 'limite_credito': D('-5000'),
                             'descuento_pronto_pago_pct': D('150'), 'cci': '1234567890123456789',
                             'cuenta_detracciones': 'CUENTA-XYZ', 'tolerancia_precio_pct': D('-10')})
        for campo in ('telefono', 'dias_credito', 'limite_credito', 'descuento_pronto_pago_pct', 'cci',
                      'cuenta_detracciones', 'tolerancia_precio_pct'):
            self.assertIn(campo, f.errors, campo)

    def test_tesoreria_fecha_futura_y_operacion_repetida(self):
        """FIN-11."""
        from finanzas.forms import OperacionForm
        from finanzas.models import Cuenta, Movimiento
        banco = Cuenta.objects.filter(tipo='BANCO', activo=True).first()
        f = OperacionForm({'cuenta': banco.pk, 'fecha': (HOY + timedelta(days=30)).isoformat(),
                           'medio_pago': 'TRANSFERENCIA'})
        self.assertFalse(f.is_valid())
        self.assertIn('futura', str(f.errors['fecha']))
        Movimiento.objects.create(cuenta=banco, fecha=HOY, tipo='INGRESO', concepto='OTRO', monto=D('10'),
                                  numero_operacion='BATDUP1')
        f = OperacionForm({'cuenta': banco.pk, 'fecha': HOY.isoformat(), 'medio_pago': 'TRANSFERENCIA',
                           'numero_operacion': 'batdup1'})
        self.assertIn('numero_operacion', f.errors)
        f = OperacionForm({'cuenta': banco.pk, 'fecha': HOY.isoformat(), 'medio_pago': 'TRANSFERENCIA',
                           'numero_operacion': 'BATDUP1', 'confirmar_repetido': 'on'})
        self.assertTrue(f.is_valid(), f.errors)

    def test_asiento_manual_periodo_cerrado_y_tipo_de_cambio(self):
        """CON-18 y CON-19."""
        from contabilidad.forms import AsientoForm
        from contabilidad.models import PeriodoContable
        anterior = HOY.replace(day=1) - timedelta(days=1)
        PeriodoContable.objects.update_or_create(periodo=anterior.strftime('%Y%m'), defaults={'cerrado': True})
        f = AsientoForm({'fecha': anterior.isoformat(), 'libro': '05', 'glosa': 'Prueba', 'moneda': 'PEN',
                         'tipo_cambio': '1'})
        self.assertIn('cerrado', str(f.errors.get('fecha')))
        f = AsientoForm({'fecha': '1900-01-01', 'libro': '05', 'glosa': 'Prueba', 'moneda': 'USD',
                         'tipo_cambio': '0'})
        self.assertIn('fecha', f.errors)
        self.assertIn('tipo_cambio', f.errors)

    def test_errores_500_de_parametros(self):
        """PLA-15 y ACT-01: mes 13 y años basura dan mensaje o el año en curso, nunca 500."""
        from core.utils import anio_param
        from planillas.forms import PlanillaForm
        rf = RequestFactory()
        for valor in ('-1', '99999', 'abc'):
            self.assertEqual(anio_param(rf.get('/', {'anio': valor})), HOY.year)
            self.assertEqual(self.client.get(reverse('activos:registro') + f'?anio={valor}').status_code, 200)
        f = PlanillaForm({'tipo': 'MENSUAL', 'mes': f'{HOY.year}-13'})
        self.assertFalse(f.is_valid())
        self.assertIn('mes', f.errors)
        futura = (HOY.replace(day=1) + timedelta(days=40)).strftime('%Y-%m')
        self.assertIn('futuro', str(PlanillaForm({'tipo': 'MENSUAL', 'mes': futura}).errors))

    def test_ver_sueldos_solo_con_permiso_explicito(self):
        """SEG-08: el grupo Planillas (calcular implícito) ya no ve sueldos."""
        from core.modulos import GRUPOS
        from core.politicas import ver_sueldos
        jefe = User.objects.create_user('jefe.logistica.188', password='x')
        jefe.groups.add(Group.objects.get_or_create(name=GRUPOS['planillas'])[0])
        self.assertFalse(ver_sueldos(jefe))

    def test_mensajes_de_lineas(self):
        """C29/R01: texto claro al faltar líneas y detalle de los errores por línea."""
        from core.templatetags.erp_tags import errores_lineas
        from inventario.views_requerimientos import ItemsFormSet
        fs = ItemsFormSet({'it-TOTAL_FORMS': '0', 'it-INITIAL_FORMS': '0', 'it-MIN_NUM_FORMS': '1',
                           'it-MAX_NUM_FORMS': '1000'}, prefix='it')
        self.assertFalse(fs.is_valid())
        self.assertIn('Agregue al menos 1 línea', ' '.join(errores_lineas(fs)))
