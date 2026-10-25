"""v1.85: correcciones de la verificación de la v1.84 (MNT-09, MNT-10, CAL-11, CAL-12, EXC-03, FIN-10, INM-09, OBR-24,
SRV-15, CON-13, PLAT-12 a PLAT-15)."""
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace

from django.urls import reverse

from obras.tests_comercial import HOY, Base

from .tests_v181 import usuario


class CorreccionesTest(Base):
    def test_avisos_con_botones_y_permiso(self):
        """MNT-10: «Aprobar → OT» es un botón; sin permiso se dice cuál falta."""
        from produccion import mantenimiento as mnt
        from produccion.models import Equipo
        eq = Equipo.objects.create(codigo='EQ-AV', nombre='Compresor')
        a = mnt.crear_aviso(eq, 'Pierde presión', 'ALTA', False, self.user)
        r = self.client.get(reverse('manufactura:avisos_mant'))
        self.assertContains(r, '<button class="btn btn-sm btn-outline-primary py-0">Aprobar → OT</button>', html=False)
        r = self.client.post(reverse('manufactura:avisos_mant'), {'aviso': a.pk, 'accion': 'convertir'})
        a.refresh_from_db()
        self.assertEqual((a.estado, r.status_code), ('CONVERTIDO', 302))
        sin = usuario('operario_av', ['mantenimiento'], ['mantenimiento.registrar'])
        mnt.crear_aviso(eq, 'Ruido', 'NORMAL', False, sin)
        self.client.force_login(sin)
        self.assertContains(self.client.get(reverse('manufactura:avisos_mant')), 'Lo aprueba quien tiene')

    def test_indicadores_ignoran_valores_imposibles(self):
        """MNT-09."""
        from produccion import mantenimiento as mnt
        from produccion.models import Equipo, OrdenMantenimiento
        from produccion.planta import crear_orden
        eq = Equipo.objects.create(codigo='EQ-NEG', nombre='Bomba')
        o = crear_orden(OrdenMantenimiento(equipo=eq, tipo='CORRECTIVO', descripcion='Falla'), self.user)
        OrdenMantenimiento.objects.filter(pk=o.pk).update(estado='CERRADA', fecha_fin=HOY, horas_parada=D('-3'),
                                                          costo_mano_obra=D('-50'))
        f = next(x for x in mnt.indicadores(HOY - timedelta(days=9), HOY)['filas'] if x['equipo'].pk == eq.pk)
        self.assertEqual((f['disponibilidad'], f['mttr'], f['horas_parada']), (D('100.00'), None, D('0')))
        self.assertIn(o, list(mnt.imposibles()))
        self.assertContains(self.client.get(reverse('manufactura:tablero_mant')), 'valores imposibles')

    def test_documento_bpm_solo_con_flujo_de_firmas(self):
        """CAL-12."""
        from calidad.models import DocumentoControlado
        from core import validacion
        d = DocumentoControlado.objects.create(codigo='POE-BPM-01', titulo='Limpieza de áreas', norma='BPM_DIGEMID')
        with self.assertRaisesMessage(validacion.ErrorValidacion, 'flujo de firmas'):
            validacion.enviar(d, self.user)
        otro = DocumentoControlado.objects.create(codigo='PR-ISO-01', titulo='Compras', norma='ISO9001')
        validacion.enviar(otro, self.user)  # los demás siguen con la validación genérica

    def test_accion_correctiva_con_responsable_yo(self):
        """EXC-03: «yo» (vacío) es el usuario actual, sin error 500."""
        from django.contrib.contenttypes.models import ContentType

        from calidad.models import Accion, NoConformidad
        nc = NoConformidad.objects.create(descripcion='Producto sin rotular', origen='INTERNA')
        ct = ContentType.objects.get_for_model(NoConformidad)
        r = self.client.post(reverse('calidad:accion_nueva', args=[ct.pk, nc.pk]), {
            'descripcion': 'Rotular el lote', 'plazo': (HOY + timedelta(days=5)).isoformat(), 'responsable': '',
            'tipo': 'CORRECTIVA'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Accion.objects.get().responsable, self.user)

    def test_quien_arma_la_propuesta_no_la_aprueba(self):
        """FIN-10: tampoco el administrador, si hay otro usuario."""
        from finanzas import propuesta_pagos as pp
        from finanzas.models import Cuenta
        usuario('gerente_fin', ['tesoreria'], ['tesoreria.aprobar_pagos'])
        banco = Cuenta.objects.create(nombre='BCP', banco='BCP', tipo='BANCO', saldo_inicial=D('1000'))
        p = pp.armar(banco, HOY, HOY, D('0'), self.user)
        self.assertFalse(pp.puede_aprobar(self.user, p))
        from core.segregacion import REGLAS_FIJAS
        self.assertTrue(any('propuesta de pagos' in r for r in REGLAS_FIJAS))

    def test_terreno_al_50_por_ciento(self):
        """INM-09."""
        from obras.forms import ObraForm
        from obras.inmobiliario import partir
        c, igv, t = partir(D('350000'), D('50'))
        self.assertEqual(c + t, D('321100.92'))
        datos = {'nombre': 'Edificio', 'tipo': 'INMOBILIARIA', 'sistema': 'SUMA_ALZADA', 'moneda': 'PEN',
                 'estado': 'PLANIFICACION', 'terreno_pct': '20', 'prorrateo': 'AREA', 'prorrateo_area_pct': '50',
                 'gastos_generales_pct': '0', 'utilidad_pct': '0', 'adelanto_directo_pct': '0',
                 'adelanto_materiales_pct': '0', 'retencion_garantia_pct': '0', 'horas_presupuestadas': '0',
                 'gastos_presupuestados': '0', 'monto_contratado': '0', 'plazo_dias': '0', 'area_m2': '0'}
        f = ObraForm(datos)
        f.is_valid()
        self.assertIn('terreno_pct', f.errors)
        f = ObraForm({**datos, 'terreno_distinto': 'on'})
        f.is_valid()
        self.assertNotIn('terreno_pct', f.errors)

    def test_versiones_emparejan_contra_el_subpresupuesto_principal(self):
        """OBR-24: el archivo nuevo trae un solo subpresupuesto; 01.01.01 es un cambio de metrado, no una baja."""
        from obras.versiones import emparejar
        principal = SimpleNamespace(codigo='001', es_gastos_generales=False)
        gg = SimpleNamespace(codigo='002', es_gastos_generales=True)
        nuevo_sub = SimpleNamespace(codigo='01', es_gastos_generales=False)

        def partida(pk, sub, item, desc):
            return SimpleNamespace(pk=pk, subpresupuesto=sub, subpresupuesto_id=1 if sub else None, item=item,
                                   descripcion=desc, es_titulo=False, clave=f'{sub.codigo if sub else ""}|{item}')
        a = {x.clave: x for x in [partida(1, principal, '01.01.01', 'ENCOFRADO DE CIMENTACION'),
                                  partida(2, principal, '01.01.02', 'CONCRETO'), partida(3, gg, '01.01.01', 'GG')]}
        n = {x.clave: x for x in [partida(11, nuevo_sub, '01.01.01', 'ENCOFRADO DE CIMENTACION'),
                                  partida(12, nuevo_sub, '01.01.02', 'CONCRETO')]}
        pares = emparejar(a, n)
        self.assertEqual({k: v.pk for k, v in pares.items()}, {'01|01.01.01': 1, '01|01.01.02': 2})

    def test_recibo_del_especialista_pasa_a_gastado(self):
        """SRV-15: con otra cuenta de honorarios (PCGE 2026) el recibo pasa de comprometido a gastado."""
        from contabilidad.models import Asiento, AsientoLinea, CuentaContable, CuentaDefecto
        from obras import presupuesto_proyecto as pp
        from obras import servicios as s
        from obras.models import Obra
        p = Obra.objects.create(nombre='Hospital', tipo='SERVICIO', cliente=self.entidad, monto_contratado=D('118000'),
                                residente=self.user, fecha_inicio=HOY - timedelta(days=10), plazo_dias=60)
        pp.guardar(p, {'ESPECIALISTAS': D('5000')})
        honorarios = CuentaContable.objects.create(codigo='6399001', nombre='Honorarios 2026', imputable=True)
        CuentaDefecto.objects.update_or_create(clave='compra_HONORARIOS', defaults={'cuenta': honorarios})
        parte = s.registrar_parte(p, s.lunes(HOY), D('20'), self.user, persona='Arq. Ruiz', costo=D('80'))
        s.aprobar_parte(parte, self.user)
        fila = lambda: next(f for f in pp.control(p)['filas'] if f['rubro'] == 'ESPECIALISTAS')
        self.assertEqual((fila()['comprometido'], fila()['gastado']), (D('1600'), D('0')))
        caja, _ = CuentaContable.objects.get_or_create(codigo='4699001', defaults={'nombre': 'Por pagar',
                                                                                   'imputable': True})
        a = Asiento.objects.create(fecha=HOY, glosa='Recibo E001-902')
        AsientoLinea.objects.create(asiento=a, cuenta=honorarios, centro_costo=p.centro_costo, debe=D('1600'))
        AsientoLinea.objects.create(asiento=a, cuenta=caja, haber=D('1600'))
        self.assertEqual((fila()['comprometido'], fila()['gastado']), (D('0'), D('1600')))

    def test_importe_del_asiento_en_la_lista(self):
        """CON-13: buscar por cuenta no multiplica el importe."""
        from contabilidad.models import Asiento, AsientoLinea, CuentaContable
        c1, _ = CuentaContable.objects.get_or_create(codigo='201199', defaults={'nombre': 'Mercaderías',
                                                                                'imputable': True})
        c2, _ = CuentaContable.objects.get_or_create(codigo='609199', defaults={'nombre': 'Costos', 'imputable': True})
        a = Asiento.objects.create(fecha=HOY, glosa='Inventario')
        for _ in range(5):
            AsientoLinea.objects.create(asiento=a, cuenta=c1, debe=D('100'))
        AsientoLinea.objects.create(asiento=a, cuenta=c2, haber=D('500'))
        r = self.client.get(reverse('contabilidad:asientos') + f'?periodo={HOY:%Y%m}&q=2011')
        total = next(x.total for x in r.context['page_obj'] if x.pk == a.pk)
        self.assertEqual(total, D('500'))

    def test_sugerencias_filtros_multiples_y_fechas(self):
        """PLAT-12, PLAT-13 y PLAT-14."""
        from core.models import Producto
        from core.utils import id_o_none
        from obras.forms import ObraForm
        Producto.objects.create(codigo='PRU-01', nombre='Prueba de buscador', unidad='NIU')
        r = self.client.get(reverse('sugerencias'), {'q': 'PRU', 'ruta': reverse('productos')})
        self.assertIn('PRU-01 · Prueba de buscador', [x['texto'] for x in r.json()])
        self.assertEqual(self.client.get(reverse('sugerencias'), {'q': 'P', 'ruta': '/productos/'}).json(), [])
        r = self.client.get(reverse('ventas:lista') + '?estado=REGISTRADO&estado=ANULADO')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'data-filtro="Estado"')
        self.assertEqual(self.client.get(reverse('inventario:lista') + '?tipo=1&tipo=2').status_code, 200)
        self.assertEqual(ObraForm().initial.get('fecha_contrato'), HOY)
        self.assertEqual(self.client.get(reverse('tercero_credito', args=[self.entidad.pk])).json()['dias'],
                         self.entidad.dias_credito)
        self.assertEqual((id_o_none(''), id_o_none('yo'), id_o_none('15')), (None, None, 15))
