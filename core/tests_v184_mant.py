"""v1.84: Mantenimiento (MNT-01 a MNT-08): avisos, planes por condición con hoja de tareas, seguridad (permiso y LOTO),
horas por técnico, cierre con paradas y calibración, indicadores y pantallas."""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from core.models import Almacen, Producto
from obras.tests_comercial import HOY, Base
from produccion import mantenimiento as mnt
from produccion.models import CentroTrabajo, Equipo, OrdenMantenimiento, PlanMantenimiento
from produccion.planta import ErrorPlanta, crear_orden


class MantBase(Base):
    def setUp(self):
        super().setUp()
        from planillas.models import Trabajador
        self.centro = CentroTrabajo.objects.create(codigo='ENV', nombre='Envasadora')
        self.eq = Equipo.objects.create(codigo='EQ-BOM1', nombre='Bomba de agua', centro=self.centro,
                                        valor_reposicion=D('10000'))
        self.tecnico = Trabajador.objects.create(nombres='Pedro', apellido_paterno='Quispe', numero_doc='42424242',
                                                 sueldo=D('3840'), fecha_ingreso=HOY - timedelta(days=300))

    def ot(self, **extra):
        datos = {'equipo': self.eq, 'tipo': 'CORRECTIVO', 'descripcion': 'Fuga en el sello mecánico'}
        datos.update(extra)
        return crear_orden(OrdenMantenimiento(**datos), self.user)


class AvisosYPlanesTest(MantBase):
    def test_aviso_aprobado_se_convierte_en_orden(self):
        """MNT-03."""
        a = mnt.crear_aviso(self.eq, 'Ruido fuerte y vibración', 'EMERGENCIA', True, self.user)
        self.eq.refresh_from_db()
        self.assertEqual(self.eq.estado, 'INOPERATIVO')
        ot = mnt.convertir_aviso(a, self.user)
        self.assertEqual((ot.tipo, ot.prioridad, a.estado), ('CORRECTIVO', 'EMERGENCIA', 'CONVERTIDO'))
        b = mnt.crear_aviso(self.eq, 'Mancha de aceite', 'NORMAL', False, self.user)
        with self.assertRaisesMessage(ErrorPlanta, 'por qué'):
            mnt.rechazar_aviso(b, '')
        mnt.rechazar_aviso(b, 'Es del equipo vecino')
        self.assertEqual(self.client.get(reverse('manufactura:avisos_mant')).status_code, 200)
        self.assertEqual(self.client.get(reverse('manufactura:aviso_nuevo')).status_code, 200)

    def test_plan_por_condicion_con_hoja_de_tareas_y_materiales(self):
        """MNT-02."""
        rep = Producto.objects.create(codigo='REP-SELLO', nombre='Sello mecánico', unidad='NIU')
        plan = PlanMantenimiento.objects.create(equipo=self.eq, tarea='Cambio de rodamientos', frecuencia_dias=None,
                                                variable='Temperatura', umbral_max=D('80'))
        plan.tareas.create(secuencia=10, descripcion='Bloquear y desenergizar', minutos=30)
        plan.tareas.create(secuencia=20, descripcion='Cambiar rodamientos', minutos=90)
        plan.materiales.create(producto=rep, cantidad=D('2'))
        self.assertIsNone(mnt.registrar_lectura(self.eq, 'Temperatura', D('70'), self.user, '°C').orden)
        l = mnt.registrar_lectura(self.eq, 'temperatura', D('85'), self.user, '°C')
        self.assertTrue(l.fuera_de_rango)
        ot = l.orden
        self.assertEqual((ot.tipo, ot.horas_estimadas), ('PREDICTIVO', D('2.00')))
        self.assertEqual(list(ot.tareas.values_list('descripcion', flat=True)),
                         ['Bloquear y desenergizar', 'Cambiar rodamientos'])
        self.assertEqual(ot.repuestos.get().cantidad, D('2'))
        self.assertIsNone(mnt.registrar_lectura(self.eq, 'Temperatura', D('90'), self.user).orden)  # ya hay una abierta
        self.assertEqual(mnt.reservado(rep), D('2'))
        # anticipación: el preventivo que vence en 5 días se programa con 7 días de anticipación
        from produccion import planta
        p2 = PlanMantenimiento.objects.create(equipo=self.eq, tarea='Lubricación', frecuencia_dias=30,
                                              ultima_fecha=HOY - timedelta(days=25), anticipacion_dias=7)
        creadas = planta.programar_preventivos(1, self.user)
        self.assertIn(p2.pk, [o.plan_id for o in creadas])
        r = self.client.get(reverse('manufactura:plan_mant', args=[plan.pk]))
        self.assertContains(r, 'Cambiar rodamientos')


class SeguridadYCierreTest(MantBase):
    def test_permiso_loto_horas_checklist_y_paradas(self):
        """MNT-08, MNT-04, MNT-07 y MNT-01."""
        from produccion.models import MotivoParada, Parada
        from sst.models import PermisoTrabajo
        ot = self.ot(requiere_permiso=True, requiere_loto=True)
        with self.assertRaisesMessage(ErrorPlanta, 'permiso de trabajo'):
            mnt.iniciar(ot)
        permiso = PermisoTrabajo.objects.create(tipo='ELECTRICO', fecha=HOY, descripcion='Cambio de sello')
        ot.permiso_trabajo = permiso
        ot.save()
        with self.assertRaisesMessage(ErrorPlanta, 'validado por SST'):
            mnt.iniciar(ot)
        PermisoTrabajo.objects.filter(pk=permiso.pk).update(estado_validacion='VALIDADO')
        ot.refresh_from_db()
        with self.assertRaisesMessage(ErrorPlanta, 'LOTO'):
            mnt.iniciar(ot)
        ot.loto_aplicado_por = 'Pedro Quispe'
        ot.save()
        mnt.iniciar(ot)
        self.assertEqual(ot.estado, 'EN_EJECUCION')
        h = mnt.registrar_horas(ot, self.tecnico, D('4'))
        self.assertEqual(h.costo, D('116.00'))  # 3,840 × 145 % / 192 h = 29.00 por hora
        t = ot.tareas.create(descripcion='Probar sin fugas')
        motivo = MotivoParada.objects.create(codigo='FALLA', nombre='Falla de equipo')
        Parada.objects.create(centro=self.centro, equipo=self.eq, motivo=motivo, orden_mantenimiento=ot,
                              inicio=timezone.now() - timedelta(hours=3), fin=timezone.now() - timedelta(hours=1))
        ot.trabajo_realizado = 'Se cambió el sello mecánico'
        with self.assertRaisesMessage(ErrorPlanta, 'checklist'):
            mnt.cerrar(ot, self.user)
        t.hecha = True
        t.save()
        mnt.cerrar(ot, self.user)
        ot.refresh_from_db()
        self.assertEqual((ot.estado, ot.costo_mano_obra, ot.horas_parada), ('CERRADA', D('116.00'), D('2.00')))

    def test_orden_de_calibracion_registra_el_certificado(self):
        """MNT-08 / CAL-08."""
        from produccion.models import CalibracionEquipo
        ot = self.ot(tipo='CALIBRACION', descripcion='Calibración anual de la balanza')
        ot.trabajo_realizado = 'Calibrada con pesas patrón'
        with self.assertRaisesMessage(ErrorPlanta, 'certificado'):
            mnt.cerrar(ot, self.user)
        mnt.cerrar(ot, self.user, calibracion=CalibracionEquipo(certificado='LC-123', proxima=HOY + timedelta(days=365)))
        self.eq.refresh_from_db()
        self.assertEqual((self.eq.es_instrumento, self.eq.proxima_calibracion), (True, HOY + timedelta(days=365)))

    def test_cierre_sin_negativos(self):
        """MNT-01."""
        from produccion.forms_planta import CierreMantenimientoForm
        ot = self.ot()
        f = CierreMantenimientoForm({'trabajo_realizado': 'Cambio de sello', 'horas_parada': '-3',
                                     'costo_mano_obra': '-50', 'costo_servicios': '0'}, instance=ot)
        self.assertFalse(f.is_valid())
        self.assertIn('horas_parada', f.errors)
        self.assertIn('costo_mano_obra', f.errors)


class IndicadoresTest(MantBase):
    def test_mtbf_mttr_pareto_y_pantallas(self):
        """MNT-06, MNT-05 y MNT-07."""
        from produccion.models import CodigoFalla
        modo = CodigoFalla.objects.create(tipo='MODO', codigo='M01', nombre='Fuga')
        for horas in ('2', '4'):
            o = self.ot(falla_modo=modo)
            OrdenMantenimiento.objects.filter(pk=o.pk).update(estado='CERRADA', fecha_fin=HOY, horas_parada=D(horas),
                                                              costo_mano_obra=D('100'))
        ind = mnt.indicadores(HOY - timedelta(days=9), HOY)
        f = next(x for x in ind['filas'] if x['equipo'].pk == self.eq.pk)
        self.assertEqual((f['fallas'], f['mttr'], f['horas_parada']), (2, D('3.00'), D('6')))
        self.assertEqual(f['mtbf'], ((D('240') - D('6')) / 2).quantize(D('0.01')))
        self.assertEqual(f['disponibilidad'], ((D('240') - D('6')) * 100 / D('240')).quantize(D('0.01')))
        self.assertEqual(ind['pareto'][0]['modo'], str(modo))
        self.assertEqual(f['pct_reposicion'], D('2.00'))  # 200 de 10,000
        for nombre in ('tablero_mant', 'calendario_mant', 'mis_ordenes_mant', 'lecturas_mant', 'ubicaciones_mant',
                       'codigos_falla', 'repuestos_criticos'):
            self.assertEqual(self.client.get(reverse(f'manufactura:{nombre}')).status_code, 200, nombre)
        self.assertEqual(self.client.get(reverse('manufactura:historial_equipo', args=[self.eq.pk])).status_code, 200)
        r = self.client.get(reverse('manufactura:tablero_mant') + '?formato=excel')
        self.assertIn('spreadsheetml', r['Content-Type'])
        self.assertEqual(self.client.get(reverse('manufactura:orden_mant', args=[o.pk])).status_code, 200)
