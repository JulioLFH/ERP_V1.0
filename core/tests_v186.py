"""v1.86: gestión del contrato y del proyecto (HR-01 a HR-20)."""
from datetime import timedelta
from decimal import Decimal as D

from django.core.cache import cache
from django.urls import reverse

from obras import gestion as g
from obras.models import (AvancePartida, EventoContrato, Obra, ParteEquipo, Partida, Presupuesto, ProgramacionPartida,
                          Recurso, RecursoAPU, Subcontrato)
from obras.tests_comercial import HOY
from obras.tests_ejecucion import EjecucionBase

from .tests_v181 import usuario


def per(fecha):
    return f'{fecha:%Y%m}'


def mes_siguiente(fecha):
    return (fecha.replace(day=28) + timedelta(days=4)).replace(day=1)


class Base186(EjecucionBase):
    def avance_y_costo(self):
        """Excavación al 50 % (EV 2,500) con S/ 4,000 de equipo: costo sobre lo esperado."""
        AvancePartida.objects.create(obra=self.obra, partida=self.a, fecha=HOY, metrado=D('50'))
        ParteEquipo.objects.create(obra=self.obra, fecha=HOY, descripcion='Excavadora', propiedad='ALQUILADO',
                                   partida=self.a, horas=D('40'), tarifa=D('100'))
        ProgramacionPartida.objects.create(partida=self.a, periodo=per(HOY), metrado=D('100'))


class ControlTest(Base186):
    def test_control_por_partida_alertas_y_margen(self):
        """HR-07, HR-12 y HR-14."""
        self.avance_y_costo()
        c = g.control_partidas(self.obra)
        fa = next(f for f in c['filas'] if f['partida'].pk == self.a.pk)
        self.assertEqual((fa['ev'], fa['ac'], fa['eac']), (D('2500.00'), D('4000.00'), D('8000.00')))
        self.assertTrue(fa['alerta'])
        self.assertEqual(c['totales']['eac'], D('18000.00'))
        self.assertEqual(c['margen_pct'], D('-8.3'))  # 18,000 − 18,000 − 1,500 de gastos generales
        self.assertTrue(c['bajo_meta'])
        costo, margen = g.alertas_proyectos()
        self.assertEqual(([o.pk for o, _ in costo], [o.pk for o, _, _ in margen]), ([self.obra.pk], [self.obra.pk]))
        from core.alertas import calcular
        titulos = [a['titulo'] for a in calcular(self.user)]
        self.assertIn('Margen proyectado bajo la meta', titulos)
        self.assertIn('Partidas con costo sobre lo esperado', titulos)
        self.assertContains(self.client.get(reverse('obras:proyeccion', args=[self.obra.pk])), 'por debajo de la meta')
        self.assertContains(self.client.get(reverse('obras:ejecucion', args=[self.obra.pk])),
                            'Margen proyectado al cierre')
        Obra.objects.filter(pk=self.obra.pk).update(margen_meta_pct=D('-20'))  # la meta propia de la obra manda
        self.obra.refresh_from_db()
        self.assertFalse(g.control_partidas(self.obra)['bajo_meta'])

    def test_valor_ganado(self):
        """HR-05."""
        self.avance_y_costo()
        r = g.valor_ganado(self.obra)
        self.assertEqual((r['bac'], r['pv'], r['ev'], r['ac']),
                         (D('15000.00'), D('5000.00'), D('2500.00'), D('4000.00')))
        self.assertEqual(r['spi'], D('0.50'))
        self.assertLess(r['cpi'], 1)
        self.assertContains(self.client.get(reverse('obras:valor_ganado', args=[self.obra.pk])), 'SPI')


class EscenariosTest(Base186):
    def test_guardar_comparar_y_volver(self):
        """HR-06."""
        ProgramacionPartida.objects.create(partida=self.a, periodo=per(HOY), metrado=D('100'))
        ProgramacionPartida.objects.create(partida=self.b, periodo=per(HOY), metrado=D('10'))
        base = g.guardar_escenario(self.obra, 'Plan base', self.user)
        ProgramacionPartida.objects.filter(partida=self.b).update(periodo=per(mes_siguiente(HOY)))
        c = g.comparar_escenarios(self.obra, base, None)
        self.assertEqual(c['meses_diferencia'], 1)
        self.assertEqual([x['partida'].pk for x in c['cambios']], [self.b.pk])
        r = self.client.post(reverse('obras:escenarios', args=[self.obra.pk]),
                             {'accion': 'restaurar', 'escenario': base.pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ProgramacionPartida.objects.get(partida=self.b).periodo, per(HOY))
        self.assertEqual(self.obra.escenarios.count(), 2)  # el vigente se guardó antes de volver
        r = self.client.get(reverse('obras:escenarios', args=[self.obra.pk]) + f'?a={base.pk}')
        self.assertContains(r, 'Partidas que cambian de mes de término')


class AdendasTest(Base186):
    def test_adenda_cambia_el_contrato_con_segregacion(self):
        """HR-03."""
        Obra.objects.filter(pk=self.obra.pk).update(monto_contratado=D('21240'), plazo_dias=120, fecha_inicio=HOY)
        r = self.client.post(reverse('obras:adendas', args=[self.obra.pk]), {
            'tipo': 'MONTO_PLAZO', 'fecha': HOY.isoformat(), 'monto': '2360', 'plazo_dias': '30',
            'motivo': 'Adicional N° 1: veredas', 'resolucion': 'RA-015-2026'})
        self.assertEqual(r.status_code, 302)
        a = self.obra.adendas.get()
        jefe = usuario('jefe_obras', ['obras'], ['obras.aprobar'])
        with self.assertRaisesMessage(g.ErrorGestion, 'Segregación'):  # hay otro usuario que puede aprobarla
            g.aprobar_adenda(a, self.user)
        self.client.force_login(jefe)
        self.client.post(reverse('obras:adendas', args=[self.obra.pk]), {'adenda': a.pk, 'accion': 'aprobar'})
        self.obra.refresh_from_db()
        self.assertEqual((self.obra.monto_contratado, self.obra.plazo_dias), (D('23600.00'), 150))
        v = g.contrato_vigente(self.obra)
        self.assertEqual((v['original'], v['plazo_original'], v['termino']), (D('21240.00'), 120,
                                                                            HOY + timedelta(days=150)))
        self.assertContains(self.client.get(reverse('obras:adendas', args=[self.obra.pk])), 'no coincide')
        g.anular_adenda(a, jefe, 'Resolución dejada sin efecto')
        self.obra.refresh_from_db()
        self.assertEqual((self.obra.monto_contratado, self.obra.plazo_dias), (D('21240.00'), 120))


class SensibilidadTest(Base186):
    def test_margen_ante_el_precio_de_los_insumos(self):
        """HR-02."""
        cemento = Recurso.objects.create(nombre='Cemento portland', unidad='bls', tipo='MAT', precio=D('25'))
        p = Presupuesto.objects.create(obra=self.obra, tipo='OFERTA', gastos_generales_pct=D('10'),
                                       utilidad_pct=D('10'))
        x = Partida.objects.create(presupuesto=p, item='01', descripcion='Concreto simple', unidad='m3',
                                   metrado=D('10'))
        RecursoAPU.objects.create(partida=x, recurso=cemento, cantidad=D('8'))
        from obras import calculos
        calculos.recalcular(p)
        p.refresh_from_db()
        r = g.sensibilidad(p, [D('10')])
        self.assertEqual((r['ingreso'], r['margen']), (D('2400.00'), D('200.00')))
        f = r['filas'][0]
        self.assertEqual((f['monto'], f['escenarios'][0]['margen'], f['quiebre']), (D('2000.00'), D('0.00'),
                                                                                   D('10.0')))
        r = self.client.get(reverse('obras:sensibilidad', args=[p.pk]) + '?variaciones=5,10')
        self.assertContains(r, 'Margen con +10 %')


class ProductividadComprasTest(Base186):
    def test_productividad_de_cuadrillas(self):
        """HR-08: 0.5 hh/m3 en el APU; la cuadrilla hizo 50 m3 en 40 hh (eficiencia 62.5 %)."""
        from obras import construccion_civil as civil
        from planillas.models import Trabajador
        operario = Recurso.objects.create(nombre='Operario', unidad='hh', tipo='MO', precio=D('25'))
        RecursoAPU.objects.create(partida=self.a, recurso=operario, cantidad=D('0.5'))
        cuadrilla = [Trabajador.objects.create(numero_doc=f'4000001{n}', apellido_paterno='QUISPE', nombres=f'LUIS {n}',
                                               fecha_ingreso=HOY - timedelta(days=30), regimen='CONSTRUCCION',
                                               categoria_civil='OPERARIO', sistema_pensiones='ONP') for n in range(4)]
        civil.registrar_tareo(self.obra, HOY, 'Excavación', [(t, self.a, D('8'), D('2'), D('0')) for t in cuadrilla],
                              self.user)
        AvancePartida.objects.create(obra=self.obra, partida=self.a, fecha=HOY, metrado=D('50'))
        r = g.productividad(self.obra)
        c = r['cuadrillas'][0]
        self.assertEqual((c['cuadrilla'], c['horas'], c['ganadas'], c['eficiencia']),
                         ('Excavación', D('40'), D('25.00'), D('62.5')))
        self.assertContains(self.client.get(reverse('obras:productividad', args=[self.obra.pk])), 'Excavación')

    def test_pronostico_de_compras(self):
        """HR-09: 10 m3 programados el mes siguiente × 8 bolsas."""
        cemento = Recurso.objects.create(nombre='Cemento portland', unidad='bls', tipo='MAT', precio=D('25'))
        RecursoAPU.objects.create(partida=self.b, recurso=cemento, cantidad=D('8'))
        sig = mes_siguiente(HOY)
        ProgramacionPartida.objects.create(partida=self.b, periodo=per(sig), metrado=D('10'))
        r = g.pronostico_compras(self.obra, 3)
        f = r['filas'][0]
        self.assertEqual((f['recurso'], f['total'], f['faltante'], f['monto']),
                         (cemento, D('80.00'), D('80.00'), D('2000.00')))
        self.assertEqual(f['pedir_desde'], max(sig - timedelta(days=g.DIAS_ANTICIPACION), HOY))
        self.assertContains(self.client.get(reverse('obras:pronostico_compras', args=[self.obra.pk])), 'Cemento')


class EquiposSubcontratosTest(Base186):
    def test_comprar_o_alquilar(self):
        """HR-10: S/ 2,000 de depreciación al mes y 100 horas al año: 240 por hora frente a 150 de alquiler."""
        from produccion.models import Equipo
        e = Equipo.objects.create(codigo='EX-02', nombre='Excavadora 320', valor_reposicion=D('120000'),
                                  tarifa_alquiler=D('150'))
        ParteEquipo.objects.create(obra=self.obra, fecha=HOY, equipo=e, propiedad='PROPIO', horas=D('100'),
                                   tarifa=D('150'))
        f = next(x for x in g.comprar_o_alquilar(12)['filas'] if x['equipo'] == e)
        self.assertEqual((f['costo_hora'], f['decision']), (D('240.00'), 'Conviene alquilarlo'))
        self.assertEqual(f['equilibrio'], D('13.33'))
        self.assertContains(self.client.get(reverse('obras:comprar_alquilar')), 'Excavadora 320')

    def test_evaluacion_de_subcontratistas(self):
        """HR-11."""
        from core.models import Tercero
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000011',
                                      nombre='ENCOFRADOS DEL SUR SAC')
        sc = Subcontrato.objects.create(obra=self.obra, proveedor=prov, descripcion='Encofrado')
        r = self.client.post(reverse('obras:subcontrato_evaluar', args=[sc.pk]),
                             {'plazo': '4', 'calidad': '5', 'seguridad': '3'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(hasattr(Subcontrato.objects.get(pk=sc.pk), 'evaluacion'))  # en borrador no se evalúa
        Subcontrato.objects.filter(pk=sc.pk).update(estado='LIQUIDADO')
        self.client.post(reverse('obras:subcontrato_evaluar', args=[sc.pk]),
                         {'plazo': '4', 'calidad': '5', 'seguridad': '3', 'comentario': 'Buen acabado'})
        p = next(x for x in g.historial_subcontratistas() if x['proveedor'] == prov)
        self.assertEqual((p['n'], p['puntaje'], p['calidad']), (1, D('4.0'), D('5.0')))
        self.assertContains(self.client.get(reverse('obras:subcontratistas')), 'ENCOFRADOS DEL SUR')
        self.assertContains(self.client.get(reverse('obras:subcontrato', args=[sc.pk])), 'Evaluación del subcontratista')


class FlujoTest(Base186):
    def test_valorizaciones_proyectadas_y_flujo(self):
        """HR-15 y HR-17: 5 m3 de concreto el mes siguiente = 5,000 × 1.2 = 6,000; con IGV y 5 % de garantía."""
        sig = mes_siguiente(HOY)
        ProgramacionPartida.objects.create(partida=self.b, periodo=per(sig), metrado=D('5'))
        vp = g.valorizaciones_proyectadas(self.obra, HOY)
        self.assertEqual((vp[0]['valorizacion'], vp[0]['neto']), (D('6000.00'), D('6726.00')))
        cobro = vp[0]['fecha_cobro']
        f = g.flujo_proyecto(self.obra, 6)
        self.assertEqual(next(x for x in f['filas'] if x['periodo'] == per(cobro))['ingresos'], D('6726.00'))
        from finanzas.proyeccion import presupuesto_caja
        datos = presupuesto_caja(6)
        self.assertIn('Valorizaciones de obra proyectadas (cronograma)', [x['concepto'] for x in datos['filas']])
        self.assertContains(self.client.get(reverse('obras:flujo_proyecto', args=[self.obra.pk])),
                            'Valorizaciones proyectadas por el cronograma')


class ComercialTest(Base186):
    def test_tasa_de_exito(self):
        """HR-01."""
        from obras import licitaciones as lic_srv
        from obras.models import Licitacion
        for n, resultado in enumerate(('GANADA', 'PERDIDA', 'EN_CURSO')):
            lic = lic_srv.crear(f'Obra {n}', self.entidad, self.user, tipo_obra='PUBLICA', procedimiento='LP',
                                numero=f'LP-{n}', valor_referencial=D('100000'))
            Licitacion.objects.filter(pk=lic.pk).update(resultado=resultado, fecha_resultado=HOY,
                                                        motivo_perdida='PRECIO' if resultado == 'PERDIDA' else '')
        r = g.tasa_exito()
        lp = r['licitaciones']['procedimiento'][0]
        self.assertEqual((lp['presentadas'], lp['ganadas'], lp['tasa'], lp['motivo']), (2, 1, D('50.0'), 'Precio'))
        self.assertContains(self.client.get(reverse('obras:tasa_exito')), 'Licitación pública')
        # el rango Desde / Hasta que la pantalla propone (el mes en curso) filtra también las oportunidades del CRM
        from ventas.models import Oportunidad
        Oportunidad.objects.create(nombre='Edificio', tercero=self.entidad, etapa='GANADA', monto=D('5000'),
                                   cerrada_en=HOY)
        r = self.client.get(reverse('obras:tasa_exito'), {'desde': HOY.replace(day=1).isoformat(),
                                                          'hasta': HOY.isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(g.tasa_exito(HOY.replace(day=1), HOY)['crm_total'][0]['ganadas'], 1)

    def test_riesgo_rentabilidad_cartera_y_prediccion(self):
        """HR-04, HR-13, HR-18 y HR-20."""
        Obra.objects.filter(pk=self.obra.pk).update(monto_contratado=D('21240'))
        EventoContrato.objects.create(obra=self.obra, tipo='PENALIDAD', monto=D('1062'), descripcion='Atraso hito 2')
        EventoContrato.objects.create(obra=self.obra, tipo='OBSERVACION', gravedad='ALTA',
                                      descripcion='Acabados observados')
        c = next(x for x in g.riesgo_clientes() if x['tercero'] == self.entidad)
        self.assertEqual((c['penalidades'], c['eventos'], c['abiertos'], c['graves']), (D('1062'), 1, 2, 1))
        # penalidad 5 % del contrato × 4 = 20; 2 abiertos × 4 + 1 grave × 4 + 1 observación = 13
        self.assertEqual((c['puntaje'], c['nivel']), (D('33'), 'Medio'))
        for nombre in ('riesgo_clientes', 'rentabilidad', 'cartera', 'prediccion'):
            self.assertEqual(self.client.get(reverse(f'obras:{nombre}')).status_code, 200, nombre)
        self.assertContains(self.client.get(reverse('obras:prediccion')), 'Datos insuficientes')
        self.assertIn(self.obra, [f['obra'] for f in g.cartera()['filas']])


class CobroApiTest(Base186):
    def test_prediccion_de_cobro(self):
        """HR-16: el cliente pagó 10 días tarde; su factura pendiente se espera 10 días después del vencimiento."""
        from core.models import Serie
        from finanzas import cobranza as cob
        from finanzas.models import Cuenta, Movimiento
        from ventas.models import Venta, VentaItem
        banco = Cuenta.objects.create(nombre='BCP', banco='BCP', tipo='BANCO', saldo_inicial=D('0'))

        def venta(emision, vence):
            serie, numero = Serie.siguiente('01', 'F001')
            v = Venta.objects.create(tipo_comprobante='01', serie=serie, numero=numero, tercero=self.entidad,
                                     fecha_emision=emision, fecha_vencimiento=vence, forma_pago='CREDITO')
            VentaItem.objects.create(documento=v, descripcion='Servicio', cantidad=1, precio_unitario=D('1000'))
            v.calcular_totales()
            v.save()
            return v
        for dias in (90, 60):
            v = venta(HOY - timedelta(days=dias), HOY - timedelta(days=dias - 30))
            Movimiento.objects.create(cuenta=banco, fecha=HOY - timedelta(days=dias - 40), tipo='INGRESO',
                                      concepto='COBRANZA', venta=v, tercero=self.entidad, monto=v.total,
                                      monto_doc=v.total, monto_doc_pen=v.total)
        pendiente = venta(HOY, HOY + timedelta(days=30))
        h = cob.historial_pagos([self.entidad.pk])[self.entidad.pk]
        self.assertEqual((h['docs'], h['atraso'], h['tarde_pct']), (2, 10, D('100.00')))
        r = cob.prediccion_cobro()
        d = next(x for x in r['docs'] if x.pk == pendiente.pk)
        self.assertEqual((d.prediccion_fecha, d.prediccion_confianza), (HOY + timedelta(days=40), 'Media'))
        self.assertContains(self.client.get(reverse('finanzas:prediccion_cobro')), 'Cobro probable')

    def test_api_para_bi(self):
        """HR-19."""
        from core.models import ApiToken
        self.avance_y_costo()
        _, clave = ApiToken.crear(self.user, 'BI')
        cache.clear()
        cab = {'HTTP_AUTHORIZATION': f'Bearer {clave}'}
        r = self.client.get('/api/v1/obras/proyectos/', **cab)
        fila = next(x for x in r.json()['results'] if x['id'] == self.obra.pk)
        self.assertEqual((fila['cpi'], fila['avance_pct']), ('0.62', '16.7'))
        r = self.client.get(f'/api/v1/obras/partidas/?obra={self.obra.pk}', **cab)
        self.assertEqual(r.json()['count'], 2)
        r = self.client.get(f'/api/v1/obras/costos/?obra={self.obra.pk}&formato=csv', **cab)
        self.assertIn('equipos', r.content.decode('utf-8-sig'))
        self.assertEqual(self.client.get('/api/v1/obras/valorizaciones/', **cab).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/obras/cobranza/', **cab).status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/obras/proyectos/{self.obra.pk}/', **cab).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/obras/partidas/', **cab).status_code, 400)
        self.assertIn('/api/v1/obras/proyectos/', self.client.get('/api/v1/', **cab).content.decode())
