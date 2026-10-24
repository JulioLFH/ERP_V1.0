"""v1.78.0: parámetros tributarios con vigencia, asistente de giro y régimen (EMP-01: plantilla, costeo, módulos que no
se usan, obligaciones) y prorrata del crédito fiscal (INM-05: destino del IGV, asiento y PLE 8.1)."""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, CompraItem
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea, CuentaContable, CuentaDefecto
from contabilidad.prorrata import coeficiente
from ventas.models import Venta, VentaItem

from . import giro, parametros
from .modulos import modulos_del_usuario
from .models import Bitacora, Empresa, Tercero

HOY = timezone.localdate()
P = HOY.strftime('%Y%m')


class ParametrosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('t', 't@t.com', 'x')
        self.client.force_login(self.user)

    def test_vigencia_y_registro(self):
        self.assertEqual(parametros.valor('UIT', date(2025, 6, 1)), D('5350'))
        self.assertEqual(parametros.valor('INM_ARRAS_PCT'), D('3'))
        r = self.client.post(reverse('parametros'), {'clave': 'UIT', 'valor': '5600', 'desde': '2030-01-01',
                                                     'nota': 'D.S. de prueba'})
        self.assertRedirects(r, reverse('parametros'))
        self.assertEqual(parametros.valor('UIT', date(2030, 2, 1)), D('5600'))
        self.assertEqual(parametros.valor('UIT', date(2025, 6, 1)), D('5350'))  # la historia se conserva
        r = self.client.get(reverse('parametros'))
        self.assertContains(r, 'D.S. de prueba')
        self.assertContains(r, 'Bancarización')


class GiroTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('t', 't@t.com', 'x')
        self.client.force_login(self.user)

    def test_asistente_aplica_plantilla_costeo_y_modulos(self):
        url = reverse('empresa_giro')
        self.assertContains(self.client.get(url), 'Giro y régimen tributario')
        r = self.client.post(url, {'accion': 'sugerir', 'giros': ['COMERCIAL'], 'regimen': 'MYPE',
                                   'sistema_costeo': 'PROCESOS'})
        self.assertContains(r, 'value="manufactura" class="form-check-input" id="id_ocultos_')  # sugerido
        r = self.client.post(url, {'accion': 'guardar', 'giros': ['COMERCIAL'], 'regimen': 'MYPE',
                                   'sistema_costeo': 'NATURALEZA', 'ocultos': ['manufactura', 'costos', 'obras'],
                                   'ciiu': '4659', 'actividad': 'Venta al por mayor'})
        self.assertRedirects(r, url)
        e = Empresa.actual()
        self.assertEqual((e.giros, e.regimen_tributario, e.sistema_costeo, e.ciiu),
                         (['COMERCIAL'], 'MYPE', 'NATURALEZA', '4659'))
        self.assertIsNone(CuentaContable.objects.get(codigo='6361').destino_debe)  # sin clase 9
        self.assertTrue(Bitacora.objects.filter(modelo='core.Empresa', motivo__icontains='Giro').exists())
        fresco = User.objects.get(pk=self.user.pk)
        mods = modulos_del_usuario(fresco)
        self.assertNotIn('obras', mods)
        self.assertIn('ajustes', mods)
        self.assertIn(self.client.get(reverse('obras:lista')).status_code, (302, 403))
        self.assertContains(self.client.get(url), 'mientras los ingresos del año no superen')
        # con costeo vuelven los destinos a la clase 9
        self.client.post(url, {'accion': 'guardar', 'giros': ['MANUFACTURA'], 'regimen': 'GENERAL',
                               'sistema_costeo': 'PROCESOS'})
        self.assertIsNotNone(CuentaContable.objects.get(codigo='6361').destino_debe)
        self.assertIn('obras', modulos_del_usuario(User.objects.get(pk=self.user.pk)))

    def test_obligaciones_por_regimen(self):
        self.assertIn('Contabilidad completa', dict(giro.obligaciones('GENERAL', D('2000')))['Libros'])
        self.assertIn('diario de formato simplificado', dict(giro.obligaciones('MYPE', D('100')))['Libros'])
        self.assertIn('1.5 %', dict(giro.obligaciones('RER'))['Pagos'])
        self.assertIn('No lleva libros', dict(giro.obligaciones('NRUS'))['Libros'])


class ProrrataTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970', nombre='CLIENTE')
        cls.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100047218', nombre='PROV')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def venta(self, numero, afectacion, monto):
        v = Venta.objects.create(tercero=self.cliente, serie='F001', numero=numero, fecha_emision=HOY,
                                 descontar_stock=False)
        VentaItem.objects.create(documento=v, descripcion='Venta', cantidad=1, precio_unitario=D(monto),
                                 afectacion=afectacion)
        v.calcular_totales()
        v.save()
        return v

    def compra(self, numero, destino):
        c = Compra.objects.create(tercero=self.prov, serie='F001', numero=numero, fecha_emision=HOY,
                                  clasificacion='SERVICIO', destino_igv=destino, ingresar_almacen=False)
        CompraItem.objects.create(documento=c, descripcion='Servicio común', cantidad=1, precio_unitario=D('1000'))
        c.calcular_totales()
        c.save()
        return c

    def test_coeficiente_asiento_y_ple(self):
        self.venta('1', 'GRAVADA', '1000')
        self.venta('2', 'EXONERADA', '500')
        self.assertEqual(coeficiente(P), D('0.6667'))
        comun = self.compra('10', 'COMUN')
        no_gravada = self.compra('11', 'NO_GRAVADA')
        self.compra('12', 'GRAVADA')
        self.assertEqual(centralizar_periodo(P)['errores'], [])
        igv = CuentaDefecto.mapa()['igv']
        lineas = AsientoLinea.objects.filter(cuenta=igv, asiento__compra=comun)
        self.assertEqual(sum(l.debe for l in lineas), D('120.01'))  # 180 × 0.6667
        self.assertFalse(AsientoLinea.objects.filter(cuenta=igv, asiento__compra=no_gravada, debe__gt=0).exists())
        gasto = sum(l.debe for l in AsientoLinea.objects.filter(asiento__compra=no_gravada, es_destino=False,
                                                               cuenta__codigo__startswith='6'))
        self.assertEqual(gasto, D('1180.00'))  # el IGV sin crédito va al gasto
        from compras.views import compras_views
        campos = compras_views.linea_ple(P, 1, comun).split('|')
        self.assertEqual(campos[13:19], ['0.00', '0.00', '1000.00', '180.00', '0.00', '0.00'])
        campos = compras_views.linea_ple(P, 2, no_gravada).split('|')
        self.assertEqual(campos[13:19], ['0.00', '0.00', '0.00', '0.00', '1000.00', '180.00'])
        user = User.objects.create_superuser('t', 't@t.com', 'x')
        self.client.force_login(user)
        r = self.client.get(reverse('contabilidad:prorrata'))
        self.assertContains(r, '0.6667')
        self.assertEqual(self.client.get(reverse('contabilidad:prorrata') + '?formato=excel').status_code, 200)
