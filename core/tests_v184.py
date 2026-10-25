"""v1.84: correcciones del checklist del 25/10/2026 (servicios por proyecto, permisos, auditoría)."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.urls import reverse

from contabilidad.models import AsientoLinea, CentroCosto, CuentaContable
from obras import servicios as s
from obras import valorizaciones as val
from obras.models import Obra
from obras.tests_comercial import HOY, Base

from .tests_v181 import usuario


class ServiciosTest(Base):
    def proyecto(self, **extra):
        datos = {'nombre': 'Hospital Regional', 'tipo': 'SERVICIO', 'cliente': self.entidad,
                 'monto_contratado': D('118000'), 'residente': self.user, 'horas_presupuestadas': D('100'),
                 'fecha_inicio': HOY - timedelta(days=10), 'plazo_dias': 60}
        datos.update(extra)
        return Obra.objects.create(**datos)

    def test_adelanto_de_servicios_con_detraccion_022(self):
        """SRV-07: el adelanto de un proyecto de servicios va con el 022 al 12 %; el de una obra, con el 030 al 4 %."""
        p = self.proyecto()
        a = val.registrar_adelanto(p, 'DIRECTO', D('16949.15'), HOY, self.user)
        self.assertEqual((a.factura.detraccion_codigo, a.factura.detraccion_pct), ('022', D('12.00')))
        obra = Obra.objects.create(nombre='Puente', tipo='PRIVADA', cliente=self.entidad, monto_contratado=D('100000'))
        b = val.registrar_adelanto(obra, 'DIRECTO', D('10000'), HOY, self.user)
        self.assertEqual((b.factura.detraccion_codigo, b.factura.detraccion_pct), ('030', D('4.00')))

    def test_entregables_contra_el_contrato_y_adelanto(self):
        """SRV-08: no pasan el contrato sin autorización, avisos de horas y plazo, y el adelanto se amortiza
        completo."""
        p = self.proyecto()
        e1 = s.nuevo_entregable(p, nombre='Anteproyecto', porcentaje=D('40'), fecha_comprometida=HOY,
                                horas_presupuestadas=D('40'))
        s.nuevo_entregable(p, nombre='Proyecto', porcentaje=D('60'), fecha_comprometida=HOY + timedelta(days=20),
                           horas_presupuestadas=D('50'))
        with self.assertRaisesMessage(s.ErrorServicio, '110 %'):
            s.nuevo_entregable(p, nombre='Adicional', porcentaje=D('10'), fecha_comprometida=HOY)
        vendedor = usuario('sin_aprobar', ['obras'], ['obras.presupuestar'])
        with self.assertRaisesMessage(s.ErrorServicio, 'lo autoriza'):
            s.nuevo_entregable(p, True, vendedor, 'Adenda 1 firmada', nombre='Adicional', porcentaje=D('10'),
                               fecha_comprometida=HOY)
        extra = s.nuevo_entregable(p, True, self.user, 'Adenda 1 firmada por el cliente', nombre='Adicional',
                                   porcentaje=D('10'), fecha_comprometida=HOY + timedelta(days=90),
                                   horas_presupuestadas=D('20'))
        self.assertEqual(len(extra.avisos), 2)  # 110 horas de 100 y fuera del plazo
        s.anular_entregable(extra, 'Adenda retirada')
        self.assertEqual(s.vigentes(p).count(), 2)
        r = self.client.post(reverse('obras:entregables', args=[p.pk]), {
            'accion': 'nuevo', 'nombre': 'Sin monto', 'porcentaje': '0', 'monto': '',
            'fecha_comprometida': HOY.isoformat(), 'horas_presupuestadas': '0', 'penalidad_diaria_pct': '0.1',
            'penalidad_maxima_pct': '10'}, follow=True)
        self.assertContains(r, 'registrado')  # SRV-14: el monto vacío se acepta
        a = val.registrar_adelanto(p, 'DIRECTO', D('10000'), HOY, self.user)
        s.entregar(e1, HOY)
        s.conformidad(e1, 'Ing. Pérez')
        c1 = s.facturar(e1, self.user)
        self.assertEqual(c1.items.get(anticipo_de=a.factura).precio_unitario, D('-4000.00'))  # 40 % del contrato
        e2 = p.entregables.get(nombre='Proyecto')
        s.entregar(e2, HOY)
        s.conformidad(e2, 'Ing. Pérez')
        sin_monto = p.entregables.get(nombre='Sin monto')
        s.anular_entregable(sin_monto, 'Registrado por error')
        c2 = s.facturar(e2, self.user)
        self.assertEqual(c2.items.get(anticipo_de=a.factura).precio_unitario, D('-6000.00'))  # el último: el saldo

    def test_conformidad_exige_el_acta(self):
        """SRV-14."""
        p = self.proyecto()
        e = s.nuevo_entregable(p, nombre='Anteproyecto', porcentaje=D('40'), fecha_comprometida=HOY)
        s.entregar(e, HOY)
        r = self.client.post(reverse('obras:entregables', args=[p.pk]), {'accion': 'conformidad', 'entregable': e.pk,
                                                                         'por': 'Ing. Pérez'}, follow=True)
        self.assertContains(r, 'Adjunte el acta')
        r = self.client.post(reverse('obras:entregables', args=[p.pk]), {
            'accion': 'conformidad', 'entregable': e.pk, 'por': 'Ing. Pérez', 'sin_acta': '1'}, follow=True)
        self.assertContains(r, 'sin el acta')

    def test_cierre_con_2351_de_agrupacion_y_distribucion_de_horas(self):
        """SRV-09: la 2351 ya existe (como cuenta de agrupación) y el asiento se registra; SRV-10: el costo de las
        horas llega al centro de costo del proyecto; SRV-12: el centro se llama «Proyecto …»."""
        from planillas.models import Trabajador
        CuentaContable.objects.update_or_create(codigo='2351', defaults={'nombre': 'Servicios en proceso',
                                                                         'imputable': False})
        p = self.proyecto()
        self.assertTrue(p.centro_costo.nombre.startswith('Proyecto '))
        oficina = CentroCosto.objects.create(codigo='ADM01', nombre='Oficina', tipo='ADMINISTRACION')
        t = Trabajador.objects.create(nombres='Luis', apellido_paterno='Rojas', numero_doc='41414141',
                                      sueldo=D('5760'), fecha_ingreso=HOY - timedelta(days=100), centro_costo=oficina)
        e = s.nuevo_entregable(p, nombre='Expediente', porcentaje=D('100'), fecha_comprometida=HOY)
        semana = s.lunes(HOY)
        parte = s.registrar_parte(p, semana, D('30'), self.user, trabajador=t, entregable=e)
        s.aprobar_parte(parte, self.user)
        periodo = semana.strftime('%Y%m')
        sit = s.situacion(p, periodo)
        self.assertEqual(sit['costo_proceso'], D('1305.00'))  # SRV-13: el entregable no está facturado
        self.assertEqual(sit['detalle'][0]['en_proceso'], D('1305.00'))
        asiento = s.distribuir_horas(periodo, self.user)
        self.assertTrue(AsientoLinea.objects.filter(asiento=asiento, centro_costo=p.centro_costo,
                                                    debe=D('1305.00')).exists())
        with self.assertRaisesMessage(s.ErrorServicio, 'No hay horas'):
            s.distribuir_horas(periodo, self.user)
        cierre = s.registrar_cierre(periodo, self.user)
        self.assertTrue(cierre.cuadrado)
        cuentas = {l.cuenta.codigo for l in cierre.lineas.select_related('cuenta')}
        self.assertTrue(any(c.startswith('2351') for c in cuentas), cuentas)
        self.assertTrue(all(l.cuenta.imputable for l in cierre.lineas.select_related('cuenta')))
        r = self.client.get(reverse('obras:servicios_proceso') + f'?periodo={periodo}')
        self.assertContains(r, 'Detalle por entregable')

    def test_proyecto_de_servicios_sin_pantallas_de_obra(self):
        """SRV-12."""
        p = self.proyecto()
        r = self.client.get(reverse('obras:ejecucion', args=[p.pk]))
        self.assertNotContains(r, 'Planilla de construcción civil')
        self.assertContains(r, '100,000.00')  # contrato sin IGV desde el monto contratado
        r = self.client.get(reverse('obras:obra', args=[p.pk]))
        self.assertContains(r, 'Sin almacén')

    def test_presupuesto_por_rubro_con_compromiso_y_exceso(self):
        """SRV-11: presupuesto por rubro; la OC aprobada compromete; lo contabilizado en el centro es gastado; la OC
        que excede el disponible pide aprobación adicional con motivo."""
        from compras.aprobacion_oc import ErrorAprobacionOC, aprobar
        from compras.models import OrdenCompra
        from contabilidad.models import Asiento
        from core.models import Tercero
        from obras import presupuesto_proyecto as pp
        p = self.proyecto()
        r = self.client.post(reverse('obras:presupuesto_proyecto', args=[p.pk]),
                             {'monto_HORAS': '5,000', 'monto_VIAJES': '1000', 'cuentas_VIAJES': ''}, follow=True)
        self.assertContains(r, 'Presupuesto del proyecto guardado: S/ 6,000.00')
        p.refresh_from_db()
        self.assertEqual(p.gastos_presupuestados, D('1000'))
        viaje, _ = CuentaContable.objects.get_or_create(codigo='631101', defaults={'nombre': 'Pasajes',
                                                                                  'imputable': True})
        caja, _ = CuentaContable.objects.get_or_create(codigo='101101', defaults={'nombre': 'Caja', 'imputable': True})
        a = Asiento.objects.create(fecha=HOY, glosa='Rendición de viaje a obra')
        AsientoLinea.objects.create(asiento=a, cuenta=viaje, centro_costo=p.centro_costo, debe=D('300'))
        AsientoLinea.objects.create(asiento=a, cuenta=caja, haber=D('300'))
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20222222222', nombre='VIAJES SAC')
        oc = OrdenCompra.objects.create(numero='OC01-00000901', tercero=prov, centro_costo=p.centro_costo,
                                        rubro_proyecto='VIAJES', estado='PENDIENTE')
        oc.items.create(descripcion='Pasajes Lima - Cusco', cantidad=D('1'), precio_unitario=D('1500'))
        oc.calcular_totales()
        oc.save()
        ev = pp.evaluar_oc(oc)
        self.assertEqual((ev['disponible'], ev['excede']), (D('700'), True))
        self.assertContains(self.client.get(reverse('compras:oc_detalle', args=[oc.pk])), 'excede el presupuesto')
        with self.assertRaisesMessage(ErrorAprobacionOC, 'Indique el motivo'):
            aprobar(oc, self.user)
        aprobar(oc, self.user, 'Viaje adicional pedido por el cliente')
        oc.refresh_from_db()
        self.assertEqual((oc.estado, oc.exceso_autorizado_por), ('APROBADO', self.user))
        fila = next(f for f in pp.control(p)['filas'] if f['rubro'] == 'VIAJES')
        self.assertEqual((fila['gastado'], fila['comprometido']), (D('300'), pp._base(oc)))
        self.assertTrue(fila['excedido'])
        self.assertContains(self.client.get(reverse('obras:presupuesto_proyecto', args=[p.pk])), 'exceso autorizado')


class PermisosYAuditoriaTest(Base):
    def test_nueva_inspeccion_oculta_sin_permiso(self):
        """SEG-07."""
        u = usuario('jefe_logistica', ['control_calidad'], [])
        self.client.force_login(u)
        r = self.client.get(reverse('manufactura:inspecciones'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, reverse('manufactura:inspeccion_nueva'))
        u2 = usuario('analista', ['control_calidad'], ['control_calidad.inspecciones'])
        self.client.force_login(u2)
        self.assertContains(self.client.get(reverse('manufactura:inspecciones')),
                            reverse('manufactura:inspeccion_nueva'))

    def test_auditoria_sin_cambios_falsos(self):
        """AUD-01: 0.00 → 0 no es un cambio."""
        from core.auditoria import _iguales
        self.assertTrue(_iguales('0.00', '0'))
        self.assertTrue(_iguales('0.00', 0))
        self.assertFalse(_iguales('1.00', '0'))
        self.assertFalse(_iguales(True, 'True') and False)
        self.assertFalse(_iguales('ABC', 'abc'))


# ---------------------------------------------------------------- inventario: estados del stock y lotes
from django.contrib.auth.models import User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import TestCase  # noqa: E402
from unittest.mock import patch  # noqa: E402

from core.models import Almacen, Lote, Producto, Tercero  # noqa: E402
from core.sustentos import adjuntar  # noqa: E402
from inventario import estados  # noqa: E402
from inventario import servicios as inv  # noqa: E402
from inventario.models import Operacion, TipoOperacion  # noqa: E402
from inventario.tests_lotes import stock_lote  # noqa: E402


class LotesBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t184', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.almacen = Almacen.principal()
        self.obra = Almacen.objects.create(codigo='OBRA1', nombre='Almacén de obra')
        self.calidad = Almacen.objects.create(codigo='CAL1', nombre='Control de calidad', uso='CALIDAD')
        self.mp = Producto.objects.create(codigo='MP-001', nombre='Alcohol 96°', clase='MATERIA_PRIMA', unidad='LTR',
                                          control='LOTE')

    def operacion(self, codigo, filas, **campos):
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo=codigo), fecha=HOY, **campos)
        for fila in filas:
            producto, cantidad, lote, vence = fila[:4]
            op.items.create(producto=producto, cantidad=D(cantidad), costo_unitario=D('5'), lote=lote,
                            vencimiento=vence, fabricacion=fila[4] if len(fila) > 4 else None)
        if op.tipo.requiere_sustento:
            adjuntar(op, SimpleUploadedFile('acta.pdf', b'%PDF-1.4 acta', content_type='application/pdf'))
        return op

    def ingreso(self, lote, cantidad='10', vence=None, producto=None):
        op = self.operacion('AJ_ING', [(producto or self.mp, cantidad, lote, vence or HOY + timedelta(days=300))],
                            almacen_destino=self.almacen)
        inv.confirmar(op, self.user)
        return Lote.objects.get(producto=producto or self.mp, codigo=lote)


class CuarentenaTest(LotesBase):
    def test_cuarentena_bloquea_toda_salida(self):
        """INV-25: el lote en cuarentena no se consume ni se traslada (salvo a calidad) y no es disponible."""
        from produccion.models import PlantillaCertificado
        from inventario.reportes_masivos import bloqueado
        PlantillaCertificado.objects.create(producto=self.mp, requiere_liberacion=True)
        lote = self.ingreso('L-QC-01')
        self.assertEqual(lote.estado_calidad, 'CUARENTENA')
        consumo = self.operacion('CONS_INT', [(self.mp, '5', 'L-QC-01', None)], almacen_origen=self.almacen)
        self.assertTrue(any('cuarentena' in e for e in inv.errores_confirmacion(consumo)))
        sin_lote = self.operacion('CONS_INT', [(self.mp, '5', '', None)], almacen_origen=self.almacen)
        with self.assertRaisesMessage(estados.ErrorLote, 'No hay stock libre'):
            inv.confirmar(sin_lote, self.user)
        traslado = self.operacion('TRAS_ALM', [(self.mp, '5', 'L-QC-01', None)], almacen_origen=self.almacen,
                                  almacen_destino=self.obra)
        self.assertTrue(inv.errores_confirmacion(traslado))
        self.assertEqual(bloqueado()[(self.mp.pk, self.almacen.pk)], D('10'))
        a_calidad = self.operacion('TRAS_ALM', [(self.mp, '10', 'L-QC-01', None)], almacen_origen=self.almacen,
                                   almacen_destino=self.calidad)
        inv.confirmar(a_calidad, self.user)  # el traslado explícito a calidad sí
        self.assertEqual(stock_lote(self.mp, 'L-QC-01', self.calidad), D('10'))
        r = self.client.get(reverse('inventario:estado_stock'))
        self.assertContains(r, 'En calidad')

    def test_cambio_de_estado_con_permisos(self):
        """INV-26: bloquear con motivo y permiso; liberar un lote en cuarentena lo hace Calidad."""
        lote = self.ingreso('L-01')
        almacenero = usuario('almacenero', ['inventario'], ['inventario.operar'])
        with self.assertRaisesMessage(estados.ErrorLote, 'no cambia'):
            estados.cambiar_estado_lote(lote, 'BLOQUEADO', 'Envase dañado', almacenero)
        jefe = usuario('jefe_alm', ['inventario'], ['inventario.estado_stock'])
        estados.cambiar_estado_lote(lote, 'BLOQUEADO', 'Envase dañado', jefe)
        lote.refresh_from_db()
        self.assertTrue(lote.bloqueado)
        estados.cambiar_estado_lote(lote, 'CUARENTENA', 'A reanálisis', jefe)
        lote.refresh_from_db()
        with self.assertRaisesMessage(estados.ErrorLote, 'no cambia'):
            estados.cambiar_estado_lote(lote, 'LIBRE', 'Conforme', jefe)  # liberar es de Calidad
        qa = usuario('qa', ['control_calidad'], ['control_calidad.liberar_lotes'])
        estados.cambiar_estado_lote(lote, 'LIBRE', 'Reanálisis conforme', qa)
        lote.refresh_from_db()
        self.assertEqual(lote.estado_calidad, 'LIBERADO')
        self.assertEqual(lote.cambios_estado.count(), 3)

    def test_producto_sin_lote_a_calidad_y_liberacion(self):
        """INV-26: un producto sin lote se pone en calidad trasladándolo; sacarlo es liberarlo (con permiso)."""
        harina = Producto.objects.create(codigo='HAR-01', nombre='Harina', clase='MATERIA_PRIMA', unidad='KGM')
        harina.mover_stock(D('20'), 'Inicial', costo=D('2'), almacen=self.almacen, origen='AJUSTE')
        estados.trasladar_estado(harina, D('8'), self.almacen, self.calidad, 'Ingreso a inspección', self.user)
        with self.assertRaisesMessage(estados.ErrorLote, 'no está libre'):
            harina.mover_stock(D('-1'), 'Consumo', almacen=self.calidad, origen='OPERACION', concepto='CONS_INT')
        almacenero = usuario('alm2', ['inventario'], ['inventario.operar'])
        with self.assertRaisesMessage(estados.ErrorLote, 'liberarlo'):
            estados.trasladar_estado(harina, D('8'), self.calidad, self.almacen, 'Liberado', almacenero)
        estados.trasladar_estado(harina, D('8'), self.calidad, self.almacen, 'Inspección conforme', self.user)
        self.assertEqual(harina.stock_en(self.almacen), D('20'))


class FefoVencimientosTest(LotesBase):
    def test_fefo_no_toma_vencidos_ni_cuarentena(self):
        """INV-27."""
        Lote.objects.create(producto=self.mp, codigo='L-VENC-01', vencimiento=date(2026, 1, 1))
        vencido = Lote.objects.get(codigo='L-VENC-01')
        from core.models import StockLote
        StockLote.objects.create(lote=vencido, almacen=self.almacen, cantidad=D('4'))
        self.ingreso('L-BUENO', '6')
        salida = self.operacion('CONS_INT', [(self.mp, '2', '', None)], almacen_origen=self.almacen)
        inv.confirmar(salida, self.user)
        self.assertEqual(stock_lote(self.mp, 'L-VENC-01'), D('4'))  # no tocó el vencido
        self.assertEqual(stock_lote(self.mp, 'L-BUENO'), D('4'))
        directo = self.operacion('CONS_INT', [(self.mp, '1', 'L-VENC-01', None)], almacen_origen=self.almacen)
        self.assertTrue(any('venció' in e for e in inv.errores_confirmacion(directo)))

    def test_ingreso_valida_vencimiento(self):
        """INV-28."""
        sin = self.operacion('AJ_ING', [(self.mp, '1', 'L-SINV-01', None)], almacen_destino=self.almacen)
        self.assertTrue(any('indique el vencimiento' in e for e in inv.errores_confirmacion(sin)))
        vencido = self.operacion('AJ_ING', [(self.mp, '1', 'L-VENC-02', date(2026, 1, 1))],
                                 almacen_destino=self.almacen)
        self.assertTrue(any('venció' in e for e in inv.errores_confirmacion(vencido)))
        self.ingreso('L-QC-02', vence=HOY + timedelta(days=200))
        otro = self.operacion('AJ_ING', [(self.mp, '1', 'L-QC-02', date(2027, 6, 30))], almacen_destino=self.almacen)
        self.assertTrue(any('ya existe con vencimiento' in e for e in inv.errores_confirmacion(otro)))
        self.mp.vida_util_dias = 365
        self.mp.save()
        fab = self.operacion('AJ_ING', [(self.mp, '2', 'L-FAB-01', None, HOY - timedelta(days=5))],
                             almacen_destino=self.almacen)
        inv.confirmar(fab, self.user)
        lote = Lote.objects.get(codigo='L-FAB-01')
        self.assertEqual((lote.fabricacion, lote.vencimiento), (HOY - timedelta(days=5), HOY + timedelta(days=360)))
        self.mp.vida_remanente_recepcion = 400
        self.mp.save()
        corto = self.operacion('AJ_ING', [(self.mp, '1', 'L-CORTO', HOY + timedelta(days=100))],
                               almacen_destino=self.almacen)
        self.assertTrue(any('mínimo para recibirlo' in e for e in inv.errores_confirmacion(corto)))

    def test_regla_fifo_manual_y_vida_de_despacho(self):
        """INV-29."""
        self.ingreso('L-NUEVO-VENCE-PRONTO', '5', HOY + timedelta(days=30))
        self.ingreso('L-B', '5', HOY + timedelta(days=10))
        self.mp.regla_salida = 'FIFO'
        self.mp.save()
        self.mp.mover_stock(D('-2'), 'Consumo FIFO', almacen=self.almacen, origen='OPERACION', concepto='CONS_INT')
        self.assertEqual(stock_lote(self.mp, 'L-NUEVO-VENCE-PRONTO'), D('3'))  # FIFO: el que ingresó primero
        self.mp.regla_salida = 'MANUAL'
        self.mp.save()
        with self.assertRaisesMessage(estados.ErrorLote, 'indique el lote'):
            self.mp.mover_stock(D('-1'), 'Consumo', almacen=self.almacen, origen='OPERACION', concepto='CONS_INT')
        self.mp.regla_salida, self.mp.vida_remanente_despacho = 'FEFO', 60
        self.mp.save()
        with self.assertRaisesMessage(estados.ErrorLote, 'No hay stock libre'):
            self.mp.mover_stock(D('-1'), 'Venta', almacen=self.almacen, origen='VENTA')
        r = self.client.get(reverse('producto_editar', args=[self.mp.pk]))
        self.assertContains(r, 'Regla de salida de lotes')


class LoteProducidoTest(LotesBase):
    def test_terminar_con_lote_vencimiento_y_trazabilidad(self):
        """INV-30: lote del producto terminado con fabricación y vencimiento por vida útil, lote del insumo
        indicado y trazabilidad por el lote producido."""
        from produccion import servicios as prod
        from produccion.models import OrdenProduccion
        from produccion.reportes_produccion import trazabilidad
        from types import SimpleNamespace
        pt = Producto.objects.create(codigo='PT-01', nombre='Gel antibacterial', clase='PRODUCTO_TERMINADO',
                                     control='LOTE', vida_util_dias=730)
        self.ingreso('MP-A', '10', HOY + timedelta(days=100))
        self.ingreso('MP-B', '10', HOY + timedelta(days=50))
        from produccion.models import ListaMateriales
        lista = ListaMateriales.objects.create(producto=pt, codigo='GEL', cantidad_base=D('5'))
        lista.componentes.create(producto=self.mp, cantidad=D('2'))
        orden = OrdenProduccion.objects.create(producto=pt, cantidad=D('5'), almacen_insumos=self.almacen,
                                               almacen_destino=self.almacen, lista=lista)
        prod.explotar(orden)
        prod.confirmar(orden, self.user)
        c = orden.consumos.get(producto=self.mp)
        prod.terminar(orden, self.user, D('5'), {c.pk: D('2')}, {}, lote_producto='261025-G1',
                      lotes_insumos={c.pk: 'MP-A'})
        lote = Lote.objects.get(producto=pt, codigo='261025-G1')
        self.assertEqual(lote.vencimiento, lote.fabricacion + timedelta(days=730))
        self.assertEqual(stock_lote(self.mp, 'MP-A'), D('8'))  # el indicado, no el FEFO (MP-B)
        orden.refresh_from_db()
        self.assertEqual(orden.lote_producido, '261025-G1')
        d = trazabilidad(SimpleNamespace(lote='261025-G1', cliente=None, producto=None))
        self.assertEqual(d['lotes'][0]['orden'], orden)
        self.assertEqual(d['lotes'][0]['insumos'][0]['lote'].codigo, 'MP-A')
        self.assertContains(self.client.get(reverse('inventario:trazabilidad') + '?lote=MP-A'), orden.numero)


# ---------------------------------------------------------------- calidad farmacéutica (CAL-01 a CAL-10)
class CalidadFarmaTest(Base):
    CLAVE = 'Clave-segura-9'

    def setUp(self):
        super().setUp()
        from produccion.models import ParametroCalidad
        self.almacen = Almacen.principal()
        self.mp = Producto.objects.create(codigo='MP-QC', nombre='Ácido cítrico', clase='MATERIA_PRIMA',
                                          unidad='KGM', control='LOTE')
        ParametroCalidad.objects.create(producto=self.mp, nombre='Acidez', unidad='°D', minimo=D('14'), maximo=D('18'))
        self.mp.mover_stock(D('10'), 'Ingreso', costo=D('3'), almacen=self.almacen, origen='OPERACION',
                            concepto='AJ_ING', lotes=[('L-QC-01', D('10'), HOY + timedelta(days=300))])
        self.lote = Lote.objects.get(producto=self.mp, codigo='L-QC-01')
        self.analista = usuario('analista_qc', ['control_calidad'], ['control_calidad.inspecciones'])
        self.qa = usuario('jefe_qa', ['control_calidad'], ['control_calidad.inspecciones', 'control_calidad.liberar_lotes',
                                                           'control_calidad.reabrir', 'control_calidad.aprobar_oos',
                                                           'control_calidad.especificaciones',
                                                           'control_calidad.aprobar_qa'])

    def inspeccion(self, usuario_=None):
        from produccion import planta
        from produccion.models import InspeccionCalidad
        insp = InspeccionCalidad(tipo='RECEPCION', producto=self.mp, lote='L-QC-01', cantidad=D('1'),
                                 almacen=self.almacen)
        return planta.crear_inspeccion(insp, usuario_ or self.analista)

    def registrar(self, insp, valor, resultado='APROBADO', motivo='', usuario_=None):
        from produccion import planta
        r = insp.resultados.get()
        return planta.registrar_resultados(insp, {r.pk: (D(valor), '', None)}, resultado, '', usuario_ or self.analista,
                                           motivo)

    def test_rastro_bloqueo_y_reapertura(self):
        """CAL-01."""
        from produccion import calidad_farma as cf
        from produccion.models import CambioResultado
        from produccion.planta import ErrorPlanta
        insp = self.inspeccion()
        self.registrar(insp, '16', 'PENDIENTE')
        with self.assertRaisesMessage(ErrorPlanta, 'motivo de la corrección'):
            self.registrar(insp, '15', 'APROBADO')
        self.registrar(insp, '15', 'APROBADO', motivo='Error de transcripción del cuaderno')
        cambios = list(CambioResultado.objects.filter(resultado__inspeccion=insp).values_list('valor_anterior',
                                                                                             'valor_nuevo'))
        self.assertEqual(cambios, [('', '16'), ('16', '15')])  # el original no se borra
        with self.assertRaisesMessage(ErrorPlanta, 'ya se decidió'):
            self.registrar(insp, '17', 'APROBADO', motivo='Otro cambio posterior')
        with self.assertRaisesMessage(ErrorPlanta, 'Reabre'):
            cf.reabrir(insp, self.analista, 'Revisión del certificado del proveedor', self.CLAVE)
        with self.assertRaisesMessage(ErrorPlanta, 'contraseña no es correcta'):
            cf.reabrir(insp, self.qa, 'Revisión del certificado del proveedor', 'otra')
        cf.reabrir(insp, self.qa, 'Revisión del certificado del proveedor', self.CLAVE)
        insp.refresh_from_db()
        self.assertEqual(insp.resultado, 'PENDIENTE')

    def test_oos_invalida_o_confirma(self):
        """CAL-02: fuera de especificación abre el OOS y no deja aprobar; error de laboratorio → reanálisis;
        confirmado → lote rechazado."""
        from produccion import calidad_farma as cf
        from produccion.planta import ErrorPlanta
        insp = self.registrar(self.inspeccion(), '22', 'OBSERVADO')
        self.assertEqual(insp.resultado, 'OOS')
        oos = insp.oos.get()
        with self.assertRaisesMessage(ErrorPlanta, 'Cierra la investigación'):
            cf.cerrar_oos(oos, {'conclusion': 'ERROR_LAB'}, self.analista, self.CLAVE)
        datos = {'conclusion': 'ERROR_LAB', 'hipotesis': 'Bureta descalibrada en la titulación del analista',
                 'causa_raiz': 'Bureta fuera de calibración', 'reanalisis': D('16')}
        cf.cerrar_oos(oos, datos, self.qa, self.CLAVE)
        insp.refresh_from_db()
        r = insp.resultados.get()
        self.assertEqual((insp.resultado, r.valor, r.conforme), ('PENDIENTE', D('16'), True))
        otra = self.registrar(self.inspeccion(), '25', 'RECHAZADO')
        cf.cerrar_oos(otra.oos.get(), {'conclusion': 'CONFIRMADO', 'causa_raiz': 'Lote del proveedor fuera de norma'},
                      self.qa, self.CLAVE)
        otra.refresh_from_db()
        self.lote.refresh_from_db()
        self.assertEqual((otra.resultado, self.lote.estado_calidad), ('RECHAZADO', 'RECHAZADO'))

    def test_liberacion_segregada_firmada_y_revision_qa(self):
        """CAL-03, CAL-06, CAL-07: quien analizó no libera; la liberación va con firma; una desviación abierta la
        bloquea."""
        from produccion import calidad_farma as cf
        from produccion import planta
        from produccion.models import CertificadoAnalisis, Desviacion
        from produccion.planta import ErrorPlanta
        insp = self.registrar(self.inspeccion(self.qa), '16', 'APROBADO', usuario_=self.qa)
        cert = planta.crear_certificado(CertificadoAnalisis(lote=self.lote, inspeccion=insp), self.qa)
        with self.assertRaisesMessage(ErrorPlanta, 'firma electrónica'):
            planta.resolver_certificado(cert, self.qa, 'aprobar')
        with self.assertRaisesMessage(ErrorPlanta, 'segregación'):
            cf.liberar(cert, self.qa, self.CLAVE)
        otro_qa = usuario('qa2', ['control_calidad'], ['control_calidad.liberar_lotes'])
        d = Desviacion.objects.create(criticidad='MAYOR', descripcion='Temperatura de almacén fuera de rango',
                                      lote=self.lote)
        with self.assertRaisesMessage(ErrorPlanta, 'Desviación'):
            cf.liberar(cert, otro_qa, self.CLAVE)
        d.impacto, d.causa_raiz = 'Sin impacto en la acidez', 'Falla del aire acondicionado del almacén'
        d.save()
        cf.cerrar_desviacion(d, self.qa, self.CLAVE)
        cf.liberar(cert, otro_qa, self.CLAVE)
        cert.refresh_from_db()
        self.lote.refresh_from_db()
        self.assertEqual((cert.estado, self.lote.estado_calidad), ('APROBADO', 'LIBERADO'))
        self.assertEqual(cert.firma.significado, 'Liberado por QA')
        r = self.client.get(reverse('manufactura:certificado', args=[cert.pk]))
        self.assertContains(r, 'Firmado electrónicamente')

    def test_validaciones_de_la_inspeccion_e_instrumento(self):
        """CAL-04 y CAL-08."""
        from produccion import calidad_farma as cf
        from produccion.forms_planta import InspeccionForm
        from produccion.models import CalibracionEquipo, Equipo
        datos = {'tipo': 'TERMINADO', 'fecha': HOY.isoformat(), 'producto': self.mp.pk, 'lote': 'LOTE-QUE-NO-EXISTE',
                 'cantidad': '999', 'almacen': self.almacen.pk}
        f = InspeccionForm(datos)
        self.assertFalse(f.is_valid())
        self.assertIn('tipo', f.errors)
        self.assertIn('lote', f.errors)
        f = InspeccionForm({**datos, 'tipo': 'RECEPCION', 'lote': 'L-QC-01'})
        self.assertIn('cantidad', f.errors)
        balanza = Equipo.objects.create(codigo='BAL-01', nombre='Balanza analítica', es_instrumento=True)
        f = InspeccionForm({**datos, 'tipo': 'RECEPCION', 'lote': 'L-QC-01', 'cantidad': '1', 'instrumento': balanza.pk})
        self.assertIn('instrumento', f.errors)
        cf.registrar_calibracion(CalibracionEquipo(equipo=balanza, fecha=HOY - timedelta(days=10),
                                                   proxima=HOY + timedelta(days=170)), self.qa)
        self.assertTrue(InspeccionForm({**datos, 'tipo': 'RECEPCION', 'lote': 'L-QC-01', 'cantidad': '1',
                                        'instrumento': balanza.pk}).is_valid())

    def test_especificacion_versionada(self):
        """CAL-05."""
        from produccion import calidad_farma as cf
        from produccion.planta import ErrorPlanta
        esp = cf.nueva_version(self.mp, self.analista, 'Versión inicial', referencia='USP 47')
        with self.assertRaisesMessage(ErrorPlanta, 'especificación aprobada'):
            self.inspeccion()
        with self.assertRaisesMessage(ErrorPlanta, 'Aprueba las especificaciones'):
            cf.aprobar_especificacion(esp, self.analista, self.CLAVE)
        cf.aprobar_especificacion(esp, self.qa, self.CLAVE)
        insp = self.inspeccion()
        self.assertEqual(insp.especificacion, esp)
        v2 = cf.nueva_version(self.mp, self.analista, 'Cambio de rango')
        cf.aprobar_especificacion(v2, self.qa, self.CLAVE)
        esp.refresh_from_db()
        self.assertEqual(esp.estado, 'OBSOLETA')

    def test_registro_de_lote_y_retiro(self):
        """CAL-06 y CAL-07."""
        from produccion import calidad_farma as cf
        from produccion.models import PasoRegistroLote, RetiroMercado
        from produccion.planta import ErrorPlanta
        from produccion.models import ListaMateriales, OrdenProduccion
        from produccion import servicios as prod
        pt = Producto.objects.create(codigo='PT-QC', nombre='Jarabe', clase='PRODUCTO_TERMINADO', control='LOTE',
                                     vida_util_dias=365)
        lista = ListaMateriales.objects.create(producto=pt, codigo='JAR', cantidad_base=D('1'))
        lista.componentes.create(producto=self.mp, cantidad=D('1'))
        orden = OrdenProduccion.objects.create(producto=pt, cantidad=D('1'), almacen_insumos=self.almacen,
                                               almacen_destino=self.almacen, lista=lista)
        prod.explotar(orden)
        prod.confirmar(orden, self.user)
        reg = cf.crear_registro_lote(orden)
        paso = reg.pasos.first()
        cf.firmar_paso(paso, self.analista, 'hecho', '62 °C')
        with self.assertRaisesMessage(ErrorPlanta, 'otra persona'):
            cf.firmar_paso(paso, self.analista, 'verificado')
        c = orden.consumos.get()
        prod.terminar(orden, self.user, D('1'), {c.pk: D('1')}, {})
        lote_pt = Lote.objects.get(producto=pt, codigo=orden.numero)
        self.assertTrue(any('registro de lote' in b for b in cf.bloqueos_liberacion(lote_pt)))
        r = self.client.get(reverse('manufactura:registro_lote', args=[orden.pk]))
        self.assertContains(r, 'Despeje de línea')
        ret = cf.iniciar_retiro(RetiroMercado(tipo='REAL', lote=lote_pt, motivo='Simulación de reclamo grave'))
        lote_pt.refresh_from_db()
        self.assertEqual((ret.clientes, lote_pt.estado_calidad), ([], 'BLOQUEADO'))
        self.assertEqual(PasoRegistroLote.objects.filter(registro=reg).count(), reg.pasos.count())

    def test_documento_con_firmas_y_lectura(self):
        """CAL-09."""
        from calidad import documentos
        from calidad.models import DocumentoControlado, LecturaDocumento
        elaborador = usuario('elab', ['calidad'], ['calidad.registrar'])
        revisor = usuario('revisor', ['calidad'], ['calidad.registrar'])
        aprobador = usuario('aprob', ['calidad'], ['calidad.validar'])
        doc = DocumentoControlado.objects.create(codigo='POE-QC-01', titulo='Muestreo de materias primas',
                                                 creado_por=elaborador)
        with self.assertRaisesMessage(documentos.ErrorDocumento, 'otra persona'):
            documentos.revisar(doc, elaborador, self.CLAVE)
        with self.assertRaisesMessage(documentos.ErrorDocumento, 'Primero'):
            documentos.aprobar(doc, aprobador, self.CLAVE)
        documentos.revisar(doc, revisor, self.CLAVE)
        documentos.aprobar(doc, aprobador, self.CLAVE)
        doc.refresh_from_db()
        self.assertEqual(doc.estado_validacion, 'VALIDADO')
        self.client.force_login(revisor)
        self.client.post(reverse('calidad:documento_flujo', args=[doc.pk]), {'accion': 'leido'})
        self.assertTrue(LecturaDocumento.objects.filter(documento=doc, usuario=revisor).exists())

    def test_proveedor_calificado(self):
        """CAL-10: la OC no se aprueba y la recepción no se confirma con un proveedor no calificado; con inspección
        completa el lote entra en cuarentena."""
        from compras.models import OrdenCompra, RegistroInfo
        from compras.registro_info import problemas_orden
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20111111111', nombre='QUÍMICA SAC')
        self.mp.exige_proveedor_calificado = True
        self.mp.save()
        oc = OrdenCompra.objects.create(numero='OC01-00000777', tercero=prov, estado='PENDIENTE')
        oc.items.create(producto=self.mp, descripcion='Ácido', cantidad=D('5'), precio_unitario=D('3'))
        self.assertTrue(problemas_orden(oc)[0])
        RegistroInfo.objects.create(producto=self.mp, proveedor=prov, calificacion='APROBADO',
                                    calificado_hasta=HOY + timedelta(days=200), nivel_inspeccion='COMPLETA')
        self.assertEqual(problemas_orden(oc)[0], [])
        oc.estado = 'APROBADO'
        oc.save()
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), fecha=HOY,
                                      almacen_destino=self.almacen, orden_compra=oc)
        op.items.create(producto=self.mp, cantidad=D('5'), lote='L-PROV-01', vencimiento=HOY + timedelta(days=300))
        inv.confirmar(op, self.user)
        self.assertEqual(Lote.objects.get(producto=self.mp, codigo='L-PROV-01').estado_calidad, 'CUARENTENA')
