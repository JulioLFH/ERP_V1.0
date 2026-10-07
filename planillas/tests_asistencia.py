"""v1.23: asistencia y turnos (faltas, tardanzas, horas extra y traslado a la planilla)."""
from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import asistencia
from .models import FilaPlanilla, Marcacion, Planilla, Trabajador, Turno

D = Decimal


class AsistenciaTest(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('rrhh', 'r@t.com', 'x'))
        self.turno = Turno.objects.create(nombre='Mañana', hora_entrada=time(8), hora_salida=time(17),
                                          refrigerio_min=60, tolerancia_min=5, dias='1234567')
        self.t = Trabajador.objects.create(numero_doc='40000009', apellido_paterno='QUISPE', nombres='ROSA',
                                           sueldo=D('2000'), fecha_ingreso=date(2024, 1, 1), turno=self.turno)

    def test_faltas_tardanzas_y_horas_extra(self):
        self.assertEqual(self.turno.horas_jornada, D('8'))
        ok, errores = asistencia.importar([
            {'dni': '40000009', 'fecha': '02/01/2025', 'entrada': '08:20', 'salida': '20:00'},
            {'dni': '99999999', 'fecha': '02/01/2025', 'entrada': '08:00', 'salida': '17:00'}])
        self.assertEqual((ok, len(errores)), (1, 1))
        Marcacion.objects.create(trabajador=self.t, fecha=date(2025, 1, 3), justificada=True,
                                 observacion='Descanso médico')
        f = asistencia.resumen('202501', hasta_hoy=False)[0]
        self.assertEqual((f['laborables'], f['asistidos'], f['faltas']), (31, 1, 29))
        self.assertEqual((f['tardanzas'], f['tardanza_min']), (1, 20))
        # 08:20 a 20:00 menos 1 h de refrigerio = 10 h 40 min: 2 h 40 min sobre la jornada de 8 h
        self.assertEqual((f['he25'], f['he35']), (D('2.00'), D('0.67')))
        planilla = Planilla.objects.create(tipo='MENSUAL', periodo='202501', estado='CALCULADA')
        fila = FilaPlanilla.objects.create(planilla=planilla, trabajador=self.t)
        r = self.client.post(reverse('planillas:asistencia'), {'periodo': '2025-01', 'accion': 'planilla'})
        self.assertRedirects(r, reverse('planillas:detalle', args=[planilla.pk]), fetch_redirect_response=False)
        fila.refresh_from_db()
        planilla.refresh_from_db()
        self.assertEqual((fila.dias_falta, fila.horas_extra_25, fila.horas_extra_35, planilla.estado),
                         (D('29'), D('2'), D('0.67'), 'BORRADOR'))

    def test_pantallas(self):
        self.assertEqual(self.client.get(reverse('planillas:asistencia') + '?periodo=2025-01').status_code, 200)
        self.assertEqual(self.client.get(reverse('planillas:turnos')).status_code, 200)
        r = self.client.post(reverse('planillas:asistencia'), {
            'accion': 'marcar', 'periodo': '2025-01', 'trabajador': self.t.pk, 'fecha': '2025-01-06',
            'entrada': '08:00', 'salida': '17:00', 'observacion': ''})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Marcacion.objects.filter(trabajador=self.t, fecha=date(2025, 1, 6)).exists())
