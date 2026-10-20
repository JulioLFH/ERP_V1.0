"""Inventario físico y reportes masivos (v1.40): tipos de conteo, foto del stock, bloqueo de movimientos, conteo a
ciegas, reconteo fuera de la tolerancia por otro contador, aprobación por monto con segregación, contabilización con
acta, indicadores y reportes MB5B, MB52, MB51, valorización con cuadre y rotación. Un escenario por clase."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Almacen, Empresa, PerfilUsuario, Producto, StockAlmacen, Ubicacion
from core.modulos import GRUPOS

from . import fisico as srv
from . import reportes_masivos as rm
from .models import ConteoCiclico

D = Decimal
HOY = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_inv', 'a@a.pe', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.almacen = Almacen.objects.create(codigo='AL-F', nombre='Almacén de prueba')
        self.harina = Producto.objects.create(codigo='MP-F1', nombre='Harina', clase='MATERIA_PRIMA')
        self.azucar = Producto.objects.create(codigo='MP-F2', nombre='Azúcar', clase='MATERIA_PRIMA')
        self.sal = Producto.objects.create(codigo='MP-F3', nombre='Sal', clase='MATERIA_PRIMA')
        self.harina.mover_stock(D('100'), 'Saldo', costo=D('2'), almacen=self.almacen)
        self.azucar.mover_stock(D('50'), 'Saldo', costo=D('4'), almacen=self.almacen)
        self.sal.mover_stock(D('10'), 'Saldo', costo=D('1'), almacen=self.almacen)
        self.sal.mover_stock(D('-10'), 'Consumo', almacen=self.almacen)  # queda en cero
        grupo = Group.objects.get_or_create(name=GRUPOS['inventario'])[0]
        self.contador = self.usuario('contador', ['inventario.operar'], grupo)
        self.contador2 = self.usuario('contador2', ['inventario.operar'], grupo)
        self.jefe = self.usuario('jefe_alm', ['inventario.operar', 'inventario.ajustar'], grupo)

    def usuario(self, nombre, acciones, grupo):
        u = User.objects.create_user(nombre, password='x')
        u.groups.add(grupo)
        PerfilUsuario.objects.create(usuario=u, acciones=acciones)
        return u


class TiposDeConteoTest(Base):
    """General, aleatorio (reproducible con la semilla), por ubicación y stock en cero."""

    def test_seleccion(self):
        general = srv.crear(self.almacen, 'GENERAL', self.admin)
        self.assertEqual({i.producto for i in general.items.all()}, {self.harina, self.azucar})
        self.assertEqual(general.items.get(producto=self.harina).sistema, D('100'))
        a1 = srv.crear(self.almacen, 'ALEATORIO', self.admin, n=1, semilla='auditoria-1')
        a2, _ = srv.seleccion(self.almacen, 'ALEATORIO', n=1, semilla='auditoria-1')
        self.assertEqual([a1.items.get().producto_id], a2)
        self.assertEqual(a1.semilla, 'auditoria-1')
        ubic = Ubicacion.objects.create(almacen=self.almacen, codigo='A-01-02')
        StockAlmacen.objects.filter(producto=self.harina, almacen=self.almacen).update(ubicacion=ubic)
        por_ubicacion = srv.crear(self.almacen, 'UBICACION', self.admin, ubicacion='A-01')
        self.assertEqual([(i.producto, i.ubicacion) for i in por_ubicacion.items.all()], [(self.harina, 'A-01-02')])
        cero = srv.crear(self.almacen, 'CERO', self.admin)
        self.assertEqual([i.producto for i in cero.items.all()], [self.sal])
        with self.assertRaises(srv.ErrorFisico):
            srv.crear(self.almacen, 'UBICACION', self.admin, ubicacion='Z-99')


class CicloCompletoTest(Base):
    """Bloqueo, conteo a ciegas, reconteo por otro contador, motivos, aprobación (quien cuenta no aprueba, tope) y
    contabilización con ajustes y acta."""

    def test_ciclo(self):
        doc = srv.crear(self.almacen, 'GENERAL', self.admin, contador=self.contador, bloquear=True)
        # bloqueo: no se mueve el almacén mientras se cuenta
        with self.assertRaises(srv.ProductoEnConteo):
            self.harina.mover_stock(D('-5'), 'Consumo', almacen=self.almacen)
        self.client.force_login(self.contador)
        r = self.client.get(reverse('inventario:fisico', args=[doc.pk]))
        self.assertContains(r, 'Conteo a ciegas')
        self.assertNotContains(r, 'Sistema (foto)')
        harina, azucar = doc.items.get(producto=self.harina), doc.items.get(producto=self.azucar)
        self.client.post(reverse('inventario:fisico', args=[doc.pk]), {'accion': 'contar', f'c_{harina.pk}': '97',
                                                                       f'c_{azucar.pk}': '50'})
        doc.refresh_from_db()
        harina.refresh_from_db()
        self.assertEqual((doc.estado, harina.diferencia, harina.valor_diferencia, harina.contado_por),
                         ('CONTADO', D('-3'), D('-6.00'), self.contador))
        self.assertTrue(harina.exige_reconteo)  # 3 % > 2 % de tolerancia
        with self.assertRaises(srv.ErrorFisico):  # el mismo contador no recuenta
            srv.registrar(doc, {harina.pk: D('98')}, self.contador, reconteo=True)
        ok, motivo = srv.puede_aprobar(self.jefe, doc)
        self.assertFalse(ok)
        self.assertIn('reconteo', motivo)
        srv.registrar(doc, {harina.pk: D('98')}, self.contador2, reconteo=True)
        harina.refresh_from_db()
        self.assertEqual((harina.final, harina.diferencia, harina.exige_reconteo), (D('98'), D('-2'), False))
        self.assertFalse(srv.puede_aprobar(self.contador, doc)[0])  # quien contó no aprueba
        with self.assertRaisesMessage(srv.ErrorFisico, 'motivo'):
            srv.aprobar(doc, self.jefe)
        srv.registrar(doc, {}, self.jefe, motivos={harina.pk: 'MERMA'})
        # tope: diferencias mayores al tope las aprueba contraloría
        Empresa.objects.update(tope_diferencias_inventario=D('1'))
        self.assertIn('tope', srv.puede_aprobar(self.jefe, doc)[1])
        Empresa.objects.update(tope_diferencias_inventario=D('5000'))
        srv.aprobar(doc, self.jefe)
        srv.contabilizar(doc, self.jefe)
        doc.refresh_from_db()
        self.assertEqual(doc.estado, 'CERRADO')
        self.assertIsNone(doc.ajuste_ingreso)
        self.assertEqual(doc.ajuste_salida.items.get().cantidad, D('2'))
        self.assertEqual(StockAlmacen.objects.get(producto=self.harina, almacen=self.almacen).cantidad, D('98'))
        from core.sustentos import tiene
        self.assertTrue(tiene(doc.ajuste_salida))
        self.harina.mover_stock(D('-5'), 'Consumo', almacen=self.almacen)  # ya no está bloqueado
        self.client.force_login(self.jefe)
        self.assertIn('spreadsheetml', self.client.get(reverse('inventario:fisico', args=[doc.pk]),
                                                       {'formato': 'acta'})['Content-Type'])


class SinBloqueoMovimientosTest(Base):
    """Sin bloqueo, lo que se mueve después de la foto se descuenta al comparar."""

    def test_movimientos_despues_de_la_foto(self):
        doc = srv.crear(self.almacen, 'GENERAL', self.admin)
        self.harina.mover_stock(D('-10'), 'Consumo', almacen=self.almacen)  # después de la foto
        harina = doc.items.get(producto=self.harina)
        srv.registrar(doc, {harina.pk: D('90')}, self.contador)
        harina.refresh_from_db()
        self.assertEqual((harina.sistema, harina.movimientos, harina.diferencia), (D('100'), D('-10'), D('0')))


class IndicadoresTest(Base):
    """IRA por almacén y contador, diferencias por motivo y productos nunca contados."""

    def test_indicadores(self):
        doc = srv.crear(self.almacen, 'GENERAL', self.admin)
        h, a = doc.items.get(producto=self.harina), doc.items.get(producto=self.azucar)
        srv.registrar(doc, {h.pk: D('100'), a.pk: D('49.5')}, self.contador, motivos={a.pk: 'DANO'})
        srv.aprobar(doc, self.jefe)
        srv.contabilizar(doc, self.jefe)
        d = srv.indicadores(self.almacen)
        self.assertEqual((d['ira_total'], d['lineas']), (D('50.0'), 2))
        self.assertEqual(d['ira']['contador'], [('contador', 2, D('50.0'))])
        self.assertEqual(d['por_motivo'], [('Daño o rotura', D('-2.00'))])
        self.assertEqual(d['nunca'], [])
        self.client.force_login(self.admin)
        for url in [reverse('inventario:fisico_indicadores'), reverse('inventario:fisico_lista'),
                    reverse('inventario:fisico_nuevo'), reverse('inventario:fisico_movil', args=[doc.pk]),
                    reverse('inventario:fisico_indicadores') + f'?almacen={self.almacen.pk}']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('inventario:fisico_nuevo'), {'almacen': self.almacen.pk, 'tipo': 'ALEATORIO',
                                                                  'n': '1', 'semilla': 'x1', 'tolerancia_pct': '2',
                                                                  'tolerancia_valor': '500'})
        nuevo = ConteoCiclico.objects.get(semilla='x1')
        self.assertRedirects(r, reverse('inventario:fisico', args=[nuevo.pk]))


class ReportesMasivosTest(Base):
    """Stock a una fecha, stock con comprometido y bloqueado, kardex masivo (Excel y CSV), valorización con cuadre
    y rotación / sin movimiento."""

    def test_reportes(self):
        ayer = HOY - timedelta(days=1)
        enc, filas, total = rm.stock_a_fecha(HOY, [self.almacen.pk])
        self.assertEqual({f[0]: f[enc.index('Cantidad')] for f in filas}, {'MP-F1': D('100'), 'MP-F2': D('50')})
        self.assertEqual(total, D('400.00'))
        self.assertEqual(rm.stock_a_fecha(ayer, [self.almacen.pk])[1], [])
        enc, filas, _ = rm.stock_actual([self.almacen.pk])
        harina = next(f for f in filas if f[0] == 'MP-F1')
        self.assertEqual(harina[enc.index('Disponible')], D('100'))
        srv.crear(self.almacen, 'GENERAL', self.admin, bloquear=True)
        enc, filas, _ = rm.stock_actual([self.almacen.pk])
        harina = next(f for f in filas if f[0] == 'MP-F1')
        self.assertEqual((harina[enc.index('Bloqueado')], harina[enc.index('Disponible')]), (D('100'), D('0')))
        movs = list(rm.kardex_masivo(HOY, HOY, [self.almacen.pk]))
        self.assertEqual(len(movs), 4)  # 3 ingresos y el consumo de la sal
        enc, filas, total, cuadre = rm.valorizacion(HOY)
        self.assertTrue(cuadre)
        enc, filas, _ = rm.rotacion([self.almacen.pk])
        sal = next(f for f in filas if f[0] == 'MP-F3')  # sin stock, pero con salidas en el periodo
        self.assertEqual((sal[enc.index('Stock')], sal[enc.index('Salidas 365 días S/')]), (D('0'), D('10.00')))
        harina = next(f for f in filas if f[0] == 'MP-F1')
        self.assertEqual(harina[enc.index('Días sin moverse')], 0)
        enc, filas, _ = rm.rotacion([self.almacen.pk], sin_movimiento=90)
        self.assertEqual(filas, [])  # todo se movió hoy
        self.client.force_login(self.admin)
        for clave in ('stock_fecha', 'stock_actual', 'kardex', 'valorizacion', 'rotacion'):
            url = reverse('inventario:reporte_masivo', args=[clave])
            self.assertEqual(self.client.get(url).status_code, 200, clave)
            self.assertEqual(self.client.get(url, {'ver': '1', 'almacen': self.almacen.pk}).status_code, 200, clave)
            r = self.client.get(url, {'ver': '1', 'formato': 'excel'})
            self.assertIn('spreadsheetml', r['Content-Type'], clave)
        r = self.client.get(reverse('inventario:reporte_masivo', args=['kardex']), {'ver': '1', 'formato': 'csv',
                                                                                   'almacen': self.almacen.pk})
        contenido = b''.join(r.streaming_content).decode('utf-8-sig')
        self.assertEqual(len(contenido.strip().splitlines()), 5)
        self.assertEqual(self.client.get(reverse('inventario:reportes_masivos')).status_code, 200)
