"""v1.66, checklist v1.56 12:53: combustible y rendimiento (FLO-05), costo por equipo y por km (FLO-06), conductores,
viajes desde las guías y comodato (FLO-07) y carga masiva de flota (FLO-08)."""
import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from flota import operacion as op
from flota.models import CargaCombustible, ComponenteEquipo, DocumentoFlota, LecturaEquipo, PrestamoEquipo, Viaje
from logistica.models import Conductor, Vehiculo
from produccion import planta
from produccion.models import Equipo, OrdenMantenimiento

from . import carga_masiva as cm
from .models import Almacen, Producto, Tercero

D = Decimal
HOY = timezone.localdate()
ITEMS = {'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000'}


def excel(filas):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('carga.xlsx', buf.getvalue())


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v166', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)


class CombustibleYCostosTest(Base):
    def test_rendimiento_desvio_y_costo_por_km(self):
        eq = Equipo.objects.create(codigo='CAM-66', nombre='Camión 66', tipo='VEHICULO', placa='CMB066', medidor='KM',
                                   rendimiento_esperado=D('10'))
        for dias, lectura, galones in ((20, '1000', '50'), (10, '1400', '40'), (1, '1600', '40')):
            self.client.post(reverse('flota:combustible'), {
                'equipo': eq.pk, 'fecha': (HOY - timedelta(days=dias)).isoformat(), 'vale': f'V-{dias}',
                'galones': galones, 'precio_galon': '16.50', 'lectura': lectura, 'tanque_lleno': 'on'})
        cargas = list(CargaCombustible.objects.filter(equipo=eq).order_by('fecha'))
        self.assertEqual([c.rendimiento for c in cargas], [None, D('10.00'), D('5.00')])
        self.assertEqual([c.desvio for c in cargas], [False, False, True])
        self.assertEqual(cargas[0].importe, D('825.00'))
        eq.refresh_from_db()
        self.assertEqual(eq.lectura_actual, D('1600'))  # los vales actualizan el odómetro
        r = self.client.post(reverse('flota:combustible'), {'equipo': eq.pk, 'fecha': HOY.isoformat(), 'galones': '5',
                                                             'precio_galon': '16', 'lectura': '1500'})
        self.assertContains(r, 'menor a la anterior')
        from core.alertas import calcular
        self.assertIn('Rendimiento de combustible fuera de lo esperado', [a['titulo'] for a in calcular(self.admin)])
        # costo: combustible + OT cerrada + documento + gasto, por km recorrido
        ot = planta.crear_orden(OrdenMantenimiento(equipo=eq, tipo='CORRECTIVO', descripcion='Frenos',
                                                   costo_mano_obra=D('500')), self.admin)
        ot.trabajo_realizado = 'Cambio de pastillas'
        planta.cerrar(ot, self.admin)
        DocumentoFlota.objects.create(equipo=eq, tipo='SOAT', emision=HOY - timedelta(days=5),
                                      vencimiento=HOY + timedelta(days=360), costo=D('450'))
        self.client.post(reverse('flota:equipo', args=[eq.pk]), {'accion': 'gasto', 'gasto-fecha': HOY.isoformat(),
                                                                 'gasto-tipo': 'PEAJE', 'gasto-importe': '25'})
        fila = op.costos([eq], HOY - timedelta(days=30), HOY)[0]
        self.assertEqual((fila['combustible'], fila['mantenimiento'], fila['documentos'], fila['gastos']),
                         (D('2145.00'), D('500.00'), D('450.00'), D('25.00')))
        self.assertEqual(fila['uso'], D('600'))
        self.assertEqual(fila['por_uso'], D('5.20'))  # 3,120 / 600 km
        r = self.client.get(reverse('flota:costos') + f'?desde={(HOY - timedelta(days=30)).isoformat()}')
        self.assertContains(r, 'CAM-66')
        self.assertContains(self.client.get(reverse('flota:equipo', args=[eq.pk])), 'Costo del año')


class ConductoresViajesYPrestamosTest(Base):
    def test_historial_viaje_desde_guia_y_comodato(self):
        v = Vehiculo.objects.first()
        eq = v.equipo
        a, b = Conductor.objects.first(), Conductor.objects.create(numero_doc='70707070', nombres='Rosa',
                                                                   apellidos='Quispe', licencia='Q70707070')
        op.asignar_conductor(eq, a, HOY - timedelta(days=30), self.admin)
        self.client.post(reverse('flota:equipo', args=[eq.pk]), {'accion': 'asignar', 'asig-conductor': b.pk,
                                                                 'asig-desde': HOY.isoformat()})
        historial = list(eq.asignaciones.order_by('desde'))
        self.assertEqual([(x.conductor, x.hasta) for x in historial], [(a, HOY), (b, None)])
        self.assertEqual(op.conductor_actual(eq), b)
        # la guía con el vehículo propio abre el viaje; al cerrarlo, el km entra al odómetro
        p = Producto.objects.get(codigo='P003')
        self.client.post(reverse('logistica:nueva') + '?tipo=09', {
            'serie': '', 'motivo_traslado': '04', 'descripcion_motivo': '', 'modalidad': '02', 'venta': '',
            'compra': '', 'transportista': '', 'efecto_stock': 'NINGUNO', 'almacen_origen': Almacen.principal().pk,
            'almacen_destino': '', 'fecha_emision': HOY.isoformat(), 'fecha_traslado': HOY.isoformat(),
            'destinatario': '', 'partida_ubigeo': '150131', 'partida_direccion': 'Av. Javier Prado 123',
            'llegada_ubigeo': '070101', 'llegada_direccion': 'Callao', 'vehiculo': v.pk, 'conductor': b.pk,
            'peso_bruto': '10', 'unidad_peso': 'KGM', 'numero_bultos': '1', 'doc_relacionado': '',
            'observaciones': '', 'items-TOTAL_FORMS': '1', 'items-0-producto': p.pk, 'items-0-descripcion': p.nombre,
            'items-0-unidad': 'NIU', 'items-0-cantidad': '1', **ITEMS})
        viaje = Viaje.objects.get(equipo=eq)
        self.assertEqual((viaje.estado, viaje.conductor, viaje.destino), ('EN_RUTA', b, 'Callao'))
        self.client.post(reverse('flota:viajes'), {'accion': 'cerrar', 'viaje': viaje.pk, 'km_llegada': '85',
                                                   'llegada': HOY.isoformat()})
        viaje.refresh_from_db()
        eq.refresh_from_db()
        self.assertEqual((viaje.estado, viaje.recorrido, eq.lectura_actual), ('CERRADO', D('85'), D('85')))
        # comodato: solo equipos marcados; uno a la vez; acta y devolución
        cliente = Tercero.objects.filter(tipo='CLIENTE').first()
        maquina = Equipo.objects.create(codigo='DISP-01', nombre='Dispensador', tipo='OTRO')
        datos = {'equipo': maquina.pk, 'cliente': cliente.pk, 'entrega': (HOY - timedelta(days=40)).isoformat(),
                 'devolucion_prevista': (HOY - timedelta(days=10)).isoformat(), 'ubicacion': 'Tienda Surco'}
        with self.assertRaisesMessage(op.ErrorOperacion, 'Se presta a clientes'):
            op.prestar(PrestamoEquipo(equipo=maquina, cliente=cliente, entrega=HOY), self.admin)
        maquina.aplica_prestamo = True
        maquina.save()
        self.client.post(reverse('flota:prestamos'), datos)
        prestamo = PrestamoEquipo.objects.get(equipo=maquina)
        self.assertTrue(prestamo.vencido)
        from core.alertas import calcular
        self.assertIn('Equipos prestados sin devolver', [x['titulo'] for x in calcular(self.admin)])
        self.assertContains(self.client.get(reverse('flota:prestamo', args=[prestamo.pk])), 'ACTA DE ENTREGA')
        self.client.post(reverse('flota:prestamo', args=[prestamo.pk]), {'fecha': HOY.isoformat(),
                                                                         'estado_devolucion': 'Conforme'})
        prestamo.refresh_from_db()
        self.assertEqual(prestamo.estado, 'DEVUELTO')


class CargaMasivaFlotaTest(Base):
    def cargar(self, tipo, filas):
        resultado, errores = cm.validar(excel(filas), tipo)
        self.assertEqual(errores, 0, [r.get('error') for r in resultado if r.get('error')])
        return cm.cargar(tipo, resultado, self.admin)

    def test_equipos_componentes_planes_lecturas_y_documentos(self):
        self.cargar('equipos', [['Código', 'Nombre', 'Tipo', 'Placa', 'Medidor', 'Rendimiento esperado'],
                                ['MC-10', 'Montacargas 10', 'Montacargas', '', 'horas', ''],
                                ['VH-77', 'Furgón 77', 'Vehículo', 'FUR-777', 'km', '12']])
        self.assertTrue(Vehiculo.objects.filter(placa='FUR777', equipo__codigo='VH-77').exists())
        Producto.objects.create(codigo='REP-AC', nombre='Aceite 15W40', tipo='BIEN')
        self.cargar('componentes', [['Equipo (código)', 'Código', 'Nombre', 'Nivel', 'Pertenece a (código)',
                                     'Repuesto (código de producto)', 'Cantidad del repuesto'],
                                    ['MC-10', 'MOT', 'Motor', 'Sistema', '', '', ''],
                                    ['MC-10', 'ACE', 'Aceite de motor', 'Pieza', 'MOT', 'REP-AC', '4']])
        aceite = ComponenteEquipo.objects.get(codigo='ACE')
        self.assertEqual((aceite.padre.codigo, aceite.repuesto.codigo, aceite.cantidad_repuesto), ('MOT', 'REP-AC', 4))
        self.cargar('planes_mantenimiento', [['Equipo (código)', 'Tarea', 'Componente (código)', 'Cada (km u horas)'],
                                             ['MC-10', 'Cambio de aceite', 'ACE', '250']])
        self.assertEqual(Equipo.objects.get(codigo='MC-10').planes.get().frecuencia_uso, D('250'))
        # lecturas: no menores a la anterior (también dentro del archivo); quedan por aprobar
        resultado, errores = cm.validar(excel([['Equipo (código o placa)', 'Fecha', 'Lectura'],
                                               ['MC-10', HOY.strftime('%d/%m/%Y'), '120'],
                                               ['MC-10', HOY.strftime('%d/%m/%Y'), '100']]), 'lecturas')
        self.assertEqual(errores, 1)
        self.cargar('lecturas', [['Equipo (código o placa)', 'Fecha', 'Lectura'],
                                 ['MC-10', (HOY - timedelta(days=3)).strftime('%d/%m/%Y'), '100'],
                                 ['FUR777', HOY.strftime('%d/%m/%Y'), '54000']])
        self.assertEqual(LecturaEquipo.objects.filter(origen='CARGA', estado='PENDIENTE').count(), 2)
        conductor = Conductor.objects.first()
        self.cargar('documentos_flota', [['Equipo (código o placa)', 'Conductor (DNI)', 'Documento', 'Vence'],
                                         ['FUR777', '', 'SOAT', (HOY + timedelta(days=200)).strftime('%d/%m/%Y')],
                                         ['', conductor.numero_doc, 'Licencia',
                                          (HOY + timedelta(days=400)).strftime('%d/%m/%Y')]])
        conductor.refresh_from_db()
        self.assertEqual(conductor.licencia_vencimiento, HOY + timedelta(days=400))
        self.assertTrue(DocumentoFlota.objects.filter(equipo__codigo='VH-77', tipo='SOAT', bloquea=True).exists())
        self.assertEqual(self.client.get(reverse('carga_masiva') + '?tipo=equipos').status_code, 200)
