"""v1.90 (checklist del 26/10/2026 en v1.89): drivers en la declaración (MAN-22), estándar con la base de la orden
(MAN-23), stock sin lote y anulación de salidas (MAN-20), absorción con declaraciones (MAN-09), liberación del lote
desde la inspección (CAL-13), validaciones (ACT-01, INV-33, SRV-16, PLAT-16), historial legible (INV-30), enlace del
formulario web (CRM-09), grupos de todos los módulos (HR-03) y CSP sin 'unsafe-inline' (SEG-09)."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from produccion.tests_reportes_produccion import Base as BaseProduccion

from .tests_v188 import _salida

HOY = timezone.localdate()


def _trabajador(doc, nombre, cese=None):
    from planillas.models import Trabajador
    return Trabajador.objects.create(numero_doc=doc, apellido_paterno=nombre, nombres='Prueba', sueldo=D('2400'),
                                     fecha_ingreso=HOY - timedelta(days=400), fecha_cese=cese)


class DriversDeclaracionTest(BaseProduccion):
    def test_varios_trabajadores_roles_y_kwh(self):
        """MAN-22: una declaración con dos personas (roles distintos), horas máquina y energía en kWh."""
        from produccion import parciales
        from produccion.models import DriverPuesto
        energia = DriverPuesto.objects.create(centro=self.horno, nombre='Energía eléctrica', unidad='kWh',
                                              tarifa=D('0.50'), por_hora=D('10'))
        o = self.orden()
        h = o.horas.get()
        t1, t2 = _trabajador('70000001', 'Quispe'), _trabajador('70000002', 'Mamani')
        d = parciales.declarar(o, self.user, '50', cargos={h.pk: [
            {'tipo': 'MAQUINA', 'cantidad': '0.5'}, {'tipo': 'DRIVER', 'driver': energia.pk, 'cantidad': '6',
                                                     'tarifa': '0.80'}]},
            personal={h.pk: [{'rol': 'MAQUINISTA', 'trabajador': t1.pk, 'horas': '0.5'},
                             {'rol': 'SUPERVISOR', 'trabajador': t2.pk, 'horas': '0.25'}]})
        self.assertEqual(sorted(d.personal.values_list('rol', flat=True)), ['MAQUINISTA', 'SUPERVISOR'])
        kwh = d.cargos.get(tipo='DRIVER')
        self.assertEqual((kwh.cantidad, kwh.importe, kwh.tarifa_editada), (D('6'), D('4.80'), True))
        self.assertEqual(d.costo_cif, D('15.00') + D('4.80'))  # 0.5 h × S/ 30 de CIF + los kWh
        # sin filas: la propuesta de la ruta con el consumo por hora del driver
        d2 = parciales.declarar(o, self.user, '10')
        self.assertEqual(d2.cargos.get(tipo='DRIVER').cantidad, D('1.0000'))  # 0.10 h × 10 kWh
        # el estándar incluye el driver por hora máquina (S/ 5 por hora)
        from produccion import servicios
        hoja = servicios.hoja_costos(self.version, cantidad=D('100'))
        self.assertEqual(hoja['tot_cif'], D('35.00'))  # 1 h × (30 + 10 kWh × 0.50)
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        self.assertContains(r, 'Horas y drivers del turno')
        self.assertContains(r, 'Energía eléctrica')

    def test_costos_tardios_en_varias_filas_y_trabajador_cesado(self):
        """MAN-22 y PLAT-16: varias filas en un envío; un trabajador cesado no carga horas."""
        from produccion import parciales, servicios
        o = self.orden()
        parciales.declarar(o, self.user, '40')
        r = self.client.post(reverse('manufactura:orden_costo_tardio', args=[o.pk]), {
            'fecha': HOY.isoformat(), 'motivo': 'Parte del turno noche que no se registró',
            'tardio-0-tipo': 'MO', 'tardio-0-importe': '20', 'tardio-1-tipo': 'OTRO', 'tardio-1-importe': '15',
            'tardio-2-tipo': 'MO', 'tardio-2-importe': ''})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sorted(o.costos_tardios.values_list('importe', flat=True)), [D('15'), D('20')])
        o.refresh_from_db()
        cesado = _trabajador('70000003', 'Cesado', cese=HOY - timedelta(days=10))
        with self.assertRaisesMessage(servicios.ErrorProduccion, 'no estaba en planilla'):
            parciales.costo_tardio(o, self.user, 'MO', HOY.isoformat(), 'Horas olvidadas del turno', horas='2',
                                   trabajador=cesado)
        self.assertNotContains(self.client.get(reverse('manufactura:orden', args=[o.pk])), 'Cesado, Prueba')


class EstandarYAnulacionTest(BaseProduccion):
    def test_estandar_con_la_cantidad_de_la_orden(self):
        """MAN-23: sin estándar liberado, el provisional reparte la preparación en la cantidad de la orden."""
        from produccion import servicios
        op = self.version.hoja.operaciones.get()
        op.horas_preparacion = D('0.40')
        op.save()
        self.version.lote_costeo = D('1')
        self.version.save()
        o = self.orden(cantidad='10')
        self.assertEqual(o.estandar_detalle['lote'], '10')
        d = servicios.desglose_estandar(o)
        self.assertEqual(d['horas_std'], d['horas_plan'])
        self.assertFalse(d['distinto'])
        self.assertContains(self.client.get(reverse('manufactura:orden', args=[o.pk])), 'Calculado para un lote')

    def test_anular_la_orden_anula_su_salida_de_almacen(self):
        """MAN-20 (3): la «Salida a orden de producción» se anula con la orden (y se avisa)."""
        from produccion import servicios
        o = self.orden()
        self.masa.refresh_from_db()
        inicial = self.masa.stock
        ns = _salida(self, o, self.masa, '5')
        self.assertContains(self.client.get(reverse('manufactura:orden', args=[o.pk])), ns.numero)
        r = self.client.post(reverse('manufactura:orden_anular', args=[o.pk]),
                             {'motivo': 'Orden registrada por error de prueba'}, follow=True)
        self.assertContains(r, f'Salidas a la orden anuladas: {ns.numero}')
        ns.refresh_from_db()
        self.masa.refresh_from_db()
        self.assertEqual((ns.estado, self.masa.stock), ('ANULADO', inicial))
        o.refresh_from_db()
        self.assertEqual(o.estado, 'ANULADA')
        self.assertFalse(o.consumos.filter(despachado__gt=0).exists())
        with self.assertRaises(servicios.ErrorProduccion):
            servicios.anular(o, self.user, 'Segunda anulación de la misma orden')

    def test_stock_sin_lote_no_es_libre(self):
        """MAN-20 (1): en un producto con lote, el stock sin lote no se puede consumir."""
        from produccion import servicios
        self.masa.control = 'LOTE'
        self.masa.save()
        o = self.orden()
        fila = next(f for f in servicios.disponibilidad(o) if f['consumo'].producto_id == self.masa.pk)
        self.assertEqual((fila['libre'], fila['sin_lote']), (D('0'), D('500')))

    def test_absorcion_incluye_declaraciones(self):
        """MAN-09: lo absorbido por la producción declarada por turnos entra al reporte del periodo."""
        from produccion import parciales, servicios
        o = self.orden()
        parciales.declarar(o, self.user, '50')
        filas = servicios.absorcion_por_puesto(HOY.strftime('%Y%m'))
        horno = next(f for f in filas if f['puesto'].pk == self.horno.pk)
        self.assertEqual(horno['cif'], D('15.00'))  # 0.5 h × S/ 30
        centro = next(f for f in servicios.absorcion(HOY.strftime('%Y%m'))[0] if f['centro'].codigo == 'PL')
        self.assertGreater(centro['absorbido'], 0)


class LiberacionDesdeInspeccionTest(BaseProduccion):
    def test_liberar_lote_con_firma_desde_la_inspeccion(self):
        """CAL-13: la inspección aprobada libera el lote en cuarentena (certificado firmado por otra persona)."""
        from core.models import Lote
        from produccion import calidad_farma as cf
        from produccion import planta
        from produccion.models import InspeccionCalidad, ParametroCalidad
        from produccion.planta import ErrorPlanta

        from .tests_v181 import usuario
        mp = self.masa
        mp.control = 'LOTE'
        mp.save()
        ParametroCalidad.objects.create(producto=mp, nombre='Humedad', unidad='%', minimo=D('1'), maximo=D('5'))
        mp.mover_stock(D('10'), 'Ingreso', costo=D('3'), almacen=self.almacen, origen='OPERACION', concepto='AJ_ING',
                       lotes=[('L-PRU-190', D('10'), HOY + timedelta(days=300))])
        lote = Lote.objects.get(producto=mp, codigo='L-PRU-190')
        lote.estado_calidad = 'CUARENTENA'
        lote.save()
        analista = usuario('analista190', ['control_calidad'], ['control_calidad.inspecciones',
                                                                'control_calidad.liberar_lotes'])
        qa = usuario('qa190', ['control_calidad'], ['control_calidad.inspecciones', 'control_calidad.liberar_lotes'])
        insp = planta.crear_inspeccion(InspeccionCalidad(tipo='RECEPCION', producto=mp, lote='L-PRU-190',
                                                         cantidad=D('1'), almacen=self.almacen), analista)
        r = insp.resultados.get()
        planta.registrar_resultados(insp, {r.pk: (D('3'), '', None)}, 'APROBADO', '', analista)
        self.assertEqual(cf.liberable(insp), lote)
        with self.assertRaisesMessage(ErrorPlanta, 'segregación'):
            cf.liberar_desde_inspeccion(insp, analista, 'Clave-segura-9')
        self.client.force_login(qa)
        respuesta = self.client.post(reverse('manufactura:inspeccion', args=[insp.pk]),
                                     {'accion': 'liberar', 'clave': 'Clave-segura-9'}, follow=True)
        self.assertContains(respuesta, 'liberado por QA')
        lote.refresh_from_db()
        self.assertEqual(lote.estado_calidad, 'LIBERADO')
        cert = insp.certificados.get()
        self.assertEqual((cert.estado, cert.aprobado_por), ('APROBADO', qa))


class ValidacionesV190Test(BaseProduccion):
    def test_activo_en_periodo_cerrado(self):
        """ACT-01: la adquisición no entra a un periodo cerrado (salvo un saldo inicial anterior al sistema)."""
        from activos.forms import ActivoForm
        from contabilidad.models import PeriodoContable
        cerrado = HOY.replace(day=1) - timedelta(days=1)
        PeriodoContable.objects.update_or_create(periodo=cerrado.strftime('%Y%m'), defaults={'cerrado': True})
        f = ActivoForm()
        f.cleaned_data, f._errors = {}, {}
        f._validar_fechas(cerrado, cerrado, 'SALDO_INICIAL')
        self.assertIn('fecha_adquisicion', f._errors)
        f = ActivoForm()
        f.cleaned_data, f._errors = {}, {}
        antiguo = cerrado.replace(year=cerrado.year - 30)
        f._validar_fechas(antiguo, antiguo, 'SALDO_INICIAL')
        self.assertNotIn('fecha_adquisicion', f._errors)

    def test_unidades_enteras_y_largo_del_lote(self):
        """INV-33."""
        from inventario.forms import OperacionItemForm
        botella = self.empaque  # NIU
        f = OperacionItemForm({'producto': botella.pk, 'cantidad': '0.5', 'rol': 'INSUMO'})
        self.assertFalse(f.is_valid())
        self.assertIn('enteros', str(f.errors['cantidad']))
        f = OperacionItemForm({'producto': self.masa.pk, 'cantidad': '0.5', 'rol': 'INSUMO', 'lote': 'L' * 31})
        self.assertFalse(f.is_valid())
        self.assertIn('lote', f.errors)
        self.assertTrue(OperacionItemForm({'producto': self.masa.pk, 'cantidad': '0.5', 'rol': 'INSUMO',
                                           'lote': 'L-2026-10'}).is_valid())

    def test_inactivos_fuera_de_las_listas(self):
        """PLAT-16: centros de costo desactivados no se ofrecen en Tesorería (el guardado se conserva)."""
        from contabilidad.models import CentroCosto
        from finanzas.forms import MovimientoForm
        viejo = CentroCosto.objects.create(codigo='X-OFF', nombre='Desactivado', activo=False)
        f = MovimientoForm()
        f['centro_costo']  # noqa: B018 — al mostrar el formulario se filtran las listas
        self.assertNotIn(viejo, f.fields['centro_costo'].queryset)

    def test_historial_sin_diccionarios(self):
        """INV-30: lo guardado como texto por versiones anteriores se resume."""
        from core.templatetags.erp_tags import valor_historial
        self.assertEqual(valor_historial("{'materiales': [], 'lote': '10'}"), '2 dato(s)')
        self.assertEqual(valor_historial('{}'), '—')
        self.assertEqual(valor_historial('Confirmada'), 'Confirmada')

    def test_grupo_obras_existe(self):
        """HR-03: los grupos de todos los módulos existen para asignarlos."""
        from core.modulos import GRUPOS
        self.assertTrue(Group.objects.filter(name=GRUPOS['obras']).exists())


class ConsorcioYCrmTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('t190', 't@t.com', 'x')
        self.client.force_login(self.user)

    def test_consorcio_suma_ruc_y_nuestra_empresa(self):
        """SRV-16."""
        from obras.models import Obra, SocioConsorcio
        from obras.views_gestion import SocioForm
        o = Obra.objects.create(codigo='OB190', nombre='Obra consorcio')
        SocioConsorcio.objects.create(obra=o, nombre='SOCIO A', ruc='20100000001', participacion_pct=D('60'),
                                      es_la_empresa=True)
        f = SocioForm({'nombre': 'SOCIO B', 'ruc': '20100000001', 'participacion_pct': '50', 'es_la_empresa': 'on'},
                      obra=o)
        self.assertFalse(f.is_valid())
        for campo in ('participacion_pct', 'ruc', 'es_la_empresa'):
            self.assertIn(campo, f.errors)
        self.assertTrue(SocioForm({'nombre': 'SOCIO B', 'ruc': '20100000002', 'participacion_pct': '40'},
                                  obra=o).is_valid())

    def test_enlace_del_formulario_web_visible(self):
        """CRM-09: el enlace se ve como texto y se puede abrir para probar."""
        from ventas.models import ConfigCRM
        cfg = ConfigCRM.actual()
        cfg.web_activo = True
        cfg.save()
        r = self.client.get(reverse('crm:configuracion'))
        self.assertContains(r, 'Abrir el formulario (probar)')
        self.assertContains(r, f'/{cfg.token_web}')

    def test_csp_sin_unsafe_inline(self):
        """SEG-09: los scripts propios llevan el nonce de la respuesta; no hay onclick ni javascript:."""
        import pathlib
        import re
        r = self.client.get(reverse('dashboard'))
        csp = r['Content-Security-Policy']
        script_src = next(p for p in csp.split(';') if p.strip().startswith('script-src'))
        self.assertNotIn('unsafe-inline', script_src)
        nonce = re.search(r"'nonce-([^']+)'", script_src).group(1)
        self.assertContains(r, f'nonce="{nonce}"')
        plantillas = pathlib.Path(__file__).resolve().parent.parent / 'templates'
        en_linea = re.compile(r'\son(click|change|input|submit|load)=|javascript:')
        malas = [str(p) for p in plantillas.rglob('*.html') if en_linea.search(p.read_text(encoding='utf-8'))]
        self.assertEqual(malas, [])
