"""Checklist v1.75.0 (18h10) INM-02 a INM-08 y OBR-12: terminación del proyecto (23 → 21), intereses activados
(NIC 23), costo mixto y áreas comunes, exoneración de vivienda, arras de retractación, desistimiento con nota de crédito
y penalidad, garantías postventa, bancarización, registro de operaciones, reportes y cierre de la obra."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento, CuentaDefecto
from core.models import Almacen, ParametroTributario, Producto
from finanzas.models import CuotaPrestamo, Movimiento, Prestamo

from . import inmobiliario as inm
from .cierre import ErrorCierre, sobrantes
from .models import Obra, Unidad
from .tests_comercial import HOY, P, Base, gasto, saldo


class Fase2Test(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from finanzas.models import Cuenta
        cls.banco = Cuenta.objects.create(nombre='BBVA recaudadora', banco='BBVA', tipo='BANCO', saldo_inicial=D('100000'))
        cls.obra = Obra.objects.create(nombre='Torre Mar', tipo='INMOBILIARIA', terreno_pct=D('50'),
                                       cuenta_recaudadora=cls.banco)
        cls.d1 = Unidad.objects.create(obra=cls.obra, codigo='D-101', tipo='DEPARTAMENTO', area_techada=D('80'),
                                       precio_lista=D('300000'))
        cls.d2 = Unidad.objects.create(obra=cls.obra, codigo='D-102', tipo='DEPARTAMENTO', area_techada=D('20'),
                                       precio_lista=D('100000'))
        cls.comun = Unidad.objects.create(obra=cls.obra, codigo='LOBBY', tipo='COMUN', area_techada=D('50'))

    def cta(self):
        return CuentaDefecto.mapa()

    def test_mixto_y_areas_comunes(self):
        p = inm.pesos(self.obra)
        self.assertEqual(set(p), {self.d1.pk, self.d2.pk})  # el lobby no lleva costo propio
        self.assertEqual(p[self.d1.pk], D('80'))
        Obra.objects.filter(pk=self.obra.pk).update(prorrateo='MIXTO', prorrateo_area_pct=D('50'))
        self.obra.refresh_from_db()
        p = inm.pesos(self.obra)
        self.assertEqual(p[self.d1.pk], D('0.5') * D('0.8') + D('0.5') * D('0.75'))  # 0.775
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'áreas comunes no se venden'):
            inm.separar(self.obra, [self.comun], self.persona, D('1'), self.user, [])

    def test_terminacion_intereses_nic23_y_entrega_desde_terminados(self):
        gasto(self.obra.centro_costo, '100000')
        # préstamo del proyecto: sus intereses llevan el centro de la obra y se activan
        pr = Prestamo.objects.create(entidad=self.entidad, descripcion='Préstamo puente', monto=D('50000'),
                                     tasa_anual=D('12'), plazo=1, primera_cuota=HOY, cuenta=self.banco,
                                     centro_costo=self.obra.centro_costo)
        c = CuotaPrestamo.objects.create(prestamo=pr, numero=1, fecha=HOY, capital=D('50000'), interes=D('4000'),
                                         saldo=D('0'))
        Movimiento.objects.create(cuenta=self.banco, fecha=HOY, tipo='EGRESO', concepto='PRESTAMO', monto=D('54000'),
                                  prestamo=pr, cuota=c, tercero=self.entidad)
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        cta = self.cta()
        self.assertEqual(saldo(cta['inmuebles_proceso'].codigo), D('104000.00'))  # 100 000 + intereses 4 000
        # conformidad de obra: el saldo pasa a terminados; lo posterior no se activa
        inm.terminar(self.obra, HOY)
        self.obra.refresh_from_db()
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'ya se terminó'):
            inm.terminar(self.obra, HOY)
        mañana = HOY + timedelta(days=1)
        if mañana.month != HOY.month:  # la prueba necesita dos días del mismo mes
            return
        v = inm.separar(self.obra, [self.d2], self.entidad, D('109000'), self.user, [('CUOTA', HOY, D('109000'))])
        inm.cobrar(v.cuotas.first(), D('109000'), HOY, None, self.user, numero_operacion='OP-9')
        inm.firmar_minuta(v, HOY)
        inm.entregar(v, mañana)
        v.refresh_from_db()
        self.assertEqual(v.costo, D('20800.00'))  # 104 000 × 20 / 100 (sin el lobby)
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        self.assertEqual(saldo(cta['inmuebles_proceso'].codigo), D('0'))
        self.assertEqual(saldo(cta['inmuebles_terminados'].codigo), D('83200.00'))
        self.assertTrue(Asiento.objects.get(origen='INMUEBLE', periodo=P).cuadrado)

    def test_exoneracion_de_vivienda(self):
        # el valor registrado desde el 1 de enero manda sobre el de por defecto de esa fecha
        ParametroTributario.objects.create(clave='INM_EXONERACION_UIT', valor=D('35'), desde=date(HOY.year, 1, 1))
        ParametroTributario.objects.create(clave='UIT', valor=D('5500'), desde=date(HOY.year, 1, 1))
        v = inm.separar(self.obra, [self.d2], self.persona, D('100000'), self.user, [])
        self.assertTrue(v.exonerada_igv)  # 100 000 <= 35 × 5 500
        cobro = inm.cobrar(v.cuotas.first(), D('1000'), HOY, None, self.user)
        comp = cobro.comprobante
        self.assertEqual((comp.igv, comp.exonerado, comp.total), (D('0'), D('1000.00'), D('1000.00')))
        v2 = inm.separar(self.obra, [self.d1], self.persona, D('300000'), self.user, [])
        self.assertFalse(v2.exonerada_igv)  # supera el límite

    def test_arras_minuta_y_desistimiento_con_penalidad(self):
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'arras de retractación sin IGV llegan hasta S/ 9,000.00'):
            inm.separar(self.obra, [self.d1], self.persona, D('300000'), self.user,
                        inm.cronograma(D('300000'), 'CONTADO', HOY, D('10000'), arras=True))
        v = inm.separar(self.obra, [self.d1], self.persona, D('300000'), self.user,
                        inm.cronograma(D('300000'), 'DIRECTO', HOY, D('9000'), D('51000'), HOY, 2, arras=True))
        arras, inicial = v.cuotas.all()[:2]
        self.assertEqual(arras.concepto, 'ARRAS')
        cobro = inm.cobrar(arras, D('9000'), HOY, None, self.user, numero_operacion='A-1')
        self.assertIsNone(cobro.comprobante)  # sin IGV ni comprobante
        self.assertEqual(cobro.movimiento.cuenta_contable, self.cta()['inmuebles_arras'])
        inm.firmar_minuta(v, HOY, self.user)
        cobro.refresh_from_db()
        self.assertIsNotNone(cobro.comprobante)  # con la minuta se emite y se aplica
        self.assertEqual((cobro.comprobante.total, cobro.comprobante.saldo), (D('9000.00'), D('0')))
        inm.cobrar(inicial, D('51000'), HOY, None, self.user, numero_operacion='A-2')
        # desistimiento: retiene 15 000; devuelve 45 000 con nota de crédito
        inm.desistir(v, 'El comprador desiste', D('15000'), HOY, self.banco, self.user, numero_operacion='D-1')
        v.refresh_from_db()
        self.d1.refresh_from_db()
        self.assertEqual((v.estado, v.penalidad, self.d1.estado), ('ANULADA', D('15000.00'), 'DISPONIBLE'))
        devoluciones = list(v.devoluciones.all())
        self.assertEqual(sum(d.monto for d in devoluciones), D('45000.00'))
        self.assertTrue(all(d.nota_credito and d.nota_credito.tipo_comprobante == '07' for d in devoluciones))
        for d in devoluciones:  # el comprobante devuelto queda sin saldo
            self.assertEqual(d.nota_credito.doc_referencia.saldo, D('0'))
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        cta = self.cta()
        self.assertEqual(saldo(cta['mov_ANTICIPO_INGRESO'].codigo), D('0'))  # lo retenido pasó a otros ingresos
        self.assertEqual(saldo(cta['inmuebles_arras'].codigo), D('0'))
        self.assertEqual(saldo(cta['cliente'].codigo), D('0'))
        # la penalidad va sin IGV adicional: el ingreso es la base de lo retenido (15 000 con su IGV ya declarado)
        self.assertEqual(-saldo(cta['inmuebles_penalidad'].codigo), D('13761.47'))

    def test_desistimiento_solo_con_arras(self):
        v = inm.separar(self.obra, [self.d2], self.persona, D('100000'), self.user,
                        inm.cronograma(D('100000'), 'CONTADO', HOY, D('3000'), arras=True))
        inm.cobrar(v.cuotas.first(), D('3000'), HOY, None, self.user, numero_operacion='A-3')
        inm.desistir(v, 'Pierde las arras', D('3000'), HOY, None, self.user)
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        self.assertEqual(saldo(self.cta()['inmuebles_arras'].codigo), D('0'))
        self.assertEqual(-saldo(self.cta()['inmuebles_penalidad'].codigo), D('3000.00'))

    def test_garantias_bancarizacion_y_registro_de_operaciones(self):
        ParametroTributario.objects.create(clave='INM_GARANTIA_PCT', valor=D('1'), desde=date(2020, 1, 1))
        gasto(self.obra.centro_costo, '50000')
        v = inm.separar(self.obra, [self.d1], self.persona, D('327000'), self.user, [('CUOTA', HOY, D('327000'))])
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'N° de operación'):
            inm.cobrar(v.cuotas.first(), D('327000'), HOY, None, self.user)
        inm.cobrar(v.cuotas.first(), D('327000'), HOY, None, self.user, numero_operacion='OP-1')
        r = inm.registro_operaciones(HOY.replace(day=1), HOY)
        self.assertEqual(len(r['individuales']), 1)  # S/ 327 000 > US$ 10 000
        self.assertIn(v, inm.fichas_incompletas())
        inm.firmar_minuta(v, HOY)
        inm.entregar(v, HOY)
        v.refresh_from_db()
        self.assertEqual(v.provision_garantia, D('3000.00'))  # 1 % de 300 000
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        self.assertEqual(-saldo(self.cta()['inmuebles_garantia'].codigo), D('3000.00'))
        r = self.client.post(reverse('obras:venta_inmueble', args=[v.pk]), {
            'accion': 'ficha', 'ocupacion': 'Médica', 'origen_fondos': 'CREDITO', 'declaracion_fondos': 'on'})
        v.refresh_from_db()
        self.assertTrue(v.ficha_completa)
        self.assertNotIn(v, inm.fichas_incompletas())

    def test_reportes_inmobiliarios(self):
        v = inm.separar(self.obra, [self.d1], self.persona, D('300000'), self.user,
                        [('SEPARACION', HOY - timedelta(days=5), D('5000')), ('CUOTA', HOY, D('295000'))])
        inm.cobrar(v.cuotas.first(), D('5000'), HOY, None, self.user, numero_operacion='OP-5')
        url = reverse('obras:inm_reportes')
        for clave, _ in __import__('obras.views_comercial', fromlist=['x']).REPORTES_INMOBILIARIOS:
            r = self.client.get(f'{url}?reporte={clave}&obra={self.obra.pk}')
            self.assertEqual(r.status_code, 200, clave)
            self.assertEqual(self.client.get(f'{url}?reporte={clave}&formato=excel').status_code, 200, clave)
        filas = inm.anticipos_por_aplicar(self.obra)
        self.assertEqual((filas[0]['cobrado'], filas[0]['anticipo']), (D('5000'), D('4587.16')))
        self.assertEqual(inm.cobranza_por_comprador(self.obra)[0]['saldo'], D('295000'))
        flujo = inm.flujo_proyecto(self.obra)
        self.assertEqual(sum(f['ingresos'] for f in flujo), D('5000'))
        self.assertEqual(len(inm.unidades_al(HOY)), 2)  # sin el lobby
        r = self.client.get(reverse('obras:unidades', args=[self.obra.pk]) + '?formato=inventario')
        self.assertEqual(r.status_code, 200)


class CierreObraTest(Base):
    def test_liquidar_exige_almacen_en_cero_y_bloquea(self):
        obra = Obra.objects.create(nombre='Colegio', estado='EJECUCION')
        cemento = Producto.objects.create(nombre='Cemento', clase='SUMINISTRO', unidad='BG')
        cemento.mover_stock(D('10'), 'Inicial', costo=D('28'), almacen=obra.almacen, origen='AJUSTE',
                            concepto='INICIAL')
        self.assertEqual(sobrantes(obra)[0][3], D('280.00'))
        url = reverse('obras:obra_editar', args=[obra.pk])
        datos = {'nombre': obra.nombre, 'tipo': 'PRIVADA', 'sistema': 'SUMA_ALZADA', 'moneda': 'PEN',
                 'monto_contratado': '0', 'plazo_dias': '0', 'estado': 'LIQUIDADA', 'gastos_generales_pct': '10',
                 'utilidad_pct': '10', 'adelanto_directo_pct': '0', 'adelanto_materiales_pct': '0',
                 'retencion_garantia_pct': '0', 'terreno_pct': '50', 'prorrateo': 'AREA', 'prorrateo_area_pct': '50',
                 'area_m2': '0'}
        r = self.client.post(url, datos)
        self.assertContains(r, 'sobrantes por S/ 280.00')
        self.assertEqual(self.client.get(reverse('obras:sobrantes')).status_code, 200)
        principal = Almacen.objects.create(codigo='ALM-C', nombre='Almacén central', es_principal=True)
        cemento.mover_stock(D('-10'), 'Traslado', almacen=obra.almacen, origen='OPERACION', concepto='TRASLADO')
        cemento.mover_stock(D('10'), 'Traslado', costo=D('28'), almacen=principal, origen='OPERACION',
                            concepto='TRASLADO')
        self.assertEqual(sobrantes(obra), [])
        r = self.client.post(url, datos)
        self.assertRedirects(r, reverse('obras:obra', args=[obra.pk]),
                             msg_prefix=str(r.context['form'].errors) if r.status_code == 200 else '')
        obra.refresh_from_db()
        obra.almacen.refresh_from_db()
        obra.centro_costo.refresh_from_db()
        self.assertTrue(obra.almacen.cerrado)
        self.assertFalse(obra.centro_costo.activo)
        from inventario.cierre import KardexCerrado
        with self.assertRaisesMessage(KardexCerrado, 'está cerrado'):
            cemento.mover_stock(D('1'), 'Ingreso', costo=D('28'), almacen=obra.almacen, origen='COMPRA')
        cemento.mover_stock(D('1'), 'Ajuste', costo=D('28'), almacen=obra.almacen, origen='AJUSTE',
                            concepto='SOBRANTE')  # los ajustes sí
        # reabrir exige motivo (y el permiso, que el administrador tiene)
        datos['estado'] = 'EJECUCION'
        self.assertContains(self.client.post(url, datos), 'motivo para reabrir')
        datos['motivo_cambio'] = 'Falta una partida'
        self.client.post(url, datos)
        obra.almacen.refresh_from_db()
        self.assertFalse(obra.almacen.cerrado)
        with self.assertRaisesMessage(ErrorCierre, 'permiso'):
            from django.contrib.auth.models import User
            from .cierre import reabrir
            reabrir(obra, User.objects.create_user('sin_permiso'), 'x')
