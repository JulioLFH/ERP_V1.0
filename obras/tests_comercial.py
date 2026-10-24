"""Checklist v1.75.0 CRM-01 (CRM para constructoras: la oportunidad es una obra en licitación con su proceso,
cronograma, requisitos, oferta y resultado) e INM-01 (proyecto inmobiliario: unidades, separación y venta con
cronograma, cobro con comprobante de anticipo, entrega con el costo de cada unidad y su contabilidad)."""
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contabilidad.centralizar import Borrador, centralizar_periodo
from contabilidad.models import Asiento, AsientoLinea, CentroCosto, CuentaContable, CuentaDefecto
from core.alertas import calcular
from core.models import Tercero
from core.modulos import GRUPOS
from finanzas.models import Cuenta
from ventas.models import Oportunidad

from . import calculos
from . import inmobiliario as inm
from . import licitaciones as lic_srv
from .models import HitoLicitacion, Licitacion, Obra, Partida, Presupuesto, RequisitoPostor, Unidad, VentaInmueble

HOY = timezone.localdate()
P = HOY.strftime('%Y%m')


def saldo(codigo):
    agg = AsientoLinea.objects.filter(cuenta__codigo=codigo).aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or D('0')) - (agg['h'] or D('0'))


def gasto(centro, monto, codigo='6361'):
    """Gasto manual de la obra (63 servicios con su centro de costo contra 4212)."""
    cuentas = {c: CuentaContable.objects.filter(codigo=c).first() or CuentaContable.objects.create(
        codigo=c, nombre=c, imputable=True) for c in (codigo, '4212')}
    b = Borrador(Asiento(fecha=HOY, origen='MANUAL', glosa='Gasto de obra'))
    b.add(cuentas[codigo], debe=D(monto), centro_costo=centro)
    b.add(cuentas['4212'], haber=D(monto))
    b.agregar_destinos()
    return b.grabar()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.entidad = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20131370645',
                                             nombre='MUNICIPALIDAD PROVINCIAL')
        cls.persona = Tercero.objects.create(tipo='CLIENTE', tipo_doc='1', numero_doc='45678912', nombre='ANA QUISPE')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)


class LicitacionTest(Base):
    def oferta(self, obra, total_cd='100000'):
        p = Presupuesto.objects.create(obra=obra, tipo='OFERTA', gastos_generales_pct=D('10'), utilidad_pct=D('10'))
        Partida.objects.create(presupuesto=p, item='01', descripcion='Obras provisionales', unidad='glb',
                               metrado=D('1'), precio_fijo=True, precio=D(total_cd))
        calculos.recalcular(p)
        p.refresh_from_db()
        return p

    def test_crear_ganar_copia_la_oferta_y_mueve_el_centro_a_obras(self):
        lic = lic_srv.crear('Puente Chilina', self.entidad, self.user, tipo_obra='PUBLICA', procedimiento='LP',
                            numero='LP-SM-3-2026-MPA', valor_referencial=D('150000'))
        obra = lic.obra
        self.assertEqual((obra.estado, obra.tipo), ('LICITACION', 'PUBLICA'))
        centro = CentroCosto.objects.get(pk=obra.centro_costo_id)
        self.assertEqual((centro.padre.codigo, centro.tipo), ('PROPUESTAS', 'VENTAS'))
        o = lic.oportunidad
        self.assertEqual((o.etapa, o.monto, o.tercero), ('PROPUESTA', D('150000'), self.entidad))
        oferta = self.oferta(obra)
        self.assertEqual(oferta.total, D('141600.00'))  # 100 000 + 10 % + 10 % + IGV
        lic_srv.sincronizar_oportunidad(lic)
        o.refresh_from_db()
        self.assertEqual(o.monto, D('141600.00'))
        self.assertEqual(lic.pct_referencial, D('94.40'))
        lic_srv.ganar(lic)
        obra.refresh_from_db()
        o.refresh_from_db()
        centro.refresh_from_db()
        self.assertEqual((obra.estado, obra.monto_contratado), ('ADJUDICADA', D('141600.00')))
        self.assertTrue(obra.presupuestos.filter(tipo='CONTRACTUAL', total=D('141600.00')).exists())
        self.assertEqual((centro.padre.codigo, centro.tipo), ('OBRAS', 'PRODUCCION'))
        self.assertEqual(o.etapa, 'GANADA')
        with self.assertRaisesMessage(lic_srv.ErrorLicitacion, 'ya está'):
            lic_srv.ganar(lic)
        r = lic_srv.reportes()
        self.assertEqual((r['ganadas'], r['tasa'], r['total_backlog']), (1, D('100.0'), D('141600.00')))

    def test_perder_exige_motivo_y_actualiza_la_oportunidad(self):
        lic = lic_srv.crear('Colegio Tiabaya', self.entidad, self.user, valor_referencial=D('80000'))
        with self.assertRaisesMessage(lic_srv.ErrorLicitacion, 'motivo'):
            lic_srv.perder(lic, '')
        lic_srv.perder(lic, 'PRECIO', 'Quedamos segundos', 'CONSTRUCTORA SUR SAC', D('76500'))
        lic.refresh_from_db()
        lic.obra.refresh_from_db()
        lic.oportunidad.refresh_from_db()
        self.assertEqual((lic.resultado, lic.obra.estado, lic.oportunidad.etapa), ('PERDIDA', 'NO_ADJUDICADA', 'PERDIDA'))
        self.assertIn('Precio; ganó CONSTRUCTORA SUR SAC por S/ 76,500.00', lic.oportunidad.motivo_perdida)
        self.assertEqual(lic_srv.reportes()['motivos'], [('Precio', 1)])

    def test_convertir_oportunidad_del_crm_y_bloqueo_de_etapa(self):
        o = Oportunidad.objects.create(nombre='Edificio corporativo', tercero=self.entidad, etapa='CALIFICADO',
                                       monto=D('2500000'))
        r = self.client.get(reverse('crm:oportunidad', args=[o.pk]))
        self.assertContains(r, 'Convertir en obra / licitación')
        r = self.client.post(reverse('obras:desde_oportunidad', args=[o.pk]))
        lic = Licitacion.objects.get(oportunidad=o)
        self.assertRedirects(r, reverse('obras:licitacion', args=[lic.obra_id]))
        o.refresh_from_db()
        self.assertEqual((o.etapa, lic.valor_referencial, lic.obra.nombre), ('PROPUESTA', D('2500000'),
                                                                             'Edificio corporativo'))
        # otra vez: lleva a la misma licitación (no duplica)
        self.client.post(reverse('obras:desde_oportunidad', args=[o.pk]))
        self.assertEqual(Licitacion.objects.filter(oportunidad=o).count(), 1)
        # el embudo no la marca como ganada: el resultado se registra en la licitación
        r = self.client.post(reverse('crm:etapa', args=[o.pk]), {'etapa': 'GANADA'})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()['url'], reverse('obras:licitacion', args=[lic.obra_id]))
        r = self.client.post(reverse('crm:oportunidad', args=[o.pk]), {
            'accion': 'datos', 'nombre': o.nombre, 'tercero': self.entidad.pk, 'etapa': 'GANADA', 'monto': '1'})
        self.assertContains(r, 'registre la buena pro')
        o.refresh_from_db()
        self.assertEqual(o.etapa, 'PROPUESTA')
        self.assertContains(self.client.get(reverse('crm:oportunidad', args=[o.pk])), 'Licitación OB')

    def test_pantalla_cronograma_requisitos_y_alertas(self):
        lic = lic_srv.crear('Hospital Camaná', self.entidad, self.user, procedimiento='CP',
                            valor_referencial=D('9000000'))
        url = reverse('obras:licitacion', args=[lic.obra_id])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'accion': 'hito', 'tipo': 'PRESENTACION', 'fecha': HOY + timedelta(days=2),
                               'hora': '10:00', 'nota': 'Mesa de partes'})
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.post(url, {'accion': 'requisito', 'tipo': 'RNP', 'descripcion': 'Constancia RNP ejecutor',
                               'vence': HOY + timedelta(days=1),
                               'archivo': SimpleUploadedFile('rnp.pdf', b'%PDF-1.4 rnp', 'application/pdf')})
        h = HitoLicitacion.objects.get(licitacion=lic)
        req = RequisitoPostor.objects.get(licitacion=lic)
        from core import sustentos
        self.assertTrue(sustentos.tiene(req))
        titulos = [a['titulo'] for a in calcular(self.user)]
        self.assertIn('Plazos de licitaciones', titulos)
        self.assertIn('Requisitos del postor vencidos o por vencer', titulos)
        self.client.post(url, {'accion': 'hito_hecho', 'id': h.pk})
        self.client.post(url, {'accion': 'cumple', 'id': req.pk})
        self.client.post(url, {'accion': 'requisito_borrar', 'id': req.pk})  # con documento: no se borra
        h.refresh_from_db()
        req.refresh_from_db()
        self.assertTrue(h.hecho and req.cumple)
        r = self.client.post(url, {'accion': 'perder', 'motivo': 'EXPERIENCIA', 'ganador': 'CONSORCIO NORTE'})
        self.assertRedirects(r, url)
        self.assertContains(self.client.get(url), 'Experiencia del postor')
        self.assertContains(self.client.get(reverse('obras:licitaciones')), 'Experiencia del postor')
        self.assertEqual(self.client.get(reverse('obras:licitaciones') + '?formato=excel').status_code, 200)

    def test_obra_en_licitacion_creada_en_obras(self):
        obra = Obra.objects.create(nombre='Losa deportiva', cliente=self.entidad)
        url = reverse('obras:licitacion', args=[obra.pk])
        self.assertContains(self.client.get(url), 'Registrar la licitación')
        self.client.post(url, {'accion': 'iniciar'})
        lic = Licitacion.objects.get(obra=obra)
        self.assertEqual(lic.oportunidad.etapa, 'PROPUESTA')
        r = self.client.post(reverse('obras:licitacion_nueva'), {
            'nombre': 'Pistas y veredas', 'cliente': self.entidad.pk, 'tipo_obra': 'PUBLICA', 'procedimiento': 'AS',
            'valor_referencial': '350000', 'participacion_pct': '100'})
        nueva = Licitacion.objects.get(obra__nombre='Pistas y veredas')
        self.assertRedirects(r, reverse('obras:licitacion', args=[nueva.obra_id]))


class InmobiliarioTest(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.banco = Cuenta.objects.create(nombre='BBVA recaudadora', banco='BBVA', tipo='BANCO')
        cls.obra = Obra.objects.create(nombre='Residencial Los Pinos', tipo='INMOBILIARIA', terreno_pct=D('50'),
                                       cuenta_recaudadora=cls.banco)
        cls.d101 = Unidad.objects.create(obra=cls.obra, codigo='D-101', piso='1', area_techada=D('75'),
                                         precio_lista=D('327000'))
        cls.d102 = Unidad.objects.create(obra=cls.obra, codigo='D-102', piso='1', area_techada=D('25'),
                                         precio_lista=D('109000'))
        cls.empresa = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970',
                                             nombre='INVERSIONES AREQUIPA SAC')

    def test_igv_solo_sobre_la_construccion(self):
        self.assertEqual(inm.partir(D('109000'), D('50')), (D('50000.00'), D('9000.00'), D('50000.00')))
        self.assertEqual(inm.partir(D('5000'), D('50')), (D('2293.58'), D('412.84'), D('2293.58')))
        self.assertEqual(inm.partir(D('1000'), D('0')), (D('847.46'), D('152.54'), D('0.00')))

    def test_cronograma(self):
        filas = inm.cronograma(D('120000'), 'DIRECTO', HOY, D('2000'), D('22000'), HOY, 4)
        self.assertEqual([(c, m) for c, _, m in filas], [('SEPARACION', D('2000')), ('INICIAL', D('22000'))] +
                         [('CUOTA', D('24000.00'))] * 4)
        self.assertEqual(filas[-1][1], inm._sumar_meses(HOY, 4))
        filas = inm.cronograma(D('300000'), 'MIVIVIENDA', HOY, D('3000'), D('27000'), bono=D('37000'))
        self.assertEqual([(c, m) for c, _, m in filas], [('SEPARACION', D('3000')), ('INICIAL', D('27000')),
                                                         ('BONO', D('37000')), ('BANCO', D('233000'))])
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'superan el precio'):
            inm.cronograma(D('1000'), 'CONTADO', HOY, D('2000'))

    def test_separar_cobrar_entregar_y_contabilizar(self):
        gasto(self.obra.centro_costo, '60000')
        o = Oportunidad.objects.create(nombre='Dpto. Los Pinos', tercero=self.persona, etapa='CALIFICADO')
        v = inm.separar(self.obra, [self.d101], self.persona, D('327000'), self.user,
                        [('SEPARACION', HOY, D('5000')), ('BANCO', HOY, D('322000'))], 'HIPOTECARIO', 'BCP',
                        oportunidad=o)
        self.d101.refresh_from_db()
        o.refresh_from_db()
        self.assertEqual((v.numero[:3], self.d101.estado, o.etapa, o.monto), ('VI-', 'SEPARADA', 'NEGOCIACION',
                                                                              D('327000')))
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'No están disponibles: D-101'):
            inm.separar(self.obra, [self.d101], self.empresa, D('1'), self.user, [])
        c1, c2 = v.cuotas.all()
        cobro = inm.cobrar(c1, D('5000'), HOY, None, self.user, numero_operacion='OP-1')
        comp = cobro.comprobante
        self.assertEqual((comp.tipo_comprobante, comp.serie[0], comp.total, comp.base_imponible, comp.igv,
                          comp.inafecto), ('03', 'B', D('5000.00'), D('2293.58'), D('412.84'), D('2293.58')))
        self.assertEqual(comp.items.count(), 2)
        self.assertEqual((cobro.movimiento.cuenta, comp.saldo), (self.banco, D('0')))
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'minuta'):
            inm.entregar(v)
        inm.firmar_minuta(v)
        o.refresh_from_db()
        self.assertEqual(o.etapa, 'GANADA')
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'Falta cobrar S/ 322,000.00'):
            inm.entregar(v)
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'supera el saldo'):
            inm.cobrar(c2, D('322000.01'), HOY, None, self.user)
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'no en efectivo (bancarización)'):
            inm.cobrar(c2, D('322000'), HOY, self.banco, self.user, medio_pago='EFECTIVO', numero_operacion='X')
        inm.cobrar(c2, D('322000'), HOY, self.banco, self.user, numero_operacion='OP-2')
        v.refresh_from_db()
        inm.entregar(v, HOY)
        v.refresh_from_db()
        self.d101.refresh_from_db()
        # 327 000 con IGV solo sobre la construcción: 300 000 sin IGV; costo 60 000 × 75 / 100 m²
        self.assertEqual((v.estado, v.ingreso, v.costo, self.d101.estado),
                         ('ENTREGADA', D('300000.00'), D('45000.00'), 'ENTREGADA'))
        r = centralizar_periodo(P)
        self.assertEqual(r['errores'], [])
        a = Asiento.objects.get(origen='INMUEBLE', periodo=P)
        self.assertTrue(a.cuadrado)
        cta = CuentaDefecto.mapa()
        lineas = {(l.cuenta.codigo, l.debe, l.haber) for l in a.lineas.select_related('cuenta')}
        self.assertEqual(lineas, {
            (cta['inmuebles_proceso'].codigo, D('60000.00'), D('0')),
            (cta['inmuebles_variacion'].codigo, D('0'), D('60000.00')),
            (cta['mov_ANTICIPO_INGRESO'].codigo, D('300000.00'), D('0')),
            (cta['inmuebles_venta'].codigo, D('0'), D('300000.00')),
            (cta['inmuebles_costo'].codigo, D('45000.00'), D('0')),
            (cta['inmuebles_proceso'].codigo, D('0'), D('45000.00'))})
        self.assertEqual(cta['inmuebles_proceso'].codigo, '2341')
        self.assertEqual(saldo('2341'), D('15000.00'))  # la unidad D-102 sigue en construcción
        self.assertEqual(saldo(cta['mov_ANTICIPO_INGRESO'].codigo), D('0'))  # los anticipos pasaron a la venta
        self.assertEqual(saldo('40111'), D('-27000.00'))  # IGV solo de la construcción
        tot = AsientoLinea.objects.aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual(tot['d'], tot['h'])
        # el tablero reparte el costo por área techada
        t = inm.tablero(self.obra)
        self.assertEqual([(u.codigo, u.costo) for u in t['unidades']], [('D-101', D('45000.00')),
                                                                         ('D-102', D('15000.00'))])
        self.assertEqual(t['costo_m2'], D('600.00'))

    def test_terreno_cargado_directo_a_inmuebles_en_construccion(self):
        proceso = CuentaDefecto.mapa()['inmuebles_proceso']
        self.assertEqual(proceso.centro_costo_regla, 'OPCIONAL')  # lleva el centro de costo del proyecto
        gasto(self.obra.centro_costo, '50000')
        b = Borrador(Asiento(fecha=HOY, origen='MANUAL', glosa='Compra del terreno'))
        b.add(proceso, debe=D('200000'), centro_costo=self.obra.centro_costo)
        b.add(CuentaContable.objects.get(codigo='4212'), haber=D('200000'))
        b.grabar()
        self.assertEqual(inm.costo_proyecto(self.obra), D('250000'))
        self.assertEqual(inm.costo_unidades(self.obra, [self.d101]), D('187500.00'))
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        self.assertEqual(saldo(proceso.codigo), D('250000.00'))  # 200 000 directo + 50 000 activado, sin duplicar

    def test_factura_para_empresa_y_anulacion(self):
        v = inm.separar(self.obra, [self.d102], self.empresa, D('109000'), self.user,
                        [('SEPARACION', HOY, D('1000')), ('CUOTA', HOY, D('108000'))], 'CONTADO')
        cobro = inm.cobrar(v.cuotas.first(), D('1000'), HOY, None, self.user)
        self.assertEqual((cobro.comprobante.tipo_comprobante, cobro.comprobante.serie[0]), ('01', 'F'))
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'registre el desistimiento'):
            inm.anular(v, 'Desistimiento')
        v2 = inm.separar(self.obra, [self.d101], self.persona, D('327000'), self.user, [])
        with self.assertRaisesMessage(inm.ErrorInmobiliario, 'motivo'):
            inm.anular(v2, '')
        inm.anular(v2, 'Desistimiento del comprador')
        self.d101.refresh_from_db()
        self.assertEqual(self.d101.estado, 'DISPONIBLE')

    def test_pantallas_carga_rapida_cobranza_y_alertas(self):
        url = reverse('obras:unidades', args=[self.obra.pk])
        self.assertContains(self.client.get(url), 'D-101')
        self.client.post(url, {'accion': 'carga', 'lineas': 'E-01\tEstacionamiento\tS1\t12.5\t0\t0\t25000\n'
                                                             'DP-01;Depósito;S1;4;0;0;8,000\nX-1;Castillo;1;1;0;0;1'})
        self.assertEqual(sorted(self.obra.unidades.values_list('codigo', flat=True)),
                         ['D-101', 'D-102', 'DP-01', 'E-01'])
        self.assertEqual(Unidad.objects.get(codigo='DP-01').precio_lista, D('8000'))
        r = self.client.post(reverse('obras:separar', args=[self.obra.pk]), {
            'unidades': [self.d102.pk, Unidad.objects.get(codigo='E-01').pk], 'cliente': self.persona.pk,
            'financiamiento': 'DIRECTO', 'fecha': HOY - timedelta(days=40), 'separacion': '1000',
            'inicial': '13000', 'fecha_inicial': HOY - timedelta(days=35), 'cuotas': '12'})
        v = VentaInmueble.objects.get(cliente=self.persona)
        self.assertRedirects(r, reverse('obras:venta_inmueble', args=[v.pk]))
        self.assertEqual((v.precio, v.cuotas.count()), (D('134000'), 14))
        self.assertContains(self.client.get(reverse('obras:venta_inmueble', args=[v.pk])), 'Cronograma de pagos')
        cuota = v.cuotas.first()
        self.assertContains(self.client.get(reverse('obras:inm_cobrar', args=[cuota.pk])), 'boleta de anticipo')
        r = self.client.post(reverse('obras:inm_cobrar', args=[cuota.pk]), {
            'monto': '1000', 'fecha': HOY, 'cuenta': self.banco.pk, 'medio_pago': 'EFECTIVO'})
        self.assertRedirects(r, reverse('obras:venta_inmueble', args=[v.pk]))
        self.assertEqual(v.cobrado, D('1000'))
        self.assertContains(self.client.get(reverse('obras:inm_cobranza')), 'Cuota inicial')
        self.assertIn('Cuotas inmobiliarias vencidas', [a['titulo'] for a in calcular(self.user)])
        self.assertEqual(self.client.get(url + '?formato=excel').status_code, 200)
        # cobrar exige Tesorería: un usuario solo de Obras no registra el cobro
        ingeniero = User.objects.create_user('ing', password='x')
        ingeniero.groups.add(Group.objects.get_or_create(name=GRUPOS['obras'])[0])
        self.client.force_login(ingeniero)
        r = self.client.post(reverse('obras:inm_cobrar', args=[cuota.pk]), {'monto': '1', 'fecha': HOY})
        self.assertRedirects(r, reverse('obras:venta_inmueble', args=[v.pk]))
        self.assertEqual(v.cobrado, D('1000'))
