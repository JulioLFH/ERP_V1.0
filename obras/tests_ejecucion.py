"""Checklist v1.78.0 (24/10 03h50): OBR-13 adelanto a anticipos y reclasificación, OBR-14 valorizaciones con
amortización y fondo de garantía, OBR-21 fórmula polinómica, OBR-17 cronograma y avance, OBR-16 versiones del S10,
OBR-18 tareo y planilla de construcción civil, OBR-19 partes de equipo, OBR-20 subcontratos, OBR-22 control por
partida, OBR-15 almacén de obra en la recepción y COM-13 stock antes de comprar (traslados)."""
import io
from datetime import timedelta
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento, AsientoLinea, CuentaDefecto
from contabilidad.reparto import resultado
from core.models import Almacen, Producto, StockAlmacen, Tercero
from ventas.models import Venta, VentaItem

from . import calculos
from . import construccion_civil as civil
from . import ejecucion as ej
from . import equipos as eq
from . import subcontratos as sub
from . import valorizaciones as val
from . import versiones as ver
from .models import AvancePartida, Obra, ParteEquipo, Partida, Presupuesto
from .tests_comercial import HOY, P, Base, saldo


def mes_anterior(fecha):
    return (fecha.replace(day=1) - timedelta(days=1)).replace(day=1)


class EjecucionBase(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Tercero.objects.filter(pk=cls.entidad.pk).update(direccion='Plaza de Armas 100, Arequipa')
        cls.entidad.refresh_from_db()
        cls.obra = Obra.objects.create(nombre='Puente Chilina', cliente=cls.entidad, tipo='PUBLICA', estado='EJECUCION',
                                       adelanto_directo_pct=D('10'), retencion_garantia_pct=D('5'),
                                       supervisor='Ing. Supervisor')
        cls.p = Presupuesto.objects.create(obra=cls.obra, tipo='CONTRACTUAL', gastos_generales_pct=D('10'),
                                           utilidad_pct=D('10'))
        cls.a = Partida.objects.create(presupuesto=cls.p, item='01', descripcion='Excavación', unidad='m3',
                                       metrado=D('100'), precio_fijo=True, precio=D('50'))
        cls.b = Partida.objects.create(presupuesto=cls.p, item='02', descripcion='Concreto', unidad='m3',
                                       metrado=D('10'), precio_fijo=True, precio=D('1000'))
        calculos.recalcular(cls.p)
        Presupuesto.objects.filter(pk=cls.p.pk).update(estado='APROBADO')
        cls.p.refresh_from_db()

    def cta(self):
        return CuentaDefecto.mapa()


class AdelantoValorizacionTest(EjecucionBase):
    def test_adelanto_va_a_anticipos_con_detraccion_y_no_es_venta(self):
        self.assertEqual(self.p.subtotal, D('18000.00'))
        a = val.registrar_adelanto(self.obra, 'DIRECTO', D('1800'), HOY, self.user)
        f = a.factura
        self.assertEqual((f.base_imponible, f.igv, f.total), (D('1800.00'), D('324.00'), D('2124.00')))
        self.assertEqual((f.detraccion_pct, f.detraccion_codigo), (D('4.00'), '030'))
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        cta = self.cta()
        self.assertEqual(saldo(cta['mov_ANTICIPO_INGRESO'].codigo), D('-1800.00'))  # Haber 122
        filas, _ = resultado(P, P, solo_obras=True)
        fila = next(x for x in filas if x['obra'] and x['obra'].pk == self.obra.pk) if filas else None
        self.assertTrue(fila is None or fila['ventas'] == 0)  # el adelanto no es venta

    def test_reclasificar_factura_emitida_como_venta(self):
        from core.models import Serie
        serie, numero = Serie.siguiente('01', 'F001')
        v = Venta.objects.create(tipo_comprobante='01', serie=serie, numero=numero, tercero=self.entidad,
                                 fecha_emision=HOY, centro_costo=self.obra.centro_costo)
        VentaItem.objects.create(documento=v, descripcion='Adelanto directo 10 %', cantidad=1, precio_unitario=D('1800'))
        v.calcular_totales()
        v.save()
        filas, _ = resultado(P, P, solo_obras=True)
        self.assertEqual(next(x for x in filas if x['obra'].pk == self.obra.pk)['ventas'], D('1800.00'))
        a = val.reclasificar(v, self.obra, 'DIRECTO', self.user, 'Era el adelanto')
        self.assertTrue(a.reclasificada)
        self.assertEqual(a.monto, D('1800.00'))
        with self.assertRaisesMessage(val.ErrorValorizacion, 'ya es un adelanto'):
            val.reclasificar(v, self.obra, 'DIRECTO', self.user)
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        self.assertEqual(saldo(self.cta()['mov_ANTICIPO_INGRESO'].codigo), D('-1800.00'))
        filas, _ = resultado(P, P, solo_obras=True)
        self.assertFalse([x for x in filas if x['obra'].pk == self.obra.pk and x['ventas']])

    def _valorizar(self):
        val.registrar_adelanto(self.obra, 'DIRECTO', D('1800'), HOY, self.user)
        ej.registrar_avance(self.obra, self.a, HOY, D('40'), self.user)
        v = val.nueva(self.obra, P, self.user)
        return v

    def test_valorizacion_amortiza_retiene_y_factura_con_deduccion_del_anticipo(self):
        v = self._valorizar()
        linea = v.lineas.get(partida=self.a)
        self.assertEqual(linea.metrado, D('40'))  # propuesto desde el avance físico
        v.refresh_from_db()
        self.assertEqual((v.costo_directo, v.gastos_generales, v.utilidad, v.subtotal),
                         (D('2000.00'), D('200.00'), D('200.00'), D('2400.00')))
        self.assertEqual(v.amort_directo, D('240.00'))  # 2 400 × 1 800 / 18 000
        self.assertEqual((v.base_factura, v.igv, v.total_factura), (D('2160.00'), D('388.80'), D('2548.80')))
        self.assertEqual(v.retencion_garantia, D('127.44'))  # 5 % del total
        with self.assertRaisesMessage(val.ErrorValorizacion, 'Supera el metrado contractual'):
            val.calcular(v, {linea.pk: D('101')})
        val.enviar(v, self.user)
        with self.assertRaisesMessage(val.ErrorValorizacion, 'supervisor'):
            val.aprobar(v, self.user, '', HOY)
        val.aprobar(v, self.user, 'Ing. Supervisor', HOY)
        comp = val.facturar(v, self.user, HOY)
        self.assertEqual((comp.base_imponible, comp.total), (D('2160.00'), D('2548.80')))
        deduccion = comp.items.get(anticipo_de__isnull=False)
        self.assertEqual(deduccion.subtotal, D('-240.00'))
        self.assertEqual(self.obra.adelantos.get().saldo, D('1560.00'))
        # Nubefact: la deducción va en positivo como regularización del anticipo
        from core.sunat import payload_comprobante
        pl = payload_comprobante(comp)
        reg = [i for i in pl['items'] if i['anticipo_regularizacion'] == 'true']
        self.assertEqual(len(reg), 1)
        self.assertEqual(D(str(reg[0]['subtotal'])), D('240'))
        self.assertEqual(D(str(pl['total_anticipo'])), D('240'))
        self.assertEqual(D(str(pl['total_gravada'])), D('2160'))
        # contabilidad: 122 al debe por la amortización y el fondo de garantía a la 1683
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        cta = self.cta()
        self.assertEqual(saldo(cta['mov_ANTICIPO_INGRESO'].codigo), D('-1560.00'))
        self.assertEqual(saldo(cta['obras_garantia'].codigo), D('127.44'))
        for a in Asiento.objects.filter(periodo=P):
            self.assertTrue(a.cuadrado, a.glosa)
        filas, _ = resultado(P, P, solo_obras=True)
        self.assertEqual(next(x for x in filas if x['obra'].pk == self.obra.pk)['ventas'], D('2400.00'))
        # devolución del fondo de garantía
        from finanzas.models import Cuenta
        banco = Cuenta.objects.create(nombre='BCP', banco='BCP', tipo='BANCO')
        with self.assertRaisesMessage(val.ErrorValorizacion, 'hasta el saldo'):
            val.cobrar_garantia(self.obra, D('200'), HOY, banco, self.user)
        val.cobrar_garantia(self.obra, D('127.44'), HOY, banco, self.user)
        self.assertEqual(val.garantia(self.obra)[2], D('0'))

    def test_formula_polinomica_reintegro_y_deduccion(self):
        filas = [['Monomio', 'Factor', '(%)', 'Símbolo', 'Índice', 'Descripción'],
                 [1, 0.400, 100, 'J', 47, 'MANO DE OBRA'], [2, 0.300, 60, 'M', 21, 'CEMENTO'],
                 [None, None, 40, None, 43, 'MADERA'], [3, 0.300, 100, 'E', 49, 'MAQUINARIA']]
        monomios = val.leer_formula(filas)
        self.assertEqual([(s, c, i) for s, c, i, _ in monomios],
                         [('J', D('0.400'), '47'), ('M', D('0.180'), '21'), ('M', D('0.120'), '43'),
                          ('E', D('0.300'), '49')])
        base = '202501'
        anterior = mes_anterior(HOY)
        val.guardar_formula(self.obra, monomios, periodo_base=base)
        val.guardar_indices(base, {(c, '1'): D('100') for c in ('47', '21', '43', '49')})
        val.guardar_indices(anterior.strftime('%Y%m'), {(c, '1'): D('100') for c in ('47', '21', '43', '49')})
        val.guardar_indices(P, {('47', '1'): D('110'), ('21', '1'): D('105'), ('43', '1'): D('100'),
                                ('49', '1'): D('120')})
        self.assertEqual(val.coeficiente_k(self.obra.formulas.get(), P), (D('1.109'), []))
        val.registrar_adelanto(self.obra, 'DIRECTO', D('1800'), anterior, self.user)  # Ka = 1.000
        ej.registrar_avance(self.obra, self.a, HOY, D('40'), self.user)
        v = val.nueva(self.obra, P, self.user)
        v.refresh_from_db()
        self.assertEqual(v.k, D('1.109'))
        self.assertEqual(v.reintegro, D('261.60'))  # 2 400 × 0.109
        self.assertEqual(v.deduccion_reintegro, D('26.16'))  # 240 × (1.109 / 1.000 − 1)
        self.assertEqual(v.base_factura, D('2400.00') + D('261.60') - D('26.16') - D('240.00'))
        with self.assertRaisesMessage(val.ErrorValorizacion, 'deben sumar 1.000'):
            val.guardar_formula(self.obra, [('J', D('0.5'), '47', '')], periodo_base=base)

    def test_pantallas(self):
        v = self._valorizar()
        for url in (reverse('obras:ejecucion', args=[self.obra.pk]), reverse('obras:valorizacion', args=[v.pk]),
                    reverse('obras:valorizacion', args=[v.pk]) + '?formato=excel',
                    reverse('obras:formula', args=[self.obra.pk]), reverse('obras:indices'),
                    reverse('obras:cronograma', args=[self.obra.pk]), reverse('obras:avance', args=[self.obra.pk]),
                    reverse('obras:reporte_partidas', args=[self.obra.pk]),
                    reverse('obras:reporte_partidas', args=[self.obra.pk]) + '?formato=excel',
                    reverse('obras:versiones', args=[self.obra.pk]), reverse('obras:tareo', args=[self.obra.pk]),
                    reverse('obras:planillas_civil', args=[self.obra.pk]), reverse('obras:partes', args=[self.obra.pk]),
                    reverse('obras:subcontratos', args=[self.obra.pk]), reverse('obras:reclasificar', args=[self.obra.pk]),
                    reverse('obras:control', args=[self.p.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('obras:valorizacion', args=[v.pk]), {'accion': 'guardar',
                                                                         f'metrado_{v.lineas.get(partida=self.a).pk}': '50'})
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.costo_directo, D('2500.00'))


class PlaneamientoTest(EjecucionBase):
    def test_cronograma_excel_curva_s_y_atrasos(self):
        from openpyxl import Workbook
        anterior = mes_anterior(HOY)
        wb = Workbook()
        ws = wb.active
        ws.append(['Ítem', 'Descripción', anterior.strftime('%Y-%m'), HOY.strftime('%Y-%m')])
        ws.append(['01', 'Excavación', 60, 40])
        ws.append(['99', 'No existe', 1, 1])
        buf = io.BytesIO()
        wb.save(buf)
        n, faltan = ej.importar_cronograma(self.p, SimpleUploadedFile('crono.xlsx', buf.getvalue()))
        self.assertEqual((n, len(faltan)), (1, 1))
        ej.registrar_avance(self.obra, self.a, HOY, D('40'), self.user)
        atrasos = ej.atrasadas(self.obra)
        self.assertEqual([x['partida'].pk for x in atrasos], [self.a.pk])  # programado 60 hasta el mes pasado
        curva = ej.curva_s(self.obra)
        self.assertEqual(curva['filas'][0]['programado'], D('3000.00'))
        self.assertEqual(curva['filas'][-1]['ejecutado'], D('2000.00'))

    def test_cronograma_ms_project_xml(self):
        xml = (b'<?xml version="1.0"?><Project xmlns="http://schemas.microsoft.com/project"><Tasks>'
               b'<Task><Name>Obra</Name><Summary>1</Summary><Start>2026-01-01T08:00:00</Start>'
               b'<Finish>2026-02-28T17:00:00</Finish></Task>'
               b'<Task><Name>Concreto</Name><WBS>2</WBS><OutlineNumber>2</OutlineNumber><Summary>0</Summary>'
               b'<Start>2026-01-01T08:00:00</Start><Finish>2026-02-28T17:00:00</Finish></Task></Tasks></Project>')
        n, faltan = ej.importar_cronograma(self.p, SimpleUploadedFile('plan.xml', xml))
        self.assertEqual((n, faltan), (1, []))
        meses = dict(self.b.programacion.values_list('periodo', 'metrado'))
        self.assertEqual(meses['202601'] + meses['202602'], D('10'))
        self.assertEqual(meses['202601'], D('5.2542'))  # 31 de 59 días

    def test_version_nueva_compara_y_traslada_lo_ejecutado(self):
        ej.registrar_avance(self.obra, self.a, HOY, D('10'), self.user)
        nuevo = calculos.copiar(self.p, 'CONTRACTUAL')
        Partida.objects.filter(presupuesto=nuevo, item='01').update(metrado=D('120'))
        Partida.objects.filter(presupuesto=nuevo, item='02').delete()
        Partida.objects.create(presupuesto=nuevo, item='03', descripcion='Muro', unidad='m2', metrado=D('5'),
                               precio_fijo=True, precio=D('200'))
        calculos.recalcular(nuevo)
        nuevo.refresh_from_db()
        c = ver.comparar(self.p, nuevo)
        self.assertEqual([x.item for x in c['altas']], ['03'])
        self.assertEqual([x.item for x in c['bajas']], ['02'])
        self.assertEqual([x['ahora'].item for x in c['cambios']], ['01'])
        r = self.client.post(reverse('obras:presupuesto', args=[nuevo.pk]), {'accion': 'aprobar'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(AvancePartida.objects.get().partida.presupuesto_id, nuevo.pk)
        self.assertEqual(val.presupuesto_contractual(self.obra).pk, nuevo.pk)
        self.assertEqual(self.client.get(reverse('obras:versiones', args=[self.obra.pk]) +
                                         f'?anterior={self.p.pk}&nuevo={nuevo.pk}').status_code, 200)


class ManoObraEquiposTest(EjecucionBase):
    def test_tareo_planilla_civil_y_su_asiento(self):
        from planillas.models import Trabajador
        t = Trabajador.objects.create(numero_doc='40000001', apellido_paterno='MAMANI', nombres='JUAN',
                                      fecha_ingreso=HOY - timedelta(days=30), regimen='CONSTRUCCION',
                                      categoria_civil='OPERARIO', sistema_pensiones='ONP')
        with self.assertRaisesMessage(civil.ErrorCivil, 'más de 24 horas'):
            civil.registrar_tareo(self.obra, HOY, 'Concreto', [(t, self.a, D('20'), D('5'), D('0'))], self.user)
        civil.registrar_tareo(self.obra, HOY, 'Concreto', [(t, self.a, D('8'), D('2'), D('0'))], self.user)
        pl = civil.nueva_planilla(self.obra, HOY)
        l = pl.lineas.get()
        self.assertEqual((l.dias, l.basico, l.dominical, l.buc, l.movilidad),
                         (D('1.00'), D('89.30'), D('14.88'), D('28.58'), D('8.60')))
        self.assertEqual(l.extras, D('35.72'))  # 89.30 / 8 × 2 × 1.60
        self.assertEqual(l.conafovicer, D('1.79'))
        self.assertEqual(l.neto, l.basico + l.dominical + l.buc + l.extras + l.movilidad - l.pension - l.conafovicer)
        civil.cerrar(pl)
        if pl.hasta.strftime('%Y%m') == P:
            self.assertEqual(centralizar_periodo(P)['errores'], [])
            a = Asiento.objects.get(origen='PLANILLA', periodo=P, glosa__startswith='Planillas de construcción')
            self.assertTrue(a.cuadrado)
            self.assertTrue(AsientoLinea.objects.filter(asiento=a, centro_costo=self.obra.centro_costo,
                                                        cuenta__codigo='6211').exists())
        r = ej.reporte_partidas(self.obra)
        fila = next(f for f in r['filas'] if not f['titulo'] and f['partida'].pk == self.a.pk)
        self.assertEqual(fila['mo'], l.costo)
        hh = civil.horas_meta_real(self.obra)
        self.assertEqual(hh['categorias'][0]['real'], D('10'))
        self.assertEqual(self.client.get(reverse('obras:planilla_civil', args=[pl.pk])).status_code, 200)

    def test_parte_de_equipo_lectura_en_flota_y_horas_para_el_reparto(self):
        from flota.models import LecturaEquipo
        from produccion.models import Equipo
        e = Equipo.objects.create(codigo='EX-01', nombre='Excavadora', medidor='HORAS')
        parte = eq.registrar(ParteEquipo(obra=self.obra, fecha=HOY, equipo=e, partida=self.a,
                                         horometro_inicio=D('100'), horometro_fin=D('108'), tarifa=D('150')), self.user)
        self.assertEqual((parte.horas, parte.costo), (D('8'), D('1200.00')))
        self.assertEqual(LecturaEquipo.objects.get(equipo=e).valor, D('108'))
        with self.assertRaises(eq.ErrorParte):  # lectura menor a la anterior
            eq.registrar(ParteEquipo(obra=self.obra, fecha=HOY, equipo=e, horometro_inicio=D('90'),
                                     horometro_fin=D('95'), tarifa=D('150')), self.user)
        self.assertEqual(eq.horas_por_centro(P, [self.obra.centro_costo_id]), {self.obra.centro_costo_id: D('8')})
        self.assertEqual(eq.resumen(self.obra)['total'], D('1200.00'))


class SubcontratoTest(EjecucionBase):
    def test_subcontrato_orden_valorizacion_hes_y_garantia(self):
        from compras.models import Compra, ConformidadServicio
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100070970', nombre='ENCOFRADOS SAC')
        sc = sub.crear(self.obra, prov, 'Excavación', [(self.a, '', '', D('50'), D('30'))], self.user,
                       garantia_pct=D('5'))
        self.assertEqual(sc.monto, D('1500.00'))
        oc = sub.emitir_orden(sc, self.user)
        with self.assertRaisesMessage(sub.ErrorSubcontrato, 'no está aprobada'):
            v = sub.valorizar(sc, P, {sc.lineas.get().pk: D('20')}, self.user)
            sub.aprobar(v, self.user)
        type(oc).objects.filter(pk=oc.pk).update(estado='APROBADO')
        sc.refresh_from_db()
        sub.anular(sc.valorizaciones.get())
        with self.assertRaisesMessage(sub.ErrorSubcontrato, 'Supera el metrado'):
            sub.valorizar(sc, P, {sc.lineas.get().pk: D('60')}, self.user)
        v = sub.valorizar(sc, P, {sc.lineas.get().pk: D('20')}, self.user)
        self.assertEqual((v.bruto, v.garantia), (D('600.00'), D('35.40')))
        sub.aprobar(v, self.user)
        hes = ConformidadServicio.objects.get()
        self.assertEqual(hes.cantidad, D('20'))
        c = Compra.objects.create(tipo_comprobante='01', serie='F001', numero='77', tercero=prov, fecha_emision=HOY,
                                  centro_costo=self.obra.centro_costo, base_imponible=D('600'), igv=D('108'),
                                  total=D('708'))
        sub.retener_garantia(v, c, self.user)
        self.assertEqual(sub.garantia(sc)[0], D('35.40'))
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        self.assertEqual(saldo(self.cta()['subcontrato_garantia'].codigo), D('-35.40'))
        r = ej.reporte_partidas(self.obra)
        self.assertEqual(next(f for f in r['filas'] if not f['titulo'] and f['partida'].pk == self.a.pk)['sub'],
                         D('600.00'))
        self.assertEqual(self.client.get(reverse('obras:subcontrato', args=[sc.pk])).status_code, 200)


class StockAntesDeComprarTest(EjecucionBase):
    def setUp(self):
        super().setUp()
        self.principal = Almacen.objects.create(codigo='PRI', nombre='Almacén principal', es_principal=True)
        self.cemento = Producto.objects.create(codigo='CEM', nombre='Cemento', clase='SUMINISTRO', unidad='BG')
        self.cemento.mover_stock(D('50'), 'Saldo inicial', costo=D('30'), almacen=self.principal)

    def test_disponibilidad_justificacion_y_traslado(self):
        from compras import solped, stock
        from core.models import ParametroTributario
        disp = stock.disponibilidad({self.cemento.pk})[self.cemento.pk]
        self.assertEqual([(f['almacen'].pk, f['disponible']) for f in disp], [(self.principal.pk, D('50'))])
        sp = solped.crear({'centro_costo': self.obra.centro_costo, 'tipo': 'MATERIAL', 'motivo': 'Concreto'},
                          [{'producto': self.cemento, 'cantidad': D('30'), 'precio_estimado': D('30')}], self.user,
                          origen='OBRA')
        sp.estado = 'LIBERADA'
        sp.save(update_fields=['estado'])
        linea = sp.lineas.get()
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100070970', nombre='UNICON')
        with self.assertRaisesMessage(solped.ErrorSolped, 'stock disponible en otros almacenes'):
            solped.a_orden_compra([linea.pk], self.user, proveedor=prov, justificaciones={})
        solped.a_orden_compra([linea.pk], self.user, proveedor=prov,
                              justificaciones={linea.pk: ('URGENCIA', 'Se necesita hoy')})
        linea.refresh_from_db()
        self.assertEqual((linea.motivo_stock, linea.disponible_al_pedir), ('URGENCIA', D('50')))
        # bloquear: la empresa no permite comprar habiendo stock
        ParametroTributario.objects.create(clave='STOCK_CONTROL_COMPRA', desde=HOY - timedelta(days=1), valor=D('2'))
        sp2 = solped.crear({'centro_costo': self.obra.centro_costo, 'tipo': 'MATERIAL', 'motivo': 'Más'},
                           [{'producto': self.cemento, 'cantidad': D('10')}], self.user)
        sp2.estado = 'LIBERADA'
        sp2.save(update_fields=['estado'])
        with self.assertRaisesMessage(solped.ErrorSolped, 'atiéndalas por traslado'):
            solped.a_orden_compra([sp2.lineas.get().pk], self.user, proveedor=prov,
                                  justificaciones={sp2.lineas.get().pk: ('URGENCIA', '')})
        # atender por traslado: aprueba el origen, sale a tránsito y la obra lo recibe
        l2 = sp2.lineas.get()
        t = stock.solicitar_traslado(self.cemento, D('10'), self.principal, self.obra.almacen, self.user, linea=l2,
                                     centro_costo=self.obra.centro_costo)
        self.assertEqual(stock.disponibilidad({self.cemento.pk})[self.cemento.pk][0]['disponible'], D('40'))
        stock.aprobar_traslado(t, self.user)
        self.assertEqual(self.cemento.stock_en(self.principal), D('40'))
        stock.recibir_traslado(t, self.user)
        self.assertEqual(self.cemento.stock_en(self.obra.almacen), D('10'))
        l2.refresh_from_db()
        self.assertTrue(l2.cerrada)
        self.assertEqual(self.client.get(reverse('compras:traslados')).status_code, 200)
        self.assertEqual(self.client.get(reverse('compras:traslado_nuevo') + f'?linea={linea.pk}').status_code, 200)
        self.assertEqual(self.client.get(reverse('compras:solped_bandeja')).status_code, 200)
        self.assertEqual(self.client.get(reverse('solicitudes:detalle', args=[sp.pk])).status_code, 200)

    def test_reserva_de_la_meta_y_traslado_rechazado_vuelve(self):
        from compras import stock
        from obras.models import RecursoAPU, Recurso
        meta = Presupuesto.objects.create(obra=self.obra, tipo='META')
        x = Partida.objects.create(presupuesto=meta, item='01', descripcion='Concreto', unidad='m3', metrado=D('10'))
        r = Recurso.objects.create(nombre='Cemento', unidad='bls', tipo='MAT', producto=self.cemento, precio=D('30'))
        RecursoAPU.objects.create(partida=x, recurso=r, cantidad=D('2'))
        calculos.recalcular(meta)
        t = stock.solicitar_traslado(self.cemento, D('25'), self.principal, self.obra.almacen, self.user)
        stock.aprobar_traslado(t, self.user)
        stock.recibir_traslado(t, self.user)
        # la meta de la obra aún consume 20: de sus 25 solo 5 están disponibles para otros
        fila = next(f for f in stock.disponibilidad({self.cemento.pk})[self.cemento.pk]
                    if f['almacen'].pk == self.obra.almacen_id)
        self.assertEqual((fila['meta'], fila['disponible']), (D('20.0000'), D('5.0000')))
        t2 = stock.solicitar_traslado(self.cemento, D('5'), self.obra.almacen, self.principal, self.user)
        stock.aprobar_traslado(t2, self.user)
        with self.assertRaisesMessage(stock.ErrorStock, 'por qué se rechaza'):
            stock.recibir_traslado(t2, self.user, aceptar=False)
        stock.recibir_traslado(t2, self.user, aceptar=False, motivo='Llegó húmedo')
        self.assertEqual(self.cemento.stock_en(self.obra.almacen), D('25'))
        t2.refresh_from_db()
        self.assertEqual(t2.estado, 'DEVUELTA')
        c = self.client.get(reverse('obras:control', args=[meta.pk]))
        self.assertContains(c, 'Otros almacenes')


class RecepcionEnAlmacenDeObraTest(EjecucionBase):
    def test_orden_de_la_obra_propone_su_almacen(self):
        """OBR-15: la recepción de una orden con el centro de costo de la obra propone su almacén de obra."""
        from compras.models import OrdenCompra, OrdenCompraItem
        self.assertEqual(Obra.de_centro(self.obra.centro_costo_id).pk, self.obra.pk)
        self.assertIsNone(Obra.de_centro(None))
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100070970', nombre='UNICON')
        cemento = Producto.objects.create(codigo='CEM', nombre='Cemento', clase='SUMINISTRO', unidad='BG')
        oc = OrdenCompra.objects.create(numero='OC01-00000099', tercero=prov, centro_costo=self.obra.centro_costo,
                                        estado='APROBADO')
        OrdenCompraItem.objects.create(documento=oc, producto=cemento, descripcion='Cemento', cantidad=D('10'),
                                       precio_unitario=D('30'))
        r = self.client.get(reverse('inventario:nueva') + f'?tipo=REC_COMPRA&oc={oc.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['form'].initial.get('almacen_destino'), self.obra.almacen_id)
        self.assertEqual(StockAlmacen.objects.filter(almacen=self.obra.almacen).count(), 0)
