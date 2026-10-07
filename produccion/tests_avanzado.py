"""v1.23: programación detallada (capacidad finita), reporte de planta, cambios de ingeniería y costeo ABC."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import CentroCosto
from core.models import Almacen, Producto

from . import abc, avanzado, servicios
from .models import (ActividadABC, CentroTrabajo, HoraOrden, ListaMateriales, OrdenProduccion, VersionFabricacion)

D = Decimal
HOY = date.today()


class AvanzadoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('jefe', 'j@t.com', 'x')
        cls.otro = User.objects.create_superuser('ingeniero', 'i@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.almacen = Almacen.principal()
        self.insumo = Producto.objects.get(codigo='P003')
        self.prod = Producto.objects.create(nombre='Galleta', clase='PRODUCTO_TERMINADO', precio_venta=D('10'))
        self.receta = ListaMateriales.objects.create(producto=self.prod, codigo='V1', cantidad_base=D('1'))
        self.receta.componentes.create(producto=self.insumo, cantidad=D('1'))
        self.version = VersionFabricacion.objects.create(producto=self.prod, codigo='V1', lista=self.receta,
                                                         vigente_desde=HOY - timedelta(days=30))
        self.horno = CentroTrabajo.objects.create(codigo='HOR', nombre='Horno', horas_turno=D('8'),
                                                  dias_laborables='1234567')

    def _orden(self, horas, prioridad=5, estado='CONFIRMADA'):
        o = OrdenProduccion.objects.create(producto=self.prod, lista=self.receta, version=self.version, cantidad=D('2'),
                                           fecha=HOY, almacen_insumos=self.almacen, almacen_destino=self.almacen,
                                           prioridad=prioridad, estado=estado, numero=f'OP-{prioridad}')
        o.consumos.create(producto=self.insumo, cantidad_plan=D('2'), cantidad_real=D('2'))
        HoraOrden.objects.create(orden=o, centro=self.horno, secuencia=10, descripcion='10 Horneado',
                                 horas_plan=D(horas), horas_real=D(horas))
        return o

    def test_programacion_con_capacidad_finita(self):
        segunda = self._orden('4', prioridad=2)
        primera = self._orden('10', prioridad=1)
        ordenes, por_centro = avanzado.programar(HOY)
        self.assertEqual([f['o'] for f in ordenes], [primera, segunda])
        op1, op2 = ordenes[0]['ops'][0], ordenes[1]['ops'][0]
        # la urgente ocupa el horno 8 h hoy y 2 h mañana; la otra empieza mañana tras ella
        self.assertEqual(op1['tramos'], [(HOY, D('8')), (HOY + timedelta(days=1), D('2'))])
        self.assertEqual((op2['inicio'], op2['tramos']), (HOY + timedelta(days=1), [(HOY + timedelta(days=1), D('4'))]))
        gantt, fechas = avanzado.diagrama(por_centro, HOY, 7)
        self.assertEqual(gantt[0]['celdas'][1][2], D('6'))  # mañana: 2 h + 4 h
        r = self.client.get(reverse('manufactura:programacion'))
        self.assertContains(r, primera.numero)
        self.client.post(reverse('manufactura:programacion'), {'accion': 'prioridad', 'orden': segunda.pk,
                                                               'prioridad': '1'})
        segunda.refresh_from_db()
        self.assertEqual(segunda.prioridad, 1)

    def test_reporte_de_planta_alimenta_el_termino(self):
        o = self._orden('6')
        hora = o.horas.get()
        r = self.client.post(reverse('manufactura:planta_orden', args=[o.pk]), {
            'hora': hora.pk, 'buena': '1.5', 'merma': '0.1', 'horas': '4', 'operario': 'Ana'})
        self.assertEqual(r.status_code, 302)
        o.refresh_from_db()
        hora.refresh_from_db()
        self.assertEqual((o.estado, hora.horas_real), ('EN_PROCESO', D('4')))
        self.assertEqual(avanzado.resumen_avance(o)['producido'], D('1.5'))
        # en la programación ya solo faltan 2 h
        ordenes, _ = avanzado.programar(HOY)
        self.assertEqual(ordenes[0]['ops'][0]['pendiente'], D('2'))
        self.assertContains(self.client.get(reverse('manufactura:orden', args=[o.pk])), 'Según el reporte de planta')
        with self.assertRaises(avanzado.ErrorAvanzado):
            avanzado.registrar_avance(o, hora, D('-1'), None, None, '', '', self.user)

    def test_cambio_de_ingenieria_lo_aprueba_otra_persona(self):
        r = self.client.post(reverse('manufactura:cambio_nuevo'), {
            'lista_actual': self.receta.pk, 'motivo': 'COSTO', 'descripcion': 'Menos insumo por mejora del horno',
            'fecha_efectiva': (HOY + timedelta(days=5)).isoformat()})
        cambio = self.receta.cambios.get()
        self.assertRedirects(r, reverse('manufactura:cambio', args=[cambio.pk]))
        with self.assertRaises(avanzado.ErrorAvanzado):  # sin cambios en la receta no se envía
            avanzado.enviar(cambio)
        cambio.lista_nueva.componentes.update(cantidad=D('0.8'))
        avanzado.enviar(cambio)
        with self.assertRaises(avanzado.ErrorAvanzado):  # quien lo pidió no lo aprueba
            avanzado.aprobar(cambio, self.user)
        avanzado.aprobar(cambio, self.otro, 'Conforme')
        self.receta.refresh_from_db()
        nueva = cambio.lista_nueva
        nueva.refresh_from_db()
        self.assertEqual((nueva.estado, nueva.vigente_desde), ('APROBADA', HOY + timedelta(days=5)))
        self.assertEqual(self.receta.vigente_hasta, HOY + timedelta(days=4))
        # hasta la fecha efectiva se fabrica con la receta actual; desde ella con la nueva
        self.assertEqual(servicios.version_para(self.prod, D('1'), HOY).lista, self.receta)
        self.assertEqual(servicios.version_para(self.prod, D('1'), HOY + timedelta(days=6)).lista, nueva)
        filas, _ = avanzado.diferencias(cambio)
        self.assertEqual(filas[0]['tipo'], 'Modificado')

    def test_costeo_abc(self):
        cc = CentroCosto.objects.create(codigo='CAL', nombre='Control de calidad', tipo='SERVICIO')
        act = ActividadABC.objects.create(codigo='PREP', nombre='Preparación de lotes', inductor='ORDENES')
        act.recursos.create(centro_costo=cc, porcentaje=D('50'))
        for i in range(2):
            OrdenProduccion.objects.create(producto=self.prod, lista=self.receta, cantidad=D('10'), fecha=HOY,
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen,
                                           estado='TERMINADA', fecha_fin=HOY, cantidad_producida=D('10'))
        with patch('produccion.abc.gasto_real', return_value=D('1000')):
            datos = abc.costeo(HOY.strftime('%Y%m'))
            r = self.client.get(reverse('costos:abc'))
        self.assertEqual(datos['actividades'][0]['costo'], D('500'))
        self.assertEqual(datos['actividades'][0]['tasa'], D('250'))
        fila = next(p for p in datos['productos'] if p['p'] == self.prod)
        self.assertEqual((fila['total'], fila['unitario']), (D('500'), D('25')))
        self.assertContains(r, 'Preparación de lotes')

    def test_pantallas(self):
        self._orden('3')
        for nombre in ('programacion', 'planta', 'cambios', 'cambio_nuevo'):
            self.assertEqual(self.client.get(reverse(f'manufactura:{nombre}')).status_code, 200, nombre)
        for nombre in ('abc', 'actividades', 'actividad_nueva'):
            self.assertEqual(self.client.get(reverse(f'costos:{nombre}')).status_code, 200, nombre)
        r = self.client.post(reverse('costos:actividad_nueva'), {
            'codigo': 'DES', 'nombre': 'Despacho', 'inductor': 'DESPACHOS', 'activo': 'on',
            'rec-TOTAL_FORMS': '1', 'rec-INITIAL_FORMS': '0', 'rec-MIN_NUM_FORMS': '0', 'rec-MAX_NUM_FORMS': '1000',
            'rec-0-centro_costo': CentroCosto.objects.create(codigo='VTA', nombre='Ventas').pk,
            'rec-0-porcentaje': '100'})
        self.assertRedirects(r, reverse('costos:actividades'))
